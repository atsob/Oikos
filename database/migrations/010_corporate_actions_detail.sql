-- =============================================================================
-- Migration 010: Corporate Actions — detail columns + link to investments
-- =============================================================================
-- Adds Gross_Per_Share and Tax_Rate to Corporate_Actions so dividend /
-- return-of-capital parameters are persisted and editable.
-- Adds Corporate_Actions_Id FK to Investments so editing a CA can cascade
-- to the transactions it generated.
-- =============================================================================

ALTER TABLE corporate_actions
    ADD COLUMN IF NOT EXISTS Gross_Per_Share NUMERIC(18, 8),
    ADD COLUMN IF NOT EXISTS Tax_Rate        NUMERIC(7, 4);   -- 0–100 percent

ALTER TABLE investments
    ADD COLUMN IF NOT EXISTS Corporate_Actions_Id INTEGER
        REFERENCES Corporate_Actions(Corporate_Actions_Id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_investments_corporate_actions_id
    ON investments (Corporate_Actions_Id)
    WHERE Corporate_Actions_Id IS NOT NULL;
