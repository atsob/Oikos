import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
import calendar
from database.connection import get_db
from database.crud import save_changes, execute_db_save, update_accounts_balances, update_holdings, update_investment_balances, update_pension_balances, insert_staking_reinvest
from ui.components import copy_df_button

@st.fragment
def _render_transaction_table(acc_id, payee_options, acc_options, cat_options, tab_key, acc_balance=0.0):
    """Render a filtered transaction register for one account.

    Filters reduce the dataset client-side — all matching rows are loaded into
    memory (no DB pagination). This keeps inline editing simple: edits are
    committed with an explicit Save button so typing in a cell does not
    trigger a rerun.

    tab_key must be unique per call ('bank' / 'cash') to avoid widget key
    collisions when both tabs exist on the same page.

    acc_balance: the account's current balance, used to anchor the per-row
    running balance computation (balance after each transaction).
    """
    from datetime import date, timedelta

    _sk = f"reg_{acc_id}_{tab_key}"  # session-state key prefix

    # ── Filter bar ────────────────────────────────────────────────────────────
    today = date.today()
    default_from = today - timedelta(days=30)

    f1, f2, f3, f4 = st.columns([3, 1.5, 1.5, 1.5])

    with f1:
        _period = st.radio(
            "Period",
            options=["30d", "3mo", "6mo", "1y", "All", "Custom"],
            index=0,
            horizontal=True,
            key=f"{_sk}_period",
            label_visibility="collapsed"
        )

    _period_map = {
        "30d": today - timedelta(days=30),
        "3mo": today - timedelta(days=90),
        "6mo": today - timedelta(days=180),
        "1y":  today - timedelta(days=365),
        "All": date(1900, 1, 1),
    }

    if _period == "Custom":
        with f2:
            _from_date = st.date_input("From", value=default_from, min_value=date(1900, 1, 1), key=f"{_sk}_from")
        with f3:
            _to_date = st.date_input("To", value=today, min_value=date(1900, 1, 1), key=f"{_sk}_to")
        _show_future = False  # custom range already explicit
    else:
        _from_date = _period_map.get(_period, default_from)
        _to_date = today

    with f4:
        _status = st.selectbox(
            "Status",
            options=["All", "Cleared", "Uncleared"],
            key=f"{_sk}_status",
            label_visibility="collapsed"
        )

    # Show future transactions checkbox (not shown for Custom — range is explicit)
    if _period != "Custom":
        _show_future = st.checkbox(
            "Include future transactions",
            value=True,
            key=f"{_sk}_future"
        )
        if _show_future:
            _to_date = date(2099, 12, 31)

    # ── Query — all matching rows, no LIMIT/OFFSET ────────────────────────────
    _params = [acc_id, _from_date, _to_date]
    _where = "WHERE Accounts_Id = %s AND Date BETWEEN %s AND %s"
    if _status == "Cleared":
        _where += " AND Cleared = TRUE"
    elif _status == "Uncleared":
        _where += " AND Cleared = FALSE"

    # Running balance: window function over ALL account transactions so the
    # balance is correctly anchored to acc_balance regardless of date filter.
    # Balance after row i (date-ascending order) =
    #   acc_balance − (grand_total − cumulative_total_up_to_i)
    _rb_sql = f"""
        WITH all_txns AS (
            SELECT
                Transactions_Id,
                SUM(Total_Amount) OVER (
                    ORDER BY Date ASC, Transactions_Id ASC
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS cumulative_total,
                SUM(Total_Amount) OVER () AS grand_total
            FROM Transactions
            WHERE Accounts_Id = %s
        )
        SELECT t.*,
               (%s::numeric - at.grand_total + at.cumulative_total) AS running_balance
        FROM Transactions t
        JOIN all_txns at ON at.Transactions_Id = t.Transactions_Id
        {_where}
        ORDER BY t.Date DESC, t.Transactions_Id DESC
    """
    # CTE needs acc_id first; balance scalar second; then the _where params.
    _rb_params = [acc_id, float(acc_balance)] + _params
    with get_db() as conn:
        df = pd.read_sql(_rb_sql, conn, params=_rb_params)

    # Place running_balance immediately before reconciliation_session_id so
    # it appears in a logical position in the grid (after the editable fields,
    # before the administrative columns at the end of the table).
    if 'running_balance' in df.columns and 'reconciliation_session_id' in df.columns:
        _cols = df.columns.tolist()
        _cols.remove('running_balance')
        _cols.insert(_cols.index('reconciliation_session_id'), 'running_balance')
        df = df[_cols]

    # ── Stat cards ────────────────────────────────────────────────────────────
    s1, s2 = st.columns(2)
    with s1:
        st.metric("Transactions", f"{len(df):,}")
    with s2:
        st.metric("Period total", f"{float(df['total_amount'].sum()):,.2f}" if not df.empty else "0.00")

    if len(df) > 500:
        st.warning(f"⚠️ {len(df):,} transactions — consider narrowing the date range for better performance.")

    # ── Sort controls ─────────────────────────────────────────────────────────
    _sc1, _sc2 = st.columns([3, 1])
    with _sc1:
        _sort_col = st.selectbox(
            "Sort by",
            options=["Date", "Transaction ID", "Payee", "Amount", "Description", "Target Account"],
            index=0,
            key=f"{_sk}_sort_col",
            label_visibility="collapsed",
        )
    with _sc2:
        # Transaction ID naturally reads ascending (oldest→newest); every other
        # column defaults to descending (newest/largest first).
        _dir_default = 0 if _sort_col == "Transaction ID" else 1
        _sort_dir = st.radio(
            "Direction",
            options=["ASC", "DESC"],
            index=_dir_default,
            horizontal=True,
            key=f"{_sk}_sort_dir",
            label_visibility="collapsed",
        )

    _sort_asc = _sort_dir == "ASC"
    if _sort_col == "Transaction ID":
        df = df.sort_values("transactions_id", ascending=_sort_asc, kind="stable").reset_index(drop=True)
    elif _sort_col == "Payee":
        df["_sk"] = df["payees_id"].map(payee_options).fillna("")
        df = df.sort_values("_sk", ascending=_sort_asc, kind="stable").drop(columns=["_sk"]).reset_index(drop=True)
    elif _sort_col == "Target Account":
        df["_sk"] = df["accounts_id_target"].map(acc_options).fillna("")
        df = df.sort_values("_sk", ascending=_sort_asc, kind="stable").drop(columns=["_sk"]).reset_index(drop=True)
    elif _sort_col == "Amount":
        df = df.sort_values("total_amount", ascending=_sort_asc, kind="stable").reset_index(drop=True)
    elif _sort_col == "Description":
        df = df.sort_values("description", ascending=_sort_asc, kind="stable").reset_index(drop=True)
    else:  # Date
        df = df.sort_values("date", ascending=_sort_asc, kind="stable").reset_index(drop=True)

    # ── Data editor ───────────────────────────────────────────────────────────
    df.insert(0, "_selected", False)

    # Stable editor key: changes only when filter or sort changes, not on every rerun.
    _filter_sig = (_period, _status, acc_id, _sort_col, _sort_dir,
                   str(_from_date) if _period == "Custom" else "",
                   str(_to_date)   if _period == "Custom" else "")
    unique_key = f"set_reg_{acc_id}_{tab_key}_{hash(str(_filter_sig)) % 10**8}"
    _orig_key  = f"{unique_key}_orig"

    # Store the original df the first time this filter+sort combination is seen.
    # This is the baseline for change detection — NOT the re-fetched df,
    # which would always equal edited_reg after a rerun and hide pending edits.
    if _orig_key not in st.session_state:
        st.session_state[_orig_key] = df.copy()
    df_original = st.session_state[_orig_key]

    col_config = {
        "_selected": st.column_config.CheckboxColumn(
            "☑", width="small",
            help="Tick to select for moving to another account"
        ),
        "transactions_id": st.column_config.NumberColumn(
            "ID", width="small", disabled=True
        ),
        "accounts_id": None,
        "date": st.column_config.DateColumn(
            "Date", width="small", format="DD/MM/YYYY"
        ),
        "payees_id": st.column_config.SelectboxColumn(
            "Payee", width="medium",
            options=list(payee_options.keys()),
            format_func=lambda x: payee_options.get(x, "Unknown")
        ),
        "description": st.column_config.TextColumn("Description"),
        "total_amount": st.column_config.NumberColumn(
            "Amount", width="small", format="%,.2f"
        ),
        "cleared": st.column_config.CheckboxColumn("Cleared", width="small"),
        "accounts_id_target": st.column_config.SelectboxColumn(
            "Target Account",
            options=list(acc_options.keys()),
            format_func=lambda x: acc_options.get(x, "Unknown")
        ),
        "total_amount_target": st.column_config.NumberColumn(
            "Target Amount", width="small", format="%,.2f"
        ),
        "running_balance": st.column_config.NumberColumn(
            "Balance", width="small", format="%,.2f", disabled=True,
            help="Account balance after this transaction (anchored to current balance)"
        ),
        "transfers_id": None,
        "embedding": None,
    }

    edited_reg = st.data_editor(
        df_original,   # always render from the stored original so edits persist
        num_rows="dynamic",
        key=unique_key,
        width="stretch",
        column_config=col_config,
    )
    _copy_txns = df_original.drop(columns=["_selected", "accounts_id", "transfers_id", "embedding"], errors="ignore").copy()
    _copy_txns["payees_id"] = _copy_txns["payees_id"].map(payee_options).fillna("")
    _copy_txns["accounts_id_target"] = _copy_txns["accounts_id_target"].map(acc_options).fillna("")
    _copy_txns = _copy_txns.rename(columns={"payees_id": "Payee", "accounts_id_target": "Target Account"})
    copy_df_button(_copy_txns, key=f"dl_reg_txns_{unique_key}")

    # ── Change detection & Save ───────────────────────────────────────────────
    # running_balance is a computed column — never saved to the DB.
    _orig_for_cmp   = df_original.drop(columns=["_selected", "running_balance"], errors="ignore")
    edited_for_save = edited_reg.drop(columns=["_selected", "running_balance"], errors="ignore")

    # Align dtypes before comparing — pd.read_sql and st.data_editor can produce
    # different dtypes for the same values (e.g. int64 vs object for nullable cols),
    # which makes .equals() return False even when no values changed.
    try:
        edited_aligned = edited_for_save.astype(_orig_for_cmp.dtypes.to_dict())
    except Exception:
        edited_aligned = edited_for_save
    _has_changes = not edited_aligned.equals(_orig_for_cmp)

    if _has_changes:
        if st.button("💾 Save Changes", key=f"save_reg_{acc_id}_{tab_key}", type="primary"):
            # Clear caches BEFORE calling execute_db_save — it calls st.rerun()
            # internally on success, so any code after it never executes.
            # Clear ALL period _orig_key entries for this account+tab so switching
            # periods after a save always shows fresh data, not a stale snapshot.
            _prefix = f"set_reg_{acc_id}_{tab_key}_"
            for _k in list(st.session_state.keys()):
                if _k.startswith(_prefix) and _k.endswith("_orig"):
                    st.session_state.pop(_k, None)
            for _k in ["df_accs", "register_df"]:
                st.session_state.pop(_k, None)
            # Call execute_db_save directly — save_changes() renders its own
            # st.button internally which conflicts with ours.
            with get_db() as _conn_save:
                execute_db_save(
                    _orig_for_cmp, edited_for_save,
                    "Transactions", "transactions_id",
                    current_acc_id=acc_id, conn=_conn_save
                )


    _selected_ids = edited_reg.loc[
        edited_reg.get("_selected", False) == True, "transactions_id"
    ].dropna().astype(int).tolist()

    _all_ids = df["transactions_id"].dropna().astype(int).tolist()

    # ── Splits section ────────────────────────────────────────────────────────
    st.write("---")
    st.subheader("🔍 Split Analysis")

    available_ids = df["transactions_id"].tolist()
    _splits_key_sel  = f"tx_selector_{acc_id}_{tab_key}"
    _splits_key_show = f"show_splits_{acc_id}_{tab_key}"
    _splits_key_cur  = f"current_tx_{acc_id}_{tab_key}"

    # If exactly one row is ticked, auto-select it for splits.
    # We must set the selectbox widget's own session_state key (not a proxy key)
    # so Streamlit picks up the value when rendering the selectbox.
    if len(_selected_ids) == 1:
        _auto_id = _selected_ids[0]
        if _auto_id in available_ids and st.session_state.get(_splits_key_cur) != _auto_id:
            st.session_state[_splits_key_cur]  = _auto_id
            st.session_state[_splits_key_show] = True
            # Override the selectbox widget state directly so index= is respected
            st.session_state[_splits_key_sel]  = _auto_id

    # Derive the selectbox index from session state (works even after auto-select)
    _cur_split_id = st.session_state.get(_splits_key_cur)
    default_ix = (available_ids.index(_cur_split_id) + 1
                  if _cur_split_id in available_ids else 0)

    selected_tx_id = st.selectbox(
        "Select Transaction ID for Splits:",
        [None] + available_ids,
        index=default_ix,
        key=_splits_key_sel
    )

    if selected_tx_id != st.session_state.get(_splits_key_cur):
        st.session_state[_splits_key_cur] = selected_tx_id
        st.session_state[_splits_key_show] = selected_tx_id is not None

    if st.session_state.get(_splits_key_show) and st.session_state.get(_splits_key_cur):
        tx_id = int(st.session_state[_splits_key_cur])
        st.write("---")
        st.write(f"### 📑 Edit Splits for ID: {tx_id}")

        with get_db() as conn:
            df_splits = pd.read_sql(
                "SELECT * FROM Splits WHERE Transactions_Id = %s",
                conn, params=(tx_id,)
            )

        editor_key = f"splits_ed_{tx_id}_{tab_key}"
        edited_splits = st.data_editor(
            df_splits,
            num_rows="dynamic",
            key=editor_key,
            width="stretch",
            column_config={
                "splits_id": st.column_config.NumberColumn("Split ID", disabled=True),
                "transactions_id": None,
                "categories_id": st.column_config.SelectboxColumn(
                    "Category", options=list(cat_options.keys()),
                    format_func=lambda x: cat_options.get(x, "Unknown"), width="large"
                ),
                "amount": st.column_config.NumberColumn("Amount", format="%,.2f", width="medium"),
                "memo": st.column_config.TextColumn("Memo", width="large"),
                "embedding": None,
            }
        )
        _copy_splits = df_splits.drop(columns=["splits_id", "transactions_id", "embedding"], errors="ignore").copy()
        _copy_splits["categories_id"] = _copy_splits["categories_id"].map(cat_options).fillna("")
        _copy_splits = _copy_splits.rename(columns={"categories_id": "Category"})
        copy_df_button(_copy_splits, key=f"dl_reg_splits_{tx_id}_{tab_key}")

        if not edited_splits.equals(df_splits):
            df_to_save = edited_splits.copy()
            df_to_save["transactions_id"] = tx_id
            with get_db() as _conn_sp:
                execute_db_save(
                    df_splits, df_to_save, "Splits", "splits_id", conn=_conn_sp
                )
            # execute_db_save shows success toast and calls st.rerun() internally

        total_split = float(edited_splits["amount"].sum())
        with get_db() as _conn_chk:
            _cur_chk = _conn_chk.cursor()
            _cur_chk.execute(
                "SELECT total_amount FROM Transactions WHERE Transactions_Id = %s", (tx_id,)
            )
            res = _cur_chk.fetchone()
        if res:
            expected_total = float(res[0])
            if abs(total_split - expected_total) > 0.01:
                st.warning(
                    f"⚠️ Sum of splits ({total_split:,.2f}) ≠ "
                    f"Transaction Total ({expected_total:,.2f})"
                )
            else:
                st.info(f"✅ Splits balance correctly ({total_split:,.2f})")


    # ── Move transactions ─────────────────────────────────────────────────────
    def _clear_register_caches():
        """Evict the data-editor snapshot and legacy caches for this account+tab."""
        _pfx = f"set_reg_{acc_id}_{tab_key}_"
        for _k in list(st.session_state.keys()):
            if _k.startswith(_pfx) and _k.endswith("_orig"):
                st.session_state.pop(_k, None)
        for _k in ["df_accs", "register_df"]:
            st.session_state.pop(_k, None)

    st.write("---")
    with st.expander("🔀 Move Transactions to Another Account"):
        st.caption("Tick ☑ on rows above, pick a target account, then click Move.")
        _move_targets = {k: v for k, v in acc_options.items() if k != acc_id}
        _m1, _m2, _m3 = st.columns([3, 1, 1])
        with _m1:
            _move_target_id = st.selectbox(
                "Move to account",
                options=list(_move_targets.keys()),
                format_func=lambda x: _move_targets[x],
                key=f"move_target_{acc_id}_{tab_key}"
            )
        with _m2:
            st.write("")
            st.write("")
            _move_btn = st.button(
                f"▶️ Move {len(_selected_ids)} selected",
                key=f"move_btn_{acc_id}_{tab_key}",
                type="primary",
                disabled=len(_selected_ids) == 0
            )
        with _m3:
            st.write("")
            st.write("")
            _move_all_btn = st.button(
                f"⏩ Move All ({len(_all_ids)})",
                key=f"move_all_btn_{acc_id}_{tab_key}",
                disabled=len(_all_ids) == 0,
            )

        # ── Confirmation: Move Selected ───────────────────────────────────────
        _move_sel_ck = f"confirm_move_sel_{acc_id}_{tab_key}"
        if _move_btn:
            st.session_state[_move_sel_ck] = True

        if st.session_state.get(_move_sel_ck) and _selected_ids:
            _target_name = _move_targets.get(_move_target_id, "selected account")
            st.warning(
                f"⚠️ Move **{len(_selected_ids)} selected transaction(s)** to **{_target_name}**? "
                "This cannot be undone automatically."
            )
            _ca, _cb, _ = st.columns([1, 1, 3])
            with _ca:
                if _ca.button("✖ Cancel", key=f"confirm_move_sel_cancel_{acc_id}_{tab_key}", width="stretch"):
                    st.session_state[_move_sel_ck] = False
                    st.rerun()
            with _cb:
                if _cb.button("✔ Yes, move", type="primary", key=f"confirm_move_sel_yes_{acc_id}_{tab_key}", width="stretch"):
                    st.session_state[_move_sel_ck] = False
                    _move_btn = True   # fall through to existing move logic below
                else:
                    _move_btn = False  # keep armed but don't execute yet
        else:
            _move_btn = False

        # ── Confirmation: Move All ────────────────────────────────────────────
        _confirm_key = f"confirm_move_all_{acc_id}_{tab_key}"
        if _move_all_btn:
            st.session_state[_confirm_key] = True

        if st.session_state.get(_confirm_key):
            _target_name = _move_targets.get(_move_target_id, "selected account")
            st.warning(
                f"⚠️ You are about to move **all {len(_all_ids)} transaction(s)** from this account "
                f"to **{_target_name}**. This cannot be undone automatically. Are you sure?"
            )
            _ca, _cb, _ = st.columns([1, 1, 3])
            _confirm_cancel = _ca.button("✖ Cancel",       key=f"confirm_cancel_{acc_id}_{tab_key}", width="stretch")
            _confirm_ok     = _cb.button("✔ Yes, move all", key=f"confirm_ok_{acc_id}_{tab_key}",    width="stretch", type="primary")

            if _confirm_cancel:
                st.session_state.pop(_confirm_key, None)
                st.rerun()

            if _confirm_ok:
                st.session_state.pop(_confirm_key, None)
                _move_btn = False          # don't double-execute
                _ids_to_move = _all_ids
                with get_db() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "UPDATE Transactions SET Accounts_Id = %s WHERE Transactions_Id = ANY(%s)",
                        (_move_target_id, _ids_to_move)
                    )
                    cur.execute(
                        """
                        SELECT DISTINCT t2.Transactions_Id
                        FROM Transactions t1
                        JOIN Transactions t2 ON t2.Transfers_Id = t1.Transfers_Id
                        WHERE t1.Transactions_Id = ANY(%s)
                          AND t1.Transfers_Id IS NOT NULL
                          AND t2.Transactions_Id != ALL(%s)
                          AND t2.Accounts_Id NOT IN (%s, %s)
                          AND t2.Accounts_Id_Target = %s
                        """,
                        (_ids_to_move, _ids_to_move, acc_id, _move_target_id, acc_id)
                    )
                    counterpart_ids = [r[0] for r in cur.fetchall()]
                    if counterpart_ids:
                        cur.execute(
                            "UPDATE Transactions SET Accounts_Id_Target = %s WHERE Transactions_Id = ANY(%s)",
                            (_move_target_id, counterpart_ids)
                        )
                update_accounts_balances(acc_id)
                update_accounts_balances(_move_target_id)
                st.success(
                    f"✅ Moved all {len(_ids_to_move)} transaction(s) to "
                    f"**{_target_name}**."
                )
                _clear_register_caches()
                st.rerun()

        if _move_btn and _selected_ids:
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE Transactions SET Accounts_Id = %s WHERE Transactions_Id = ANY(%s)",
                    (_move_target_id, _selected_ids)
                )
                # For transfer transactions: find counterpart rows in other accounts
                # that reference acc_id as target, and remap to _move_target_id
                cur.execute(
                    """
                    SELECT DISTINCT t2.Transactions_Id
                    FROM Transactions t1
                    JOIN Transactions t2 ON t2.Transfers_Id = t1.Transfers_Id
                    WHERE t1.Transactions_Id = ANY(%s)
                      AND t1.Transfers_Id IS NOT NULL
                      AND t2.Transactions_Id != ALL(%s)
                      AND t2.Accounts_Id NOT IN (%s, %s)
                      AND t2.Accounts_Id_Target = %s
                    """,
                    (_selected_ids, _selected_ids, acc_id, _move_target_id, acc_id)
                )
                counterpart_ids = [r[0] for r in cur.fetchall()]
                if counterpart_ids:
                    cur.execute(
                        "UPDATE Transactions SET Accounts_Id_Target = %s WHERE Transactions_Id = ANY(%s)",
                        (_move_target_id, counterpart_ids)
                    )
            update_accounts_balances(acc_id)
            update_accounts_balances(_move_target_id)
            st.success(
                f"✅ Moved {len(_selected_ids)} transaction(s) to "
                f"**{_move_targets[_move_target_id]}**."
            )
            _clear_register_caches()
            st.rerun()


@st.dialog("🔍 Transaction Search Results", width="large")
def _search_dialog(query: str, payee_options: dict, acc_options: dict) -> None:
    """Full-text search across payee, description, memo, category, and account name.

    Results are displayed in an editable table — Payee, Description, Date, Amount,
    Cleared, and Target Account can all be changed inline and saved back to the DB.
    """
    from database.connection import get_connection as _gc

    term = f"%{query.strip()}%"

    conn = _gc()
    # Fetch the raw Transactions columns needed for editing, plus display extras.
    df_res = pd.read_sql("""
        WITH RECURSIVE
        CategoryHierarchy AS (
            SELECT Categories_Id,
                   Categories_Name::TEXT AS Full_Path
              FROM Categories
             WHERE Categories_Id_Parent IS NULL
            UNION ALL
            SELECT c.Categories_Id,
                   ch.Full_Path || ' : ' || c.Categories_Name
              FROM Categories c
              JOIN CategoryHierarchy ch ON c.Categories_Id_Parent = ch.Categories_Id
        ),
        SplitSummary AS (
            SELECT s.Transactions_Id,
                   STRING_AGG(DISTINCT COALESCE(ch.Full_Path, c.Categories_Name), ', ') AS categories,
                   STRING_AGG(DISTINCT s.Memo, ', ')                                     AS memos
              FROM Splits s
              LEFT JOIN Categories       c  ON c.Categories_Id  = s.Categories_Id
              LEFT JOIN CategoryHierarchy ch ON ch.Categories_Id = s.Categories_Id
             GROUP BY s.Transactions_Id
        )
        SELECT DISTINCT
               t.Transactions_Id     AS transactions_id,
               t.Date                AS date,
               a.Accounts_Name       AS account,
               t.Payees_Id           AS payees_id,
               t.Description         AS description,
               t.Total_Amount        AS total_amount,
               t.Cleared             AS cleared,
               t.Accounts_Id_Target  AS accounts_id_target,
               t.Total_Amount_Target AS total_amount_target,
               t.Transfers_Id        AS transfers_id,
               ss.categories,
               ss.memos
          FROM Transactions t
          LEFT JOIN Accounts     a  ON a.Accounts_Id     = t.Accounts_Id
          LEFT JOIN Payees       p  ON p.Payees_Id       = t.Payees_Id
          LEFT JOIN SplitSummary ss ON ss.Transactions_Id = t.Transactions_Id
          -- joins below are only for the WHERE filter
          LEFT JOIN Splits       s  ON s.Transactions_Id  = t.Transactions_Id
          LEFT JOIN Categories   c  ON c.Categories_Id   = s.Categories_Id
         WHERE t.Description     ILIKE %(term)s
            OR p.Payees_Name     ILIKE %(term)s
            OR a.Accounts_Name   ILIKE %(term)s
            OR s.Memo            ILIKE %(term)s
            OR c.Categories_Name ILIKE %(term)s
         ORDER BY t.Date DESC, t.Transactions_Id DESC
         LIMIT 500
    """, conn, params={"term": term})
    conn.close()

    st.caption(f'Results for **"{query}"** — {len(df_res):,} transaction(s) found (max 500)')

    if df_res.empty:
        st.info("No transactions matched your search.")
        return

    df_res["total_amount"] = df_res["total_amount"].astype(float)

    # _orig_key tracks pre-edit state only within a single dialog session.
    # It is keyed by query + the earliest transactions_id in the result so that
    # a fresh DB fetch (different rows or different values) always wins over cache.
    _freshness = str(df_res["transactions_id"].min()) + str(len(df_res))
    _orig_key = f"_search_orig_{query}_{_freshness}"
    if _orig_key not in st.session_state:
        # Clear any stale snapshot for this query before storing the fresh one
        for _k in [k for k in st.session_state if k.startswith(f"_search_orig_{query}")]:
            del st.session_state[_k]
        st.session_state[_orig_key] = df_res.copy()
    df_original = st.session_state[_orig_key]

    edited = st.data_editor(
        df_res,
        hide_index=True,
        width='stretch',
        key=f"_search_editor_{query}",
        column_config={
            "transactions_id":    st.column_config.NumberColumn("ID",             width="small",  disabled=True),
            "date":               st.column_config.DateColumn("Date",             width="small",  format="DD/MM/YYYY"),
            "account":            st.column_config.TextColumn("Account",                          disabled=True),
            "payees_id":          st.column_config.SelectboxColumn(
                                      "Payee",
                                      options=list(payee_options.keys()),
                                      format_func=lambda x: payee_options.get(x, "—"),
                                  ),
            "description":        st.column_config.TextColumn("Description"),
            "total_amount":       st.column_config.NumberColumn("Amount",         width="small",  format="%,.2f"),
            "cleared":            st.column_config.CheckboxColumn("Clr",          width="small"),
            "accounts_id_target": st.column_config.SelectboxColumn(
                                      "Target Account",
                                      options=[None] + list(acc_options.keys()),
                                      format_func=lambda x: acc_options.get(x, "—") if x else "—",
                                  ),
            "total_amount_target": st.column_config.NumberColumn("Target Amt",   width="small",  format="%,.2f"),
            "transfers_id":       None,
            "categories":         st.column_config.TextColumn("Categories",                       disabled=True),
            "memos":              st.column_config.TextColumn("Memos",                            disabled=True),
        },
        column_order=[
            "transactions_id", "date", "account", "payees_id", "description",
            "total_amount", "cleared", "accounts_id_target", "total_amount_target",
            "categories", "memos",
        ],
    )

    total = df_original["total_amount"].sum()
    st.caption(f"Sum of matched amounts: **{total:,.2f}**")

    # Change detection — exclude read-only display columns
    _cmp_cols = ["transactions_id", "date", "payees_id", "description",
                 "total_amount", "cleared", "accounts_id_target", "total_amount_target"]
    try:
        _has_changes = not edited[_cmp_cols].astype(
            df_original[_cmp_cols].dtypes.to_dict()
        ).equals(df_original[_cmp_cols])
    except Exception:
        _has_changes = not edited[_cmp_cols].equals(df_original[_cmp_cols])

    if _has_changes:
        if st.button("💾 Save Changes", type="primary", key=f"_search_save_{query}"):
            _editable = ["date", "payees_id", "description",
                         "total_amount", "cleared", "accounts_id_target", "total_amount_target"]
            from database.crud import _safe_val
            try:
                with get_db() as _conn_save:
                    _cur = _conn_save.cursor()
                    _n = 0
                    for _, edit_row in edited.iterrows():
                        tx_id = int(edit_row["transactions_id"])
                        orig_rows = df_original[df_original["transactions_id"] == tx_id]
                        if orig_rows.empty:
                            continue
                        orig_row = orig_rows.iloc[0]
                        # Build SET clause only for columns that actually changed
                        _sets, _vals = [], []
                        for col in _editable:
                            new_v = _safe_val(edit_row[col])
                            old_v = _safe_val(orig_row[col])
                            if new_v != old_v:
                                _sets.append(f"{col} = %s")
                                _vals.append(new_v)
                        if not _sets:
                            continue
                        _vals.append(tx_id)
                        _cur.execute(
                            f"UPDATE Transactions SET {', '.join(_sets)} WHERE Transactions_Id = %s",
                            tuple(_vals),
                        )
                        _n += 1
                # Clear caches after successful commit (get_db commits on exit)
                st.session_state.pop(_orig_key, None)
                st.session_state.pop(f"_search_editor_{query}", None)
                for _k in ["df_accs", "register_df"]:
                    st.session_state.pop(_k, None)
                st.success(f"Saved {_n} transaction(s).")
                st.rerun()
            except Exception as _e:
                st.error(f"Save failed: {_e}")


@st.dialog("💰 Accounts Balance Overview", width="large")
def _multi_account_dialog() -> None:
    """Compact balance overview for all active bank/cash/investment accounts.

    Shows every account in a single table with current balance and a
    30/60/90-day income, expenses, and net cash-flow summary.
    No transaction lists — just the numbers you need at a glance.
    """
    from database.connection import get_connection as _gc

    period_days = st.pills(
        "Period",
        [30, 60, 90, 180],
        format_func=lambda x: f"{x}d",
        default=30,
        key="mac_period",
    )

    conn = _gc()
    df_accs = pd.read_sql("""
        SELECT a.Accounts_Id       AS accounts_id,
               a.Accounts_Name     AS name,
               a.Accounts_Type     AS type,
               a.Accounts_Balance  AS balance,
               c.Currencies_ShortName AS currency
        FROM Accounts a
        JOIN Currencies c ON c.Currencies_Id = a.Currencies_Id
        WHERE a.Is_Active = TRUE
          AND a.Accounts_Type IN ('Checking','Savings','Cash','Credit Card')
        ORDER BY a.Accounts_Type, a.Accounts_Name
    """, conn)

    if df_accs.empty:
        conn.close()
        st.info("No active bank/cash accounts found.")
        return

    # Fetch period totals for all accounts in one query
    ids_sql = ", ".join(str(int(i)) for i in df_accs['accounts_id'].tolist())
    df_flow = pd.read_sql(f"""
        SELECT
            t.Accounts_Id                                          AS accounts_id,
            SUM(CASE WHEN t.Total_Amount > 0 THEN  t.Total_Amount ELSE 0 END) AS income,
            SUM(CASE WHEN t.Total_Amount < 0 THEN -t.Total_Amount ELSE 0 END) AS expenses
        FROM Transactions t
        WHERE t.Accounts_Id IN ({ids_sql})
          AND t.Date >= CURRENT_DATE - %(days)s * INTERVAL '1 day'
          AND t.Transfers_Id IS NULL
          AND t.Is_Draft = FALSE
        GROUP BY t.Accounts_Id
    """, conn, params={"days": period_days})
    conn.close()

    df = df_accs.merge(df_flow, on='accounts_id', how='left')
    df['income']   = df['income'].fillna(0).astype(float)
    df['expenses'] = df['expenses'].fillna(0).astype(float)
    df['net']      = df['income'] - df['expenses']
    df['balance']  = df['balance'].astype(float)

    # Totals row
    total_row = pd.DataFrame([{
        'name':     '─── TOTAL ───',
        'type':     '',
        'currency': 'EUR',
        'balance':  df['balance'].sum(),
        'income':   df['income'].sum(),
        'expenses': df['expenses'].sum(),
        'net':      df['net'].sum(),
    }])
    df_display = pd.concat(
        [df[['name','type','currency','balance','income','expenses','net']], total_row],
        ignore_index=True,
    )

    st.dataframe(
        df_display,
        hide_index=True,
        width='stretch',
        column_config={
            "name":     st.column_config.TextColumn("Account"),
            "type":     st.column_config.TextColumn("Type",     width="small"),
            "currency": st.column_config.TextColumn("CCY",      width="small"),
            "balance":  st.column_config.NumberColumn("Balance",          format="%.2f"),
            "income":   st.column_config.NumberColumn(f"Income ({period_days}d)",   format="%.2f"),
            "expenses": st.column_config.NumberColumn(f"Expenses ({period_days}d)", format="%.2f"),
            "net":      st.column_config.NumberColumn(f"Net ({period_days}d)",      format="%.2f"),
        },
        column_order=["name","type","currency","balance","income","expenses","net"],
    )
    st.caption(f"Transfers excluded · Period: last {period_days} days · Balances in account currency")


def get_or_create_payee_id(cur, payee_name, categories_id_default=None):
    """Lookup or insert a payee. Accepts a cursor; commit is handled by the caller."""
    if not payee_name:
        return None
    payee_name = payee_name.strip()
    if not payee_name:
        return None

    cur.execute("SELECT payees_id FROM Payees WHERE Payees_Name = %s", (payee_name,))
    row = cur.fetchone()
    if row:
        return row[0]

    if categories_id_default is not None:
        cur.execute("INSERT INTO Payees (Payees_Name, Categories_Id_Default) VALUES (%s, %s) RETURNING Payees_Id", (payee_name, categories_id_default))
    else:
        cur.execute("INSERT INTO Payees (Payees_Name) VALUES (%s) RETURNING Payees_Id", (payee_name,))
    return cur.fetchone()[0]


def get_latest_fx_rate(cur, currencies_id_1, currencies_id_2, as_of_date=None):
    """Get the latest FX rate from base to target currency as of the given date."""
    currencies_id_1 = int(currencies_id_1)
    currencies_id_2 = int(currencies_id_2)
    if currencies_id_1 == currencies_id_2:
        return 1.0

    if as_of_date is not None:
        if isinstance(as_of_date, datetime):
            as_of_date = as_of_date.date()
        elif isinstance(as_of_date, pd.Timestamp):
            as_of_date = as_of_date.date()
        elif isinstance(as_of_date, str):
            as_of_date = datetime.fromisoformat(as_of_date).date()

    if as_of_date:
        cur.execute("""
            SELECT fx_rate FROM Historical_FX 
            WHERE currencies_id_1 = %s AND currencies_id_2 = %s AND date <= %s
            ORDER BY date DESC LIMIT 1
        """, (currencies_id_1, currencies_id_2, as_of_date))
    else:
        cur.execute("""
            SELECT fx_rate FROM Historical_FX 
            WHERE currencies_id_1 = %s AND currencies_id_2 = %s 
            ORDER BY date DESC LIMIT 1
        """, (currencies_id_1, currencies_id_2))
    row = cur.fetchone()
    if row:
        return float(row[0])
    
    # Try reverse rate
    if as_of_date:
        cur.execute("""
            SELECT 1.0 / fx_rate FROM Historical_FX 
            WHERE currencies_id_1 = %s AND currencies_id_2 = %s AND date <= %s
            ORDER BY date DESC LIMIT 1
        """, (currencies_id_2, currencies_id_1, as_of_date))
    else:
        cur.execute("""
            SELECT 1.0 / fx_rate FROM Historical_FX 
            WHERE currencies_id_1 = %s AND currencies_id_2 = %s 
            ORDER BY date DESC LIMIT 1
        """, (currencies_id_2, currencies_id_1))
    row = cur.fetchone()
    if row:
        return float(row[0])
    
    return 1.0  # Default if no rate found


def add_months(start_date, months):
    month = start_date.month - 1 + months
    year = start_date.year + month // 12
    month = month % 12 + 1
    day = min(start_date.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def advance_date(base_date, periodicity):
    if periodicity == "Weekly":
        return base_date + timedelta(weeks=1)
    if periodicity == "Monthly":
        return add_months(base_date, 1)
    if periodicity == "Bi-Monthly":
        return add_months(base_date, 2)
    if periodicity == "Quarterly":
        return add_months(base_date, 3)
    if periodicity == "Semi-Annually":
        return add_months(base_date, 6)
    if periodicity == "Annually":
        return add_months(base_date, 12)
    return base_date


def build_recurrence_schedule(start_date, next_date, periodicity, installments=None, end_date=None):
    dates = [start_date]
    if not periodicity:
        return dates

    next_date = next_date or advance_date(start_date, periodicity)
    if next_date <= start_date:
        next_date = advance_date(start_date, periodicity)

    current = next_date
    while True:
        if installments is not None:
            if len(dates) >= installments:
                break
        elif end_date is not None:
            if current > end_date:
                break
        else:
            break

        dates.append(current)
        current = advance_date(current, periodicity)

    return dates


def insert_bank_transaction(cur, accounts_id, tx_date, payees_id, description, total_amount, cleared=True, accounts_id_target=None, total_amount_target=None, transfers_id=None):
    if transfers_id is not None:
        cur.execute(
            """
            INSERT INTO Transactions (Accounts_Id, Date, Payees_Id, Description, Total_Amount, Cleared, Accounts_Id_Target, Total_Amount_Target, Transfers_Id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING Transactions_Id
            """,
            (accounts_id, tx_date, payees_id, description, total_amount, cleared, accounts_id_target, total_amount_target, transfers_id)
        )
    else:
        cur.execute(
            """
            INSERT INTO Transactions (Accounts_Id, Date, Payees_Id, Description, Total_Amount, Cleared, Accounts_Id_Target, Total_Amount_Target)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING Transactions_Id
            """,
            (accounts_id, tx_date, payees_id, description, total_amount, cleared, accounts_id_target, total_amount_target)
        )
    return cur.fetchone()[0]


def insert_transaction_splits(cur, bank_transaction_id, splits):
    for split in splits:
        cur.execute(
            """
            INSERT INTO Splits (Transactions_Id, Categories_Id, Amount, Memo)
            VALUES (%s, %s, %s, %s)
            """,
            (bank_transaction_id, split.get('categories_id'), split['amount'], split.get('memo'))
        )


def reset_transaction_form_state():
    if 'reset_counter' not in st.session_state:
        st.session_state.reset_counter = 0
    st.session_state.reset_counter += 1


def _render_new_investment_form(acc_id, acc_type, df_accs, df_securities, get_db_fn,
                                update_holdings_fn, update_investment_balances_fn):
    """Form for entering a new investment transaction with optional linked-account transfer.

    Mirrors the qif_importer X-type logic:
      - Investments row always goes into the Investments table.
      - For actions that move cash to/from a linked bank/cash account (Buy→cash out,
        Sell/Dividend/IntInc/RtrnCap→cash in, MiscExp→cash out) a Transactions row is
        inserted in the linked account and its ID stored in Transactions_Id.
      - Brokerage accounts never get a mirror Transactions row — the Investments entry
        is the authoritative record on the investment side.
      - Securities are filtered to match the investment account currency.
      - The default linked account comes from Accounts.Accounts_Id_Linked.
      - When currencies differ, target amount is auto-calculated from Historical_FX.
    """

    # ── Action metadata ────────────────────────────────────────────────────────
    ALL_ACTIONS = [
        'Buy', 'Sell', 'Dividend', 'Reinvest', 'IntInc', 'RtrnCap',
        'MiscExp', 'MiscInc', 'ShrIn', 'ShrOut', 'CashIn', 'CashOut',
        'Split', 'Vest', 'Grant', 'Exercise', 'Expire',
    ]
    CASH_OUT_ACTIONS = {'Buy', 'MiscExp', 'CashOut'}
    CASH_IN_ACTIONS  = {'Sell', 'Dividend', 'IntInc', 'RtrnCap', 'MiscInc', 'CashIn'}
    LINKED_CAPABLE   = CASH_OUT_ACTIONS | CASH_IN_ACTIONS
    NO_SECURITY_ACTIONS = {'CashIn', 'CashOut'}
    QTY_REQUIRED_ACTIONS = {'Buy', 'Sell', 'ShrIn', 'ShrOut', 'Reinvest', 'Split', 'Vest', 'Grant', 'Exercise', 'Expire'}

    # ── Account metadata ───────────────────────────────────────────────────────
    acc_row = df_accs[df_accs['accounts_id'] == acc_id].iloc[0]
    acc_currencies_id = int(acc_row['currencies_id'])
    acc_linked_default = acc_row.get('accounts_id_linked')
    if pd.isna(acc_linked_default):
        acc_linked_default = None
    else:
        acc_linked_default = int(acc_linked_default)

    # ── All securities — show currency code alongside name ────────────────────
    INV_TYPES = {'Brokerage', 'Pension', 'Other Investment', 'Margin'}
    _cur_map = (df_securities.set_index('securities_id')['currencies_shortname'].to_dict()
                if 'currencies_shortname' in df_securities.columns else {})
    _acc_cur_name = _cur_map.get(acc_currencies_id, '')  # best-effort from sec table

    sec_options = {
        int(row['securities_id']): (
            f"{row['securities_name']} ({row['currencies_shortname']})"
            if row.get('currencies_shortname') else row['securities_name']
        )
        for _, row in df_securities.iterrows()
    }
    # Plain-name variant (used for payee creation and linked account descriptions)
    sec_names_plain = {int(row['securities_id']): row['securities_name']
                       for _, row in df_securities.iterrows()}

    # ── Cash/bank accounts eligible as transfer targets ───────────────────────
    cash_acc_options = {
        row['accounts_id']: f"{row['accounts_name']} ({row['accounts_balance']:,.2f})"
        for _, row in df_accs.iterrows()
        if row['accounts_type'] not in INV_TYPES and row['accounts_id'] != acc_id
    }
    cash_acc_ids = list(cash_acc_options.keys())

    st.info(
        "Fill in the transaction details below. "
        "For **Buy / Sell / Dividend / IntInc / RtrnCap / MiscExp** you can optionally link "
        "a cash account — this mirrors the Quicken BuyX/SellX/DivX behaviour."
    )

    rc = st.session_state.get('inv_form_reset', 0)

    # ── Linked-account selectbox OUTSIDE the form so FX can be pre-calculated ─
    # (same pattern as the payee selector in the bank transaction form)
    enable_linked = st.checkbox(
        "Create linked cash account transfer (BuyX / SellX / DivX)",
        value=(acc_linked_default is not None),  # default ON when account has a configured linked account
        key=f"inv_linked_chk_{rc}",
    )
    linked_acc_id = None
    linked_acc_curr = None
    fx_rate = 1.0
    if enable_linked:
        if not cash_acc_ids:
            st.warning("No eligible cash/bank accounts found.")
            enable_linked = False
        else:
            # Default to the account's configured linked account if available
            default_idx = 0
            if acc_linked_default and acc_linked_default in cash_acc_ids:
                default_idx = cash_acc_ids.index(acc_linked_default)
            linked_acc_id = st.selectbox(
                "Linked Cash Account",
                options=cash_acc_ids,
                format_func=lambda x: cash_acc_options.get(x, "Unknown"),
                index=default_idx,
                key=f"inv_linked_acc_{rc}",
            )
            linked_row = df_accs[df_accs['accounts_id'] == linked_acc_id]
            if not linked_row.empty:
                linked_acc_curr = int(linked_row.iloc[0]['currencies_id'])
            # Pre-calculate FX rate with today's date (user can override target amount)
            if linked_acc_curr and linked_acc_curr != acc_currencies_id:
                with get_db_fn() as _conn_fx:
                    _cur_fx = _conn_fx.cursor()
                    fx_rate = get_latest_fx_rate(_cur_fx, acc_currencies_id, linked_acc_curr)

    # ── Input widgets — outside st.form so Enter key never triggers a save ───
    col1, col2 = st.columns(2)

    with col1:
        inv_date = st.date_input("Date", value=date.today(), min_value=date(1900, 1, 1), key=f"inv_date_{rc}")

        inv_action = st.selectbox(
            "Action", ALL_ACTIONS,
            key=f"inv_action_{rc}",
            help="Choose the investment action type."
        )

        sec_ids = [None] + list(sec_options.keys())
        inv_security = st.selectbox(
            "Security",
            options=sec_ids,
            format_func=lambda x: sec_options.get(x, "— none —"),
            key=f"inv_sec_{rc}",
            help="All securities shown. Currency code is displayed in brackets.",
        )

        _INSTRUMENT_TYPES = [
            "", "Stock", "ETF", "Bond", "CFD", "CEF", "CFDOnETF", "CFDOnStock",
            "CFDOnIndex", "CFDOnFutures", "CFDOnFund", "Fund", "Option", "FX Spot", "Other",
        ]
        inv_instrument_type = st.selectbox(
            "Instrument Type (optional)",
            options=_INSTRUMENT_TYPES,
            index=0,
            key=f"inv_instr_{rc}",
            help=(
                "Capture the specific traded instrument, independent of the security master. "
                "Useful when the same underlying is traded as both a regular security and a CFD "
                "(e.g. Saxo: CFDOnETF, CFDOnStock, CFDOnIndex, FX Spot). Leave blank for standard trades."
            ),
        )

    with col2:
        inv_qty = st.number_input(
            "Quantity", min_value=0.0, value=0.0, step=0.0001, format="%.8f",
            key=f"inv_qty_{rc}",
        )
        inv_price = st.number_input(
            "Price Per Share", min_value=0.0, value=0.0, step=0.00000001, format="%.8f",
            key=f"inv_price_{rc}",
        )
        inv_comm = st.number_input(
            "Commission (0 = auto-derive)",
            min_value=0.0, value=0.0, step=0.00000001, format="%.8f",
            key=f"inv_comm_{rc}",
            help=(
                "Leave 0 to auto-derive from Qty × Price vs Total (Sec Currency): "
                "Buy → Total(Sec) − Qty×Price; Sell → Qty×Price − Total(Sec)."
            ),
        )
        inv_memo = st.text_input("Memo / Description", key=f"inv_memo_{rc}")

    # ── Amounts (always shown: Total Sec · FX Rate · Total Acc) ───────────────
    st.divider()
    st.markdown("**Amounts**")

    _sec_currencies_id_new = None
    _sec_cur_name_new = ""
    if inv_security:
        _sr = df_securities[df_securities['securities_id'] == inv_security]
        if not _sr.empty:
            _sec_currencies_id_new = int(_sr.iloc[0]['currencies_id'])
            _sec_cur_name_new = str(_sr.iloc[0].get('currencies_shortname', ''))
    _is_xc_new = (_sec_currencies_id_new is not None and _sec_currencies_id_new != acc_currencies_id)

    # Account currency display name
    _acc_cur_name_new = ""
    try:
        with get_db_fn() as _cn_an:
            _can = _cn_an.cursor()
            _can.execute("SELECT Currencies_ShortName FROM Currencies WHERE Currencies_Id = %s",
                         (acc_currencies_id,))
            _rn = _can.fetchone()
            if _rn:
                _acc_cur_name_new = _rn[0]
    except Exception:
        pass

    # FX rate default: Historical_FX for cross-currency, 1.0 for same currency
    _def_fx_new = 1.0
    if _is_xc_new and _sec_currencies_id_new:
        try:
            with get_db_fn() as _conn_xfx_n:
                _def_fx_new = float(
                    get_latest_fx_rate(_conn_xfx_n.cursor(), _sec_currencies_id_new,
                                       acc_currencies_id, inv_date) or 1.0
                )
        except Exception:
            _def_fx_new = 1.0

    _sec_lbl_n = f"Total ({_sec_cur_name_new})" if _sec_cur_name_new else "Total (Sec Currency)"
    _acc_lbl_n = f"Total ({_acc_cur_name_new})" if _acc_cur_name_new else "Total (Acc Currency)"

    _na1, _na2, _na3 = st.columns(3)
    with _na1:
        inv_total_sec_cur = st.number_input(
            _sec_lbl_n,
            value=0.0, step=0.01, format="%.8f",
            key=f"inv_total_sec_{rc}",
            help="Total in the security's native currency. Leave 0 to auto-calculate from Qty × Price ± Commission.",
        )
    with _na2:
        inv_fx_rate = st.number_input(
            "FX Rate (1 Sec → Acc)",
            value=_def_fx_new,
            min_value=0.000001, step=0.00000001, format="%.8f",
            key=f"inv_fx_{rc}",
            help=(
                "1 unit of security currency = this many account-currency units. "
                + ("Rate from Historical_FX for the transaction date."
                   if _is_xc_new else
                   "Same currency as account — defaults to 1.0.")
            ),
        )
    with _na3:
        inv_total_acc_cur = st.number_input(
            _acc_lbl_n,
            value=0.0, step=0.01, format="%.8f",
            key=f"inv_total_acc_{rc}",
            help="Total in the account's currency. Leave 0 to auto-calculate from Total(Sec) × FX Rate.",
        )

    # ── Live auto-calc hint ────────────────────────────────────────────────
    _qxp_n = inv_qty * inv_price if inv_qty > 0 and inv_price > 0 else 0.0
    _hint_parts_n: list[str] = []
    _h_sec_n = inv_total_sec_cur
    if inv_comm == 0 and inv_total_sec_cur > 0 and _qxp_n > 0:
        if inv_action == 'Buy':
            _h_comm_n = max(0.0, inv_total_sec_cur - _qxp_n)
        elif inv_action == 'Sell':
            _h_comm_n = max(0.0, _qxp_n - inv_total_sec_cur)
        else:
            _h_comm_n = 0.0
        if _h_comm_n > 0:
            _hint_parts_n.append(f"Commission → **{_h_comm_n:,.8f}**")
    if inv_total_sec_cur == 0 and _qxp_n > 0:
        _c_for_hint = inv_comm if inv_comm > 0 else 0.0
        if inv_action == 'Buy':
            _h_sec_n = _qxp_n + _c_for_hint
        elif inv_action == 'Sell':
            _h_sec_n = max(0.0, _qxp_n - _c_for_hint)
        else:
            _h_sec_n = _qxp_n
        if _h_sec_n > 0:
            _hint_parts_n.append(f"{_sec_lbl_n} → **{_h_sec_n:,.8f}**")
    if inv_total_acc_cur == 0 and _h_sec_n > 0 and _is_xc_new:
        _h_acc_n = _h_sec_n * float(inv_fx_rate)
        _hint_parts_n.append(f"{_acc_lbl_n} → **{_h_acc_n:,.8f}**")
    if not _is_xc_new:
        st.caption(
            f"Security and account share the same currency ({_acc_cur_name_new or '?'}) — "
            "FX Rate is 1.0; Total(Sec) = Total(Acc)."
        )
    elif _hint_parts_n:
        st.caption("💡 On save: " + " · ".join(_hint_parts_n))
    else:
        st.caption(f"FX rate as of **{inv_date}** from Historical_FX — edit if the booked rate differs.")

    # ── Linked account cross-currency target amount ────────────────────────
    linked_target_amount = None
    if enable_linked and linked_acc_id and linked_acc_curr and linked_acc_curr != acc_currencies_id:
        st.divider()
        st.caption(
            f"Linked account is in a different currency. "
            f"Target amount pre-calculated using FX rate **{fx_rate:.6f}** (editable)."
        )
        linked_target_amount = st.number_input(
            "Target Amount in Linked Account Currency",
            value=0.0,
            step=0.01, format="%.4f",
            key=f"inv_linked_target_{rc}",
            help="Auto-filled after you set the total amount; override if needed.",
        )
    elif enable_linked and linked_acc_id:
        st.caption("Linked account uses the same currency — no conversion needed.")

    if enable_linked and linked_acc_id and inv_action not in LINKED_CAPABLE:
        st.warning(f"**{inv_action}** does not support a linked cash transfer.")

    submitted = st.button("💾 Save Investment Transaction", key=f"inv_submit_{rc}")

    # ── Validation & save ──────────────────────────────────────────────────────
    if submitted:
        errors = []
        if inv_action not in NO_SECURITY_ACTIONS and not inv_security:
            errors.append("Please select a Security (required for this action).")
        if inv_action in QTY_REQUIRED_ACTIONS and inv_qty <= 0:
            errors.append(f"Quantity must be > 0 for action '{inv_action}'.")
        if enable_linked and not linked_acc_id:
            errors.append("Please select a linked cash account.")

        if errors:
            for e in errors:
                st.error(e)
        else:
            # ── Resolve amounts: commission → total_sec → fx → total_acc ─────────
            _qxp_save = inv_qty * inv_price if inv_qty > 0 and inv_price > 0 else 0.0

            # Commission: user value OR derived from Total(Sec) and Qty×Price
            if inv_comm > 0:
                calc_commission = inv_comm
            elif inv_total_sec_cur > 0 and _qxp_save > 0:
                if inv_action == 'Buy':
                    calc_commission = max(0.0, inv_total_sec_cur - _qxp_save)
                elif inv_action == 'Sell':
                    calc_commission = max(0.0, _qxp_save - inv_total_sec_cur)
                else:
                    calc_commission = 0.0
            else:
                calc_commission = 0.0

            # Total (Security Currency): user value OR derived from Qty×Price ± Commission
            if inv_total_sec_cur != 0:
                calc_total_sec = inv_total_sec_cur
            elif _qxp_save > 0:
                if inv_action == 'Buy':
                    calc_total_sec = _qxp_save + calc_commission
                elif inv_action == 'Sell':
                    calc_total_sec = max(0.0, _qxp_save - calc_commission)
                else:
                    calc_total_sec = _qxp_save
            else:
                # Fallback: derive from account-currency total when no price data
                calc_total_sec = abs(inv_total_acc_cur) if inv_total_acc_cur != 0 else 0.0

            # FX Rate (always stored; 1.0 for same-currency)
            calc_fx = float(inv_fx_rate) if inv_fx_rate and float(inv_fx_rate) > 0 else 1.0

            # Total (Account Currency): user value OR Total(Sec) × FX Rate
            if inv_total_acc_cur != 0:
                calc_total_acc = inv_total_acc_cur
            elif calc_total_sec != 0:
                calc_total_acc = round(calc_total_sec * calc_fx, 8)
            else:
                calc_total_acc = 0.0

            try:
                with get_db_fn() as conn:
                    cur = conn.cursor()

                    cur.execute(
                        """
                        INSERT INTO Investments
                            (Accounts_Id, Securities_Id, Date, Action,
                             Quantity, Price_Per_Share, Commission,
                             Total_Amount_AccCur, Total_Amount_SecCur, FX_Rate,
                             Description, Instrument_Type)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING Investments_Id
                        """,
                        (
                            acc_id,
                            inv_security,
                            inv_date,
                            inv_action,
                            inv_qty          if inv_qty   > 0 else None,
                            inv_price        if inv_price > 0 else None,
                            calc_commission  if calc_commission > 0 else None,
                            calc_total_acc   if calc_total_acc != 0 else None,
                            calc_total_sec   if calc_total_sec != 0 else None,
                            calc_fx,
                            inv_memo or None,
                            inv_instrument_type or None,
                        ),
                    )
                    investments_id = cur.fetchone()[0]

                    # 2. Linked cash account transfer (mirrors qif_importer X-type logic)
                    if enable_linked and linked_acc_id and inv_action in LINKED_CAPABLE:
                        cash_tx_amount = (-abs(calc_total_acc) if inv_action in CASH_OUT_ACTIONS
                                         else abs(calc_total_acc))

                        # Security name becomes the payee and description on the cash side
                        sec_name = sec_names_plain.get(inv_security) if inv_security else None
                        payee_id = get_or_create_payee_id(cur, sec_name) if sec_name else None
                        cash_description = sec_name or inv_memo or inv_action

                        # Determine target amount when currencies differ
                        if linked_acc_curr and linked_acc_curr != acc_currencies_id:
                            if linked_target_amount and linked_target_amount != 0:
                                target_amt = abs(linked_target_amount)
                            else:
                                actual_fx = get_latest_fx_rate(cur, acc_currencies_id, linked_acc_curr, inv_date)
                                target_amt = abs(calc_total_acc) * (actual_fx or fx_rate)
                            cash_tx_amount_linked = (-abs(target_amt) if inv_action in CASH_OUT_ACTIONS
                                                     else abs(target_amt))
                        else:
                            cash_tx_amount_linked = cash_tx_amount
                            target_amt = None

                        cur.execute(
                            """
                            INSERT INTO Transactions
                                (Accounts_Id, Date, Payees_Id, Description,
                                 Total_Amount, Cleared,
                                 Accounts_Id_Target, Total_Amount_Target, Transfers_Id)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            RETURNING Transactions_Id
                            """,
                            (
                                linked_acc_id,
                                inv_date,
                                payee_id,
                                cash_description,
                                cash_tx_amount_linked,
                                True,
                                acc_id,               # investment account (informational)
                                abs(calc_total_acc),  # investment amount in account currency
                                None,                 # no Transfers_Id — Investments is authoritative
                            ),
                        )
                        linked_tx_id = cur.fetchone()[0]

                        # Back-fill Transactions_Id on the Investments row
                        cur.execute(
                            "UPDATE Investments SET Transactions_Id = %s WHERE Investments_Id = %s",
                            (linked_tx_id, investments_id),
                        )

                st.success("✅ Investment transaction saved!")
                update_holdings_fn()
                if acc_type in ('Brokerage', 'Margin', 'Other Investment'):
                    update_investment_balances_fn()
                else:
                    from database.crud import update_pension_balances
                    update_pension_balances()

                # Update the linked cash account balance when a transfer was created
                if enable_linked and linked_acc_id and inv_action in LINKED_CAPABLE:
                    update_accounts_balances()

                # Clear caches so the investment register, linked cash account
                # register, and account list all reflect the newly saved row on
                # the next render without requiring a full browser refresh.
                # The register key includes sort column+direction, so clear all variants.
                _inv_prefix = f"inv_reg_{acc_id}_"
                for _k in [k for k in st.session_state if k.startswith(_inv_prefix) and k.endswith("_orig")]:
                    st.session_state.pop(_k, None)
                # Also clear the linked account's register snapshot so the cash
                # side shows the new transfer row immediately.
                if enable_linked and linked_acc_id:
                    _cash_prefix = f"set_reg_{linked_acc_id}_"
                    for _k in [k for k in st.session_state if k.startswith(_cash_prefix) and k.endswith("_orig")]:
                        st.session_state.pop(_k, None)
                st.session_state.pop("df_accs", None)
                st.cache_data.clear()   # flush all @st.cache_data queries (account register, balances, etc.)

                st.session_state['inv_form_reset'] = rc + 1
                st.rerun()

            except Exception as exc:
                st.error(f"Error saving investment transaction: {exc}")


def _render_edit_investment_form(acc_id, acc_type, df_accs, df_securities, get_db_fn,
                                  update_holdings_fn, update_investment_balances_fn):
    """Edit an existing investment transaction with optional linked cash account management.

    Mirrors _render_new_investment_form but operates on an existing Investments row:
      - Lets the user search by date range then pick a record from a selectbox.
      - Pre-fills all fields from the selected row.
      - On save: UPDATEs the Investments row and creates / updates / deletes the
        linked Transactions row in the cash account (Investments.Transactions_Id).
    """

    ALL_ACTIONS = [
        'Buy', 'Sell', 'Dividend', 'Reinvest', 'IntInc', 'RtrnCap',
        'MiscExp', 'MiscInc', 'ShrIn', 'ShrOut', 'CashIn', 'CashOut',
        'Split', 'Vest', 'Grant', 'Exercise', 'Expire',
    ]
    CASH_OUT_ACTIONS     = {'Buy', 'MiscExp', 'CashOut'}
    CASH_IN_ACTIONS      = {'Sell', 'Dividend', 'IntInc', 'RtrnCap', 'MiscInc', 'CashIn'}
    LINKED_CAPABLE       = CASH_OUT_ACTIONS | CASH_IN_ACTIONS
    NO_SECURITY_ACTIONS  = {'CashIn', 'CashOut'}
    QTY_REQUIRED_ACTIONS = {'Buy', 'Sell', 'ShrIn', 'ShrOut', 'Reinvest', 'Split',
                             'Vest', 'Grant', 'Exercise', 'Expire'}
    _INSTRUMENT_TYPES    = [
        "", "Stock", "ETF", "Bond", "CFD", "CEF", "CFDOnETF", "CFDOnStock",
        "CFDOnIndex", "CFDOnFutures", "CFDOnFund", "Fund", "Option", "FX Spot", "Other",
    ]

    # ── Account metadata ───────────────────────────────────────────────────────
    acc_row = df_accs[df_accs['accounts_id'] == acc_id].iloc[0]
    acc_currencies_id = int(acc_row['currencies_id'])
    acc_linked_default = acc_row.get('accounts_id_linked')
    if pd.isna(acc_linked_default):
        acc_linked_default = None
    else:
        acc_linked_default = int(acc_linked_default)

    INV_TYPES = {'Brokerage', 'Pension', 'Other Investment', 'Margin'}
    cash_acc_options = {
        int(row['accounts_id']): f"{row['accounts_name']} ({row['accounts_balance']:,.2f})"
        for _, row in df_accs.iterrows()
        if row['accounts_type'] not in INV_TYPES and row['accounts_id'] != acc_id
    }
    cash_acc_ids = list(cash_acc_options.keys())

    # ── Step 1: Date range filter + investment picker ──────────────────────────
    st.markdown("#### 🔍 Select Investment to Edit")
    _fc1, _fc2 = st.columns(2)
    with _fc1:
        _edit_from = st.date_input("From", value=date.today() - timedelta(days=90),
                                   min_value=date(1990, 1, 1), 
                                    key=f"inv_edit_from_{acc_id}")
    with _fc2:
        _edit_to = st.date_input("To", value=date.today(),
                                 min_value=date(1990, 1, 1),
                                  key=f"inv_edit_to_{acc_id}")

    with get_db_fn() as _conn_load:
        df_inv = pd.read_sql(
            """SELECT i.investments_id, i.date, i.action, i.quantity,
                      i.price_per_share, i.commission,
                      i.total_amount_acccur, i.total_amount_seccur, i.fx_rate,
                      i.description, i.transactions_id, i.securities_id,
                      i.instrument_type,
                      s.securities_name
               FROM   Investments i
               LEFT JOIN Securities s ON s.Securities_Id = i.Securities_Id
               WHERE  i.Accounts_Id = %s
                 AND  i.Date BETWEEN %s AND %s
               ORDER  BY i.Date DESC, i.Investments_Id DESC""",
            _conn_load,
            params=(acc_id, _edit_from, _edit_to),
        )

    if df_inv.empty:
        st.info("No investments found in the selected date range.")
        return

    _inv_opts: dict = {}
    for _, _r in df_inv.iterrows():
        _lbl = f"{_r['date']}  |  {_r['action']}  |  {_r.get('securities_name') or '—'}"
        if _r.get('total_amount_acccur') is not None:
            _lbl += f"  |  {float(_r['total_amount_acccur']):,.4f}"
        _inv_opts[int(_r['investments_id'])] = _lbl

    sel_inv_id = st.selectbox(
        "Investment to edit",
        options=list(_inv_opts.keys()),
        format_func=lambda x: _inv_opts.get(x, str(x)),
        key=f"inv_edit_sel_{acc_id}",
    )

    if sel_inv_id is None:
        return

    sel_row = df_inv[df_inv['investments_id'] == sel_inv_id].iloc[0]

    # ── Load existing linked transaction ──────────────────────────────────────
    _raw_tx_id = sel_row.get('transactions_id')
    existing_linked_tx: dict | None = None
    existing_linked_acc_id: int | None = None
    if _raw_tx_id is not None and not pd.isna(_raw_tx_id):
        with get_db_fn() as _conn_tx:
            _cur_tx = _conn_tx.cursor()
            _cur_tx.execute(
                """SELECT transactions_id, accounts_id, total_amount, date
                   FROM   Transactions WHERE Transactions_Id = %s""",
                (int(_raw_tx_id),),
            )
            _tx_row = _cur_tx.fetchone()
            if _tx_row:
                existing_linked_tx = {
                    'transactions_id': _tx_row[0],
                    'accounts_id':     _tx_row[1],
                    'total_amount':    _tx_row[2],
                    'date':            _tx_row[3],
                }
                existing_linked_acc_id = _tx_row[1]

    # ── Detect selection change → write DB values into session state ───────────
    # Streamlit ignores value=/index= when a key already exists in session state,
    # so we must SET the target values ourselves instead of just clearing keys.
    _last_sel_key = f"inv_edit_last_{acc_id}"
    if st.session_state.get(_last_sel_key) != sel_inv_id:
        _act = str(sel_row['action']) if sel_row['action'] else ALL_ACTIONS[0]
        _raw_sec = sel_row.get('securities_id')
        _sec_id  = int(_raw_sec) if (_raw_sec is not None and pd.notna(_raw_sec)) else None
        _dv = sel_row['date']
        if hasattr(_dv, 'date'):   # pandas Timestamp → plain date
            _dv = _dv.date()
        def _f(col):
            v = sel_row.get(col)
            return float(v) if (v is not None and pd.notna(v)) else 0.0

        # Load raw FX rate from the stored row; fall back to 1.0 if absent
        _raw_fx_e = sel_row.get('fx_rate')
        _fx_pref  = (float(_raw_fx_e)
                     if (_raw_fx_e is not None and not pd.isna(_raw_fx_e) and float(_raw_fx_e) > 0)
                     else 1.0)

        st.session_state[f"inv_edit_date_{acc_id}"]      = _dv
        st.session_state[f"inv_edit_action_{acc_id}"]    = _act
        st.session_state[f"inv_edit_sec_{acc_id}"]       = _sec_id
        st.session_state[f"inv_edit_qty_{acc_id}"]       = _f('quantity')
        st.session_state[f"inv_edit_price_{acc_id}"]     = _f('price_per_share')
        st.session_state[f"inv_edit_comm_{acc_id}"]      = _f('commission')
        st.session_state[f"inv_edit_total_sec_{acc_id}"] = _f('total_amount_seccur')
        st.session_state[f"inv_edit_fx_{acc_id}"]        = _fx_pref
        st.session_state[f"inv_edit_total_acc_{acc_id}"] = _f('total_amount_acccur')
        st.session_state[f"inv_edit_memo_{acc_id}"]      = sel_row.get('description') or ""
        st.session_state[f"inv_edit_instr_{acc_id}"]     = sel_row.get('instrument_type') or ""
        st.session_state[f"inv_edit_linked_chk_{acc_id}"] = (
            existing_linked_tx is not None or acc_linked_default is not None)
        _def_linked = (existing_linked_acc_id if existing_linked_acc_id in cash_acc_ids
                       else (acc_linked_default if acc_linked_default in cash_acc_ids else None))
        if _def_linked is not None:
            st.session_state[f"inv_edit_linked_acc_{acc_id}"] = _def_linked
        st.session_state.pop(f"inv_edit_linked_target_{acc_id}", None)
        # Remove old key so it doesn't shadow the new total_acc key
        st.session_state.pop(f"inv_edit_total_{acc_id}", None)
        st.session_state[_last_sel_key] = sel_inv_id

    # ── Linked-account selectbox (outside form for FX pre-calc) ───────────────
    enable_linked = st.checkbox(
        "Maintain linked cash account transfer (BuyX / SellX / DivX)",
        value=(existing_linked_tx is not None or acc_linked_default is not None),
        key=f"inv_edit_linked_chk_{acc_id}",
    )
    linked_acc_id: int | None = None
    linked_acc_curr: int | None = None
    fx_rate = 1.0

    if enable_linked:
        if not cash_acc_ids:
            st.warning("No eligible cash/bank accounts found.")
            enable_linked = False
        else:
            _default_linked = (
                existing_linked_acc_id
                if existing_linked_acc_id in cash_acc_ids
                else (acc_linked_default if acc_linked_default in cash_acc_ids else None)
            )
            _def_idx = cash_acc_ids.index(_default_linked) if _default_linked in cash_acc_ids else 0
            linked_acc_id = st.selectbox(
                "Linked Cash Account",
                options=cash_acc_ids,
                format_func=lambda x: cash_acc_options.get(x, "Unknown"),
                index=_def_idx,
                key=f"inv_edit_linked_acc_{acc_id}",
            )
            _linked_row = df_accs[df_accs['accounts_id'] == linked_acc_id]
            if not _linked_row.empty:
                linked_acc_curr = int(_linked_row.iloc[0]['currencies_id'])
            if linked_acc_curr and linked_acc_curr != acc_currencies_id:
                with get_db_fn() as _conn_fx:
                    fx_rate = get_latest_fx_rate(_conn_fx.cursor(),
                                                  acc_currencies_id, linked_acc_curr)

    if existing_linked_tx:
        if enable_linked:
            st.caption(
                f"ℹ️ Linked to transaction #{existing_linked_tx['transactions_id']} "
                f"(amount: {float(existing_linked_tx['total_amount']):,.4f}, "
                f"date: {existing_linked_tx['date']}). Will be updated on save."
            )
        else:
            st.warning(
                f"⚠️ Currently linked to transaction #{existing_linked_tx['transactions_id']}. "
                f"Saving with link **disabled** will **delete** that transaction."
            )

    # ── Edit fields ───────────────────────────────────────────────────────────
    st.divider()

    # All securities — show currency code, no account-currency filter
    existing_sec_id: int | None = None
    _raw_sec = sel_row.get('securities_id')
    if _raw_sec is not None and not pd.isna(_raw_sec):
        existing_sec_id = int(_raw_sec)
    _cur_map_e = (df_securities.set_index('securities_id')['currencies_shortname'].to_dict()
                  if 'currencies_shortname' in df_securities.columns else {})
    sec_options = {
        int(r['securities_id']): (
            f"{r['securities_name']} ({r['currencies_shortname']})"
            if r.get('currencies_shortname') else r['securities_name']
        )
        for _, r in df_securities.iterrows()
    }
    sec_names_plain = {int(r['securities_id']): r['securities_name']
                       for _, r in df_securities.iterrows()}

    col1, col2 = st.columns(2)

    with col1:
        inv_date = st.date_input(
            "Date",
            value=sel_row['date'],
            min_value=date(1900, 1, 1),
            key=f"inv_edit_date_{acc_id}",
        )

        _existing_action = str(sel_row['action']) if sel_row['action'] else ALL_ACTIONS[0]
        _action_idx = ALL_ACTIONS.index(_existing_action) if _existing_action in ALL_ACTIONS else 0
        inv_action = st.selectbox(
            "Action", ALL_ACTIONS,
            index=_action_idx,
            key=f"inv_edit_action_{acc_id}",
        )

        _sec_ids = [None] + list(sec_options.keys())
        _sec_def_idx = _sec_ids.index(existing_sec_id) if existing_sec_id in _sec_ids else 0
        inv_security = st.selectbox(
            "Security",
            options=_sec_ids,
            format_func=lambda x: sec_options.get(x, "— none —"),
            index=_sec_def_idx,
            key=f"inv_edit_sec_{acc_id}",
        )

        _existing_instr = sel_row.get('instrument_type') or ""
        _instr_idx = _INSTRUMENT_TYPES.index(_existing_instr) if _existing_instr in _INSTRUMENT_TYPES else 0
        inv_instrument_type = st.selectbox(
            "Instrument Type (optional)",
            options=_INSTRUMENT_TYPES,
            index=_instr_idx,
            key=f"inv_edit_instr_{acc_id}",
        )

    with col2:
        inv_qty = st.number_input(
            "Quantity", min_value=0.0,
            value=float(sel_row['quantity']) if sel_row.get('quantity') is not None else 0.0,
            step=0.0001, format="%.8f",
            key=f"inv_edit_qty_{acc_id}",
        )
        inv_price = st.number_input(
            "Price Per Share", min_value=0.0,
            value=float(sel_row['price_per_share']) if sel_row.get('price_per_share') is not None else 0.0,
            step=0.00000001, format="%.8f",
            key=f"inv_edit_price_{acc_id}",
        )
        inv_comm = st.number_input(
            "Commission (0 = auto-derive)",
            min_value=0.0,
            value=float(sel_row['commission']) if sel_row.get('commission') is not None else 0.0,
            step=0.00000001, format="%.8f",
            key=f"inv_edit_comm_{acc_id}",
            help=(
                "Leave 0 to auto-derive from Qty × Price vs Total (Sec Currency): "
                "Buy → Total(Sec) − Qty×Price; Sell → Qty×Price − Total(Sec)."
            ),
        )
        inv_memo = st.text_input(
            "Memo / Description",
            value=sel_row.get('description') or "",
            key=f"inv_edit_memo_{acc_id}",
        )

    # ── Amounts (always shown: Total Sec · FX Rate · Total Acc) ───────────────
    st.divider()
    st.markdown("**Amounts**")

    _sec_currencies_id_ed = None
    _sec_cur_name_ed = ""
    if inv_security:
        _sr_ed = df_securities[df_securities['securities_id'] == inv_security]
        if not _sr_ed.empty:
            _sec_currencies_id_ed = int(_sr_ed.iloc[0]['currencies_id'])
            _sec_cur_name_ed = str(_sr_ed.iloc[0].get('currencies_shortname', ''))
    _is_xc_ed = (_sec_currencies_id_ed is not None and _sec_currencies_id_ed != acc_currencies_id)

    # Account currency display name
    _acc_cur_name_ed = ""
    try:
        with get_db_fn() as _cn_ae:
            _cae = _cn_ae.cursor()
            _cae.execute("SELECT Currencies_ShortName FROM Currencies WHERE Currencies_Id = %s",
                         (acc_currencies_id,))
            _re = _cae.fetchone()
            if _re:
                _acc_cur_name_ed = _re[0]
    except Exception:
        pass

    _sec_lbl_e = f"Total ({_sec_cur_name_ed})" if _sec_cur_name_ed else "Total (Sec Currency)"
    _acc_lbl_e = f"Total ({_acc_cur_name_ed})" if _acc_cur_name_ed else "Total (Acc Currency)"

    # Values are pre-loaded into session state when selection changes (see above).
    # For cross-currency: FX is loaded from the stored row.
    # For same-currency:  FX session state is set to 1.0.
    _ea1, _ea2, _ea3 = st.columns(3)
    with _ea1:
        inv_total_sec_cur = st.number_input(
            _sec_lbl_e,
            value=float(sel_row['total_amount_seccur']) if sel_row.get('total_amount_seccur') is not None else 0.0,
            step=0.01, format="%.8f",
            key=f"inv_edit_total_sec_{acc_id}",
            help="Total in the security's native currency. Leave 0 to auto-calculate from Qty × Price ± Commission.",
        )
    with _ea2:
        inv_fx_rate = st.number_input(
            "FX Rate (1 Sec → Acc)",
            value=st.session_state.get(f"inv_edit_fx_{acc_id}", 1.0),
            min_value=0.000001, step=0.00000001, format="%.8f",
            key=f"inv_edit_fx_{acc_id}",
            help=(
                "Stored FX rate for this transaction. "
                "1 unit of security currency = this many account-currency units."
                + (" Same currency — defaults to 1.0." if not _is_xc_ed else "")
            ),
        )
    with _ea3:
        inv_total_acc_cur = st.number_input(
            _acc_lbl_e,
            value=float(sel_row['total_amount_acccur']) if sel_row.get('total_amount_acccur') is not None else 0.0,
            step=0.01, format="%.8f",
            key=f"inv_edit_total_acc_{acc_id}",
            help="Total in the account's currency. Leave 0 to auto-calculate from Total(Sec) × FX Rate.",
        )

    # ── Live auto-calc hint ────────────────────────────────────────────────────
    _qxp_e = inv_qty * inv_price if inv_qty > 0 and inv_price > 0 else 0.0
    _hint_parts_e: list[str] = []
    _h_sec_e = inv_total_sec_cur
    if inv_comm == 0 and inv_total_sec_cur > 0 and _qxp_e > 0:
        if inv_action == 'Buy':
            _h_comm_e = max(0.0, inv_total_sec_cur - _qxp_e)
        elif inv_action == 'Sell':
            _h_comm_e = max(0.0, _qxp_e - inv_total_sec_cur)
        else:
            _h_comm_e = 0.0
        if _h_comm_e > 0:
            _hint_parts_e.append(f"Commission → **{_h_comm_e:,.8f}**")
    if inv_total_sec_cur == 0 and _qxp_e > 0:
        _c_hint_e = inv_comm if inv_comm > 0 else 0.0
        if inv_action == 'Buy':
            _h_sec_e = _qxp_e + _c_hint_e
        elif inv_action == 'Sell':
            _h_sec_e = max(0.0, _qxp_e - _c_hint_e)
        else:
            _h_sec_e = _qxp_e
        if _h_sec_e > 0:
            _hint_parts_e.append(f"{_sec_lbl_e} → **{_h_sec_e:,.8f}**")
    if inv_total_acc_cur == 0 and _h_sec_e > 0 and _is_xc_ed:
        _h_acc_e = _h_sec_e * float(inv_fx_rate)
        _hint_parts_e.append(f"{_acc_lbl_e} → **{_h_acc_e:,.8f}**")
    if not _is_xc_ed:
        st.caption(
            f"Security and account share the same currency ({_acc_cur_name_ed or '?'}) — "
            "FX Rate is 1.0; Total(Sec) = Total(Acc)."
        )
    elif _hint_parts_e:
        st.caption("💡 On save: " + " · ".join(_hint_parts_e))
    else:
        st.caption(f"Loaded stored FX rate — edit if needed.")

    # ── Cross-currency target amount ───────────────────────────────────────────
    linked_target_amount = None
    if enable_linked and linked_acc_id and linked_acc_curr and linked_acc_curr != acc_currencies_id:
        st.divider()
        st.caption(
            f"Linked account is in a different currency. "
            f"Target amount pre-calculated using FX rate **{fx_rate:.6f}** (editable)."
        )
        linked_target_amount = st.number_input(
            "Target Amount in Linked Account Currency",
            value=0.0, step=0.01, format="%.4f",
            key=f"inv_edit_linked_target_{acc_id}",
        )
    elif enable_linked and linked_acc_id:
        st.caption("Linked account uses the same currency — no conversion needed.")

    if enable_linked and linked_acc_id and inv_action not in LINKED_CAPABLE:
        st.warning(f"**{inv_action}** does not support a linked cash transfer.")

    # ── Save ──────────────────────────────────────────────────────────────────
    if st.button("💾 Update Investment Transaction", key=f"inv_edit_submit_{acc_id}"):
        errors = []
        if inv_action not in NO_SECURITY_ACTIONS and not inv_security:
            errors.append("Please select a Security (required for this action).")
        if inv_action in QTY_REQUIRED_ACTIONS and inv_qty <= 0:
            errors.append(f"Quantity must be > 0 for action '{inv_action}'.")
        if enable_linked and not linked_acc_id:
            errors.append("Please select a linked cash account.")

        if errors:
            for e in errors:
                st.error(e)
        else:
            # ── Resolve amounts: commission → total_sec → fx → total_acc ─────────
            _qxp_save_e = inv_qty * inv_price if inv_qty > 0 and inv_price > 0 else 0.0

            # Commission: user value OR derived from Total(Sec) and Qty×Price
            if inv_comm > 0:
                calc_commission = inv_comm
            elif inv_total_sec_cur > 0 and _qxp_save_e > 0:
                if inv_action == 'Buy':
                    calc_commission = max(0.0, inv_total_sec_cur - _qxp_save_e)
                elif inv_action == 'Sell':
                    calc_commission = max(0.0, _qxp_save_e - inv_total_sec_cur)
                else:
                    calc_commission = 0.0
            else:
                calc_commission = 0.0

            # Total (Security Currency)
            if inv_total_sec_cur != 0:
                calc_total_sec = inv_total_sec_cur
            elif _qxp_save_e > 0:
                if inv_action == 'Buy':
                    calc_total_sec = _qxp_save_e + calc_commission
                elif inv_action == 'Sell':
                    calc_total_sec = max(0.0, _qxp_save_e - calc_commission)
                else:
                    calc_total_sec = _qxp_save_e
            else:
                calc_total_sec = abs(inv_total_acc_cur) if inv_total_acc_cur != 0 else 0.0

            # FX Rate
            calc_fx = float(inv_fx_rate) if inv_fx_rate and float(inv_fx_rate) > 0 else 1.0

            # Total (Account Currency)
            if inv_total_acc_cur != 0:
                calc_total_acc = inv_total_acc_cur
            elif calc_total_sec != 0:
                calc_total_acc = round(calc_total_sec * calc_fx, 8)
            else:
                calc_total_acc = 0.0

            try:
                with get_db_fn() as conn:
                    cur = conn.cursor()

                    # 1. UPDATE Investments row
                    cur.execute(
                        """UPDATE Investments SET
                               Securities_Id       = %s,
                               Date                = %s,
                               Action              = %s::investments_action,
                               Quantity            = %s,
                               Price_Per_Share     = %s,
                               Commission          = %s,
                               Total_Amount_AccCur = %s,
                               Total_Amount_SecCur = %s,
                               FX_Rate             = %s,
                               Description         = %s,
                               Instrument_Type     = %s
                           WHERE Investments_Id = %s""",
                        (
                            inv_security,
                            inv_date,
                            inv_action,
                            inv_qty          if inv_qty   > 0 else None,
                            inv_price        if inv_price > 0 else None,
                            calc_commission  if calc_commission > 0 else None,
                            calc_total_acc   if calc_total_acc != 0 else None,
                            calc_total_sec   if calc_total_sec != 0 else None,
                            calc_fx,
                            inv_memo or None,
                            inv_instrument_type or None,
                            sel_inv_id,
                        ),
                    )

                    # 2. Handle linked cash transaction
                    wants_link = enable_linked and linked_acc_id and inv_action in LINKED_CAPABLE
                    has_link   = existing_linked_tx is not None

                    if has_link and not wants_link:
                        # Remove link: NULL FK first, then delete cash transaction
                        cur.execute("UPDATE Investments SET Transactions_Id = NULL "
                                    "WHERE Investments_Id = %s", (sel_inv_id,))
                        cur.execute("DELETE FROM Transactions WHERE Transactions_Id = %s",
                                    (existing_linked_tx['transactions_id'],))

                    elif wants_link:
                        cash_tx_amount = (-abs(calc_total_acc) if inv_action in CASH_OUT_ACTIONS
                                         else abs(calc_total_acc))
                        sec_name = sec_names_plain.get(inv_security) if inv_security else None
                        payee_id = get_or_create_payee_id(cur, sec_name) if sec_name else None
                        cash_description = sec_name or inv_memo or inv_action

                        if linked_acc_curr and linked_acc_curr != acc_currencies_id:
                            if linked_target_amount and linked_target_amount != 0:
                                target_amt = abs(linked_target_amount)
                            else:
                                actual_fx = get_latest_fx_rate(
                                    cur, acc_currencies_id, linked_acc_curr, inv_date)
                                target_amt = abs(calc_total_acc) * (actual_fx or fx_rate)
                            cash_tx_amount_linked = (-abs(target_amt) if inv_action in CASH_OUT_ACTIONS
                                                     else abs(target_amt))
                        else:
                            cash_tx_amount_linked = cash_tx_amount
                            target_amt = None

                        _linked_acc_id_int = int(linked_acc_id)
                        if has_link and _linked_acc_id_int == existing_linked_acc_id:
                            # Same cash account → UPDATE existing transaction
                            cur.execute(
                                """UPDATE Transactions SET
                                       Date                = %s,
                                       Payees_Id           = %s,
                                       Description         = %s,
                                       Total_Amount        = %s,
                                       Accounts_Id_Target  = %s,
                                       Total_Amount_Target = %s
                                   WHERE Transactions_Id = %s""",
                                (
                                    inv_date, payee_id, cash_description,
                                    cash_tx_amount_linked,
                                    acc_id, abs(calc_total_acc),
                                    existing_linked_tx['transactions_id'],
                                ),
                            )
                        else:
                            # Different / new cash account → NULL FK + delete old, then create new
                            if has_link:
                                cur.execute("UPDATE Investments SET Transactions_Id = NULL "
                                            "WHERE Investments_Id = %s", (sel_inv_id,))
                                cur.execute("DELETE FROM Transactions WHERE Transactions_Id = %s",
                                            (existing_linked_tx['transactions_id'],))
                            cur.execute(
                                """INSERT INTO Transactions
                                       (Accounts_Id, Date, Payees_Id, Description,
                                        Total_Amount, Cleared,
                                        Accounts_Id_Target, Total_Amount_Target, Transfers_Id)
                                   VALUES (%s, %s, %s, %s, %s, TRUE, %s, %s, NULL)
                                   RETURNING Transactions_Id""",
                                (
                                    _linked_acc_id_int, inv_date, payee_id, cash_description,
                                    cash_tx_amount_linked,
                                    acc_id, abs(calc_total_acc),
                                ),
                            )
                            _new_tx_id = cur.fetchone()[0]
                            cur.execute(
                                "UPDATE Investments SET Transactions_Id = %s "
                                "WHERE Investments_Id = %s",
                                (_new_tx_id, sel_inv_id),
                            )

                st.success("✅ Investment transaction updated!")
                update_holdings_fn()
                if acc_type in ('Brokerage', 'Margin', 'Other Investment'):
                    update_investment_balances_fn()
                else:
                    update_pension_balances()

                if wants_link or (has_link and not wants_link):
                    update_accounts_balances()

                # Clear register caches
                _inv_prefix = f"inv_reg_{acc_id}_"
                for _k in [k for k in st.session_state if k.startswith(_inv_prefix) and k.endswith("_orig")]:
                    st.session_state.pop(_k, None)
                if enable_linked and linked_acc_id:
                    _cash_prefix = f"set_reg_{linked_acc_id}_"
                    for _k in [k for k in st.session_state if k.startswith(_cash_prefix) and k.endswith("_orig")]:
                        st.session_state.pop(_k, None)
                st.session_state.pop("df_accs", None)
                st.cache_data.clear()

                # Reset picker so the form shows freshly after save
                st.session_state.pop(_last_sel_key, None)
                st.session_state.pop(f"inv_edit_sel_{acc_id}", None)
                st.rerun()

            except Exception as exc:
                st.error(f"Error updating investment transaction: {exc}")


def _render_security_transactions(acc_id: int, sec_options: dict, key_suffix: str):
    """Render a security selector and, once chosen, all investment transactions
    for that security within the current account.

    sec_options  – {securities_id: securities_name} for the account.
    key_suffix   – unique string to avoid Streamlit key collisions across tabs.
    """
    with get_db() as conn:
        df_secs = pd.read_sql(
            f"""SELECT DISTINCT i.securities_id, s.securities_name
                FROM investments i
                JOIN securities s ON i.securities_id = s.securities_id
                WHERE i.accounts_id = {acc_id}
                ORDER BY s.securities_name""",
            conn,
        )

    if df_secs.empty:
        return

    sec_map = dict(zip(df_secs['securities_id'], df_secs['securities_name']))

    st.divider()
    selected = st.selectbox(
        "🔍 Security transactions:",
        options=[None] + list(sec_map.keys()),
        format_func=lambda x: "— select a security —" if x is None else sec_map.get(x, str(x)),
        key=f"sec_txn_sel_{acc_id}_{key_suffix}",
        label_visibility="collapsed",
    )

    if selected is None:
        st.caption("Select a security above to view its transactions in this account.")
        return

    with get_db() as conn:
        df_inv = pd.read_sql(
            f"""SELECT date, action, quantity, price_per_share,
                       total_amount_acccur, total_amount_seccur, fx_rate, description
                FROM investments
                WHERE accounts_id = {acc_id} AND securities_id = {selected}
                ORDER BY date ASC, investments_id ASC""",
            conn,
        )

    if df_inv.empty:
        st.info("No investment records found for this security.")
        return

    # ── Summary metrics (all in account currency) ────────────────────────────
    buy_mask      = df_inv['action'].isin(['Buy', 'ShrIn', 'Reinvest', 'Vest'])
    sell_mask     = df_inv['action'].isin(['Sell', 'ShrOut', 'Expire'])
    misc_exp_mask = df_inv['action'] == 'MiscExp'
    misc_inc_mask = df_inv['action'] == 'MiscInc'

    net_qty      = df_inv.loc[buy_mask,      'quantity'].sum() - df_inv.loc[sell_mask, 'quantity'].sum()
    buy_amt      = df_inv.loc[buy_mask,      'total_amount_acccur'].sum()
    sell_amt     = df_inv.loc[sell_mask,     'total_amount_acccur'].sum()
    misc_exp_amt = df_inv.loc[misc_exp_mask, 'total_amount_acccur'].sum()
    misc_inc_amt = df_inv.loc[misc_inc_mask, 'total_amount_acccur'].sum()
    net_pnl      = sell_amt + misc_inc_amt - buy_amt - misc_exp_amt

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Net Qty",       f"{net_qty:,.6f}")
    c2.metric("Buy Amount",    f"{buy_amt:,.2f}")
    c3.metric("Sell Amount",   f"{sell_amt:,.2f}")
    c4.metric("Misc Exp",      f"{misc_exp_amt:,.2f}")
    c5.metric("Misc Inc",      f"{misc_inc_amt:,.2f}")
    c6.metric("Net P&L",       f"{net_pnl:,.2f}", delta_color="normal")

    # ── Transaction table ────────────────────────────────────────────────────
    def _colour_action(val):
        colours = {
            'Buy': 'color:#2ecc71', 'ShrIn': 'color:#2ecc71',
            'Reinvest': 'color:#27ae60', 'Vest': 'color:#27ae60',
            'Sell': 'color:#e74c3c', 'ShrOut': 'color:#e74c3c', 'Expire': 'color:#e74c3c',
            'Dividend': 'color:#3498db', 'RtrnCap': 'color:#3498db',
            'MiscExp': 'color:#e67e22',
            'MiscInc': 'color:#9b59b6',
        }
        return colours.get(val, '')

    styled = df_inv.style.map(_colour_action, subset=['action'])

    st.dataframe(
        styled,
        hide_index=True,
        width="stretch",
        column_config={
            'date':                 st.column_config.DateColumn('Date',          format='DD/MM/YYYY'),
            'action':               st.column_config.TextColumn('Action'),
            'quantity':             st.column_config.NumberColumn('Quantity',    format='%,.8f'),
            'price_per_share':      st.column_config.NumberColumn('Price',       format='%,.8f'),
            'total_amount_acccur': st.column_config.NumberColumn('Total (Acc)', format='%,.8f'),
            'total_amount_seccur': st.column_config.NumberColumn('Total (Sec)', format='%,.8f'),
            'fx_rate':              st.column_config.NumberColumn('FX Rate',     format='%,.6f'),
            'description':          st.column_config.TextColumn('Description',   width='large'),
        },
    )
    copy_df_button(df_inv, key=f"dl_reg_sec_txns_{acc_id}_{key_suffix}")


def render_register():
    """Render the Register page."""
    st.title("📝 Account Transactions Register")
    
    # Initialize counters
    st.session_state.reset_counter = st.session_state.get('reset_counter', 0)
    st.session_state.balance_update_counter = st.session_state.get('balance_update_counter', 0)
    
    # Load data with caching
 #   if 'df_accs' not in st.session_state:
 #       st.session_state.df_accs = pd.read_sql("SELECT * FROM Accounts WHERE Is_Active = True ORDER BY Accounts_Name ASC", conn)
 #   df_accs = st.session_state.df_accs


    # 1. Top bar: inactive toggle (left) + transaction search (right)
    _top_chk_col, _top_srch_col, _top_srch_btn_col = st.columns([2, 2.5, 0.5])
    with _top_chk_col:
        show_inactive = st.checkbox("Display Inactive Accounts", value=False)
    with _top_srch_col:
        _search_query = st.text_input(
            "Search transactions",
            placeholder="Payee, description, category…",
            key="reg_search_query",
            label_visibility="collapsed",
        )
    with _top_srch_btn_col:
        _search_clicked = st.button("🔍", key="reg_search_btn",
                                    help="Search across all accounts",
                                    use_container_width=True)
    _do_search = _search_clicked and _search_query.strip()
    if _search_clicked and not _search_query.strip():
        st.toast("Please enter a search term first.", icon="⚠️")

    # 2. Check if the preference has changed to clear the cache
    if "last_show_inactive" not in st.session_state:
        st.session_state.last_show_inactive = show_inactive

    if st.session_state.last_show_inactive != show_inactive:
        if 'df_accs' in st.session_state:
            del st.session_state.df_accs
        st.session_state.last_show_inactive = show_inactive
        st.rerun()

    # 3. Load data with dynamic Query — use get_db() for cache misses only
    needs_db = (
        'df_accs' not in st.session_state
        or 'df_payees' not in st.session_state
        or 'df_payee_list' not in st.session_state
        or 'df_securities' not in st.session_state
        or 'df_cat_list' not in st.session_state
    )
    if needs_db:
        with get_db() as conn:
            if 'df_accs' not in st.session_state:
                if show_inactive:
                    query = "SELECT * FROM Accounts ORDER BY Accounts_Name ASC"
                else:
                    query = "SELECT * FROM Accounts WHERE Is_Active = True ORDER BY Accounts_Name ASC"
                st.session_state.df_accs = pd.read_sql(query, conn)

            if 'df_payees' not in st.session_state:
                st.session_state.df_payees = pd.read_sql(
                    "SELECT Payees_Id, Payees_Name, Categories_Id_Default FROM Payees", conn)

            if 'df_payee_list' not in st.session_state:
                st.session_state.df_payee_list = pd.read_sql(
                    "SELECT Payees_Id, Payees_Name FROM Payees", conn)

            if 'df_securities' not in st.session_state:
                st.session_state.df_securities = pd.read_sql(
                    """SELECT s.Securities_Id, s.Securities_Name, s.Currencies_Id,
                              c.Currencies_ShortName
                         FROM Securities s
                         JOIN Currencies c ON c.Currencies_Id = s.Currencies_Id
                        ORDER BY s.Securities_Name""", conn)

            if 'df_cat_list' not in st.session_state:
                query_cat_hierarchy = """
                WITH RECURSIVE CategoryHierarchy AS (
                    SELECT Categories_Id, Categories_Name::TEXT as Full_Path
                    FROM Categories
                    WHERE Categories_Id_Parent IS NULL
                    UNION ALL
                    SELECT c.Categories_Id, ch.Full_Path || ' : ' || c.Categories_Name
                    FROM Categories c
                    JOIN CategoryHierarchy ch ON c.Categories_Id_Parent = ch.Categories_Id
                )
                SELECT Categories_Id, Full_Path FROM CategoryHierarchy ORDER BY Full_Path;
                """
                st.session_state.df_cat_list = pd.read_sql(query_cat_hierarchy, conn)

    df_accs      = st.session_state.df_accs
    df_payees    = st.session_state.df_payees
    df_payee_list = st.session_state.df_payee_list
    df_securities = st.session_state.df_securities
    df_cat_list  = st.session_state.df_cat_list
    
    acc_options = {
        row['accounts_id']: f"{row['accounts_name']} ({row['accounts_balance']:,.2f})"
        for _, row in df_accs.iterrows()
    }
    acc_ids_list = list(acc_options.keys())
    payee_options = df_payee_list.set_index('payees_id')['payees_name'].to_dict()
    payee_names = df_payee_list['payees_name'].tolist()

    if _do_search:
        _search_dialog(_search_query, payee_options, acc_options)
    cat_options = df_cat_list.set_index('categories_id')['full_path'].to_dict()
    sec_options = df_securities.set_index('securities_id')['securities_name'].to_dict()
    # Extended options dict that includes currency code for display in forms
    _sec_cur_map = df_securities.set_index('securities_id')['currencies_shortname'].to_dict() if 'currencies_shortname' in df_securities.columns else {}
    sec_options_with_cur = {
        sid: f"{name} ({_sec_cur_map.get(sid, '')})" if _sec_cur_map.get(sid) else name
        for sid, name in sec_options.items()
    }
    
    # Account selection
    if 'account_id_internal' not in st.session_state or st.session_state.account_id_internal is None:
        st.session_state.account_id_internal = acc_ids_list[0] if acc_ids_list else None

    # Determine the index for the selectbox based on current account in session state
    current_account = st.session_state.account_id_internal
    default_index = acc_ids_list.index(current_account) if current_account in acc_ids_list else 0

    _acc_col, _btn_col = st.columns([5, 1])
    with _acc_col:
        acc_id = st.selectbox(
            "Select Account:",
            options=acc_ids_list,
            format_func=lambda x: acc_options.get(x, "Unknown"),
            index=default_index,
            key=f"account_id_internal_{st.session_state.balance_update_counter}",
        )
    with _btn_col:
        st.write("")  # vertical alignment spacer
        if st.button("💰 Overview", help="Balance & cash-flow summary for all accounts",
                     key="reg_compare_btn", use_container_width=True):
            _multi_account_dialog()
    
    # Update session state with the selected account
    st.session_state.account_id_internal = acc_id
    
    acc_type    = df_accs.loc[df_accs['accounts_id'] == acc_id, 'accounts_type'].values[0]
    acc_balance = df_accs.loc[df_accs['accounts_id'] == acc_id, 'accounts_balance'].values[0]
    acc_credit_limit = float(df_accs.loc[df_accs['accounts_id'] == acc_id, 'credit_limit'].values[0] or 0)

    # Main transactions df
    if 'register_df' not in st.session_state or st.session_state.get('register_acc_id') != acc_id:
        with get_db() as conn:
            df = pd.read_sql(f"SELECT * FROM Transactions WHERE Accounts_Id = {acc_id} ORDER BY Date DESC", conn)
        st.session_state.register_df = df
        st.session_state.register_acc_id = acc_id
    else:
        df = st.session_state.register_df
    
    if acc_type not in ['Brokerage', 'Pension', 'Other Investment', 'Margin']:  #'Brokerage', 'Pension', 'Other Investment', 'Margin', 'Real Estate', 'Vehicle', 'Asset', 'Liability'

        if acc_type == 'Credit Card' and acc_credit_limit != 0:
            available_credit = abs(acc_credit_limit) + float(acc_balance)  # balance is negative for CC debt
            _cc1, _cc2 = st.columns(2)
            _cc1.subheader(f"Outstanding Balance: {acc_balance:,.2f}")
            _cc2.subheader(f"Available Credit: {available_credit:,.2f} / {abs(acc_credit_limit):,.2f}")
        else:
            st.subheader(f"Outstanding Balance: {acc_balance:,.2f}")

        t_new, t_view = st.tabs(["➕ New Transaction / Transfer", "👁️ View Register"])

        with t_new:
            t_tx, t_transfer = st.tabs(["Transaction", "Money Transfer"])

            with t_tx:
                st.info("Create a bank/cash transaction and optional category splits.")

                recurring = st.checkbox("Recurring transaction", key=f"tx_recurring_{st.session_state.reset_counter}")

                transaction_mode = st.radio(
                    "Transaction mode",
                    ["Single Category", "Split Transaction"],
                    horizontal=True,
                    key=f"tx_transaction_mode_{st.session_state.reset_counter}"
                )

                date = st.date_input("Date", datetime.now().date(), key=f"tx_date_{st.session_state.reset_counter}", format="DD/MM/YYYY")

                # ── Payee selector OUTSIDE the form so selecting a payee
                # triggers an immediate rerun and the category list rebuilds.
                _payee_sk = f"tx_payee_select_{st.session_state.reset_counter}"
                _payee_txt_sk = f"tx_payee_text_{st.session_state.reset_counter}"
                if payee_names:
                    selected_payee = st.selectbox(
                        "Payee",
                        ["(new payee)"] + payee_names,
                        index=0,
                        help="Select an existing payee or choose New to type a payee name.",
                        key=_payee_sk
                    )
                    if selected_payee == "(new payee)":
                        payee_name = st.text_input(
                            "New Payee Name", value="",
                            help="Enter a new payee name.",
                            key=_payee_txt_sk
                        )
                    else:
                        payee_name = selected_payee
                else:
                    payee_name = st.text_input(
                        "Payee", value="",
                        help="Enter a payee name. Missing payees are created automatically.",
                        key=_payee_txt_sk
                    )

                # Resolve ranked category list as soon as payee is known
                default_cat = None
                payee_top_cats = []
                if payee_name and payee_name in payee_names:
                    payee_row = df_payees[df_payees['payees_name'] == payee_name]
                    if not payee_row.empty:
                        default_cat = payee_row['categories_id_default'].values[0]
                        _payee_id_val = int(payee_row['payees_id'].values[0])
                        _pcat_cache_key = f"payee_top_cats_{_payee_id_val}"
                        if _pcat_cache_key not in st.session_state:
                            with get_db() as _conn_cats:
                                _df_top = pd.read_sql("""
                                    SELECT s.Categories_Id, COUNT(*) AS cnt
                                    FROM Splits s
                                    JOIN Transactions t
                                      ON t.Transactions_Id = s.Transactions_Id
                                    WHERE t.Payees_Id = %s
                                      AND s.Categories_Id IS NOT NULL
                                    GROUP BY s.Categories_Id
                                    ORDER BY cnt DESC
                                """, _conn_cats, params=(_payee_id_val,))
                            st.session_state[_pcat_cache_key] = (
                                _df_top['categories_id'].tolist()
                            )
                        payee_top_cats = st.session_state[_pcat_cache_key]

                # ── Total Amount — kept OUTSIDE the form so pressing Enter while
                # editing the amount does not accidentally submit the transaction.
                _total_key   = f"tx_total_amount_{st.session_state.reset_counter}"
                _pending_key = f"tx_total_pending_{st.session_state.reset_counter}"

                # If the calculator applied a value in the previous run, promote it
                # to the widget key NOW — before the widget is instantiated — then clear it.
                if _pending_key in st.session_state:
                    st.session_state[_total_key] = st.session_state.pop(_pending_key)

                st.session_state.setdefault(_total_key, 0.0)

                _ta_col, _calc_col = st.columns([3, 2])
                with _ta_col:
                    total_amount = st.number_input(
                        "Total Amount",
                        format="%.2f",
                        key=_total_key,
                    )

                with _calc_col:
                    with st.expander("🧮 Calculator", expanded=False):
                        st.caption(
                            "Enter any arithmetic expression. "
                            "Examples: `1200 / 12` · `-350 * 3` · `(450 + 30) / 6`"
                        )
                        _expr = st.text_input(
                            "Expression",
                            placeholder="e.g.  1200 / 12",
                            label_visibility="collapsed",
                            key=f"calc_expr_{st.session_state.reset_counter}",
                        )
                        _calc_result = None
                        if _expr and _expr.strip():
                            try:
                                import ast as _ast
                                _tree = _ast.parse(_expr.strip(), mode="eval")
                                _SAFE_NODES = (
                                    _ast.Expression, _ast.Constant,
                                    _ast.BinOp, _ast.UnaryOp,
                                    _ast.Add, _ast.Sub, _ast.Mult, _ast.Div,
                                    _ast.FloorDiv, _ast.Mod, _ast.Pow,
                                    _ast.USub, _ast.UAdd,
                                )
                                if all(isinstance(n, _SAFE_NODES) for n in _ast.walk(_tree)):
                                    _calc_result = float(
                                        eval(compile(_tree, "<calc>", "eval"))  # noqa: S307
                                    )
                                    st.markdown(f"**= {_calc_result:,.4f}**")
                                else:
                                    st.warning("Only arithmetic is supported (+  −  ×  ÷  **).")
                            except Exception:
                                st.warning("Invalid expression — check syntax.")

                        if _calc_result is not None:
                            if st.button(
                                f"→ Apply {_calc_result:,.2f} to Total Amount",
                                key=f"calc_apply_{st.session_state.reset_counter}",
                                type="primary",
                                width="stretch",
                            ):
                                # Write to the PENDING key, not the widget key.
                                # On the next run the pending value is promoted
                                # before the widget renders (see above).
                                st.session_state[_pending_key] = round(_calc_result, 2)
                                st.rerun()

                if total_amount > 0:
                    st.success("Income transaction")
                elif total_amount < 0:
                    st.error("Expense transaction")
                else:
                    st.info("Enter a non-zero amount above to save.")

                # ── Container (not st.form) so Enter key never triggers a save ──────
                with st.container():
                    desc = st.text_input("Description", key=f"tx_description_{st.session_state.reset_counter}")

                    st.write("---")
                    transaction_category = None
                    split_rows = []
                    if transaction_mode == "Single Category":
                        # Top-used categories for this payee first (★), then the rest
                        _all_cat_ids = list(cat_options.keys())
                        _remaining   = [c for c in _all_cat_ids if c not in payee_top_cats]
                        _ordered     = payee_top_cats + _remaining
                        cat_list     = [None] + _ordered

                        def _cat_label(x):
                            if x is None:
                                return "Select a category"
                            label = cat_options.get(x, "Unknown")
                            return f"★ {label}" if x in payee_top_cats else label

                        # Default: explicit payee default first, else most-used
                        default_index = 0
                        if default_cat and default_cat in cat_list:
                            default_index = cat_list.index(default_cat)
                        elif payee_top_cats:
                            default_index = 1  # first item after None is the top cat

                        transaction_category = st.selectbox(
                            "Category",
                            cat_list,
                            format_func=_cat_label,
                            index=default_index,
                            key=f"tx_transaction_category_{st.session_state.reset_counter}"
                        )
                    else:
                        st.info("Add one row per split. The sum of split amounts must equal the Total Amount.")
                        # Reorder category options: top-used for this payee first,
                        # then remaining. SelectboxColumn doesn't support format_func
                        # so we build a display-label dict and pass ordered label strings.
                        _all_cat_ids   = list(cat_options.keys())
                        _remaining_ids = [c for c in _all_cat_ids if c not in payee_top_cats]
                        _split_ordered_ids = payee_top_cats + _remaining_ids

                        # Map id → display label (★ for top-used)
                        _split_cat_labels = {
                            c: (f"★ {cat_options[c]}" if c in payee_top_cats else cat_options[c])
                            for c in _split_ordered_ids
                            if c in cat_options
                        }

                        df_new_splits = pd.DataFrame([{'categories_id': None, 'amount': 0.0, 'memo': ''}])
                        new_splits_data = st.data_editor(
                            df_new_splits,
                            num_rows="dynamic",
                            hide_index=True,
                            width='stretch',
                            key=f"new_splits_editor_{st.session_state.reset_counter}",
                            column_config={
                                "categories_id": st.column_config.SelectboxColumn(
                                    "Category",
                                    options=_split_ordered_ids,
                                    format_func=lambda x: _split_cat_labels.get(x, cat_options.get(x, "Unknown"))
                                ),
                                "amount": st.column_config.NumberColumn("Amount"),
                                "memo": st.column_config.TextColumn("Memo")
                            }
                        )
                        new_splits_data = new_splits_data.dropna(subset=['categories_id', 'amount'], how='all').reset_index(drop=True)
                        split_rows = new_splits_data.to_dict('records')

                    if recurring:
                        next_occurrence = None
                        recurrence_periodicity = None
                        recurrence_installments = None
                        recurrence_end_date = None
                        with st.expander("Recurring transaction options", expanded=True):
                            r1, r2 = st.columns(2)
                            next_occurrence = r1.date_input("Next occurrence date", date + timedelta(days=30), key=f"tx_next_occurrence_{st.session_state.reset_counter}", format="DD/MM/YYYY")
                            recurrence_periodicity = r2.selectbox(
                                "Periodicity",
                                ["Weekly", "Monthly", "Bi-Monthly", "Quarterly", "Semi-Annually", "Annually"],
                                key=f"tx_recurrence_periodicity_{st.session_state.reset_counter}"
                            )

                            recurrence_end_type = st.radio(
                                "End after",
                                ["Number of installments", "End date"],
                                horizontal=True,
                                key=f"tx_recurrence_end_type_{st.session_state.reset_counter}"
                            )

                            if recurrence_end_type == "Number of installments":
                                recurrence_installments = st.number_input(
                                    "Installments",
                                    min_value=2,
                                    value=2,
                                    step=1,
                                    help="Total occurrences including the current transaction.",
                                    key=f"tx_recurrence_installments_{st.session_state.reset_counter}"
                                )
                            else:
                                recurrence_end_date = st.date_input("End date", date + timedelta(days=365), key=f"tx_recurrence_end_date_{st.session_state.reset_counter}", format="DD/MM/YYYY")
                    else:
                        next_occurrence = None
                        recurrence_periodicity = None
                        recurrence_installments = None
                        recurrence_end_date = None
                    
                    if st.button("🔥 Save Transaction & Splits", key=f"tx_submit_{st.session_state.reset_counter}"):
                        try:
                            val_total_amount = float(st.session_state.get(_total_key, 0.0))
                            if val_total_amount == 0:
                                st.error("The total amount must be non-zero.")
                            elif transaction_mode == "Single Category" and not transaction_category:
                                st.error("Please select a category for the single-category transaction.")
                            elif transaction_mode == "Split Transaction" and not split_rows:
                                st.error("Please add at least one split for a split transaction.")
                            else:
                                if transaction_mode == "Split Transaction":
                                    split_amounts = [pd.to_numeric(row['amount'], errors='coerce') for row in split_rows]
                                    if any(pd.isna(x) for x in split_amounts):
                                        st.error("Please enter valid numeric amounts for all split rows.")
                                        raise ValueError("Invalid split amount")

                                    if abs(sum(float(x) for x in split_amounts) - val_total_amount) > 0.01:
                                        st.error(f"Split total ({sum(float(x) for x in split_amounts):,.2f}) doesn't match Total Amount ({val_total_amount:,.2f})")
                                        raise ValueError("Split total mismatch")

                                dates = build_recurrence_schedule(
                                    date,
                                    next_occurrence,
                                    recurrence_periodicity,
                                    installments=recurrence_installments,
                                    end_date=recurrence_end_date
                                )

                                with get_db() as conn:
                                    cur = conn.cursor()
                                    payee_id = get_or_create_payee_id(cur, payee_name.strip()) if payee_name else None

                                    # Set default category for new payee if single category transaction
                                    if payee_id and transaction_mode == "Single Category" and payee_name and payee_name not in payee_names:
                                        cur.execute("UPDATE Payees SET Categories_Id_Default = %s WHERE Payees_Id = %s", (transaction_category, payee_id))

                                    for idx, tx_date in enumerate(dates):
                                        tx_desc = desc or ""
                                        if len(dates) > 1:
                                            tx_desc = f"{tx_desc} ({idx + 1}/{len(dates)})"

                                        tx_id = insert_bank_transaction(
                                            cur,
                                            acc_id,
                                            tx_date,
                                            payee_id,
                                            tx_desc,
                                            val_total_amount
                                        )

                                        if transaction_mode == "Single Category":
                                            insert_transaction_splits(
                                                cur,
                                                tx_id,
                                                [{
                                                    'categories_id': transaction_category,
                                                    'amount': val_total_amount,
                                                    'memo': None
                                                }]
                                            )
                                        else:
                                            for row in split_rows:
                                                row_amount = float(pd.to_numeric(row['amount'], errors='coerce') or 0)
                                                insert_transaction_splits(
                                                    cur,
                                                    tx_id,
                                                    [{
                                                        'categories_id': row['categories_id'],
                                                        'amount': row_amount,
                                                        'memo': row.get('memo')
                                                    }]
                                                )
                                # get_db().__exit__ commits here
                                update_accounts_balances(st.session_state["account_id_internal"])
                                st.session_state.balance_update_counter += 1
                                # Clear balance-dependent caches so the rerun
                                # picks up the updated account balances from the DB.
                                for _k in ['df_accs', 'register_df']:
                                    st.session_state.pop(_k, None)
                                # Also clear all register _orig_key snapshots for
                                # this account so every period reloads fresh data.
                                _acc_prefix_bank = f"set_reg_{acc_id}_bank_"
                                _acc_prefix_cash = f"set_reg_{acc_id}_cash_"
                                for _k in list(st.session_state.keys()):
                                    if (_k.startswith(_acc_prefix_bank) or
                                            _k.startswith(_acc_prefix_cash)) and                                             _k.endswith("_orig"):
                                        st.session_state.pop(_k, None)
                                st.success("Transaction and splits saved!")
                                reset_transaction_form_state()
                                st.rerun()
                        except ValueError:
                            pass
                        except Exception as e:
                            st.error(f"Error saving transaction: {e}")

            with t_transfer:
                st.info("Create a money transfer and mirror it on the target account.")

                recurring_transfer = st.checkbox("Recurring transfer", key=f"transfer_recurring_{st.session_state.reset_counter}")

                with st.container():  # not st.form — Enter key must not trigger save
                    date = st.date_input("Date", datetime.now().date(), key=f"transfer_date_{st.session_state.reset_counter}", format="DD/MM/YYYY")
                    
                    direction = st.radio(
                        "Transfer direction",
                        ["Transfer out", "Transfer in"],
                        horizontal=True,
                        key=f"transfer_direction_{st.session_state.reset_counter}"
                    )

                    target_accounts = [aid for aid in acc_ids_list if aid != acc_id]
                    if not target_accounts:
                        st.warning("No other active accounts are available for transfers.")
                        accounts_id_target = None
                        source_curr = None
                        target_curr = None
                        fx_rate = 1.0
                    else:
                        accounts_id_target = st.selectbox(
                            "Target Account",
                            target_accounts,
                            format_func=lambda x: acc_options.get(x, "Unknown"),
                            key=f"transfer_target_account_{st.session_state.reset_counter}"
                        )
                        source_curr = int(df_accs.loc[df_accs['accounts_id'] == acc_id, 'currencies_id'].values[0])
                        target_curr = int(df_accs.loc[df_accs['accounts_id'] == accounts_id_target, 'currencies_id'].values[0])
                        with get_db() as _conn_fx:
                            _cur_fx = _conn_fx.cursor()
                            fx_rate = float(get_latest_fx_rate(_cur_fx, source_curr, target_curr, date))

                    t_amount = st.number_input("Transfer Amount", value=0.0, format="%.2f", key=f"transfer_amount_{st.session_state.reset_counter}")
                    default_total_amount_target = abs(t_amount) * fx_rate if t_amount != 0 else 0.0
                    t_total_amount_target = st.number_input(
                        "Target Amount",
                        value=default_total_amount_target,
                        format="%.2f",
                        help=f"Auto-calculated based on FX rate {fx_rate:.4f}. Edit if needed.",
                        key=f"transfer_total_amount_target_{st.session_state.reset_counter}"
                    )

                    # Payee selection for transfers
                    default_payee_index = 0
                    if "Transfer Money" in payee_names:
                        default_payee_index = payee_names.index("Transfer Money") + 1  # +1 for "(new payee)"

                    selected_transfer_payee = st.selectbox(
                        "Payee",
                        ["(new payee)"] + payee_names,
                        index=default_payee_index,
                        help="Select an existing payee or choose New to type a payee name.",
                        key=f"transfer_payee_select_{st.session_state.reset_counter}"
                    )
                    if selected_transfer_payee == "(new payee)":
                        transfer_payee_name = st.text_input(
                            "New Payee Name",
                            value="Transfer Money" if default_payee_index == 0 else "",
                            help="Enter a new payee name.",
                            key=f"transfer_payee_text_{st.session_state.reset_counter}"
                        )
                    else:
                        transfer_payee_name = selected_transfer_payee

                    transfer_desc = st.text_input("Description", key=f"transfer_description_{st.session_state.reset_counter}")

                    if recurring_transfer:
                        next_transfer_date = None
                        transfer_periodicity = None
                        transfer_installments = None
                        transfer_end_date = None
                        with st.expander("Recurring transfer options", expanded=True):
                            r1, r2 = st.columns(2)
                            next_transfer_date = r1.date_input("Next occurrence date", date + timedelta(days=30), key=f"transfer_next_date_{st.session_state.reset_counter}", format="DD/MM/YYYY")
                            transfer_periodicity = r2.selectbox(
                                "Periodicity",
                                ["Weekly", "Monthly", "Bi-Monthly", "Quarterly", "Semi-Annually", "Annually"],
                                key=f"transfer_periodicity_{st.session_state.reset_counter}"
                            )

                            transfer_end_type = st.radio(
                                "End after",
                                ["Number of installments", "End date"],
                                horizontal=True,
                                key=f"transfer_end_type_{st.session_state.reset_counter}"
                            )

                            if transfer_end_type == "Number of installments":
                                transfer_installments = st.number_input(
                                    "Installments",
                                    min_value=2,
                                    value=2,
                                    step=1,
                                    help="Total occurrences including the current transfer.",
                                    key=f"transfer_installments_{st.session_state.reset_counter}"
                                )
                            else:
                                transfer_end_date = st.date_input("End date", date + timedelta(days=365), key=f"transfer_end_date_{st.session_state.reset_counter}", format="DD/MM/YYYY")
                    else:
                        next_transfer_date = None
                        transfer_periodicity = None
                        transfer_installments = None
                        transfer_end_date = None

                    if st.button("🔥 Save Transfer", key=f"transfer_submit_{st.session_state.reset_counter}"):
                        try:
                            if t_amount <= 0:
                                st.error("Transfer amount must be greater than zero.")
                            elif not accounts_id_target:
                                st.error("Please select a target account for the transfer.")
                            else:
                                transfer_amount = abs(t_amount)
                                transfer_total_amount_target = abs(t_total_amount_target) if t_total_amount_target else transfer_amount
                                dates = build_recurrence_schedule(
                                    date,
                                    next_transfer_date,
                                    transfer_periodicity,
                                    installments=transfer_installments,
                                    end_date=transfer_end_date
                                )

                                with get_db() as conn:
                                    cur = conn.cursor()
                                    payee_id = get_or_create_payee_id(cur, transfer_payee_name.strip()) if transfer_payee_name else None

                                    for idx, tx_date in enumerate(dates):
                                        cur.execute("SELECT nextval('transfers_id_seq')")
                                        transfers_id = cur.fetchone()[0]

                                        transfer_label = transfer_desc or "Transfer"
                                        if len(dates) > 1:
                                            transfer_label = f"{transfer_label} ({idx + 1}/{len(dates)})"

                                        if direction == "Transfer out":
                                            source_account = acc_id
                                            destination_account = accounts_id_target
                                            source_amount = -transfer_amount
                                            destination_amount = transfer_total_amount_target
                                        else:
                                            source_account = accounts_id_target
                                            destination_account = acc_id
                                            source_amount = -transfer_total_amount_target
                                            destination_amount = transfer_amount

                                        source_tx_id = insert_bank_transaction(
                                            cur,
                                            source_account,
                                            tx_date,
                                            payee_id,
                                            transfer_label,
                                            source_amount,
                                            accounts_id_target=destination_account,
                                            total_amount_target=abs(destination_amount),
                                            transfers_id=transfers_id
                                        )
                                        insert_transaction_splits(
                                            cur,
                                            source_tx_id,
                                            [{
                                                'categories_id': None,
                                                'amount': source_amount,
                                                'memo': 'Transfer'
                                            }]
                                        )

                                        destination_tx_id = insert_bank_transaction(
                                            cur,
                                            destination_account,
                                            tx_date,
                                            payee_id,
                                            transfer_label,
                                            destination_amount,
                                            accounts_id_target=source_account,
                                            total_amount_target=abs(source_amount),
                                            transfers_id=transfers_id
                                        )
                                        insert_transaction_splits(
                                            cur,
                                            destination_tx_id,
                                            [{
                                                'categories_id': None,
                                                'amount': destination_amount,
                                                'memo': 'Transfer'
                                            }]
                                        )
                                # get_db().__exit__ commits here
                                update_accounts_balances(acc_id)
                                update_accounts_balances(accounts_id_target)
                                st.session_state.balance_update_counter += 1
                                # Clear balance-dependent caches so the rerun
                                # picks up the updated account balances from the DB.
                                for _k in ['df_accs', 'register_df']:
                                    st.session_state.pop(_k, None)
                                # Also clear all register _orig_key snapshots for
                                # this account so every period reloads fresh data.
                                _acc_prefix_bank = f"set_reg_{acc_id}_bank_"
                                _acc_prefix_cash = f"set_reg_{acc_id}_cash_"
                                for _k in list(st.session_state.keys()):
                                    if (_k.startswith(_acc_prefix_bank) or
                                            _k.startswith(_acc_prefix_cash)) and                                             _k.endswith("_orig"):
                                        st.session_state.pop(_k, None)
                                st.success("Transfer saved and mirrored successfully!")
                                reset_transaction_form_state()
                                st.rerun()
                        except Exception as e:
                            st.error(f"Error saving transfer: {e}")

        with t_view:
            _render_transaction_table(acc_id, payee_options, acc_options, cat_options, "bank", acc_balance)
    else:
        tab_inv_new, tab_inv_edit, tab_inv_view, tab_edit_hold, cash_view = st.tabs([
            "➕ New Investment Transaction", "✏️ Edit Investment",
            "📓 Investment Register", "✏️ Review Holdings",
            "👁️ Cash Transaction Register",
        ])
        with cash_view:
            _render_transaction_table(acc_id, payee_options, acc_options, cat_options, "cash", acc_balance)

        # ── New investment transaction form ────────────────────────────────────
        with tab_inv_new:
            _render_new_investment_form(
                acc_id, acc_type, df_accs, df_securities, get_db,
                update_holdings, update_investment_balances
            )

        # ── Edit investment transaction form ───────────────────────────────────
        with tab_inv_edit:
            _render_edit_investment_form(
                acc_id, acc_type, df_accs, df_securities, get_db,
                update_holdings, update_investment_balances
            )

        with tab_inv_view:

            # Build sec_options first — needed for Security sort below
            _full_sec_options = df_securities.set_index('securities_id')['securities_name'].to_dict()

            # ── Sort controls ──────────────────────────────────────────────────
            _inv_sk = f"inv_sort_{acc_id}"
            _isc1, _isc2 = st.columns([3, 1])
            with _isc1:
                _inv_sort_col = st.selectbox(
                    "Sort by",
                    options=["Date", "Security", "Total Amount"],
                    index=0,
                    key=f"{_inv_sk}_col",
                    label_visibility="collapsed",
                )
            with _isc2:
                _inv_sort_dir = st.radio(
                    "Direction",
                    options=["ASC", "DESC"],
                    index=1,
                    horizontal=True,
                    key=f"{_inv_sk}_dir",
                    label_visibility="collapsed",
                )

            _inv_sort_asc = _inv_sort_dir == "ASC"

            # ── Cache the investment data (keyed by sort so a sort change refetches) ──
            _inv_orig_key = f"inv_reg_{acc_id}_{_inv_sort_col}_{_inv_sort_dir}_orig"
            if _inv_orig_key not in st.session_state:
                _column_order = [
                    "investments_id", "accounts_id", "date", "securities_id",
                    "action", "instrument_type", "quantity", "price_per_share",
                    "commission", "total_amount_acccur", "total_amount_seccur", "fx_rate",
                    "description", "transactions_id", "embedding",
                ]
                with get_db() as conn:
                    _df_fresh = pd.read_sql(
                        "SELECT * FROM Investments WHERE Accounts_Id = %(acc_id)s",
                        conn, params={'acc_id': acc_id}
                    )
                _df_fresh = _df_fresh[[c for c in _column_order if c in _df_fresh.columns]]
                # Apply sort
                if _inv_sort_col == "Security":
                    _df_fresh["_sk"] = _df_fresh["securities_id"].map(_full_sec_options).fillna("")
                    _df_fresh = _df_fresh.sort_values("_sk", ascending=_inv_sort_asc, kind="stable").drop(columns=["_sk"]).reset_index(drop=True)
                elif _inv_sort_col == "Total Amount":
                    _df_fresh = _df_fresh.sort_values("total_amount_acccur", ascending=_inv_sort_asc, kind="stable").reset_index(drop=True)
                else:  # Date
                    _df_fresh = _df_fresh.sort_values("date", ascending=_inv_sort_asc, kind="stable").reset_index(drop=True)
                st.session_state[_inv_orig_key] = _df_fresh

            df_inv_orig = st.session_state[_inv_orig_key]

            # ── Display-only link columns ──────────────────────────────────────
            # These are NOT stored in df_inv_orig / session state and are stripped
            # from edited_df before change-detection and save.
            _df_for_editor = df_inv_orig.copy()
            _df_for_editor.insert(0, "linked", _df_for_editor["transactions_id"].notna())

            # Batch-fetch the linked cash account name for every row that has a
            # transactions_id, using a single indexed query.
            _linked_tx_ids = [
                int(x) for x in _df_for_editor["transactions_id"].dropna()
                if pd.notna(x)
            ]
            if _linked_tx_ids:
                with get_db() as _conn_lkup:
                    _df_lkup = pd.read_sql(
                        """SELECT t.transactions_id,
                                  a.accounts_name
                           FROM   Transactions t
                           JOIN   Accounts a ON a.accounts_id = t.accounts_id
                           WHERE  t.transactions_id = ANY(%s)""",
                        _conn_lkup,
                        params=(_linked_tx_ids,),
                    )
                _lkup_acc = _df_lkup.set_index("transactions_id")["accounts_name"].to_dict()
            else:
                _lkup_acc = {}

            _df_for_editor["cash_account"] = (
                _df_for_editor["transactions_id"]
                .apply(lambda x: _lkup_acc.get(int(x), "") if pd.notna(x) else "")
            )
            _df_for_editor["cash_tx_id"] = (
                _df_for_editor["transactions_id"]
                .apply(lambda x: int(x) if pd.notna(x) else None)
            )

            _edited_with_linked = st.data_editor(
                _df_for_editor,
                num_rows="dynamic",
                key=f"inv_reg_{acc_id}_{_inv_sort_col}_{_inv_sort_dir}",  # reset editor when sort changes
                width="stretch",
                column_config={
                    "linked": st.column_config.CheckboxColumn(
                        "🔗",
                        disabled=True,
                        pinned=True,
                        help="Checked = entry is linked to a cash-account transaction; "
                             "unchecked = no cash-side entry exists yet.",
                    ),
                    "cash_account": st.column_config.TextColumn(
                        "Linked Account",
                        disabled=True,
                        width="medium",
                        help="Cash account that holds the linked transaction.",
                    ),
                    "cash_tx_id": st.column_config.NumberColumn(
                        "Cash TX ID",
                        disabled=True,
                        format="%d",
                        width="small",
                        help="Transaction ID of the linked cash-account entry.",
                    ),
                    "investments_id": st.column_config.NumberColumn(
                        "Transaction ID",
                        disabled=True
                    ),
                    "accounts_id": None,
                    "date": st.column_config.DateColumn(
                        "Date",
                        format="DD/MM/YYYY"
                    ),
                    "securities_id": st.column_config.SelectboxColumn(
                        "Security",
                        options=list(_full_sec_options.keys()),
                        format_func=lambda x: (
                            "" if (x is None or (hasattr(x, "__float__") and __import__("math").isnan(float(x))))
                            else _full_sec_options.get(x, str(x))
                        ),
                        width="large"
                    ),
                    "action": st.column_config.SelectboxColumn(
                        "Action",
                        options=['Buy', 'Sell', 'Dividend', 'Reinvest', 'Split',
                                 'ShrIn', 'ShrOut', 'IntInc', 'CashIn', 'CashOut',
                                 'Vest', 'Expire', 'Grant', 'Exercise',
                                 'MiscExp', 'MiscInc', 'RtrnCap'],
                        required=True
                    ),
                    "instrument_type": st.column_config.SelectboxColumn(
                        "Instrument Type",
                        options=["", "Stock", "ETF", "Bond", "CFD", "CEF", "CFDOnETF", "CFDOnStock",
                                 "CFDOnIndex", "CFDOnFutures", "CFDOnFund", "Fund", "Option",
                                 "FX Spot", "Other"],
                        width="small",
                        help="Specific traded instrument (e.g. CFDOnETF for Saxo CFD trades, "
                             "FX Spot for currency pairs). Leave blank for standard transactions.",
                    ),
                    "quantity": st.column_config.NumberColumn(
                        "Quantity",
                        format="%,.8f",
                    ),
                    "price_per_share": st.column_config.NumberColumn(
                        "Price",
                        format="%,.8f",
                    ),
                    "commission": st.column_config.NumberColumn(
                        "Commission",
                        format="%,.8f",
                    ),
                    "total_amount_acccur": st.column_config.NumberColumn(
                        "Total (Acc)",
                        format="%,.8f",
                        help="Total amount in the investment account's currency.",
                    ),
                    "total_amount_seccur": st.column_config.NumberColumn(
                        "Total (Sec)",
                        format="%,.8f",
                        help="Total amount in the security's native currency.",
                    ),
                    "fx_rate": st.column_config.NumberColumn(
                        "FX Rate",
                        format="%,.6f",
                        help="Rate used at booking: 1 security-currency unit = this many account-currency units.",
                    ),
                    "description": st.column_config.TextColumn(
                        "Memo",
                        width="large"
                    ),
                    "transactions_id": None,  # hidden — used for cascade delete/update
                    "embedding": None,
                },
            )

            # Strip all display-only columns — they are not DB columns and must
            # not reach execute_db_save or the change-detection logic.
            edited_df = _edited_with_linked.drop(
                columns=["linked", "cash_account", "cash_tx_id"], errors="ignore"
            )
            _copy_inv = _df_for_editor.drop(
                columns=["investments_id", "accounts_id", "transactions_id", "embedding"],
                errors="ignore",
            ).copy()
            _copy_inv["securities_id"] = _copy_inv["securities_id"].map(_full_sec_options).fillna("")
            _copy_inv = _copy_inv.rename(columns={
                "securities_id": "Security",
                "linked":        "Linked",
                "cash_account":  "Linked Account",
                "cash_tx_id":    "Cash TX ID",
            })
            copy_df_button(_copy_inv, key=f"dl_reg_inv_{acc_id}")

            # ── Change detection & Save ────────────────────────────────────────
            # Align dtypes before comparing — data_editor can return different
            # dtypes than read_sql (e.g. object vs int64 for nullable columns).
            try:
                _edited_aligned = edited_df.astype(df_inv_orig.dtypes.to_dict())
            except Exception:
                _edited_aligned = edited_df
            _inv_has_changes = not _edited_aligned.equals(df_inv_orig)

            if _inv_has_changes:
                # Fill accounts_id for new rows
                new_rows_mask = ~edited_df['investments_id'].isin(df_inv_orig['investments_id'])
                edited_df.loc[new_rows_mask, 'accounts_id'] = acc_id

                # Auto-calculate total_amount for new Buy/Sell rows
                def calculate_total(row):
                    qty    = float(row.get('quantity') or 0)
                    price  = float(row.get('price_per_share') or 0)
                    comm   = float(row.get('commission') or 0)
                    action = str(row.get('action')).strip()
                    if action == 'Buy':
                        return (qty * price) + comm
                    elif action == 'Sell':
                        return (qty * price) - comm
                    return row.get('total_amount_acccur')

                if new_rows_mask.any():
                    edited_df.loc[new_rows_mask, 'total_amount_acccur'] = edited_df[new_rows_mask].apply(calculate_total, axis=1)

                if st.button("💾 Save Investments", key=f"save_inv_reg_{acc_id}", type="primary"):
                    # Clear the cache before execute_db_save calls st.rerun(),
                    # so the next render re-fetches fresh data from the DB.
                    st.session_state.pop(_inv_orig_key, None)
                    with get_db() as _conn_save:
                        execute_db_save(
                            df_inv_orig, edited_df,
                            "Investments", "investments_id",
                            conn=_conn_save
                        )

            # ── Manage linked cash account ─────────────────────────────────────────
            # Allows creating a cash-account link for unlinked transactions, or
            # removing/inspecting an existing link.
            # Note: when an existing linked transaction is saved via the register above,
            # execute_db_save already syncs its Date and Total_Amount_AccCur automatically.
            _LINK_CAPABLE_MGR = {'Buy', 'Sell', 'Dividend', 'IntInc', 'RtrnCap', 'MiscExp'}
            _CASH_OUT_MGR     = {'Buy', 'MiscExp'}
            _INV_TYPES_MGR    = {'Brokerage', 'Pension', 'Other Investment', 'Margin'}

            # Resolve the account's default linked cash account from df_accs
            _mgr_acc_row = df_accs[df_accs['accounts_id'] == acc_id].iloc[0]
            _raw_linked  = _mgr_acc_row.get('accounts_id_linked')
            _mgr_linked_default = (
                None if (_raw_linked is None or pd.isna(_raw_linked))
                else int(_raw_linked)
            )

            _df_linkable = df_inv_orig[df_inv_orig['action'].isin(_LINK_CAPABLE_MGR)].copy()

            with st.expander("🔗 Manage Linked Cash Account", expanded=False):
                if _df_linkable.empty:
                    st.caption("No linkable transactions (Buy / Sell / Dividend / IntInc / RtrnCap / MiscExp) in this account.")
                else:
                    # ── Bulk-create missing linked cash transactions ────────────────
                    _df_unlinked   = _df_linkable[_df_linkable['transactions_id'].isna()].copy()
                    _n_unlinked    = len(_df_unlinked)
                    _n_linked      = len(_df_linkable) - _n_unlinked
                    _bulk_key      = f"inv_bulk_create_pending_{acc_id}"

                    if _n_unlinked > 0:
                        with st.container(border=True):
                            st.caption(
                                f"**{_n_unlinked}** unlinked  ·  **{_n_linked}** already linked  "
                                f"(of {len(_df_linkable)} total linkable transactions)"
                            )
                            if not _mgr_linked_default:
                                st.warning(
                                    "No default linked cash account configured for this investment "
                                    "account.  Set one in Account Settings to enable bulk creation."
                                )
                            else:
                                _bulk_cash_row  = df_accs[df_accs['accounts_id'] == _mgr_linked_default]
                                _bulk_cash_name = _bulk_cash_row.iloc[0]['accounts_name'] if not _bulk_cash_row.empty else str(_mgr_linked_default)
                                st.info(f"Target cash account: **{_bulk_cash_name}**  (account default)")
                                if st.button(
                                    f"📋 Create all {_n_unlinked} missing linked cash transactions",
                                    key=f"inv_bulk_create_btn_{acc_id}",
                                    type="primary",
                                ):
                                    st.session_state[_bulk_key] = {
                                        "rows": [
                                            {
                                                "investments_id": int(r["investments_id"]),
                                                "action":         str(r["action"]),
                                                "total":          float(r.get("total_amount_acccur") or 0),
                                                "date":           r["date"],
                                                "sec_id":         r.get("securities_id"),
                                            }
                                            for _, r in _df_unlinked.iterrows()
                                        ],
                                        "cash_acc_id":   _mgr_linked_default,
                                        "cash_acc_name": _bulk_cash_name,
                                    }
                                    st.rerun()

                        # ── Bulk-create confirmation ───────────────────────────────
                        _pending_bulk = st.session_state.get(_bulk_key)
                        if _pending_bulk:
                            _pb_rows  = _pending_bulk["rows"]
                            _pb_count = len(_pb_rows)
                            _pb_cash  = _pending_bulk["cash_acc_name"]
                            st.warning(
                                f"⚠️ Please confirm: create **{_pb_count}** cash transaction(s) "
                                f"in **{_pb_cash}** and link them to the corresponding investment "
                                "entries.\n\n**This action cannot be undone automatically.**"
                            )
                            _bc1, _bc2, _ = st.columns([1, 1, 4])
                            with _bc1:
                                _bulk_confirmed = st.button(
                                    "✅ Yes, create all",
                                    type="primary",
                                    key=f"inv_bulk_create_yes_{acc_id}",
                                    width="stretch",
                                )
                            with _bc2:
                                if st.button("❌ Cancel", key=f"inv_bulk_create_no_{acc_id}", width="stretch"):
                                    del st.session_state[_bulk_key]
                                    st.rerun()

                            if _bulk_confirmed:
                                del st.session_state[_bulk_key]
                                _bc_cash_id  = _pending_bulk["cash_acc_id"]
                                _bc_created  = 0
                                _bc_errors: list = []
                                try:
                                    with get_db() as _conn_bc:
                                        _cur_bc = _conn_bc.cursor()
                                        for _row in _pb_rows:
                                            _bc_inv_id  = _row["investments_id"]
                                            _bc_action  = _row["action"]
                                            _bc_total   = _row["total"]
                                            _bc_dt      = _row["date"]
                                            _bc_sec_nm  = _full_sec_options.get(_row["sec_id"]) if _row["sec_id"] else None
                                            _bc_sign    = -abs(_bc_total) if _bc_action in _CASH_OUT_MGR else abs(_bc_total)
                                            try:
                                                _bc_payee_id = (
                                                    get_or_create_payee_id(_cur_bc, _bc_sec_nm)
                                                    if _bc_sec_nm else None
                                                )
                                                _cur_bc.execute(
                                                    """
                                                    INSERT INTO Transactions
                                                        (Accounts_Id, Date, Payees_Id, Description,
                                                         Total_Amount, Cleared,
                                                         Accounts_Id_Target, Total_Amount_Target,
                                                         Transfers_Id)
                                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                                                    RETURNING Transactions_Id
                                                    """,
                                                    (
                                                        _bc_cash_id, _bc_dt, _bc_payee_id,
                                                        _bc_sec_nm or _bc_action, _bc_sign, True,
                                                        acc_id, abs(_bc_total), None,
                                                    ),
                                                )
                                                _bc_tx_id = _cur_bc.fetchone()[0]
                                                _cur_bc.execute(
                                                    "UPDATE Investments SET Transactions_Id = %s "
                                                    "WHERE Investments_Id = %s",
                                                    (_bc_tx_id, _bc_inv_id),
                                                )
                                                _bc_created += 1
                                            except Exception as _bc_row_err:
                                                _bc_errors.append(f"Inv #{_bc_inv_id}: {_bc_row_err}")
                                        _conn_bc.commit()
                                    update_accounts_balances(_bc_cash_id)
                                    update_investment_balances()
                                    st.session_state.pop(_inv_orig_key, None)
                                    st.session_state.pop("df_accs", None)
                                    if _bc_errors:
                                        st.warning(f"⚠️ {len(_bc_errors)} error(s):")
                                        for _e in _bc_errors:
                                            st.caption(_e)
                                    if _bc_created:
                                        st.success(
                                            f"✅ Created {_bc_created} cash transaction(s) "
                                            f"in {_pb_cash} and linked."
                                        )
                                    st.rerun()
                                except Exception as _bc_outer_err:
                                    st.error(f"❌ Bulk create failed: {_bc_outer_err}")

                    st.divider()

                    # ── Individual transaction management ──────────────────────────
                    def _fmt_inv_row(r):
                        sec   = _full_sec_options.get(r['securities_id'], '—')
                        total = f"{r['total_amount_acccur']:,.2f}" if pd.notna(r.get('total_amount_acccur')) else '—'
                        icon  = "🔗" if pd.notna(r.get('transactions_id')) else "⚪"
                        d     = r['date'].date() if hasattr(r['date'], 'date') else r['date']
                        return f"{d} | {r['action']} | {sec} | {total} {icon}"

                    _link_opts = {int(r['investments_id']): _fmt_inv_row(r)
                                  for _, r in _df_linkable.iterrows()}

                    _sel_inv_id = st.selectbox(
                        "Select transaction  (🔗 = already linked  ⚪ = unlinked)",
                        options=list(_link_opts.keys()),
                        format_func=lambda x: _link_opts.get(x, str(x)),
                        key=f"inv_link_sel_{acc_id}",
                    )

                    _sel_row    = _df_linkable[_df_linkable['investments_id'] == _sel_inv_id].iloc[0]
                    _raw_tx_id  = _sel_row.get('transactions_id')
                    _existing_tx_id = (
                        None if (_raw_tx_id is None or pd.isna(_raw_tx_id))
                        else int(_raw_tx_id)
                    )

                    if _existing_tx_id:
                        # ── Already linked — show details & offer to remove ────────
                        with get_db() as _conn_lk:
                            _linked_tx = pd.read_sql(
                                """SELECT t.transactions_id, t.date, a.accounts_name,
                                          t.total_amount, t.description
                                   FROM Transactions t
                                   JOIN Accounts a ON t.accounts_id = a.accounts_id
                                   WHERE t.transactions_id = %s""",
                                _conn_lk, params=(_existing_tx_id,)
                            )
                        if not _linked_tx.empty:
                            _lr = _linked_tx.iloc[0]
                            st.success(
                                f"✅ Linked → **{_lr['accounts_name']}** | "
                                f"Date: {_lr['date']} | Amount: {_lr['total_amount']:,.2f}"
                            )
                            st.caption(
                                "Editing the transaction above and saving will automatically "
                                "sync the date and amount to this cash entry."
                            )
                            if st.button(
                                "🔓 Remove link (deletes the cash transaction)",
                                key=f"inv_unlink_{acc_id}_{_sel_inv_id}",
                                type="secondary",
                            ):
                                with get_db() as _conn_ul:
                                    _cur_ul = _conn_ul.cursor()
                                    # NULL the FK first — required before deleting the
                                    # referenced Transaction row (FK constraint).
                                    _cur_ul.execute(
                                        "UPDATE Investments SET transactions_id = NULL "
                                        "WHERE investments_id = %s",
                                        (_sel_inv_id,)
                                    )
                                    _cur_ul.execute(
                                        "DELETE FROM Transactions WHERE transactions_id = %s",
                                        (_existing_tx_id,)
                                    )
                                    _conn_ul.commit()
                                update_investment_balances()
                                st.session_state.pop(_inv_orig_key, None)
                                st.session_state.pop("df_accs", None)
                                st.success("Link removed.")
                                st.rerun()
                        else:
                            st.warning(
                                f"Linked transaction #{_existing_tx_id} not found "
                                f"(may have been deleted externally)."
                            )
                            if st.button(
                                "🗑 Clear stale reference",
                                key=f"inv_stale_{acc_id}_{_sel_inv_id}",
                                type="secondary",
                            ):
                                with get_db() as _conn_cs:
                                    _cur_cs = _conn_cs.cursor()
                                    _cur_cs.execute(
                                        "UPDATE Investments SET transactions_id = NULL "
                                        "WHERE investments_id = %s",
                                        (_sel_inv_id,)
                                    )
                                    _conn_cs.commit()
                                update_investment_balances()
                                st.session_state.pop(_inv_orig_key, None)
                                st.session_state.pop("df_accs", None)
                                st.rerun()

                    else:
                        # ── Not linked — offer to create a link ────────────────────
                        st.info("This transaction has no linked cash account entry.")

                        _ca_opts = {
                            int(r['accounts_id']):
                                f"{r['accounts_name']} ({r['accounts_balance']:,.2f})"
                            for _, r in df_accs.iterrows()
                            if r['accounts_type'] not in _INV_TYPES_MGR
                            and r['accounts_id'] != acc_id
                        }
                        _ca_ids = list(_ca_opts.keys())

                        if not _ca_ids:
                            st.warning("No eligible cash/bank accounts found.")
                        else:
                            _ca_default_idx = (
                                _ca_ids.index(_mgr_linked_default)
                                if _mgr_linked_default and _mgr_linked_default in _ca_ids
                                else 0
                            )
                            _link_target_id = st.selectbox(
                                "Cash account to link to",
                                options=_ca_ids,
                                format_func=lambda x: _ca_opts.get(x, str(x)),
                                index=_ca_default_idx,
                                key=f"inv_link_target_{acc_id}_{_sel_inv_id}",
                            )
                            if st.button(
                                "🔗 Create linked cash transaction",
                                key=f"inv_create_link_{acc_id}_{_sel_inv_id}",
                                type="primary",
                            ):
                                _action  = str(_sel_row['action'])
                                _total   = float(_sel_row.get('total_amount_acccur') or 0)
                                _dt      = _sel_row['date']
                                _sec_id  = _sel_row.get('securities_id')
                                _sec_nm  = _full_sec_options.get(_sec_id) if _sec_id else None
                                _cash_sign = -abs(_total) if _action in _CASH_OUT_MGR else abs(_total)
                                try:
                                    with get_db() as _conn_cl:
                                        _cur_cl = _conn_cl.cursor()
                                        _payee_id = (
                                            get_or_create_payee_id(_cur_cl, _sec_nm)
                                            if _sec_nm else None
                                        )
                                        _cur_cl.execute(
                                            """
                                            INSERT INTO Transactions
                                                (Accounts_Id, Date, Payees_Id, Description,
                                                 Total_Amount, Cleared,
                                                 Accounts_Id_Target, Total_Amount_Target,
                                                 Transfers_Id)
                                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                                            RETURNING Transactions_Id
                                            """,
                                            (
                                                _link_target_id, _dt, _payee_id,
                                                _sec_nm or _action, _cash_sign, True,
                                                acc_id, abs(_total), None,
                                            ),
                                        )
                                        _new_tx_id = _cur_cl.fetchone()[0]
                                        _cur_cl.execute(
                                            "UPDATE Investments SET Transactions_Id = %s "
                                            "WHERE Investments_Id = %s",
                                            (_new_tx_id, int(_sel_inv_id))
                                        )
                                        _conn_cl.commit()
                                    update_accounts_balances(_link_target_id)
                                    update_investment_balances()
                                    st.session_state.pop(_inv_orig_key, None)
                                    st.session_state.pop("df_accs", None)
                                    st.success(
                                        f"✅ Cash transaction #{_new_tx_id} created in "
                                        f"{_ca_opts[_link_target_id]} and linked."
                                    )
                                    st.rerun()
                                except Exception as _link_err:
                                    st.error(f"Error creating link: {_link_err}")


        with tab_edit_hold:
         #   st.subheader(f"Current Holdings: {selected_inv_acc['accounts_name']}")
            with get_db() as conn:
                df_h = pd.read_sql(f"SELECT Holdings_Id, Accounts_Id, Securities_Id, Quantity, Simple_Avg_Price, Fifo_Avg_Price, Staking FROM Holdings WHERE Accounts_Id = {acc_id}", conn)

            # Creation of a new column with Status icons
            def get_status_icon(q):
                if q == 0:
                    return "🔵" # Blue for zero
                elif q < 0:
                    return "🔴" # Red for negative (short)
                return "🟢"     # Green for positive

            # Addition of the Status column at the beginning of the DataFrame
            df_h.insert(0, "Status", df_h['quantity'].apply(get_status_icon))
            
            edited_h = st.data_editor(
                df_h, 
            #    key=f"inv_h_editor_{inv_acc_id}",
                key="inv_holdings",
                width="stretch",
                column_config={
                    "Status": st.column_config.TextColumn("Status", width="small", disabled=True), # Locked column for status icons
                    # Hiding the IDs by setting them to None
                    "holdings_id": None,
                    "accounts_id": None,                
                    "securities_id": st.column_config.SelectboxColumn(
                        "Security",
                        options=list(sec_options.keys()),
                        format_func=lambda x: sec_options.get(x, "NO SECURITY"),
                        width="large"
                    ),
                    # Format numbers
                    "quantity": st.column_config.NumberColumn("Quantity", format="%,.8f"),
                    "simple_avg_price": st.column_config.NumberColumn("Simple Avg Price", format="%,.4f"),
                    "fifo_avg_price": st.column_config.NumberColumn("FIFO Avg Price", format="%,.4f"),
                    "staking": st.column_config.CheckboxColumn("Staking", help="When enabled, a quantity increase auto-creates a Reinvest entry for the difference"),
                },
                hide_index=True
            )
            _copy_h_edit = df_h.drop(columns=["holdings_id", "accounts_id", "Status"], errors="ignore").copy()
            _copy_h_edit["securities_id"] = _copy_h_edit["securities_id"].map(sec_options).fillna("")
            _copy_h_edit = _copy_h_edit.rename(columns={"securities_id": "Security"})
            copy_df_button(_copy_h_edit, key=f"dl_reg_holdings_edit_{acc_id}")

            if not edited_h.equals(df_h):
                save_df = edited_h.drop(columns=["Status"])
                orig_df = df_h.drop(columns=["Status"])

                # Detect staking holdings where quantity increased → queue Reinvest entries
                staking_entries = []
                for _, row in save_df.iterrows():
                    if row.get('staking'):
                        orig_rows = orig_df[orig_df['holdings_id'] == row['holdings_id']]
                        if not orig_rows.empty:
                            old_qty = float(orig_rows.iloc[0]['quantity'])
                            new_qty = float(row['quantity'])
                            diff = new_qty - old_qty
                            if diff > 0:
                                staking_entries.append({
                                    'accounts_id':    int(row['accounts_id']),
                                    'securities_id':  int(row['securities_id']),
                                    'quantity':       diff,
                                    'price_per_share': None,  # auto-populated from Historical_Prices in insert_staking_reinvest
                                    'date':           datetime.today().date(),
                                })

                if staking_entries:
                    n = len(staking_entries)
                    st.info(f"📊 Staking: will create {n} Reinvest entr{'y' if n == 1 else 'ies'} for the quantity increase{'s' if n > 1 else ''}")

                if st.button("💾 Save Holdings"):
                    if staking_entries:
                        insert_staking_reinvest(staking_entries)
                    execute_db_save(orig_df, save_df, "Holdings", "holdings_id")

            _render_security_transactions(acc_id, sec_options, "edit")

        if st.button("🚀 Update Holdings"):
            with st.spinner("Processing..."):
                update_holdings()
                st.balloons()