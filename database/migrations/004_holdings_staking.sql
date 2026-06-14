-- Add Staking flag to Holdings
-- When true, a manual quantity increase in Edit Holdings automatically creates
-- a Reinvest investment entry for the difference (staking rewards).

ALTER TABLE Holdings ADD COLUMN IF NOT EXISTS Staking BOOLEAN DEFAULT FALSE;
