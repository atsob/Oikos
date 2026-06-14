-- =============================================================================
-- Migration 007: Add 'Default' to corporate_action_type enum
-- =============================================================================
-- Adds the 'Default' value (company default / bankruptcy write-off) to the
-- existing corporate_action_type enum.  Safe to re-run.
-- =============================================================================

DO $$ BEGIN
    ALTER TYPE corporate_action_type ADD VALUE IF NOT EXISTS 'Default';
EXCEPTION WHEN others THEN NULL; END $$;
