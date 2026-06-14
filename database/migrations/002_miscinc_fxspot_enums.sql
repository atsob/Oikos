-- =============================================================================
-- Migration 002: Add MiscInc to Investments_Action and
--                FX Spot to Investments_Instrument_Type enums
-- =============================================================================
-- Run this script directly against the live database.
-- ALTER TYPE ... ADD VALUE cannot run inside a transaction block in
-- PostgreSQL < 12.  For PG 12+ the IF NOT EXISTS clause makes it idempotent.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Investments_Action: add MiscInc (income equivalent of MiscExp)
-- Used for: CFDCashAdjustment (dividend equivalents for CFD holders),
--           cash dividends on CFD positions, and similar miscellaneous income.
-- ---------------------------------------------------------------------------
ALTER TYPE Investments_Action ADD VALUE IF NOT EXISTS 'MiscInc';

-- ---------------------------------------------------------------------------
-- Investments_Instrument_Type: add FX Spot
-- Used for: SAXO FxSpot and FxForwards trades (Silver/Euro, EURUSD, etc.)
-- ---------------------------------------------------------------------------
ALTER TYPE Investments_Instrument_Type ADD VALUE IF NOT EXISTS 'FX Spot';

-- =============================================================================
-- Verify (run after migration):
-- =============================================================================
-- SELECT enumlabel
--   FROM pg_enum
--   JOIN pg_type ON pg_enum.enumtypid = pg_type.oid
--  WHERE pg_type.typname = 'investments_action'
--  ORDER BY enumsortorder;
--
-- SELECT enumlabel
--   FROM pg_enum
--   JOIN pg_type ON pg_enum.enumtypid = pg_type.oid
--  WHERE pg_type.typname = 'investments_instrument_type'
--  ORDER BY enumsortorder;
-- =============================================================================
