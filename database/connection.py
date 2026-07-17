import time
import logging
import psycopg2
from contextlib import contextmanager
from psycopg2 import pool, extensions
from langchain_community.utilities import SQLDatabase
from config.settings import ENV_CONFIG, DB_URI

log = logging.getLogger(__name__)

DB_CONFIG = {
    'dbname':   ENV_CONFIG['db_name'],
    'user':     ENV_CONFIG['db_user'],
    'password': ENV_CONFIG['db_password'],
    'host':     ENV_CONFIG['db_host'],
    'port':     int(ENV_CONFIG['db_port']),
    'connect_timeout': 10,
    # Apply the local timezone to every session so TIMESTAMPTZ columns are
    # returned in local time (EET/EEST) rather than the PostgreSQL server's UTC.
    # This is a session-level SET; it does not alter the server configuration.
    'options':  f"-c TimeZone={ENV_CONFIG['db_timezone']}",
}

_pool: "pool.SimpleConnectionPool | None" = None

_TZ = ENV_CONFIG['db_timezone']   # e.g. "Europe/Athens"


def _apply_tz(conn) -> None:
    """Explicitly SET TIME ZONE on *conn*.

    Belt-and-suspenders alongside the `options` parameter in DB_CONFIG:
    pooled connections created before a code reload would not have the
    `options` applied, so we enforce the timezone on every checkout too.
    """
    try:
        cur = conn.cursor()
        cur.execute(f"SET TIME ZONE %s", (_TZ,))
        cur.close()
        conn.commit()
    except Exception as exc:
        log.debug("_apply_tz failed: %s", exc)


_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 1.5   # seconds; doubles each attempt


def _retry(fn, label: str = "DB operation"):
    """Call *fn()* up to _RETRY_ATTEMPTS times, backing off on OperationalError."""
    delay = _RETRY_BASE_DELAY
    last_exc = None
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            return fn()
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as exc:
            last_exc = exc
            if attempt < _RETRY_ATTEMPTS:
                log.warning("%s failed (attempt %d/%d): %s — retrying in %.1fs",
                            label, attempt, _RETRY_ATTEMPTS, exc, delay)
                time.sleep(delay)
                delay *= 2
    raise last_exc


def _get_pool() -> pool.SimpleConnectionPool:
    global _pool
    if _pool is None or _pool.closed:
        _pool = pool.SimpleConnectionPool(1, 20, **DB_CONFIG)
    return _pool


def _reset_pool():
    """Close all pooled connections and force a fresh pool on next use."""
    global _pool
    try:
        if _pool is not None and not _pool.closed:
            _pool.closeall()
    except Exception:
        pass
    _pool = None


def _is_conn_alive(conn) -> bool:
    """Ping the server to verify the connection is actually usable."""
    if conn is None or conn.closed != 0:
        return False
    try:
        conn.cursor().execute("SELECT 1")
        return True
    except Exception:
        return False


@contextmanager
def get_db():
    """Yield a pooled connection with auto commit/rollback and stale-connection recovery.

    If the checked-out connection is dead (network drop, server restart) it is
    discarded and a fresh connection is opened before yielding to the caller.
    If that also fails the pool is fully reset and one more attempt is made.
    """
    p = _get_pool()
    conn = p.getconn()

    # Discard stale connections silently; replace with a fresh one.
    if not _is_conn_alive(conn):
        try:
            p.putconn(conn, close=True)
        except Exception:
            pass
        try:
            conn = _retry(lambda: p.getconn(), "pool checkout")
        except Exception:
            _reset_pool()
            p = _get_pool()
            conn = p.getconn()

    # Enforce local timezone on every checkout (handles pool connections that
    # were created before the DB_CONFIG `options` setting was in place).
    _apply_tz(conn)

    try:
        yield conn
        conn.commit()
    except Exception:
        if _is_conn_alive(conn):
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    finally:
        if _is_conn_alive(conn):
            p.putconn(conn)
        else:
            try:
                p.putconn(conn, close=True)
            except Exception:
                pass


def _run_startup_migrations():
    """Run idempotent DDL migrations on startup (once per process)."""
    _STMTS = [
        # Historical_Prices: provenance columns
        "ALTER TABLE Historical_Prices ADD COLUMN IF NOT EXISTS Source        VARCHAR(50)",
        "ALTER TABLE Historical_Prices ADD COLUMN IF NOT EXISTS Downloaded_At TIMESTAMPTZ",
        "CREATE INDEX IF NOT EXISTS idx_price_source ON Historical_Prices(Source)",
        # Holdings: staking flag (migration 004)
        "ALTER TABLE Holdings ADD COLUMN IF NOT EXISTS Staking BOOLEAN DEFAULT FALSE",
        # News: opt-in per-payee tracking flag, and the News_Items table itself
        "ALTER TABLE Payees ADD COLUMN IF NOT EXISTS Track_For_News BOOLEAN DEFAULT FALSE",
        """CREATE TABLE IF NOT EXISTS News_Items (
               News_Id      SERIAL PRIMARY KEY,
               Source_Type  VARCHAR(20) NOT NULL CHECK (Source_Type IN ('Security', 'Institution', 'Payee')),
               Source_Id    INTEGER NOT NULL,
               Title        TEXT NOT NULL,
               Url          TEXT NOT NULL,
               Publisher    VARCHAR(150),
               Summary      TEXT,
               Published_At TIMESTAMPTZ,
               Fetched_At   TIMESTAMPTZ DEFAULT NOW(),
               Is_Read      BOOLEAN DEFAULT FALSE,
               UNIQUE(Source_Type, Source_Id, Url)
           )""",
        "CREATE INDEX IF NOT EXISTS idx_news_items_source    ON News_Items(Source_Type, Source_Id)",
        "CREATE INDEX IF NOT EXISTS idx_news_items_published ON News_Items(Published_At DESC)",
        # AI helper view: transactions with amounts converted to EUR via latest FX rate
        """CREATE OR REPLACE VIEW v_transactions_eur AS
           SELECT
               t.transactions_id,
               t.date,
               t.description,
               t.total_amount,
               c.currencies_shortname                                             AS currency,
               COALESCE(
                   (SELECT fx.fx_rate FROM historical_fx fx
                    WHERE fx.currencies_id_1 = a.currencies_id
                    ORDER BY fx.date DESC LIMIT 1), 1
               )                                                                  AS fx_rate,
               t.total_amount * COALESCE(
                   (SELECT fx.fx_rate FROM historical_fx fx
                    WHERE fx.currencies_id_1 = a.currencies_id
                    ORDER BY fx.date DESC LIMIT 1), 1
               )                                                                  AS amount_eur,
               t.payees_id,
               p.payees_name                                                      AS payee,
               a.accounts_id,
               a.accounts_name,
               a.accounts_type,
               t.accounts_id_target
           FROM transactions t
           JOIN accounts a    ON a.accounts_id    = t.accounts_id
           JOIN currencies c  ON c.currencies_id  = a.currencies_id
           LEFT JOIN payees p ON p.payees_id      = t.payees_id""",
    ]
    try:
        conn     = psycopg2.connect(**DB_CONFIG)
        mig_cur  = conn.cursor()
        for stmt in _STMTS:
            try:
                mig_cur.execute(stmt)
                conn.commit()
            except Exception as stmt_exc:
                conn.rollback()
                log.warning("Startup migration statement failed: %s — %s", stmt, stmt_exc)
        mig_cur.close()
        conn.close()
        log.info("Startup migrations completed.")
    except Exception as exc:
        log.warning("Startup migration connection failed: %s", exc)


_run_startup_migrations()


def get_connection() -> extensions.connection:
    """Open and return a plain (non-pooled) connection with retry on transient failure.

    Kept for backward compatibility. Prefer get_db() for new code.
    """
    conn = _retry(lambda: psycopg2.connect(**DB_CONFIG), "get_connection")
    _apply_tz(conn)
    return conn


def get_sql_database() -> SQLDatabase:
    """Return a LangChain SQLDatabase with pool_pre_ping so stale connections
    are transparently replaced, and retry on the initial connect."""
    def _build():
        return SQLDatabase.from_uri(
            DB_URI,
            engine_args={"pool_pre_ping": True, "connect_args": {"connect_timeout": 10}},
            include_tables=[
                'accounts', 'transactions', 'splits',
                'categories', 'currencies', 'institutions',
                'historical_fx', 'historical_prices', 'holdings',
                'investments', 'payees', 'securities',
            ],
            sample_rows_in_table_info=0
        )
    return _retry(_build, "get_sql_database")