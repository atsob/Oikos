-- Fixes a balance-corruption risk for Pension/Brokerage/Other Investment/Margin
-- accounts. These account types maintain Accounts_Balance from the Investments
-- table (see database/crud.py: update_pension_balances / update_investment_balances),
-- not from Transactions -- database/crud.py's update_accounts_balances() already
-- excludes them from its generic Transactions-only recompute. This trigger,
-- however, had no such exclusion: any Transactions row touching one of these
-- accounts' Accounts_Id (directly, or as Accounts_Id_Target via the single-row
-- transfer model) silently overwrote its correct Investments-derived balance
-- with an incomplete/wrong sum. Every branch below now excludes these account
-- types, matching the Python-side exclusion. See CHANGELOG 2026-09-09.

CREATE OR REPLACE FUNCTION public.update_accounts_balance_with_transfer()
    RETURNS trigger
    LANGUAGE plpgsql
AS $$
BEGIN
    IF (TG_OP = 'INSERT') THEN
        IF NEW.Is_Draft THEN RETURN NULL; END IF;
        UPDATE Accounts
           SET Accounts_Balance = Accounts_Balance + NEW.Total_Amount
         WHERE Accounts_Id = NEW.Accounts_Id
           AND Accounts_Type NOT IN ('Pension', 'Brokerage', 'Other Investment', 'Margin');
        IF NEW.Accounts_Id_Target IS NOT NULL AND NEW.Total_Amount_Target IS NOT NULL THEN
            UPDATE Accounts
               SET Accounts_Balance = Accounts_Balance + NEW.Total_Amount_Target
             WHERE Accounts_Id = NEW.Accounts_Id_Target
               AND Accounts_Type NOT IN ('Pension', 'Brokerage', 'Other Investment', 'Margin');
        END IF;

    ELSIF (TG_OP = 'DELETE') THEN
        IF OLD.Is_Draft THEN RETURN NULL; END IF;
        UPDATE Accounts
           SET Accounts_Balance = Accounts_Balance - OLD.Total_Amount
         WHERE Accounts_Id = OLD.Accounts_Id
           AND Accounts_Type NOT IN ('Pension', 'Brokerage', 'Other Investment', 'Margin');
        IF OLD.Accounts_Id_Target IS NOT NULL AND OLD.Total_Amount_Target IS NOT NULL THEN
            UPDATE Accounts
               SET Accounts_Balance = Accounts_Balance - OLD.Total_Amount_Target
             WHERE Accounts_Id = OLD.Accounts_Id_Target
               AND Accounts_Type NOT IN ('Pension', 'Brokerage', 'Other Investment', 'Margin');
        END IF;

    ELSIF (TG_OP = 'UPDATE') THEN
        IF OLD.Is_Draft AND NEW.Is_Draft THEN
            RETURN NULL;
        ELSIF OLD.Is_Draft AND NOT NEW.Is_Draft THEN
            -- Draft → Confirmed: add balance
            UPDATE Accounts
               SET Accounts_Balance = Accounts_Balance + NEW.Total_Amount
             WHERE Accounts_Id = NEW.Accounts_Id
               AND Accounts_Type NOT IN ('Pension', 'Brokerage', 'Other Investment', 'Margin');
            IF NEW.Accounts_Id_Target IS NOT NULL AND NEW.Total_Amount_Target IS NOT NULL THEN
                UPDATE Accounts
                   SET Accounts_Balance = Accounts_Balance + NEW.Total_Amount_Target
                 WHERE Accounts_Id = NEW.Accounts_Id_Target
                   AND Accounts_Type NOT IN ('Pension', 'Brokerage', 'Other Investment', 'Margin');
            END IF;
        ELSIF NOT OLD.Is_Draft AND NEW.Is_Draft THEN
            -- Confirmed → Draft: remove balance
            UPDATE Accounts
               SET Accounts_Balance = Accounts_Balance - OLD.Total_Amount
             WHERE Accounts_Id = OLD.Accounts_Id
               AND Accounts_Type NOT IN ('Pension', 'Brokerage', 'Other Investment', 'Margin');
            IF OLD.Accounts_Id_Target IS NOT NULL AND OLD.Total_Amount_Target IS NOT NULL THEN
                UPDATE Accounts
                   SET Accounts_Balance = Accounts_Balance - OLD.Total_Amount_Target
                 WHERE Accounts_Id = OLD.Accounts_Id_Target
                   AND Accounts_Type NOT IN ('Pension', 'Brokerage', 'Other Investment', 'Margin');
            END IF;
        ELSE
            -- Both confirmed: normal update
            UPDATE Accounts
               SET Accounts_Balance = Accounts_Balance - OLD.Total_Amount
             WHERE Accounts_Id = OLD.Accounts_Id
               AND Accounts_Type NOT IN ('Pension', 'Brokerage', 'Other Investment', 'Margin');
            UPDATE Accounts
               SET Accounts_Balance = Accounts_Balance + NEW.Total_Amount
             WHERE Accounts_Id = NEW.Accounts_Id
               AND Accounts_Type NOT IN ('Pension', 'Brokerage', 'Other Investment', 'Margin');
        END IF;
    END IF;
    RETURN NULL;
END;
$$;
