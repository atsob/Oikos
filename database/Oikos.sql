-- =============================================================================
-- Oikos Personal Finance — PostgreSQL Schema
-- =============================================================================
-- Run on a fresh database to create the complete schema from scratch.
-- All object definitions are idempotent (IF NOT EXISTS / OR REPLACE).
-- For migrating an existing database see the "Migration notes" comments.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- =============================================================================
-- ENUM TYPES
-- =============================================================================

CREATE TYPE Institutions_Type AS ENUM (
    'Bank', 'Credit Union', 'Insurance', 'Pension Fund',
    'Broker', 'Crypto Exchange', 'Internal', 'Other'
);

CREATE TYPE Accounts_Type AS ENUM (
    'Cash', 'Checking', 'Savings', 'Credit Card',
    'Brokerage', 'Pension', 'Other Investment', 'Margin',
    'Loan', 'Real Estate', 'Vehicle', 'Asset', 'Liability', 'Other'
);

-- Migration: ALTER TYPE Securities_Type ADD VALUE IF NOT EXISTS 'CFD';
--            ALTER TYPE Securities_Type ADD VALUE IF NOT EXISTS 'Closed-End Fund';
CREATE TYPE Securities_Type AS ENUM (
    'Stock', 'ETF', 'Bond', 'Mutual Fund', 'Crypto',
    'Option', 'Commodity', 'PF_Unit', 'CD', 'Emp. Stock Opt.',
    'FX Spot', 'Market Index', 'CFD', 'Closed-End Fund', 'Other'
);

CREATE TYPE Categories_Type AS ENUM (
    'Income', 'Expense', 'Transfer', 'Trading',
    'Investment', 'Dividend', 'Interest', 'Tax', 'Fee'
);

CREATE TYPE Investments_Action AS ENUM (
    'Buy', 'Sell', 'Dividend', 'Reinvest', 'Split',
    'ShrIn', 'ShrOut', 'IntInc', 'CashIn', 'CashOut',
    'Grant', 'Vest', 'Exercise', 'Expire', 'MiscExp', 'MiscInc', 'RtrnCap'
);

-- Migration 001: CREATE TYPE then ALTER TABLE (see database/migrations/001_instrument_type_enum.sql)
-- Note: the live type is named "instrument_type" (not "Investments_Instrument_Type" as the
-- migration file's older draft has it) — kept in sync here for a faithful fresh-install schema.
CREATE TYPE instrument_type AS ENUM (
    'Stock', 'ETF', 'Bond', 'CFD', 'CEF', 'CFDOnETF', 'CFDOnStock',
    'CFDOnIndex', 'CFDOnFutures', 'CFDOnFund', 'Fund', 'Option', 'Other', 'FX Spot'
);

CREATE SEQUENCE IF NOT EXISTS transfers_id_seq START 1 INCREMENT 1;


-- =============================================================================
-- LOOKUP / REFERENCE TABLES
-- =============================================================================

-- Tax category rules (must be created before Securities which references it)
CREATE TABLE IF NOT EXISTS Tax_Category_Rules (
    Tax_Category            VARCHAR(50)  PRIMARY KEY,
    Display_Name            VARCHAR(100) NOT NULL,
    Gains_Taxable           BOOLEAN      DEFAULT FALSE,
    Gains_Rate              NUMERIC(5,2),
    Gains_Tax_Code          VARCHAR(20),
    Dividend_Local_Tax_Rate NUMERIC(5,2),
    Dividend_WHT_Creditable BOOLEAN      DEFAULT TRUE,
    Reinvest_Taxable        BOOLEAN      DEFAULT FALSE,
    Income_Tax_Rate         NUMERIC(5,2),
    Show_In_Capital_Gains   BOOLEAN      NOT NULL DEFAULT TRUE,
    Notes                   TEXT
);

INSERT INTO Tax_Category_Rules
    (Tax_Category, Display_Name,
     Gains_Taxable, Gains_Rate, Gains_Tax_Code,
     Dividend_Local_Tax_Rate, Dividend_WHT_Creditable, Reinvest_Taxable,
     Income_Tax_Rate, Notes)
VALUES
    ('Local Listed',   'Local Listed Shares',   FALSE, NULL, '659-660', 5.00, TRUE,  FALSE, NULL, 'Listed shares on local exchange. Capital gains exempt for retail (<0.5% holding). Dividends taxed at 5% with foreign WHT credit.'),
    ('Foreign Listed', 'Foreign Listed Shares',  FALSE, NULL, NULL,      5.00, TRUE,  FALSE, NULL, 'Foreign shares under a tax treaty. Capital gains generally exempt. Dividends 5% with WHT credit.'),
    ('UCITS',          'UCITS EU Fund / ETF',    FALSE, NULL, NULL,      0.00, FALSE, FALSE, NULL, 'EU-domiciled UCITS funds. Both capital gains and reinvested dividends are exempt.'),
    ('Non-UCITS',      'Non-UCITS Fund / ETF',   TRUE,  15.00, NULL,     5.00, TRUE,  TRUE,  NULL, 'Non-UCITS funds and leveraged/inverse ETFs. Capital gains taxed at 15%, dividends at 5%.'),
    ('CD',             'Time Deposit / CD',      FALSE, NULL, NULL,      NULL, FALSE, FALSE, 15.00,'Interest income from time deposits and CDs taxed at 15% (withheld at source).'),
    ('Bond',           'Bond / Fixed Income',    FALSE, NULL, NULL,      NULL, FALSE, FALSE, 15.00,'Coupon interest taxed at 15%. Capital gains treatment depends on instrument.'),
    ('Crypto',         'Cryptocurrency',         TRUE,  15.00, NULL,     NULL, FALSE, TRUE,  15.00,'Capital gains taxed at 15%. Staking and other income taxed at 15% as income at receipt.'),
    ('Other',          'Other / Unclassified',   NULL,  NULL, NULL,      NULL, NULL,  NULL,  NULL, 'Review manually — tax treatment not determined.')
ON CONFLICT (Tax_Category) DO NOTHING;

-- Instrument type → effective tax category override
CREATE TABLE IF NOT EXISTS Instrument_Type_Tax_Override (
    Instrument_Type        VARCHAR(50) PRIMARY KEY,
    Tax_Category_Override  VARCHAR(50) REFERENCES Tax_Category_Rules(Tax_Category) ON DELETE SET NULL,
    Notes                  TEXT
);

INSERT INTO Instrument_Type_Tax_Override (Instrument_Type, Tax_Category_Override, Notes) VALUES
    ('Stock',         NULL,         'Use underlying security''s Tax_Category'),
    ('ETF',           NULL,         'Use underlying security''s Tax_Category'),
    ('Bond',          'Bond',       'Always bond tax rules regardless of security'),
    ('CFD',           'Non-UCITS',  'All CFDs are fully taxable'),
    ('CEF',           'Non-UCITS',  'Closed-end funds treated as Non-UCITS'),
    ('CFDOnETF',      'Non-UCITS',  'UCITS exemption does not apply to CFD wrappers'),
    ('CFDOnStock',    NULL,         'Use underlying security''s Tax_Category'),
    ('CFDOnIndex',    'Non-UCITS',  'Index CFDs fully taxable'),
    ('CFDOnFutures',  'Non-UCITS',  'Futures CFDs fully taxable'),
    ('CFDOnFund',     'Non-UCITS',  'Fund CFDs fully taxable'),
    ('Fund',          NULL,         'Use underlying security''s Tax_Category'),
    ('Option',        'Other',      'Options — review manually'),
    ('FX Spot',       'Other',      'Includes commodity/FX pairs traded as FX Spot on SAXO'),
    ('Other',         'Other',      'Review manually')
ON CONFLICT (Instrument_Type) DO NOTHING;

CREATE TABLE Currencies (
    Currencies_Id       SERIAL PRIMARY KEY,
    Currencies_ShortName CHAR(3) UNIQUE NOT NULL,   -- EUR, USD, GBP, BTC …
    Currencies_Name     VARCHAR(100) NOT NULL,
    embedding           vector(768)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_currencies_id         ON Currencies(Currencies_Id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_currencies_shortname  ON Currencies(Currencies_ShortName);


CREATE TABLE IF NOT EXISTS Credit_Ratings_LT (
    Credit_Ratings_LT_Id INTEGER PRIMARY KEY,
    Quality              VARCHAR(25) NOT NULL,
    Description          VARCHAR(25) NOT NULL,
    Moodys               VARCHAR(4) UNIQUE NOT NULL,
    S_P                  VARCHAR(4) UNIQUE NOT NULL,
    Fitch                VARCHAR(4) UNIQUE NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_credit_ratings_lt_id ON Credit_Ratings_LT(Credit_Ratings_LT_Id);


CREATE TABLE Institutions (
    Institutions_Id   SERIAL PRIMARY KEY,
    Institutions_Name VARCHAR(100) NOT NULL,
    Institutions_Type Institutions_Type NOT NULL,
    BIC_Code          VARCHAR(11),
    Moodys            VARCHAR(4) REFERENCES Credit_Ratings_LT(Moodys),
    S_P               VARCHAR(4) REFERENCES Credit_Ratings_LT(S_P),
    Fitch             VARCHAR(4) REFERENCES Credit_Ratings_LT(Fitch),
    Contact           VARCHAR(100),
    Phone             VARCHAR(20),
    Email             VARCHAR(100),
    Website           VARCHAR(255),
    Notes             TEXT,
    embedding         vector(768)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_institutions_id   ON Institutions(Institutions_Id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_institutions_name ON Institutions(Institutions_Name);


CREATE TABLE Categories (
    Categories_Id        SERIAL PRIMARY KEY,
    Categories_Name      VARCHAR(100) NOT NULL,
    Categories_Id_Parent INTEGER REFERENCES Categories(Categories_Id) ON DELETE CASCADE,
    Categories_Type      Categories_Type NOT NULL,
    embedding            vector(768),
    UNIQUE(Categories_Name, Categories_Id_Parent)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_categories_id   ON Categories(Categories_Id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_categories_name ON Categories(Categories_Name, Categories_Id_Parent);


-- =============================================================================
-- SECURITIES
-- =============================================================================

CREATE TABLE Securities (
    Securities_Id        SERIAL PRIMARY KEY,
    Ticker               VARCHAR(255) UNIQUE NOT NULL,
    Securities_Name      VARCHAR(255) UNIQUE NOT NULL,
    Securities_Type      Securities_Type NOT NULL,
    Currencies_Id        INTEGER NOT NULL REFERENCES Currencies(Currencies_Id),
    Sector               VARCHAR(50),
    Industry             VARCHAR(50),
    Analyst_Rating       VARCHAR(20),
    Analyst_Target_Price NUMERIC(20, 8),
    Is_Active            BOOLEAN DEFAULT TRUE,
    Yahoo_Ticker         VARCHAR(30),
    TV_Symbol            VARCHAR(30),
    TV_Exchange          VARCHAR(30),
    ISIN                 VARCHAR(12),
    Maturity_Date        DATE,
    Coupon_Rate          NUMERIC(6, 4),
    Face_Value           NUMERIC(20, 8),
    Coupon_Frequency     VARCHAR(20) DEFAULT 'Annual',
    Is_Tax_Exempt        BOOLEAN DEFAULT FALSE,
    Tax_Category         VARCHAR(50) REFERENCES Tax_Category_Rules(Tax_Category) ON DELETE SET NULL,
    Dividend_Yield       NUMERIC(8,4),
    Dividend_Rate        NUMERIC(16,6),
    Dividend_Frequency   VARCHAR(20),
    Ex_Dividend_Date     DATE,
    Dividend_Pay_Date    DATE,
    Payout_Ratio         NUMERIC(8,4),
    Five_Year_Avg_Yield  NUMERIC(8,4),
    embedding            vector(768)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_securities_id           ON Securities(Securities_Id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_securities_name         ON Securities(Securities_Name);
CREATE        INDEX IF NOT EXISTS idx_securities_yahoo_ticker ON Securities(Yahoo_Ticker)  WHERE Yahoo_Ticker IS NOT NULL;
CREATE        INDEX IF NOT EXISTS idx_securities_tv_symbol    ON Securities(TV_Symbol)     WHERE TV_Symbol IS NOT NULL;

-- Historical dividend records
CREATE TABLE IF NOT EXISTS Securities_Dividends (
    Dividend_Id    SERIAL PRIMARY KEY,
    Securities_Id  INTEGER NOT NULL
                   REFERENCES Securities(Securities_Id) ON DELETE CASCADE,
    Ex_Date        DATE    NOT NULL,
    Pay_Date       DATE,
    Amount         NUMERIC(16,6) NOT NULL,
    UNIQUE (Securities_Id, Ex_Date)
);
CREATE INDEX IF NOT EXISTS idx_sec_div_sec_id  ON Securities_Dividends(Securities_Id);
CREATE INDEX IF NOT EXISTS idx_sec_div_ex_date ON Securities_Dividends(Ex_Date);

-- =============================================================================
-- ACCOUNTS & HOLDINGS
-- =============================================================================

CREATE TABLE Accounts (
    Accounts_Id       SERIAL PRIMARY KEY,
    Accounts_Name     VARCHAR(100) UNIQUE NOT NULL,
    Accounts_Type     Accounts_Type NOT NULL,
    Institutions_Id   INTEGER REFERENCES Institutions(Institutions_Id),
    IBAN              VARCHAR(34),
    Currencies_Id     INTEGER NOT NULL REFERENCES Currencies(Currencies_Id),
    Credit_Limit      NUMERIC(18, 2) DEFAULT 0,
    Is_Active         BOOLEAN DEFAULT TRUE,
    Accounts_Id_Linked INTEGER REFERENCES Accounts(Accounts_Id),  -- investment accounts: default linked cash account
    Accounts_Balance  NUMERIC(28, 18) DEFAULT 0,                  -- high precision for crypto/satoshi
    embedding         vector(768)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_id   ON Accounts(Accounts_Id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_name ON Accounts(Accounts_Name);


CREATE TABLE Holdings (
    Holdings_Id      SERIAL PRIMARY KEY,
    Accounts_Id      INTEGER NOT NULL REFERENCES Accounts(Accounts_Id) ON DELETE CASCADE,
    Securities_Id    INTEGER NOT NULL REFERENCES Securities(Securities_Id),
    Quantity         NUMERIC(28, 18) NOT NULL DEFAULT 0,
    Simple_Avg_Price  NUMERIC(20, 8),
    Fifo_Avg_Price    NUMERIC(20, 8),
    Fifo_Avg_Cost_EUR NUMERIC(20, 8),  -- weighted-avg EUR cost per share at historical FX (for unrealized P&L)
    Last_Update      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    Staking          BOOLEAN DEFAULT FALSE,  -- if TRUE, these holdings are currently staked/locked up and not available for trading
    embedding        vector(768),
    UNIQUE(Accounts_Id, Securities_Id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_holdings_id     ON Holdings(Holdings_Id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_holdings_accsec ON Holdings(Accounts_Id, Securities_Id);


-- =============================================================================
-- PAYEES
-- =============================================================================

CREATE TABLE Payees (
    Payees_Id           SERIAL PRIMARY KEY,
    Payees_Name         VARCHAR(255) UNIQUE NOT NULL,
    Categories_Id_Default INTEGER REFERENCES Categories(Categories_Id),
    Notes               TEXT,
    embedding           vector(768)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_payees_id   ON Payees(Payees_Id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_payees_name ON Payees(Payees_Name);


-- =============================================================================
-- BANK IMPORT — RECONCILIATION SESSIONS
-- (created before Transactions so the FK can reference it)
-- =============================================================================

CREATE TABLE IF NOT EXISTS Reconciliation_Sessions (
    Session_Id          SERIAL PRIMARY KEY,
    Accounts_Id         INTEGER REFERENCES Accounts(Accounts_Id) ON DELETE CASCADE,
    Session_Date        TIMESTAMP DEFAULT NOW(),
    Statement_Date      DATE,
    Statement_Balance   NUMERIC(18, 2),
    App_Balance         NUMERIC(18, 2),
    Difference          NUMERIC(18, 2),
    Transactions_Count  INTEGER,
    Status              VARCHAR(20) DEFAULT 'completed',
    Notes               TEXT
);


-- =============================================================================
-- TRANSACTIONS & SPLITS
-- =============================================================================

-- Recurring payment templates (defined before Transactions so the FK can reference it)
CREATE TABLE IF NOT EXISTS Recurring_Templates (
    Templates_Id        SERIAL PRIMARY KEY,
    Name                VARCHAR(100) NOT NULL,
    Accounts_Id         INTEGER NOT NULL REFERENCES Accounts(Accounts_Id) ON DELETE CASCADE,
    Payees_Id           INTEGER REFERENCES Payees(Payees_Id),
    Description         TEXT,
    Total_Amount        NUMERIC(28, 18),
    Periodicity         VARCHAR(20) NOT NULL DEFAULT 'Monthly',
    -- Allowed values: Daily | Weekly | Biweekly | Monthly | Quarterly | Semiannually | Annually
    Next_Due_Date       DATE NOT NULL,
    End_Date            DATE,                   -- NULL = runs indefinitely
    Auto_Confirm        BOOLEAN DEFAULT FALSE,  -- TRUE = bypass review queue (e.g. installments)
    Active              BOOLEAN DEFAULT TRUE,
    Accounts_Id_Target  INTEGER REFERENCES Accounts(Accounts_Id),
    Created_At          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS Recurring_Template_Splits (
    Splits_Id       SERIAL PRIMARY KEY,
    Templates_Id    INTEGER NOT NULL REFERENCES Recurring_Templates(Templates_Id) ON DELETE CASCADE,
    Categories_Id   INTEGER REFERENCES Categories(Categories_Id),
    Amount          NUMERIC(28, 18),
    Memo            TEXT
);

CREATE INDEX IF NOT EXISTS idx_recurring_templates_due
    ON Recurring_Templates(Next_Due_Date) WHERE Active = TRUE;


CREATE TABLE Transactions (
    Transactions_Id         SERIAL PRIMARY KEY,
    Accounts_Id             INTEGER NOT NULL REFERENCES Accounts(Accounts_Id) ON DELETE CASCADE,
    Date                    DATE NOT NULL,            -- future dates supported (scheduled installments)
    Payees_Id               INTEGER REFERENCES Payees(Payees_Id),
    Description             TEXT,
    Total_Amount            NUMERIC(28, 18),
    Cleared                 BOOLEAN DEFAULT FALSE,    -- bank-confirmed; FALSE for future/pending
    Accounts_Id_Target      INTEGER REFERENCES Accounts(Accounts_Id),
    Total_Amount_Target     NUMERIC(28, 18),          -- target-side amount (used for cross-currency transfers)
    Transfers_Id            INTEGER,                  -- shared value links the two legs of a transfer
    Reconciled              BOOLEAN DEFAULT FALSE,    -- set TRUE when matched during bank import reconciliation
    Reconciliation_Session_Id INTEGER REFERENCES Reconciliation_Sessions(Session_Id) ON DELETE SET NULL,
    Is_Draft                BOOLEAN DEFAULT FALSE,
    Templates_Id            INTEGER REFERENCES Recurring_Templates(Templates_Id) ON DELETE SET NULL,
    embedding               vector(768)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_id       ON Transactions(Transactions_Id);
CREATE        INDEX IF NOT EXISTS idx_transfers_id          ON Transactions(Transfers_Id) WHERE Transfers_Id IS NOT NULL;
CREATE        INDEX IF NOT EXISTS idx_transactions_transfers_id ON Transactions(Transfers_Id) WHERE Transfers_Id IS NOT NULL;
CREATE        INDEX IF NOT EXISTS idx_transactions_accounts_date ON Transactions(Accounts_Id, Date DESC);
CREATE        INDEX IF NOT EXISTS idx_transactions_date     ON Transactions(Date);
CREATE        INDEX IF NOT EXISTS idx_transactions_draft    ON Transactions(Is_Draft) WHERE Is_Draft = TRUE;


CREATE TABLE Splits (
    Splits_Id       SERIAL PRIMARY KEY,
    Transactions_Id INTEGER NOT NULL REFERENCES Transactions(Transactions_Id) ON DELETE CASCADE,
    Categories_Id   INTEGER REFERENCES Categories(Categories_Id),
    Amount          NUMERIC(28, 18),
    Memo            TEXT,
    embedding       vector(768)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_splits_id       ON Splits(Splits_Id);
CREATE        INDEX IF NOT EXISTS idx_splits_tx_id    ON Splits(Transactions_Id);
CREATE        INDEX IF NOT EXISTS idx_splits_category ON Splits(Categories_Id) WHERE Categories_Id IS NOT NULL;


-- =============================================================================
-- TRANSACTION BALANCE TRIGGER
-- =============================================================================

-- Draft transactions (Is_Draft = TRUE) never affect account balances until
-- they are confirmed (Is_Draft flipped to FALSE).
CREATE OR REPLACE FUNCTION public.update_accounts_balance_with_transfer()
    RETURNS trigger
    LANGUAGE plpgsql
AS $$
BEGIN
    IF (TG_OP = 'INSERT') THEN
        IF NEW.Is_Draft THEN RETURN NULL; END IF;
        UPDATE Accounts
           SET Accounts_Balance = Accounts_Balance + NEW.Total_Amount
         WHERE Accounts_Id = NEW.Accounts_Id;
        IF NEW.Accounts_Id_Target IS NOT NULL THEN
            UPDATE Accounts
               SET Accounts_Balance = Accounts_Balance + COALESCE(NEW.Total_Amount_Target, -NEW.Total_Amount)
             WHERE Accounts_Id = NEW.Accounts_Id_Target;
        END IF;

    ELSIF (TG_OP = 'DELETE') THEN
        IF OLD.Is_Draft THEN RETURN NULL; END IF;
        UPDATE Accounts
           SET Accounts_Balance = Accounts_Balance - OLD.Total_Amount
         WHERE Accounts_Id = OLD.Accounts_Id;
        IF OLD.Accounts_Id_Target IS NOT NULL THEN
            UPDATE Accounts
               SET Accounts_Balance = Accounts_Balance - COALESCE(OLD.Total_Amount_Target, -OLD.Total_Amount)
             WHERE Accounts_Id = OLD.Accounts_Id_Target;
        END IF;

    ELSIF (TG_OP = 'UPDATE') THEN
        IF OLD.Is_Draft AND NEW.Is_Draft THEN
            RETURN NULL;
        ELSIF OLD.Is_Draft AND NOT NEW.Is_Draft THEN
            -- Draft → Confirmed: add balance
            UPDATE Accounts
               SET Accounts_Balance = Accounts_Balance + NEW.Total_Amount
             WHERE Accounts_Id = NEW.Accounts_Id;
            IF NEW.Accounts_Id_Target IS NOT NULL THEN
                UPDATE Accounts
                   SET Accounts_Balance = Accounts_Balance + COALESCE(NEW.Total_Amount_Target, -NEW.Total_Amount)
                 WHERE Accounts_Id = NEW.Accounts_Id_Target;
            END IF;
        ELSIF NOT OLD.Is_Draft AND NEW.Is_Draft THEN
            -- Confirmed → Draft: remove balance
            UPDATE Accounts
               SET Accounts_Balance = Accounts_Balance - OLD.Total_Amount
             WHERE Accounts_Id = OLD.Accounts_Id;
            IF OLD.Accounts_Id_Target IS NOT NULL THEN
                UPDATE Accounts
                   SET Accounts_Balance = Accounts_Balance - COALESCE(OLD.Total_Amount_Target, -OLD.Total_Amount)
                 WHERE Accounts_Id = OLD.Accounts_Id_Target;
            END IF;
        ELSE
            -- Both confirmed: normal update
            UPDATE Accounts
               SET Accounts_Balance = Accounts_Balance - OLD.Total_Amount
             WHERE Accounts_Id = OLD.Accounts_Id;
            UPDATE Accounts
               SET Accounts_Balance = Accounts_Balance + NEW.Total_Amount
             WHERE Accounts_Id = NEW.Accounts_Id;
        END IF;
    END IF;
    RETURN NULL;
END;
$$;

CREATE TRIGGER trg_update_balance
AFTER INSERT OR UPDATE OR DELETE ON Transactions
FOR EACH ROW EXECUTE FUNCTION update_accounts_balance_with_transfer();


-- =============================================================================
-- CORPORATE ACTIONS
-- =============================================================================

CREATE TYPE corporate_action_type AS ENUM (
    'Split',
    'Reverse Split',
    'Merger',
    'Acquisition',
    'Spinoff',
    'Rights Issue',
    'Name Change',
    'Delisting',
    'Other',
    'Default',
    'Dividend',
    'Return of Capital'
);

CREATE TABLE IF NOT EXISTS Corporate_Actions (
    Corporate_Actions_Id  SERIAL PRIMARY KEY,
    Securities_Id         INTEGER      NOT NULL REFERENCES Securities(Securities_Id),
    Action_Type           corporate_action_type NOT NULL,
    Effective_Date        DATE         NOT NULL,
    Ratio_New             NUMERIC,
    Ratio_Old             NUMERIC,
    Gross_Per_Share       NUMERIC(18, 8),
    Tax_Rate              NUMERIC(7, 4),       -- 0–100 percent
    Description           TEXT,
    Created_At            TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_corporate_actions_sec_date
    ON Corporate_Actions (Securities_Id, Effective_Date);


-- =============================================================================
-- INVESTMENTS
-- =============================================================================

CREATE TABLE Investments (
    Investments_Id   SERIAL PRIMARY KEY,
    Accounts_Id      INTEGER NOT NULL REFERENCES Accounts(Accounts_Id) ON DELETE CASCADE,
    Securities_Id    INTEGER REFERENCES Securities(Securities_Id),
    Date             DATE NOT NULL,
    Action           Investments_Action NOT NULL,
    Quantity         NUMERIC(28, 18),
    Price_Per_Share  NUMERIC(20, 8),
    Commission          NUMERIC(20, 8) DEFAULT 0,
    Total_Amount_AccCur NUMERIC(28, 18),             -- total in the investment account currency
    Total_Amount_SecCur NUMERIC(28, 18),             -- total in the security's native currency
    FX_Rate             NUMERIC(20, 8) DEFAULT 1.0,  -- rate used at booking: sec_cur → acc_cur
    Description      TEXT,
    Transactions_Id  INTEGER REFERENCES Transactions(Transactions_Id),  -- linked cash-side row (BuyX/SellX/DivX)
    Instrument_Type  instrument_type,  -- optional: actual traded instrument (e.g. CFDOnETF, CFDOnStock)
                                                  -- overrides Security.Is_Tax_Exempt for tax calculations when set
    Tax_Amount          NUMERIC(18, 6),              -- withholding tax (negative = tax withheld, in account currency)
    Corporate_Actions_Id INTEGER REFERENCES Corporate_Actions(Corporate_Actions_Id) ON DELETE SET NULL,
    embedding        vector(768)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_investments_id          ON Investments(Investments_Id);
CREATE        INDEX IF NOT EXISTS idx_investments_linked_tx   ON Investments(Transactions_Id) WHERE Transactions_Id IS NOT NULL;
CREATE        INDEX IF NOT EXISTS idx_investments_accsec_date ON Investments(Accounts_Id, Securities_Id, Date DESC);
CREATE        INDEX IF NOT EXISTS idx_investments_action      ON Investments(Action);
CREATE        INDEX IF NOT EXISTS idx_investments_date        ON Investments(Date);
-- Covering index for P&L reconstruction — eliminates heap fetches for common columns
CREATE        INDEX IF NOT EXISTS idx_investments_accsec_covering
    ON Investments(Accounts_Id, Securities_Id, Date DESC)
    INCLUDE (Action, Quantity, Total_Amount_AccCur, Total_Amount_SecCur, FX_Rate, Price_Per_Share);
CREATE        INDEX IF NOT EXISTS idx_investments_corporate_actions_id
    ON Investments(Corporate_Actions_Id) WHERE Corporate_Actions_Id IS NOT NULL;


-- =============================================================================
-- HOLDINGS TRIGGER (Quantity safety net — cost basis is Python's job)
-- =============================================================================

-- This trigger only guarantees Holdings.Quantity stays correct immediately after
-- any INSERT/UPDATE on Investments — a plain signed net sum, which needs no lot
-- matching and is always right regardless of long/short history. It deliberately
-- does NOT attempt Simple_Avg_Price / Fifo_Avg_Price / Fifo_Avg_Cost_EUR: those
-- require proper signed-lot FIFO matching (to handle short positions and
-- negative-quantity adjustment rows correctly), which is done authoritatively by
-- database.crud.update_holdings() (Python), called by every code path that
-- writes to Investments. The previous version of this trigger tried to
-- approximate FIFO here by comparing aggregate total-buys-vs-total-sells sign
-- for the whole account/security history, which broke silently whenever a
-- position went both long and short over its lifetime, or had negative-quantity
-- Reinvest rows (e.g. daily interest-accrual reversals on cash-fund holdings) —
-- in one observed case making a real ~2.8-unit holding vanish (Quantity came out
-- as exactly 0 and the Holdings row got deleted).
CREATE OR REPLACE FUNCTION public.update_holdings_from_investments()
    RETURNS trigger
    LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO Holdings (Accounts_Id, Securities_Id, Quantity, Last_Update)
    SELECT
        NEW.Accounts_Id,
        NEW.Securities_Id,
        COALESCE(SUM(
            CASE WHEN Action IN ('Buy', 'Reinvest', 'ShrIn') THEN Quantity
                 WHEN Action IN ('Sell', 'ShrOut')           THEN -Quantity
                 ELSE 0 END
        ), 0),
        CURRENT_TIMESTAMP
    FROM Investments
    WHERE Accounts_Id  = NEW.Accounts_Id
      AND Securities_Id = NEW.Securities_Id
      AND Action IN ('Buy', 'Reinvest', 'ShrIn', 'Sell', 'ShrOut')
    ON CONFLICT (Accounts_Id, Securities_Id)
    DO UPDATE SET
        Quantity    = EXCLUDED.Quantity,
        Last_Update = EXCLUDED.Last_Update;

    RETURN NEW;
END;
$$;


-- =============================================================================
-- HISTORICAL MARKET DATA
-- =============================================================================

CREATE TABLE Historical_Prices (
    Securities_Id   INTEGER REFERENCES Securities(Securities_Id) ON DELETE CASCADE,
    Date            DATE NOT NULL,
    Close           NUMERIC(20, 8) NOT NULL,
    High            NUMERIC(20, 8),
    Low             NUMERIC(20, 8),
    Volume          BIGINT,
    Source          VARCHAR(50),
    Downloaded_At   TIMESTAMPTZ,
    embedding       vector(768),
    PRIMARY KEY (Securities_Id, Date)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_price_id       ON Historical_Prices(Securities_Id, Date);
CREATE        INDEX IF NOT EXISTS idx_prices_sec_date ON Historical_Prices(Securities_Id, Date DESC);
CREATE INDEX IF NOT EXISTS idx_price_source ON Historical_Prices(Source);

-- Migration (run once on existing databases):
-- ALTER TABLE Historical_Prices
--     ADD COLUMN IF NOT EXISTS Source        VARCHAR(50),
--     ADD COLUMN IF NOT EXISTS Downloaded_At TIMESTAMPTZ;
-- CREATE INDEX IF NOT EXISTS idx_price_source ON Historical_Prices(Source);


CREATE TABLE Historical_FX (
    Currencies_Id_1 INTEGER REFERENCES Currencies(Currencies_Id),
    Currencies_Id_2 INTEGER REFERENCES Currencies(Currencies_Id),
    Date            DATE NOT NULL,
    FX_Rate         NUMERIC(18, 10) NOT NULL,
    embedding       vector(768),
    PRIMARY KEY (Currencies_Id_1, Currencies_Id_2, Date)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_fxrate_id       ON Historical_FX(Currencies_Id_1, Currencies_Id_2, Date);
CREATE        INDEX IF NOT EXISTS idx_fx_currency_date ON Historical_FX(Currencies_Id_1, Date DESC);


-- =============================================================================
-- TRANSFER ISSUES  (audit log for unmatched / problematic transfers)
-- =============================================================================

CREATE TABLE IF NOT EXISTS Transfer_Issues (
    Issue_Id         SERIAL PRIMARY KEY,
    Issue_Type       VARCHAR(50) NOT NULL,
    Status           VARCHAR(20) NOT NULL DEFAULT 'Open',
    Transactions_Id_A INTEGER REFERENCES Transactions(Transactions_Id) ON DELETE CASCADE,
    Transactions_Id_B INTEGER REFERENCES Transactions(Transactions_Id) ON DELETE CASCADE,
    Date_A           DATE,
    Date_B           DATE,
    Amount_A         NUMERIC(28, 18),
    Amount_B         NUMERIC(28, 18),
    Accounts_Id_A    INTEGER REFERENCES Accounts(Accounts_Id),
    Accounts_Id_B    INTEGER REFERENCES Accounts(Accounts_Id),
    Description_A    TEXT,
    Description_B    TEXT,
    Notes            TEXT,
    Created_At       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    Resolved_At      TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_transfer_issues_status ON Transfer_Issues(Status);


-- =============================================================================
-- APP SETTINGS  (key-value store for user preferences)
-- =============================================================================

CREATE TABLE IF NOT EXISTS App_Settings (
    Key   TEXT PRIMARY KEY,
    Value TEXT
);


-- =============================================================================
-- GOALS
-- =============================================================================

CREATE TABLE IF NOT EXISTS Goals (
    Goal_Id        SERIAL PRIMARY KEY,
    Goal_Name      VARCHAR(200) NOT NULL,
    Target_Amount  NUMERIC(15, 2) NOT NULL,
    Target_Date    DATE,
    Current_Amount NUMERIC(15, 2) DEFAULT 0,
    Notes          TEXT,
    Is_Active      BOOLEAN DEFAULT TRUE,
    Created_At     TIMESTAMP DEFAULT NOW()
);


-- =============================================================================
-- BUDGETS
-- =============================================================================

CREATE TABLE IF NOT EXISTS Budgets (
    Budget_Id     SERIAL PRIMARY KEY,
    Year          INTEGER NOT NULL,
    Month         INTEGER NOT NULL,
    Categories_Id INTEGER NOT NULL REFERENCES Categories(Categories_Id),
    Budget_Amount NUMERIC(15, 2) NOT NULL,
    UNIQUE(Year, Month, Categories_Id)
);

CREATE TABLE IF NOT EXISTS Annual_Budgets (
    Budget_Id     SERIAL PRIMARY KEY,
    Year          INTEGER NOT NULL,
    Categories_Id INTEGER NOT NULL REFERENCES Categories(Categories_Id),
    Budget_Amount NUMERIC(15, 2) NOT NULL,
    UNIQUE(Year, Categories_Id)
);


-- =============================================================================
-- BANK IMPORT — PROFILES, RULES & HISTORY
-- =============================================================================

CREATE TABLE IF NOT EXISTS Import_Profiles (
    Profile_Id            SERIAL PRIMARY KEY,
    Profile_Name          VARCHAR(100) UNIQUE NOT NULL,
    Bank_Name             VARCHAR(100),
    File_Type             VARCHAR(10)  DEFAULT 'xlsx',
    Date_Column           VARCHAR(100),
    Description_Column    VARCHAR(100),
    Debit_Column          VARCHAR(100),
    Credit_Column         VARCHAR(100),
    Amount_Column         VARCHAR(100),
    Balance_Column        VARCHAR(100),
    Date_Format           VARCHAR(30)  DEFAULT '%d/%m/%Y',
    Encoding              VARCHAR(20)  DEFAULT 'utf-8',
    Skip_Rows             INTEGER      DEFAULT 0,
    Decimal_Separator     VARCHAR(1)   DEFAULT '.',
    Thousands_Separator   VARCHAR(1)   DEFAULT ',',
    Sign_Convention       VARCHAR(20)  DEFAULT 'debit_credit',
    Invert_Amounts        BOOLEAN      DEFAULT FALSE,
    Installment_Column    VARCHAR(100) DEFAULT '',
    Secondary_Date_Column VARCHAR(100) DEFAULT '',
    Created_At            TIMESTAMP    DEFAULT NOW()
);


CREATE TABLE IF NOT EXISTS Payee_Rules (
    Rule_Id       SERIAL PRIMARY KEY,
    Pattern       VARCHAR(500) NOT NULL,
    Match_Type    VARCHAR(20) DEFAULT 'contains',   -- contains | starts_with | exact | regex
    Payees_Id     INTEGER REFERENCES Payees(Payees_Id) ON DELETE SET NULL,
    Categories_Id INTEGER REFERENCES Categories(Categories_Id) ON DELETE SET NULL,
    Priority      INTEGER DEFAULT 0,
    Created_At    TIMESTAMP DEFAULT NOW()
);


-- Remembers the Reconcile / Import / Skip decision for each
-- (account, normalised-description, amount-sign) triple so that
-- future imports can pre-fill the same action automatically.
CREATE TABLE IF NOT EXISTS Import_Statement_History (
    History_Id      SERIAL PRIMARY KEY,
    Accounts_Id     INTEGER NOT NULL REFERENCES Accounts(Accounts_Id) ON DELETE CASCADE,
    Description_Key TEXT    NOT NULL,   -- lower-cased, whitespace-collapsed, [CCY] prefix stripped
    Amount_Sign     SMALLINT NOT NULL DEFAULT 0,   -- +1 credit / -1 debit / 0 unknown
    Last_Action     VARCHAR(20) NOT NULL,          -- 'Import' | 'Reconcile' | 'Skip'
    Payees_Id       INTEGER REFERENCES Payees(Payees_Id) ON DELETE SET NULL,
    Categories_Id   INTEGER REFERENCES Categories(Categories_Id) ON DELETE SET NULL,
    Last_Seen       TIMESTAMP DEFAULT NOW(),
    Seen_Count      INTEGER DEFAULT 1,
    UNIQUE(Accounts_Id, Description_Key, Amount_Sign)
);


-- =============================================================================
-- AI SUMMARIES
-- =============================================================================

CREATE TABLE IF NOT EXISTS AI_Weekly_Summaries (
    Summary_Id   SERIAL PRIMARY KEY,
    Week_Start   DATE NOT NULL UNIQUE,
    Generated_At TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    Summary_Text TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_weekly_summaries_week ON AI_Weekly_Summaries(Week_Start);

CREATE TABLE IF NOT EXISTS AI_Monthly_Summaries (
    Summary_Id   SERIAL PRIMARY KEY,
    Month_Start  DATE NOT NULL UNIQUE,
    Generated_At TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    Summary_Text TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_monthly_summaries_month ON AI_Monthly_Summaries(Month_Start);


-- =============================================================================
-- REPORTING & ANALYTICS
-- =============================================================================

CREATE TABLE IF NOT EXISTS Allocation_Targets (
    Securities_Type VARCHAR(50) PRIMARY KEY,
    Target_Pct      NUMERIC(5, 2) NOT NULL DEFAULT 0,
    Notes           TEXT
);
-- Seed with zeros; update via Settings UI or direct SQL
INSERT INTO Allocation_Targets (Securities_Type, Target_Pct) VALUES
    ('Stock',        0),
    ('ETF',          0),
    ('Bond',         0),
    ('Mutual Fund',  0),
    ('Crypto',       0),
    ('Option',       0),
    ('Commodity',    0),
    ('PF_Unit',      0),
    ('CFD',          0),
    ('Closed-End Fund', 0)
ON CONFLICT (Securities_Type) DO NOTHING;


CREATE TABLE IF NOT EXISTS Benchmark_Presets (
    Preset_Id   SERIAL PRIMARY KEY,
    Preset_Name VARCHAR(100) UNIQUE NOT NULL,
    Account_Ids INTEGER[] NOT NULL DEFAULT '{}',
    Created_At  TIMESTAMP DEFAULT NOW(),
    Updated_At  TIMESTAMP DEFAULT NOW()
);

-- Saved account-selection presets for Inv. Performance / Correlation reports
CREATE TABLE IF NOT EXISTS Portfolio_Presets (
    Preset_Id   SERIAL PRIMARY KEY,
    Preset_Name VARCHAR(100) UNIQUE NOT NULL,
    Account_Ids INTEGER[] NOT NULL DEFAULT '{}',
    Created_At  TIMESTAMP DEFAULT NOW(),
    Updated_At  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS Custom_Report_Presets (
    Preset_Id   SERIAL PRIMARY KEY,
    Preset_Name VARCHAR(100) UNIQUE NOT NULL,
    Config      JSONB NOT NULL DEFAULT '{}',
    Created_At  TIMESTAMP DEFAULT NOW(),
    Updated_At  TIMESTAMP DEFAULT NOW()
);

-- Server-side UI preferences (decimal/date format, saved report filters, last-used
-- tabs, etc.) so they follow the user across browsers/devices/origins instead of
-- being trapped in a single browser's localStorage.
CREATE TABLE IF NOT EXISTS User_Preferences (
    Pref_Key   VARCHAR(100) PRIMARY KEY,
    Pref_Value JSONB NOT NULL,
    Updated_At TIMESTAMP DEFAULT NOW()
);


-- =============================================================================
-- IMPORT SUPPORT TABLES
-- =============================================================================

CREATE TABLE IF NOT EXISTS import_ignored_records (
    source     TEXT        NOT NULL,
    desc_key   TEXT        NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (source, desc_key)
);

CREATE TABLE IF NOT EXISTS import_security_mappings (
    source          TEXT    NOT NULL,
    source_symbol   TEXT    NOT NULL,
    securities_id   INTEGER NOT NULL,
    PRIMARY KEY (source, source_symbol)
);

-- =============================================================================
-- WATCHLIST & ALERTS
-- =============================================================================

CREATE TABLE IF NOT EXISTS Watchlist (
    Watchlist_Id  SERIAL PRIMARY KEY,
    Securities_Id INTEGER NOT NULL REFERENCES Securities(Securities_Id) ON DELETE CASCADE,
    Target_Price  NUMERIC(20,8),
    Stop_Loss     NUMERIC(20,8),
    Note          TEXT,
    Added_Date    DATE DEFAULT CURRENT_DATE,
    UNIQUE(Securities_Id)
);

CREATE TABLE IF NOT EXISTS Alerts (
    Alert_Id      SERIAL PRIMARY KEY,
    Alert_Type    TEXT NOT NULL,
    Securities_Id INTEGER REFERENCES Securities(Securities_Id) ON DELETE CASCADE,
    Asset_Type    TEXT,
    Threshold     NUMERIC(20,8),
    Direction     TEXT,
    Is_Active     BOOLEAN DEFAULT TRUE,
    Note          TEXT,
    Created_At    TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS Signal_Notifications (
    Securities_Id     INTEGER PRIMARY KEY
                      REFERENCES Securities(Securities_Id) ON DELETE CASCADE,
    Last_Known_Signal TEXT,
    Previous_Signal   TEXT,
    Changed_At        TIMESTAMP DEFAULT NOW(),
    Acknowledged      BOOLEAN DEFAULT TRUE
);


-- =============================================================================
-- SCHEDULER
-- =============================================================================

CREATE TABLE IF NOT EXISTS Scheduler_Jobs (
    job_id       VARCHAR(80)  PRIMARY KEY,
    name         VARCHAR(200) NOT NULL,
    description  TEXT,
    schedule     VARCHAR(200),
    enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    last_run     TIMESTAMPTZ,
    last_status  VARCHAR(20),
    last_message TEXT
);


-- =============================================================================
-- MATERIALIZED VIEWS  (refresh: SELECT refresh_mv_prices_fx();)
-- =============================================================================

-- Fast latest-price lookup (replaces expensive DISTINCT ON correlated subqueries)
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_latest_prices AS
    SELECT DISTINCT ON (Securities_Id)
        Securities_Id,
        Date  AS price_date,
        Close AS latest_close
    FROM Historical_Prices
    ORDER BY Securities_Id, Date DESC
WITH DATA;
CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_latest_prices ON mv_latest_prices(Securities_Id);

-- Fast latest-FX lookup
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_latest_fx AS
    SELECT DISTINCT ON (Currencies_Id_1)
        Currencies_Id_1,
        Date    AS fx_date,
        FX_Rate AS latest_rate
    FROM Historical_FX
    ORDER BY Currencies_Id_1, Date DESC
WITH DATA;
CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_latest_fx ON mv_latest_fx(Currencies_Id_1);

-- Refresh both views in one call
CREATE OR REPLACE FUNCTION refresh_mv_prices_fx()
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_latest_prices;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_latest_fx;
END;
$$;


-- =============================================================================
-- COLLATION REFRESH  (run after PostgreSQL upgrades)
-- =============================================================================
-- ALTER DATABASE "Finance" REFRESH COLLATION VERSION;


-- =============================================================================
-- VIEWS
-- =============================================================================

-- v_transactions_eur: all transactions with amounts converted to EUR via the
-- latest available FX rate. Used by the AI assistant so it can aggregate
-- spending/income in EUR without writing multi-currency FX joins.
-- Exclude internal transfers by filtering WHERE accounts_id_target IS NULL.
CREATE OR REPLACE VIEW v_transactions_eur AS
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
LEFT JOIN payees p ON p.payees_id      = t.payees_id;


-- =============================================================================
-- HISTORICAL_PRICES PARTITIONING  (migration guide — apply when table is large)
-- =============================================================================
--
-- 1. Rename original:
--    ALTER TABLE Historical_Prices RENAME TO Historical_Prices_old;
--
-- 2. Create partitioned table:
--    CREATE TABLE Historical_Prices (
--        Securities_Id INTEGER NOT NULL REFERENCES Securities(Securities_Id) ON DELETE CASCADE,
--        Date DATE NOT NULL,
--        Close NUMERIC(20, 8) NOT NULL,
--        High NUMERIC(20, 8), Low NUMERIC(20, 8), Volume BIGINT,
--        embedding vector(768),
--        PRIMARY KEY (Securities_Id, Date)
--    ) PARTITION BY RANGE (Date);
--
-- 3. Create year partitions:
--    CREATE TABLE Historical_Prices_2020 PARTITION OF Historical_Prices
--        FOR VALUES FROM ('2020-01-01') TO ('2021-01-01');
--    -- repeat for each year…
--
-- 4. Copy data and drop old table:
--    INSERT INTO Historical_Prices SELECT * FROM Historical_Prices_old;
--    DROP TABLE Historical_Prices_old;
