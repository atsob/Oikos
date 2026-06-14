import streamlit as st
import pandas as pd
import plotly.express as px
from ui.components import format_qty_display, color_negative_red, style_qty_display, copy_df_button
from database.queries import (get_hist_net_worth_data, get_transaction_anomalies, get_weekly_summaries,
                               get_all_accounts_for_nwr, get_nwr_account_selection,
                               get_savings_rate_data, get_cash_flow_forecast, get_category_hierarchy,
                               check_triggered_alerts)
from database.connection import get_connection
from database.crud import update_accounts_balances, update_holdings, update_investment_balances, update_pension_balances, save_nwr_account_selection, get_draft_transactions, confirm_draft_transaction
from datetime import datetime
from ai.weekly_summary import run as run_weekly_summary
from ai.monthly_summary import run as run_monthly_summary

def render_ai_monthly_summaries_ui(conn):
#    st.header("Monthly AI Financial Summary")

    # 1. Βρίσκουμε την 1η του προηγούμενου μήνα
    # Αν σήμερα είναι 8 Μαΐου, το last_month_start θα γίνει 2026-04-01
    last_month_start = (pd.Timestamp.now() - pd.offsets.MonthEnd(1)).replace(day=1)

    # 2. Δημιουργούμε τη λίστα ξεκινώντας από εκεί και πηγαίνοντας προς τα πίσω
    month_options = [
        (last_month_start - pd.offsets.DateOffset(months=i)).strftime('%Y-%m-01') 
        for i in range(24)
    ]

    # Selectbox για επιλογή μήνα
    selected_month = st.selectbox(
        "Select Month", 
        options=month_options,
        format_func=lambda x: pd.to_datetime(x).strftime('%B %Y'),
        index=0 # Προεπιλογή ο πιο πρόσφατος (Απρίλιος)
    )

    # 2. Έλεγχος αν υπάρχει ήδη summary στη βάση
    # Υποθέτουμε ότι ο πίνακας έχει στήλες 'month' και 'summary_text'
    existing_data = pd.read_sql(
        "SELECT summary_text FROM ai_monthly_summaries WHERE month_start = %s", 
        conn, 
        params=(selected_month,)
    )
#    conn.close()

    # 3. Εμφάνιση αποτελέσματος ή κουμπί εκτέλεσης
    if not existing_data.empty:
        st.success(f"Summary found for {pd.to_datetime(selected_month).strftime('%B %Y')}:")
        st.markdown(existing_data.iloc[0]['summary_text'])
        
        # Προαιρετικά: Κουμπί για επανεκτέλεση (Regenerate)
        if st.button("Regenerate Summary"):
            with st.spinner("Generating new summary..."):
                run_monthly_summary(target_month=selected_month)
                st.rerun()
    else:
        st.warning("No summary found for this month.")
        if st.button("Generate AI Summary Now"):
            with st.spinner("Analyzing financial data and generating summary..."):
                try:
                    run_monthly_summary(target_month=selected_month)
                    st.success("Summary generated successfully!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")


def _invalidate_balance_caches():
    """Clear every layer that caches account balances.

    Two layers need to be purged after a balance-changing action:
      1. @st.cache_data  — used by queries.py (Net Worth, reports, etc.)
      2. st.session_state.df_accs — used by register.py to display account
         balances in the account selector; it is populated on demand and
         will be re-fetched from the DB on the next Register page load.
    """
    st.cache_data.clear()
    st.session_state.pop('df_accs', None)


def _render_pending_review_banner():
    """Show pending draft transactions at the top of the dashboard with inline actions."""
    df = get_draft_transactions()
    if df.empty:
        return

    n = len(df)
    total = df['total_amount'].apply(pd.to_numeric, errors='coerce').sum()

    with st.expander(
        f"⏳ **{n} transaction{'s' if n != 1 else ''} pending review** — total {total:+,.2f}",
        expanded=True,
    ):
        # ── Bulk confirm ────────────────────────────────────────────────────
        col_bulk, col_info = st.columns([1, 5])
        with col_bulk:
            if st.button("✅ Confirm All", type="primary", key="dash_confirm_all", use_container_width=True):
                errors = []
                for tx_id in df['transactions_id'].tolist():
                    try:
                        confirm_draft_transaction(int(tx_id))
                    except Exception as exc:
                        errors.append(str(exc))
                if errors:
                    st.error(f"Some confirmations failed: {'; '.join(errors)}")
                else:
                    st.success(f"All {n} drafts confirmed.")
                _invalidate_balance_caches()
                st.rerun()
        with col_info:
            st.caption("Review and confirm or discard each pending transaction below, or confirm all at once.")

        st.divider()

        # ── Per-row cards ───────────────────────────────────────────────────
        for _, row in df.iterrows():
            tx_id       = int(row['transactions_id'])
            tx_date     = row['date']
            account     = row.get('accounts_name', '')
            payee       = row.get('payees_name') or '—'
            desc        = row.get('description') or '—'
            amount      = row.get('total_amount')
            tmpl_name   = row.get('template_name') or '—'
            splits_sum  = row.get('splits_summary') or '—'

            c_date, c_amt, c_acc, c_payee, c_splits, c_tmpl, c_confirm, c_discard = st.columns(
                [1.5, 1.1, 1.4, 1.4, 2.5, 1.6, 0.9, 0.9]
            )

            with c_date:
                new_date = st.date_input(
                    "Date", value=tx_date, format="DD/MM/YYYY",
                    key=f"dash_date_{tx_id}", label_visibility="collapsed"
                )
            with c_amt:
                new_amount = st.number_input(
                    "Amount", value=float(amount) if pd.notna(amount) else 0.0,
                    step=0.01, format="%.2f",
                    key=f"dash_amt_{tx_id}", label_visibility="collapsed"
                )
            with c_acc:
                st.markdown(f"<small>{account}</small>", unsafe_allow_html=True)
            with c_payee:
                st.markdown(f"<small>{payee}</small>", unsafe_allow_html=True)
            with c_splits:
                st.markdown(f"<small>{splits_sum}</small>", unsafe_allow_html=True)
            with c_tmpl:
                st.markdown(f"<small>🔁 {tmpl_name}</small>", unsafe_allow_html=True)
            with c_confirm:
                if st.button("✅", key=f"dash_ok_{tx_id}", help=f"Confirm — {desc}", use_container_width=True):
                    from database.connection import get_connection as _gc
                    conn2 = _gc()
                    try:
                        with conn2.cursor() as cur:
                            cur.execute(
                                "UPDATE Transactions SET Date = %s, Total_Amount = %s WHERE Transactions_Id = %s",
                                (new_date, new_amount, tx_id)
                            )
                        conn2.commit()
                    finally:
                        conn2.close()
                    confirm_draft_transaction(tx_id)
                    _invalidate_balance_caches()
                    st.rerun()
            with c_discard:
                if st.button("🗑️", key=f"dash_del_{tx_id}", help="Discard draft", use_container_width=True):
                    from database.connection import get_connection as _gc
                    conn2 = _gc()
                    try:
                        with conn2.cursor() as cur:
                            cur.execute("DELETE FROM Splits WHERE Transactions_Id = %s", (tx_id,))
                            cur.execute(
                                "DELETE FROM Transactions WHERE Transactions_Id = %s AND Is_Draft = TRUE",
                                (tx_id,)
                            )
                        conn2.commit()
                    finally:
                        conn2.close()
                    _invalidate_balance_caches()
                    st.rerun()


def render_dashboard(conn):
    """Render the Dashboard page."""
    st.title("🏛 Net Worth")

    # ── Alert banners ─────────────────────────────────────────────────────
    try:
        _triggered = check_triggered_alerts()
        for _alert in _triggered:
            if _alert.get('level') == 'error':
                st.error(_alert['message'])
            else:
                st.warning(_alert['message'])
    except Exception:
        pass  # alerts are best-effort; never block the dashboard

    # ── Pending review banner ─────────────────────────────────────────────
    _render_pending_review_banner()

    # ── Actionable spending insights ──────────────────────────────────────
    try:
        from ui.insights import render_spending_insights
        render_spending_insights()
    except Exception:
        pass  # insights are best-effort; never block the dashboard

    # ── Account selection & options ───────────────────────────────────────
    _DASH_NW_KEY = 'dashboard_nw_account_ids'
    df_accounts  = get_all_accounts_for_nwr()
    all_ids      = df_accounts['accounts_id'].tolist()

    saved_ids = get_nwr_account_selection(_DASH_NW_KEY)
    init_sel  = set(saved_ids) if saved_ids is not None else set(all_ids)

    df_sel = df_accounts.copy()
    df_sel.insert(0, 'Include', df_sel['accounts_id'].isin(init_sel))

    with st.expander("⚙️ Account Selection & Options", expanded=False):
        edited_accs = st.data_editor(
            df_sel.rename(columns={'accounts_name': 'Account', 'accounts_type': 'Type'}),
            column_config={
                'Include':     st.column_config.CheckboxColumn('Include', default=True),
                'accounts_id': None,
            },
            hide_index=True,
            width="stretch",
            disabled=['Account', 'Type'],
            key="dashboard_nw_account_editor",
        )
        selected_ids = edited_accs[edited_accs['Include']]['accounts_id'].tolist()

        include_future = st.checkbox(
            "Include future registered transactions",
            value=False,
            help="When unchecked (default) balances reflect only transactions up to today's date.",
            key="dashboard_nw_future",
        )

        _cs, _ = st.columns([1, 5])
        if _cs.button("💾 Save Selection", key="dashboard_nw_save"):
            save_nwr_account_selection(selected_ids, _DASH_NW_KEY)
            st.success("Selection saved!")

    if not selected_ids:
        st.warning("No accounts selected — open the ⚙️ panel above and include at least one account.")
    else:
        # ── Build query ───────────────────────────────────────────────────
        _ids_sql   = ", ".join(str(int(i)) for i in selected_ids)
        _acc_filt  = f"AND a.Accounts_Id IN ({_ids_sql})"
        _hold_filt = f"AND h.Accounts_Id IN ({_ids_sql})"

        # Balance expressions
        # ─ Cash/bank accounts: balances come from the Transactions table.
        #   When "exclude future" is on, cap to Date <= today.
        # ─ All other account types (Assets, Pension, Other Investment) have
        #   their balance pre-computed from the Investments table by the
        #   update_*_balances() functions and stored in Accounts_Balance.
        #   Using a Transactions subquery there always returns 0, so we
        #   always read the stored value for those types.
        if include_future:
            _b_cash = "a.Accounts_Balance"
        else:
            _b_cash = (
                "(SELECT COALESCE(SUM(t.Total_Amount), 0) "
                " FROM Transactions t "
                " WHERE t.Accounts_Id = a.Accounts_Id AND t.Date <= CURRENT_DATE)"
            )
        # Stored balance — used for Assets, Pension, Other Investment cash
        _b_stored = "a.Accounts_Balance"

        query_combined = f"""
            WITH Latest_FX AS (
                SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
                FROM Historical_FX
                ORDER BY Currencies_Id_1, Date DESC
            ),
            Latest_Prices AS (
                SELECT DISTINCT ON (Securities_Id) Securities_Id, Close
                FROM Historical_Prices
                ORDER BY Securities_Id, Date DESC
            )
            -- ASSETS (Real Estate, Vehicle, Asset …)
            -- Balance stored in Accounts_Balance via manual/bulk updates — no Transactions rows.
            SELECT a.Accounts_Name AS name, 'Assets' AS type, c.Currencies_ShortName AS curr,
                   {_b_stored} AS qty,
                   CASE WHEN c.Currencies_ShortName = 'EUR' THEN {_b_stored}
                        ELSE {_b_stored} * COALESCE(fx.FX_Rate, 1) END AS value_eur
            FROM Accounts a
            LEFT JOIN Currencies c  ON a.Currencies_Id = c.Currencies_Id
            LEFT JOIN Latest_FX fx  ON a.Currencies_Id = fx.Currencies_Id_1
            WHERE a.Is_Active = TRUE
              AND a.Accounts_Type NOT IN ('Cash','Checking','Savings','Credit Card',
                                          'Brokerage','Pension','Other Investment',
                                          'Margin','Loan','Other')
              {_acc_filt}

            UNION ALL
            -- CASH (bank, checking, savings, credit card …)
            -- Balance derived from Transactions — date filter applied when exclude_future.
            SELECT a.Accounts_Name AS name, 'Cash' AS type, c.Currencies_ShortName AS curr,
                   {_b_cash} AS qty,
                   CASE WHEN c.Currencies_ShortName = 'EUR' THEN {_b_cash}
                        ELSE {_b_cash} * COALESCE(fx.FX_Rate, 1) END AS value_eur
            FROM Accounts a
            LEFT JOIN Currencies c  ON a.Currencies_Id = c.Currencies_Id
            LEFT JOIN Latest_FX fx  ON a.Currencies_Id = fx.Currencies_Id_1
            WHERE a.Is_Active = TRUE
              AND a.Accounts_Type NOT IN ('Brokerage','Pension','Other Investment',
                                          'Margin','Real Estate','Vehicle','Asset','Liability')
              {_acc_filt}

            UNION ALL
            -- CASH balance of Other Investment accounts
            -- Computed by update_investment_balances() from Investments + Transactions.
            -- Always read the stored value; the future-toggle has no meaningful effect here.
            SELECT a.Accounts_Name AS name, 'Cash' AS type, c.Currencies_ShortName AS curr,
                   {_b_stored} AS qty,
                   CASE WHEN c.Currencies_ShortName = 'EUR' THEN {_b_stored}
                        ELSE {_b_stored} * COALESCE(fx.FX_Rate, 1) END AS value_eur
            FROM Accounts a
            LEFT JOIN Currencies c  ON a.Currencies_Id = c.Currencies_Id
            LEFT JOIN Latest_FX fx  ON a.Currencies_Id = fx.Currencies_Id_1
            WHERE a.Is_Active = TRUE AND a.Accounts_Type = 'Other Investment'
              {_acc_filt}

            UNION ALL
            -- PENSION accounts
            -- Balance computed by update_pension_balances() from Investments (CashIn/IntInc/CashOut).
            -- Always read the stored value; pension entries are never "future" in practice.
            SELECT a.Accounts_Name AS name, 'Pension' AS type, c.Currencies_ShortName AS curr,
                   {_b_stored} AS qty,
                   CASE WHEN c.Currencies_ShortName = 'EUR' THEN {_b_stored}
                        ELSE {_b_stored} * COALESCE(fx.FX_Rate, 1) END AS value_eur
            FROM Accounts a
            LEFT JOIN Currencies c  ON a.Currencies_Id = c.Currencies_Id
            LEFT JOIN Latest_FX fx  ON a.Currencies_Id = fx.Currencies_Id_1
            WHERE a.Is_Active = TRUE AND a.Accounts_Type = 'Pension'
              {_acc_filt}

            UNION ALL
            -- INVESTMENTS (market value from Holdings × latest close price)
            SELECT s.Securities_Name AS name, 'Investment' AS type,
                   c.Currencies_ShortName AS curr,
                   SUM(h.Quantity) AS qty,
                   SUM(CASE WHEN c.Currencies_ShortName = 'EUR'
                                THEN h.Quantity * COALESCE(lp.Close, 0)
                            ELSE (h.Quantity * COALESCE(lp.Close, 0)) * COALESCE(fx.FX_Rate, 1)
                       END) AS value_eur
            FROM Holdings h
            JOIN Securities s   ON s.Securities_Id  = h.Securities_Id
            JOIN Currencies c   ON c.Currencies_Id  = s.Currencies_Id
            JOIN Latest_Prices lp ON lp.Securities_Id = h.Securities_Id
            LEFT JOIN Latest_FX fx ON s.Currencies_Id  = fx.Currencies_Id_1
            WHERE h.Quantity <> 0 {_hold_filt}
            GROUP BY s.Securities_Name, c.Currencies_ShortName

            ORDER BY type ASC, value_eur DESC
        """

        df_net = pd.read_sql(query_combined, conn)
        df_net.columns = [c.lower() for c in df_net.columns]
        df_net['type'] = df_net['type'].str.strip()
        df_net['qty_display'] = df_net.apply(format_qty_display, axis=1)

        _future_note = (
            "including future registered transactions"
            if include_future else
            "transactions up to today · future entries excluded"
        )
        st.caption(f"Balances: {_future_note}")

        # ── Pie chart — allocation by asset class ─────────────────────────
        _TYPE_COLORS = {
            'Cash':       '#3498DB',
            'Investment': '#F39C12',
            'Assets':     '#2ECC71',
            'Pension':    '#9B59B6',
        }

        _df_pie = (
            df_net[df_net['value_eur'] > 0]
            .groupby('type')['value_eur']
            .sum()
            .reset_index()
            .sort_values('value_eur', ascending=False)
        )

        if not _df_pie.empty:
            _total_pos = _df_pie['value_eur'].sum()
            fig_pie = px.pie(
                _df_pie,
                names='type',
                values='value_eur',
                title='<b>Net Worth Allocation by Asset Class</b>',
                template='plotly_dark',
                hole=0.40,
                color='type',
                color_discrete_map=_TYPE_COLORS,
            )
            fig_pie.update_traces(
                textposition='inside',
                textinfo='percent+label',
                hovertemplate='<b>%{label}</b><br>€ %{value:,.2f}<br>%{percent}<extra></extra>',
            )
            fig_pie.update_layout(
                margin=dict(l=0, r=0, t=50, b=10),
                showlegend=True,
                legend=dict(orientation='h', yanchor='bottom', y=-0.15, xanchor='center', x=0.5),
            )
            st.plotly_chart(fig_pie, width="stretch")

        # ── Summary metrics ───────────────────────────────────────────────
        # Savings rate: last complete month, bank/cash accounts only
        _sr_delta_str = ""
        _sr_income = _sr_expenses = 0.0
        _sr_month_label = ""
        try:
            _sr_df = get_savings_rate_data(months=2)
            if len(_sr_df) >= 2:
                _sr_cur         = _sr_df.iloc[-1]["savings_rate_pct"]
                _sr_prev        = _sr_df.iloc[-2]["savings_rate_pct"]
                _sr_prev_label  = pd.to_datetime(_sr_df.iloc[-2]["month"]).strftime("%b %Y")
                _sr_delta_pp    = _sr_cur - _sr_prev
                _sr_delta_str   = f"{_sr_delta_pp:+.1f} pp vs {_sr_prev_label}"
                _sr_income      = float(_sr_df.iloc[-1]["income_eur"])
                _sr_expenses    = float(_sr_df.iloc[-1]["expenses_eur"])
                _sr_month_label = pd.to_datetime(_sr_df.iloc[-1]["month"]).strftime("%b %Y")
            elif len(_sr_df) == 1:
                _sr_cur         = _sr_df.iloc[0]["savings_rate_pct"]
                _sr_income      = float(_sr_df.iloc[0]["income_eur"])
                _sr_expenses    = float(_sr_df.iloc[0]["expenses_eur"])
                _sr_month_label = pd.to_datetime(_sr_df.iloc[0]["month"]).strftime("%b %Y")
            else:
                _sr_cur = 0.0
        except Exception:
            _sr_cur = 0.0

        _nw        = df_net['value_eur'].sum()
        _assets    = df_net[df_net['type'] == 'Assets']['value_eur'].sum()
        _cash      = df_net[df_net['type'] == 'Cash']['value_eur'].sum()
        _pension   = df_net[df_net['type'] == 'Pension']['value_eur'].sum()
        _inv       = df_net[df_net['type'] == 'Investment']['value_eur'].sum()

        m1, m2, m3 = st.columns(3)
        m1.metric("Net Worth",   f"€ {_nw:,.2f}")
        m2.metric("Assets",      f"€ {_assets:,.2f}")
        m3.metric("Cash",        f"€ {_cash:,.2f}")

        m4, m5, m6 = st.columns(3)
        m4.metric("Pension",      f"€ {_pension:,.2f}")
        m5.metric("Investments",  f"€ {_inv:,.2f}")
        _sr_label = f"Cash Savings Rate · {_sr_month_label}" if _sr_month_label else "Cash Savings Rate"
        m6.metric(
            _sr_label,
            f"{_sr_cur:.1f}%",
            delta=_sr_delta_str if _sr_delta_str else None,
            help=(
                f"Last complete month ({_sr_month_label}). "
                "Covers bank & cash accounts only — excludes investment account dividends, "
                "interest and realised P&L. Delta = change vs the prior month."
            ),
        )
        if _sr_income or _sr_expenses:
            _sr_saved = _sr_income - _sr_expenses
            m6.markdown(
                f'<div style="font-size:0.82em; margin-top:-8px; line-height:1.6">'
                f'<span style="color:#888">% of income saved (bank accounts only)</span><br>'
                f'<span style="color:#2ECC71">▲ € {_sr_income:,.0f}</span>'
                f'&nbsp;<span style="color:#888; font-size:0.9em">income</span>'
                f' &nbsp;&nbsp; '
                f'<span style="color:#E74C3C">▼ € {_sr_expenses:,.0f}</span>'
                f'&nbsp;<span style="color:#888; font-size:0.9em">expenses</span>'
                f' &nbsp;&nbsp; '
                f'<span style="color:#F39C12">€ {_sr_saved:,.0f}</span>'
                f'&nbsp;<span style="color:#888; font-size:0.9em">saved</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

        # ── Detail table ──────────────────────────────────────────────────
        new_order  = ['name', 'type', 'curr', 'qty', 'qty_display', 'value_eur']
        df_net     = df_net.reindex(columns=new_order)
        styled_df  = (
            df_net.style
            .map(color_negative_red, subset=['value_eur', 'qty'])
            .apply(lambda x: style_qty_display(df_net), subset=['qty_display'], axis=0)
            .format({"qty": "{:,.2f}", "value_eur": "{:,.2f} €"})
            .hide(['qty'], axis=1)
        )
        st.dataframe(
            styled_df,
            width="stretch",
            hide_index=True,
            column_order=("name", "type", "curr", "qty_display", "value_eur"),
            column_config={
                "name":        "Description",
                "type":        "Category",
                "curr":        "Currency",
                "qty_display": "Value / Quantity",
                "value_eur":   "Value (€)",
            },
        )
        copy_df_button(styled_df, key="dl_dashboard_portfolio")

        # ── Upcoming Bills widget ─────────────────────────────────────────
        st.markdown("#### \U0001f4c5 Upcoming Bills (Next 14 Days)")
        try:
            _horizon = pd.Timestamp.today().normalize() + pd.Timedelta(days=14)

            # Build full category-path lookup once (name → full_path)
            _cat_hier   = get_category_hierarchy()
            _cat_map    = dict(zip(_cat_hier['name'], _cat_hier['full_path']))

            # 1. Confirmed: explicitly entered future transactions (recursive category path)
            _bills_conn = get_connection()
            df_confirmed = pd.read_sql("""
                WITH RECURSIVE cat_path AS (
                    SELECT Categories_Id,
                           Categories_Name::TEXT AS full_path,
                           Categories_Id_Parent
                    FROM Categories
                    WHERE Categories_Id_Parent IS NULL
                    UNION ALL
                    SELECT c.Categories_Id,
                           cp.full_path || ' : ' || c.Categories_Name,
                           c.Categories_Id_Parent
                    FROM Categories c
                    JOIN cat_path cp ON c.Categories_Id_Parent = cp.Categories_Id
                )
                SELECT
                    t.Date         AS date,
                    p.Payees_Name  AS payee,
                    t.Total_Amount AS amount_eur,
                    STRING_AGG(DISTINCT cp.full_path, ', ') AS category
                FROM Transactions t
                LEFT JOIN Payees p    ON p.Payees_Id       = t.Payees_Id
                LEFT JOIN Splits s    ON s.Transactions_Id = t.Transactions_Id
                LEFT JOIN cat_path cp ON cp.Categories_Id  = s.Categories_Id
                WHERE t.Date > CURRENT_DATE
                  AND t.Date <= CURRENT_DATE + INTERVAL '14 days'
                  AND t.Transfers_Id IS NULL
                GROUP BY t.Transactions_Id, t.Date, p.Payees_Name, t.Total_Amount
                ORDER BY t.Date
            """, _bills_conn)
            _bills_conn.close()

            df_confirmed['date'] = pd.to_datetime(df_confirmed['date'])
            df_confirmed['type'] = 'Confirmed'

            # 2. Projected: recurring payees whose next expected date falls in the window
            _df_future, _df_recurring = get_cash_flow_forecast(months_back=3)
            if not _df_recurring.empty:
                _df_recurring['next_expected_date'] = pd.to_datetime(_df_recurring['next_expected_date'])
                _today = pd.Timestamp.today().normalize()
                df_projected = _df_recurring[
                    (_df_recurring['next_expected_date'] > _today) &
                    (_df_recurring['next_expected_date'] <= _horizon)
                ][['next_expected_date', 'payees_name', 'avg_amount_eur', 'category']].copy()
                df_projected.columns = ['date', 'payee', 'amount_eur', 'category']
                # Map leaf category name → full hierarchical path
                df_projected['category'] = df_projected['category'].map(_cat_map).fillna(df_projected['category'])
                df_projected['type'] = 'Projected'
            else:
                df_projected = pd.DataFrame(columns=['date', 'payee', 'amount_eur', 'category', 'type'])

            # 3. Drop projected rows that are covered by a confirmed entry for the
            #    same payee within 7 days — avoids showing both when the recurring
            #    estimate lands just before/after the actual scheduled transaction.
            if not df_projected.empty and not df_confirmed.empty:
                _TOLERANCE = pd.Timedelta(days=7)
                def _has_confirmed(row):
                    same_payee = df_confirmed['payee'] == row['payee']
                    close_date = (df_confirmed['date'] - row['date']).abs() <= _TOLERANCE
                    return (same_payee & close_date).any()
                df_projected = df_projected[~df_projected.apply(_has_confirmed, axis=1)]

            # Merge, sort, display  (filter out empty frames to avoid dtype FutureWarning)
            _frames = [df for df in [df_confirmed, df_projected] if not df.empty]
            df_bills = pd.concat(_frames, ignore_index=True) if _frames else pd.DataFrame(columns=['date', 'payee', 'amount_eur', 'category', 'type'])
            df_bills  = df_bills.sort_values('date').reset_index(drop=True)

            if df_bills.empty:
                st.success("No bills due in the next 14 days ✅")
            else:
                df_bills['date'] = df_bills['date'].dt.strftime('%Y-%m-%d')
                st.dataframe(
                    df_bills,
                    hide_index=True,
                    width="stretch",
                    column_config={
                        "date":      "Date",
                        "payee":     "Payee",
                        "amount_eur": st.column_config.NumberColumn("Amount (€)", format="%.2f €"),
                        "category":  "Category",
                        "type":      st.column_config.TextColumn("Type",
                                         help="Confirmed = entered in the register · Projected = estimated from last 3 months of recurring patterns"),
                    },
                    column_order=["date", "payee", "amount_eur", "category", "type"],
                )
                copy_df_button(df_bills, key="dl_dashboard_bills")
                _n_proj = len(df_projected)
                if _n_proj:
                    st.caption(f"{_n_proj} projected payment(s) estimated from the last 3 months of recurring patterns.")
        except Exception as _e:
            st.info(f"Upcoming bills unavailable: {_e}")

    # Update buttons
    # Custom CSS to center the "Update All" button row and add spacing
    st.markdown("""
        <style>
        div.stButton > button {
            border-radius: 5px;
            height: 3em;
            font-weight: bold;
        }
        </style>
    """, unsafe_allow_html=True)

    st.subheader("Balances Synchronization")

    # Create a 2x2 grid for specific updates
    grid = st.columns(2)

    # Define the buttons in a list to keep code DRY (Don't Repeat Yourself)
    tasks = [
        ("Bank & Cash Accounts", update_accounts_balances),
        ("Investment Cash Accounts", update_investment_balances),
        ("Pension Accounts", update_pension_balances),
        ("Security Holdings", update_holdings)
    ]

    for i, (label, func) in enumerate(tasks):
        with grid[i % 2]:
            if st.button(f"🔄 Update {label}", width='stretch'):
                with st.spinner(f"Updating {label}..."):
                    func()
                    st.toast(f"{label} updated!") # Less intrusive than balloons for small tasks
                    st.rerun()  # Refresh the page to show updated data

    st.markdown("---")

    # Center the "Update All" button and make it prominent
    _, center_col, _ = st.columns([1, 2, 1])

    with center_col:
        # use 'primary' type to give it the brand color
        if st.button("🚀 Run Full Update", type="primary", width='stretch'):
            with st.spinner("Processing full update..."):
                update_accounts_balances()
                update_investment_balances()
                update_pension_balances()
                update_holdings()
                st.balloons()
                st.success("All balances up to date!")
                st.rerun()

    # ------------------------------------------------------------------
    # AI WEEKLY SUMMARY
    # ------------------------------------------------------------------
    st.markdown("---")
    st.subheader("🧠 AI Weekly Summary")

    try:
        df_summaries = get_weekly_summaries()
        if df_summaries.empty:
            st.info("No weekly summary available yet. The scheduler generates one every Monday at 07:00, or run `python -m ai.weekly_summary` manually.")
        else:
            latest = df_summaries.iloc[0]
            week_label = latest['week_start'].strftime("Week of %B %d, %Y")
            generated  = latest['generated_at'].strftime("%Y-%m-%d %H:%M")
            st.caption(f"{week_label} · generated {generated}")
            st.markdown(latest['summary_text'])

            if len(df_summaries) > 1:
                with st.expander("📚 Previous summaries"):
                    for _, row in df_summaries.iloc[1:].iterrows():
                        st.markdown(
                            f"**{row['week_start'].strftime('Week of %B %d, %Y')}** "
                            f"<span style='opacity:.6;font-size:.85em'>(generated {row['generated_at'].strftime('%Y-%m-%d')})</span>",
                            unsafe_allow_html=True,
                        )
                        st.markdown(row['summary_text'])
                        st.divider()
    except Exception as e:
        st.info(f"Weekly summary unavailable: {e}")

    # ------------------------------------------------------------------
    # AI MONTHLY SUMMARIES
    # ------------------------------------------------------------------
    st.markdown("---")
    st.subheader("🧠 AI Monthly Summaries")
    render_ai_monthly_summaries_ui(conn)

    # ------------------------------------------------------------------
    # ANOMALY DETECTION
    # ------------------------------------------------------------------
    st.markdown("---")
    st.subheader("🚨 Unusual Transactions (Last 30 Days)")
    st.caption("Transactions whose amount is ≥ 2.5 standard deviations from the typical amount for that payee & category.")

    try:
        df_anomalies = get_transaction_anomalies(2.5, 30)
        if df_anomalies.empty:
            st.success("No unusual transactions detected in the last 30 days.")
        else:
            st.warning(f"{len(df_anomalies)} unusual transaction(s) detected.")
            df_anomalies['date'] = pd.to_datetime(df_anomalies['date']).dt.strftime('%Y-%m-%d')
            st.dataframe(
                df_anomalies.style.format({
                    'amount_eur': '{:,.2f} €',
                    'mean_eur':   '{:,.2f} €',
                    'std_eur':    '{:,.2f} €',
                    'z_score':    '{:+.2f}',
                }).map(
                    lambda v: 'color: #E74C3C; font-weight: bold' if isinstance(v, float) and abs(v) >= 3 else '',
                    subset=['z_score']
                ),
                hide_index=True, width='stretch',
                column_config={
                    'date':         'Date',
                    'payees_name':  'Payee',
                    'category':     'Category',
                    'accounts_name':'Account',
                    'amount_eur':   st.column_config.NumberColumn('Amount (€)',   format='%,.2f €'),
                    'mean_eur':     st.column_config.NumberColumn('Typical (€)',  format='%,.2f €'),
                    'std_eur':      st.column_config.NumberColumn('Std Dev (€)',  format='%,.2f €'),
                    'z_score':      st.column_config.NumberColumn('Z-Score',      format='%+.2f',
                                        help="How many standard deviations from the mean for this payee/category"),
                }
            )
            copy_df_button(df_anomalies, key="dl_dashboard_anomalies")
    except Exception as e:
        st.info(f"Anomaly detection unavailable: {e}")
