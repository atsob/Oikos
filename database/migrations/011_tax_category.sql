-- =============================================================================
-- Migration 011: Tax Category Rules + Securities.Tax_Category
-- =============================================================================

-- ── 1. Tax rules reference table ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS Tax_Category_Rules (
    Tax_Category            VARCHAR(50)  PRIMARY KEY,
    Display_Name            VARCHAR(100) NOT NULL,
    -- Capital gains
    Gains_Taxable           BOOLEAN      DEFAULT FALSE,
    Gains_Rate              NUMERIC(5,2),          -- NULL = exempt
    Gains_Tax_Code          VARCHAR(20),           -- e.g. '659-660'
    -- Dividends
    Dividend_Local_Tax_Rate NUMERIC(5,2),          -- e.g. 5.00 %
    Dividend_WHT_Creditable BOOLEAN      DEFAULT TRUE,
    -- Reinvested dividends
    Reinvest_Taxable        BOOLEAN      DEFAULT FALSE,
    -- Interest / other income (IntInc from CD, Bond, Crypto staking)
    Income_Tax_Rate         NUMERIC(5,2),          -- e.g. 15.00 %
    -- UI notes
    Notes                   TEXT
);

INSERT INTO Tax_Category_Rules
    (Tax_Category, Display_Name,
     Gains_Taxable, Gains_Rate, Gains_Tax_Code,
     Dividend_Local_Tax_Rate, Dividend_WHT_Creditable, Reinvest_Taxable,
     Income_Tax_Rate, Notes)
VALUES
    ('Local Listed',   'Local Listed Shares',
     FALSE, NULL, '659-660',
     5.00, TRUE, TRUE,
     NULL, 'Listed shares on local exchange. Capital gains exempt for retail (<0.5% holding). Dividends taxed at 5% with foreign WHT credit.'),

    ('Foreign Listed', 'Foreign Listed Shares',
     FALSE, NULL, NULL,
     5.00, TRUE, TRUE,
     NULL, 'Foreign shares under a tax treaty. Capital gains generally exempt. Dividends 5% with WHT credit.'),

    ('UCITS',          'UCITS EU Fund / ETF',
     FALSE, NULL, NULL,
     0.00, FALSE, FALSE,
     NULL, 'EU-domiciled UCITS funds. Both capital gains and reinvested dividends are exempt.'),

    ('Non-UCITS',      'Non-UCITS Fund / ETF',
     TRUE,  15.00, NULL,
     5.00, TRUE, TRUE,
     NULL, 'Non-UCITS funds and leveraged/inverse ETFs. Capital gains taxed at 15%, dividends at 5%.'),

    ('CD',             'Time Deposit / CD',
     FALSE, NULL, NULL,
     NULL, FALSE, FALSE,
     15.00, 'Interest income from time deposits and CDs taxed at 15% (withheld at source).'),

    ('Bond',           'Bond / Fixed Income',
     FALSE, NULL, NULL,
     NULL, FALSE, FALSE,
     15.00, 'Coupon interest taxed at 15%. Capital gains treatment depends on instrument.'),

    ('Crypto',         'Cryptocurrency',
     TRUE,  15.00, NULL,
     NULL, FALSE, TRUE,
     15.00, 'Capital gains taxed at 15%. Staking and other income taxed at 15% as income at receipt.'),

    ('Other',          'Other / Unclassified',
     NULL,  NULL, NULL,
     NULL, NULL, NULL,
     NULL, 'Review manually — tax treatment not determined.')

ON CONFLICT (Tax_Category) DO NOTHING;


-- ── 2. Tax_Category column on Securities ─────────────────────────────────────

ALTER TABLE Securities
    ADD COLUMN IF NOT EXISTS Tax_Category VARCHAR(50)
        REFERENCES Tax_Category_Rules(Tax_Category) ON DELETE SET NULL;


-- ── 3. Auto-populate from Securities_Type + exchange hints ───────────────────

-- CDs
UPDATE Securities SET Tax_Category = 'CD'
WHERE Tax_Category IS NULL AND Securities_Type = 'CD';

-- Crypto
UPDATE Securities SET Tax_Category = 'Crypto'
WHERE Tax_Category IS NULL AND Securities_Type = 'Crypto';

-- Bonds
UPDATE Securities SET Tax_Category = 'Bond'
WHERE Tax_Category IS NULL AND Securities_Type = 'Bond';

-- Non-UCITS (Closed-End Fund, CFD — clearly non-UCITS)
UPDATE Securities SET Tax_Category = 'Non-UCITS'
WHERE Tax_Category IS NULL
  AND Securities_Type IN ('CFD', 'Closed-End Fund');

-- ETF / Mutual Fund / PF_Unit → default UCITS (user can correct Non-UCITS cases)
UPDATE Securities SET Tax_Category = 'UCITS'
WHERE Tax_Category IS NULL
  AND Securities_Type IN ('ETF', 'Mutual Fund', 'PF_Unit');

-- Stocks: Local Listed if ATHEX indicators present
UPDATE Securities SET Tax_Category = 'Local Listed'
WHERE Tax_Category IS NULL
  AND Securities_Type = 'Stock'
  AND (
      Yahoo_Ticker ILIKE '%.AT'
   OR TV_Exchange  ILIKE '%ATH%'
   OR TV_Exchange  ILIKE '%XATH%'
   OR Ticker       ILIKE '%.AT'
  );

-- Stocks: Foreign Listed for everything else
UPDATE Securities SET Tax_Category = 'Foreign Listed'
WHERE Tax_Category IS NULL
  AND Securities_Type = 'Stock';

-- Everything else (FX Spot, Market Index, Emp. Stock Opt., Commodity, Other, Option)
UPDATE Securities SET Tax_Category = 'Other'
WHERE Tax_Category IS NULL;

-- Is_Tax_Exempt override: these are already exempt regardless of type
-- (e.g. Hellenic T-Bills marked exempt) — keep their existing Tax_Category
-- but do NOT force them to UCITS since they may be Bonds.
-- Users can review and correct via the Setup tab.
