-- Add Show_In_Capital_Gains flag to Tax_Category_Rules
-- FALSE = exclude from capital gains report (e.g. CDs where maturity is not a gain event)
ALTER TABLE Tax_Category_Rules
    ADD COLUMN IF NOT EXISTS Show_In_Capital_Gains BOOLEAN NOT NULL DEFAULT TRUE;

-- CDs: maturity is principal return, not a capital gain — interest tracked via IntInc
UPDATE Tax_Category_Rules SET Show_In_Capital_Gains = FALSE WHERE Tax_Category = 'CD';
-- Bonds: coupon is IntInc; maturity gain is interest income, not a capital gain event
UPDATE Tax_Category_Rules SET Show_In_Capital_Gains = FALSE WHERE Tax_Category = 'Bond';
