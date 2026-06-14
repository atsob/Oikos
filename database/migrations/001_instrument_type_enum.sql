-- =============================================================================
-- Migration 001: Convert Investments.Instrument_Type VARCHAR(50) → ENUM
-- =============================================================================
-- Run this script against the live database inside a transaction.
-- Roll back the entire transaction if the preflight check returns any rows.
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- Preflight: find any values that would fail the cast.
-- This SELECT must return 0 rows before the migration can proceed.
-- If rows appear, clean them up (UPDATE or NULL them out) and re-run.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    bad_count INTEGER;
BEGIN
    SELECT COUNT(*)
      INTO bad_count
      FROM Investments
     WHERE Instrument_Type IS NOT NULL
       AND Instrument_Type NOT IN (
           'Stock', 'ETF', 'Bond', 'CFD', 'CEF', 'CFDOnETF', 'CFDOnStock',
           'CFDOnIndex', 'CFDOnFutures', 'CFDOnFund', 'Fund', 'Option', 'Other'
       );

    IF bad_count > 0 THEN
        RAISE EXCEPTION
            'Preflight failed: % row(s) contain unrecognised Instrument_Type values. '
            'Run the diagnostic query below, fix the data, then re-run this migration.',
            bad_count;
    END IF;
END;
$$;

-- Diagnostic query (run manually to inspect bad rows before fixing):
-- SELECT DISTINCT Instrument_Type, COUNT(*) AS cnt
--   FROM Investments
--  WHERE Instrument_Type IS NOT NULL
--    AND Instrument_Type NOT IN (
--        'Stock','ETF','Bond','CFD','CEF','CFDOnETF','CFDOnStock',
--        'CFDOnIndex','CFDOnFutures','CFDOnFund','Fund','Option','Other'
--    )
--  GROUP BY Instrument_Type
--  ORDER BY Instrument_Type;

-- ---------------------------------------------------------------------------
-- Step 1: Create the ENUM type.
-- ---------------------------------------------------------------------------
CREATE TYPE Investments_Instrument_Type AS ENUM (
    'Stock', 'ETF', 'Bond', 'CFD', 'CEF', 'CFDOnETF', 'CFDOnStock',
    'CFDOnIndex', 'CFDOnFutures', 'CFDOnFund', 'Fund', 'Option', 'Other'
);

-- ---------------------------------------------------------------------------
-- Step 2: Cast the column. NULL values pass through unchanged.
-- ---------------------------------------------------------------------------
ALTER TABLE Investments
    ALTER COLUMN Instrument_Type TYPE Investments_Instrument_Type
    USING Instrument_Type::Investments_Instrument_Type;

COMMIT;

-- =============================================================================
-- Rollback (run only if you need to revert — do NOT run after COMMIT):
-- =============================================================================
-- BEGIN;
-- ALTER TABLE Investments
--     ALTER COLUMN Instrument_Type TYPE VARCHAR(50);
-- DROP TYPE Investments_Instrument_Type;
-- COMMIT;
-- =============================================================================
