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
        # Portfolio_Presets: scope column so the same table can hold separately-named
        # presets per report (Inv. Performance, Net Worth, Investment Position)
        # instead of one global Preset_Name namespace.
        "ALTER TABLE Portfolio_Presets ADD COLUMN IF NOT EXISTS Report_Scope VARCHAR(32) NOT NULL DEFAULT 'inv_performance'",
        "ALTER TABLE Portfolio_Presets DROP CONSTRAINT IF EXISTS portfolio_presets_preset_name_key",
        # Postgres has no ADD CONSTRAINT IF NOT EXISTS — wrap in a DO block so this doesn't
        # log a warning on every single startup/reload once already applied. A UNIQUE
        # constraint creates a backing index, so re-adding it raises duplicate_table
        # ("relation ... already exists", 42P07) rather than duplicate_object.
        """DO $$ BEGIN
               ALTER TABLE Portfolio_Presets ADD CONSTRAINT portfolio_presets_scope_name_unique UNIQUE (Report_Scope, Preset_Name);
           EXCEPTION WHEN duplicate_table OR duplicate_object THEN NULL;
           END $$""",
        # Recurring_Templates.Periodicity: normalise to the hyphenated vocabulary
        # ('Bi-Weekly', 'Bi-Monthly', 'Semi-Annual', 'Annual') used everywhere else —
        # Recurring.tsx's dropdown and the CASE statements in api/routers/recurring.py.
        # "Save as recurring template" on an existing transaction used to write
        # 'Biweekly'/'Semiannually'/'Annually' instead, which the real scheduled
        # draft generator (database/crud.py) didn't recognise and silently advanced
        # as if Monthly. Data-only fix; harmless no-op once already applied.
        "UPDATE Recurring_Templates SET Periodicity = 'Annual' WHERE Periodicity = 'Annually'",
        "UPDATE Recurring_Templates SET Periodicity = 'Semi-Annual' WHERE Periodicity = 'Semiannually'",
        "UPDATE Recurring_Templates SET Periodicity = 'Bi-Weekly' WHERE Periodicity = 'Biweekly'",
        # Portfolio X-Ray: cached ETF/Mutual Fund look-through composition (sector
        # weights, asset mix, top holdings, expense ratio) from yfinance's
        # get_funds_data(), refreshed monthly by the scheduler / on-demand.
        """CREATE TABLE IF NOT EXISTS Fund_Composition (
               Securities_Id                  INTEGER PRIMARY KEY REFERENCES Securities(Securities_Id) ON DELETE CASCADE,
               Asset_Cash_Pct                 NUMERIC(6,4),
               Asset_Stock_Pct                NUMERIC(6,4),
               Asset_Bond_Pct                 NUMERIC(6,4),
               Asset_Preferred_Pct            NUMERIC(6,4),
               Asset_Convertible_Pct          NUMERIC(6,4),
               Asset_Other_Pct                NUMERIC(6,4),
               Sector_Weightings              JSONB,
               Category_Name                  VARCHAR(100),
               Fund_Family                    VARCHAR(150),
               Legal_Type                     VARCHAR(50),
               Expense_Ratio_Pct              NUMERIC(6,4),
               Category_Avg_Expense_Ratio_Pct NUMERIC(6,4),
               Total_Net_Assets               NUMERIC(20,2),
               Holdings_Turnover_Pct          NUMERIC(6,4),
               Bond_Ratings                   JSONB,
               Bond_Duration                  NUMERIC(8,4),
               Bond_Maturity                  NUMERIC(8,4),
               Equity_PE                      NUMERIC(10,4),
               Equity_PB                      NUMERIC(10,4),
               Equity_PS                      NUMERIC(10,4),
               Equity_PCF                     NUMERIC(10,4),
               Equity_Median_Market_Cap       NUMERIC(20,2),
               Equity_3yr_Earnings_Growth_Pct NUMERIC(8,4),
               Last_Updated                   TIMESTAMPTZ DEFAULT NOW(),
               Fetch_Error                    TEXT
           )""",
        """CREATE TABLE IF NOT EXISTS Fund_Top_Holdings (
               Fund_Holding_Id  SERIAL PRIMARY KEY,
               Securities_Id    INTEGER NOT NULL REFERENCES Securities(Securities_Id) ON DELETE CASCADE,
               Rank             SMALLINT NOT NULL,
               Symbol           VARCHAR(20) NOT NULL,
               Holding_Name     VARCHAR(200),
               Weight_Pct       NUMERIC(6,4) NOT NULL,
               UNIQUE(Securities_Id, Rank)
           )""",
        "CREATE INDEX IF NOT EXISTS idx_fund_top_holdings_symbol ON Fund_Top_Holdings(Symbol)",
        # Security Detail Overview tab: cached daily quote fields (day/52-week
        # range, volume, P/E, market cap) from yfinance's ticker.info, kept
        # in their own 1:1 child table rather than bloating Securities.
        """CREATE TABLE IF NOT EXISTS Securities_Quote (
               Securities_Id     INTEGER PRIMARY KEY REFERENCES Securities(Securities_Id) ON DELETE CASCADE,
               Prev_Close        NUMERIC(20,8),
               Day_Open          NUMERIC(20,8),
               Day_High          NUMERIC(20,8),
               Day_Low           NUMERIC(20,8),
               Week52_High       NUMERIC(20,8),
               Week52_Low        NUMERIC(20,8),
               Volume            BIGINT,
               Avg_Volume        BIGINT,
               Trailing_PE       NUMERIC(10,4),
               Market_Cap        NUMERIC(20,2),
               Quote_Updated_At  TIMESTAMPTZ
           )""",
        # Issuers: name + Moody's/S&P/Fitch ratings, mirroring Institutions'
        # rating columns (same Credit_Ratings_LT FK). Kept as its own table
        # rather than reusing Institutions, since an issuer isn't a financial
        # institution the user holds accounts with. Originally shipped as
        # "Bond Issuers" (bond-only); renamed to the generic "Issuers" since
        # it's also meant to cover fund/ETF issuers, not just bond issuers —
        # these three renames are one-time migrations for installs that
        # already have the old names; harmless no-ops once already applied
        # (each catches the "doesn't exist" error a repeat run would raise).
        """DO $$ BEGIN
               ALTER TABLE Bond_Issuers RENAME TO Issuers;
           EXCEPTION WHEN undefined_table THEN NULL;
           END $$""",
        """DO $$ BEGIN
               ALTER TABLE Issuers RENAME COLUMN Bond_Issuers_Id TO Issuers_Id;
           EXCEPTION WHEN undefined_table OR undefined_column THEN NULL;
           END $$""",
        """DO $$ BEGIN
               ALTER TABLE Issuers RENAME COLUMN Bond_Issuers_Name TO Issuers_Name;
           EXCEPTION WHEN undefined_table OR undefined_column THEN NULL;
           END $$""",
        """DO $$ BEGIN
               ALTER TABLE Securities RENAME COLUMN Bond_Issuer_Id TO Issuer_Id;
           EXCEPTION WHEN undefined_column THEN NULL;
           END $$""",
        """CREATE TABLE IF NOT EXISTS Issuers (
               Issuers_Id   SERIAL PRIMARY KEY,
               Issuers_Name VARCHAR(150) UNIQUE NOT NULL,
               Moodys       VARCHAR(4) REFERENCES Credit_Ratings_LT(Moodys),
               S_P          VARCHAR(4) REFERENCES Credit_Ratings_LT(S_P),
               Fitch        VARCHAR(4) REFERENCES Credit_Ratings_LT(Fitch),
               Notes        TEXT
           )""",
        # Must run after Issuers is created (FK target).
        "ALTER TABLE Securities ADD COLUMN IF NOT EXISTS Issuer_Id INTEGER REFERENCES Issuers(Issuers_Id) ON DELETE SET NULL",
        # X-Ray Style Box: manual category override for funds Yahoo doesn't
        # supply a Morningstar-style category for (mostly non-US ETFs).
        "ALTER TABLE Fund_Composition ADD COLUMN IF NOT EXISTS Category_Override VARCHAR(50)",
        # X-Ray Asset Allocation: manual asset-class override for funds Yahoo's
        # generic 6-bucket asset_classes split can't classify correctly (e.g.
        # physical commodity ETCs land 100% in "other" — Yahoo has no
        # commodity-specific bucket). When set, the fund's whole value routes
        # to this one class instead of being split across the 6 Yahoo buckets.
        "ALTER TABLE Fund_Composition ADD COLUMN IF NOT EXISTS Asset_Class_Override VARCHAR(20)",
        # Some exchanges quote in a minor currency unit Yahoo reports as its own
        # currency code — e.g. London Stock Exchange ordinary shares in GBp/GBX
        # (pence) rather than GBP (pounds), a factor of 100 apart. Price_Scale
        # lets downloaders normalize every price/quote field for that security
        # back to its actual major-unit currency before storing it, instead of
        # silently treating "552.1 GBp" as "552.1 GBP".
        "ALTER TABLE Securities ADD COLUMN IF NOT EXISTS Price_Scale NUMERIC(10,4) DEFAULT 1",
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