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
from database.crud import update_accounts_balances, update_db_stats, update_investment_balances, update_pension_balances, update_holdings

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
    
    def clear_tables(self, tables_to_clear):
        """Clear specified tables"""
        for table in tables_to_clear:
            st.write(f"  - Clearing {table}...")
            self.cur.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE;")
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
            sectype = security.type if hasattr(security, 'type') else 'Stock'
            
            # Check if security already exists
            existing_id = self.get_id("Securities", "securities_id", "ticker", ticker)
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
    
    def import_accounts_and_transactions(self, qif):
        """Import accounts and transactions from QIF object"""
        st.info("💰 Importing Accounts and Transactions...")
        
        acc_count = 0
        bank_tx_count = 0
        inv_tx_count = 0
        
        for acc_name, acc_obj in qif.accounts.items():
            # Get currency
            qif_currency = getattr(acc_obj, 'currency', 'EUR')
            
            # Create/Get Currency
            curr_id = self.get_or_create_id(
                'Currencies', 'Currencies_Id', 'Currencies_ShortName', qif_currency,
                {'Currencies_Name': qif_currency}
            )
            
            # Create/Get Account
            acc_id = self.get_or_create_id(
                'Accounts', 'Accounts_Id', 'Accounts_Name', acc_name,
                {'Accounts_Type': 'Checking', 'Currencies_Id': curr_id}
            )
            acc_count += 1
            
            c_acc_id = self.clean_id(acc_id)

            # 1. Πρώτα ανακτούμε το Currencies_Id του τρέχοντος λογαριασμού
            # Υποθέτουμε ότι το c_acc_id είναι το ID του λογαριασμού που επεξεργάζεστε
            self.cur.execute("SELECT Currencies_Id FROM Accounts WHERE Accounts_Id = %s", (c_acc_id,))
            account_currency_id = self.cur.fetchone()[0]

            # Process transactions
            for tx_list in acc_obj.transactions.values():
                for tx in tx_list:
                    # Bank Transaction
                    if hasattr(tx, 'payee'):
                        # Παράλειψη αν το συνολικό ποσό της συναλλαγής είναι 0 (προαιρετικό, ανάλογα με τη λογική σας)
                        if tx.amount == 0:
                            continue

                        # 1. Ανίχνευση Μεταφοράς
                        is_transfer = False
                        target_acc_id = None
                        raw_cat = str(tx.category) if tx.category else ""

                        if raw_cat.startswith('[') and raw_cat.endswith(']'):
                            is_transfer = True
                            target_acc_name = raw_cat[1:-1]
                            target_acc_id = self.get_or_create_id(
                                "Accounts", "Accounts_Id", "Accounts_Name", target_acc_name
                            )

                        # 2. Έλεγχος για Διπλότυπη Μεταφορά (Matching)
                        existing_transfer_id = None
                        if is_transfer:
                            self.cur.execute("""
                                SELECT Transfers_Id FROM Transactions 
                                WHERE Accounts_Id = %s AND Accounts_Id_Target = %s 
                                AND Date = %s AND Total_Amount = %s
                                LIMIT 1
                            """, (target_acc_id, c_acc_id, tx.date, -tx.amount))
                            
                            res = self.cur.fetchone()
                            if res:
                                existing_transfer_id = res[0]
                                
                        # 3. Payee & Cleared status
                        p_id = self.get_or_create_id("Payees", "payees_id", "payees_name", tx.payee) if tx.payee else None
                        c_payee_id = self.clean_id(p_id)
                        is_cleared = True if tx.cleared in ['X', '*', 'R'] else False

                        # 4. Καθορισμός Transfers_Id
                        current_transfer_id = existing_transfer_id
                        if is_transfer and not current_transfer_id:
                            # Χρήση sequence για παραγωγή νέου ID μεταφοράς
                            self.cur.execute("SELECT nextval('transactions_transactions_id_seq')") 
                            current_transfer_id = self.cur.fetchone()[0]

                        # 5. Εισαγωγή στην Transactions
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

                        # 6. Εισαγωγή Splits με έλεγχο μηδενικού ποσού
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
                        ticker_val = (tx.security or "UNKNOWN")[:255]
                        
                        # Προσπάθεια εύρεσης του security
                        s_id = self.get_id("Securities", "securities_id", "securities_name", ticker_val)
                        
                    #    if not s_id and ticker_val is not None and ticker_val != "UNKNOWN":
                        if not s_id and ticker_val != "UNKNOWN":
                            # 2. Δημιουργία security με το νόμισμα του λογαριασμού
                            # Προσαρμόζουμε το dictionary των extra πεδίων για να περιλαμβάνει το Currencies_Id
                            extra_fields = {
                                "ticker": ticker_val[:10], 
                                "securities_type": 'Stock',
                                "currencies_id": account_currency_id,  # Εδώ μπαίνει το νόμισμα του λογαριασμού
                                "is_active": True
                            }
                            
                            # Χρήση της get_or_create_id (βεβαιωθείτε ότι η μέθοδος δέχεται extra_fields για το INSERT)
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
                        
                        # QIF has no FX data; FX_Rate = 1.0, SecCur = AccCur
                        self.cur.execute("""
                            INSERT INTO Investments
                            (Accounts_Id, Securities_Id, Date, Action, Quantity, Price_Per_Share, Commission,
                             Total_Amount_AccCur, Total_Amount_SecCur, FX_Rate,
                             Description)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (c_acc_id, c_sec_id, tx.date, my_action, qnt, prc, comm,
                              amt, amt, 1.0,
                              tx.memo if hasattr(tx, 'memo') else None))
                        
                        inv_tx_count += 1
            
            self.conn.commit()
        
        st.success(f"✅ Imported {acc_count} accounts, {bank_tx_count} bank transactions, {inv_tx_count} investment transactions!")
        
        # Return flags indicating what was imported
        return bank_tx_count > 0, inv_tx_count > 0
    
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
    
    def import_full_qif(self, qif_file_path, tables_to_clear, import_options):
        """Complete QIF import process"""
        try:
            self.connect()
            
            # Disable triggers
            self.disable_triggers()
            
            # Clear selected tables
            if tables_to_clear:
                self.clear_tables(tables_to_clear)
            
            # Parse QIF file
            st.info("📄 Parsing QIF file...")
            qif = quiffen.Qif.parse(qif_file_path, day_first=False, encoding='latin-1')
            st.success("✅ QIF file parsed successfully!")
            
            # Track what was imported
            has_bank_tx = False
            has_inv_tx = False
            
            # Import based on selections
            if import_options.get('import_categories', True):
                self.import_categories_from_qif(qif_file_path)
            
            if import_options.get('import_securities', True):
                self.import_securities(qif)
            
            if import_options.get('import_accounts', True):
                has_bank_tx, has_inv_tx = self.import_accounts_and_transactions(qif)
            
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
        
        # Table selection for clearing
        st.markdown("### 🗑️ Select Tables to Clear Before Import")
        st.warning("⚠️ Clearing tables will permanently delete existing data in selected tables")
        
        col1, col2 = st.columns(2)
        
        with col1:
            clear_categories = st.checkbox("🗂️ Categories", value=False, key="clear_categories")
            clear_bank_tx = st.checkbox("🏦 Bank Transactions", value=False, key="clear_bank_tx")
            clear_bank_splits = st.checkbox("📊 Bank Transaction Splits", value=False, key="clear_bank_splits")
        
        with col2:
            clear_inv_tx = st.checkbox("📈 Investment Transactions", value=False, key="clear_inv_tx")
            clear_holdings = st.checkbox("💼 Holdings", value=False, key="clear_holdings")
        
        # Build list of tables to clear
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
                    importer.import_full_qif(temp_path, tables_to_clear, import_options)
                    
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