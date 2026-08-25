-- Installment series via Recurring Templates. See database/queries.py::_confirm_draft_row
-- and api/routers/recurring.py. A template with Total_Occurrences set generates its first
-- occurrence exactly like any normal template (no special-casing in the generation paths);
-- confirming that first draft generates the remaining N-1 as new drafts and deactivates the
-- template. Mutually exclusive with Auto_Confirm -- an auto-confirmed occurrence never passes
-- through the confirm hook that triggers the rest of the series.

ALTER TABLE Recurring_Templates ADD COLUMN IF NOT EXISTS Total_Occurrences INTEGER;

DO $$ BEGIN
    ALTER TABLE Recurring_Templates ADD CONSTRAINT recurring_templates_total_occurrences_check
        CHECK (Total_Occurrences IS NULL OR Total_Occurrences >= 2);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
