-- =============================================================================
-- Migration 012: Instrument Type Tax Override + Reinvest fix
-- =============================================================================

-- ── 1. Fix Reinvest_Taxable for Local/Foreign Listed ─────────────────────────
-- Reinvested dividends (scrip/DRIP) are treated as a capital event, not income.
UPDATE Tax_Category_Rules
SET Reinvest_Taxable = FALSE
WHERE Tax_Category IN ('Local Listed', 'Foreign Listed');


-- ── 2. Instrument type → effective tax category override table ────────────────
-- When an investment's Instrument_Type has an entry here, its Tax_Category
-- overrides the underlying security's Tax_Category for reporting purposes.
-- NULL Tax_Category_Override means "use the security's Tax_Category".

CREATE TABLE IF NOT EXISTS Instrument_Type_Tax_Override (
    Instrument_Type        VARCHAR(50) PRIMARY KEY,   -- matches Investments_Instrument_Type enum
    Tax_Category_Override  VARCHAR(50) REFERENCES Tax_Category_Rules(Tax_Category) ON DELETE SET NULL,
    Notes                  TEXT
);

INSERT INTO Instrument_Type_Tax_Override
    (Instrument_Type, Tax_Category_Override, Notes)
VALUES
    ('Stock',         NULL,          'Use underlying security''s Tax_Category'),
    ('ETF',           NULL,          'Use underlying security''s Tax_Category'),
    ('Bond',          'Bond',        'Always bond tax rules regardless of security'),
    ('CFD',           'Non-UCITS',   'All CFDs are fully taxable'),
    ('CEF',           'Non-UCITS',   'Closed-end funds treated as Non-UCITS'),
    ('CFDOnETF',      'Non-UCITS',   'UCITS exemption does not apply to CFD wrappers'),
    ('CFDOnStock',    NULL,          'Use underlying security''s Tax_Category'),
    ('CFDOnIndex',    'Non-UCITS',   'Index CFDs fully taxable'),
    ('CFDOnFutures',  'Non-UCITS',   'Futures CFDs fully taxable'),
    ('CFDOnFund',     'Non-UCITS',   'Fund CFDs fully taxable'),
    ('Fund',          NULL,          'Use underlying security''s Tax_Category'),
    ('Option',        'Other',       'Options — review manually'),
    ('FX Spot',       'Other',       'Includes commodity/FX pairs traded as FX Spot on SAXO'),
    ('Other',         'Other',       'Review manually')

ON CONFLICT (Instrument_Type) DO NOTHING;
