-- =============================================================================
-- Migration 003: Multi-currency support for Investments table
--   • Rename Total_Amount → Total_Amount_AccCur  (account-currency amount)
--   • Add    Total_Amount_SecCur                  (security-native-currency amount)
--   • Add    FX_Rate                              (rate used at booking: sec_cur → acc_cur)
-- =============================================================================
-- Idempotency notes:
--   ALTER TABLE … RENAME COLUMN will fail if the column was already renamed.
--   Run the verification query at the bottom first to confirm the current state.
-- =============================================================================

-- Step 1: rename the existing column
ALTER TABLE Investments
    RENAME COLUMN Total_Amount TO Total_Amount_AccCur;

-- Step 2: add the two new columns
ALTER TABLE Investments
    ADD COLUMN IF NOT EXISTS Total_Amount_SecCur  NUMERIC(28, 18),
    ADD COLUMN IF NOT EXISTS FX_Rate               NUMERIC(20, 8)  DEFAULT 1.0;

-- Step 3: backfill rows where the security currency = account currency (trivial case)
--         FX_Rate = 1.0, both amounts are identical
-- Note: PostgreSQL UPDATE…FROM cannot reference the target table alias inside a JOIN,
--       so use comma-separated FROM tables instead.
UPDATE Investments
   SET Total_Amount_SecCur = Investments.Total_Amount_AccCur,
       FX_Rate              = 1.0
  FROM Securities s, Accounts a
 WHERE Investments.Securities_Id = s.Securities_Id
   AND Investments.Accounts_Id   = a.Accounts_Id
   AND s.Currencies_Id            = a.Currencies_Id
   AND Investments.Total_Amount_SecCur IS NULL;

-- Step 4: backfill account-level entries (Securities_Id IS NULL) —
--         these have no instrument, so sec and acc currency are the same
UPDATE Investments
   SET Total_Amount_SecCur = Total_Amount_AccCur,
       FX_Rate              = 1.0
 WHERE Securities_Id IS NULL
   AND Total_Amount_SecCur IS NULL;

-- Step 5: rebuild covering index to include the new columns
DROP INDEX IF EXISTS idx_investments_accsec_covering;
CREATE INDEX idx_investments_accsec_covering
    ON Investments(Accounts_Id, Securities_Id, Date DESC)
    INCLUDE (Action, Quantity, Total_Amount_AccCur, Total_Amount_SecCur, FX_Rate, Price_Per_Share);

-- Step 6 (optional): backfill remaining cross-currency rows from Historical_FX.
--   Rows where sec_currency != acc_currency and no same-rate backfill was possible
--   can be populated using the nearest available Historical_FX rate on or before
--   the investment date.  Uses a CTE because LATERAL inside UPDATE...FROM cannot
--   reference the target table.
WITH fx_data AS (
    SELECT
        i.Investments_Id,
        i.Total_Amount_AccCur,
        COALESCE(
            (SELECT hfx.FX_Rate
               FROM Historical_FX hfx
              WHERE hfx.Currencies_Id_1 = s.Currencies_Id
                AND hfx.Currencies_Id_2 = a.Currencies_Id
                AND hfx.Date <= i.Date
              ORDER BY hfx.Date DESC LIMIT 1),
            1.0 / NULLIF(
                (SELECT hfx.FX_Rate
                   FROM Historical_FX hfx
                  WHERE hfx.Currencies_Id_1 = a.Currencies_Id
                    AND hfx.Currencies_Id_2 = s.Currencies_Id
                    AND hfx.Date <= i.Date
                  ORDER BY hfx.Date DESC LIMIT 1),
                0),
            1.0
        ) AS fx
    FROM Investments i
    JOIN Securities s ON s.Securities_Id = i.Securities_Id
    JOIN Accounts   a ON a.Accounts_Id   = i.Accounts_Id
    WHERE i.Total_Amount_SecCur IS NULL
      AND s.Currencies_Id != a.Currencies_Id
)
UPDATE Investments
   SET FX_Rate             = fx_data.fx,
       Total_Amount_SecCur = ROUND(
           fx_data.Total_Amount_AccCur / NULLIF(fx_data.fx, 0),
           18)
  FROM fx_data
 WHERE Investments.Investments_Id = fx_data.Investments_Id;

-- =============================================================================
-- Verification (run after migration):
-- =============================================================================
-- \d Investments
--
-- SELECT COUNT(*) AS total,
--        COUNT(Total_Amount_SecCur) AS with_sec_cur,
--        COUNT(*) - COUNT(Total_Amount_SecCur) AS still_null
-- FROM Investments;
-- =============================================================================
