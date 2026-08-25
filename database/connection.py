import os
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


def _bootstrap_schema_if_empty(conn) -> bool:
    """Apply the full schema (Oikos.sql) on a brand-new, empty database.
    Returns True if this call just created the schema, False if it already existed.

    Self-hosting shouldn't require anyone to know to hand-run a .sql file
    before the app will start — a fresh `docker-compose up` against an empty
    Postgres database should just work. Everything below (_run_startup_migrations)
    only ever ALTERs/adds to tables that are assumed to already exist, so
    without this, that whole function silently no-ops (every ALTER TABLE fails
    "relation does not exist" and is swallowed) and the app is left with zero
    tables. Detected via one representative table (Accounts) rather than
    tracking a separate "schema version" row — cheap, and Oikos.sql's own
    statements are already idempotent, so even a partially-applied prior
    attempt is safe to retry.
    """
    cur = conn.cursor()
    cur.execute("SELECT to_regclass('public.accounts')")
    if cur.fetchone()[0] is not None:
        return False  # already initialized
    schema_path = os.path.join(os.path.dirname(__file__), "Oikos.sql")
    with open(schema_path, encoding="utf-8") as f:
        schema_sql = f.read()
    cur.execute(schema_sql)
    conn.commit()
    log.info("Empty database detected — applied Oikos.sql to bootstrap the schema.")
    return True


def _seed_reference_data(conn) -> None:
    """Populate a freshly-bootstrapped database with starter reference data.

    Only ever called right after _bootstrap_schema_if_empty() creates the schema
    on a brand-new database — never on an existing install — so there's no
    risk of duplicating or overwriting a real user's data. The data itself
    (database/seed_data.py) is a hand-curated, de-identified export: no family
    names, personal accounts, or named individuals, per the exclusions worked
    out when this was added (see CHANGELOG).
    """
    from database import seed_data as sd

    cur = conn.cursor()

    cur.executemany(
        """INSERT INTO Credit_Ratings_LT
               (Credit_Ratings_LT_Id, Quality, Description, Moodys, S_P, Fitch)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (Credit_Ratings_LT_Id) DO NOTHING""",
        sd.CREDIT_RATINGS_LT,
    )

    cur.executemany(
        "INSERT INTO Currencies (Currencies_Shortname, Currencies_Name) VALUES (%s, %s) "
        "ON CONFLICT (Currencies_Shortname) DO NOTHING",
        sd.CURRENCIES,
    )

    cur.executemany(
        """INSERT INTO Institutions
               (Institutions_Name, Institutions_Type, Bic_Code, Website, Notes, Moodys, S_P, Fitch)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (Institutions_Name) DO NOTHING""",
        sd.INSTITUTIONS,
    )

    # Oikos.sql already inserts a base set of Tax_Category_Rules / Instrument_Type_Tax_Override
    # rows (ON CONFLICT DO NOTHING there). This seed's set is a superset with a few refined
    # values, so upsert on top of those rather than plain INSERT.
    cur.executemany(
        """INSERT INTO Tax_Category_Rules
               (Tax_Category, Display_Name, Gains_Taxable, Gains_Rate, Gains_Tax_Code,
                Dividend_Local_Tax_Rate, Dividend_Wht_Creditable, Reinvest_Taxable,
                Income_Tax_Rate, Notes, Show_In_Capital_Gains)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (Tax_Category) DO UPDATE SET
               Display_Name = EXCLUDED.Display_Name,
               Gains_Taxable = EXCLUDED.Gains_Taxable,
               Gains_Rate = EXCLUDED.Gains_Rate,
               Gains_Tax_Code = EXCLUDED.Gains_Tax_Code,
               Dividend_Local_Tax_Rate = EXCLUDED.Dividend_Local_Tax_Rate,
               Dividend_Wht_Creditable = EXCLUDED.Dividend_Wht_Creditable,
               Reinvest_Taxable = EXCLUDED.Reinvest_Taxable,
               Income_Tax_Rate = EXCLUDED.Income_Tax_Rate,
               Notes = EXCLUDED.Notes,
               Show_In_Capital_Gains = EXCLUDED.Show_In_Capital_Gains""",
        sd.TAX_CATEGORY_RULES,
    )

    cur.executemany(
        """INSERT INTO Instrument_Type_Tax_Override (Instrument_Type, Tax_Category_Override, Notes)
           VALUES (%s, %s, %s)
           ON CONFLICT (Instrument_Type) DO UPDATE SET
               Tax_Category_Override = EXCLUDED.Tax_Category_Override,
               Notes = EXCLUDED.Notes""",
        sd.INSTRUMENT_TYPE_TAX_OVERRIDE,
    )

    cur.executemany(
        """INSERT INTO Securities
               (Ticker, Securities_Name, Securities_Type, Currencies_Id, Sector, Industry,
                Yahoo_Ticker, Tv_Symbol, Tv_Exchange, Isin, Tax_Category, Is_Active, Is_Tax_Exempt)
           SELECT %s, %s, %s, Currencies_Id, %s, %s, %s, %s, %s, %s, %s, %s, %s
           FROM Currencies WHERE Currencies_Shortname = %s
           ON CONFLICT (Ticker) DO NOTHING""",
        [(t, name, typ, sector, industry, yt, tvs, tve, isin, taxcat, active, taxexempt, ccy)
         for (t, name, typ, ccy, sector, industry, yt, tvs, tve, isin, taxcat, active, taxexempt)
         in sd.SECURITIES],
    )

    # Categories form a tree (up to 3 levels deep); insert parent-before-child,
    # remapping each source install's old id to the new one via RETURNING so
    # child rows can point at the right freshly-generated parent id.
    old_to_new: dict[int, int] = {}
    remaining = list(sd.CATEGORIES)
    while remaining:
        ready = [r for r in remaining if r[2] is None or r[2] in old_to_new]
        if not ready:
            log.warning("Seed categories: %d rows have unresolved parents — skipping them.", len(remaining))
            break
        for old_id, name, old_parent, ctype in ready:
            new_parent = old_to_new[old_parent] if old_parent is not None else None
            cur.execute(
                "INSERT INTO Categories (Categories_Name, Categories_Id_Parent, Categories_Type) "
                "VALUES (%s, %s, %s) RETURNING Categories_Id",
                (name, new_parent, ctype),
            )
            old_to_new[old_id] = cur.fetchone()[0]
        remaining = [r for r in remaining if r[0] not in old_to_new]

    cur.executemany(
        "INSERT INTO Payees (Payees_Name) VALUES (%s) ON CONFLICT (Payees_Name) DO NOTHING",
        [(p,) for p in sd.PAYEES],
    )

    conn.commit()
    log.info(
        "Seeded reference data: %d currencies, %d institutions, %d tax rules, "
        "%d instrument tax overrides, %d securities, %d categories, %d payees.",
        len(sd.CURRENCIES), len(sd.INSTITUTIONS), len(sd.TAX_CATEGORY_RULES),
        len(sd.INSTRUMENT_TYPE_TAX_OVERRIDE), len(sd.SECURITIES), len(old_to_new), len(sd.PAYEES),
    )


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
        # X-Ray Asset Allocation: target percentages keyed by X-Ray's look-through
        # asset classes (Stocks/Cash/Bonds/Commodities/Crypto/Other/Preferred/
        # Convertible) — separate from Allocation_Targets (keyed by Securities_Type,
        # used by the older non-look-through Inv. Portfolio -> Allocation tab, kept
        # unchanged), since a fund's value now splits across multiple classes here
        # rather than counting entirely under one Securities_Type bucket.
        """CREATE TABLE IF NOT EXISTS XRay_Allocation_Targets (
               Asset_Class VARCHAR(30) PRIMARY KEY,
               Target_Pct  NUMERIC(5,2) NOT NULL DEFAULT 0
           )""",
        # One-time best-effort seed from the older Allocation_Targets, for whichever
        # classes map 1:1 (Stock->Stocks, Bond->Bonds, Cash & Savings->Cash,
        # Commodity->Commodities, Crypto->Crypto). ETF has no equivalent here — its
        # value is now split across other classes via fund look-through — so it's
        # left out; ON CONFLICT DO NOTHING makes this purely a first-run seed, never
        # overwriting a target the user has since set directly on the new table.
        """INSERT INTO XRay_Allocation_Targets (Asset_Class, Target_Pct)
           SELECT CASE Securities_Type
                    WHEN 'Stock' THEN 'Stocks'
                    WHEN 'Bond' THEN 'Bonds'
                    WHEN 'Cash & Savings' THEN 'Cash'
                    WHEN 'Commodity' THEN 'Commodities'
                    WHEN 'Crypto' THEN 'Crypto'
                  END AS asset_class,
                  Target_Pct
           FROM Allocation_Targets
           WHERE Securities_Type IN ('Stock','Bond','Cash & Savings','Commodity','Crypto')
           ON CONFLICT (Asset_Class) DO NOTHING""",
        # Login auth (migration 014) — see api/routers/auth.py, api/deps.py.
        # Sessions.Token_Hash stores a SHA-256 hash of the session cookie's random
        # token, never the raw token, so a leaked DB backup can't hand over live sessions.
        """CREATE TABLE IF NOT EXISTS Users (
               Users_Id      SERIAL PRIMARY KEY,
               Username      VARCHAR(100) UNIQUE NOT NULL,
               Password_Hash TEXT NOT NULL,
               Created_At    TIMESTAMPTZ DEFAULT now()
           )""",
        """CREATE TABLE IF NOT EXISTS Sessions (
               Sessions_Id SERIAL PRIMARY KEY,
               Token_Hash  TEXT UNIQUE NOT NULL,
               Users_Id    INTEGER NOT NULL REFERENCES Users(Users_Id) ON DELETE CASCADE,
               Created_At  TIMESTAMPTZ DEFAULT now(),
               Expires_At  TIMESTAMPTZ NOT NULL
           )""",
        # Installment series (migration 015) — a Recurring_Templates row with
        # Total_Occurrences set generates its first occurrence like any normal
        # template, then confirming it generates the rest. See
        # database/queries.py::_confirm_draft_row.
        "ALTER TABLE Recurring_Templates ADD COLUMN IF NOT EXISTS Total_Occurrences INTEGER",
        """DO $$ BEGIN
               ALTER TABLE Recurring_Templates ADD CONSTRAINT recurring_templates_total_occurrences_check
                   CHECK (Total_Occurrences IS NULL OR Total_Occurrences >= 2);
           EXCEPTION WHEN duplicate_object THEN NULL;
           END $$""",
        # Installment series v2 (migration 016) — Total_Occurrences no longer deactivates the
        # template after one series; each cycle's draft, once confirmed, expands into a fresh
        # N-installment series spaced by Installment_Frequency, while the template itself keeps
        # recurring independently on Periodicity/Next_Due_Date/End_Date. See
        # database\queries.py::_maybe_expand_installment_series.
        "ALTER TABLE Recurring_Templates ADD COLUMN IF NOT EXISTS Installment_Frequency VARCHAR(20)",
        "ALTER TABLE Transactions ADD COLUMN IF NOT EXISTS Installment_Group_Id INTEGER",
        "ALTER TABLE Transactions ADD COLUMN IF NOT EXISTS Installment_Seq INTEGER",
        """CREATE INDEX IF NOT EXISTS idx_transactions_installment_group
               ON Transactions(Installment_Group_Id) WHERE Installment_Group_Id IS NOT NULL""",
    ]
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        try:
            just_bootstrapped = _bootstrap_schema_if_empty(conn)
        except Exception as bootstrap_exc:
            just_bootstrapped = False
            conn.rollback()
            log.warning("Schema bootstrap failed: %s", bootstrap_exc)
        if just_bootstrapped:
            try:
                _seed_reference_data(conn)
            except Exception as seed_exc:
                conn.rollback()
                log.warning("Reference data seeding failed: %s", seed_exc)
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