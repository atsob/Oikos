-- =============================================================================
-- Migration 008: Add 'Dividend' to corporate_action_type enum
-- =============================================================================
DO $$ BEGIN
    ALTER TYPE corporate_action_type ADD VALUE IF NOT EXISTS 'Dividend';
EXCEPTION WHEN others THEN NULL; END $$;
