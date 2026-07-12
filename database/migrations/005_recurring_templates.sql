-- =============================================================================
-- Migration 005: Recurring Templates
-- =============================================================================
-- Column naming follows the schema convention: all PKs and FKs use the plural
-- table-name prefix (Accounts_Id, Payees_Id, Transactions_Id, Templates_Id …).
-- Safe to re-run (IF NOT EXISTS / OR REPLACE throughout).
-- =============================================================================

-- ── New tables ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS Recurring_Templates (
    Templates_Id        SERIAL PRIMARY KEY,
    Name                VARCHAR(100) NOT NULL,
    Accounts_Id         INTEGER NOT NULL REFERENCES Accounts(Accounts_Id) ON DELETE CASCADE,
    Payees_Id           INTEGER REFERENCES Payees(Payees_Id),
    Description         TEXT,
    Total_Amount        NUMERIC(28, 18),
    Periodicity         VARCHAR(20) NOT NULL DEFAULT 'Monthly',
    -- Allowed values: Daily | Weekly | Biweekly | Monthly | Quarterly | Semiannually | Annually
    Next_Due_Date       DATE NOT NULL,
    End_Date            DATE,                   -- NULL = runs indefinitely
    Auto_Confirm        BOOLEAN DEFAULT FALSE,  -- TRUE = bypass review queue (e.g. installments)
    Active              BOOLEAN DEFAULT TRUE,
    Accounts_Id_Target  INTEGER REFERENCES Accounts(Accounts_Id),
    Created_At          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS Recurring_Template_Splits (
    Splits_Id       SERIAL PRIMARY KEY,
    Templates_Id    INTEGER NOT NULL REFERENCES Recurring_Templates(Templates_Id) ON DELETE CASCADE,
    Categories_Id   INTEGER REFERENCES Categories(Categories_Id),
    Amount          NUMERIC(28, 18),
    Memo            TEXT
);

CREATE INDEX IF NOT EXISTS idx_recurring_templates_due
    ON Recurring_Templates(Next_Due_Date) WHERE Active = TRUE;

-- ── Extend Transactions ───────────────────────────────────────────────────────

ALTER TABLE Transactions
    ADD COLUMN IF NOT EXISTS Is_Draft    BOOLEAN DEFAULT FALSE;

ALTER TABLE Transactions
    ADD COLUMN IF NOT EXISTS Templates_Id INTEGER
        REFERENCES Recurring_Templates(Templates_Id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_transactions_draft
    ON Transactions(Is_Draft) WHERE Is_Draft = TRUE;

-- ── Draft-aware balance trigger ───────────────────────────────────────────────
-- Draft transactions (Is_Draft = TRUE) never affect account balances until
-- they are confirmed (Is_Draft flipped to FALSE).

-- The Accounts_Id_Target branches below fire only when Total_Amount_Target is
-- explicitly set, not merely whenever Accounts_Id_Target is set — see the
-- matching comment in database/Oikos.sql for the full rationale (distinguishes
-- the single-row transfer model, which relies on this branch, from the
-- two-row model, where each row already updates its own account and this
-- branch used to double-count).
CREATE OR REPLACE FUNCTION public.update_accounts_balance_with_transfer()
    RETURNS trigger
    LANGUAGE plpgsql
AS $$
BEGIN
    IF (TG_OP = 'INSERT') THEN
        IF NEW.Is_Draft THEN RETURN NULL; END IF;
        UPDATE Accounts
           SET Accounts_Balance = Accounts_Balance + NEW.Total_Amount
         WHERE Accounts_Id = NEW.Accounts_Id;
        IF NEW.Accounts_Id_Target IS NOT NULL AND NEW.Total_Amount_Target IS NOT NULL THEN
            UPDATE Accounts
               SET Accounts_Balance = Accounts_Balance + NEW.Total_Amount_Target
             WHERE Accounts_Id = NEW.Accounts_Id_Target;
        END IF;

    ELSIF (TG_OP = 'DELETE') THEN
        IF OLD.Is_Draft THEN RETURN NULL; END IF;
        UPDATE Accounts
           SET Accounts_Balance = Accounts_Balance - OLD.Total_Amount
         WHERE Accounts_Id = OLD.Accounts_Id;
        IF OLD.Accounts_Id_Target IS NOT NULL AND OLD.Total_Amount_Target IS NOT NULL THEN
            UPDATE Accounts
               SET Accounts_Balance = Accounts_Balance - OLD.Total_Amount_Target
             WHERE Accounts_Id = OLD.Accounts_Id_Target;
        END IF;

    ELSIF (TG_OP = 'UPDATE') THEN
        IF OLD.Is_Draft AND NEW.Is_Draft THEN
            RETURN NULL;
        ELSIF OLD.Is_Draft AND NOT NEW.Is_Draft THEN
            -- Draft → Confirmed: add balance
            UPDATE Accounts
               SET Accounts_Balance = Accounts_Balance + NEW.Total_Amount
             WHERE Accounts_Id = NEW.Accounts_Id;
            IF NEW.Accounts_Id_Target IS NOT NULL AND NEW.Total_Amount_Target IS NOT NULL THEN
                UPDATE Accounts
                   SET Accounts_Balance = Accounts_Balance + NEW.Total_Amount_Target
                 WHERE Accounts_Id = NEW.Accounts_Id_Target;
            END IF;
        ELSIF NOT OLD.Is_Draft AND NEW.Is_Draft THEN
            -- Confirmed → Draft: remove balance
            UPDATE Accounts
               SET Accounts_Balance = Accounts_Balance - OLD.Total_Amount
             WHERE Accounts_Id = OLD.Accounts_Id;
            IF OLD.Accounts_Id_Target IS NOT NULL AND OLD.Total_Amount_Target IS NOT NULL THEN
                UPDATE Accounts
                   SET Accounts_Balance = Accounts_Balance - OLD.Total_Amount_Target
                 WHERE Accounts_Id = OLD.Accounts_Id_Target;
            END IF;
        ELSE
            -- Both confirmed: normal update
            UPDATE Accounts
               SET Accounts_Balance = Accounts_Balance - OLD.Total_Amount
             WHERE Accounts_Id = OLD.Accounts_Id;
            UPDATE Accounts
               SET Accounts_Balance = Accounts_Balance + NEW.Total_Amount
             WHERE Accounts_Id = NEW.Accounts_Id;
        END IF;
    END IF;
    RETURN NULL;
END;
$$;
