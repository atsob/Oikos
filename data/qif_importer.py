"""
QIF Importer Module for Finance OS
Handles importing QIF files into the PostgreSQL database
"""

import quiffen
import csv
import streamlit as st
import pandas as pd
import tempfile
import os
from datetime import datetime
from database.connection import get_connection
from database.crud import (
    update_accounts_balances, update_db_stats, update_investment_balances,
    update_pension_balances, update_holdings,
    save_nwr_account_selection,
)
from database.queries import get_nwr_account_selection

# Suggested Oikos Accounts_Type per QIF AccountType — QIF can't distinguish
# Checking from Savings (both are just "Bank"), so that split is always a
# manual override in the Map/Define wizard step, never something the parser
# can infer.
_QIF_TO_OIKOS_TYPE = {
    'Cash': 'Cash', 'Bank': 'Checking', 'CCard': 'Credit Card',
    'Oth A': 'Asset', 'Oth L': 'Liability', 'Invst': 'Brokerage',
    'Invoice': 'Other', 'Unknown': 'Other',
}
# UI-bucket grouping for the wizard's account list (display only).
_QIF_TYPE_TO_GROUP = {
    'Cash': 'cash', 'Bank': 'cash', 'CCard': 'credit',
    'Oth A': 'asset_liability', 'Oth L': 'asset_liability', 'Invst': 'investment',
    'Invoice': 'other', 'Unknown': 'other',
}

class QIFImporter:
    """Handles QIF file import operations"""
    
    def __init__(self):
        self.conn = None
        self.cur = None
        
    def connect(self):
        """Establish database connection"""
        self.conn = get_connection()
        self.cur = self.conn.cursor()
        
    def disconnect(self):
        """Close database connection"""
        if self.cur:
            self.cur.close()
        if self.conn:
            self.conn.close()
    
    def clean_id(self, val):
        """Convert (7,) or [7] or 7 to clean 7. Returns None if val is None."""
        if val is None:
            return None
        if isinstance(val, (tuple, list)):
            return val[0] if val else None
        return val
    
    def get_id(self, table, id_col, name_col, name_val):
        """Retrieve ID from table by name"""
        self.cur.execute(f"SELECT {id_col} FROM {table} WHERE {name_col} = %s", (name_val,))
        result = self.cur.fetchone()
        if result:
            return result[0]
        return None
    
    def get_or_create_id(self, table, id_col, name_col, name_val, extra_cols=None):
        """Get existing ID or create new record"""
        # First try to find existing
        self.cur.execute(f"SELECT {id_col} FROM {table} WHERE {name_col} = %s", (name_val,))
        result = self.cur.fetchone()
        if result:
            return result[0]
        
        # Create new if doesn't exist
        if extra_cols:
            cols = [name_col] + list(extra_cols.keys())
            placeholders = ", ".join(["%s"] * len(cols))
            vals = [name_val] + list(extra_cols.values())
            self.cur.execute(
                f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) RETURNING {id_col}", 
                vals
            )
        else:
            self.cur.execute(
                f"INSERT INTO {table} ({name_col}) VALUES (%s) RETURNING {id_col}", 
                (name_val,)
            )
        
        new_id = self.cur.fetchone()[0]
        self.conn.commit()
        return new_id
    
    def get_or_create_category_recursive(self, full_name, cat_type='Expense'):
        """Create category hierarchy recursively"""
        if not full_name:
            return None
        
        parts = full_name.split(':')
        parent_id = None
        
        for part in parts:
            part = part.strip()
            if not part:
                continue
            
            # Search for existing category
            if parent_id is None:
                self.cur.execute(
                    "SELECT Categories_Id FROM Categories WHERE Categories_Name = %s AND Categories_Id_Parent IS NULL",
                    (part,)
                )
            else:
                self.cur.execute(
                    "SELECT Categories_Id FROM Categories WHERE Categories_Name = %s AND Categories_Id_Parent = %s",
                    (part, parent_id)
                )
            
            row = self.cur.fetchone()
            if row:
                current_id = row[0]
            else:
                # Insert new category
                self.cur.execute("""
                    INSERT INTO Categories (Categories_Name, Categories_Id_Parent, Categories_Type)
                    VALUES (%s, %s, %s) RETURNING Categories_Id
                """, (part, parent_id, cat_type))
                current_id = self.cur.fetchone()[0]
            
            parent_id = current_id
        
        return parent_id
    
    def ensure_transfer_issues_table(self):
        """Create Transfer_Issues table if it doesn't exist"""
        self.cur.execute("""
            CREATE TABLE IF NOT EXISTS Transfer_Issues (
                Issue_Id           SERIAL PRIMARY KEY,
                Issue_Type         VARCHAR(50) NOT NULL,
                Status             VARCHAR(20) NOT NULL DEFAULT 'Open',
                Transactions_Id_A  INTEGER REFERENCES Transactions(Transactions_Id) ON DELETE CASCADE,
                Transactions_Id_B  INTEGER REFERENCES Transactions(Transactions_Id) ON DELETE CASCADE,
                Date_A             DATE,
                Date_B             DATE,
                Amount_A           NUMERIC(28,18),
                Amount_B           NUMERIC(28,18),
                Accounts_Id_A      INTEGER REFERENCES Accounts(Accounts_Id),
                Accounts_Id_B      INTEGER REFERENCES Accounts(Accounts_Id),
                Description_A      TEXT,
                Description_B      TEXT,
                Notes              TEXT,
                Created_At         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                Resolved_At        TIMESTAMP
            )
        """)
        self.cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_transfer_issues_status
            ON Transfer_Issues(Status)
        """)
        self.conn.commit()

    def scan_date_mismatched_transfers(self):
        """
        Post-import scan for transfers where the two linked sides have different dates.
        These arise when a Quicken user edits the date on one side of a transfer
        without updating the other. Each genuine mismatch is recorded in Transfer_Issues.

        Only flags pairs where the date difference is 1–3 days and no existing issue
        already covers the same pair.
        """
        self.cur.execute("""
            SELECT
                t1.Transactions_Id, t1.Date, t1.Total_Amount,
                t1.Accounts_Id, t1.Description,
                t2.Transactions_Id, t2.Date, t2.Total_Amount,
                t2.Accounts_Id, t2.Description,
                t1.Transfers_Id
            FROM Transactions t1
            JOIN Transactions t2
              ON  t2.Transfers_Id      = t1.Transfers_Id
              AND t2.Accounts_Id       = t1.Accounts_Id_Target
              AND t2.Accounts_Id_Target = t1.Accounts_Id
              AND t2.Transactions_Id   > t1.Transactions_Id  -- avoid double-counting
            WHERE t1.Transfers_Id IS NOT NULL
              AND t1.Date != t2.Date
              AND ABS(t1.Date - t2.Date) BETWEEN 1 AND 3
              -- skip pairs already flagged
              AND NOT EXISTS (
                  SELECT 1 FROM Transfer_Issues i
                  WHERE (i.Transactions_Id_A = t1.Transactions_Id
                         OR i.Transactions_Id_B = t1.Transactions_Id)
              )
        """)
        rows = self.cur.fetchall()
        count = 0
        for row in rows:
            (tx_id_a, date_a, amt_a, acc_a, desc_a,
             tx_id_b, date_b, amt_b, acc_b, desc_b, _tid) = row
            self.flag_transfer_issue(
                issue_type = 'DATE_MISMATCH',
                tx_id_a    = tx_id_a,
                tx_id_b    = tx_id_b,
                date_a     = date_a,
                date_b     = date_b,
                amount_a   = amt_a,
                amount_b   = amt_b,
                acc_id_a   = acc_a,
                acc_id_b   = acc_b,
                desc_a     = desc_a,
                desc_b     = desc_b,
                notes      = f"Date difference: {abs((date_a - date_b).days)} day(s)"
            )
            count += 1
        self.conn.commit()
        return count

    def flag_transfer_issue(self, issue_type, tx_id_a, tx_id_b,
                            date_a, date_b, amount_a, amount_b,
                            acc_id_a, acc_id_b, desc_a, desc_b, notes=None):
        """Record a transfer issue for manual review"""
        self.cur.execute("""
            INSERT INTO Transfer_Issues (
                Issue_Type, Transactions_Id_A, Transactions_Id_B,
                Date_A, Date_B, Amount_A, Amount_B,
                Accounts_Id_A, Accounts_Id_B, Description_A, Description_B, Notes
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (issue_type, tx_id_a, tx_id_b,
              date_a, date_b, amount_a, amount_b,
              acc_id_a, acc_id_b, desc_a, desc_b, notes))

    def disable_triggers(self):
        """Disable triggers for faster import"""
        st.info("⏸️ Disabling triggers for faster import...")
        try:
            self.cur.execute("ALTER TABLE Transactions DISABLE TRIGGER trg_update_balance;")
            self.cur.execute("ALTER TABLE Investments DISABLE TRIGGER trg_update_holdings;")
            self.conn.commit()
        except Exception as e:
            st.warning(f"Could not disable triggers (they may not exist): {e}")
    
    def enable_triggers(self):
        """Re-enable triggers after import"""
        st.info("▶️ Re-enabling triggers...")
        try:
            self.cur.execute("ALTER TABLE Transactions ENABLE TRIGGER trg_update_balance;")
            self.cur.execute("ALTER TABLE Investments ENABLE TRIGGER trg_update_holdings;")
            self.conn.commit()
        except Exception as e:
            st.warning(f"Could not enable triggers (they may not exist): {e}")
    
    def clear_tables(self, tables_to_clear,
                     exclude_bank_account_ids=None,
                     exclude_inv_account_ids=None):
        """Clear specified tables.

        When account-exclusion lists are provided the relevant tables are
        cleared with DELETE (preserving rows belonging to excluded accounts)
        rather than TRUNCATE.  Tables that have no applicable exclusion list
        (Categories, Holdings, …) are always TRUNCATEd.
        """
        _bank_excl = [int(i) for i in (exclude_bank_account_ids or [])]
        _inv_excl  = [int(i) for i in (exclude_inv_account_ids  or [])]

        for table in tables_to_clear:
            st.write(f"  - Clearing {table}...")

            if table == 'Transactions' and _bank_excl:
                # NULL out Investments.Transactions_Id references that point to
                # transactions we are about to delete, to avoid FK violations.
                self.cur.execute("""
                    UPDATE Investments
                    SET Transactions_Id = NULL
                    WHERE Transactions_Id IN (
                        SELECT Transactions_Id FROM Transactions
                        WHERE Accounts_Id NOT IN %s
                    )
                """, (tuple(_bank_excl),))
                # Delete all bank transactions except those in excluded accounts
                self.cur.execute(
                    "DELETE FROM Transactions WHERE Accounts_Id NOT IN %s",
                    (tuple(_bank_excl),)
                )
            elif table == 'Splits' and _bank_excl:
                # Delete splits whose parent transaction belongs to a non-excluded account.
                # (When Transactions is also being cleared, its CASCADE already handles
                # most of these; this covers the case where only Splits is selected.)
                self.cur.execute("""
                    DELETE FROM Splits
                    WHERE Transactions_Id IN (
                        SELECT Transactions_Id FROM Transactions
                        WHERE Accounts_Id NOT IN %s
                    )
                """, (tuple(_bank_excl),))
            elif table == 'Investments' and _inv_excl:
                # Delete investment transactions except those in excluded accounts
                self.cur.execute(
                    "DELETE FROM Investments WHERE Accounts_Id NOT IN %s",
                    (tuple(_inv_excl),)
                )
            else:
                self.cur.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE;")

        self.conn.commit()
        st.write("All specified tables cleared.")
        st.write("Resetting sequence transfers_id_seq...")
        self.cur.execute("SELECT setval('Transfers_Id_Seq', GREATEST(1, COALESCE((SELECT MAX(Transfers_Id) FROM Transactions), 1)));")
        self.conn.commit()
        st.success(f"✅ Cleared {len(tables_to_clear)} tables successfully!")
    
    def import_categories_from_qif(self, qif_file_path):
        """Import categories directly from QIF file"""
        st.info("📂 Importing Categories...")
        
        self.cur.execute("SELECT count(*) FROM Categories")
        result = self.cur.fetchone()
        cat_entries_before = result[0] if result else 0  # Πρόσβαση στο πρώτο στοιχείο
        print(f"Entries: {cat_entries_before}")

        with open(qif_file_path, 'r', encoding='latin-1') as f:
            current_cat = None
            current_type = 'Expense'
            in_category_section = False
            cat_count = 0
            
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                if line.startswith('!Type:Cat'):
                    in_category_section = True
                    continue
                elif line.startswith('!Type:'):
                    in_category_section = False
                    continue
                
                if in_category_section:
                    if line.startswith('N'):
                        current_cat = line[1:]
                    elif line.startswith('I'):
                        current_type = 'Income'
                    elif line == '^':
                        if current_cat:
                            self.get_or_create_category_recursive(current_cat, cat_type=current_type)
                            cat_count += 1
                        current_cat = None
                        current_type = 'Expense'

        self.cur.execute("SELECT count(*) FROM Categories")
        result = self.cur.fetchone()
        cat_entries_after = result[0] if result else 0  # Πρόσβαση στο πρώτο στοιχείο
        print(f"Entries: {cat_entries_after}")


        cat_imported = cat_entries_after - cat_entries_before
        st.success(f"✅ Imported {cat_imported} categories!")
    
    def import_securities(self, qif):
        """Import securities from QIF object"""
        st.info("📊 Importing Securities...")
        sec_count = 0
        
        for security in qif.securities.values():
            name = security.name
            ticker = security.symbol if security.symbol else name[:50]
            _VALID_SEC_TYPES = {
                'Stock', 'ETF', 'Bond', 'Mutual Fund', 'Crypto', 'Option',
                'Commodity', 'PF_Unit', 'CD', 'Emp. Stock Opt.', 'FX Spot',
                'Market Index', 'Other',
            }
            _QIF_TYPE_MAP = {
                'mf': 'Mutual Fund', 'mutual fund': 'Mutual Fund',
                'etf': 'ETF', 'bond': 'Bond', 'stock': 'Stock',
            }
            raw_type = str(security.type).strip() if hasattr(security, 'type') and security.type else ''
            sectype = (
                raw_type if raw_type in _VALID_SEC_TYPES
                else _QIF_TYPE_MAP.get(raw_type.lower(), 'Stock')
            )
            
            # Check if security already exists — by ticker first, then by name
            existing_id = self.get_id("Securities", "securities_id", "ticker", ticker)
            if not existing_id:
                existing_id = self.get_id("Securities", "securities_id", "securities_name", name)
            if not existing_id:
                # Set EUR as the default Security Currency
                self.cur.execute("SELECT Currencies_Id FROM Currencies WHERE Currencies_ShortName = %s", ("EUR",))
                account_currency_id = self.cur.fetchone()[0]

                self.get_or_create_id(
                    "Securities", "securities_id", "ticker", ticker,
                    {"securities_name": name, "securities_type": sectype, "currencies_id": account_currency_id}
                )
                sec_count += 1
        
        st.success(f"✅ Imported {sec_count} new securities!")

    def _resolve_account(self, name, account_map, default_currency_id):
        """Resolve a QIF account name to an Oikos Accounts_Id — the single choke
        point replacing what used to be 4 separate inline get_or_create_id calls.
        Memoized per-instance (self._account_resolution_cache) so repeated
        references to the same name (transfer/split/X-type targets) resolve once.

        account_map is the wizard's Map/Define-step output: {qif_name: {'action':
        'skip'|'map'|'create', 'oikos_account_id': int|None, 'new_name': str|None,
        'new_type': str|None}}, or None for the legacy no-wizard call path.

        A 'skip' entry only prevents that account's own transactions from being
        imported (handled by the caller, before this method is ever invoked for
        that account's own top-level loop turn) — it does NOT stop this method
        from resolving the same name when it's referenced as someone else's
        transfer/split/X-type-cash-leg target. In that case, like any name with
        no map/create entry, it safety-net auto-creates as Checking, exactly
        matching pre-wizard behavior, so a transfer never silently loses a leg.
        """
        if not hasattr(self, '_account_resolution_cache'):
            self._account_resolution_cache = {}
        if name in self._account_resolution_cache:
            return self._account_resolution_cache[name]

        entry = (account_map or {}).get(name)
        if entry and entry.get('action') == 'map':
            acc_id = self.clean_id(entry['oikos_account_id'])
        elif entry and entry.get('action') == 'create':
            acc_id = self.clean_id(self.get_or_create_id(
                "Accounts", "Accounts_Id", "Accounts_Name", entry['new_name'],
                {"Accounts_Type": entry['new_type'], "Currencies_Id": default_currency_id}
            ))
        else:
            acc_id = self.clean_id(self.get_or_create_id(
                "Accounts", "Accounts_Id", "Accounts_Name", name,
                {"Accounts_Type": "Checking", "Currencies_Id": default_currency_id}
            ))

        self._account_resolution_cache[name] = acc_id
        return acc_id

    def import_accounts_and_transactions(self, qif, account_map=None, date_from=None, date_to=None):
        """Import accounts and transactions from QIF object.

        account_map: optional wizard Map/Define output (see _resolve_account) —
        None means the legacy behavior (every account auto-created as Checking).
        date_from/date_to: optional datetime.date bounds (inclusive) — transactions
        outside the range are skipped entirely, on both the bank and investment
        branches.

        Returns a summary dict (accounts_created/mapped/skipped/fallback,
        bank_tx_count, inv_tx_count, has_bank_tx, has_inv_tx) rather than the old
        (bool, bool) tuple — this method has exactly one caller (import_full_qif).
        """
        st.info("💰 Importing Accounts and Transactions...")

        acc_count = 0
        bank_tx_count = 0
        inv_tx_count = 0
        accounts_created = 0
        accounts_mapped = 0
        accounts_skipped = 0
        accounts_fallback = 0

        # Pre-pass: resolve (get-or-create) every non-skipped account BEFORE any
        # transaction is processed. Without this, an account first touched as a
        # transfer TARGET from another account (which can happen before its own
        # turn in the loop below) would get silently auto-created as Checking via
        # the old inline get_or_create_id calls, and its real QIF type / the
        # user's chosen map-or-create type would never take effect — get_or_create
        # is get-OR-create, so whatever type wins on first creation sticks forever.
        self._account_resolution_cache = {}
        for acc_name, acc_obj in qif.accounts.items():
            entry = (account_map or {}).get(acc_name) if account_map is not None else None
            action = entry.get('action') if entry else None

            if action == 'skip':
                accounts_skipped += 1
                continue
            elif action == 'map':
                accounts_mapped += 1
            elif action == 'create':
                accounts_created += 1
            else:
                accounts_fallback += 1

            qif_currency = getattr(acc_obj, 'currency', 'EUR')
            curr_id = self.get_or_create_id(
                'Currencies', 'Currencies_Id', 'Currencies_ShortName', qif_currency,
                {'Currencies_Name': qif_currency}
            )
            self._resolve_account(acc_name, account_map, curr_id)
        self.conn.commit()

        for acc_name, acc_obj in qif.accounts.items():
            entry = (account_map or {}).get(acc_name) if account_map is not None else None
            if entry and entry.get('action') == 'skip':
                continue

            # Get currency
            qif_currency = getattr(acc_obj, 'currency', 'EUR')

            # Create/Get Currency
            curr_id = self.get_or_create_id(
                'Currencies', 'Currencies_Id', 'Currencies_ShortName', qif_currency,
                {'Currencies_Name': qif_currency}
            )

            # Resolve Account (already created in the pre-pass above — cache hit)
            acc_id = self._resolve_account(acc_name, account_map, curr_id)
            acc_count += 1

            c_acc_id = self.clean_id(acc_id)

            # 1. Πρώτα ανακτούμε το Currencies_Id του τρέχοντος λογαριασμού
            # Υποθέτουμε ότι το c_acc_id είναι το ID του λογαριασμού που επεξεργάζεστε
            self.cur.execute("SELECT Currencies_Id FROM Accounts WHERE Accounts_Id = %s", (c_acc_id,))
            account_currency_id = self.cur.fetchone()[0]

            # Process transactions
            for tx_list in acc_obj.transactions.values():
                # Per-account occurrence counter: tracks how many times each
                # (date, amount, to_account) combination has been processed so far.
                # Used by the duplicate guard to distinguish genuine duplicate
                # transfers from cross-account mirror detection.
                _tx_occurrence_count = {}

                # Separate counter for X-type investment cash legs:
                # tracks (date, cash_acc_id, inv_acc_id) occurrences within this account.
                _x_type_occurrence_count = {}

                for tx in tx_list:
                    # Skip sentinel transactions inserted by the malformed-QIF patch
                    if getattr(tx, 'memo', None) == '__QIF_SKIP__':
                        continue

                    # Bank Transaction
                    if hasattr(tx, 'payee'):
                        if date_from and tx.date.date() < date_from:
                            continue
                        if date_to and tx.date.date() > date_to:
                            continue

                        # Παράλειψη αν το συνολικό ποσό της συναλλαγής είναι 0 (προαιρετικό, ανάλογα με τη λογική σας)
                        if tx.amount == 0:
                            continue

                        # 1. Transfer detection
                        #
                        # Quiffen exposes the L field in two ways depending on content:
                        #   tx.to_account  — when L is [AccountName]  (already bracket-stripped)
                        #   tx.category    — when L is a plain category, possibly "Cat|[Account]"
                        #
                        # Priority: to_account first, then fall back to category parsing.
                        is_transfer = False
                        target_acc_id = None
                        target_acc_name = None

                        raw_to_account = getattr(tx, 'to_account', None)
                        if raw_to_account:
                            # Direct transfer: quiffen already stripped the brackets
                            target_acc_name = raw_to_account.strip().strip('[]')
                        else:
                            # Fallback: parse category for embedded [AccountName]
                            raw_cat = str(tx.category) if tx.category else ""
                            # Handle "Category|[AccountName]" pipe format
                            if '|' in raw_cat:
                                raw_cat = raw_cat.split('|')[-1].strip()
                            if raw_cat.startswith('[') and raw_cat.endswith(']'):
                                target_acc_name = raw_cat[1:-1]

                        if target_acc_name:
                            resolved_target_id = self._resolve_account(target_acc_name, account_map, account_currency_id)
                            # Skip self-transfers (L field points to the current account)
                            if resolved_target_id != c_acc_id:
                                is_transfer = True
                                target_acc_id = resolved_target_id
                            else:
                                pass
                        # 2. Duplicate-transfer guard
                        #
                        # For each transfer, one of three situations applies:
                        #
                        # A) NEW transfer (first account to process it):
                        #    No rows exist in DB → INSERT source + mirror
                        #
                        # B) MIRROR SIDE (second account to process it):
                        #    The other account already inserted a source+mirror pair.
                        #    This tx is the "other side" — identified by finding a row
                        #    where (target_acc → c_acc) exists with a Transfers_Id.
                        #    Action: UPDATE both rows with correct cross-currency amounts,
                        #    then skip. For same-currency this is a no-op on amounts.
                        #
                        # C) FULL DUPLICATE (re-import, or own source row exists):
                        #    (c_acc → target_acc) source row already exists → skip entirely.
                        #
                        # Multiple genuine transfers same date+accounts are handled by the
                        # per-account occurrence counter: each time this account processes
                        # a (date, target_acc) pair, the counter increments. We only consider
                        # situation B/C if the DB has at least that many rows already.
                        existing_transfer_id = None
                        _is_mirror_side = False
                        if is_transfer:
                            # Per-account occurrence counter for (date, target_acc).
                            # Counts how many times THIS account has processed this pair.
                            _tx_key = (tx.date, target_acc_id)
                            _tx_occurrence_count[_tx_key] = _tx_occurrence_count.get(_tx_key, 0) + 1
                            _nth = _tx_occurrence_count[_tx_key]

                            # Count own source rows: (c_acc → target_acc)
                            self.cur.execute("""
                                SELECT COUNT(*) FROM Transactions
                                WHERE Date = %s AND Accounts_Id = %s
                                  AND Accounts_Id_Target = %s AND Transfers_Id IS NOT NULL
                            """, (tx.date, c_acc_id, target_acc_id))
                            _own_count = (self.cur.fetchone() or (0,))[0]

                            # Count mirror rows: (target_acc → c_acc)
                            self.cur.execute("""
                                SELECT COUNT(*) FROM Transactions
                                WHERE Date = %s AND Accounts_Id = %s
                                  AND Accounts_Id_Target = %s AND Transfers_Id IS NOT NULL
                            """, (tx.date, target_acc_id, c_acc_id))
                            _mir_count = (self.cur.fetchone() or (0,))[0]

                            # Select the Nth transfers_id (1-based) in insertion order.
                            # Using OFFSET(_nth-1) ensures each occurrence of a repeated
                            # transfer pair maps to its own distinct row, not always MAX.
                            def _nth_transfers_id(acc_from, acc_to, n):
                                self.cur.execute("""
                                    SELECT Transfers_Id FROM Transactions
                                    WHERE Date = %s AND Accounts_Id = %s
                                      AND Accounts_Id_Target = %s AND Transfers_Id IS NOT NULL
                                    ORDER BY Transfers_Id ASC
                                    LIMIT 1 OFFSET %s
                                """, (tx.date, acc_from, acc_to, n - 1))
                                _r = self.cur.fetchone()
                                return _r[0] if _r else None

                            if _mir_count >= _nth:
                                # Situation B: mirror row exists for this occurrence.
                                # Check mirror direction FIRST — critical for cross-currency
                                # where own_count also >= _nth due to the inserted mirror row.
                                _is_mirror_side = True
                                # Try to match by exact amount first (same-currency):
                                # the mirror's Total_Amount should equal -tx.amount.
                                self.cur.execute("""
                                    SELECT Transfers_Id FROM Transactions
                                    WHERE Date = %s AND Accounts_Id = %s
                                      AND Accounts_Id_Target = %s AND Transfers_Id IS NOT NULL
                                      AND Total_Amount = %s
                                    ORDER BY Transfers_Id ASC
                                    LIMIT 1
                                """, (tx.date, target_acc_id, c_acc_id, -tx.amount))
                                _r = self.cur.fetchone()
                                if _r:
                                    existing_transfer_id = _r[0]
                                else:
                                    # Cross-currency: amounts differ, fall back to Nth by order
                                    existing_transfer_id = _nth_transfers_id(target_acc_id, c_acc_id, _nth)
                            elif _own_count >= _nth:
                                # Situation C: own source row exists → full duplicate, skip
                                # Match by exact amount for same-currency, Nth for cross-currency
                                self.cur.execute("""
                                    SELECT Transfers_Id FROM Transactions
                                    WHERE Date = %s AND Accounts_Id = %s
                                      AND Accounts_Id_Target = %s AND Transfers_Id IS NOT NULL
                                      AND Total_Amount = %s
                                    ORDER BY Transfers_Id ASC
                                    LIMIT 1
                                """, (tx.date, c_acc_id, target_acc_id, tx.amount))
                                _r = self.cur.fetchone()
                                if _r:
                                    existing_transfer_id = _r[0]
                                else:
                                    existing_transfer_id = _nth_transfers_id(c_acc_id, target_acc_id, _nth)

                        # 2b. Date-mismatch detection is done as a post-import scan
                        #     after all accounts are processed (see scan_date_mismatched_transfers).
                        _pending_date_mismatch = None

                        # 3. Payee & Cleared status
                        p_id = self.get_or_create_id("Payees", "payees_id", "payees_name", tx.payee) if tx.payee else None
                        c_payee_id = self.clean_id(p_id)
                        is_cleared = True if tx.cleared in ['X', '*', 'R'] else False

                        # 4. Καθορισμός Transfers_Id
                        current_transfer_id = existing_transfer_id
                        if is_transfer and not current_transfer_id:
                            self.cur.execute("SELECT nextval('transfers_id_seq')")
                            current_transfer_id = self.cur.fetchone()[0]

                        # 4b. Decompose split transactions that mix transfers and categories.
                        #     The DB handles transfers at transaction level only, so we break
                        #     a split like [-304.66 → Loan, -35.95 Loan:Car Interest] into:
                        #       • one proper transfer transaction per transfer-split
                        #       • one regular transaction per category-split
                        #     The original parent amount is never inserted.
                        if (not is_transfer
                                and hasattr(tx, 'splits') and tx.splits
                                and any(getattr(s, 'to_account', None)
                                        for s in tx.splits if s.amount != 0)):
                            _tr_splits  = [s for s in tx.splits
                                           if getattr(s, 'to_account', None) and s.amount != 0]
                            _cat_splits = [s for s in tx.splits
                                           if not getattr(s, 'to_account', None) and s.amount != 0]

                            for split in _tr_splits:
                                s_target_name = split.to_account.strip().strip('[]')
                                s_target_id = self._resolve_account(s_target_name, account_map, account_currency_id)
                                if s_target_id == c_acc_id:
                                    continue

                                # Duplicate guard for this split-derived transfer
                                _s_key = (tx.date, s_target_id)
                                _tx_occurrence_count[_s_key] = _tx_occurrence_count.get(_s_key, 0) + 1
                                _s_nth = _tx_occurrence_count[_s_key]

                                self.cur.execute("""
                                    SELECT COUNT(*) FROM Transactions
                                    WHERE Date = %s AND Accounts_Id = %s
                                      AND Accounts_Id_Target = %s AND Transfers_Id IS NOT NULL
                                """, (tx.date, s_target_id, c_acc_id))
                                _s_mir = (self.cur.fetchone() or (0,))[0]

                                self.cur.execute("""
                                    SELECT COUNT(*) FROM Transactions
                                    WHERE Date = %s AND Accounts_Id = %s
                                      AND Accounts_Id_Target = %s AND Transfers_Id IS NOT NULL
                                """, (tx.date, c_acc_id, s_target_id))
                                _s_own = (self.cur.fetchone() or (0,))[0]

                                if _s_own >= _s_nth:
                                    continue  # full duplicate
                                if _s_mir >= _s_nth:
                                    continue  # mirror already exists; target account will update

                                self.cur.execute("SELECT nextval('transfers_id_seq')")
                                s_xfer_id = self.cur.fetchone()[0]

                                self.cur.execute("""
                                    INSERT INTO Transactions (
                                        Accounts_Id, Date, Payees_Id, Description,
                                        Total_Amount, Cleared, Accounts_Id_Target,
                                        Total_Amount_Target, Transfers_Id
                                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                """, (
                                    c_acc_id, tx.date, c_payee_id,
                                    split.memo or tx.memo or tx.payee,
                                    split.amount, is_cleared,
                                    s_target_id, -split.amount, s_xfer_id
                                ))
                                bank_tx_count += 1

                                # Mirror in target (skip if brokerage)
                                self.cur.execute(
                                    "SELECT Accounts_Type FROM Accounts WHERE Accounts_Id = %s",
                                    (s_target_id,)
                                )
                                _s_tgt = self.cur.fetchone()
                                if not (_s_tgt and _s_tgt[0] in ('Brokerage',)):
                                    self.cur.execute("""
                                        INSERT INTO Transactions (
                                            Accounts_Id, Date, Payees_Id, Description,
                                            Total_Amount, Cleared, Accounts_Id_Target,
                                            Total_Amount_Target, Transfers_Id
                                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                    """, (
                                        s_target_id, tx.date, c_payee_id,
                                        split.memo or tx.memo or tx.payee,
                                        -split.amount, is_cleared,
                                        c_acc_id, split.amount, s_xfer_id
                                    ))
                                    bank_tx_count += 1

                            for split in _cat_splits:
                                cat_id = None
                                if split.category:
                                    if hasattr(split.category, 'hierarchy') and split.category.hierarchy:
                                        _cname = split.category.hierarchy
                                    elif hasattr(split.category, 'name'):
                                        _cname = split.category.name
                                    else:
                                        _cname = str(split.category)
                                    cat_id = self.get_or_create_category_recursive(_cname, cat_type='Expense')

                                self.cur.execute("""
                                    INSERT INTO Transactions (
                                        Accounts_Id, Date, Payees_Id, Description,
                                        Total_Amount, Cleared
                                    ) VALUES (%s,%s,%s,%s,%s,%s)
                                    RETURNING Transactions_Id
                                """, (
                                    c_acc_id, tx.date, c_payee_id,
                                    split.memo or tx.memo or tx.payee,
                                    split.amount, is_cleared
                                ))
                                _s_bt_id = self.cur.fetchone()[0]
                                bank_tx_count += 1

                                self.cur.execute("""
                                    INSERT INTO Splits (Transactions_Id, Categories_Id, Amount, Memo)
                                    VALUES (%s,%s,%s,%s)
                                """, (_s_bt_id, self.clean_id(cat_id), split.amount, split.memo))

                            continue  # skip the normal parent INSERT (steps 5-7)

                        # 5. Insert or update
                        # If this is the mirror side of a cross-account transfer: update the
                        # existing source row's Total_Amount_Target with the correct amount
                        # (critical for cross-currency transfers), then skip INSERT.
                        if is_transfer and _is_mirror_side:
                            # This is the second/mirror side of the transfer.
                            # Update both rows with the correct cross-currency amounts:
                            #
                            # Source row (target_acc → c_acc, e.g. ABN -66.48 → RON):
                            #   Total_Amount_Target should be tx.amount (e.g. +300.00 RON)
                            #
                            # Mirror row (c_acc → target_acc, e.g. RON +66.48 placeholder):
                            #   Total_Amount should be tx.amount (e.g. +300.00 RON)
                            #   Total_Amount_Target should be the source amount (e.g. -66.48 EUR)
                            #   (We get the source amount from the source row itself)

                            # Get the source row's actual amount for updating the mirror
                            self.cur.execute("""
                                SELECT Total_Amount FROM Transactions
                                WHERE Transfers_Id = %s
                                  AND Accounts_Id = %s
                                  AND Accounts_Id_Target = %s
                            """, (existing_transfer_id, target_acc_id, c_acc_id))
                            _src_amount_row = self.cur.fetchone()
                            _src_amount = _src_amount_row[0] if _src_amount_row else None

                            # Update source row: set correct Total_Amount_Target
                            self.cur.execute("""
                                UPDATE Transactions
                                SET Total_Amount_Target = %s
                                WHERE Transfers_Id = %s
                                  AND Accounts_Id = %s
                                  AND Accounts_Id_Target = %s
                            """, (tx.amount, existing_transfer_id, target_acc_id, c_acc_id))

                            # Update mirror row: set correct Total_Amount and Total_Amount_Target
                            self.cur.execute("""
                                UPDATE Transactions
                                SET Total_Amount = %s,
                                    Total_Amount_Target = %s
                                WHERE Transfers_Id = %s
                                  AND Accounts_Id = %s
                                  AND Accounts_Id_Target = %s
                            """, (tx.amount, _src_amount, existing_transfer_id, c_acc_id, target_acc_id))

                            continue

                        if is_transfer and existing_transfer_id:
                            continue

                        self.cur.execute("""
                            INSERT INTO Transactions (
                                Accounts_Id, Date, Payees_Id, Description, 
                                Total_Amount, Cleared, Accounts_Id_Target, 
                                Total_Amount_Target, Transfers_Id
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            RETURNING Transactions_Id
                        """, (
                            c_acc_id, tx.date, c_payee_id, tx.memo or tx.payee, 
                            tx.amount, is_cleared, target_acc_id, 
                            -tx.amount if is_transfer else None,
                            current_transfer_id
                        ))

                        bt_id = self.cur.fetchone()[0]
                        bank_tx_count += 1

                        # 6. For transfers: insert the mirror leg in the target account.
                        #    Only for NEW transfers (no existing_transfer_id) to avoid duplicates.
                        #    For brokerage/investment targets: the Investments table CashIn/CashOut
                        #    row already represents the brokerage side, so we skip the Transactions
                        #    mirror. The source row (bank/card side) keeps its target fields so the
                        #    register still shows where the money went.
                        # Only 'Brokerage' accounts use the Investments table as their
                        # cash ledger. Pension/Other Investment/Margin accounts still need
                        # a Transactions mirror row — they don't have brokerage-style CashIn rows.
                        BROKERAGE_ONLY = ('Brokerage',)
                        if is_transfer and not existing_transfer_id and target_acc_id:
                            c_target_acc_id = self.clean_id(target_acc_id)
                            self.cur.execute(
                                "SELECT Accounts_Type FROM Accounts WHERE Accounts_Id = %s",
                                (c_target_acc_id,)
                            )
                            _tgt_type_row = self.cur.fetchone()
                            _target_is_brokerage = (
                                _tgt_type_row and _tgt_type_row[0] in BROKERAGE_ONLY
                            )
                            if not _target_is_brokerage:
                                # Non-brokerage: insert the mirror row as normal
                                self.cur.execute("""
                                    INSERT INTO Transactions (
                                        Accounts_Id, Date, Payees_Id, Description,
                                        Total_Amount, Cleared, Accounts_Id_Target,
                                        Total_Amount_Target, Transfers_Id
                                    )
                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                                """, (
                                    c_target_acc_id, tx.date, c_payee_id,
                                    tx.memo or tx.payee,
                                    -tx.amount,
                                    is_cleared,
                                    c_acc_id,
                                    tx.amount,
                                    current_transfer_id
                                ))
                                bank_tx_count += 1

                            # Brokerage target: no mirror INSERT — source row already has
                            # Accounts_Id_Target and Transfers_Id set (from step 5),
                            # which is sufficient for the bank register to show the destination.

                            bank_tx_count += 1

                        # 7. Εισαγωγή Splits με έλεγχο μηδενικού ποσού
                        if hasattr(tx, 'splits') and tx.splits:
                            for split in tx.splits:
                                # ΕΔΩ: Έλεγχος για split amount != 0
                                if split.amount != 0:
                                    cat_id = None
                                    if split.category:
                                        if hasattr(split.category, 'hierarchy') and split.category.hierarchy:
                                            cat_name = split.category.hierarchy
                                        elif hasattr(split.category, 'name'):
                                            cat_name = split.category.name
                                        else:
                                            cat_name = str(split.category)
                                        
                                        cat_id = self.get_or_create_category_recursive(cat_name, cat_type='Expense')
                                    
                                    self.cur.execute("""
                                        INSERT INTO Splits (Transactions_Id, Categories_Id, Amount, Memo)
                                        VALUES (%s, %s, %s, %s)
                                    """, (bt_id, self.clean_id(cat_id), split.amount, split.memo))

                        else:
                            # Single split logic (αν δεν υπάρχουν splits)
                            # ΕΔΩ: Έλεγχος για transaction amount != 0
                            if tx.amount != 0:
                                cat_id = None
                                if hasattr(tx, 'category') and tx.category and not is_transfer: # Αν είναι μεταφορά, συνήθως δεν βάζουμε split κατηγορίας
                                    if hasattr(tx.category, 'hierarchy') and tx.category.hierarchy:
                                        cat_name = tx.category.hierarchy
                                    elif hasattr(tx.category, 'name'):
                                        cat_name = tx.category.name
                                    else:
                                        cat_name = str(tx.category)
                                    
                                    cat_id = self.get_or_create_category_recursive(cat_name, cat_type='Expense')                                
                                self.cur.execute("""
                                    INSERT INTO Splits (Transactions_Id, Categories_Id, Amount, Memo)
                                    VALUES (%s, %s, %s, %s)
                                """, (bt_id, self.clean_id(cat_id), tx.amount, tx.memo))

                    # Investment Transaction
                    elif hasattr(tx, 'security'):
                        if date_from and tx.date.date() < date_from:
                            continue
                        if date_to and tx.date.date() > date_to:
                            continue

                        ticker_val = (tx.security or "UNKNOWN")[:255]
                        
                        # Attempt to find security by name
                        s_id = self.get_id("Securities", "securities_id", "securities_name", ticker_val)
                        
                        if not s_id and ticker_val != "UNKNOWN":
                            # Create security using the account's currency
                            extra_fields = {
                                "ticker": ticker_val[:10], 
                                "securities_type": 'Stock',
                                "currencies_id": account_currency_id,
                                "is_active": True
                            }
                            s_id = self.get_or_create_id(
                                "Securities", "securities_id", "securities_name", ticker_val,
                                extra_fields
                            )
                        
                        c_sec_id = self.clean_id(s_id)
                        
                        # Map Quicken actions to database actions
                        action_map = {
                            'Buy': 'Buy', 'BuyX': 'Buy', 
                            'Sell': 'Sell', 'SellX': 'Sell',
                            'Div': 'Dividend', 'DivX': 'Dividend', 'Dividend': 'Dividend',
                            'ReinvDiv': 'Reinvest', 'ReinvInt': 'Reinvest', 
                            'Splt': 'Split', 'StkSplit': 'Split', 
                            'ShrsIn': 'ShrIn', 'ShrsOut': 'ShrOut',
                            'IntInc': 'IntInc', 'IntIncX': 'IntInc', 
                            'Cash': 'CashIn', 'XIn': 'CashIn', 'WithdrwX': 'CashOut', 'XOut': 'CashOut', 
                            'RtrnCap': 'RtrnCap', 'RtrnCapX': 'RtrnCap',
                            'MiscExpX': 'MiscExp', 'Grant': 'Grant',
                            'Vest': 'Vest', 'ExercisX': 'Exercise', 'Expire': 'Expire'
                        }
                        
                        raw_action = str(tx.action).strip() if hasattr(tx, 'action') else 'Buy'
                        my_action = action_map.get(raw_action, 'Buy')
                        
                        qnt = tx.quantity if hasattr(tx, 'quantity') and tx.quantity else 0
                        prc = tx.price if hasattr(tx, 'price') and tx.price and my_action != 'Reinvest' else 0
                        comm = tx.commission if hasattr(tx, 'commission') and tx.commission else 0
                        amt = tx.amount if hasattr(tx, 'amount') and tx.amount else 0
                        
                        # QIF has no FX data; assume account and security share the same
                        # currency → FX_Rate = 1.0, Total_Amount_SecCur = Total_Amount_AccCur
                        self.cur.execute("""
                            INSERT INTO Investments
                            (Accounts_Id, Securities_Id, Date, Action, Quantity, Price_Per_Share, Commission,
                             Total_Amount_AccCur, Total_Amount_SecCur, FX_Rate,
                             Description)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            RETURNING Investments_Id
                        """, (c_acc_id, c_sec_id, tx.date, my_action, qnt, prc, comm,
                              amt, amt, 1.0,
                              tx.memo if hasattr(tx, 'memo') else None))
                        inv_row = self.cur.fetchone()
                        inv_id = inv_row[0] if inv_row else None

                        inv_tx_count += 1

                        # ----------------------------------------------------------------
                        # Handle X-type actions: BuyX, SellX, DivX, IntIncX, RtrnCapX,
                        # MiscExpX, WithdrwX, ExercisX — cash goes to/from another account
                        # (L field = transfer_account, $ field = to_amount)
                        # ----------------------------------------------------------------
                        is_x_action = raw_action.endswith('X') or raw_action in ('XIn', 'XOut')
                        
                        if is_x_action:
                            # ------------------------------------------------------------------
                            # Resolve the linked cash account from the L field.
                            #
                            # The L field can arrive in several forms:
                            #   [Account Name]                  -> pure transfer
                            #   Category|[Account Name]         -> category piped with transfer
                            #   Category:Sub|[Account Name]     -> hierarchical category + transfer
                            #
                            # quiffen may expose this as transfer_account / to_account, or
                            # leave the raw string inside tx.category.
                            # ------------------------------------------------------------------
                            raw_transfer = getattr(tx, 'transfer_account', None) or getattr(tx, 'to_account', None)

                            if raw_transfer is None and hasattr(tx, 'category'):
                                raw_cat_str = str(tx.category) if tx.category else ""

                                # Handle pipe-separated "Category|[Account]" format
                                if '|' in raw_cat_str:
                                    raw_cat_str = raw_cat_str.split('|')[-1].strip()

                                if raw_cat_str.startswith('[') and raw_cat_str.endswith(']'):
                                    raw_transfer = raw_cat_str[1:-1]

                            # Normalise: strip brackets and pipe prefix regardless of source.
                            # quiffen sometimes leaves brackets on to_account for investment entries.
                            if raw_transfer:
                                raw_transfer = str(raw_transfer).strip()
                                if '|' in raw_transfer:
                                    raw_transfer = raw_transfer.split('|')[-1].strip()
                                raw_transfer = raw_transfer.strip('[]').strip()

                            # Cash amount: the $ field (to_amount); fall back to transaction amount
                            cash_amt = getattr(tx, 'to_amount', None)
                            if cash_amt is None:
                                cash_amt = amt

                            if raw_transfer and cash_amt:
                                # Resolve the linked cash account (map/create/fallback).
                                cash_acc_id = self._resolve_account(raw_transfer, account_map, account_currency_id)

                                # When L resolves to the SAME account, the buy/sell is
                                # internally funded (no external cash account involved).
                                # update_investment_balances() already debits Buy / credits Sell
                                # from the Investments table. We create an offsetting Transactions
                                # row so the net balance effect is zero:
                                #   BuyX  → credit +abs(cash_amt)  offsets the Investments debit
                                #   SellX → debit  -abs(cash_amt)  offsets the Investments credit
                                if cash_acc_id == c_acc_id:
                                    CASH_OUT_ACTIONS = {'BuyX', 'MiscExpX', 'WithdrwX', 'XIn'}
                                    self_amount = (
                                        abs(cash_amt) if raw_action in CASH_OUT_ACTIONS
                                        else -abs(cash_amt)
                                    )
                                    self.cur.execute("""
                                        INSERT INTO Transactions (
                                            Accounts_Id, Date, Payees_Id, Description,
                                            Total_Amount, Cleared
                                        ) VALUES (%s, %s, %s, %s, %s, %s)
                                    """, (
                                        c_acc_id, tx.date, None,
                                        tx.memo if hasattr(tx, 'memo') and tx.memo else raw_action,
                                        self_amount, True
                                    ))
                                    bank_tx_count += 1
                                else:
                                    # Determine cash flow direction from the linked account's perspective:
                                    # BuyX / MiscExpX / WithdrwX  → cash leaves the linked account (debit, negative)
                                    # SellX / DivX / IntIncX / RtrnCapX / ExercisX → cash arrives (credit, positive)
                                    # cash_tx_amount: the amount as it appears in the CASH account.
                                    # Cash OUT actions (BuyX, XIn, WithdrwX, MiscExpX):
                                    #   cash account loses money → negative
                                    # Cash IN actions (SellX, DivX, IntIncX, RtrnCapX, XOut):
                                    #   cash account gains money → positive
                                    CASH_OUT_ACTIONS = {
                                        'BuyX', 'MiscExpX', 'WithdrwX', 'XIn'
                                    }
                                    CASH_IN_ACTIONS = {
                                        'SellX', 'DivX', 'IntIncX', 'RtrnCapX', 'ExercisX', 'XOut'
                                    }
                                    if raw_action in CASH_OUT_ACTIONS:
                                        cash_tx_amount = -abs(cash_amt)
                                    else:
                                        cash_tx_amount = abs(cash_amt)

                                    # Duplicate guard: the bank transfer block may have already
                                    # inserted a mirror row for this cash leg when both the bank
                                    # account and investment account appear in the QIF.
                                    # Match on date + amount + both account IDs (both directions)
                                    # to avoid false positives from unrelated same-date/amount rows.
                                    # Check whether the bank transfer block already created
                                    # rows for this cash leg. The bank block always records the
                                    # cash account as source with the natural sign (negative for
                                    # outflows, positive for inflows). We check both +/- abs(amt)
                                    # to cover all action types (BuyX, SellX, XIn, DivX, etc.)
                                    # regardless of the sign convention used by cash_tx_amount.
                                    # Match on date + account pair only (no amount).
                                    # Cross-currency transfers have different amounts on each
                                    # side (e.g. -100 EUR bank / +103.12 USD brokerage), so
                                    # amount-based matching would miss the existing row.
                                    # Use occurrence counter to allow multiple genuine
                                    # transfers between the same accounts on the same date.
                                    _x_key = (tx.date, min(cash_acc_id, c_acc_id),
                                              max(cash_acc_id, c_acc_id))
                                    _x_type_occurrence_count[_x_key] = (
                                        _x_type_occurrence_count.get(_x_key, 0) + 1
                                    )
                                    _x_nth = _x_type_occurrence_count[_x_key]

                                    self.cur.execute("""
                                        SELECT Transfers_Id FROM Transactions
                                        WHERE Date = %s
                                          AND Accounts_Id = %s
                                          AND Accounts_Id_Target = %s
                                          AND Transfers_Id IS NOT NULL
                                        UNION
                                        SELECT Transfers_Id FROM Transactions
                                        WHERE Date = %s
                                          AND Accounts_Id = %s
                                          AND Accounts_Id_Target = %s
                                          AND Transfers_Id IS NOT NULL
                                        ORDER BY 1 ASC
                                        LIMIT 1 OFFSET %s
                                    """, (
                                        tx.date, cash_acc_id, c_acc_id,
                                        tx.date, c_acc_id,   cash_acc_id,
                                        _x_nth - 1
                                    ))
                                    existing_x_transfer = self.cur.fetchone()

                                    if not existing_x_transfer:
                                        # Check if the investment account (c_acc_id) is a
                                        # Brokerage/investment-type account. For those, the
                                        # Investments table CashIn/CashOut row already captures
                                        # the cash movement — inserting a Transactions mirror
                                        # into the brokerage account would be redundant.
                                        # We only insert into the CASH account side (e.g. Revolut).
                                        BROKERAGE_ONLY = ('Brokerage',)
                                        self.cur.execute(
                                            "SELECT Accounts_Type FROM Accounts WHERE Accounts_Id = %s",
                                            (c_acc_id,)
                                        )
                                        _acc_type_row = self.cur.fetchone()
                                        _inv_acc_is_brokerage = (
                                            _acc_type_row and
                                            _acc_type_row[0] in BROKERAGE_ONLY
                                        )

                                        self.cur.execute("SELECT nextval('transfers_id_seq')")
                                        transfer_link_id = self.cur.fetchone()[0]

                                        # Always insert the cash-side (bank/card) transaction.
                                        # For brokerage accounts: Accounts_Id_Target points to
                                        # the investment account (informational — no mirror
                                        # Transactions row; the Investments table is authoritative).
                                        self.cur.execute("""
                                            INSERT INTO Transactions (
                                                Accounts_Id, Date, Payees_Id, Description,
                                                Total_Amount, Cleared, Accounts_Id_Target,
                                                Total_Amount_Target, Transfers_Id
                                            )
                                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                                            RETURNING Transactions_Id
                                        """, (
                                            cash_acc_id,
                                            tx.date,
                                            None,
                                            tx.memo if hasattr(tx, 'memo') else raw_action,
                                            cash_tx_amount,
                                            True,
                                            c_acc_id,               # always set target to investment account
                                            abs(amt) if amt else abs(cash_tx_amount),  # investment amount
                                            None if _inv_acc_is_brokerage else transfer_link_id
                                        ))
                                        cash_tx_id = self.cur.fetchone()[0]
                                        bank_tx_count += 1

                                        # Back-fill Transactions_Id on the Investments row
                                        # so cascade delete/update can find this cash transaction.
                                        if inv_id and _inv_acc_is_brokerage:
                                            self.cur.execute(
                                                "UPDATE Investments SET Transactions_Id = %s WHERE Investments_Id = %s",
                                                (cash_tx_id, inv_id)
                                            )

                                        # Only insert the investment-account mirror in Transactions
                                        # if it is NOT a brokerage account (the Investments table
                                        # CashIn/CashOut row already serves as the mirror for those).
                                        if not _inv_acc_is_brokerage:
                                            self.cur.execute("""
                                                INSERT INTO Transactions (
                                                    Accounts_Id, Date, Payees_Id, Description,
                                                    Total_Amount, Cleared, Accounts_Id_Target,
                                                    Total_Amount_Target, Transfers_Id
                                                )
                                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                                            """, (
                                                c_acc_id,
                                                tx.date,
                                                None,
                                                tx.memo if hasattr(tx, 'memo') else raw_action,
                                                -cash_tx_amount,
                                                True,
                                                cash_acc_id,
                                                cash_tx_amount,
                                                transfer_link_id
                                            ))
                                            bank_tx_count += 1
            
            self.conn.commit()
        
        st.success(f"✅ Imported {acc_count} accounts, {bank_tx_count} bank transactions, {inv_tx_count} investment transactions!")

        return {
            'accounts_created': accounts_created,
            'accounts_mapped': accounts_mapped,
            'accounts_skipped': accounts_skipped,
            'accounts_fallback': accounts_fallback,
            'bank_tx_count': bank_tx_count,
            'inv_tx_count': inv_tx_count,
            'has_bank_tx': bank_tx_count > 0,
            'has_inv_tx': inv_tx_count > 0,
        }
    
    def import_prices_from_qif(self, qif_file_path):
        """Import historical prices from QIF file"""
        st.info("📈 Importing Historical Prices...")
        price_count = 0
        
        with open(qif_file_path, 'r', encoding='latin-1') as f:
            for line in f:
                parts = line.strip().split(',')
                if len(parts) == 3:
                    ticker = parts[0].strip()
                    price_value = parts[1].strip()
                    raw_date = parts[2].strip()
                    
                    try:
                        # Parse date
                        clean_date = raw_date.replace(" ", "").replace("'", "/")
                        date_obj = datetime.strptime(clean_date, "%m/%d/%y")
                        
                        # Get security ID
                        s_id = self.get_id("Securities", "securities_id", "ticker", ticker)
                        if not s_id:
                            s_id = self.get_id("Securities", "securities_id", "securities_name", ticker)
                        
                        if s_id:
                            c_sec_id = self.clean_id(s_id)
                            
                            self.cur.execute("""
                                INSERT INTO Historical_Prices (Securities_Id, Date, Close)
                                VALUES (%s, %s, %s)
                                ON CONFLICT (Securities_Id, Date) 
                                DO NOTHING
                            """, (c_sec_id, date_obj.date(), float(price_value)))
                            
                            price_count += 1
                            
                    except (ValueError, IndexError) as e:
                        continue
        
        self.conn.commit()
        st.success(f"✅ Imported {price_count} historical prices!")
    
    def _parse_qif_file(self, qif_file_path):
        """Pre-clean and parse a QIF file into a quiffen.Qif object. Read-only —
        never touches self.conn/self.cur or the database. Shared by both
        import_full_qif and preview_qif so the two always parse identically."""
        # Handles two classes of malformed transactions that crash quiffen:
        #   1. Amount fields (T/U/O/$) with no digits -> replaced with 0.00
        #   2. Transaction blocks with no D (date) line -> dropped entirely
        import re as _re, tempfile as _tempfile, os as _os
        _amount_line_re = _re.compile(r'^([TUO$])(.*)$')
        _valid_decimal_re = _re.compile(r'[0-9]')

        with open(qif_file_path, 'r', encoding='latin-1') as _f:
            _raw_lines = _f.readlines()

        # Split into transaction blocks (separated by ^), clean each one,
        # then drop blocks that have no date line.
        _cleaned_lines = []
        _current_block = []
        for _line in _raw_lines:
            _stripped = _line.rstrip('\n\r')
            if _stripped == '^':
                # End of block — validate and flush
                _has_date = any(l.startswith('D') for l in _current_block)
                _is_header = any(l.startswith('!') for l in _current_block)
                if _is_header or _has_date:
                    # Fix any bad amount lines before keeping
                    _fixed_block = []
                    for _bl in _current_block:
                        _m = _amount_line_re.match(_bl.rstrip('\n\r'))
                        if _m:
                            _prefix, _val = _m.group(1), _m.group(2)
                            if not _valid_decimal_re.search(_val):
                                _bl = _prefix + '0.00\n'
                        _fixed_block.append(_bl)
                    _cleaned_lines.extend(_fixed_block)
                    _cleaned_lines.append('^\n')
                # else: silently drop the dateless block
                _current_block = []
            else:
                _current_block.append(_line)
        # Flush any trailing lines after last ^
        if _current_block:
            _cleaned_lines.extend(_current_block)

        # Remove stray !Type: lines that appear immediately before !Account.
        # This QIF format emits !Type:<x> between every pair of !Account blocks
        # as a separator, but quiffen misinterprets them as setting the type
        # context for the PREVIOUS account, causing transactions to be attributed
        # to the wrong account. Strip them out entirely.
        _stripped_lines = []
        _n = len(_cleaned_lines)
        for _i, _cl in enumerate(_cleaned_lines):
            if _cl.rstrip('\n\r').startswith('!Type:'):
                # Look ahead (skip blank lines) for next non-blank line
                _j = _i + 1
                while _j < _n and _cleaned_lines[_j].strip() == '':
                    _j += 1
                if _j < _n and _cleaned_lines[_j].strip() == '!Account':
                    continue  # drop this stray !Type: line
            _stripped_lines.append(_cl)
        _cleaned_lines = _stripped_lines

        with _tempfile.NamedTemporaryFile(mode='w', encoding='latin-1',
                                          suffix='.qif', delete=False) as _tmp:
            _tmp.writelines(_cleaned_lines)
            _clean_path = _tmp.name

        try:
            # Monkey-patch quiffen's Transaction.from_list to skip dateless/invalid
            # transactions instead of raising ValidationError. Handles QIF files where
            # a missing ^ separator causes quiffen to see a no-date sub-block.
            import quiffen.core.transaction as _qtx
            from pydantic import ValidationError as _ValErr

            _orig_from_list = _qtx.Transaction.from_list.__func__

            import datetime as _dt

            @classmethod
            def _safe_from_list(cls, lst, day_first=False, line_number=0):
                try:
                    return _orig_from_list(cls, lst, day_first=day_first, line_number=line_number)
                except Exception as _e:
                    # Catch any transaction-level parse failure and return a sentinel
                    # that the import loop will recognise and skip:
                    #   - Missing date field      (ValidationError: "field required")
                    #   - Unparseable date value  (ParserError: "unknown string format")
                    #   - Bad amount value        (InvalidOperation / ConversionSyntax)
                    _sentinel = cls(
                        date=_dt.datetime(1, 1, 1),
                        amount=0,
                        memo='__QIF_SKIP__'
                    )
                    return _sentinel, {}

            _qtx.Transaction.from_list = _safe_from_list

            # Patch ALL quiffen from_list methods that can crash on malformed QIF data.
            # Each returns a minimal sentinel/placeholder so parse_string can continue.
            import quiffen.core.category as _qcat
            import quiffen.core.investment as _qinv
            import quiffen.core.class_type as _qcls
            import quiffen.core.security as _qsec

            _orig_cat_from_list = _qcat.Category.from_list.__func__
            _orig_inv_from_list = _qinv.Investment.from_list.__func__
            _orig_cls_from_list = _qcls.Class.from_list.__func__
            _orig_sec_from_list = _qsec.Security.from_list.__func__

            @classmethod
            def _safe_cat_from_list(cls, lst):
                try:
                    return _orig_cat_from_list(cls, lst)
                except Exception:
                    return cls(name='__INVALID_CATEGORY__')

            @classmethod
            def _safe_inv_from_list(cls, lst, day_first=False, line_number=0):
                try:
                    return _orig_inv_from_list(cls, lst, day_first=day_first, line_number=line_number)
                except Exception:
                    return cls(date=_dt.datetime(1, 1, 1), memo='__QIF_SKIP__')

            @classmethod
            def _safe_cls_from_list(cls, lst):
                try:
                    return _orig_cls_from_list(cls, lst)
                except Exception:
                    return cls(name='__INVALID_CLASS__')

            @classmethod
            def _safe_sec_from_list(cls, lst, line_number=0):
                try:
                    return _orig_sec_from_list(cls, lst, line_number=line_number)
                except Exception:
                    return cls(name='__INVALID_SECURITY__')

            _qcat.Category.from_list = _safe_cat_from_list
            _qinv.Investment.from_list = _safe_inv_from_list
            _qcls.Class.from_list = _safe_cls_from_list
            _qsec.Security.from_list = _safe_sec_from_list

            qif = quiffen.Qif.parse(_clean_path, day_first=False, encoding='latin-1')
        finally:
            _os.unlink(_clean_path)
            # Restore all patched methods
            try:
                _qtx.Transaction.from_list = classmethod(_orig_from_list)
                _qcat.Category.from_list = classmethod(_orig_cat_from_list)
                _qinv.Investment.from_list = classmethod(_orig_inv_from_list)
                _qcls.Class.from_list = classmethod(_orig_cls_from_list)
                _qsec.Security.from_list = classmethod(_orig_sec_from_list)
            except Exception:
                pass
        return qif

    def preview_qif(self, qif_file_path):
        """Read-only Parse-step preview: parses the file and returns a per-account
        summary (QIF type, suggested Oikos type/group, transaction counts, date
        range, existing-account-name match) plus a global date range and totals.
        Never writes, never disables triggers, never clears tables — only issues
        read-only SELECTs (existing-name lookups) against the database."""
        self.connect()
        try:
            qif = self._parse_qif_file(qif_file_path)

            accounts = []
            global_min = None
            global_max = None
            total_bank_tx = 0
            total_inv_tx = 0

            for acc_name, acc_obj in qif.accounts.items():
                # acc_obj.account_type (from the !Account block's own T line) isn't
                # reliably populated by quiffen in practice — but the AccountType
                # keys of acc_obj.transactions (set from each !Type:X transaction
                # section header) are, so prefer those; account_type is only a
                # fallback for the edge case of an account with zero transactions.
                txn_type_keys = [k for k in acc_obj.transactions.keys()
                                  if (k.value if hasattr(k, 'value') else str(k)) != 'Unknown']
                if txn_type_keys:
                    qif_type = txn_type_keys[0].value if hasattr(txn_type_keys[0], 'value') else str(txn_type_keys[0])
                else:
                    qif_type_raw = getattr(acc_obj, 'account_type', None)
                    qif_type = qif_type_raw.value if hasattr(qif_type_raw, 'value') else (str(qif_type_raw) if qif_type_raw else 'Unknown')
                if qif_type not in _QIF_TO_OIKOS_TYPE:
                    qif_type = 'Unknown'

                bank_tx_count = 0
                inv_tx_count = 0
                acc_min = None
                acc_max = None

                for tx_list in acc_obj.transactions.values():
                    for tx in tx_list:
                        if getattr(tx, 'memo', None) == '__QIF_SKIP__':
                            continue
                        if not (hasattr(tx, 'payee') or hasattr(tx, 'security')):
                            continue
                        if hasattr(tx, 'payee'):
                            bank_tx_count += 1
                        else:
                            inv_tx_count += 1
                        tx_date = tx.date.date()
                        if acc_min is None or tx_date < acc_min:
                            acc_min = tx_date
                        if acc_max is None or tx_date > acc_max:
                            acc_max = tx_date

                if acc_min and (global_min is None or acc_min < global_min):
                    global_min = acc_min
                if acc_max and (global_max is None or acc_max > global_max):
                    global_max = acc_max
                total_bank_tx += bank_tx_count
                total_inv_tx += inv_tx_count

                self.cur.execute(
                    "SELECT Accounts_Id, Accounts_Type FROM Accounts WHERE Accounts_Name = %s", (acc_name,)
                )
                existing = self.cur.fetchone()

                accounts.append({
                    'qif_name': acc_name,
                    'qif_type': qif_type,
                    'suggested_oikos_type': _QIF_TO_OIKOS_TYPE.get(qif_type, 'Other'),
                    'suggested_group': _QIF_TYPE_TO_GROUP.get(qif_type, 'other'),
                    'bank_tx_count': bank_tx_count,
                    'inv_tx_count': inv_tx_count,
                    'min_date': acc_min.isoformat() if acc_min else None,
                    'max_date': acc_max.isoformat() if acc_max else None,
                    'existing_match_id': existing[0] if existing else None,
                    'existing_match_type': existing[1] if existing else None,
                })

            return {
                'accounts': accounts,
                'date_range': {
                    'min': global_min.isoformat() if global_min else None,
                    'max': global_max.isoformat() if global_max else None,
                },
                'totals': {
                    'accounts': len(accounts),
                    'bank_tx': total_bank_tx,
                    'inv_tx': total_inv_tx,
                },
            }
        finally:
            self.disconnect()

    def import_full_qif(self, qif_file_path, tables_to_clear, import_options,
                        exclude_bank_account_ids=None,
                        exclude_inv_account_ids=None,
                        account_map=None,
                        date_from=None,
                        date_to=None):
        """Complete QIF import process"""
        try:
            self.connect()

            # Ensure Transfer_Issues table exists
            self.ensure_transfer_issues_table()

            # Disable triggers
            self.disable_triggers()

            # Clear selected tables (honouring account exclusions)
            if tables_to_clear:
                self.clear_tables(
                    tables_to_clear,
                    exclude_bank_account_ids=exclude_bank_account_ids,
                    exclude_inv_account_ids=exclude_inv_account_ids,
                )

            # Parse QIF file
            st.info("📄 Parsing QIF file...")
            qif = self._parse_qif_file(qif_file_path)
            st.success("✅ QIF file parsed successfully!")

            # Track what was imported
            has_bank_tx = False
            has_inv_tx = False
            summary = {}

            # Import based on selections
            if import_options.get('import_categories', True):
                self.import_categories_from_qif(qif_file_path)

            if import_options.get('import_securities', True):
                self.import_securities(qif)

            if import_options.get('import_accounts', True):
                summary = self.import_accounts_and_transactions(
                    qif, account_map=account_map, date_from=date_from, date_to=date_to
                )
                has_bank_tx, has_inv_tx = summary['has_bank_tx'], summary['has_inv_tx']
                issue_count = self.scan_date_mismatched_transfers()
                if issue_count:
                    st.warning(f"⚠️ {issue_count} transfer(s) flagged with date mismatches — review in Transfer Issues.")

            if import_options.get('import_prices', True):
                self.import_prices_from_qif(qif_file_path)

            # Automatic post-processing based on what was imported
            st.info("🔄 Automatically updating database statistics...")
            update_db_stats()
            if has_bank_tx or import_options.get('force_update_balances', False):
                st.info("🔄 Automatically updating account balances (bank & cash accounts)...")
                update_accounts_balances()
                st.info("🔄 Automatically updating pension account balances...")
                update_pension_balances()
                st.info("🔄 Automatically updating investment cash balances...")
                update_investment_balances()

            if has_inv_tx or import_options.get('force_update_holdings', False):
                st.info("🔄 Automatically updating holdings...")
                update_holdings()

            # Re-enable triggers
            self.enable_triggers()

            st.success("✅ QIF import completed successfully!")
            return summary

        except Exception as e:
            st.error(f"❌ Error during import: {str(e)}")
            raise e
        finally:
            self.disconnect()


def render_qif_importer():
    """Render the QIF Importer UI component"""
    st.subheader("📁 QIF File Importer")
    st.markdown("Import data from Quicken QIF files into your Finance OS database")
    st.warning("⚠️ This tool will modify your database. Please backup before proceeding!")
    
    # File upload
    uploaded_file = st.file_uploader(
        "Choose a QIF file",
        type=['qif'],
        help="Select a QIF file exported from Quicken or other financial software"
    )
    
    if uploaded_file is not None:
        # Create a temporary file using Python's tempfile module (Windows compatible)
        try:
            # Use tempfile.NamedTemporaryFile for cross-platform compatibility
            with tempfile.NamedTemporaryFile(mode='wb', suffix='.qif', delete=False) as tmp_file:
                tmp_file.write(uploaded_file.getbuffer())
                temp_path = tmp_file.name
            
            st.success(f"✅ File loaded: {uploaded_file.name}")
            st.info(f"📁 Temporary file: {temp_path}")
            
        except Exception as e:
            st.error(f"Failed to create temporary file: {str(e)}")
            return
        
        # ── Fetch accounts for exclusion pickers ──────────────────────────────
        try:
            _conn_tmp = get_connection()
            df_all_accs = pd.read_sql(
                "SELECT Accounts_Id AS acc_id, Accounts_Name AS acc_name "
                "FROM Accounts ORDER BY Accounts_Name",
                _conn_tmp,
            )
            df_inv_accs = pd.read_sql(
                """SELECT DISTINCT a.Accounts_Id AS acc_id, a.Accounts_Name AS acc_name
                   FROM Accounts a
                   JOIN Investments i ON i.Accounts_Id = a.Accounts_Id
                   ORDER BY a.Accounts_Name""",
                _conn_tmp,
            )
            _conn_tmp.close()
        except Exception:
            df_all_accs = pd.DataFrame(columns=['acc_id', 'acc_name'])
            df_inv_accs = pd.DataFrame(columns=['acc_id', 'acc_name'])

        _all_acc_map = dict(zip(df_all_accs['acc_id'], df_all_accs['acc_name']))
        _inv_acc_map = dict(zip(df_inv_accs['acc_id'], df_inv_accs['acc_name']))

        # ── Load saved exclusion settings ──────────────────────────────────────
        _saved_bank_excl = get_nwr_account_selection('qif_exclude_bank_accs') or []
        _saved_inv_excl  = get_nwr_account_selection('qif_exclude_inv_accs')  or []
        # Guard: keep only IDs that still exist in the DB
        _saved_bank_excl = [i for i in _saved_bank_excl if i in _all_acc_map]
        _saved_inv_excl  = [i for i in _saved_inv_excl  if i in _inv_acc_map]

        # ── Table selection for clearing ───────────────────────────────────────
        st.markdown("### 🗑️ Select Tables to Clear Before Import")
        st.warning("⚠️ Clearing tables will permanently delete existing data in selected tables")

        col1, col2 = st.columns(2)

        with col1:
            clear_categories  = st.checkbox("🗂️ Categories",               value=False, key="clear_categories")
            clear_bank_tx     = st.checkbox("🏦 Bank Transactions",          value=False, key="clear_bank_tx")
            clear_bank_splits = st.checkbox("📊 Bank Transaction Splits",   value=False, key="clear_bank_splits")

        with col2:
            clear_inv_tx  = st.checkbox("📈 Investment Transactions", value=False, key="clear_inv_tx")
            clear_holdings = st.checkbox("💼 Holdings",               value=False, key="clear_holdings")

        # ── Account exclusion settings ─────────────────────────────────────────
        st.markdown("### 🔒 Account Exclusion Settings")
        st.caption(
            "Select accounts whose data should be **preserved** when clearing. "
            "The same bank-account selection applies to both Bank Transactions and Splits. "
            "Settings are saved independently of the import and persist across sessions."
        )

        excl_col1, excl_col2 = st.columns(2)

        with excl_col1:
            excl_bank_accounts = st.multiselect(
                "🏦 Preserve these accounts (Bank TX & Splits):",
                options=list(_all_acc_map.keys()),
                default=_saved_bank_excl,
                format_func=lambda x: _all_acc_map.get(x, str(x)),
                key="qif_excl_bank",
                help="Bank Transactions and Splits for these accounts will NOT be deleted.",
                disabled=not (clear_bank_tx or clear_bank_splits),
            )

        with excl_col2:
            excl_inv_accounts = st.multiselect(
                "📈 Preserve these accounts (Investment TX):",
                options=list(_inv_acc_map.keys()),
                default=_saved_inv_excl,
                format_func=lambda x: _inv_acc_map.get(x, str(x)),
                key="qif_excl_inv",
                help="Investment Transactions for these accounts will NOT be deleted.",
                disabled=not clear_inv_tx,
            )

        if st.button("💾 Save Exclusion Settings", key="qif_save_excl"):
            save_nwr_account_selection(excl_bank_accounts, 'qif_exclude_bank_accs')
            save_nwr_account_selection(excl_inv_accounts,  'qif_exclude_inv_accs')
            st.success("✅ Exclusion settings saved.")

        # ── Build list of tables to clear ─────────────────────────────────────
        tables_to_clear = []
        if clear_categories:
            tables_to_clear.append("Categories")
        if clear_bank_tx:
            tables_to_clear.append("Transactions")
        if clear_bank_splits:
            tables_to_clear.append("Splits")
        if clear_inv_tx:
            tables_to_clear.append("Investments")
        if clear_holdings:
            tables_to_clear.append("Holdings")
        
        # Import options
        st.markdown("### 📥 Import Options")
        
        col1, col2 = st.columns(2)
        
        with col1:
            import_categories = st.checkbox("📂 Import Categories", value=True)
            import_securities = st.checkbox("📊 Import Securities", value=True)
            import_accounts = st.checkbox("💰 Import Accounts & Transactions", value=True)
        
        with col2:
            import_prices = st.checkbox("📈 Import Historical Prices", value=True)
            force_update_balances = st.checkbox("🔄 Force Update Account Balances", value=False, 
                                                help="Update balances even if no new transactions were imported")
            force_update_holdings = st.checkbox("💼 Force Update Holdings", value=False,
                                                help="Update holdings even if no new investment transactions were imported")
        
        import_options = {
            'import_categories': import_categories,
            'import_securities': import_securities,
            'import_accounts': import_accounts,
            'import_prices': import_prices,
            'force_update_balances': force_update_balances,
            'force_update_holdings': force_update_holdings
        }
        
        # Show summary
        with st.expander("📋 Import Summary"):
            st.markdown("**Tables to clear:**")
            if tables_to_clear:
                for table in tables_to_clear:
                    st.write(f"  - {table}")
            else:
                st.write("  - No tables will be cleared (data will be appended)")

            if excl_bank_accounts:
                preserved = ", ".join(_all_acc_map.get(i, str(i)) for i in excl_bank_accounts)
                st.markdown(f"**Bank TX / Splits — preserved accounts:** {preserved}")
            if excl_inv_accounts:
                preserved = ", ".join(_inv_acc_map.get(i, str(i)) for i in excl_inv_accounts)
                st.markdown(f"**Investment TX — preserved accounts:** {preserved}")
            
            st.markdown("**Data to import:**")
            st.write(f"  - Categories: {'Yes' if import_categories else 'No'}")
            st.write(f"  - Securities: {'Yes' if import_securities else 'No'}")
            st.write(f"  - Accounts & Transactions: {'Yes' if import_accounts else 'No'}")
            st.write(f"  - Historical Prices: {'Yes' if import_prices else 'No'}")
            
            st.markdown("**Post-import actions:**")
            if import_accounts:
                st.write("  - Account balances will be automatically updated if bank transactions imported")
                st.write("  - Holdings will be automatically updated if investment transactions imported")
            if force_update_balances:
                st.write("  - Account balances will be forcibly updated")
            if force_update_holdings:
                st.write("  - Holdings will be forcibly updated")
        
        # Import button
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            if st.button("🚀 Start QIF Import", type="primary", width='stretch'):
                try:
                    importer = QIFImporter()
                    importer.import_full_qif(
                        temp_path, tables_to_clear, import_options,
                        exclude_bank_account_ids=excl_bank_accounts or None,
                        exclude_inv_account_ids=excl_inv_accounts  or None,
                    )
                    
                    st.balloons()
                    st.success("✅ QIF import completed successfully!")
                    st.info("🔄 Refreshing page to show updated data...")
                    
                    # Clean up temp file
                    try:
                        os.unlink(temp_path)
                    except:
                        pass
                    
                    st.rerun()
                    
                except Exception as e:
                    st.error(f"❌ Import failed: {str(e)}")
                    st.exception(e)
                finally:
                    # Clean up temp file
                    try:
                        if os.path.exists(temp_path):
                            os.unlink(temp_path)
                    except:
                        pass
        
        # File preview
        with st.expander("📄 Preview QIF File (first 50 lines)"):
            try:
                # Reset file pointer to beginning
                uploaded_file.seek(0)
                content = uploaded_file.getvalue().decode('latin-1')
                lines = content.split('\n')[:50]
                st.code('\n'.join(lines), language='text')
            except Exception as e:
                st.write(f"Could not preview file: {e}")