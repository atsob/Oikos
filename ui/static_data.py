import streamlit as st
import pandas as pd
from database.connection import get_db
from database.crud import save_changes
from database.crud import update_payee_default_category
from data.downloaders import download_securities_info_from_yahoo
from ui.components import copy_df_button


# render_settings() was removed — superseded by render_static_data() and render_market_data()

# ── Cached reference-data loaders ─────────────────────────────────────────────
# These are small, rarely-changing tables used for dropdowns and option lists.
# Caching them avoids re-querying on every Streamlit rerun.

@st.cache_data(ttl=600)
def _sd_load_currencies():
    with get_db() as conn:
        return pd.read_sql("SELECT Currencies_Id, Currencies_ShortName FROM Currencies ORDER BY Currencies_ShortName ASC", conn)

@st.cache_data(ttl=600)
def _sd_load_institutions():
    with get_db() as conn:
        return pd.read_sql("SELECT Institutions_Id, Institutions_Name FROM Institutions ORDER BY Institutions_Name ASC", conn)

@st.cache_data(ttl=600)
def _sd_load_accounts_list():
    with get_db() as conn:
        return pd.read_sql("SELECT Accounts_Id, Accounts_Name FROM Accounts ORDER BY Accounts_Name ASC", conn)

@st.cache_data(ttl=3600)
def _sd_load_credit_ratings():
    with get_db() as conn:
        return pd.read_sql("SELECT Moodys, S_P, Fitch FROM Credit_Ratings_LT ORDER BY Credit_Ratings_LT_Id ASC", conn)

@st.cache_data(ttl=120)
def _sd_load_category_hierarchy():
    """Full recursive category path list — used for dropdowns and the merge section."""
    with get_db() as conn:
        return pd.read_sql("""
            WITH RECURSIVE CategoryHierarchy AS (
                SELECT Categories_Id, Categories_Name::TEXT AS Full_Path
                FROM   Categories
                WHERE  Categories_Id_Parent IS NULL
                UNION ALL
                SELECT c.Categories_Id, ch.Full_Path || ' : ' || c.Categories_Name
                FROM   Categories c
                JOIN   CategoryHierarchy ch ON c.Categories_Id_Parent = ch.Categories_Id
            )
            SELECT Categories_Id, Full_Path FROM CategoryHierarchy ORDER BY Full_Path
        """, conn)

@st.cache_data(ttl=120)
def _sd_load_cats_with_splits():
    """Categories that have at least one split, with counts — for the merge section."""
    with get_db() as conn:
        return pd.read_sql("""
            WITH RECURSIVE CategoryHierarchy AS (
                SELECT Categories_Id, Categories_Name::TEXT AS Full_Path
                FROM   Categories WHERE Categories_Id_Parent IS NULL
                UNION ALL
                SELECT c.Categories_Id, ch.Full_Path || ' : ' || c.Categories_Name
                FROM   Categories c
                JOIN   CategoryHierarchy ch ON c.Categories_Id_Parent = ch.Categories_Id
            )
            SELECT c.Categories_Id, c.Categories_Type,
                   ch.Full_Path, COUNT(s.Splits_Id) AS splits_count
            FROM   Categories c
            JOIN   CategoryHierarchy ch ON ch.Categories_Id = c.Categories_Id
            JOIN   Splits s ON s.Categories_Id = c.Categories_Id
            GROUP  BY c.Categories_Id, c.Categories_Type, ch.Full_Path
            ORDER  BY c.Categories_Type, ch.Full_Path
        """, conn)

@st.cache_data(ttl=120)
def _sd_load_payees_for_merge():
    """Payees with transaction counts (source) and all payees (target) for the merge section."""
    with get_db() as conn:
        df_with_tx = pd.read_sql("""
            SELECT p.Payees_Id, p.Payees_Name, COUNT(t.Transactions_Id) AS transactions_count
            FROM   Payees p
            JOIN   Transactions t ON t.Payees_Id = p.Payees_Id
            GROUP  BY p.Payees_Id, p.Payees_Name
            ORDER  BY p.Payees_Name
        """, conn)
        df_all = pd.read_sql("SELECT Payees_Id, Payees_Name FROM Payees ORDER BY Payees_Name", conn)
    return df_with_tx, df_all


def render_static_data():
    """Render the Static Data page (Institutions, Categories, Payees, Accounts)."""
    st.title("Static Data")
    t1, t2, t3, t4 = st.tabs(["Institutions", "Categories", "Payees", "Accounts"])

    # ── Shared reference data (all cached) ──────────────────────────────────
    df_curr_list = _sd_load_currencies()
    df_inst_list = _sd_load_institutions()
    df_acc_list  = _sd_load_accounts_list()
    df_cat_list  = _sd_load_category_hierarchy()
    df_ratings   = _sd_load_credit_ratings()

    curr_options   = df_curr_list.set_index('currencies_id')['currencies_shortname'].to_dict()
    inst_options   = df_inst_list.set_index('institutions_id')['institutions_name'].to_dict()
    acc_options    = df_acc_list.set_index('accounts_id')['accounts_name'].to_dict()
    cat_options    = df_cat_list.set_index('categories_id')['full_path'].to_dict()
    moodys_options = dict(zip(df_ratings['moodys'], df_ratings['moodys']))
    s_p_options    = dict(zip(df_ratings['s_p'],    df_ratings['s_p']))
    fitch_options  = dict(zip(df_ratings['fitch'],  df_ratings['fitch']))

    # ── Institutions ──────────────────────────────────────────────────────────
    with t1:
        with get_db() as conn:
            df = pd.read_sql("""
                SELECT Institutions_Id, Institutions_Name, Institutions_Type,
                       BIC_Code, Moodys, S_P, Fitch,
                       Contact, Phone, Email, Website, Notes, embedding
                FROM   Institutions
                ORDER  BY Institutions_Name ASC
            """, conn)
        edited_inst = st.data_editor(
            df,
            num_rows="dynamic",
            key="sd_inst",
            column_config={
                "institutions_id":   None,
                "institutions_name": st.column_config.TextColumn("Institution Name", width="medium", pinned=True),
                "institutions_type": st.column_config.SelectboxColumn(
                    "Institution Type",
                    options=['Bank', 'Credit Union', 'Insurance', 'Pension Fund',
                             'Broker', 'Crypto Exchange', 'Internal', 'Other'],
                ),
                "bic_code":          st.column_config.TextColumn("BIC Code",  width="small"),
                "moodys":            st.column_config.SelectboxColumn(
                    "Moody's", options=list(moodys_options.keys()),
                    format_func=lambda x: moodys_options.get(x, "Unknown"), width="small",
                ),
                "s_p":               st.column_config.SelectboxColumn(
                    "S&P", options=list(s_p_options.keys()),
                    format_func=lambda x: s_p_options.get(x, "Unknown"), width="small",
                ),
                "fitch":             st.column_config.SelectboxColumn(
                    "Fitch", options=list(fitch_options.keys()),
                    format_func=lambda x: fitch_options.get(x, "Unknown"), width="small",
                ),
                "contact":           st.column_config.TextColumn("Contact Info",  width="small"),
                "phone":             st.column_config.TextColumn("Phone",         width="small"),
                "email":             st.column_config.TextColumn("Email Address", width="medium"),
                "website":           st.column_config.TextColumn("Website",       width="small"),
                "embedding":         None,
            },
        )
        if not edited_inst.equals(df):
            save_changes(df, edited_inst, "Institutions", "institutions_id")

    # ── Categories ────────────────────────────────────────────────────────────
    with t2:
        with get_db() as conn:
            df = pd.read_sql("""
                WITH RECURSIVE Descendants AS (
                    SELECT Categories_Id AS root_id, Categories_Id AS child_id
                    FROM   Categories
                    UNION ALL
                    SELECT d.root_id, c.Categories_Id
                    FROM   Categories c
                    JOIN   Descendants d ON c.Categories_Id_Parent = d.child_id
                ),
                SplitCounts AS (
                    SELECT d.root_id AS Categories_Id, COUNT(*) AS cnt
                    FROM   Splits s
                    JOIN   Descendants d ON s.Categories_Id = d.child_id
                    GROUP  BY d.root_id
                )
                SELECT c.*,
                       COALESCE(sc.cnt, 0) AS transactions_count
                FROM   Categories c
                LEFT JOIN SplitCounts sc ON sc.Categories_Id = c.Categories_Id
                WHERE  c.Categories_Name NOT IN (SELECT Accounts_Name FROM Accounts)
                ORDER  BY c.Categories_Id
            """, conn)

        _cat_sort_labels = {
            "categories_id_parent": "Parent Category",
            "categories_name":      "Category Name",
            "categories_type":      "Type",
            "transactions_count":   "Transactions Count",
        }
        _c1, _c2 = st.columns([2, 1])
        with _c1:
            _cat_sort_col = st.selectbox("Sort by", options=list(_cat_sort_labels.keys()),
                format_func=lambda x: _cat_sort_labels[x], index=0, key="sd_cat_sort_col")
        with _c2:
            _cat_sort_asc = st.radio("Direction", options=["Ascending", "Descending"],
                horizontal=True, key="sd_cat_sort_dir") == "Ascending"

        df = df.sort_values(_cat_sort_col, ascending=_cat_sort_asc).reset_index(drop=True)

        edited_cat = st.data_editor(
            df, num_rows="dynamic", key="sd_cat",
            column_config={
                "categories_id": None,
                "categories_name": st.column_config.TextColumn("Category Name", width="medium"),
                "categories_type": st.column_config.SelectboxColumn("Type", options=['Income', 'Expense', 'Transfer', 'Trading', 'Investment', 'Dividend', 'Interest', 'Tax', 'Fee']),
                "categories_id_parent": st.column_config.SelectboxColumn(
                    "Parent Category", options=list(cat_options.keys()),
                    format_func=lambda x: cat_options.get(x, "Unknown"), width="large"
                ),
                "transactions_count": st.column_config.NumberColumn("Transactions Count", width="small", disabled=True),
                "embedding": None,
            }
        )
        _cat_computed = ["transactions_count"]
        df_cat_save     = df.drop(columns=[c for c in _cat_computed if c in df.columns])
        edited_cat_save = edited_cat.drop(columns=[c for c in _cat_computed if c in edited_cat.columns])
        if not edited_cat_save.equals(df_cat_save):
            save_changes(df_cat_save, edited_cat_save, "Categories", "categories_id")
            _sd_load_category_hierarchy.clear()
            _sd_load_cats_with_splits.clear()

        st.divider()
        st.subheader("🔀 Merge Category Splits")
        st.caption(
            "Reassign all splits from one category to another of the same type. "
            "Only categories with the same type are shown as valid targets."
        )

        df_cats_with_splits = _sd_load_cats_with_splits()
        # Build df_all_cats from the cached hierarchy + a quick type lookup
        with get_db() as conn:
            df_cat_types = pd.read_sql(
                "SELECT Categories_Id, Categories_Type FROM Categories", conn
            )
        df_all_cats = (
            _sd_load_category_hierarchy()
            .merge(df_cat_types, on="categories_id")
            .rename(columns={"full_path": "full_path", "categories_type": "categories_type"})
            [["categories_id", "categories_type", "full_path"]]
            .sort_values(["categories_type", "full_path"])
        )

        if df_cats_with_splits.empty:
            st.info("No categories with splits found.")
        else:
            _from_cat_options = {
                int(row.categories_id): (
                    f"[{row.categories_type}]  {row.full_path}"
                    f"  ({int(row.splits_count)} splits)"
                )
                for row in df_cats_with_splits.itertuples()
            }
            _cm1, _cm2 = st.columns(2)
            with _cm1:
                _cat_from_id = st.selectbox("From Category (source)", options=list(_from_cat_options.keys()),
                    format_func=lambda x: _from_cat_options[x], key="sd_merge_cat_from")

            _from_type_rows = df_cats_with_splits.loc[df_cats_with_splits["categories_id"] == _cat_from_id, "categories_type"]
            _from_cat_type  = _from_type_rows.iloc[0] if not _from_type_rows.empty else None
            _to_cat_options = {
                int(row.categories_id): f"{row.full_path}"
                for row in df_all_cats.itertuples()
                if row.categories_type == _from_cat_type and int(row.categories_id) != _cat_from_id
            }
            with _cm2:
                if _to_cat_options:
                    _cat_to_id = st.selectbox(f"To Category (target — {_from_cat_type} only)",
                        options=list(_to_cat_options.keys()),
                        format_func=lambda x: _to_cat_options[x], key="sd_merge_cat_to")
                else:
                    st.warning(f"No other **{_from_cat_type}** categories available as target.")
                    _cat_to_id = None

            with get_db() as conn:
                df_cat_preview = pd.read_sql("""
                    SELECT s.Splits_Id, t.Date, a1.Accounts_Name,
                        COALESCE((SELECT Payees_Name FROM Payees WHERE Payees_Id = t.Payees_Id), 'NO PAYEE') AS Payees_Name,
                        t.Description, s.Amount AS Split_Amount, t.Total_Amount,
                        (SELECT a2.Accounts_Name FROM Accounts a2 WHERE a2.Accounts_Id = t.Accounts_Id_Target) AS Accounts_Name_Target,
                        t.Total_Amount_Target
                    FROM Transactions t
                    JOIN Splits s ON s.Transactions_Id = t.Transactions_Id
                    JOIN Accounts a1 ON a1.Accounts_Id = t.Accounts_Id
                    WHERE s.Categories_Id = %s ORDER BY t.Date DESC
                """, conn, params=(_cat_from_id,))

            _from_label = _from_cat_options.get(_cat_from_id, str(_cat_from_id))
            st.markdown(f"**Splits for:** {_from_label}")
            st.caption("Select specific rows to merge only those splits. Leave all unselected to merge all.")
            _cat_sel_event = st.dataframe(
                df_cat_preview.drop(columns=["splits_id"]),
                width="stretch", hide_index=True,
                selection_mode="multi-row", on_select="rerun",
                key="sd_cat_preview_sel",
                column_config={
                    "date": st.column_config.DateColumn("Date", width="small"),
                    "accounts_name": st.column_config.TextColumn("Account", width="medium"),
                    "payees_name": st.column_config.TextColumn("Payee", width="medium"),
                    "description": st.column_config.TextColumn("Description", width="medium"),
                    "split_amount": st.column_config.NumberColumn("Split Amount", format="%,.2f", width="small"),
                    "total_amount": st.column_config.NumberColumn("Tx Amount", format="%,.2f", width="small"),
                    "accounts_name_target": st.column_config.TextColumn("Target Account", width="medium"),
                    "total_amount_target": st.column_config.NumberColumn("Target Amount", format="%,.2f", width="small"),
                },
            )
            copy_df_button(df_cat_preview, key="sd_dl_cat_preview")

            _cat_sel_rows = [i for i in (_cat_sel_event.selection.rows if _cat_sel_event else []) if i is not None and i < len(df_cat_preview)]
            _cat_selected_ids = (
                df_cat_preview.iloc[_cat_sel_rows]["splits_id"].tolist()
                if _cat_sel_rows else []
            )
            _cat_all_selected = len(_cat_selected_ids) == 0  # empty selection = all
            _cat_merge_count = len(df_cat_preview) if _cat_all_selected else len(_cat_selected_ids)

            # Show any deferred post-merge message (persisted across the rerun)
            if 'sd_merge_post_msg' in st.session_state:
                _pm_level, _pm_text = st.session_state.pop('sd_merge_post_msg')
                if _pm_level == 'warning':
                    st.warning(_pm_text)
                else:
                    st.success(_pm_text)

            if _cat_to_id:
                _from_cat_name = _from_cat_options.get(_cat_from_id, "")
                _to_cat_name   = _to_cat_options.get(_cat_to_id, "")

                with get_db() as conn:
                    _child_count_pre = pd.read_sql(
                        "SELECT COUNT(*) AS cnt FROM Categories WHERE Categories_Id_Parent = %s",
                        conn, params=(_cat_from_id,)
                    ).iloc[0]["cnt"]

                _delete_disabled = not _cat_all_selected or _child_count_pre > 0
                _delete_help = (
                    "After all splits are moved, permanently delete the source category. "
                    "Only available when merging all splits and the category has no sub-categories. "
                    "Any associated budget entries and payee-default references are cleaned up automatically."
                )
                _also_delete = st.checkbox(
                    "Also delete source category after merge",
                    key="sd_merge_cat_also_delete",
                    disabled=_delete_disabled,
                    help=_delete_help,
                )
                if not _cat_all_selected:
                    st.caption(f"ℹ️ {len(_cat_selected_ids)} of {len(df_cat_preview)} split(s) selected — delete option disabled.")
                elif _child_count_pre > 0:
                    st.caption(f"ℹ️ Source category has {_child_count_pre} sub-categor{'y' if _child_count_pre == 1 else 'ies'} — delete option disabled.")

                if st.button("▶️ Merge Category Splits", type="primary", key="sd_merge_cat_btn"):
                    st.session_state['sd_merge_cat_confirm'] = True

                if st.session_state.get('sd_merge_cat_confirm'):
                    _delete_note = " and permanently **delete** the source category" if _also_delete else ""
                    _scope_note = "all" if _cat_all_selected else f"{_cat_merge_count} selected"
                    st.warning(
                        f"⚠️ This will move **{_scope_note} split(s)** from "
                        f"**{_from_cat_name}** → **{_to_cat_name}**{_delete_note}. "
                        "This cannot be undone."
                    )
                    _cn, _cy, _ = st.columns([1, 1, 3])
                    with _cn:
                        if st.button("✖ Cancel", key="sd_merge_cat_cancel", width="stretch"):
                            st.session_state['sd_merge_cat_confirm'] = False
                            st.rerun()
                    with _cy:
                        if st.button("✔ Yes, merge", type="primary", key="sd_merge_cat_yes", width="stretch"):
                            with st.spinner(f"Moving {_cat_merge_count} splits…"):
                                try:
                                    # 1. Reassign selected or all splits
                                    with get_db() as conn:
                                        cur = conn.cursor()
                                        if _cat_all_selected:
                                            cur.execute(
                                                "UPDATE Splits SET Categories_Id = %s WHERE Categories_Id = %s",
                                                (_cat_to_id, _cat_from_id),
                                            )
                                        else:
                                            _placeholders = ",".join(["%s"] * len(_cat_selected_ids))
                                            cur.execute(
                                                f"UPDATE Splits SET Categories_Id = %s WHERE Splits_Id IN ({_placeholders})",
                                                [_cat_to_id] + _cat_selected_ids,
                                            )

                                    st.session_state['sd_merge_cat_confirm'] = False
                                    _sd_load_cats_with_splits.clear()
                                    st.toast(
                                        f"✅ {_cat_merge_count} split(s) moved from "
                                        f"**{_from_cat_name}** to **{_to_cat_name}**.",
                                        icon="✅",
                                    )

                                    # 2. Optionally delete the source category
                                    if _also_delete:
                                        with get_db() as conn:
                                            cur = conn.cursor()
                                            cur.execute(
                                                "SELECT COUNT(*) FROM Categories WHERE Categories_Id_Parent = %s",
                                                (_cat_from_id,),
                                            )
                                            _child_count = cur.fetchone()[0]

                                        if _child_count > 0:
                                            st.session_state['sd_merge_post_msg'] = (
                                                'warning',
                                                f"⚠️ **{_from_cat_name}** was NOT deleted: "
                                                f"it has {_child_count} sub-categor"
                                                f"{'y' if _child_count == 1 else 'ies'}. "
                                                "Delete or reassign them first, then remove this category manually.",
                                            )
                                        else:
                                            # Remove budget entries (best-effort — table may not exist yet)
                                            try:
                                                with get_db() as conn:
                                                    cur = conn.cursor()
                                                    cur.execute(
                                                        "DELETE FROM Annual_Budgets WHERE Categories_Id = %s",
                                                        (_cat_from_id,),
                                                    )
                                            except Exception:
                                                pass

                                            # Clear payee default-category reference, then delete
                                            try:
                                                with get_db() as conn:
                                                    cur = conn.cursor()
                                                    cur.execute(
                                                        "UPDATE Payees SET categories_id_default = NULL "
                                                        "WHERE categories_id_default = %s",
                                                        (_cat_from_id,),
                                                    )
                                                    cur.execute(
                                                        "DELETE FROM Categories WHERE Categories_Id = %s",
                                                        (_cat_from_id,),
                                                    )
                                                st.toast(
                                                    f"🗑️ Category '{_from_cat_name}' deleted.",
                                                    icon="🗑️",
                                                )
                                            except Exception as _del_err:
                                                st.session_state['sd_merge_post_msg'] = (
                                                    'warning',
                                                    f"⚠️ Splits merged but could not delete "
                                                    f"**{_from_cat_name}**: {_del_err}",
                                                )

                                    st.rerun()
                                except Exception as e:
                                    st.error(f"❌ Error during merge: {e}")

    # ── Payees ────────────────────────────────────────────────────────────────
    with t3:
        with get_db() as conn:
            df = pd.read_sql("""
                SELECT p.*,
                       COALESCE(t.cnt, 0) AS transactions_count
                FROM   Payees p
                LEFT JOIN (
                    SELECT Payees_Id, COUNT(*) AS cnt
                    FROM   Transactions
                    GROUP  BY Payees_Id
                ) t ON t.Payees_Id = p.Payees_Id
                ORDER  BY p.Payees_Name ASC
            """, conn)

        _payee_sort_labels = {
            "payees_name":            "Payee Name",
            "categories_id_default":  "Default Category",
            "transactions_count":     "Transactions Count",
        }
        _c1, _c2 = st.columns([2, 1])
        with _c1:
            _payee_sort_col = st.selectbox("Sort by", options=list(_payee_sort_labels.keys()),
                format_func=lambda x: _payee_sort_labels[x], index=0, key="sd_payee_sort_col")
        with _c2:
            _payee_sort_asc = st.radio("Direction", options=["Ascending", "Descending"],
                horizontal=True, key="sd_payee_sort_dir") == "Ascending"

        df = df.sort_values(_payee_sort_col, ascending=_payee_sort_asc).reset_index(drop=True)

        edited_payee = st.data_editor(
            df, num_rows="dynamic", key="sd_pay",
            column_config={
                "payees_id": None,
                "payees_name": st.column_config.TextColumn("Payee Name", width="medium"),
                "categories_id_default": st.column_config.SelectboxColumn(
                    "Default Category", options=list(cat_options.keys()),
                    format_func=lambda x: cat_options.get(x, "Unknown"), width="medium"
                ),
                "notes": st.column_config.TextColumn("Notes", width="medium"),
                "transactions_count": st.column_config.NumberColumn("Transactions Count", width="small", disabled=True),
                "embedding": None,
            }
        )
        _payee_computed = ["transactions_count"]
        df_payee_save     = df.drop(columns=[c for c in _payee_computed if c in df.columns])
        edited_payee_save = edited_payee.drop(columns=[c for c in _payee_computed if c in edited_payee.columns])
        if not edited_payee_save.equals(df_payee_save):
            save_changes(df_payee_save, edited_payee_save, "Payees", "payees_id")
            _sd_load_payees_for_merge.clear()

        if st.button("🔄 Update Default Category based on usage, in case not defined", key="sd_payee_update_default"):
            with st.spinner("Processing..."):
                update_payee_default_category()
                st.success("Updated successfully!")
                st.balloons()
                st.rerun()

        st.divider()
        st.subheader("🔀 Merge Payee Transactions")
        st.caption(
            "Reassign all transactions from one payee to another. "
            "This is useful for merging duplicates or correcting misspellings."
        )

        df_payees_with_transactions, df_all_payees = _sd_load_payees_for_merge()

        if df_payees_with_transactions.empty:
            st.info("No payees with transactions found.")
        else:
            _from_payee_options = {
                int(row.payees_id): f"{row.payees_name} ({row.transactions_count} transactions)"
                for row in df_payees_with_transactions.itertuples()
            }
            _cm1, _cm2 = st.columns(2)
            with _cm1:
                _payee_from_id = st.selectbox("From Payee (source)", options=list(_from_payee_options.keys()),
                    format_func=lambda x: _from_payee_options[x], key="sd_merge_payee_from")
            with _cm2:
                _to_payee_options = {
                    int(row.payees_id): f"{row.payees_name}"
                    for row in df_all_payees.itertuples()
                    if int(row.payees_id) != _payee_from_id
                }
                if _to_payee_options:
                    _payee_to_id = st.selectbox("To Payee (target)", options=list(_to_payee_options.keys()),
                        format_func=lambda x: _to_payee_options[x], key="sd_merge_payee_to")
                else:
                    st.warning("No other payees available as target.")
                    _payee_to_id = None

            with get_db() as conn:
                df_payee_preview = pd.read_sql("""
                    SELECT t.Transactions_Id, t.Date, a1.Accounts_Name, p.Payees_Name, t.Description, t.Total_Amount,
                        (SELECT a2.Accounts_Name FROM Accounts a2 WHERE a2.Accounts_Id = t.Accounts_Id_Target) AS Accounts_Name_Target,
                        t.Total_Amount_Target
                    FROM Transactions t JOIN Payees p ON p.Payees_Id = t.Payees_Id
                    JOIN Accounts a1 ON a1.Accounts_Id = t.Accounts_Id
                    WHERE p.Payees_Id = %s ORDER BY t.Date DESC
                """, conn, params=(_payee_from_id,))

            _from_label = _from_payee_options.get(_payee_from_id, str(_payee_from_id))
            st.markdown(f"**Transactions for:** {_from_label}")
            st.caption("Select specific rows to merge only those transactions. Leave all unselected to merge all.")
            _payee_sel_event = st.dataframe(
                df_payee_preview.drop(columns=["transactions_id"]),
                width="stretch", hide_index=True,
                selection_mode="multi-row", on_select="rerun",
                key="sd_payee_preview_sel",
                column_config={
                    "date": st.column_config.DateColumn("Date", width="small"),
                    "accounts_name": st.column_config.TextColumn("Account", width="medium"),
                    "payees_name": st.column_config.TextColumn("Payee", width="medium"),
                    "description": st.column_config.TextColumn("Description", width="medium"),
                    "total_amount": st.column_config.NumberColumn("Amount", format="%,.2f", width="small"),
                    "accounts_name_target": st.column_config.TextColumn("Target Account", width="medium"),
                    "total_amount_target": st.column_config.NumberColumn("Target Amount", format="%,.2f", width="small"),
                },
            )
            copy_df_button(df_payee_preview, key="sd_dl_payee_preview")

            _payee_sel_rows = [i for i in (_payee_sel_event.selection.rows if _payee_sel_event else []) if i is not None and i < len(df_payee_preview)]
            _payee_selected_ids = (
                df_payee_preview.iloc[_payee_sel_rows]["transactions_id"].tolist()
                if _payee_sel_rows else []
            )
            _payee_all_selected = len(_payee_selected_ids) == 0  # empty selection = all
            _payee_merge_count = len(df_payee_preview) if _payee_all_selected else len(_payee_selected_ids)

            if _payee_to_id:
                _from_payee_name = _from_payee_options.get(_payee_from_id, "")
                _to_payee_name   = _to_payee_options.get(_payee_to_id, "")

                _also_delete_payee = st.checkbox(
                    "Also delete source payee after merge",
                    key="sd_merge_payee_also_delete",
                    disabled=not _payee_all_selected,
                    help="After all transactions are moved, permanently delete the source payee. Only available when merging all transactions.",
                )
                if not _payee_all_selected:
                    st.caption(f"ℹ️ {len(_payee_selected_ids)} of {len(df_payee_preview)} transaction(s) selected — delete option disabled.")

                if st.session_state.get('sd_merge_payee_post_msg'):
                    _pm_level, _pm_text = st.session_state.pop('sd_merge_payee_post_msg')
                    if _pm_level == 'warning':
                        st.warning(_pm_text)
                    else:
                        st.success(_pm_text)

                if st.button("▶️ Merge Payee Transactions", type="primary", key="sd_merge_payee_btn"):
                    st.session_state['sd_merge_payee_confirm'] = True

                if st.session_state.get('sd_merge_payee_confirm'):
                    _delete_note = " and permanently **delete** the source payee" if _also_delete_payee else ""
                    _scope_note = "all" if _payee_all_selected else f"{_payee_merge_count} selected"
                    st.warning(
                        f"⚠️ This will move **{_scope_note} transaction(s)** from "
                        f"**{_from_payee_name}** → **{_to_payee_name}**{_delete_note}. "
                        f"This cannot be undone."
                    )
                    _cn, _cy, _ = st.columns([1, 1, 3])
                    with _cn:
                        if st.button("✖ Cancel", key="sd_merge_payee_cancel", width="stretch"):
                            st.session_state['sd_merge_payee_confirm'] = False
                            st.rerun()
                    with _cy:
                        if st.button("✔ Yes, merge", type="primary", key="sd_merge_payee_yes", width="stretch"):
                            with st.spinner(f"Moving {_payee_merge_count} transactions…"):
                                try:
                                    with get_db() as conn:
                                        cur = conn.cursor()
                                        cur.execute("ALTER TABLE Transactions DISABLE TRIGGER trg_update_balance;")
                                        conn.commit()
                                        if _payee_all_selected:
                                            cur.execute("UPDATE Transactions SET Payees_Id = %s WHERE Payees_Id = %s", (_payee_to_id, _payee_from_id))
                                        else:
                                            _placeholders = ",".join(["%s"] * len(_payee_selected_ids))
                                            cur.execute(
                                                f"UPDATE Transactions SET Payees_Id = %s WHERE Transactions_Id IN ({_placeholders})",
                                                [_payee_to_id] + _payee_selected_ids,
                                            )
                                        conn.commit()
                                        cur.execute("ALTER TABLE Transactions ENABLE TRIGGER trg_update_balance;")
                                        conn.commit()
                                    st.session_state['sd_merge_payee_confirm'] = False
                                    _sd_load_payees_for_merge.clear()
                                    st.toast(
                                        f"✅ {_payee_merge_count} transaction(s) moved from "
                                        f"**{_from_payee_name}** to **{_to_payee_name}**.",
                                        icon="✅",
                                    )
                                    if _also_delete_payee:
                                        try:
                                            with get_db() as conn:
                                                cur = conn.cursor()
                                                cur.execute("DELETE FROM Payees WHERE Payees_Id = %s", (_payee_from_id,))
                                                conn.commit()
                                            st.session_state['sd_merge_payee_post_msg'] = (
                                                'success',
                                                f"🗑️ **{_from_payee_name}** has been deleted.",
                                            )
                                        except Exception as _del_err:
                                            st.session_state['sd_merge_payee_post_msg'] = (
                                                'warning',
                                                f"⚠️ Transactions merged but could not delete "
                                                f"**{_from_payee_name}**: {_del_err}",
                                            )
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"❌ Error during merge: {e}")

    # ── Accounts ──────────────────────────────────────────────────────────────
    with t4:
        with get_db() as conn:
            df = pd.read_sql("SELECT * FROM Accounts ORDER BY Accounts_Name ASC", conn)

        column_order = [
            "accounts_id", "accounts_name", "accounts_type", "currencies_id",
            "institutions_id", "is_active", "iban", "credit_limit",
            "accounts_id_linked", "accounts_balance", "embedding"
        ]
        df = df[column_order]

        def _get_status_icon(q):
            if q == 0:   return "🔵"
            elif q < 0:  return "🔴"
            return "🟢"

        df.insert(0, "Balance", df['accounts_balance'].apply(_get_status_icon))

        edited_acc = st.data_editor(
            df, num_rows="dynamic", key="sd_acc", width="content",
            column_config={
                "Balance":           st.column_config.TextColumn("Status", width="auto", disabled=True, pinned=True),
                "accounts_id":       None,
                "accounts_name":     st.column_config.TextColumn("Account Name", width="auto", pinned=True),
                "accounts_type":     st.column_config.SelectboxColumn("Type",
                    options=['Cash','Checking','Savings','Credit Card','Brokerage','Pension','Other Investment','Margin','Loan','Real Estate','Vehicle','Asset','Liability','Other'], width="auto"),
                "currencies_id":     st.column_config.SelectboxColumn("Currency", options=list(curr_options.keys()),
                    format_func=lambda x: curr_options.get(x, "Unknown"), width="auto"),
                "institutions_id":   st.column_config.SelectboxColumn("Institution", options=list(inst_options.keys()),
                    format_func=lambda x: inst_options.get(x, "Unknown"), width="auto"),
                "is_active":         st.column_config.CheckboxColumn("Active", width="auto"),
                "iban":              st.column_config.TextColumn("IBAN", width="medium"),
                "credit_limit":      st.column_config.NumberColumn("Credit Limit", width="auto", format="%,.2f"),
                "accounts_id_linked": st.column_config.SelectboxColumn("Linked Account",
                    options={None: "None", **acc_options},
                    format_func=lambda x: acc_options.get(x, "Unknown") if x is not None else "None", width="auto"),
                "accounts_balance":  st.column_config.NumberColumn("Balance", width="auto", format="%,.2f"),
                "embedding":         None,
            }
        )
        if not edited_acc.equals(df):
            save_df = edited_acc.drop(columns=["Balance"])
            save_changes(df.drop(columns=["Balance"]), save_df, "Accounts", "accounts_id")
            _sd_load_accounts_list.clear()

