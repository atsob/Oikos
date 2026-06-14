-- =============================================================================
-- Migration 006: Corporate Actions
-- =============================================================================
-- Adds a Corporate_Actions table to record security-level events such as
-- stock splits, reverse splits, mergers, spin-offs, etc.
-- Action_Type is an ENUM so valid values are enforced at the DB level.
-- Safe to re-run (IF NOT EXISTS / DO $$ throughout).
-- =============================================================================

-- ── ENUM type ─────────────────────────────────────────────────────────────────
DO $$ BEGIN
    CREATE TYPE corporate_action_type AS ENUM (
        'Split',
        'Reverse Split',
        'Merger',
        'Acquisition',
        'Spinoff',
        'Rights Issue',
        'Name Change',
        'Delisting',
        'Other'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ── Table ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS Corporate_Actions (
    Corporate_Actions_Id  SERIAL PRIMARY KEY,
    Securities_Id         INTEGER      NOT NULL REFERENCES Securities(Securities_Id),
    Action_Type           corporate_action_type NOT NULL,
    Effective_Date        DATE         NOT NULL,
    Ratio_New             NUMERIC,
    Ratio_Old             NUMERIC,
    Description           TEXT,
    Created_At            TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_corporate_actions_sec_date
    ON Corporate_Actions (Securities_Id, Effective_Date);
