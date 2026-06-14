"""Recurring Templates — manage schedule definitions and review draft transactions."""

import streamlit as st
import streamlit.components.v1 as _components
import pandas as pd
from datetime import date, timedelta
from database.connection import get_connection
from database.crud import (
    get_recurring_templates,
    get_template_splits,
    save_recurring_template,
    delete_recurring_template,
    create_template_from_transaction,
    generate_draft_transactions,
    confirm_draft_transaction,
    get_draft_transactions,
    get_transaction_splits,
    save_draft_transaction,
    get_confirmed_from_templates,
)

PERIODICITIES = ['Daily', 'Weekly', 'Biweekly', 'Monthly', 'Quarterly', 'Semiannually', 'Annually']

# ── Prevent Enter from submitting the form when focus is on a number input ──
_NO_ENTER_SUBMIT_JS = """
<script>
(function() {
    function blockEnterOnNumbers(e) {
        if (e.key === 'Enter') {
            var el = e.target;
            if (el && (el.type === 'number' || el.inputMode === 'decimal' || el.inputMode === 'numeric')) {
                e.preventDefault();
                e.stopPropagation();
            }
        }
    }
    // Attach on the parent document (Streamlit renders in an iframe)
    var doc = window.parent ? window.parent.document : document;
    doc.removeEventListener('keydown', blockEnterOnNumbers, true);
    doc.addEventListener('keydown', blockEnterOnNumbers, true);
})();
</script>
"""

def _inject_no_enter_submit():
    """Inject a JS listener that prevents Enter from submitting forms on number inputs."""
    _components.html(_NO_ENTER_SUBMIT_JS, height=0, scrolling=False)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_accounts():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT Accounts_Id, Accounts_Name FROM Accounts ORDER BY Accounts_Name")
            return {r[1]: r[0] for r in cur.fetchall()}
    finally:
        conn.close()


def _load_payees():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT Payees_Id, Payees_Name FROM Payees ORDER BY Payees_Name")
            return {r[1]: r[0] for r in cur.fetchall()}
    finally:
        conn.close()


def _load_categories():
    """Return {full_path: categories_id} with recursive parent : child names."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                WITH RECURSIVE ch AS (
                    SELECT Categories_Id, Categories_Name::TEXT AS full_path
                    FROM   Categories
                    WHERE  Categories_Id_Parent IS NULL
                    UNION ALL
                    SELECT c.Categories_Id, ch.full_path || ' : ' || c.Categories_Name
                    FROM   Categories c
                    JOIN   ch ON c.Categories_Id_Parent = ch.Categories_Id
                )
                SELECT Categories_Id, full_path FROM ch ORDER BY full_path
            """)
            return {r[1]: r[0] for r in cur.fetchall()}
    finally:
        conn.close()


def _load_transactions_recent():
    """Return last 6 months of confirmed transactions for the 'create from tx' picker."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT t.Transactions_Id, t.Date, a.Accounts_Name, p.Payees_Name, t.Description, t.Total_Amount
                FROM Transactions t
                JOIN Accounts a ON a.Accounts_Id = t.Accounts_Id
                LEFT JOIN Payees p ON p.Payees_Id = t.Payees_Id
                WHERE t.Is_Draft = FALSE
                  AND t.Date >= CURRENT_DATE - INTERVAL '6 months'
                ORDER BY t.Date DESC
                LIMIT 500
            """)
            rows = cur.fetchall()
        return pd.DataFrame(rows, columns=['transactions_id','date','accounts_name','payees_name','description','total_amount'])
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Template editor form
# ─────────────────────────────────────────────────────────────────────────────

def _template_form(accounts: dict, payees: dict, categories: dict,
                   existing: dict | None = None, existing_splits: list | None = None):
    """Render the template header + splits editor. Returns (template_dict, splits_list) or None."""

    is_edit = existing is not None
    prefix = "te_edit" if is_edit else "te_new"

    _inject_no_enter_submit()

    with st.form(key=f"{prefix}_form", border=True):
        st.subheader("✏️ Edit Template" if is_edit else "➕ New Template")

        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input(
                "Template Name *",
                value=existing.get('name', '') if is_edit else '',
                key=f"{prefix}_name",
            )
            acc_name = st.selectbox(
                "Account *",
                options=list(accounts.keys()),
                index=list(accounts.keys()).index(existing['accounts_name'])
                      if is_edit and existing.get('accounts_name') in accounts else 0,
                key=f"{prefix}_acc",
            )
            header_amount = st.number_input(
                "Amount",
                value=float(existing.get('total_amount') or 0.0) if is_edit else 0.0,
                step=0.01, format="%.2f",
                key=f"{prefix}_amount",
                help="For transfers: authoritative amount. For split transactions: used as default; final total is derived from splits.",
            )
            periodicity = st.selectbox(
                "Periodicity",
                options=PERIODICITIES,
                index=PERIODICITIES.index(existing['periodicity'])
                      if is_edit and existing.get('periodicity') in PERIODICITIES
                      else PERIODICITIES.index('Monthly'),
                key=f"{prefix}_period",
            )
            next_due = st.date_input(
                "Next Due Date *",
                value=existing['next_due_date'] if is_edit and existing.get('next_due_date') else date.today() + timedelta(days=30),
                key=f"{prefix}_due",
            )

        with c2:
            payee_options = ['— None —'] + list(payees.keys())
            current_payee = existing.get('payees_name') if is_edit else None
            payee_idx = payee_options.index(current_payee) if current_payee in payee_options else 0
            payee_sel = st.selectbox("Payee", options=payee_options, index=payee_idx, key=f"{prefix}_payee")

            description = st.text_input(
                "Description",
                value=existing.get('description', '') or '' if is_edit else '',
                key=f"{prefix}_desc",
            )
            end_date_enabled = st.checkbox(
                "Set end date",
                value=bool(existing.get('end_date')) if is_edit else False,
                key=f"{prefix}_end_enabled",
            )
            end_date = None
            if end_date_enabled:
                end_date = st.date_input(
                    "End Date",
                    value=existing['end_date'] if is_edit and existing.get('end_date') else date.today() + timedelta(days=365),
                    key=f"{prefix}_end",
                )

            # Target account (for transfer templates)
            target_options = ['— None —'] + list(accounts.keys())
            current_target = None
            if is_edit and existing.get('accounts_id_target'):
                # Reverse-lookup name from id
                for n, i in accounts.items():
                    if i == existing['accounts_id_target']:
                        current_target = n
                        break
            target_idx = target_options.index(current_target) if current_target in target_options else 0
            target_sel = st.selectbox("Transfer to Account", options=target_options, index=target_idx, key=f"{prefix}_target")

        c3, c4 = st.columns(2)
        with c3:
            auto_confirm = st.checkbox(
                "Auto-confirm (bypass review queue)",
                value=bool(existing.get('auto_confirm', False)) if is_edit else False,
                key=f"{prefix}_auto",
                help="Enable for committed installments — generated transactions will be confirmed immediately.",
            )
        with c4:
            active = st.checkbox(
                "Active",
                value=bool(existing.get('active', True)) if is_edit else True,
                key=f"{prefix}_active",
            )

        # ── Splits sub-editor ────────────────────────────────────────────────
        st.markdown("**Splits** *(required for non-transfer transactions; optional for transfers)*")
        cat_options = list(categories.keys())

        # Seed from existing splits or provide one blank row
        initial_splits = []
        if is_edit and existing_splits:
            for s in existing_splits:
                initial_splits.append({
                    'Category': s.get('categories_name') or '',
                    'Amount': float(s.get('amount') or 0),
                    'Memo': s.get('memo') or '',
                })
        if not initial_splits:
            initial_splits = [{'Category': '', 'Amount': 0.0, 'Memo': ''}]

        splits_df = pd.DataFrame(initial_splits)
        edited_splits = st.data_editor(
            splits_df,
            num_rows="dynamic",
            width='stretch',
            key=f"{prefix}_splits",
            column_config={
                'Category': st.column_config.SelectboxColumn(
                    'Category', options=cat_options, required=True
                ),
                'Amount': st.column_config.NumberColumn(
                    'Amount', format="%.2f", required=True
                ),
                'Memo': st.column_config.TextColumn('Memo'),
            },
        )

        submitted = st.form_submit_button("💾 Save Template", type="primary", use_container_width=True)

    if not submitted:
        return None, None

    # ── Validate ────────────────────────────────────────────────────────────
    is_transfer = target_sel != '— None —'
    errors = []
    if not name.strip():
        errors.append("Template Name is required.")
    if not is_transfer and (edited_splits.empty or edited_splits['Category'].eq('').all()):
        errors.append("At least one split with a category is required (or set a Transfer Account for transfer templates).")
    if errors:
        for e in errors:
            st.error(e)
        return None, None

    has_splits = not edited_splits.empty and not edited_splits['Category'].eq('').all()
    # Transfers use the explicit header amount; split-based transactions sum their splits
    # (falling back to header_amount if splits are empty or sum to zero).
    if is_transfer or not has_splits:
        total = float(header_amount)
    else:
        splits_sum = float(edited_splits['Amount'].fillna(0).sum())
        total = splits_sum if splits_sum != 0 else float(header_amount)

    template = {
        'templates_id': existing.get('templates_id') if is_edit else None,
        'name':              name.strip(),
        'accounts_id':       accounts[acc_name],
        'accounts_name':     acc_name,
        'payees_id':         payees.get(payee_sel) if payee_sel != '— None —' else None,
        'description':       description.strip() or None,
        'total_amount':      total,
        'periodicity':       periodicity,
        'next_due_date':     next_due,
        'end_date':          end_date,
        'auto_confirm':      auto_confirm,
        'active':            active,
        'accounts_id_target': accounts.get(target_sel) if target_sel != '— None —' else None,
    }

    splits = []
    for _, row in edited_splits.iterrows():
        cat = row.get('Category', '')
        if cat and cat in categories:
            splits.append({
                'categories_id': categories[cat],
                'amount':        float(row.get('Amount') or 0),
                'memo':          str(row.get('Memo') or '').strip() or None,
            })

    return template, splits


# ─────────────────────────────────────────────────────────────────────────────
# Tab 1 — Templates
# ─────────────────────────────────────────────────────────────────────────────

def _render_templates_tab(accounts, payees, categories):
    # ── Action buttons ───────────────────────────────────────────────────────
    col_new, col_from_tx, _ = st.columns([1, 1.4, 4])
    with col_new:
        if st.button("➕ New Template", use_container_width=True):
            st.session_state['rt_mode'] = 'new'
            st.session_state.pop('rt_edit_id', None)
    with col_from_tx:
        if st.button("📋 Create from Transaction", use_container_width=True):
            st.session_state['rt_mode'] = 'from_tx'
            st.session_state.pop('rt_edit_id', None)

    mode = st.session_state.get('rt_mode')

    # ── Create from existing transaction ────────────────────────────────────
    if mode == 'from_tx':
        st.markdown("---")
        st.subheader("Create Template from Existing Transaction")
        df_recent = _load_transactions_recent()
        if df_recent.empty:
            st.info("No recent transactions found.")
        else:
            df_recent['label'] = (
                df_recent['date'].astype(str) + '  |  ' +
                df_recent['accounts_name'].fillna('') + '  |  ' +
                df_recent['payees_name'].fillna('') + '  |  ' +
                df_recent['description'].fillna('') + '  |  ' +
                df_recent['total_amount'].apply(lambda x: f"{x:,.2f}" if pd.notna(x) else '')
            )
            sel_label = st.selectbox("Select transaction", df_recent['label'].tolist(), key="rt_from_tx_sel")
            sel_row = df_recent[df_recent['label'] == sel_label].iloc[0]
            st.caption(f"Transaction ID: {sel_row['transactions_id']}")

            if st.button("Create Template from this Transaction", type="primary"):
                try:
                    new_tid = create_template_from_transaction(int(sel_row['transactions_id']))
                    st.success(f"Template #{new_tid} created. Edit it below.")
                    st.session_state['rt_mode'] = 'edit'
                    st.session_state['rt_edit_id'] = new_tid
                    st.rerun()
                except Exception as exc:
                    st.error(f"Failed: {exc}")

        if st.button("Cancel", key="rt_from_tx_cancel"):
            st.session_state.pop('rt_mode', None)
            st.rerun()

    # ── New template form ────────────────────────────────────────────────────
    elif mode == 'new':
        st.markdown("---")
        template, splits = _template_form(accounts, payees, categories)
        if template is not None:
            try:
                tid = save_recurring_template(template, splits)
                st.success(f"Template #{tid} saved.")
                st.session_state.pop('rt_mode', None)
                st.rerun()
            except Exception as exc:
                st.error(f"Save failed: {exc}")
        if st.button("Cancel", key="rt_new_cancel"):
            st.session_state.pop('rt_mode', None)
            st.rerun()

    # ── Templates grid ───────────────────────────────────────────────────────
    st.markdown("---")
    df = get_recurring_templates()
    if df.empty:
        st.info("No recurring templates yet. Create one with the buttons above.")
        return

    # Display columns
    display_cols = ['templates_id','name','accounts_name','payees_name','periodicity',
                    'next_due_date','total_amount','auto_confirm','active']
    df_display = df[display_cols].copy()
    df_display.columns = ['ID','Name','Account','Payee','Periodicity','Next Due','Amount','Auto-Confirm','Active']

    st.dataframe(
        df_display,
        width='stretch',
        hide_index=True,
        column_config={
            'ID':           st.column_config.NumberColumn('ID', width='small'),
            'Amount':       st.column_config.NumberColumn('Amount', format="%.2f"),
            'Next Due':     st.column_config.DateColumn('Next Due'),
            'Auto-Confirm': st.column_config.CheckboxColumn('Auto-Confirm'),
            'Active':       st.column_config.CheckboxColumn('Active'),
        },
    )

    # ── Per-template actions ─────────────────────────────────────────────────
    st.markdown("**Select a template to edit or delete:**")
    template_labels = df.apply(
        lambda r: f"#{int(r['templates_id'])} — {r['name']} ({r['accounts_name']})", axis=1
    ).tolist()

    sel_label = st.selectbox("Template", template_labels, key="rt_sel_template", label_visibility="collapsed")
    sel_idx   = template_labels.index(sel_label)
    sel_row   = df.iloc[sel_idx]
    sel_id    = int(sel_row['templates_id'])

    col_edit, col_del = st.columns([1, 1])
    with col_edit:
        if st.button("✏️ Edit", use_container_width=True, key="rt_edit_btn"):
            st.session_state['rt_mode'] = 'edit'
            st.session_state['rt_edit_id'] = sel_id
            st.rerun()
    with col_del:
        if st.button("🗑️ Delete", use_container_width=True, key="rt_del_btn", type="secondary"):
            st.session_state['rt_confirm_delete'] = sel_id

    if st.session_state.get('rt_confirm_delete') == sel_id:
        st.warning(f"Delete template **{sel_row['name']}**? Any pending drafts will be unlinked.")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Yes, delete", type="primary", key="rt_del_confirm"):
                try:
                    delete_recurring_template(sel_id)
                    st.success("Template deleted.")
                    st.session_state.pop('rt_confirm_delete', None)
                    st.session_state.pop('rt_mode', None)
                    st.rerun()
                except Exception as exc:
                    st.error(f"Delete failed: {exc}")
        with c2:
            if st.button("Cancel", key="rt_del_cancel"):
                st.session_state.pop('rt_confirm_delete', None)
                st.rerun()

    # ── Edit form ────────────────────────────────────────────────────────────
    if mode == 'edit' and st.session_state.get('rt_edit_id') == sel_id:
        st.markdown("---")
        existing = sel_row.to_dict()
        existing_splits_df = get_template_splits(sel_id)
        existing_splits = existing_splits_df.to_dict('records') if not existing_splits_df.empty else []

        template, splits = _template_form(accounts, payees, categories, existing, existing_splits)
        if template is not None:
            try:
                save_recurring_template(template, splits)
                st.success("Template updated.")
                st.session_state.pop('rt_mode', None)
                st.session_state.pop('rt_edit_id', None)
                st.rerun()
            except Exception as exc:
                st.error(f"Save failed: {exc}")
        if st.button("Cancel edit", key="rt_edit_cancel"):
            st.session_state.pop('rt_mode', None)
            st.session_state.pop('rt_edit_id', None)
            st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# Tab 2 — Pending Review
# ─────────────────────────────────────────────────────────────────────────────

def _render_pending_tab(accounts: dict, payees: dict, categories: dict):
    col_gen, col_bulk, _ = st.columns([1.2, 1.2, 4])
    with col_gen:
        if st.button("🔄 Generate Due Drafts", use_container_width=True,
                     help="Create draft transactions for all templates due today or earlier"):
            try:
                n = generate_draft_transactions()
                if n:
                    st.success(f"{n} draft(s) generated.")
                else:
                    st.info("Nothing due — all templates are up to date.")
                st.rerun()
            except Exception as exc:
                st.error(f"Generation failed: {exc}")

    df = get_draft_transactions()

    if df.empty:
        st.info("No pending drafts. Use **Generate Due Drafts** or wait for the scheduler.")
        return

    with col_bulk:
        if st.button("✅ Confirm All", use_container_width=True, type="primary"):
            errors = []
            for tx_id in df['transactions_id'].tolist():
                try:
                    confirm_draft_transaction(int(tx_id))
                except Exception as exc:
                    errors.append(str(exc))
            if errors:
                st.error(f"Some confirmations failed: {'; '.join(errors)}")
            else:
                st.success(f"All {len(df)} drafts confirmed.")
            st.cache_data.clear()
            st.session_state.pop('df_accs', None)
            st.rerun()

    st.markdown(f"**{len(df)} pending draft(s)**")

    for _, row in df.iterrows():
        tx_id       = int(row['transactions_id'])
        tx_date     = row['date']
        amount      = row.get('total_amount')
        tmpl_name   = row.get('template_name') or '—'
        periodicity = row.get('periodicity') or ''

        with st.expander(
            f"📅 {tx_date}  |  {row.get('accounts_name','')}  |  "
            f"{row.get('payees_name') or '—'}  |  "
            f"**{float(amount):,.2f}** *(from: {tmpl_name})*",
            expanded=False,
        ):
            # ── Header fields ─────────────────────────────────────────────
            h1, h2, h3 = st.columns(3)
            with h1:
                new_date = st.date_input("Date", value=tx_date, key=f"pd_date_{tx_id}")
            with h2:
                new_amount = st.number_input(
                    "Amount", value=float(amount) if pd.notna(amount) else 0.0,
                    step=0.01, format="%.2f", key=f"pd_amt_{tx_id}"
                )
            with h3:
                new_desc = st.text_input(
                    "Description",
                    value=row.get('description') or '',
                    key=f"pd_desc_{tx_id}"
                )

            h4, h5, h6 = st.columns(3)
            with h4:
                acc_options = ['— keep current —'] + list(accounts.keys())
                new_acc_sel = st.selectbox(
                    "Account", options=acc_options,
                    index=acc_options.index(row.get('accounts_name'))
                          if row.get('accounts_name') in acc_options else 0,
                    key=f"pd_acc_{tx_id}"
                )
            with h5:
                payee_options = ['— keep current —'] + list(payees.keys())
                new_payee_sel = st.selectbox(
                    "Payee", options=payee_options,
                    index=payee_options.index(row.get('payees_name'))
                          if row.get('payees_name') in payee_options else 0,
                    key=f"pd_payee_{tx_id}"
                )
            with h6:
                st.caption(f"🔁 Template: **{tmpl_name}** · {periodicity}")

            # ── Splits editor ─────────────────────────────────────────────
            st.markdown("**Splits**")
            splits_df_raw = get_transaction_splits(tx_id)
            cat_options   = list(categories.keys())

            if splits_df_raw.empty:
                initial_splits = [{'Category': '', 'Amount': float(amount) if pd.notna(amount) else 0.0, 'Memo': ''}]
            else:
                initial_splits = [
                    {
                        'Category': r.get('categories_name') or '',
                        'Amount':   float(r.get('amount') or 0),
                        'Memo':     r.get('memo') or '',
                    }
                    for _, r in splits_df_raw.iterrows()
                ]

            edited_splits = st.data_editor(
                pd.DataFrame(initial_splits),
                num_rows="dynamic",
                width='stretch',
                key=f"pd_splits_{tx_id}",
                column_config={
                    'Category': st.column_config.SelectboxColumn(
                        'Category', options=cat_options
                    ),
                    'Amount': st.column_config.NumberColumn('Amount', format="%.2f"),
                    'Memo':   st.column_config.TextColumn('Memo'),
                },
            )

            # ── Action buttons ────────────────────────────────────────────
            ba1, ba2, ba3 = st.columns([1.2, 1.2, 4])
            with ba1:
                save_and_confirm = st.button(
                    "✅ Save & Confirm", key=f"pd_confirm_{tx_id}",
                    type="primary", use_container_width=True
                )
            with ba2:
                save_only = st.button(
                    "💾 Save", key=f"pd_apply_{tx_id}",
                    use_container_width=True,
                    help="Save changes without confirming"
                )
            with ba3:
                discard = st.button(
                    "🗑️ Discard draft", key=f"pd_del_{tx_id}",
                    use_container_width=True
                )

            if save_and_confirm or save_only:
                # Resolve account / payee selections
                resolved_acc_id = (
                    accounts[new_acc_sel]
                    if new_acc_sel != '— keep current —'
                    else int(row['accounts_id'])
                )
                resolved_payee_id = (
                    payees[new_payee_sel]
                    if new_payee_sel != '— keep current —'
                    else (row.get('payees_id') if pd.notna(row.get('payees_id')) else None)
                )
                total = float(edited_splits['Amount'].fillna(0).sum()) or float(new_amount)
                splits_out = [
                    {
                        'categories_id': categories[r['Category']],
                        'amount': float(r['Amount'] or 0),
                        'memo':   str(r.get('Memo') or '').strip() or None,
                    }
                    for _, r in edited_splits.iterrows()
                    if r.get('Category') and r['Category'] in categories
                ]
                try:
                    save_draft_transaction(
                        tx_id,
                        fields={
                            'date':               new_date,
                            'total_amount':       total,
                            'description':        new_desc.strip() or None,
                            'accounts_id':        resolved_acc_id,
                            'payees_id':          resolved_payee_id,
                            'accounts_id_target': row.get('accounts_id_target') if pd.notna(row.get('accounts_id_target')) else None,
                        },
                        splits=splits_out,
                    )
                    if save_and_confirm:
                        confirm_draft_transaction(tx_id)
                        st.cache_data.clear()
                        st.session_state.pop('df_accs', None)
                        st.success("Saved and confirmed.")
                    else:
                        st.success("Changes saved.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Failed: {exc}")

            if discard:
                conn = get_connection()
                try:
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM Splits WHERE Transactions_Id = %s", (tx_id,))
                        cur.execute(
                            "DELETE FROM Transactions WHERE Transactions_Id = %s AND Is_Draft = TRUE",
                            (tx_id,)
                        )
                    conn.commit()
                    st.success("Draft discarded.")
                    st.rerun()
                except Exception as exc:
                    conn.rollback()
                    st.error(f"Failed: {exc}")
                finally:
                    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Tab 3 — History
# ─────────────────────────────────────────────────────────────────────────────

def _render_history_tab():
    df = get_confirmed_from_templates()
    if df.empty:
        st.info("No confirmed transactions from templates yet.")
        return

    st.caption(f"Showing last {len(df)} confirmed transactions generated from recurring templates.")
    df_display = df.copy()
    df_display.columns = ['ID','Date','Account','Payee','Description','Amount','Template','Periodicity']
    st.dataframe(
        df_display,
        width='stretch',
        hide_index=True,
        column_config={
            'ID':     st.column_config.NumberColumn('ID', width='small'),
            'Date':   st.column_config.DateColumn('Date'),
            'Amount': st.column_config.NumberColumn('Amount', format="%.2f"),
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def render_recurring():
    st.title("🔁 Recurring Transactions")

    # Kick off draft generation on every page load (idempotent)
    try:
        n = generate_draft_transactions()
        if n:
            st.toast(f"✅ {n} recurring draft(s) generated automatically.", icon="🔁")
    except Exception:
        pass  # non-fatal — user can trigger manually

    accounts   = _load_accounts()
    payees     = _load_payees()
    categories = _load_categories()

    tab_tmpl, tab_pending, tab_history = st.tabs(["📋 Templates", "⏳ Pending Review", "✅ History"])

    with tab_tmpl:
        _render_templates_tab(accounts, payees, categories)

    with tab_pending:
        _render_pending_tab(accounts, payees, categories)

    with tab_history:
        _render_history_tab()
