import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import numpy as np
from database.connection import get_db
from database.crud import update_holdings, save_nwr_account_selection
from database.queries import (
    get_category_hierarchy, get_hist_inv_positions_data,
    get_net_worth_report_data, get_all_accounts_for_nwr, get_nwr_account_selection,
    get_nwr_security_detail,
    get_pnl_report_data, get_income_expense_data, get_portfolio_signals,
    get_cash_flow_forecast, get_dividend_tracker_data, get_dividend_forecast_data, get_asset_allocation_data,
    get_sector_allocation_data, get_allocation_targets, save_allocation_targets, get_rebalancing_plan,
    get_fx_exposure_data, get_bond_schedule_data,
    get_savings_rate_data,
    upsert_budget, delete_budget, get_budget_vs_actual, get_annual_income,
    get_ytd_expense_transactions,
    get_spending_trends,
    get_investment_income_report, get_bank_interest_report,
    get_capital_gains_report, get_tax_loss_opportunities,
    get_all_inv_txns_for_gains,
    get_goals, upsert_goal, delete_goal,
    get_price_returns,
    get_portfolio_weights,
    get_investable_portfolio_value,
    get_investment_accounts,
    get_benchmark_candidates,
    get_benchmark_returns,
    get_benchmark_presets,
    upsert_benchmark_preset,
    delete_benchmark_preset,
    get_investment_cashflows,
    get_spending_by_payee,
    get_payee_transactions,
    update_split,
    get_expense_categories,
    get_custom_report_presets,
    upsert_custom_report_preset,
    delete_custom_report_preset,
    get_all_payees,
    get_custom_report_data,
    get_custom_report_investment_data,
    get_custom_report_investment_drill_down,
    get_custom_report_drill_down,
    get_all_securities_for_filter,
)
from data.downloaders import download_historical_fx, download_historical_prices_from_tradingview, download_historical_prices_from_yahoo, download_bond_prices_from_solidus, download_securities_info_from_yahoo, download_securities_info_from_tradingview
from ui.components import color_negative_red, color_value, custom_metric, get_color, copy_df_button, pf_plotly_chart
from datetime import datetime, timedelta

def _period_to_dates(period_label: str, grouping: str):
    """Convert a period label (e.g. '2023', '2023 Q3', '2023-07') to (date_from, date_to)."""
    import calendar
    if grouping == "year":
        y = int(period_label)
        return datetime(y, 1, 1).date(), datetime(y, 12, 31).date()
    elif grouping == "quarter":
        y, q = int(period_label.split(" Q")[0]), int(period_label.split(" Q")[1])
        m1 = (q - 1) * 3 + 1
        m2 = m1 + 2
        return datetime(y, m1, 1).date(), datetime(y, m2, calendar.monthrange(y, m2)[1]).date()
    else:  # month  "YYYY-MM"
        y, m = int(period_label.split("-")[0]), int(period_label.split("-")[1])
        return datetime(y, m, 1).date(), datetime(y, m, calendar.monthrange(y, m)[1]).date()


def _build_hierarchical_pivot(pivot: "pd.DataFrame", periods: list) -> "pd.DataFrame":
    """Convert a flat category×period pivot into a hierarchical display table."""
    all_leaf_paths = list(pivot.index)

    # Expand to all ancestor paths
    all_paths: set = set(all_leaf_paths)
    for path in all_leaf_paths:
        parts = path.split(" : ")
        for i in range(1, len(parts)):
            all_paths.add(" : ".join(parts[:i]))

    # Compute totals for every path (leaf + ancestor)
    def _sum_for(path):
        leaves = [p for p in all_leaf_paths if p == path or p.startswith(path + " : ")]
        return {col: pivot.loc[[p for p in leaves if p in pivot.index], col].sum()
                for col in periods + ["TOTAL"]}

    path_totals = {p: _sum_for(p) for p in all_paths}

    # Hierarchical sort: by top-level category, then full path
    sorted_paths = sorted(all_paths, key=lambda p: (p.split(" : ")[0].lower(), p.lower()))

    rows, parent_display_names = [], set()
    for path in sorted_paths:
        level  = path.count(" : ")
        label  = "  " * level + path.split(" : ")[-1]
        is_par = any(p.startswith(path + " : ") for p in all_paths if p != path)
        if is_par:
            parent_display_names.add(label)
        row = {"Category": label, "_is_bold": is_par}
        row.update(path_totals[path])
        rows.append(row)

    # Grand total
    grand = {"Category": "OVERALL TOTAL", "_is_bold": True}
    grand.update({col: pivot[col].sum() for col in periods + ["TOTAL"]})
    rows.append(grand)

    df = pd.DataFrame(rows)
    return df, parent_display_names


def render_custom_reports():
    """Custom report builder with saveable presets."""
    import json

    st.subheader("\U0001f4cb Custom Reports")
    st.caption(
        "Build a spending report for any date range, accounts, categories, and payees. "
        "Save the configuration as a named preset to reuse later."
    )

    # ── Preset management ─────────────────────────────────────────────────────
    presets_df  = get_custom_report_presets()
    preset_names = presets_df["preset_name"].tolist() if not presets_df.empty else []
    preset_map: dict = {}
    if not presets_df.empty:
        for _, row in presets_df.iterrows():
            cfg = row["config"]
            if isinstance(cfg, str):
                cfg = json.loads(cfg)
            preset_map[row["preset_name"]] = (int(row["preset_id"]), cfg or {})

    pp1, pp2, pp3, pp4 = st.columns([3, 3, 1, 1])
    sel_preset = pp1.selectbox(
        "Preset", ["(New Report)"] + sorted(preset_names), key="cr_preset_sel",
    )
    preset_name_input = pp2.text_input(
        "Name", value="" if sel_preset == "(New Report)" else sel_preset,
        placeholder="Preset name to save as…", key=f"cr_preset_name_{sel_preset}",
        label_visibility="collapsed",
    )
    save_btn = pp3.button("\U0001f4be Save", key="cr_save_btn", width="stretch")
    del_btn  = pp4.button("\U0001f5d1️ Delete", key="cr_del_btn",
                          disabled=(sel_preset == "(New Report)"),
                          width="stretch")

    loaded_cfg: dict = {}
    if sel_preset != "(New Report)" and sel_preset in preset_map:
        loaded_cfg = preset_map[sel_preset][1]

    # ── Date range ────────────────────────────────────────────────────────────
    now = datetime.now()
    DR_OPTIONS = ["Year to Date", "Last Year", "Last 12 Months", "Last 24 Months", "All Time", "Custom"]
    default_dr = loaded_cfg.get("date_range_type", "Last 12 Months")
    if default_dr not in DR_OPTIONS:
        default_dr = "Last 12 Months"

    dr1, dr2, dr3, dr4 = st.columns([2, 2, 2, 2])
    date_range_type = dr1.selectbox(
        "Date Range", DR_OPTIONS, index=DR_OPTIONS.index(default_dr),
        key=f"cr_dr_type_{sel_preset}",
    )
    GROUPING_OPTIONS = ["Year", "Quarter", "Month"]
    default_grp = loaded_cfg.get("column_grouping", "year").capitalize()
    if default_grp not in GROUPING_OPTIONS:
        default_grp = "Year"
    grouping_str = dr2.selectbox(
        "Column Grouping", GROUPING_OPTIONS, index=GROUPING_OPTIONS.index(default_grp),
        key=f"cr_grouping_{sel_preset}",
    )
    grouping = grouping_str.lower()

    # initialised here so the save block can always reference them
    date_from_is_today = False
    date_to_is_today   = False

    if date_range_type == "Year to Date":
        date_from = datetime(now.year, 1, 1).date()
        date_to   = now.date()
    elif date_range_type == "Last Year":
        date_from = datetime(now.year - 1, 1, 1).date()
        date_to   = datetime(now.year - 1, 12, 31).date()
    elif date_range_type == "Last 12 Months":
        date_from = (now - timedelta(days=365)).date()
        date_to   = now.date()
    elif date_range_type == "Last 24 Months":
        date_from = (now - timedelta(days=730)).date()
        date_to   = now.date()
    elif date_range_type == "All Time":
        date_from = datetime(2000, 1, 1).date()
        date_to   = now.date()
    else:  # Custom
        saved_from          = loaded_cfg.get("date_from")
        saved_to            = loaded_cfg.get("date_to")
        date_from_is_today  = loaded_cfg.get("date_from_is_today", False)
        date_to_is_today    = loaded_cfg.get("date_to_is_today",   False)
        def_from = (datetime.strptime(saved_from, "%Y-%m-%d").date()
                    if saved_from else datetime(now.year, 1, 1).date())
        def_to   = (datetime.strptime(saved_to, "%Y-%m-%d").date()
                    if saved_to else now.date())

        with dr3:
            date_from_is_today = st.checkbox(
                "Use today", value=date_from_is_today,
                key=f"cr_from_today_{sel_preset}",
                help="When saved, this preset will always use today's date as the From date.",
            )
            if date_from_is_today:
                date_from = now.date()
                st.date_input("From", value=date_from, disabled=True,
                              key=f"cr_date_from_dis_{sel_preset}")
            else:
                date_from = st.date_input(
                    "From", value=def_from, key=f"cr_date_from_{sel_preset}",
                    min_value=datetime(2000, 1, 1).date(), max_value=now.date(),
                )

        with dr4:
            date_to_is_today = st.checkbox(
                "Use today", value=date_to_is_today,
                key=f"cr_to_today_{sel_preset}",
                help="When saved, this preset will always use today's date as the To date.",
            )
            if date_to_is_today:
                date_to = now.date()
                st.date_input("To", value=date_to, disabled=True,
                              key=f"cr_date_to_dis_{sel_preset}")
            else:
                date_to = st.date_input(
                    "To", value=def_to, key=f"cr_date_to_{sel_preset}",
                    min_value=datetime(2000, 1, 1).date(), max_value=now.date(),
                )

    # ── Account filter ────────────────────────────────────────────────────────
    with st.expander("\U0001f3e6 Accounts", expanded=bool(loaded_cfg.get("account_ids"))):
        all_accts_df   = get_all_accounts_for_nwr()
        acct_id_to_name = dict(zip(all_accts_df["accounts_id"], all_accts_df["accounts_name"]))
        acct_name_to_id = {v: k for k, v in acct_id_to_name.items()}
        all_acct_names  = sorted(all_accts_df["accounts_name"].tolist())
        saved_acct_ids  = loaded_cfg.get("account_ids") or []

        use_all_accts = st.radio(
            "Accounts to include", ["All Accounts", "Selected Accounts"],
            horizontal=True, key=f"cr_acct_mode_{sel_preset}",
            index=1 if loaded_cfg.get("use_all_accts") == "Selected Accounts" else (1 if saved_acct_ids else 0),
        )
        account_ids: list = []
        if use_all_accts == "Selected Accounts":
            def_accts = [acct_id_to_name[i] for i in saved_acct_ids if i in acct_id_to_name]
            sel_acct_names = st.multiselect("Accounts", all_acct_names, default=def_accts,
                                            key=f"cr_accts_{sel_preset}")
            account_ids = [acct_name_to_id[n] for n in sel_acct_names if n in acct_name_to_id]

    # ── Category filter ───────────────────────────────────────────────────────
    with st.expander("\U0001f3f7️ Categories", expanded=bool(loaded_cfg.get("category_ids") or loaded_cfg.get("use_all_cats") == "Selected Categories")):
        cat_hier_df = get_category_hierarchy()
        EXCLUDED_TYPES = {"Income", "Transfer", "Trading", "Investment", "Interest", "Dividend"}
        exp_cats = cat_hier_df[~cat_hier_df["categories_type"].isin(EXCLUDED_TYPES)].copy()
        cat_path_to_id = dict(zip(exp_cats["full_path"], exp_cats["categories_id"]))
        cat_id_to_path = {v: k for k, v in cat_path_to_id.items()}
        all_cat_paths  = sorted(exp_cats["full_path"].tolist())
        saved_cat_ids  = loaded_cfg.get("category_ids") or []

        use_all_cats = st.radio(
            "Categories to include", ["All Expense Categories", "Selected Categories"],
            horizontal=True, key=f"cr_cat_mode_{sel_preset}",
            index=1 if loaded_cfg.get("use_all_cats") == "Selected Categories" else (1 if saved_cat_ids else 0),
        )
        category_ids: list = []
        if use_all_cats == "Selected Categories":
            def_cats = [cat_id_to_path[i] for i in saved_cat_ids if i in cat_id_to_path]
            sel_cat_paths = st.multiselect(
                "Categories", all_cat_paths, default=def_cats,
                key=f"cr_cats_{sel_preset}",
                help="Selecting a parent category includes all its sub-categories in the results.",
            )
            category_ids = [cat_path_to_id[p] for p in sel_cat_paths if p in cat_path_to_id]

    # ── Payee filter ──────────────────────────────────────────────────────────
    with st.expander("\U0001f464 Payees", expanded=bool(loaded_cfg.get("payee_names"))):
        all_payees_df   = get_all_payees()
        all_payee_names = sorted(all_payees_df["payees_name"].tolist())
        saved_payees    = loaded_cfg.get("payee_names") or []

        use_all_payees = st.radio(
            "Payees to include", ["All Payees", "Selected Payees"],
            horizontal=True, key=f"cr_payee_mode_{sel_preset}",
            index=1 if loaded_cfg.get("use_all_payees") == "Selected Payees" else (1 if saved_payees else 0),
        )
        payee_names: list = []
        if use_all_payees == "Selected Payees":
            sel_payees = st.multiselect(
                "Payees", all_payee_names, default=saved_payees,
                key=f"cr_payees_{sel_preset}",
            )
            payee_names = sel_payees

    # ── Securities filter ─────────────────────────────────────────────────────
    saved_sec_ids = loaded_cfg.get("security_ids") or []
    with st.expander("📈 Securities", expanded=bool(saved_sec_ids or loaded_cfg.get("use_all_secs") == "Selected Securities")):
        st.caption(
            "Filter to transactions linked to specific securities "
            "(e.g. dividend income, interest, or fees associated with an investment). "
            "Leave empty to include all transactions regardless of security."
        )
        all_sec_df  = get_all_securities_for_filter()
        sec_id_to_name = dict(zip(all_sec_df["securities_id"], all_sec_df["securities_name"]))
        sec_name_to_id = {v: k for k, v in sec_id_to_name.items()}
        all_sec_names  = sorted(all_sec_df["securities_name"].tolist())

        use_all_secs = st.radio(
            "Securities to include", ["All Securities", "Selected Securities"],
            horizontal=True, key=f"cr_sec_mode_{sel_preset}",
            index=1 if loaded_cfg.get("use_all_secs") == "Selected Securities" else (1 if saved_sec_ids else 0),
        )
        security_ids: list = []
        if use_all_secs == "Selected Securities":
            def_secs = [sec_id_to_name[i] for i in saved_sec_ids if i in sec_id_to_name]
            sel_sec_names = st.multiselect(
                "Securities", all_sec_names, default=def_secs,
                key=f"cr_secs_{sel_preset}",
            )
            security_ids = [int(sec_name_to_id[n]) for n in sel_sec_names if n in sec_name_to_id]

    # ── Additional options ────────────────────────────────────────────────────
    with st.expander("⚙️ Additional Options", expanded=bool(
            loaded_cfg.get("include_transfers") or loaded_cfg.get("use_account_currency"))):
        opt1, opt2 = st.columns(2)

        with opt1:
            st.markdown("**Transfer transactions**")
            include_transfers = st.toggle(
                "Include transfer transactions",
                value=bool(loaded_cfg.get("include_transfers", False)),
                key=f"cr_inc_transfers_{sel_preset}",
                help=(
                    "By default, transactions marked as internal transfers are excluded. "
                    "Enable this only if you want to see transfer flows in the report."
                ),
            )

        with opt2:
            st.markdown("**Reporting currency**")
            use_account_currency = st.toggle(
                "Use account native currency (no EUR conversion)",
                value=bool(loaded_cfg.get("use_account_currency", False)),
                key=f"cr_acct_ccy_{sel_preset}",
                help=(
                    "When ON: amounts are reported in each account's own currency without FX conversion. "
                    "Useful when all selected accounts share the same currency. "
                    "When OFF (default): all amounts are converted to EUR using the last available FX rate of each reporting period (e.g. Dec 31 rate for yearly grouping)."
                ),
            )
            if use_account_currency:
                st.caption("⚠️ Totals may mix currencies if multiple account currencies are selected.")

    # ── Save / Delete ─────────────────────────────────────────────────────────
    if save_btn:
        pname = preset_name_input.strip()
        if not pname or pname == "(New Report)":
            st.warning("Enter a preset name before saving.")
        else:
            cfg = {
                "date_range_type":      date_range_type,
                "date_from":            str(date_from),
                "date_to":              str(date_to),
                "date_from_is_today":   date_from_is_today,
                "date_to_is_today":     date_to_is_today,
                "column_grouping":      grouping,
                "use_all_accts":        use_all_accts,
                "account_ids":          account_ids,
                "use_all_cats":         use_all_cats,
                "category_ids":         category_ids,
                "use_all_payees":       use_all_payees,
                "payee_names":          payee_names,
                "use_all_secs":         use_all_secs,
                "security_ids":         security_ids,
                "include_transfers":    include_transfers,
                "use_account_currency": use_account_currency,
            }
            upsert_custom_report_preset(pname, cfg)
            get_custom_report_presets.clear()
            st.toast(f"Preset '{pname}' saved.", icon="✅")
            st.rerun()

    _cr_del_key = f"cr_del_confirm_{sel_preset}"
    if del_btn and sel_preset != "(New Report)":
        st.session_state[_cr_del_key] = True
        st.rerun()
    if st.session_state.get(_cr_del_key):
        st.warning(f"Delete preset **'{sel_preset}'**? This cannot be undone.")
        dc1, dc2 = st.columns(2)
        with dc1:
            if st.button("Cancel", key="cr_del_cancel"):
                st.session_state.pop(_cr_del_key, None)
                st.rerun()
        with dc2:
            if st.button("Yes, delete", key="cr_del_yes", type="primary"):
                delete_custom_report_preset(preset_map[sel_preset][0])
                get_custom_report_presets.clear()
                st.session_state.pop(_cr_del_key, None)
                st.toast(f"Preset '{sel_preset}' deleted.", icon="\U0001f5d1️")
                st.rerun()

    # ── Run report ────────────────────────────────────────────────────────────
    st.divider()
    run_col, _ = st.columns([1, 4])
    if run_col.button("▶️ Run Report", type="primary", key="cr_run",
                      width="stretch"):
        _investment_mode = use_all_cats == "Selected Categories" and not category_ids
        if not _investment_mode and use_all_cats == "Selected Categories" and not category_ids:
            st.warning("Select at least one category or switch to 'All Expense Categories'.")
        elif use_all_accts == "Selected Accounts" and not account_ids:
            st.warning("Select at least one account or switch to 'All Accounts'.")
        else:
            with st.spinner("Running report…"):
                if _investment_mode:
                    _df = get_custom_report_investment_data(
                        date_from, date_to, grouping,
                        security_ids=security_ids,
                        account_ids=account_ids or None,
                        use_account_currency=use_account_currency,
                    )
                else:
                    _df = get_custom_report_data(
                        date_from, date_to, grouping,
                        account_ids=account_ids or None,
                        category_ids=category_ids or None,
                        payee_names=payee_names or None,
                        security_ids=security_ids or None,
                        include_transfers=include_transfers,
                        use_account_currency=use_account_currency,
                    )
            st.session_state["cr_result_df"] = _df
            st.session_state["cr_result_params"] = {
                "date_from":            date_from,
                "date_to":              date_to,
                "grouping":             grouping,
                "grouping_str":         grouping_str,
                "account_ids":          account_ids or None,
                "category_ids":         category_ids or None,
                "payee_names":          payee_names or None,
                "security_ids":         security_ids or None,
                "include_transfers":    include_transfers,
                "use_account_currency": use_account_currency,
                "investment_mode":      _investment_mode,
            }

    # ── Display results (persist across interactions) ─────────────────────────
    if "cr_result_df" not in st.session_state:
        return

    df     = st.session_state["cr_result_df"]
    params = st.session_state["cr_result_params"]
    grouping      = params["grouping"]
    grouping_str  = params["grouping_str"]
    investment_mode = params.get("investment_mode", False)
    cat_label     = "Securities" if investment_mode else "Categories"

    if df.empty:
        st.info("No data found for the selected filters and date range.")
        return

    # ── Summary metrics ───────────────────────────────────────────────────────
    df_by_period = df.groupby(["period", "period_order"])["amount_eur"].sum().reset_index()
    df_by_period = df_by_period.sort_values("period_order")
    total_all = df_by_period["amount_eur"].sum()

    m1, m2, m3 = st.columns(3)
    m1.metric("Grand Total",  f"€ {total_all:,.2f}")
    m2.metric("Periods",      str(len(df_by_period)))
    m3.metric(cat_label,      str(df["category"].nunique()))

    # ── Bar chart ─────────────────────────────────────────────────────────────
    chart_title = f"<b>Total {'Cashflow' if investment_mode else 'Spending'} by {grouping_str}</b>"
    fig = px.bar(
        df_by_period, x="period", y="amount_eur",
        title=chart_title,
        template="plotly_dark",
        labels={"amount_eur": "Amount (€)", "period": grouping_str},
        text="amount_eur",
    )
    fig.update_traces(texttemplate="€%{y:,.0f}", textposition="outside")
    fig.update_layout(margin=dict(l=0, r=0, t=50, b=0))
    pf_plotly_chart(fig)

    # ── Hierarchical pivot table ──────────────────────────────────────────────
    st.markdown(f"#### {'Cashflow by Security' if investment_mode else 'Spending by Category'}")

    period_order_map = df.groupby("period")["period_order"].min().sort_values()
    periods = period_order_map.index.tolist()

    pivot = df.pivot_table(
        index="category", columns="period", values="amount_eur",
        aggfunc="sum", fill_value=0,
    )
    pivot = pivot.reindex(columns=periods, fill_value=0)
    pivot["TOTAL"] = pivot.sum(axis=1)

    df_hier, parent_labels = _build_hierarchical_pivot(pivot, periods)
    parent_labels.add("OVERALL TOTAL")
    bold_set = {lbl.strip() for lbl in parent_labels}

    display_cols = ["Category"] + periods + ["TOTAL"]
    df_display   = df_hier[display_cols].copy()

    fmt = {col: "€ {:,.2f}" for col in periods + ["TOTAL"]}

    def _style_pivot(row):
        is_bold = row["Category"].strip() in bold_set
        styles = []
        for col in row.index:
            css = "font-weight: bold; " if is_bold else ""
            if col != "Category":
                try:
                    if float(row[col]) < 0:
                        css += "color: #E74C3C;"
                except (TypeError, ValueError):
                    pass
            styles.append(css)
        return styles

    st.dataframe(
        df_display.style.format(fmt).apply(_style_pivot, axis=1),
        hide_index=True,
        width="stretch",
        column_config={
            "Category": st.column_config.Column(pinned=True),
        },
    )
    copy_df_button(df_display, key="dl_cr_report")

    # Drill-down
    _ALL_ITEMS   = '— All Securities —' if investment_mode else '— All Categories —'
    _ALL_PERIODS = '— All Periods —'

    all_items_sorted = sorted(df['category'].unique().tolist())

    dd_col1, dd_col2 = st.columns(2)
    sel_item = dd_col1.selectbox(
        'Security' if investment_mode else 'Category',
        options=[_ALL_ITEMS] + all_items_sorted,
        key='cr_dd_cat',
    )
    sel_period = dd_col2.selectbox(
        'Period', options=[_ALL_PERIODS] + periods,
        key='cr_dd_period',
    )

    if sel_period == _ALL_PERIODS:
        dd_from, dd_to = params['date_from'], params['date_to']
    else:
        dd_from, dd_to = _period_to_dates(sel_period, grouping)

    _use_acct_ccy = params.get('use_account_currency', False)
    _amt_label    = 'Amount (native)' if _use_acct_ccy else 'Amount (€)'

    if investment_mode:
        st.markdown('#### 🔍 Investment Drill-Down')
        st.caption('Select a security and period to view the underlying investment entries.')

        sec_name_arg = None if sel_item == _ALL_ITEMS else sel_item
        df_inv = get_custom_report_investment_drill_down(
            dd_from, dd_to,
            security_name=sec_name_arg,
            account_ids=params['account_ids'],
            use_account_currency=_use_acct_ccy,
        )

        if df_inv.empty:
            st.info('No investment entries found for the selected security and period.')
        else:
            dd_total = df_inv['amount_eur'].sum()
            st.caption(f'{len(df_inv)} entry(ies) · net total € {dd_total:,.2f}')
            _inv_cols = ['date', 'security', 'action', 'quantity', 'price', 'amount', 'amount_eur', 'account']
            if _use_acct_ccy:
                _inv_cols.insert(_inv_cols.index('amount') + 1, 'currency')
            df_inv_show = df_inv[_inv_cols].copy()
            df_inv_show['date'] = df_inv_show['date'].dt.strftime('%Y-%m-%d')
            st.dataframe(
                df_inv_show,
                column_config={
                    'date':       st.column_config.TextColumn('Date'),
                    'security':   st.column_config.TextColumn('Security'),
                    'action':     st.column_config.TextColumn('Action',   width='small'),
                    'quantity':   st.column_config.NumberColumn('Qty',     format='%,.4f'),
                    'price':      st.column_config.NumberColumn('Price',   format='%,.4f'),
                    'amount':     st.column_config.NumberColumn('Amount',  format='%,.2f'),
                    'currency':   st.column_config.TextColumn('CCY',       width='small'),
                    'amount_eur': st.column_config.NumberColumn(_amt_label, format='%+,.2f'),
                    'account':    st.column_config.TextColumn('Account'),
                },
                hide_index=True,
                width='stretch',
            )
            copy_df_button(df_inv_show, key='dl_cr_inv_dd')
    else:
        st.markdown('#### 🔍 Transaction Drill-Down')
        st.caption('Select a category and period to view and edit the underlying transactions.')

        drill_paths: set = set(df['category'].unique())
        for _p in list(drill_paths):
            parts = _p.split(' : ')
            for i in range(1, len(parts)):
                drill_paths.add(' : '.join(parts[:i]))
        drill_paths_sorted = sorted(drill_paths, key=lambda p: (p.split(' : ')[0].lower(), p.lower()))

        path_to_label: dict = {_ALL_ITEMS: _ALL_ITEMS}
        for _p in drill_paths_sorted:
            lvl = _p.count(' : ')
            path_to_label[_p] = ' ' * (lvl * 4) + _p.split(' : ')[-1]

        cat_path_arg = None if sel_item == _ALL_ITEMS else sel_item

        df_txn = get_custom_report_drill_down(
            dd_from, dd_to,
            category_path=cat_path_arg,
            account_ids=params['account_ids'],
            category_ids=params.get('category_ids'),
            payee_names=params['payee_names'],
            security_ids=params.get('security_ids'),
            include_transfers=params.get('include_transfers', False),
            use_account_currency=_use_acct_ccy,
        )

        if df_txn.empty:
            st.info('No transactions found for the selected category and period.')
        else:
            dd_total = df_txn['amount_eur'].sum()
            st.caption(f'{len(df_txn)} transaction(s) · net total € {dd_total:,.2f}')

            df_cats   = get_expense_categories()
            cat_opts  = df_cats['full_path'].tolist()
            cat_id_map = dict(zip(df_cats['full_path'], df_cats['categories_id']))

            _dd_cols = ['splits_id', 'date', 'payee', 'category', 'amount_eur', 'notes']
            if _use_acct_ccy and 'currency' in df_txn.columns:
                _dd_cols = ['splits_id', 'date', 'payee', 'category', 'currency', 'amount_eur', 'notes']
            df_edit = df_txn[_dd_cols].copy()
            df_edit['date'] = df_edit['date'].dt.strftime('%Y-%m-%d')

            edited = st.data_editor(
                df_edit,
                disabled=['splits_id', 'date', 'payee', 'amount_eur'] + (['currency'] if _use_acct_ccy else []),
                column_config={
                    'splits_id':  st.column_config.NumberColumn('ID',         format='%d'),
                    'date':       st.column_config.TextColumn('Date'),
                    'payee':      st.column_config.TextColumn('Payee'),
                    'currency':   st.column_config.TextColumn('CCY',          width='small'),
                    'category':   st.column_config.SelectboxColumn('Category', options=cat_opts, required=True),
                    'amount_eur': st.column_config.NumberColumn(_amt_label,   format='%,.2f'),
                    'notes':      st.column_config.TextColumn('Notes'),
                },
                hide_index=True,
                width='stretch',
                key=f'cr_dd_editor_{sel_item}_{sel_period}',
            )

            _cats_changed  = not edited['category'].equals(df_edit['category'])
            _notes_changed = not edited['notes'].fillna('').equals(df_edit['notes'].fillna(''))
            _has_changes   = _cats_changed or _notes_changed

            copy_df_button(df_edit.drop(columns=['splits_id']), key='dl_cr_txn_dd')

            if st.button('💾 Save Changes', key='cr_dd_save', disabled=not _has_changes):
                changes, errors = 0, []
                for i, row in edited.iterrows():
                    orig        = df_txn.iloc[i]
                    new_cat_id  = cat_id_map.get(row['category'])
                    orig_notes  = orig['notes'] or ''
                    new_notes   = row['notes'] or ''
                    if new_cat_id is None:
                        errors.append(f"Row {i+1}: unknown category '{row['category']}'")
                        continue
                    if new_cat_id != int(orig['categories_id']) or new_notes != orig_notes:
                        update_split(int(row['splits_id']), new_cat_id, new_notes or None)
                        changes += 1
                for e in errors:
                    st.error(e)
                if changes:
                    st.session_state.pop('cr_result_df', None)
                    st.success(f'Saved {changes} change(s). Re-run the report to refresh the table.')
                elif not errors:
                    st.info('No changes detected.')


def render_investment_consistency_check():
    """Investment data consistency / anomaly checker with inline edit & save."""
    from database.queries import get_investment_consistency_data, update_investment_row

    st.subheader("🔍 Investment Data Consistency Check")
    st.caption(
        "Detects anomalies in Quantity × Price, Commission, Total (Acc/Sec ccy) and FX Rate. "
        "Edit any field inline and click **Save Changes** to persist."
    )

    # ── Filters ──────────────────────────────────────────────────────────────
    f1, f2, f3 = st.columns(3)

    all_accts = get_all_accounts_for_nwr()
    acct_name_to_id = dict(zip(all_accts["accounts_name"], all_accts["accounts_id"]))
    sel_acct_names  = f1.multiselect("Accounts", sorted(acct_name_to_id), key="ic_accts")
    account_ids     = [acct_name_to_id[n] for n in sel_acct_names] or None

    all_actions = ["Buy","Sell","Dividend","IntInc","RtrnCap",
                   "CashIn","CashOut","MiscExp","Reinvest","ShrIn","ShrOut"]
    sel_actions = f2.multiselect("Actions", all_actions, key="ic_actions")

    anomalies_only = f3.toggle("Anomalies only", value=True, key="ic_anom_only")

    if st.button("🔍 Run Check", type="primary", key="ic_run"):
        st.session_state["ic_df_raw"] = get_investment_consistency_data(account_ids)

    if "ic_df_raw" not in st.session_state:
        return

    df = st.session_state["ic_df_raw"].copy()
    if sel_actions:
        df = df[df["action"].isin(sel_actions)]

    if df.empty:
        st.info("No investment records found.")
        return

    # ── Anomaly detection ─────────────────────────────────────────────────────
    ATOL   = 0.10          # €/$  absolute tolerance
    PTOL   = 0.005         # 0.5% relative tolerance
    TRADEABLE = {"Buy", "Sell", "Reinvest", "ShrIn", "ShrOut"}

    def _detect(row):
        issues, recs = [], []
        qty     = row["quantity"]   or 0.0
        price   = row["price"]      or 0.0
        comm    = row["commission"] or 0.0
        t_acc   = row["total_acc"]
        t_sec   = row["total_sec"]
        fx      = row["fx_rate"]    or 1.0
        action  = row["action"]
        same    = row["acc_currency"] == row["sec_currency"]

        notional = qty * price

        # 1. Missing totals
        if t_acc is None:
            issues.append("NULL total_acc")
            if t_sec is not None:
                recs.append(f"Set total_acc = {t_sec * fx:.2f} (total_sec × fx)")
        if t_sec is None and qty and price and action in TRADEABLE:
            issues.append("NULL total_sec")
            recs.append(f"Set total_sec ≈ {notional:.2f} (qty × price)")

        # 2. FX rate anomalies
        if same and abs(fx - 1.0) > 0.001:
            issues.append(f"Same ccy but FX_Rate={fx:.4f}")
            recs.append("Set FX_Rate = 1.0")
        if not same and fx and abs(fx - 1.0) < 0.001:
            issues.append(f"Cross-ccy ({row['sec_currency']}/{row['acc_currency']}) but FX_Rate=1.0")
            recs.append("Look up correct FX rate for trade date")

        # 3. FX consistency: total_acc ≈ total_sec × fx
        if t_acc is not None and t_sec is not None and fx:
            expected_acc = t_sec * fx
            delta = abs(t_acc - expected_acc)
            tol   = max(ATOL, abs(expected_acc) * PTOL)
            if delta > tol:
                issues.append(
                    f"total_acc ({t_acc:.2f}) ≠ total_sec × fx ({expected_acc:.2f}), Δ={delta:.2f}"
                )
                recs.append(f"Set total_acc = {expected_acc:.2f}  OR  fx = {(t_acc/t_sec):.6f}")

        # 4. Price/qty consistency for tradeable actions
        if t_sec is not None and qty and price and action in TRADEABLE:
            # Expected sec total: notional ± commission (commission may be in acc ccy)
            # Allow ±comm + tolerance
            diff = abs(t_sec - notional)
            tol  = max(ATOL, comm + abs(notional) * PTOL)
            if diff > tol:
                issues.append(
                    f"total_sec ({t_sec:.2f}) ≠ qty×price ({notional:.2f}), Δ={diff:.2f}"
                )
                expected_sec = notional + comm if action in ("Buy","ShrIn","Reinvest") \
                               else max(0.0, notional - comm)
                recs.append(f"Expected total_sec ≈ {expected_sec:.2f}")

        # 5. Zero price or qty for tradeable
        if action in TRADEABLE:
            if not qty:
                issues.append("Quantity is 0 / NULL")
            if not price:
                issues.append("Price is 0 / NULL")

        return "; ".join(issues) if issues else "", "; ".join(recs) if recs else ""

    df[["anomalies", "recommendations"]] = df.apply(
        _detect, axis=1, result_type="expand"
    )

    df_show = df if not anomalies_only else df[df["anomalies"] != ""]

    # ── Summary banner ────────────────────────────────────────────────────────
    n_total  = len(df)
    n_issues = (df["anomalies"] != "").sum()
    c1, c2, c3 = st.columns(3)
    c1.metric("Total records", n_total)
    c2.metric("⚠️ Anomalies",  n_issues)
    c3.metric("✅ Clean",       n_total - n_issues)

    if df_show.empty:
        st.success("No anomalies found — all investment records look consistent! 🎉")
        return

    # ── Editable table ───────────────────────────────────────────────────────
    st.markdown(f"Showing **{len(df_show)}** record(s).")

    edit_cols = ["investments_id", "date", "account", "security", "action",
                 "quantity", "price", "commission",
                 "total_acc", "total_sec", "fx_rate",
                 "acc_currency", "sec_currency",
                 "anomalies", "recommendations"]
    df_edit = df_show[edit_cols].copy()
    df_edit["date"] = df_edit["date"].dt.strftime("%Y-%m-%d")

    edited = st.data_editor(
        df_edit,
        disabled=["investments_id", "date", "account", "security", "action",
                  "acc_currency", "sec_currency", "anomalies", "recommendations"],
        column_config={
            "investments_id":  st.column_config.NumberColumn("ID",           format="%d", width="small"),
            "date":            st.column_config.TextColumn("Date",           width="small"),
            "account":         st.column_config.TextColumn("Account"),
            "security":        st.column_config.TextColumn("Security"),
            "action":          st.column_config.TextColumn("Action",         width="small"),
            "quantity":        st.column_config.NumberColumn("Qty",           format="%.6f"),
            "price":           st.column_config.NumberColumn("Price",         format="%.6f"),
            "commission":      st.column_config.NumberColumn("Commission",    format="%.4f"),
            "total_acc":       st.column_config.NumberColumn("Total (acc)",   format="%.4f"),
            "total_sec":       st.column_config.NumberColumn("Total (sec)",   format="%.4f"),
            "fx_rate":         st.column_config.NumberColumn("FX Rate",       format="%.6f"),
            "acc_currency":    st.column_config.TextColumn("Acc Ccy",         width="small"),
            "sec_currency":    st.column_config.TextColumn("Sec Ccy",         width="small"),
            "anomalies":       st.column_config.TextColumn("⚠️ Anomalies",    width="large"),
            "recommendations": st.column_config.TextColumn("💡 Recommendations", width="large"),
        },
        hide_index=True,
        width='stretch',
        key="ic_editor",
        num_rows="fixed",
    )

    copy_df_button(df_edit, key="dl_ic_report")

    # ── Save ─────────────────────────────────────────────────────────────────
    _editable = ["quantity", "price", "commission", "total_acc", "total_sec", "fx_rate"]

    def _has_changes():
        for col in _editable:
            orig = df_edit[col].fillna(0.0)
            curr = edited[col].fillna(0.0)
            if not orig.equals(curr):
                return True
        return False

    if st.button("💾 Save Changes", key="ic_save", disabled=not _has_changes()):
        saved, errors = 0, []
        for i, row in edited.iterrows():
            orig = df_edit.iloc[i]
            changes = {}
            for col in _editable:
                ov = orig[col]
                nv = row[col]
                # Treat NaN == NaN as equal
                if pd.isna(ov) and pd.isna(nv):
                    continue
                if pd.isna(ov) != pd.isna(nv) or (not pd.isna(ov) and abs(float(ov) - float(nv)) > 1e-9):
                    changes[col] = None if pd.isna(nv) else float(nv)
            if changes:
                try:
                    update_investment_row(int(row["investments_id"]), changes)
                    saved += 1
                except Exception as e:
                    errors.append(f"Row {i+1} (ID {int(row['investments_id'])}): {e}")
        for e in errors:
            st.error(e)
        if saved:
            st.success(f"Saved {saved} row(s). Re-run the check to refresh anomaly flags.")
            st.session_state.pop("ic_df_raw", None)
        elif not errors:
            st.info("No changes detected.")


def render_reports():
    """Render the Reports page."""
    st.title("Reports")

    hist_sub_menu = st.sidebar.selectbox(
        "Select Report:",
        [
            "Net Worth Report",
            "Income & Expense",
            "Cash Flow Forecast",
            "Investment Positions",
            "Investment Performance",
            "Investment Tax Report",
            "Securities & Portfolio Analysis",
            "Budget & Spending",
            "Custom Reports",
            "Financial Planning",
        ],
        key="hist_sub_nav",
    )

    # Force a clean rerun whenever the sub-menu changes so that Streamlit fully
    # replaces the previous report's widget tree (especially large st.tabs blocks)
    # rather than leaving orphaned tab content visible in the DOM.
    _prev = st.session_state.get("_prev_hist_sub_nav")
    if _prev is not None and _prev != hist_sub_menu:
        st.session_state["_prev_hist_sub_nav"] = hist_sub_menu
        st.rerun()
    st.session_state["_prev_hist_sub_nav"] = hist_sub_menu

    if hist_sub_menu == "Net Worth Report":
        render_net_worth_report()

    elif hist_sub_menu == "Investment Positions":
        last_day_prev_month = pd.Timestamp.now().replace(day=1) - pd.Timedelta(days=1)

        min_inv_date = st.sidebar.date_input(
            "📅 Start Date",
            value=st.session_state.inv_date_val,
            min_value=datetime(1900, 1, 1),
            max_value=last_day_prev_month,
            key="inv_date"
        )
        st.session_state.inv_date_val = min_inv_date

        df_raw = get_hist_inv_positions_data(min_inv_date)

        tab_graph, tab_data, tab_details, tab_alloc, tab_sector, tab_fx = st.tabs([
            "📊 Graph", "📋 Summary per Account", "🔍 Detail Analysis",
            "🥧 Asset Allocation", "🏭 Sector & Industry", "🌍 FX Exposure",
        ])

        # Pre-compute pivot only when data exists; the last two tabs have their own queries.
        if not df_raw.empty:
            df_acc_daily = df_raw.groupby(['date', 'accounts_name'])['value_in_eur'].sum().reset_index()
            df_total = df_raw.groupby('date')['value_in_eur'].sum().reset_index()
            df_total['accounts_name'] = 'TOTAL'
            df_plot  = pd.concat([df_acc_daily, df_total])
            df_pivot = df_plot.pivot(index='date', columns='accounts_name', values='value_in_eur').fillna(0).reset_index()
        else:
            df_pivot = pd.DataFrame()
        
        with tab_graph:
            st.subheader("📈 Investment Position Progress (Monthly)")
            if df_pivot.empty:
                st.warning("No data found for the selected period.")
            else:
                fig = px.line(df_pivot, x="date", y=[c for c in df_pivot.columns if c != 'date'],
                             title="<b>Investment Value per Account</b>", template="plotly_dark")
                fig.for_each_trace(lambda t: t.update(line=dict(color="white", width=4) if t.name == "TOTAL" else dict(width=2)))
                pf_plotly_chart(fig)

                st.markdown("#### 📉 Portfolio Drawdown")
                df_dd = df_total.copy().sort_values('date')
                df_dd['peak'] = df_dd['value_in_eur'].cummax()
                df_dd['drawdown_pct'] = (df_dd['value_in_eur'] - df_dd['peak']) / df_dd['peak'].replace(0, float('nan')) * 100
                max_dd = df_dd['drawdown_pct'].min()
                st.caption(f"Max drawdown over the selected period: **{max_dd:.2f}%**")
                fig_dd = px.area(
                    df_dd, x='date', y='drawdown_pct',
                    title="<b>Drawdown from Peak (%)</b>",
                    template="plotly_dark",
                    color_discrete_sequence=["#E74C3C"],
                    labels={'drawdown_pct': 'Drawdown (%)', 'date': 'Date'}
                )
                fig_dd.update_layout(yaxis_tickformat='.1f', margin=dict(l=0, r=0, t=50, b=0))
                pf_plotly_chart(fig_dd)


        with tab_data:
            st.subheader("📋 Monthly Summary per Account")
            if df_pivot.empty:
                st.warning("No data found for the selected period.")
            else:
                df_summary = df_pivot.sort_values('date', ascending=False).copy()
                df_summary['date'] = pd.to_datetime(df_summary['date']).dt.strftime('%Y-%m-%d')
                numeric_cols = [col for col in df_summary.columns if col != 'date']
                col_config = {"date": st.column_config.TextColumn("Date")}
                for col in numeric_cols:
                    col_config[col] = st.column_config.NumberColumn(label=col, format="%,.2f €")
                df_summary = df_summary.set_index('date')
                st.dataframe(df_summary, hide_index=False, width='stretch', column_config=col_config)
                copy_df_button(df_summary, key="dl_rpt_hist_inv_summary")

        with tab_details:
            st.subheader("🔍 Drill-down per Security")
            if df_raw.empty:
                st.warning("No data found for the selected period.")
            else:
                available_dates = sorted(df_raw['date'].unique(), reverse=True)
                selected_date = st.selectbox("Select Snapshot Date:", available_dates)
                df_snapshot = df_raw[df_raw['date'] == selected_date].copy()
                st.dataframe(
                    df_snapshot[['accounts_name', 'securities_name', 'qty_at_date', 'price_at_date', 'value_in_eur']],
                    column_config={
                        "accounts_name":  "Account",
                        "securities_name": "Security",
                        "qty_at_date":    st.column_config.NumberColumn("Quantity", format="%,.8f"),
                        "price_at_date":  st.column_config.NumberColumn("Price",    format="%,.2f"),
                        "value_in_eur":   st.column_config.NumberColumn("Value (€)", format="%,.2f €"),
                    },
                    hide_index=True, width="stretch"
                )
                copy_df_button(df_snapshot, key="dl_rpt_hist_inv_detail")

        with tab_alloc:
            render_asset_allocation()

        with tab_sector:
            render_sector_allocation()

        with tab_fx:
            render_fx_exposure()

    elif hist_sub_menu == "Investment Performance":
        # ── Shared preset selector (drives Benchmark, Risk Metrics, Correlation, Monte Carlo) ──
        _perf_inv_accts  = get_investment_accounts()
        _perf_id_to_name = dict(zip(_perf_inv_accts['accounts_id'], _perf_inv_accts['accounts_name']))
        _perf_name_to_id = {v: k for k, v in _perf_id_to_name.items()}
        _perf_all_names  = list(_perf_inv_accts['accounts_name'])
        _PERF_BUILTIN    = "Full Portfolio"

        _perf_presets_df = get_benchmark_presets()
        _perf_preset_map = {}
        for _, _pp in _perf_presets_df.iterrows():
            _perf_preset_map[_pp['preset_name']] = _pp['account_ids'] or []

        with st.expander("⚙️ Portfolio Preset", expanded=False):
            _pp1, _pp2, _pp3, _pp4 = st.columns([3, 3, 1, 1])
            with _pp1:
                sel_preset = st.selectbox(
                    "Preset",
                    [_PERF_BUILTIN] + sorted(_perf_preset_map.keys()),
                    key="perf_preset_sel",
                    help="Drives which accounts are used in Benchmark, Risk Metrics, Correlation and Monte Carlo.",
                )
            with _pp2:
                _perf_name_input = st.text_input(
                    "Preset name",
                    value="" if sel_preset == _PERF_BUILTIN else sel_preset,
                    placeholder="Name to save as…",
                    key="perf_preset_name_input",
                    label_visibility="collapsed",
                )
            with _pp3:
                _perf_save = st.button("💾 Save", key="perf_save_btn", width="stretch")
            with _pp4:
                _perf_del = st.button(
                    "🗑️ Delete", key="perf_del_btn",
                    disabled=(sel_preset == _PERF_BUILTIN),
                    width="stretch",
                )

            if sel_preset == _PERF_BUILTIN:
                _perf_default_names = _perf_all_names
            else:
                _perf_saved_ids     = _perf_preset_map.get(sel_preset) or []
                _perf_default_names = [_perf_id_to_name[i] for i in _perf_saved_ids if i in _perf_id_to_name]

            _perf_sel_names = st.multiselect(
                "Accounts in this preset",
                options=_perf_all_names,
                default=_perf_default_names,
                key=f"perf_accts_{sel_preset}",
                disabled=(sel_preset == _PERF_BUILTIN),
                help="Accounts included in all performance analyses on this page.",
            )
            _perf_sel_ids = (
                None
                if sel_preset == _PERF_BUILTIN
                else (tuple(_perf_name_to_id[n] for n in _perf_sel_names if n in _perf_name_to_id) or None)
            )

            if _perf_save:
                _pname = _perf_name_input.strip()
                if not _pname or _pname == _PERF_BUILTIN:
                    st.warning("Please enter a valid preset name (not 'Full Portfolio').")
                elif not _perf_sel_names:
                    st.warning("Select at least one account before saving.")
                else:
                    upsert_benchmark_preset(_pname, _perf_sel_ids)
                    get_benchmark_presets.clear()
                    st.toast(f"Preset '{_pname}' saved.", icon="✅")
                    st.rerun()

            _perf_del_key = f"perf_del_confirm_{sel_preset}"
            if _perf_del and sel_preset != _PERF_BUILTIN:
                st.session_state[_perf_del_key] = True
                st.rerun()
            if st.session_state.get(_perf_del_key):
                st.warning(f"Delete preset **'{sel_preset}'**? This cannot be undone.")
                _pd1, _pd2 = st.columns(2)
                with _pd1:
                    if st.button("Cancel", key="perf_del_cancel"):
                        st.session_state.pop(_perf_del_key, None)
                        st.rerun()
                with _pd2:
                    if st.button("Yes, delete", key="perf_del_yes", type="primary"):
                        _pmatch = _perf_presets_df[_perf_presets_df['preset_name'] == sel_preset]
                        if not _pmatch.empty:
                            delete_benchmark_preset(int(_pmatch.iloc[0]['preset_id']))
                        get_benchmark_presets.clear()
                        st.session_state.pop(_perf_del_key, None)
                        st.toast(f"Preset '{sel_preset}' deleted.", icon="🗑️")
                        st.rerun()

        _risk_acct_ids = _perf_sel_ids

        tab_report, tab_perf, tab_savings, tab_dividends, tab_bonds, tab_bm, tab_risk, tab_corr, tab_monte, tab_returns = st.tabs([
            "📊 P&L Report", "🏆 Performance", "💰 Savings", "💸 Dividend Tracker",
            "📋 Bond Schedule", "📈 Benchmark", "⚡ Risk Metrics", "🔗 Correlation", "🎲 Monte Carlo",
            "📐 TWR / MWR",
        ])
        
        with tab_report:
            st.subheader("📈 Profit & Loss")
         
            df_pnl = get_pnl_report_data()
            if df_pnl is not None:
                if df_pnl.empty:
                    st.warning("No P&L data found. Please refresh the report.")
                    return
            else:
                st.error("Error fetching P&L data. Please try refreshing.")
                return

            if st.sidebar.button("🔄 Recalculate P&L", key="recalculate_pnl_btn"):
                get_pnl_report_data.clear()
                with st.spinner("Running :green[update_holdings()]"):
                    update_holdings()
                st.cache_data.clear()
                st.rerun()

            if st.sidebar.button ("🔄 Refresh Market Data", key="refresh_market_data_btn"):
                get_pnl_report_data.clear()
                with st.spinner("Running :green[download_historical_fx('2d')]"):
                    download_historical_fx("2d")
                with st.spinner("Running :green[download_historical_prices_from_yahoo('2d')]"):
                    download_historical_prices_from_yahoo("2d")
                with st.spinner("Running :green[download_historical_prices_from_tradingview('2d')]"):
                    download_historical_prices_from_tradingview("2d")
                with st.spinner("Running :green[download_bond_prices_from_solidus()]"):
                    download_bond_prices_from_solidus()
                with st.spinner("Running :green[update_holdings()]"):
                    update_holdings()
                st.cache_data.clear()
                st.rerun()
            
            try:
                summary = {
                    "Total Current Value": df_pnl['current_value_eur'].sum(),
                    "Total Realized P&L": df_pnl['realized_pnl_eur'].fillna(0).sum(),
                    "Total Unrealized P&L": df_pnl['unrealized_pnl_eur'].fillna(0).sum(),
                    "Total Net All Time P&L": df_pnl['pnl_net_all_time_eur'].fillna(0).sum(),
                    "Total Daily P&L": df_pnl['pnl_dtd_eur'].sum(),
                    "Total Weekly P&L": df_pnl['pnl_wtd_eur'].sum(),
                    "Total Monthly P&L": df_pnl['pnl_mtd_eur'].sum(),
                    "Total Quarterly P&L": df_pnl['pnl_qtd_eur'].sum(),
                    "Total YTD P&L": df_pnl['pnl_ytd_eur'].sum(),
                    "Total Daily Market P&L": df_pnl['pnl_dtd_market_eur'].sum(),
                    "Total Daily FX P&L": df_pnl['pnl_dtd_fx_eur'].sum(),
                    "Total YTD Market P&L": df_pnl['pnl_ytd_market_eur'].sum(),
                    "Total YTD FX P&L": df_pnl['pnl_ytd_fx_eur'].sum(),
                    "Total YTD Realized P&L": df_pnl['realized_pnl_ytd_eur'].fillna(0).sum(),
                    "Total YTD Unrealized P&L": df_pnl['unrealized_pnl_ytd_eur'].fillna(0).sum()
                }

                row1_col1, row1_col2, row1_col3, row1_col4 = st.columns(4)

                with row1_col1:
                    total_dtd_pnl = df_pnl['pnl_dtd_eur'].sum()
                    total_current_value = df_pnl['current_value_eur'].sum()
                    _prev_value = total_current_value - total_dtd_pnl
                    _dtd_pct = (total_dtd_pnl / _prev_value * 100) if _prev_value != 0 else 0
                    st.metric(
                        "Total Current Value (EUR)",
                        f"{total_current_value:,.2f} €",
                        delta=f"{total_dtd_pnl:,.2f} €  ({_dtd_pct:+.2f}%)",
                    )
 
                with row1_col2:
                    total_realized_pnl = df_pnl['realized_pnl_eur'].fillna(0).sum()
                    custom_metric(
                        label="Total Realized P&L", 
                        value=f"{total_realized_pnl:,.2f} €", 
                        pnl_value=total_realized_pnl
                    )

                with row1_col3:
                    total_unrealized_pnl = df_pnl['unrealized_pnl_eur'].fillna(0).sum()
                    custom_metric(
                        label="Total Unrealized P&L", 
                        value=f"{total_unrealized_pnl:,.2f} €", 
                        pnl_value=total_unrealized_pnl
                    )

                with row1_col4:
                    total_net_all_time_pnl = df_pnl['pnl_net_all_time_eur'].fillna(0).sum()
                    custom_metric(
                        label="Total Net All Time P&L", 
                        value=f"{total_net_all_time_pnl:,.2f} €", 
                        pnl_value=total_net_all_time_pnl
                    )

                row2_col1, row2_col2, row2_col3, row2_col4, row2_col5 = st.columns(5)

                with row2_col1:
                    total_dtd_pnl = df_pnl['pnl_dtd_eur'].sum()
                    custom_metric(
                        label="Total Daily P&L", 
                        value=f"{total_dtd_pnl:,.2f} €", 
                        pnl_value=total_dtd_pnl
                    )

                with row2_col2:
                    total_wtd_pnl = df_pnl['pnl_wtd_eur'].sum()
                    custom_metric(
                        label="Total Weekly P&L", 
                        value=f"{total_wtd_pnl:,.2f} €", 
                        pnl_value=total_wtd_pnl
                    )

                with row2_col3:
                    total_mtd_pnl = df_pnl['pnl_mtd_eur'].sum()
                    custom_metric(
                        label="Total Monthly P&L", 
                        value=f"{total_mtd_pnl:,.2f} €", 
                        pnl_value=total_mtd_pnl
                    )

                with row2_col4:                    
                    total_qtd_pnl = df_pnl['pnl_qtd_eur'].sum()
                    custom_metric(
                        label="Total Quarterly P&L",
                        value=f"{total_qtd_pnl:,.2f} €",
                        pnl_value=total_qtd_pnl
                    )

                with row2_col5:
                    total_ytd_pnl = df_pnl['pnl_ytd_eur'].sum()
                    custom_metric(
                        label="Total YTD P&L", 
                        value=f"{total_ytd_pnl:,.2f} €", 
                        pnl_value=total_ytd_pnl
                    )


            #    st.write("---") # Διαχωριστική γραμμή
                m_col1, m_col2, m_col3, m_col4, m_col5, m_col6 = st.columns(6)


                with m_col1:
                    # Χρήση f-string και πρόσβαση στο dictionary με []
                    st.markdown(f"""
                        <div style="line-height: 1.5;text-align: center;">
                            <p style="color: grey; font-size: 14px; margin: 0; font-family: sans-serif;">Daily Market / FX Split</p>
                            <p style="margin: 0; font-weight: bold;">
                                <span style="color: {get_color(summary['Total Daily Market P&L'])};">{summary['Total Daily Market P&L']:+,.2f} €</span> / 
                                <span style="color: {get_color(summary['Total Daily FX P&L'])};">{summary['Total Daily FX P&L']:+,.2f} €</span>
                            </p>
                        </div>
                    """, unsafe_allow_html=True)

                with m_col5:
                    st.markdown(f"""
                        <div style="line-height: 1.5;text-align: center;">
                            <p style="color: grey; font-size: 14px; margin: 0; font-family: sans-serif;">YTD Realized / Unrealized</p>
                            <p style="margin: 0; font-weight: bold;">
                                <span style="color: {get_color(summary['Total YTD Realized P&L'])};">{summary['Total YTD Realized P&L']:+,.2f} €</span> / 
                                <span style="color: {get_color(summary['Total YTD Unrealized P&L'])};">{summary['Total YTD Unrealized P&L']:+,.2f} €</span>
                            </p>
                        </div>
                    """, unsafe_allow_html=True)

                with m_col6:
                    st.markdown(f"""
                        <div style="line-height: 1.5;text-align: center;">
                            <p style="color: grey; font-size: 14px; margin: 0; font-family: sans-serif;">YTD Market / FX</p>
                            <p style="margin: 0; font-weight: bold;">
                                <span style="color: {get_color(summary['Total YTD Market P&L'])};">{summary['Total YTD Market P&L']:+,.2f} €</span> / 
                                <span style="color: {get_color(summary['Total YTD FX P&L'])};">{summary['Total YTD FX P&L']:+,.2f} €</span>
                            </p>
                        </div>
                    """, unsafe_allow_html=True)


                # 1. Καθαρισμός από τυχόν προϋπάρχοντα σύνολα της SQL για αποφυγή double-counting
                df_input = df_pnl[df_pnl['securities_name'].notnull()].copy()

                # 2. Υπολογισμός Κόστους μόνο για τις ανοιχτές θέσεις (Denominator για το YOC)
                # Το Current Value - Unrealized P&L μας δίνει το κεφάλαιο που είναι "κλειδωμένο" τώρα
                df_input['current_cost_basis_eur'] = df_input['current_value_eur'] - df_input['unrealized_pnl_eur']

                # 3. Υπολογισμός αναμενόμενου μερίσματος (Numerator για το YOC)
                df_input['annual_div_cash_eur'] = (df_input['dividend_yoc_pct'] / 100) * df_input['current_cost_basis_eur']

                # 4. Ομαδοποίηση
                # Percentage columns must NOT be aggregated directly — averaging
                # percentages across positions gives a wrong result.  They are
                # recomputed from the aggregated EUR totals after the groupby.
                _PCT_COLS = {'pnl_dtd_percent', 'pnl_ytd_percent', 'pnl_net_all_time_percent'}

                agg_dict = {
                    'current_value_eur': 'sum',
                    'pnl_dtd_market_eur': 'sum',
                    'pnl_dtd_fx_eur': 'sum',
                    'pnl_dtd_eur': 'sum',
                    'pnl_wtd_eur': 'sum',
                    'pnl_mtd_eur': 'sum',
                    'pnl_qtd_eur': 'sum',
                    'pnl_ytd_market_eur': 'sum',
                    'pnl_ytd_fx_eur': 'sum',
                    'realized_pnl_ytd_eur': 'sum',
                    'unrealized_pnl_ytd_eur': 'sum',
                    'pnl_ytd_eur': 'sum',
                    'pnl_all_time_eur': 'sum',
                    'realized_pnl_eur': 'sum',
                    'unrealized_pnl_eur': 'sum',
                    'pnl_net_all_time_eur': 'sum',
                    'gross_invested_all_time_eur': 'sum',
                    'direct_cashin_eur': 'first',   # same value for every row of an account
                    'linked_cashin_eur': 'first',
                    'annual_div_cash_eur': 'sum',
                    'current_cost_basis_eur': 'sum',
                }

                # Add any other pnl_* EUR columns dynamically, but skip percentage columns
                for col in df_input.columns:
                    if col.startswith('pnl_') and col not in agg_dict and col not in _PCT_COLS:
                        agg_dict[col] = 'sum'

                df_acc = df_input.groupby('accounts_name').agg(agg_dict)

                # 5. Recompute percentage columns from aggregated EUR totals.
                #    DTD/YTD: P&L / (current_value − P&L) = P&L / period_start_value.
                #    ROI %: (current_value + withdrawals − deposits) / deposits
                #           treats the account as a black box; avoids double-counting
                #           internal reinvestments (sell A → buy B).
                _safe = lambda s: s.replace(0, np.nan)

                df_acc['pnl_dtd_percent'] = (
                    df_acc['pnl_dtd_eur']
                    / _safe(df_acc['current_value_eur'] - df_acc['pnl_dtd_eur'])
                    * 100
                ).fillna(0)

                df_acc['pnl_ytd_percent'] = (
                    df_acc['pnl_ytd_eur']
                    / _safe(df_acc['current_value_eur'] - df_acc['pnl_ytd_eur'])
                    * 100
                ).fillna(0)

                # Account-level ROI denominator — three-tier priority:
                #
                # 1. direct_cashin_eur  — explicit CashIn (Securities_Id IS NULL) in
                #    Investments: pensions, accounts where contributions are recorded
                #    before a Buy. Best: captures the true external capital deployed.
                #
                # 2. linked_cashin_eur  — transfers recorded in the LINKED cash account
                #    with Accounts_Id_Target = this investment account.  Buy/Sell-linked
                #    Transactions have Accounts_Id_Target = NULL so they are excluded;
                #    no double-counting with gross_invested.
                #
                # 3. gross_invested_all_time_eur — sum of all Buy amounts (fallback).
                #    Correct for simple accounts; understates ROI when proceeds are
                #    reinvested (the reinvested amount is counted twice in the denominator).
                def _roi_denom(row):
                    if row['direct_cashin_eur'] > 0:
                        return row['direct_cashin_eur']
                    if row['linked_cashin_eur'] > 0:
                        return row['linked_cashin_eur']
                    if row['gross_invested_all_time_eur'] > 0:
                        return row['gross_invested_all_time_eur']
                    return np.nan

                df_acc['pnl_net_all_time_percent'] = (
                    df_acc['pnl_net_all_time_eur']
                    / df_acc.apply(_roi_denom, axis=1)
                    * 100
                ).fillna(0)

                # 6. Weighted Yield on Cost for the account
                df_acc['dividend_yoc_pct'] = (
                    df_acc['annual_div_cash_eur']
                    / _safe(df_acc['current_cost_basis_eur'])
                    * 100
                ).fillna(0)

                # 7. Drop helper columns before display
                df_acc = df_acc.drop(columns=['pnl_all_time_eur', 'annual_div_cash_eur', 'current_cost_basis_eur',
                                              'gross_invested_all_time_eur', 'direct_cashin_eur', 'linked_cashin_eur'])

                st.divider() # Optional separation line for better visual effect

                # ── Display filters (affect both tables) ──────────────────────
                col1, col2, col3, col4 = st.columns(4)

                with col1:
                    show_closed_accounts = st.checkbox("Show Closed Accounts", value=False)
                with col2:
                    show_market_fx_split = st.checkbox("Show Market/FX Split", value=False)
                with col3:
                    show_realized_unrealized_split = st.checkbox("Show Realized/Unrealized Split", value=False)
                with col4:
                    show_pnl_percent = st.checkbox("Show P&L %", value=True)

                # Filter the account-level table: closed accounts have zero current value
                if not show_closed_accounts:
                    df_acc = df_acc[df_acc['current_value_eur'] != 0]

                if not show_market_fx_split:
                    df_acc = df_acc.drop(columns=['pnl_dtd_market_eur','pnl_dtd_fx_eur', 'pnl_ytd_market_eur', 'pnl_ytd_fx_eur'])
                if not show_realized_unrealized_split:
                    df_acc = df_acc.drop(columns=['realized_pnl_ytd_eur', 'unrealized_pnl_ytd_eur', 'realized_pnl_eur', 'unrealized_pnl_eur'])
                if not show_pnl_percent:
                    df_acc = df_acc.drop(columns=['pnl_dtd_percent', 'pnl_ytd_percent', 'pnl_net_all_time_percent'])

                df_acc = df_acc.rename(columns={
                    'current_value_eur': 'Current Value',
                    'pnl_dtd_market_eur': 'Daily Market P&L',
                    'pnl_dtd_fx_eur': 'Daily FX P&L',
                    'pnl_dtd_eur': 'Daily P&L',
                    'pnl_dtd_percent': 'Daily P&L %',
                    'pnl_wtd_eur': 'Weekly P&L',
                    'pnl_mtd_eur': 'Monthly P&L',
                    'pnl_qtd_eur': 'Quarterly P&L',
                    'pnl_ytd_market_eur': 'YTD Market P&L',
                    'pnl_ytd_fx_eur': 'YTD FX P&L',
                    'realized_pnl_ytd_eur': 'YTD Realized P&L',
                    'unrealized_pnl_ytd_eur': 'YTD Unrealized P&L',
                    'pnl_ytd_eur': 'YTD P&L',
                    'pnl_ytd_percent': 'YTD P&L %',
                #    'pnl_all_time_eur': 'Total P&L',
                    'realized_pnl_eur': 'Realized P&L',
                    'unrealized_pnl_eur': 'Unrealized P&L',
                    'pnl_net_all_time_eur': 'Total Net P&L',
                    'pnl_net_all_time_percent': 'ROI %',
                    'dividend_yoc_pct': 'Annual YOC %'
                })

                # Reorder columns so each % column appears immediately after its EUR sibling
                _col_order = [
                    'Current Value',
                    'Daily Market P&L', 'Daily FX P&L',
                    'Daily P&L', 'Daily P&L %',
                    'Weekly P&L', 'Monthly P&L', 'Quarterly P&L',
                    'YTD Market P&L', 'YTD FX P&L',
                    'YTD Realized P&L', 'YTD Unrealized P&L',
                    'YTD P&L', 'YTD P&L %',
                    'Realized P&L', 'Unrealized P&L',
                    'Total Net P&L', 'ROI %',
                    'Annual YOC %',
                ]
                df_acc = df_acc[[c for c in _col_order if c in df_acc.columns]]

                df_acc.index.name = "Account"
            #    st.dataframe(df_acc.style.map(color_negative_red).format("{:,.2f} €"), width="stretch")

                # Ορίζουμε τις στήλες που θέλουν σύμβολο € (όλες εκτός από τα % columns)
                _pct_display_cols = {'Daily P&L %', 'YTD P&L %', 'ROI %', 'Annual YOC %', 'Price Day Change %'}
                euro_cols = [col for col in df_acc.columns if col not in _pct_display_cols]

                st.dataframe(
                    df_acc.style
                    .map(color_negative_red)
                    .format({
                        **{col: "{:,.2f} €" for col in euro_cols}, # Όλα τα υπόλοιπα σε €
                        'Daily P&L %': "{:+.2f}%",
                        'YTD P&L %': "{:+.2f}%",
                        'ROI %': "{:+.2f}%",
                        'Annual YOC %': "{:.4f}%"                  # Το YOC σε %
                    }),
                    width="stretch"
                )
                copy_df_button(df_acc, key="dl_rpt_pnl_account")



                # ── Security-level detail filters + account selector ─────────
                col1, col2, col3 = st.columns([1, 1, 2])

                with col1:
                    show_closed_positions = st.checkbox("Show Closed Positions", value=False)

                # Build acc_options using the same show_closed_accounts flag from above
                if show_closed_accounts:
                    acc_options = df_pnl['accounts_name'].unique()
                else:
                    active_accs = df_pnl.groupby('accounts_name')['current_value_eur'].sum()
                    acc_options = active_accs[active_accs != 0].index.tolist()

                with col3:
                    # Preserve the selected account across Recalculate/Refresh reruns.
                    #
                    # Problem: when st.rerun() is called mid-script (inside the button
                    # handler), the selectbox hasn't rendered yet in that pass.
                    # Streamlit's widget-cleanup mechanism then clears the widget key
                    # from session_state, so on the next render the selection is lost.
                    #
                    # Solution: a "shadow" key that is a plain session_state variable
                    # (not owned by any widget) — Streamlit never auto-clears it.
                    # We write to it every time the widget renders, and restore from it
                    # when the widget key has been wiped.
                    _acc_list   = list(acc_options)
                    _SHADOW_KEY = "pnl_account_select_saved"
                    _widget_val = st.session_state.get("pnl_account_select")
                    _shadow_val = st.session_state.get(_SHADOW_KEY)
                    # Pick the best candidate: prefer live widget value, fall back to shadow
                    _target = (
                        _widget_val if _widget_val in _acc_list else
                        _shadow_val if _shadow_val in _acc_list else
                        (_acc_list[0] if _acc_list else None)
                    )
                    if _target:
                        st.session_state["pnl_account_select"] = _target
                    selected_acc = st.selectbox(
                        "Select Account for Details:",
                        _acc_list,
                        key="pnl_account_select"
                    )
                    # Always keep shadow in sync so the next rerun can restore it
                    st.session_state[_SHADOW_KEY] = selected_acc

                with get_db() as conn:
                    query_acc_id = f"SELECT Accounts_Id FROM Accounts WHERE Accounts_Name = '{selected_acc}'"
                    df_acc_id = pd.read_sql(query_acc_id, conn)

                    if not df_acc_id.empty:
                        account_id = df_acc_id.iloc[0]['accounts_id']
                        query_holdings = f"""
                            SELECT h.Securities_Id, s.Securities_Name, h.Quantity 
                            FROM Holdings h 
                            JOIN Securities s ON h.Securities_Id = s.Securities_Id 
                            WHERE h.Accounts_Id = {account_id}
                        """
                        df_holdings = pd.read_sql(query_holdings, conn)

                        query_prices = f"""
                            SELECT DISTINCT ON (h.Securities_Id)
                                h.Securities_Id,
                                s.Securities_Name,
                                h.Quantity,
                                (SELECT hp.Close
                                FROM Historical_Prices hp
                                WHERE hp.Securities_Id = h.Securities_Id
                                AND hp.Date <= CURRENT_DATE
                                ORDER BY hp.Date DESC LIMIT 1) AS Latest_Price,
                                (SELECT hp.Close
                                FROM Historical_Prices hp
                                WHERE hp.Securities_Id = h.Securities_Id
                                AND hp.Date <= CURRENT_DATE
                                ORDER BY hp.Date DESC LIMIT 1 OFFSET 1) AS Prev_Price
                            FROM Holdings h
                            JOIN Securities s ON h.Securities_Id = s.Securities_Id
                            WHERE h.Accounts_Id = {account_id} AND h.Quantity != 0
                        """
                        df_prices = pd.read_sql(query_prices, conn)
                    else:
                        df_acc_id = pd.DataFrame()
                        df_holdings = pd.DataFrame()
                        df_prices = pd.DataFrame()

                if not df_acc_id.empty:
                    account_id = df_acc_id.iloc[0]['accounts_id']
                    
                    df_details = df_pnl[df_pnl['accounts_name'] == selected_acc].copy()
                    
                    if not df_holdings.empty:
                        df_details = df_details.merge(
                            df_holdings[['securities_name', 'quantity']], 
                            on='securities_name', 
                            how='left'
                        )
                    else:
                        df_details['quantity'] = 0
                    
                    if not df_prices.empty:
                        _price_cols = ['securities_name', 'latest_price']
                        if 'prev_price' in df_prices.columns:
                            _price_cols.append('prev_price')
                        df_details = df_details.merge(
                            df_prices[_price_cols],
                            on='securities_name',
                            how='left'
                        )
                    else:
                        df_details['latest_price'] = 0

                    df_details['quantity']     = df_details['quantity'].fillna(0)
                    df_details['latest_price'] = df_details['latest_price'].fillna(0)

                    if 'prev_price' in df_details.columns:
                        df_details['prev_price'] = df_details['prev_price'].fillna(0)
                        df_details['price_day_change'] = (
                            df_details['latest_price'] - df_details['prev_price']
                        )
                        df_details['price_day_change_pct'] = (
                            df_details['price_day_change']
                            / df_details['prev_price'].replace(0, float('nan'))
                            * 100
                        ).fillna(0)
                    else:
                        df_details['price_day_change']     = 0.0
                        df_details['price_day_change_pct'] = 0.0

                    # ── Recompute % columns from EUR totals ───────────────
                    # Averaging or summing percentages across rows is wrong.
                    # DTD/YTD use:  P&L / (current_value − P&L) = P&L / period_start_value.
                    # Total Net uses gross_invested (sum of all buy costs) as denominator.
                    # net_invested is WRONG for profitable/closed positions: when sells exceed
                    # buys the denominator goes negative, flipping the sign to e.g. -100%.
                    def _pct(pnl_col, base_col):
                        denom = (df_details[base_col] - df_details[pnl_col]).replace(0, float('nan'))
                        return (df_details[pnl_col] / denom * 100).fillna(0)

                    df_details['pnl_dtd_percent'] = _pct('pnl_dtd_eur', 'current_value_eur')
                    df_details['pnl_ytd_percent'] = _pct('pnl_ytd_eur', 'current_value_eur')
                    df_details['pnl_net_all_time_percent'] = (
                        df_details['pnl_net_all_time_eur']
                        / df_details['gross_invested_all_time_eur'].replace(0, float('nan'))
                        * 100
                    ).fillna(0)

                    df_display = df_details[[
                        'securities_name', 'quantity', 'latest_price', 'current_value_eur',
                        'pnl_dtd_market_eur', 'pnl_dtd_fx_eur',
                        'pnl_dtd_eur', 'pnl_dtd_percent', 'pnl_wtd_eur', 'pnl_mtd_eur', 'pnl_qtd_eur',
                        'pnl_ytd_market_eur', 'pnl_ytd_fx_eur',
                        'realized_pnl_ytd_eur', 'unrealized_pnl_ytd_eur',
                        'pnl_ytd_eur', 'pnl_ytd_percent', 'realized_pnl_eur', 'unrealized_pnl_eur',
                        'pnl_net_all_time_eur', 'pnl_net_all_time_percent',
                        'price_day_change', 'price_day_change_pct',
                        'dividend_yoc_pct'
                    ]].rename(columns={
                        'securities_name': 'Security',
                        'quantity': 'Quantity',        
                        'latest_price': 'Latest Price',      
                        'current_value_eur': 'Value (€)',
                        'pnl_dtd_market_eur': 'Daily Market P&L',
                        'pnl_dtd_fx_eur': 'Daily FX P&L',
                        'pnl_dtd_eur': 'Daily P&L',
                        'pnl_dtd_percent': 'Daily P&L %',
                        'pnl_wtd_eur': 'Weekly P&L',
                        'pnl_mtd_eur': 'Monthly P&L',
                        'pnl_qtd_eur': 'Quarterly P&L',
                        'pnl_ytd_market_eur': 'YTD Market P&L',
                        'pnl_ytd_fx_eur': 'YTD FX P&L',''
                        'realized_pnl_ytd_eur': 'YTD Realized P&L',
                        'unrealized_pnl_ytd_eur': 'YTD Unrealized P&L',
                        'pnl_ytd_eur': 'YTD P&L',
                        'pnl_ytd_percent': 'YTD P&L %',
                    #    'pnl_all_time_eur': 'Total P&L',
                        'realized_pnl_eur': 'Realized P&L',
                        'unrealized_pnl_eur': 'Unrealized P&L',
                        'pnl_net_all_time_eur': 'Total Net P&L',
                        'pnl_net_all_time_percent': 'ROI %',
                        'price_day_change': 'Price Day Change',
                        'price_day_change_pct': 'Price Day Change %',
                        'dividend_yoc_pct': 'Annual YOC %'
                    })

                    # 3. Φιλτράρισμα Κλειστών Θέσεων στο τελικό Dataframe
                    if not show_closed_positions:
                        df_display = df_display[df_display['Value (€)'] != 0]


                    pnl_cols = ['Daily Market P&L', 'Daily FX P&L', 'Daily P&L', 'Daily P&L %', 'Weekly P&L', 'Monthly P&L', 'Quarterly P&L', 'YTD Market P&L', 'YTD FX P&L', 'YTD Realized P&L', 'YTD Unrealized P&L', 'YTD P&L', 'YTD P&L %', 'Realized P&L', 'Unrealized P&L', 'Total Net P&L', 'ROI %', 'Price Day Change', 'Price Day Change %', 'Annual YOC %']

                    # 1. Set 'Security' as the index so Streamlit treats it as the frozen lead column
                    df_to_show = df_display.set_index('Security')

                    if not show_market_fx_split:
                        df_to_show = df_to_show.drop(columns=['Daily Market P&L','Daily FX P&L', 'YTD Market P&L', 'YTD FX P&L'])

                    if not show_realized_unrealized_split:
                        df_to_show = df_to_show.drop(columns=['YTD Realized P&L', 'YTD Unrealized P&L', 'Realized P&L', 'Unrealized P&L'])

                    if not show_pnl_percent:
                        df_to_show = df_to_show.drop(columns=['Daily P&L %', 'YTD P&L %', 'ROI %']) 

                    existing_pnl_cols = [col for col in pnl_cols if col in df_to_show.columns]

                    st.dataframe(
                    #    df_display.style
                        df_to_show.style
                    #    .map(color_negative_red, subset=pnl_cols)
                        .map(color_negative_red, subset=existing_pnl_cols)
                        .format({
                            **{col: "{:,.2f} €" for col in existing_pnl_cols
                               if col not in {'Price Day Change', 'Price Day Change %'}},
                            'Daily P&L %': "{:.2f}%",
                            'YTD P&L %': "{:.2f}%",
                            'ROI %': "{:.2f}%",
                            'Price Day Change': "{:+,.4f}",
                            'Price Day Change %': "{:+.2f}%",
                            'Value (€)': "{:,.2f} €",
                            'Latest Price': "{:,.2f}",
                            'Quantity': "{:,.8f}",
                            'Annual YOC %': "{:.4f}%"
                        }),
                        width="stretch",
                        hide_index=False
                    )
                    copy_df_button(df_to_show, key="dl_rpt_pnl_positions")

                else:
                    st.warning(f"No account found with name: {selected_acc}")
                    
            except Exception as e:
                st.error(f"Error: {e}")
                st.exception(e)


        with tab_savings:
            st.subheader("💰 Savings Accounts — Yield over Cost & APY")
            st.caption(
                "**Principal** = non-interest cash inflows (deposits/transfers in, excluding interest). "
                "**Total Interest** = sum of splits categorised as 'Interest'. "
                "**Cumulative YoC** = Total Interest ÷ Principal × 100. "
                "**APY** = (1 + Total Interest / Principal) ^ (365 / holding days) − 1, "
                "i.e. the compound annualised rate implied by actual interest earned over the holding period."
            )

            with get_db() as conn:
                df_savings = pd.read_sql("""
                    WITH CategorizedSplits AS (
                        SELECT
                            t.Accounts_Id,
                            t.Transactions_Id,
                            t.Date,
                            t.Transfers_Id,
                            CASE WHEN t.Transfers_Id IS NOT NULL THEN t.Total_Amount
                                 ELSE s.Amount
                            END AS Amount,
                            cat.Categories_Type,
                            CASE WHEN t.Transfers_Id IS NOT NULL THEN 'Principal'
                                 WHEN cat.Categories_Type = 'Interest' THEN 'Interest'
                                 ELSE 'Principal'
                            END AS Kind
                        FROM Transactions t--, Accounts a
                        LEFT JOIN Splits s   ON s.Transactions_Id = t.Transactions_Id
                        LEFT JOIN Categories cat ON cat.Categories_Id = s.Categories_Id
						LEFT JOIN Accounts a ON a.Accounts_Id = t.Accounts_Id
						WHERE a.Accounts_Type = 'Savings'
                    ),
                    -- Latest FX rate per non-EUR account for converting to EUR
                    NonEURAccounts AS (
                        SELECT DISTINCT a.Accounts_Id, a.Currencies_Id
                        FROM Accounts a
                        WHERE a.Currencies_Id NOT IN (
                            SELECT Currencies_Id FROM Currencies WHERE Currencies_ShortName = 'EUR'
                        )
                    ),
                    Last_FXRates AS (
                        SELECT nea.Accounts_Id, hfx.FX_Rate
                        FROM Historical_FX hfx
                        JOIN NonEURAccounts nea ON nea.Currencies_Id = hfx.Currencies_Id_1
                        WHERE hfx.Currencies_Id_2 = (
                                SELECT Currencies_Id FROM Currencies WHERE Currencies_ShortName = 'EUR'
                              )
                          AND hfx.Date = (
                                SELECT MAX(h2.Date)
                                FROM Historical_FX h2
                                WHERE h2.Currencies_Id_1 = hfx.Currencies_Id_1
                                  AND h2.Currencies_Id_2 = hfx.Currencies_Id_2
                                  AND h2.Date <= CURRENT_DATE
                              )
                    ),
                    -- Overall account stats (total principal, total interest, date range)
                    AccountStats AS (
                        SELECT
                            cs.Accounts_Id,
                            MIN(cs.Date)  AS first_tx_date,
                            MAX(cs.Date)  AS last_tx_date,
                            MAX(CASE WHEN cs.Kind = 'Interest' THEN cs.Date END) AS last_interest_date,
                            SUM(CASE WHEN cs.Kind = 'Principal'
                                     THEN COALESCE(cs.Amount, 0) ELSE 0 END)     AS principal,
                            SUM(CASE WHEN cs.Kind = 'Principal'
                                     THEN COALESCE(cs.Amount, 0) * COALESCE(fx.FX_Rate, 1)
                                     ELSE 0 END)                                 AS principal_eur,
                            SUM(CASE WHEN cs.Kind = 'Interest'
                                     THEN COALESCE(cs.Amount, 0) ELSE 0 END)     AS total_interest,
                            SUM(CASE WHEN cs.Kind = 'Interest'
                                     THEN COALESCE(cs.Amount, 0) * COALESCE(fx.FX_Rate, 1)
                                     ELSE 0 END)                                 AS total_interest_eur
                        FROM CategorizedSplits cs
                        LEFT JOIN Last_FXRates fx ON fx.Accounts_Id = cs.Accounts_Id
                        GROUP BY cs.Accounts_Id
                    ),
                    -- The two most recent interest payment dates per account
                    InterestDates AS (
                        SELECT cs.Accounts_Id, cs.Date AS interest_date,
                               ROW_NUMBER() OVER (
                                   PARTITION BY cs.Accounts_Id
                                   ORDER BY cs.Date DESC
                               ) AS rn
                        FROM (
                            SELECT DISTINCT Accounts_Id, Date
                            FROM CategorizedSplits
                            WHERE Kind = 'Interest'
                        ) cs
                    ),
                    LastInterestDate  AS (SELECT Accounts_Id, interest_date AS last_interest_date
                                          FROM InterestDates WHERE rn = 1),
                    PriorInterestDate AS (SELECT Accounts_Id, interest_date AS prior_interest_date
                                          FROM InterestDates WHERE rn = 2),
                    -- Interest earned in the last interest period
                    -- (from prior interest date up to and including last interest date)
                    LastPeriodInterest AS (
                        SELECT cs.Accounts_Id,
                               SUM(cs.Amount) AS last_interest_sum,
                               SUM(cs.Amount * COALESCE(fx.FX_Rate, 1)) AS last_interest_sum_eur
                        FROM CategorizedSplits cs
                        JOIN LastInterestDate li ON li.Accounts_Id = cs.Accounts_Id
                        LEFT JOIN PriorInterestDate pi ON pi.Accounts_Id = cs.Accounts_Id
                        LEFT JOIN Last_FXRates fx ON fx.Accounts_Id = cs.Accounts_Id
                        WHERE cs.Kind = 'Interest'
                          AND cs.Date = li.last_interest_date
                        GROUP BY cs.Accounts_Id
                    ),
                    -- Running balance at the start of the last interest period.
                    -- = sum of all principal movements up to and including the prior interest date
                    -- (or up to first tx if no prior interest date exists).
                    PeriodStartBalance AS (
                        SELECT cs.Accounts_Id,
                               SUM(cs.Amount) AS period_start_balance,
                               SUM(cs.Amount * COALESCE(fx.FX_Rate, 1)) AS period_start_balance_eur
                        FROM CategorizedSplits cs
                        LEFT JOIN PriorInterestDate pi ON pi.Accounts_Id = cs.Accounts_Id
                        LEFT JOIN Last_FXRates fx ON fx.Accounts_Id = cs.Accounts_Id
						WHERE cs.Date <= pi.prior_interest_date										  
                        GROUP BY cs.Accounts_Id
                    ),
                    PeriodEndBalance AS (
                        SELECT cs.Accounts_Id,
                               SUM(cs.Amount) AS period_end_balance,
                               SUM(cs.Amount * COALESCE(fx.FX_Rate, 1)) AS period_end_balance_eur
                        FROM CategorizedSplits cs
                        LEFT JOIN LastInterestDate li ON li.Accounts_Id = cs.Accounts_Id
                        LEFT JOIN Last_FXRates fx ON fx.Accounts_Id = cs.Accounts_Id
						WHERE cs.Date < li.last_interest_date										  
                        GROUP BY cs.Accounts_Id
                    ),
                    -- 1. Παραγωγή όλων των ημερών μεταξύ Prior και Last Interest Date
                    PeriodDates AS (
                        SELECT 
                            pid.Accounts_Id,
                            pid.prior_interest_date + generate_series(0, (lid.last_interest_date - pid.prior_interest_date)-1)::int AS calendar_day
                        FROM PriorInterestDate pid
                        JOIN LastInterestDate lid ON pid.Accounts_Id = lid.Accounts_Id
                    ),
                    -- 2. Υπολογισμός τρέχοντος υπολοίπου για κάθε ημέρα της περιόδου
                    DailyBalances AS (
                        SELECT 
                            pd.Accounts_Id,
                            pd.calendar_day,
                            (SELECT SUM(cs.Amount) 
                             FROM CategorizedSplits cs 
                             WHERE cs.Accounts_Id = pd.Accounts_Id 
                               AND cs.Date <= pd.calendar_day) AS daily_balance
                        FROM PeriodDates pd
                    ),
                    -- 3. Υπολογισμός Μέσου Όρου
                    PeriodAverageBalance AS (
                        SELECT 
                            dbal.Accounts_Id,
                            AVG(dbal.daily_balance) AS avg_period_balance,
                            -- Μετατροπή σε EUR αν χρειάζεται (χρησιμοποιώντας το fx rate που ορίσατε πριν)
                            AVG(dbal.daily_balance) * COALESCE(fx.FX_Rate, 1) AS avg_period_balance_eur
						FROM DailyBalances dbal
                        LEFT JOIN Last_FXRates fx ON fx.Accounts_Id = dbal.Accounts_Id
                        GROUP BY dbal.Accounts_Id, COALESCE(fx.FX_Rate, 1)
                    )
					SELECT
                        a.Accounts_Id,
                        a.Accounts_Name,
                        a.Accounts_Type,
                        c.Currencies_ShortName          AS currency,
                        a.Accounts_Balance              AS current_balance,
                        ast.first_tx_date,
                        ast.last_tx_date,
                        ast.last_interest_date,
                        ast.principal,
                        ast.principal_eur,
                        ast.total_interest,
                        ast.total_interest_eur,
                        -- Last interest period fields
                        pid.prior_interest_date,
                        lpi.last_interest_sum,
                        lpi.last_interest_sum_eur,
                        psb.period_start_balance,
                        psb.period_start_balance_eur,
                        pse.period_end_balance,
                        pse.period_end_balance_eur,
                        pab.avg_period_balance,
                        pab.avg_period_balance_eur						
					FROM Accounts a
                    JOIN Currencies c       ON c.Currencies_Id  = a.Currencies_Id
                    LEFT JOIN AccountStats ast ON ast.Accounts_Id = a.Accounts_Id
                    LEFT JOIN PriorInterestDate pid ON pid.Accounts_Id = a.Accounts_Id
                    LEFT JOIN LastPeriodInterest lpi ON lpi.Accounts_Id = a.Accounts_Id
                    LEFT JOIN PeriodStartBalance psb ON psb.Accounts_Id = a.Accounts_Id
                    LEFT JOIN PeriodEndBalance pse ON pse.Accounts_Id = a.Accounts_Id
                    LEFT JOIN PeriodAverageBalance pab ON a.Accounts_Id = pab.Accounts_Id					
                    WHERE a.Accounts_Type = 'Savings'
                    ORDER BY a.Accounts_Name;
                """, conn)

            if df_savings.empty:
                st.info("No Savings accounts found.")
            else:
                df_savings.columns = [c.lower() for c in df_savings.columns]

                # Parse dates
                for _dc in ['first_tx_date', 'last_tx_date', 'last_interest_date',
                            'last_interest_date', 'prior_interest_date']:
                    df_savings[_dc] = pd.to_datetime(df_savings[_dc])

                # ── Overall period calculations ───────────────────────────────
                df_savings['holding_days_total'] = (
                    (df_savings['last_tx_date'] - df_savings['first_tx_date'])
                    .dt.days.clip(lower=1)
                )
                _principal = df_savings['principal'].replace(0, float('nan'))

                # Annualised interest over full holding period
                df_savings['annual_interest_cash'] = (
                    df_savings['total_interest'] / df_savings['holding_days_total'] * 365
                )
                df_savings['annual_yoc_pct'] = (
                    df_savings['annual_interest_cash'] / _principal * 100
                ).fillna(0)

                _r_total = df_savings['total_interest'] / _principal
                df_savings['apy_pct'] = (
                    ((1 + _r_total) ** (365 / df_savings['holding_days_total']) - 1) * 100
                ).fillna(0)

                # ── Last interest period calculations ─────────────────────────
                # Period = prior_interest_date → last_interest_date
                # If no prior interest date (first ever interest payment),
                # fall back to first_tx_date as period start.
                _period_start = df_savings['prior_interest_date'].fillna(
                    df_savings['first_tx_date']
                )
                df_savings['period_start_date'] = _period_start
                df_savings['holding_days_last'] = (
                    (df_savings['last_interest_date'] - _period_start)
                    .dt.days.clip(lower=1)
                )

                # Average principal during the period, calculated in SQL as the average of daily balances.:
                # (balance at period start + balance at period end) / 2
                # Period-start balance comes from SQL (PeriodStartBalance CTE).
                # Period-end balance ≈ period_start_balance + last_interest_sum
                # (interest is added to the account at period end).
                _apb = df_savings['avg_period_balance'].replace(0, float('nan'))
            #    _psb = df_savings['period_start_balance'].replace(0, float('nan'))
            #    _period_end_balance = _psb + df_savings['last_interest_sum'].fillna(0)
            #    df_savings['avg_principal_last'] = ((_psb + _period_end_balance) / 2)
                df_savings['avg_principal_last'] = _apb

                # Annualise last period interest over actual period length
                df_savings['annual_interest_cash_last'] = (
                    df_savings['last_interest_sum'] / df_savings['holding_days_last'] * 365
                )

                # Annual YOC (last period) = annualised interest / avg principal * 100
            #    _avg_p = df_savings['avg_principal_last'].replace(0, float('nan'))
                _avg_p = df_savings['avg_period_balance'].replace(0, float('nan'))
                df_savings['annual_yoc_pct_last'] = (
                    df_savings['annual_interest_cash_last'] / _avg_p * 100
                ).fillna(0)

                # APY (last period) = (1 + period_interest/avg_principal)^(365/period_days)-1
                _r_last = df_savings['last_interest_sum'] / _avg_p
                df_savings['apy_pct_last'] = (
                    ((1 + _r_last) ** (365 / df_savings['holding_days_last']) - 1) * 100
                ).fillna(0)


                # ── Summary metrics ───────────────────────────────────────────
                m1, m2, m3, m4, m5 = st.columns(5)
                with m1:
                    st.metric("Savings Accounts", len(df_savings))
                with m2:
                    st.metric("Total Principal",
                              f"{df_savings['principal_eur'].sum():,.2f} €")
                with m3:
                    st.metric("Total Interest Received",
                              f"{df_savings['total_interest_eur'].sum():,.2f} €")
                with m4:
                    _avg_yoc = df_savings['annual_yoc_pct'].replace(0, float('nan')).mean()
                    st.metric("Avg Annual YOC",
                              f"{_avg_yoc:.2f}%" if not pd.isna(_avg_yoc) else "N/A")
                with m5:
                    _avg_apy = df_savings['apy_pct'].replace(0, float('nan')).mean()
                    st.metric("Avg APY",
                              f"{_avg_apy:.2f}%" if not pd.isna(_avg_apy) else "N/A")

                st.write("---")

                # ── Bar chart: Annual YOC per account ─────────────────────────
                df_chart = df_savings[df_savings['annual_yoc_pct'] != 0].sort_values('annual_yoc_pct', ascending=True).copy()
                if not df_chart.empty:
                    fig_yoc = px.bar(
                        df_chart,
                        x='annual_yoc_pct',
                        y='accounts_name',
                        orientation='h',
                        color='annual_yoc_pct',
                        color_continuous_scale='RdYlGn',
                        labels={'annual_yoc_pct': 'Annual YOC (%)', 'accounts_name': 'Account'},
                        title='Annual Yield over Cost (%) per Savings Account',
                        template='plotly_dark',
                        text='annual_yoc_pct',
                    )
                    fig_yoc.update_traces(texttemplate="%{x:.2f}%")
                    fig_yoc.update_traces(textposition='outside')
                    fig_yoc.update_layout(
                        coloraxis_showscale=False,
                        margin=dict(l=0, r=60, t=50, b=0),
                        yaxis_title=None
                    )
                    pf_plotly_chart(fig_yoc)

                # ── Detail table ──────────────────────────────────────────────
                st.subheader("Detail")
                df_display = df_savings[[
                    'accounts_name', 'accounts_type', 'currency',
                    'principal', 'total_interest', 'annual_interest_cash',
                    'current_balance', 'annual_yoc_pct', 'apy_pct',
                    'holding_days_total', 'first_tx_date', 'last_tx_date'
                ]].copy()

                df_display['first_tx_date'] = df_display['first_tx_date'].dt.strftime('%Y-%m-%d')
                df_display['last_tx_date']  = df_display['last_tx_date'].dt.strftime('%Y-%m-%d')

                st.dataframe(
                    df_display.style.format({
                        'principal':             "{:,.2f}",
                        'total_interest':        "{:,.2f}",
                        'annual_interest_cash':  "{:,.2f}",
                        'current_balance':       "{:,.2f}",
                        'annual_yoc_pct':        "{:.4f}%",
                        'apy_pct':               "{:.4f}%",
                        'holding_days_total':    "{:,.0f}",
                    }),
                    hide_index=True,
                    width="stretch",
                    column_config={
                        'accounts_name':        'Account',
                        'accounts_type':        'Type',
                        'currency':             'Currency',
                        'principal':            st.column_config.NumberColumn('Principal', format="%,.2f"),
                        'total_interest':       st.column_config.NumberColumn('Total Interest', format="%,.2f"),
                        'annual_interest_cash': st.column_config.NumberColumn('Ann. Interest', format="%,.2f",
                                                    help="Total interest annualised over holding period"),
                        'current_balance':      st.column_config.NumberColumn('Current Balance', format="%,.2f"),
                        'annual_yoc_pct':       st.column_config.NumberColumn('Annual YOC %', format="%.4f%%",
                                                    help="Annualised interest / Principal × 100 — same method as P&L securities"),
                        'apy_pct':              st.column_config.NumberColumn('APY %', format="%.4f%%",
                                                    help="(1 + Interest/Principal)^(365/days) − 1"),
                        'holding_days_total':         st.column_config.NumberColumn('Days Held', format="%,d"),
                        'first_tx_date':        'First Transaction',
                        'last_tx_date':         'Last Transaction',
                    }
                )
                copy_df_button(df_display, key="dl_rpt_savings_detail")

            #    csv = df_display.to_csv(index=False)
            #    st.download_button(
            #        label="📥 Download as CSV",
            #        data=csv,
            #        file_name="savings_yield_over_cost.csv",
            #        mime="text/csv"
            #    )

                # ── Detail table: last interest period ───────────────────────────
                st.subheader("Detail for Last Interest Period")
                st.caption(
                    "Period = prior interest payment date → last interest payment date. "
                    "Annual YOC and APY are calculated using the **average principal** "
                    "during this period and the **actual number of days** in the period."
                )
                df_display2 = df_savings[[
                    'accounts_name', 'accounts_type', 'currency',
                    'avg_principal_last', 'last_interest_sum',
                    'annual_interest_cash_last',
                    'annual_yoc_pct_last', 'apy_pct_last',
                    'holding_days_last', 'period_start_date', 'last_interest_date'
                ]].copy()

                df_display2['period_start_date']   = df_display2['period_start_date'].dt.strftime('%Y-%m-%d')
                df_display2['last_interest_date'] = df_display2['last_interest_date'].dt.strftime('%Y-%m-%d')

                st.dataframe(
                    df_display2.style.format({
                        'avg_principal_last':        "{:,.2f}",
                        'last_interest_sum':         "{:,.2f}",
                        'annual_interest_cash_last': "{:,.2f}",
                        'annual_yoc_pct_last':       "{:.4f}%",
                        'apy_pct_last':              "{:.4f}%",
                        'holding_days_last':         "{:,.0f}",
                    }),
                    hide_index=True,
                    width="stretch",
                    column_config={
                        'accounts_name':            'Account',
                        'accounts_type':            'Type',
                        'currency':                 'Currency',
                        'avg_principal_last':       st.column_config.NumberColumn('Avg Principal',     format="%,.2f",
                                                        help="Average of balance at period start and end"),
                        'last_interest_sum':        st.column_config.NumberColumn('Period Interest',   format="%,.2f"),
                        'annual_interest_cash_last':st.column_config.NumberColumn('Ann. Interest',     format="%,.2f",
                                                        help="Period interest annualised over period days"),
                        'annual_yoc_pct_last':      st.column_config.NumberColumn('Annual YOC %',      format="%.4f%%",
                                                        help="Ann. Interest / Avg Principal × 100"),
                        'apy_pct_last':             st.column_config.NumberColumn('APY %',             format="%.4f%%",
                                                        help="(1 + Period Interest / Avg Principal)^(365/days) − 1"),
                        'holding_days_last':        st.column_config.NumberColumn('Period Days',       format="%,d"),
                        'period_start_date':        'Period Start',
                        'last_interest_date':       'Period End (Last Interest)',
                    }
                )
                copy_df_button(df_display2, key="dl_rpt_savings_detail2")

        with tab_dividends:
            render_dividend_tracker()

        with tab_bonds:
            render_bond_schedule()

        with tab_bm:
            render_benchmark_comparison(account_ids=_risk_acct_ids, preset_label=sel_preset)

        with tab_risk:
            render_risk_metrics(account_ids=_risk_acct_ids)

        with tab_corr:
            render_correlation_matrix(account_ids=_risk_acct_ids)

        with tab_monte:
            render_monte_carlo(account_ids=_risk_acct_ids)

        with tab_returns:
            render_twr_mwr(account_ids=_risk_acct_ids)

        with tab_perf:
            st.subheader("🏆 Best & Worst Performing Securities")

            # ── Controls ──────────────────────────────────────────────────────
            _pc1, _pc2, _pc3 = st.columns([4, 2, 1])
            with _pc1:
                _perf_period = st.radio(
                    "Period:",
                    ["Daily", "WTD", "MTD", "QTD", "YTD", "All-Time"],
                    horizontal=True, key="perf_period_radio",
                )
            with _pc2:
                _perf_view = st.radio(
                    "View by:",
                    ["€ Change", "% Change"],
                    horizontal=True, key="perf_view_radio",
                )
            with _pc3:
                _perf_top_n = st.number_input(
                    "Top N", min_value=3, max_value=50, value=15, step=1,
                    key="perf_top_n",
                )

            # period → (eur_col, pct_col, invested_col)
            _perf_period_map = {
                "Daily":    ("pnl_dtd_eur",           "pnl_dtd_pct",              None),
                "WTD":      ("pnl_wtd_eur",           "pnl_wtd_pct",              None),
                "MTD":      ("pnl_mtd_eur",           "pnl_mtd_pct",              None),
                "QTD":      ("pnl_qtd_eur",           "pnl_qtd_pct",              None),
                "YTD":      ("pnl_ytd_eur",           "pnl_ytd_percent",          None),
                "All-Time": ("pnl_net_all_time_eur",  "pnl_net_all_time_percent", "gross_invested_all_time_eur"),
            }
            _pnl_col, _pct_col, _inv_col = _perf_period_map[_perf_period]
            _sort_by_pct = (_perf_view == "% Change")

            # ── Aggregate by security ─────────────────────────────────────────
            if df_pnl is not None and not df_pnl.empty:
                _agg_cols = {
                    "current_value_eur":          "sum",
                    "gross_invested_all_time_eur": "sum",
                    "pnl_net_all_time_eur":        "sum",
                    "unrealized_pnl_eur":          "sum",
                    "realized_pnl_eur":            "sum",
                    "pnl_dtd_eur":                 "sum",
                    "pnl_ytd_eur":                 "sum",
                    "pnl_qtd_eur":                 "sum",
                    "pnl_mtd_eur":                 "sum",
                    "pnl_wtd_eur":                 "sum",
                }
                _pnl_by_sec = (
                    df_pnl
                    .groupby("securities_name", as_index=False)
                    .agg({k: v for k, v in _agg_cols.items() if k in df_pnl.columns})
                )

                # ── Recompute % columns from aggregated sums ──────────────────
                _prev_val = (_pnl_by_sec["current_value_eur"]
                             - _pnl_by_sec.get("pnl_dtd_eur", 0)).replace(0, float("nan"))

                if "pnl_dtd_eur" in _pnl_by_sec.columns:
                    _prev = (_pnl_by_sec["current_value_eur"]
                             - _pnl_by_sec["pnl_dtd_eur"]).replace(0, float("nan"))
                    _pnl_by_sec["pnl_dtd_pct"] = _pnl_by_sec["pnl_dtd_eur"] / _prev * 100

                if "gross_invested_all_time_eur" in _pnl_by_sec.columns:
                    _inv = _pnl_by_sec["gross_invested_all_time_eur"].replace(0, float("nan"))
                    _pnl_by_sec["pnl_net_all_time_percent"] = _pnl_by_sec["pnl_net_all_time_eur"] / _inv * 100
                    _pnl_by_sec["pnl_ytd_percent"]          = _pnl_by_sec["pnl_ytd_eur"]          / _inv * 100
                    _pnl_by_sec["pnl_wtd_pct"]              = _pnl_by_sec["pnl_wtd_eur"]           / _inv * 100
                    _pnl_by_sec["pnl_mtd_pct"]              = _pnl_by_sec["pnl_mtd_eur"]           / _inv * 100
                    _pnl_by_sec["pnl_qtd_pct"]              = _pnl_by_sec["pnl_qtd_eur"]           / _inv * 100

                if _pnl_col not in _pnl_by_sec.columns:
                    st.warning(f"Column '{_pnl_col}' not available.")
                else:
                    _df_valid = _pnl_by_sec.dropna(subset=[_pnl_col]).copy()

                    # Primary sort column depends on View toggle
                    _primary = (_pct_col if _sort_by_pct and _pct_col
                                         and _pct_col in _pnl_by_sec.columns
                                else _pnl_col)

                    # Build display columns — always show both € and %, lead with primary
                    _display_cols = ["securities_name"]
                    _col_cfg: dict = {
                        "securities_name": st.column_config.TextColumn("Security"),
                    }
                    if _sort_by_pct and _pct_col and _pct_col in _pnl_by_sec.columns:
                        _display_cols += [_pct_col, _pnl_col]
                        _col_cfg[_pct_col] = st.column_config.NumberColumn("Change %",  format="%+.2f%%")
                        _col_cfg[_pnl_col] = st.column_config.NumberColumn("P&L (€)",   format="%+,.2f €")
                    else:
                        _display_cols += [_pnl_col]
                        _col_cfg[_pnl_col] = st.column_config.NumberColumn("P&L (€)",   format="%+,.2f €")
                        if _pct_col and _pct_col in _pnl_by_sec.columns:
                            _display_cols.append(_pct_col)
                            _col_cfg[_pct_col] = st.column_config.NumberColumn("Change %", format="%+.2f%%")
                    if _inv_col and _inv_col in _pnl_by_sec.columns:
                        _display_cols.append(_inv_col)
                        _col_cfg[_inv_col] = st.column_config.NumberColumn("Invested (€)", format="%,.2f €")

                    _df_sort = _df_valid.dropna(subset=[_primary])
                    _top    = _df_sort.nlargest(int(_perf_top_n),  _primary)
                    _bottom = _df_sort.nsmallest(int(_perf_top_n), _primary)

                    # ── Summary metrics ───────────────────────────────────────
                    _sm1, _sm2, _sm3, _sm4 = st.columns(4)
                    _sm1.metric("Securities", len(_df_valid))
                    _sm2.metric(f"Total P&L ({_perf_period})", f"€ {_df_valid[_pnl_col].sum():+,.2f}")
                    _sm3.metric("Winners", int((_df_valid[_pnl_col] > 0).sum()))
                    _sm4.metric("Losers",  int((_df_valid[_pnl_col] < 0).sum()))

                    st.divider()

                    # ── Tables side-by-side ───────────────────────────────────
                    _tcol, _lcol = st.columns(2)
                    with _tcol:
                        st.success(f"📈 Top {int(_perf_top_n)} Gainers")
                        st.dataframe(
                            _top[_display_cols], column_config=_col_cfg,
                            hide_index=True, width='stretch',
                        )
                        copy_df_button(_top, key="dl_rpt_top_perf")
                    with _lcol:
                        st.error(f"📉 Top {int(_perf_top_n)} Losers")
                        st.dataframe(
                            _bottom[_display_cols], column_config=_col_cfg,
                            hide_index=True, width='stretch',
                        )
                        copy_df_button(_bottom, key="dl_rpt_bot_perf")

                    st.divider()

                    # ── Bar chart ─────────────────────────────────────────────
                    _chart_col = _primary
                    _chart_df  = pd.concat([_top, _bottom]).drop_duplicates("securities_name")
                    _chart_df  = _chart_df.sort_values(_chart_col, ascending=True)
                    _chart_df["_color"] = _chart_df[_chart_col].apply(
                        lambda v: "gain" if v >= 0 else "loss"
                    )
                    _x_label = (f"Change % — {_perf_period}" if _sort_by_pct
                                else f"P&L (€) — {_perf_period}")
                    _fig_bar = px.bar(
                        _chart_df,
                        x=_chart_col, y="securities_name", orientation="h",
                        color="_color",
                        color_discrete_map={"gain": "#2ecc71", "loss": "#e74c3c"},
                        labels={_chart_col: _x_label, "securities_name": "", "_color": ""},
                        title=f"Top & Least Performers — {_perf_period} ({_perf_view})",
                    )
                    _fig_bar.update_layout(showlegend=False,
                                           yaxis={"categoryorder": "total ascending"})
                    if _sort_by_pct:
                        _fig_bar.update_xaxes(ticksuffix="%")
                    pf_plotly_chart(_fig_bar)

                    # ── Full ranked table ─────────────────────────────────────
                    with st.expander("📋 All Securities Ranked", expanded=False):
                        _all_ranked = _df_valid.sort_values(_primary, ascending=False).copy()
                        _all_ranked.insert(0, "Rank", range(1, len(_all_ranked) + 1))
                        _full_cfg = {"Rank": st.column_config.NumberColumn("Rank", format="%d"),
                                     **_col_cfg}
                        st.dataframe(
                            _all_ranked[["Rank"] + _display_cols],
                            column_config=_full_cfg,
                            hide_index=True, width='stretch',
                        )
                        copy_df_button(_all_ranked, key="dl_rpt_all_perf")

    elif hist_sub_menu == "Securities & Portfolio Analysis":
        # 1. Tabs
        tab_change, tab_volat, tab_inv_signals, tab_port_signals = st.tabs(["📈 Price Change %", "🌊 Volatility", "🎯 Investment Signals", "📢 Portfolio Action Signals"])

        # Sidebar: Refresh button only — account filter removed (all securities shown,
        # filtering is handled per-tab via the Portfolio Action Signals options).
        if st.sidebar.button("🔁 Recalculate Indicators", key="spa_recalculate_indicators_btn"):
            get_portfolio_signals.clear()
            with st.spinner("Running :green[update_holdings()]"):
                update_holdings()
            st.cache_data.clear()
            st.rerun()

        if st.sidebar.button("🔄 Refresh All Market Data", key="refresh_all_btn"):
            st.cache_data.clear()
            with st.spinner("Refreshing all market data... This may take a while."):
                download_historical_fx("6m")
                download_historical_prices_from_yahoo("6m")
                download_historical_prices_from_tradingview("6m")
                download_bond_prices_from_solidus()
                download_securities_info_from_yahoo()
                download_securities_info_from_tradingview()
                update_holdings()
            st.rerun()

        df_data = get_portfolio_signals(None)

    #    st.sidebar.write(f"DEBUG: Found {len(df_data)} securities")
    #    if "Berkshire Hathaway Inc" in df_data['securities_name'].values:
    #        st.sidebar.success("✅ Berkshire found in data")
    #    else:
    #        st.sidebar.error("❌ Berkshire NOT in data")

        with tab_change:
            st.subheader("📈 Securities Top Price Change %")

            # 1. Mapping των επιλογών του UI με τις στήλες της SQL
            period_map = {
                "Daily (%)": "daily_chg_pct",
                "Weekly (%)": "weekly_chg_pct",
                "Monthly (%)": "monthly_chg_pct",
                "Quarterly (%)": "quarterly_chg_pct",
                "Semi-Annual (%)": "semiannual_chg_pct",
                "Annual (%)": "annual_chg_pct",
                "Tri-Annual (%)": "triannual_chg_pct",
                "YTD (%)": "ytd_chg_pct"
            }

            mover_col = st.radio(
                "Select Period:", 
                list(period_map.keys()), 
                horizontal=True,
                key="mover_sort_radio"
            )

            # 2. Προετοιμασία του DataFrame για την επιλεγμένη περίοδο
            selected_sql_col = period_map[mover_col]
            
            # --- ΝΕΟ: Φιλτράρισμα βάσει price_today_date ---
            # Μετατροπή σε datetime αν δεν είναι ήδη
            df_filtered = df_data.copy()
            df_filtered['price_today_date'] = pd.to_datetime(df_filtered['price_today_date']).dt.date
            today = pd.Timestamp.now().date()

            if mover_col == "Daily (%)":
                # Μόνο αν η τιμή είναι σημερινή
                df_filtered = df_filtered[df_filtered['price_today_date'] == today]
            elif mover_col == "Weekly (%)":
                # Μόνο αν η τιμή είναι εντός των τελευταίων 7 ημερών
                week_ago = today - pd.Timedelta(days=7)
                df_filtered = df_filtered[df_filtered['price_today_date'] >= week_ago]
            else:
                # Για όλες τις άλλες περιόδους (Monthly, YTD κλπ), 
                # ίσως θέλετε ένα "λογικό" όριο, π.χ. εντός τελευταίου μήνα
                month_ago = today - pd.Timedelta(days=30)
                df_filtered = df_filtered[df_filtered['price_today_date'] >= month_ago]
            # ----------------------------------------------

            # Προετοιμασία του DataFrame για την εμφάνιση
            df_display = df_filtered[['securities_name', selected_sql_col]].copy()
            df_display.columns = ['Security', mover_col]
            df_display = df_display.dropna(subset=[mover_col])


            # Φιλτράρουμε και μετονομάζουμε δυναμικά
        #    df_display = df_data[['securities_name', selected_sql_col]].copy()
        #    df_display.columns = ['Security', mover_col]
            
            # Αφαίρεση εγγραφών με NaN (αν υπάρχουν) για σωστό sorting
        #    df_display = df_display.dropna(subset=[mover_col])

            # 3. Εμφάνιση Gainers / Losers
            gainer_col, loser_col = st.columns(2)
            
            with gainer_col:
                st.success(f"📈 Top Gainers ({mover_col})")
                top_gainers = df_display.sort_values(by=mover_col, ascending=False).head(10)
                st.dataframe(
                    top_gainers.style.format({mover_col: "{:,.2f}%"}),
                    hide_index=True,
                    width='stretch',
                    column_config={
                        "Security": st.column_config.TextColumn("Security"),
                        mover_col: st.column_config.NumberColumn(mover_col, format="%.2f%%"),
                    }
                )
                copy_df_button(top_gainers, key="dl_rpt_sec_gainers")

            with loser_col:
                st.error(f"📉 Top Losers ({mover_col})")
                top_losers = df_display.sort_values(by=mover_col, ascending=True).head(10)
                st.dataframe(
                    top_losers.style.format({mover_col: "{:,.2f}%"}),
                    hide_index=True,
                    width='stretch',
                    column_config={
                        "Security": st.column_config.TextColumn("Security"),
                        mover_col: st.column_config.NumberColumn(mover_col, format="%.2f%%"),
                    }
                )
                copy_df_button(top_losers, key="dl_rpt_sec_losers")

        with tab_volat:
            st.subheader("🌊 Securities Top Volatility")

            # 1. Mapping των επιλογών Volatility με τις στήλες της SQL
            vol_map = {
                "Monthly Vol (ann)": "vol_1m_ann",
                "Quarterly Vol (ann)": "vol_3m_ann",
                "Annual Vol (ann)": "vol_1y_ann",
                "YTD Vol (ann)": "vol_ytd_ann"
            }

            vol_period = st.radio(
                "Select Volatility Period:", 
                list(vol_map.keys()), 
                horizontal=True,
                key="vol_sort_radio"
            )

            # 2. Προετοιμασία του DataFrame
            selected_vol_col = vol_map[vol_period]
            df_vol_display = df_data[['securities_name', selected_vol_col]].copy()
            df_vol_display.columns = ['Security', vol_period]
            
            # Αφαίρεση μηδενικών ή NaN τιμών (συχνά σε τίτλους χωρίς κίνηση)
            df_vol_display = df_vol_display.dropna(subset=[vol_period])
            df_vol_display = df_vol_display[df_vol_display[vol_period] > 0]

            # 3. Εμφάνιση High / Low Volatility
            high_vol_col, low_vol_col = st.columns(2)
            
            with high_vol_col:
                st.warning(f"⚡ High Volatility ({vol_period})")
                top_high_vol = df_vol_display.sort_values(by=vol_period, ascending=False).head(10)
                st.dataframe(
                    top_high_vol.style.format({vol_period: "{:,.2f}%"}),
                    hide_index=True,
                    width='stretch',
                    column_config={
                        "Security": st.column_config.TextColumn("Security"),
                        vol_period: st.column_config.NumberColumn("Volatility %", format="%.2f%%"),
                    }
                )
                copy_df_button(top_high_vol, key="dl_rpt_high_vol")

            with low_vol_col:
                st.info(f"🛡️ Low Volatility ({vol_period})")
                top_low_vol = df_vol_display.sort_values(by=vol_period, ascending=True).head(10)
                st.dataframe(
                    top_low_vol.style.format({vol_period: "{:,.2f}%"}),
                    hide_index=True,
                    width='stretch',
                    column_config={
                        "Security": st.column_config.TextColumn("Security"),
                        vol_period: st.column_config.NumberColumn("Volatility %", format="%.2f%%"),
                    }
                )
                copy_df_button(top_low_vol, key="dl_rpt_low_vol")


        with tab_inv_signals:
            st.subheader("🎯 Risk-Reward Analysis")
            df_plot = df_data.copy()

            # Fill NaN quality scores and compute bubble size
            df_plot['quality_score'] = df_plot['quality_score'].fillna(0)
            df_plot['marker_size']   = df_plot['quality_score'].apply(lambda x: max(x, 0) + 5)

            # ── User-controlled outlier filter ────────────────────────────────
            _fc1, _fc2 = st.columns([3, 1])
            with _fc1:
                vol_cap_pct = st.slider(
                    "Volatility cap (percentile)",
                    min_value=50, max_value=100, value=95, step=1,
                    key="spa_vol_cap_pct",
                    help=(
                        "Securities above this volatility percentile are hidden from the "
                        "chart to preserve readability. Set to 100 to show all. "
                        "They always appear in the table below regardless of this setting."
                    ),
                )
            # Compute the absolute threshold from the chosen percentile
            if vol_cap_pct >= 100:
                vol_cap  = float('inf')
                df_chart = df_plot.copy()
            else:
                vol_cap  = max(df_plot['vol_1y_ann'].quantile(vol_cap_pct / 100.0), 10.0)
                df_chart = df_plot[df_plot['vol_1y_ann'] <= vol_cap].copy()

            n_hidden = len(df_plot) - len(df_chart)
            with _fc2:
                if vol_cap_pct < 100:
                    st.metric("Cap at", f"{vol_cap:.0f}%", help="Absolute volatility threshold derived from the chosen percentile.")

            if n_hidden > 0:
                hidden_names = (
                    df_plot[df_plot['vol_1y_ann'] > vol_cap]['securities_name']
                    .tolist()
                )
                st.caption(
                    f"ℹ️ {n_hidden} securit{'y' if n_hidden == 1 else 'ies'} "
                    f"with volatility > {vol_cap:.0f}% hidden from chart "
                    f"({', '.join(hidden_names)}). They still appear in the table below."
                )

            # 1. Scatter Chart
            fig = px.scatter(
                df_chart,
                x="vol_1y_ann",
                y="annual_chg_pct",
                size="marker_size",
                color="sharpe_ratio",
                hover_name="securities_name",
                hover_data={"marker_size": False, "quality_score": True, "sharpe_ratio": ":.2f"},
                labels={
                    "vol_1y_ann":      "Annual Volatility (%)",
                    "annual_chg_pct":  "Annual Return (%)",
                    "sharpe_ratio":    "Sharpe Ratio",
                    "quality_score":   "Quality Score",
                },
                title="Risk vs. Reward Matrix",
                color_continuous_scale=px.colors.diverging.RdYlGn,
            )

            # Quadrant reference lines
            fig.add_hline(y=0, line_dash="dash", line_color="white")
            fig.add_vline(x=df_chart["vol_1y_ann"].median(), line_dash="dash", line_color="gray")

            pf_plotly_chart(fig)

            # 2. Top Efficiency Picks Table
            st.markdown("### 🏆 Top Efficiency Picks (High Sharpe Ratio)")
            top_picks = df_data.sort_values("sharpe_ratio", ascending=False).head(20)
            
            st.dataframe(
                top_picks[['securities_name', 'annual_chg_pct', 'vol_1y_ann', 'sharpe_ratio', 'quality_score']],
                column_config={
                    "securities_name": "Security",
                    "annual_chg_pct": st.column_config.NumberColumn("Return 1Y", format="%.2f%%"),
                    "vol_1y_ann": st.column_config.NumberColumn("Vol 1Y", format="%.2f%%"),
                    "sharpe_ratio": st.column_config.ProgressColumn("Sharpe Ratio", min_value=0, max_value=3, format="%.2f"),
                    "quality_score": "Quality Score"
                },
                hide_index=True,
                width='stretch'
            )
            copy_df_button(top_picks, key="dl_rpt_top_picks")

        with tab_port_signals:
            st.subheader("📢 Portfolio Action Signals")

            # 1. Προσθήκη του επιλογέα στο UI
            view_option = st.radio(
                "Filter View:",
                ["Show All", "Hide Neutral", "Show Only Open Positions"],
                horizontal=True,
                key="signal_view_filter"
            )

            # 2. Εφαρμογή της λογικής φιλτραρίσματος
            if view_option == "Show All":
                df_rec = df_data.copy()

            elif view_option == "Hide Neutral":
                # Εξαιρούμε μόνο όσα έχουν σήμα '⚪ NEUTRAL'
                # (Φροντίστε το string να ταιριάζει ακριβώς με αυτό της SQL)
                df_rec = df_data[df_data['recommendation_signal'] != '⚪ NEUTRAL'].copy()

            elif view_option == "Show Only Open Positions":
                # Δείχνουμε μόνο όσα έχουν Quantity > 0
                df_rec = df_data[df_data['current_value_eur'] > 0].copy()

         
            # Display με χρωματική κωδικοποίηση
            def color_rec(val):
                if not isinstance(val, str): return ''
                v = val.upper()
                if 'CONVICTION SELL' in v or 'UNDERPERFORM' in v:  return 'color: darkred; font-weight: bold'
                if 'SELL' in v or 'CAUTION' in v:                  return 'color: red; font-weight: bold'
                if 'HIGH CONVICTION BUY' in v:                     return 'color: darkgreen; font-weight: bold'
                if 'STRONG' in v or 'CONVICTION BUY' in v:         return 'color: green; font-weight: bold'
                if 'BUY' in v or 'UPGRADE' in v:                   return 'color: mediumseagreen; font-weight: bold'
                if 'CONTRARIAN' in v:                               return 'color: darkorange; font-weight: bold'
                return ''

            def color_pnl(val):
                if val is None or (hasattr(val, '__float__') and val != val):
                    return ''
                try:
                    return 'color: green; font-weight: bold' if float(val) >= 0 else 'color: red; font-weight: bold'
                except (TypeError, ValueError):
                    return ''

            # P&L % = unrealized P&L / cost basis
            if "total_cost_eur" in df_rec.columns:
                df_rec["pnl_pct"] = (
                    df_rec["unrealized_pnl_eur"] /
                    df_rec["total_cost_eur"].replace(0, float("nan"))
                ) * 100
            else:
                df_rec["pnl_pct"] = float("nan")

            _display_cols = [
                'securities_name',
                'final_signal', 'recommendation_signal', 'wall_street_view',
                'current_value_eur', 'unrealized_pnl_eur', 'pnl_pct',
                'sharpe_ratio', 'quality_score',
                'price_today', 'high_3y', 'pct_from_high_3y', 'low_3y', 'pct_from_low_3y',
                'upside_pct', 'target_price',
            ]
            _style_df = df_rec[[c for c in _display_cols if c in df_rec.columns]].copy()
            st.dataframe(
                _style_df.style
                    .map(color_rec,  subset=[c for c in ['recommendation_signal', 'final_signal'] if c in _style_df.columns])
                    .map(color_pnl,  subset=[c for c in ['unrealized_pnl_eur', 'pnl_pct']        if c in _style_df.columns]),
                column_config={
                    "securities_name":    st.column_config.TextColumn("Security",     width="medium", pinned=True),
                    "final_signal":       st.column_config.TextColumn("Final Signal", help="Conviction signals appear when our math matches Analyst views"),
                    "recommendation_signal": "Math Signal",
                    "wall_street_view":   st.column_config.TextColumn("Analyst View"),
                    "current_value_eur":  st.column_config.NumberColumn("Value",       format="%,.2f €", width="small"),
                    "unrealized_pnl_eur": st.column_config.NumberColumn("Unreal. P&L", format="%+,.2f €", help="Unrealized P&L in EUR (market value minus FIFO cost basis)"),
                    "pnl_pct":            st.column_config.NumberColumn("P&L %",       format="%+.2f%%",  help="Unrealized P&L as % of cost basis"),
                    "sharpe_ratio":       st.column_config.NumberColumn("Sharpe",      format="%.2f"),
                    "quality_score":      st.column_config.NumberColumn("Quality",     format="%.2f"),
                    "price_today":        st.column_config.NumberColumn("Current Price", format="%.4f"),
                    "high_3y":            st.column_config.NumberColumn("3Y High",     format="%.4f", help="Highest price in last 3 years (post-split prices only)"),
                    "pct_from_high_3y":   st.column_config.NumberColumn("% from High", format="%+.2f%%", help="Current price vs 3-year high"),
                    "low_3y":             st.column_config.NumberColumn("3Y Low",      format="%.4f", help="Lowest price in last 3 years (post-split prices only)"),
                    "pct_from_low_3y":    st.column_config.NumberColumn("% from Low",  format="%+.2f%%", help="Current price vs 3-year low"),
                    "upside_pct":         st.column_config.NumberColumn("Upside %",    format="%+.2f%%", help="Analyst Target Price vs Current Price"),
                    "target_price":       st.column_config.NumberColumn("Target Price", format="%.2f"),
                },
                hide_index=True,
                width='stretch'
            )
            copy_df_button(df_rec, key="dl_rpt_signals")

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

            st.subheader("Market Data Synchronization")

            # Create a grid for specific updates (3 columns × 2 rows)
            grid = st.columns(3)

            # Define the buttons in a list to keep code DRY (Don't Repeat Yourself)
            tasks = [
                ("FX Rates from Yahoo", download_historical_fx),
                ("Security Prices from Yahoo", download_historical_prices_from_yahoo),
                ("Security Prices from TradingView", download_historical_prices_from_tradingview),
                ("Bond Prices from Solidus", download_bond_prices_from_solidus),
                ("Info from Yahoo", download_securities_info_from_yahoo),
                ("Info from TradingView", download_securities_info_from_tradingview),
            ]

            for i, (label, func) in enumerate(tasks):
                with grid[i % 3]:
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
                        download_historical_fx("3y")
                        download_historical_prices_from_yahoo("3y")
                        download_historical_prices_from_tradingview("3y")
                        download_bond_prices_from_solidus()
                        download_securities_info_from_yahoo()
                        download_securities_info_from_tradingview()
                        st.balloons()
                        st.success("All data up to date!")
                        st.rerun()

    elif hist_sub_menu == "Income & Expense":
        render_income_expense_reports()

    elif hist_sub_menu == "Cash Flow Forecast":
        render_cash_flow_forecast()

    elif hist_sub_menu == "Budget & Spending":
        render_budget_and_spending()

    elif hist_sub_menu == "Custom Reports":
        render_custom_reports()

    elif hist_sub_menu == "Investment Tax Report":
        render_tax_report()

    elif hist_sub_menu == "Financial Planning":
        render_financial_planning()


def render_income_expense_reports():
    """Render Income and Expense Reports page"""
    st.subheader("📊 Income & Expense Analysis")
    
    # Get category hierarchy
    df_categories = get_category_hierarchy()
    
    if df_categories.empty:
        st.warning("No categories found in the database. Please add categories in Settings.")
        return
    
    income_cats = df_categories[df_categories['categories_type'] == 'Income']
    expense_cats = df_categories[df_categories['categories_type'] == 'Expense']
    tax_cats = df_categories[df_categories['categories_type'] == 'Tax']
    div_cats = df_categories[df_categories['categories_type'] == 'Dividend']
    int_cats = df_categories[df_categories['categories_type'] == 'Interest']
    
    # Create category options dict for selectbox
    cat_options = {}
    for _, row in df_categories.iterrows():
        cat_options[row['categories_id']] = f"{'  ' * row['level']}{row['full_path']}"
    
    # Date range selection
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        report_type = st.selectbox(
            "Report Type",
            ["Total Summary", "Income Analysis", "Expense Analysis", "Tax Analysis", "Dividend Analysis", "Interest Analysis"],
            key="ie_report_type"
        )
    
    with col2:
        period_type = st.selectbox(
            "Period Type",
            ["Monthly", "Quarterly", "Yearly"],
            key="ie_period_type"
        )
    
    with col3:
        # Default YTD
        today = datetime.now().date()
        start_of_year = datetime(today.year, 1, 1).date()
        
        start_date = st.date_input(
            "Start Date",
            value=start_of_year,
            min_value="1900-01-01",
            max_value=today,
            key="ie_start_date"
        )
    
    with col4:
        end_date = st.date_input(
            "End Date",
            value=today,
            key="ie_end_date"
        )
    
    # Category filter (only for detailed views)
    filter_category = None
    if report_type != "Total Summary":
        with st.expander("🔍 Filter by Category", expanded=False):
            # Δημιουργούμε ένα dictionary: {id: "Name"}
            if report_type == "Income Analysis":
                current_cats = income_cats
            elif report_type == "Expense Analysis":
                 current_cats = expense_cats
            elif report_type == "Tax Analysis":
                current_cats = tax_cats 
            elif report_type == "Dividend Analysis":
                current_cats = div_cats
            elif report_type == "Interest Analysis":
                current_cats = int_cats

                
            # Κατασκευή του mapping: ID -> Name
            cat_map = {row['categories_id']: cat_options.get(row['categories_id'], "Unknown") 
                    for _, row in current_cats.iterrows()}
            
            # Λίστα επιλογών: Μόνο τα IDs (συμπεριλαμβανομένου του "All" ως string)
            options = ["All"] + list(cat_map.keys())

            selected_cat = st.selectbox(
                "Select Category",
                options=options,
                format_func=lambda x: cat_map.get(x, "All Categories"),
                key="ie_category_filter"
            )
            
            if selected_cat != "All":
                filter_category = selected_cat
    
    # Load data
    if start_date and end_date:
        if start_date > end_date:
            st.error("Start date must be before end date")
            return

        # Ορισμός όλων των πιθανών τύπων από το ENUM σου
        all_account_types = [
            'Cash', 'Checking', 'Savings', 'Credit Card', 'Brokerage', 
            'Pension', 'Other Investment', 'Margin', 'Loan', 'Real Estate', 
            'Vehicle', 'Asset', 'Liability', 'Other'
        ]

        # Προεπιλεγμένοι τύποι (αυτοί που είχες στον κώδικα)
        default_cash = ['Cash', 'Checking', 'Savings', 'Credit Card', 'Loan', 'Real Estate', 'Vehicle', 'Asset', 'Liability', 'Other']

        # Προεπιλεγμένοι τύποι (αυτοί που είχες στον κώδικα)
        default_inv = ['Brokerage', 'Other Investment', 'Margin']

        col_a, col_b, col_c = st.columns(3)

        with col_a:      
            with st.expander("🏦 Cash Account Types", expanded=False):
                options_for_cash = [t for t in all_account_types if t not in st.session_state.get("ie_inv_account_types_filter", [])]
                
                selected_cash_account_types = st.multiselect(
                    "Select Cash Types:",
                    options=options_for_cash,
                #    default=[t for t in default_cash if t in options_for_cash],
                    default=default_cash,
                    key="ie_cash_account_types_filter"
                )

        with col_b:
            with st.expander("📈 Investment Account Types", expanded=False):
                # Αφαιρούμε από τις επιλογές όσα είναι ήδη επιλεγμένα στα Cash
                options_for_inv = [t for t in all_account_types if t not in selected_cash_account_types]
                
                selected_inv_account_types = st.multiselect(
                    "Select Investment Types:",
                    options=options_for_inv,
                #    default=[t for t in default_inv if t in options_for_inv],
                    default=default_inv,
                    key="ie_inv_account_types_filter"
                )

        with col_c:
        # Function για το reset
            def reset_account_filters():
                st.session_state["ie_cash_account_types_filter"] = default_cash
                st.session_state["ie_inv_account_types_filter"] = default_inv

            # Τοποθέτηση του κουμπιού (π.χ. δίπλα από τους επιλογείς)
            st.button("🔄 Reset to Defaults", on_click=reset_account_filters)


        # Έλεγχος αν κάποια λίστα είναι άδεια για να αποφύγεις σφάλματα στην SQL
        if not selected_cash_account_types or not selected_inv_account_types:
            st.warning("Please select at least one account type.")
        else:
            with st.spinner("Loading data..."):
                df = get_income_expense_data(start_date, end_date, filter_category, selected_cash_account_types, selected_inv_account_types)
            
            if df.empty:
                st.warning("No transactions found for the selected period.")
                return

            # Handle datetime conversion safely
            try:
                if 'date' in df.columns:
                    df['date'] = pd.to_datetime(df['date'], errors='coerce')
                    if hasattr(df['date'].dt, 'tz') and df['date'].dt.tz is not None:
                        df['date'] = df['date'].dt.tz_localize(None)
                
                if 'month_date' in df.columns:
                    df['month_date'] = pd.to_datetime(df['month_date'], errors='coerce')
                    if hasattr(df['month_date'].dt, 'tz') and df['month_date'].dt.tz is not None:
                        df['month_date'] = df['month_date'].dt.tz_localize(None)
            except Exception as e:
                st.warning(f"Date conversion issue: {e}")
                if 'year' in df.columns and 'month' in df.columns:
                    df['date'] = pd.to_datetime(df['year'].astype(str) + '-' + df['month'].astype(str) + '-01')
                    df['month_date'] = df['date']
            
            # Filter by report type
            if report_type == "Income Analysis":
                df = df[df['categories_type'] == 'Income']
            elif report_type == "Expense Analysis":
                df = df[df['categories_type'] == 'Expense']
            elif report_type == "Tax Analysis":
                df = df[df['categories_type'] == 'Tax']
            elif report_type == "Dividend Analysis":
                df = df[df['categories_type'] == 'Dividend']
            elif report_type == "Interest Analysis":
                df = df[df['categories_type'] == 'Interest']
            
            if df.empty:
                st.warning(f"No {report_type} transactions found for the selected period.")
                return

            # Display summary metrics
            #st.markdown("### 📈 Summary")
            
            # total_bank_income = df[df['source_type'] == 'Bank'][df['categories_type'] == 'Income']['split_amount'].sum() if 'Bank' in df['source_type'].values and 'Income' in df['categories_type'].values else 0
            # Πιο σωστή και αποδοτική σύνταξη με χρήση του .loc
            condition = (df['source_type'] == 'Bank') & (df['categories_type'] == 'Income')
            total_bank_income = df.loc[condition, 'split_amount'].sum()

            condition = (df['source_type'] == 'Bank') & (df['categories_type'] == 'Interest')
            total_bank_interest = df.loc[condition, 'split_amount'].sum()
            total_income_non_investment = total_bank_income + total_bank_interest

            condition = (df['source_type'] == 'Investment') & (df['categories_type'] == 'Income')
            total_investment_income = df.loc[condition, 'split_amount'].sum()

            condition = (df['source_type'] == 'Investment') & (df['categories_type'] == 'Dividend')
            total_investment_dividend = df.loc[condition, 'split_amount'].sum()

            condition = (df['source_type'] == 'Investment') & (df['categories_type'] == 'Interest')
            total_investment_interest = df.loc[condition, 'split_amount'].sum()
            total_income_investment = total_investment_income + total_investment_dividend + total_investment_interest
 
            overall_income = total_income_non_investment + total_income_investment

            condition = (df['source_type'] == 'Bank') & (df['categories_type'] == 'Expense')
            total_bank_expense = df.loc[condition, 'split_amount'].sum()

            condition = (df['categories_type'] == 'Tax')
            total_tax = df.loc[condition, 'split_amount'].sum()
            overall_expense_non_investment = total_bank_expense + total_tax

            condition = (df['source_type'] == 'Investment') & (df['categories_type'] == 'Expense')
            total_investment_expense = df.loc[condition, 'split_amount'].sum()
            overall_expense = overall_expense_non_investment + total_investment_expense

            net_savings = overall_income + overall_expense
            
            overall_col1, overall_col2, overall_col3, overall_col4 = st.columns(4)
            with overall_col1:
            #    st.metric("Overall Income", f"€ {overall_income:,.2f}") 
                custom_metric(
                    label="Overall Income", 
                    value=f"€ {overall_income:,.2f}", 
                    pnl_value=overall_income    
                )                
            with overall_col2:
            #    st.metric("Overall Expenses", f"€ {overall_expense:,.2f}")
                custom_metric(
                    label="Overall Expenses", 
                    value=f"€ {overall_expense:,.2f}", 
                    pnl_value=overall_expense    
                )                     
            with overall_col3:
                if report_type == "Total Summary":
                #    st.metric("Net Savings", f"€ {net_savings:,.2f}", 
                #            delta="Positive" if net_savings > 0 else "Negative",
                #            delta_color="normal" if net_savings > 0 else "inverse")
                    custom_metric(
                        label="Net Savings", 
                        value=f"€ {net_savings:,.2f}", 
                        pnl_value=net_savings    
                    )                        
            with overall_col4:
                if report_type == "Total Summary":
                    savings_rate = (net_savings / overall_income * 100) if overall_income > 0 else 0
                #    st.metric("Savings Rate", f"{savings_rate:.1f}%")
                    custom_metric(
                        label="Savings Rate", 
                        value=f"{round(savings_rate, 2):.2f}%", 
                        pnl_value=savings_rate    
                    )       

            m_col1, m_col2, m_col3, m_col4 = st.columns(4)

            with m_col1:
                # Χρήση f-string και πρόσβαση στο dictionary με []
                st.markdown(f"""
                    <div style="line-height: 1.5;text-align: center;">
                        <p style="color: grey; font-size: 16px; margin: 0; font-family: sans-serif;">Earned & Reimbursed / Investments</p>
                        <p style="margin: 0; font-weight: bold;">
                            <span style="color: {get_color(total_income_non_investment)};">€ {total_income_non_investment:+,.2f}</span> / 
                            <span style="color: {get_color(total_income_investment)};">€ {total_income_investment:+,.2f}</span>
                        </p>
                    </div>
                """, unsafe_allow_html=True)

            with m_col2:
                st.markdown(f"""
                    <div style="line-height: 1.5;text-align: center;">
                        <p style="color: grey; font-size: 16px; margin: 0; font-family: sans-serif;">Expenses / Taxes / Investments</p>
                        <p style="margin: 0; font-weight: bold;">
                            <span style="color: {get_color(total_bank_expense)};">€ {total_bank_expense:+,.2f}</span> / 
                            <span style="color: {get_color(total_tax)};">€ {total_tax:+,.2f}</span> / 
                            <span style="color: {get_color(total_investment_expense)};">€ {total_investment_expense:+,.2f}</span>
                        </p>
                    </div>
                """, unsafe_allow_html=True)

            with m_col3:
                bank_total = df[df['source_type'] == 'Bank']['split_amount'].sum()
                inv_total = df[df['source_type'] == 'Investment']['split_amount'].sum()

                st.markdown(f"""
                    <div style="line-height: 1.5;text-align: center;">
                        <p style="color: grey; font-size: 16px; margin: 0; font-family: sans-serif;">Savings by Cash / Investments</p>
                        <p style="margin: 0; font-weight: bold;">
                            <span style="color: {get_color(bank_total)};">€ {bank_total:+,.2f}</span> / 
                            <span style="color: {get_color(inv_total)};">€ {inv_total:+,.2f}</span> 
                        </p>
                    </div>
                """, unsafe_allow_html=True)

            st.divider()

            # Define aggregate_by_period inside the function
            def aggregate_by_period(df, period='monthly'):
                """Aggregate data by period (monthly, quarterly, yearly)"""
                if df.empty:
                    return pd.DataFrame(), []
                
                df_copy = df.copy()
                
                # Ensure we have a proper date column for grouping
                if 'month_date' in df_copy.columns and not df_copy['month_date'].isna().all():
                    if period == 'monthly':
                        df_copy['period'] = df_copy['month_date'].dt.strftime('%Y-%m')
                    elif period == 'quarterly':
                        df_copy['period'] = df_copy['month_date'].dt.year.astype(str) + '-Q' + df_copy['month_date'].dt.quarter.astype(str)
                    else:
                        df_copy['period'] = df_copy['month_date'].dt.year.astype(str)
                elif 'date' in df_copy.columns and not df_copy['date'].isna().all():
                    if period == 'monthly':
                        df_copy['period'] = df_copy['date'].dt.strftime('%Y-%m')
                    elif period == 'quarterly':
                        df_copy['period'] = df_copy['date'].dt.year.astype(str) + '-Q' + ((df_copy['date'].dt.month - 1) // 3 + 1).astype(str)
                    else:
                        df_copy['period'] = df_copy['date'].dt.year.astype(str)
                else:
                    if period == 'monthly':
                        df_copy['period'] = df_copy['year'].astype(str) + '-' + df_copy['month'].astype(str).str.zfill(2)
                    elif period == 'quarterly':
                        df_copy['period'] = df_copy['year'].astype(str) + '-Q' + ((df_copy['month'] - 1) // 3 + 1).astype(str)
                    else:
                        df_copy['period'] = df_copy['year'].astype(str)
                
                # Aggregate by category and period
                agg_df = df_copy.groupby(['category_full_path', 'categories_type', 'period'])['split_amount'].sum().reset_index()
                
                # Pivot for easier display
                pivot_df = agg_df.pivot_table(
                    index=['category_full_path', 'categories_type'],
                    columns='period',
                    values='split_amount',
                    fill_value=0
                ).reset_index()
                
                # Add total column
                period_cols = [col for col in pivot_df.columns if col not in ['category_full_path', 'categories_type']]
                if period_cols:
                    pivot_df['Total'] = pivot_df[period_cols].sum(axis=1)
                else:
                    pivot_df['Total'] = 0
                
                # Sort period columns chronologically
                try:
                    period_cols_sorted = sorted(period_cols)
                    pivot_df = pivot_df[['category_full_path', 'categories_type'] + period_cols_sorted + ['Total']]
                    return pivot_df, period_cols_sorted
                except:
                    return pivot_df, period_cols
        
            # Aggregate by period
            period_map = {'Monthly': 'monthly', 'Quarterly': 'quarterly', 'Yearly': 'yearly'}
            aggregated_df, period_columns = aggregate_by_period(df, period_map[period_type])
            
            if aggregated_df.empty:
                st.warning("No data available for the selected criteria.")
                return
            
            # Tabs for different views
            if period_columns:
                tab_chart, tab_table, tab_trend, tab_top_cats, tab_top_payees = st.tabs([
                    "📊 Chart", "📋 Detailed Table", "📈 Trend Analysis",
                    "🏷️ Top Categories", "👥 Top Payees",
                ])
                
                with tab_chart:
                    # 1. Slider για το Top N
                    top_n = st.slider("Show Top N Categories", 5, 20, 10, key="ie_top_n")
                    
                    # --- STACKED BAR CHART: Income column + Expenses column ---
                    st.subheader("Income vs Expenses Comparison")

                    # Classify every categories_type into Income or Expenses group
                    INCOME_TYPES_ORDER = ['Income', 'Dividend', 'Interest']
                    EXPENSE_TYPES_ORDER = ['Expense', 'Tax']

                    # Colors: greens for income types, reds for expense types
                    TYPE_COLORS = {
                        'Income':   '#27AE60',
                        'Dividend': '#1ABC9C',
                        'Interest': '#2980B9',
                        'Expense':  '#E74C3C',
                        'Tax':      '#8E44AD',
                    }
                    # Fallback palettes for any unlisted types
                    _income_fallbacks = ['#16A085', '#1E8449', '#117A65', '#0E6655']
                    _expense_fallbacks = ['#922B21', '#6E2FBF', '#1A5276', '#784212']

                    # Prepare data: sum each categories_type across period columns, take abs
                    bar_data = aggregated_df.groupby('categories_type')[period_columns].sum().reset_index()
                    bar_melted = bar_data.melt(id_vars=['categories_type'], var_name='Period', value_name='Amount')
                    bar_melted['Amount'] = bar_melted['Amount'].abs()
                    bar_melted['Period'] = bar_melted['Period'].astype(str)
                    bar_melted = bar_melted.sort_values(by='Period')

                    # Determine which types actually exist in the data
                    existing_types = bar_melted['categories_type'].unique().tolist()

                    # Build ordered lists for income/expense, then catch any unknowns
                    income_types = [t for t in INCOME_TYPES_ORDER if t in existing_types]
                    expense_types = [t for t in EXPENSE_TYPES_ORDER if t in existing_types]
                    # Unknown types: assign to income or expense by exclusion
                    known = set(INCOME_TYPES_ORDER + EXPENSE_TYPES_ORDER)
                    for t in existing_types:
                        if t not in known:
                            income_types.append(t)   # treat unknowns as income by default

                    fig_bar = go.Figure()

                    # Income traces — stacked in the "Income" offset group
                    for i, cat_type in enumerate(income_types):
                        df_sub = bar_melted[bar_melted['categories_type'] == cat_type]
                        if df_sub.empty:
                            continue
                        color = TYPE_COLORS.get(cat_type, _income_fallbacks[i % len(_income_fallbacks)])
                        fig_bar.add_trace(go.Bar(
                            x=df_sub['Period'],
                            y=df_sub['Amount'],
                            name=cat_type,
                            marker_color=color,
                            offsetgroup='Income',
                            legendgroup='Income',
                            legendgrouptitle_text='Income' if i == 0 else None,
                            hovertemplate='%{x}<br>' + cat_type + ': %{y:,.2f}<extra></extra>',
                        ))

                    # Expense traces — stacked in the "Expenses" offset group
                    for i, cat_type in enumerate(expense_types):
                        df_sub = bar_melted[bar_melted['categories_type'] == cat_type]
                        if df_sub.empty:
                            continue
                        color = TYPE_COLORS.get(cat_type, _expense_fallbacks[i % len(_expense_fallbacks)])
                        fig_bar.add_trace(go.Bar(
                            x=df_sub['Period'],
                            y=df_sub['Amount'],
                            name=cat_type,
                            marker_color=color,
                            offsetgroup='Expenses',
                            legendgroup='Expenses',
                            legendgrouptitle_text='Expenses' if i == 0 else None,
                            hovertemplate='%{x}<br>' + cat_type + ': %{y:,.2f}<extra></extra>',
                        ))

                    fig_bar.update_layout(
                        barmode='stack',
                        xaxis_type='category',
                        xaxis_tickangle=-45,
                        yaxis_title='Amount (€)',
                        hovermode='x unified',
                        height=450,
                        legend=dict(groupclick='toggleitem'),
                    )
                    pf_plotly_chart(fig_bar)

                    st.markdown("---")

                    # --- SIDE-BY-SIDE PIE CHARTS ---
                    st.subheader("Distribution Analysis")
                    col_inc, col_exp = st.columns(2)

                    pie_groups = [
                        ("Income", income_types, col_inc, px.colors.qualitative.Pastel),
                        ("Expenses", expense_types, col_exp, px.colors.qualitative.Safe),
                    ]

                    for group_label, group_types, column, color_seq in pie_groups:
                        with column:
                            # Combine all category types in this group
                            type_df = aggregated_df[
                                aggregated_df['categories_type'].isin(group_types)
                            ].copy()

                            if not type_df.empty:
                                type_df['Total_Abs'] = type_df['Total'].abs()
                                top_cats = type_df.nlargest(top_n, 'Total_Abs')['category_full_path'].tolist()
                                type_df['Category'] = type_df['category_full_path'].apply(
                                    lambda x: x if x in top_cats else "Other"
                                )
                                pie_agg = type_df.groupby('Category')['Total_Abs'].sum().reset_index()

                                fig_pie = px.pie(
                                    pie_agg,
                                    values='Total_Abs',
                                    names='Category',
                                    title=f"{group_label} Breakdown",
                                    hole=0.4,
                                    height=550,
                                    color_discrete_sequence=color_seq,
                                )
                                fig_pie.update_traces(
                                    textposition='inside',
                                    textinfo='percent+label',
                                    insidetextorientation='radial',
                                    textfont=dict(size=14),
                                )
                                fig_pie.update_layout(
                                    showlegend=False,
                                    title_font=dict(size=18),
                                    margin=dict(t=60, b=20, l=20, r=20),
                                )
                                pf_plotly_chart(fig_pie)
                            else:
                                st.info(f"No {group_label} data available.")
                
                with tab_table:
                    st.subheader(f"{report_type} - {period_type} Breakdown")
                    
                    display_df = aggregated_df.copy()

                    # Remove duplicate category column if it exists
                    if 'category_full_path' in display_df.columns:
                        # Make sure we don't have duplicate column names
                        display_df = display_df.loc[:, ~display_df.columns.duplicated()]
                        
                    if 'categories_type' in display_df.columns:
                        display_df = display_df.sort_values(by='categories_type', ascending=False)

                    format_dict = {col: "{:,.2f} €" for col in period_columns + ['Total']}
                    
                    # Remove categories_type if it's all the same (not needed for display)
                    #if 'categories_type' in display_df.columns and len(display_df['categories_type'].unique()) == 1:
                    #    display_df = display_df.drop(columns=['categories_type'])
                    
                    st.dataframe(
                        display_df.style.format(format_dict),
                        width='stretch',
                        hide_index=True,
                        column_config={
                            "category_full_path": "Category",
                            "categories_type": "Type"}
                    )
                    copy_df_button(display_df, key="dl_rpt_ie_breakdown")
                
                with tab_trend:
                    st.subheader("Monthly Trend Analysis")
                    
                    trend_data = df.copy()
                    if 'date' in trend_data.columns and not trend_data['date'].isna().all():
                        trend_data['month_year'] = trend_data['date'].dt.strftime('%b %Y')
                        trend_data['month_num'] = trend_data['date'].dt.year * 100 + trend_data['date'].dt.month
                        
                        monthly_trend = trend_data.groupby(['month_num', 'month_year', 'category_full_path'])['split_amount'].sum().reset_index()
                        
                        if not monthly_trend.empty:
                            top_trend_cats = monthly_trend.groupby('category_full_path')['split_amount'].sum().nlargest(8).index.tolist()
                            trend_filtered = monthly_trend[monthly_trend['category_full_path'].isin(top_trend_cats)]
                            
                            if not trend_filtered.empty:
                                fig_line = px.line(
                                    trend_filtered,
                                    x='month_year',
                                    y='split_amount',
                                    color='category_full_path',
                                    title=f"{report_type} Monthly Trend - Top 8 Categories",
                                    labels={'split_amount': 'Amount (€)', 'month_year': 'Month'},
                                    markers=True
                                )
                                fig_line.update_layout(xaxis_tickangle=-45, hovermode='x unified', height=500)
                                pf_plotly_chart(fig_line)
                
                # ── TOP CATEGORIES ─────────────────────────────────────────────
                with tab_top_cats:
                    st.subheader("🏷️ Top Categories")

                    _tc_n = st.slider("Show Top N Categories", 5, 30, 15, key="ie_tc_n")

                    # Build per-category summary from raw df
                    cat_summary = (
                        df.groupby(['category_full_path', 'categories_type'])
                        .agg(total=('split_amount', 'sum'), count=('split_amount', 'count'))
                        .reset_index()
                    )
                    cat_summary['abs_total'] = cat_summary['total'].abs()
                    cat_summary['avg_per_tx'] = (cat_summary['total'] / cat_summary['count']).round(2)

                    INCOME_TYPES = ['Income', 'Dividend', 'Interest']
                    EXPENSE_TYPES = ['Expense', 'Tax']

                    income_top = (
                        cat_summary[cat_summary['categories_type'].isin(INCOME_TYPES)]
                        .nlargest(_tc_n, 'abs_total')
                        .sort_values('total', ascending=True)
                    )
                    expense_top = (
                        cat_summary[cat_summary['categories_type'].isin(EXPENSE_TYPES)]
                        .nlargest(_tc_n, 'abs_total')
                        .sort_values('abs_total', ascending=True)
                    )

                    col_inc_c, col_exp_c = st.columns(2)

                    with col_inc_c:
                        st.markdown("#### 💚 Top Income Categories")
                        if not income_top.empty:
                            fig_tc_inc = go.Figure(go.Bar(
                                x=income_top['total'],
                                y=income_top['category_full_path'],
                                orientation='h',
                                marker_color='#27AE60',
                                text=income_top['total'].apply(lambda v: f"€ {v:,.2f}"),
                                textposition='auto',
                                hovertemplate='%{y}<br>€ %{x:,.2f}<extra></extra>',
                            ))
                            fig_tc_inc.update_layout(
                                height=max(300, len(income_top) * 32),
                                margin=dict(l=0, r=20, t=0, b=0),
                                xaxis_title='Amount (€)',
                            )
                            pf_plotly_chart(fig_tc_inc)
                        else:
                            st.info("No income categories found.")

                    with col_exp_c:
                        st.markdown("#### 🔴 Top Expense Categories")
                        if not expense_top.empty:
                            fig_tc_exp = go.Figure(go.Bar(
                                x=expense_top['abs_total'],
                                y=expense_top['category_full_path'],
                                orientation='h',
                                marker_color='#E74C3C',
                                text=expense_top['total'].apply(lambda v: f"€ {v:,.2f}"),
                                textposition='auto',
                                hovertemplate='%{y}<br>€ %{x:,.2f}<extra></extra>',
                            ))
                            fig_tc_exp.update_layout(
                                height=max(300, len(expense_top) * 32),
                                margin=dict(l=0, r=20, t=0, b=0),
                                xaxis_title='Amount (€)',
                            )
                            pf_plotly_chart(fig_tc_exp)
                        else:
                            st.info("No expense categories found.")

                    st.divider()
                    st.markdown("#### 📋 Category Detail")

                    # Period breakdown table (reuse aggregated_df) enriched with tx count
                    cat_counts = (
                        df.groupby('category_full_path')['split_amount']
                        .count()
                        .reset_index()
                        .rename(columns={'split_amount': 'tx_count'})
                    )
                    detail_cats = (
                        aggregated_df
                        .merge(cat_counts, on='category_full_path', how='left')
                        .assign(abs_total=lambda d: d['Total'].abs())
                        .sort_values('abs_total', ascending=False)
                        .drop(columns=['abs_total'])
                        .reset_index(drop=True)
                    )
                    fmt_cats = {col: "{:,.2f} €" for col in period_columns + ['Total']}
                    st.dataframe(
                        detail_cats.style.format(fmt_cats),
                        width='stretch', hide_index=True,
                        column_config={
                            "category_full_path": st.column_config.TextColumn("Category", pinned=True),
                            "categories_type":    "Type",
                            "tx_count":           st.column_config.NumberColumn("# Txs", format="%d"),
                            "Total":              st.column_config.NumberColumn("Total (€)", format="%,.2f"),
                        },
                    )
                    copy_df_button(detail_cats, key="dl_rpt_ie_top_cats")

                    st.divider()
                    st.markdown("#### 🔍 Categories Drill Down")

                    all_cats = sorted(df['category_full_path'].unique())
                    selected_drill_cat = st.selectbox(
                        "Select Category:",
                        ["All Categories"] + all_cats,
                        key="ie_drill_category",
                    )

                    drill_df = (
                        df[df['category_full_path'] == selected_drill_cat].copy()
                        if selected_drill_cat != "All Categories"
                        else df.copy()
                    )

                    if not drill_df.empty:
                        if selected_drill_cat != "All Categories":
                            d_total = drill_df['split_amount'].sum()
                            d_count = len(drill_df)
                            _dm1, _dm2 = st.columns(2)
                            with _dm1:
                                custom_metric("Total", f"€ {d_total:,.2f}", pnl_value=d_total)
                            with _dm2:
                                st.metric("Transactions", f"{d_count:,}")

                        display_cols = [
                            'date', 'description', 'payees_name', 'category_full_path',
                            'split_amount', 'split_amount_original', 'original_currency',
                            'accounts_name', 'source_type',
                        ]
                        display_cols = [c for c in display_cols if c in drill_df.columns]
                        display_drill_df = drill_df[display_cols].sort_values('date', ascending=False).copy()
                        if 'date' in display_drill_df.columns:
                            display_drill_df['date'] = display_drill_df['date'].dt.strftime('%Y-%m-%d')

                        st.dataframe(
                            display_drill_df,
                            width='stretch',
                            hide_index=True,
                            column_config={
                                'date':                  'Date',
                                'description':           'Description',
                                'payees_name':           'Payee',
                                'category_full_path':    'Category',
                                'split_amount':          st.column_config.NumberColumn('Amount (€)',       format="%.2f"),
                                'split_amount_original': st.column_config.NumberColumn('Original Amount', format="%.2f"),
                                'original_currency':     'Currency',
                                'accounts_name':         'Account',
                                'source_type':           'Source',
                            },
                        )
                        copy_df_button(display_drill_df, key="dl_rpt_ie_drilldown")

                # ── TOP PAYEES ──────────────────────────────────────────────────
                with tab_top_payees:
                    st.subheader("👥 Top Payees")

                    _tp_n = st.slider("Show Top N Payees", 5, 30, 15, key="ie_tp_n")

                    df_with_payees = df[df['payees_name'].notna()].copy()

                    if df_with_payees.empty:
                        st.info("No payee data available for the selected period and filters.")
                    else:
                        payee_summary = (
                            df_with_payees
                            .groupby('payees_name')
                            .agg(
                                total=('split_amount', 'sum'),
                                count=('split_amount', 'count'),
                                top_category=(
                                    'category_full_path',
                                    lambda x: x.value_counts().index[0] if len(x) > 0 else '—'
                                ),
                            )
                            .reset_index()
                        )
                        payee_summary['abs_total'] = payee_summary['total'].abs()
                        payee_summary['avg_per_tx'] = (payee_summary['total'] / payee_summary['count']).round(2)

                        income_payees = (
                            payee_summary[payee_summary['total'] > 0]
                            .nlargest(_tp_n, 'abs_total')
                            .sort_values('total', ascending=True)
                        )
                        expense_payees = (
                            payee_summary[payee_summary['total'] < 0]
                            .nlargest(_tp_n, 'abs_total')
                            .sort_values('abs_total', ascending=True)
                        )

                        col_inc_p, col_exp_p = st.columns(2)

                        with col_inc_p:
                            st.markdown("#### 💚 Top Income Payees")
                            if not income_payees.empty:
                                fig_tp_inc = go.Figure(go.Bar(
                                    x=income_payees['total'],
                                    y=income_payees['payees_name'],
                                    orientation='h',
                                    marker_color='#27AE60',
                                    text=income_payees['total'].apply(lambda v: f"€ {v:,.2f}"),
                                    textposition='auto',
                                    hovertemplate='%{y}<br>€ %{x:,.2f}<extra></extra>',
                                ))
                                fig_tp_inc.update_layout(
                                    height=max(300, len(income_payees) * 32),
                                    margin=dict(l=0, r=20, t=0, b=0),
                                    xaxis_title='Amount (€)',
                                )
                                pf_plotly_chart(fig_tp_inc)
                            else:
                                st.info("No income payees found.")

                        with col_exp_p:
                            st.markdown("#### 🔴 Top Expense Payees")
                            if not expense_payees.empty:
                                fig_tp_exp = go.Figure(go.Bar(
                                    x=expense_payees['abs_total'],
                                    y=expense_payees['payees_name'],
                                    orientation='h',
                                    marker_color='#E74C3C',
                                    text=expense_payees['total'].apply(lambda v: f"€ {v:,.2f}"),
                                    textposition='auto',
                                    hovertemplate='%{y}<br>€ %{x:,.2f}<extra></extra>',
                                ))
                                fig_tp_exp.update_layout(
                                    height=max(300, len(expense_payees) * 32),
                                    margin=dict(l=0, r=20, t=0, b=0),
                                    xaxis_title='Amount (€)',
                                )
                                pf_plotly_chart(fig_tp_exp)
                            else:
                                st.info("No expense payees found.")

                        st.divider()
                        st.markdown("#### 📋 Payee Summary")

                        payee_detail = (
                            payee_summary
                            .sort_values('abs_total', ascending=False)
                            .drop(columns=['abs_total'])
                            .reset_index(drop=True)
                        )
                        st.dataframe(
                            payee_detail.style.format({
                                'total':      "{:,.2f} €",
                                'avg_per_tx': "{:,.2f} €",
                            }),
                            width='stretch', hide_index=True,
                            column_config={
                                "payees_name":  st.column_config.TextColumn("Payee", pinned=True),
                                "total":        st.column_config.NumberColumn("Total (€)",       format="%,.2f"),
                                "count":        st.column_config.NumberColumn("# Txs",           format="%d"),
                                "avg_per_tx":   st.column_config.NumberColumn("Avg / Tx (€)",    format="%,.2f"),
                                "top_category": st.column_config.TextColumn("Top Category"),
                            },
                        )
                        copy_df_button(payee_detail, key="dl_rpt_ie_top_payees")

                        st.divider()
                        st.markdown("#### 🔍 Payee Drill Down")

                        all_payee_names = sorted(df_with_payees['payees_name'].unique())
                        selected_payee = st.selectbox(
                            "Select Payee:",
                            ["All Payees"] + all_payee_names,
                            key="ie_payee_drill",
                        )

                        payee_drill_df = (
                            df_with_payees[df_with_payees['payees_name'] == selected_payee].copy()
                            if selected_payee != "All Payees"
                            else df_with_payees.copy()
                        )

                        if not payee_drill_df.empty:
                            p_total = payee_drill_df['split_amount'].sum()
                            p_count = len(payee_drill_df)
                            _pm1, _pm2 = st.columns(2)
                            with _pm1:
                                custom_metric("Total", f"€ {p_total:,.2f}", pnl_value=p_total)
                            with _pm2:
                                st.metric("Transactions", f"{p_count:,}")

                            # Category breakdown for the selected payee
                            if selected_payee != "All Payees":
                                cat_bk = (
                                    payee_drill_df
                                    .groupby('category_full_path')['split_amount']
                                    .agg(['sum', 'count'])
                                    .reset_index()
                                    .rename(columns={'sum': 'total', 'count': 'cnt'})
                                    .sort_values('total')
                                )
                                if not cat_bk.empty:
                                    st.markdown("**Category Breakdown**")
                                    fig_bk = go.Figure(go.Bar(
                                        x=cat_bk['total'],
                                        y=cat_bk['category_full_path'],
                                        orientation='h',
                                        marker_color=[
                                            '#27AE60' if v >= 0 else '#E74C3C'
                                            for v in cat_bk['total']
                                        ],
                                        text=cat_bk['total'].apply(lambda v: f"€ {v:,.2f}"),
                                        textposition='auto',
                                        hovertemplate='%{y}<br>€ %{x:,.2f}<extra></extra>',
                                    ))
                                    fig_bk.update_layout(
                                        height=max(200, len(cat_bk) * 32),
                                        margin=dict(l=0, r=20, t=0, b=0),
                                        xaxis_title='Amount (€)',
                                    )
                                    pf_plotly_chart(fig_bk)

                            # Transaction list
                            drill_cols = [
                                'date', 'description', 'payees_name', 'category_full_path',
                                'split_amount', 'split_amount_original', 'original_currency',
                                'accounts_name',
                            ]
                            drill_cols = [c for c in drill_cols if c in payee_drill_df.columns]
                            drill_disp = payee_drill_df[drill_cols].sort_values('date', ascending=False).copy()
                            if 'date' in drill_disp.columns:
                                drill_disp['date'] = drill_disp['date'].dt.strftime('%Y-%m-%d')
                            st.dataframe(
                                drill_disp,
                                width='stretch', hide_index=True,
                                column_config={
                                    'date':                   'Date',
                                    'description':            'Description',
                                    'payees_name':            'Payee',
                                    'category_full_path':     'Category',
                                    'split_amount':           st.column_config.NumberColumn('Amount (€)',        format="%.2f"),
                                    'split_amount_original':  st.column_config.NumberColumn('Original Amount',  format="%.2f"),
                                    'original_currency':      'Currency',
                                    'accounts_name':          'Account',
                                },
                            )
                            copy_df_button(drill_disp, key="dl_rpt_ie_payee_detail")

            else:
                st.info("No period data available for the selected criteria.")


# ======================================================
# DIVIDEND TRACKER
# ======================================================

def _render_dividend_forecast():
    """Forecast tab: projected 12-month dividend income for currently-held securities."""
    import datetime as _dt
    from dateutil.relativedelta import relativedelta

    today = pd.Timestamp.now().date()

    df_fc = get_dividend_forecast_data()
    if df_fc.empty:
        st.info("No dividend-paying securities found in current holdings.")
        return

    # ── Frequency → payments per year ────────────────────────────────────────
    _FREQ_MAP = {
        "monthly":     12,
        "quarterly":    4,
        "semi-annual":  2,
        "bi-annual":    2,
        "annual":       1,
        "yearly":       1,
    }

    def _payments_per_year(freq_str) -> int:
        if pd.isna(freq_str) or not str(freq_str).strip():
            return 4  # assume quarterly when unknown
        return _FREQ_MAP.get(str(freq_str).strip().lower(), 4)

    def _next_payment_dates(anchor_date, freq_str, horizon_months: int = 12) -> list:
        """Generate expected payment dates for the next `horizon_months` months."""
        ppy = _payments_per_year(freq_str)
        interval_months = max(round(12 / ppy), 1)
        dates = []
        # Start from the anchor; if it's in the past advance to the nearest future date
        d = pd.Timestamp(anchor_date).date() if pd.notna(anchor_date) else today
        while d <= today:
            d += relativedelta(months=interval_months)
        cutoff = today + relativedelta(months=horizon_months)
        while d <= cutoff:
            dates.append(d)
            d += relativedelta(months=interval_months)
        return dates

    # ── Compute annual projected income per security ──────────────────────────
    rows = []
    for _, r in df_fc.iterrows():
        mv   = float(r.get('market_value_eur') or 0)
        cost = float(r.get('cost_basis_eur')   or 0)
        qty  = float(r.get('total_qty')         or 0)
        fx   = 1.0  # market_value_eur is already converted

        # Priority: Dividend_Rate (annual per share, sec ccy) → Fwd Yield → trailing 12m
        dr   = r.get('dividend_rate')
        dy   = r.get('dividend_yield')
        t12  = float(r.get('trailing_12m_income_eur') or 0)

        if pd.notna(dr) and float(dr) > 0 and mv > 0:
            # Dividend_Rate is in security currency per share; market_value_eur already in EUR
            # annual_income = qty × rate_sec_ccy × fx_rate ≈ (rate / price) × market_value
            price = mv / qty if qty else 0
            annual = (float(dr) / price * mv) if price else 0
            method = "Dividend Rate"
        elif pd.notna(dy) and float(dy) > 0 and mv > 0:
            annual = mv * float(dy) / 100
            method = "Fwd Yield"
        elif t12 > 0:
            annual = t12
            method = "Trailing 12m"
        else:
            continue  # no data to forecast

        if annual <= 0:
            continue

        freq       = r.get('dividend_frequency')
        ppy        = _payments_per_year(freq)
        per_pmt    = annual / ppy

        # Ex-date anchor → future ex-dates
        ex_anchor  = r.get('ex_dividend_date') or r.get('last_ex_date')
        ex_dates   = _next_payment_dates(ex_anchor, freq, horizon_months=13)

        # Pay-date anchor → future pay-dates (offset from ex-dates by the known lag)
        raw_ex  = r.get('ex_dividend_date')
        raw_pay = r.get('dividend_pay_date')
        if pd.notna(raw_ex) and pd.notna(raw_pay):
            _lag = (pd.Timestamp(raw_pay).date() - pd.Timestamp(raw_ex).date()).days
            _lag = max(_lag, 0)  # guard against reversed data
        else:
            _lag = 0  # no lag info — use ex-date as cash-flow date

        pay_anchor = r.get('dividend_pay_date') or ex_anchor
        pay_dates  = _next_payment_dates(pay_anchor, freq, horizon_months=13)

        next_expected_ex  = ex_dates[0]  if ex_dates  else None
        next_expected_pay = pay_dates[0] if pay_dates else next_expected_ex

        rows.append({
            'securities_name':       r['securities_name'],
            'securities_type':       r['securities_type'],
            'total_qty':             qty,
            'market_value_eur':      mv,
            'cost_basis_eur':        cost,
            'annual_forecast_eur':   round(annual, 2),
            'per_payment_eur':       round(per_pmt, 2),
            'payments_per_year':     ppy,
            'frequency':             freq or 'Quarterly (assumed)',
            'method':                method,
            'dividend_yield':        r.get('dividend_yield'),
            'next_expected_ex_date': next_expected_ex,
            'next_expected_pay_date': next_expected_pay,
            'last_known_ex_date':    r.get('ex_dividend_date'),
            'last_known_pay_date':   r.get('dividend_pay_date'),
            'pay_lag_days':          _lag if _lag else None,
            'ex_dates':              ex_dates,
            'next_pay_dates':        pay_dates,
        })

    if not rows:
        st.info("No forecast data available — no securities with dividend yield, rate or trailing income.")
        return

    df_rows = pd.DataFrame(rows)
    total_annual  = df_rows['annual_forecast_eur'].sum()
    total_monthly = total_annual / 12

    # ── Summary metrics ───────────────────────────────────────────────────────
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Projected Annual Income",  f"€ {total_annual:,.2f}")
    m2.metric("Projected Monthly Avg",    f"€ {total_monthly:,.2f}")
    m3.metric("Securities forecasted",    str(len(df_rows)))
    _fwd_yoc = (total_annual / df_rows['cost_basis_eur'].sum() * 100) if df_rows['cost_basis_eur'].sum() > 0 else 0
    m4.metric("Portfolio Yield on Cost",  f"{_fwd_yoc:.2f}%")

    st.divider()

    # ── Monthly forecast bar chart — bucketed by payment date ────────────────
    # Use pay dates (when cash arrives) rather than ex-dates for cash-flow accuracy.
    _monthly: dict = {}
    for _, r in df_rows.iterrows():
        for d in r['next_pay_dates']:
            key = d.replace(day=1)
            _monthly[key] = _monthly.get(key, 0) + r['per_payment_eur']

    if _monthly:
        df_monthly_fc = (
            pd.DataFrame(list(_monthly.items()), columns=['month', 'income_eur'])
            .sort_values('month')
            .head(12)
        )
        df_monthly_fc['month'] = pd.to_datetime(df_monthly_fc['month'])
        fig_fc = px.bar(
            df_monthly_fc, x='month', y='income_eur',
            title="<b>Projected Monthly Dividend Income (€) — Next 12 Months</b>",
            labels={'income_eur': 'Projected Income (€)', 'month': 'Month'},
            template='plotly_dark',
            color_discrete_sequence=['#3498DB'],
        )
        fig_fc.update_layout(margin=dict(l=0, r=0, t=50, b=0))
        pf_plotly_chart(fig_fc)

    # ── Per-security forecast table ───────────────────────────────────────────
    st.markdown("#### Projected Income by Security")

    _tbl_cols = ['securities_name', 'securities_type', 'annual_forecast_eur',
                 'per_payment_eur', 'frequency', 'dividend_yield',
                 'next_expected_ex_date', 'next_expected_pay_date',
                 'last_known_ex_date', 'last_known_pay_date', 'pay_lag_days',
                 'method', 'market_value_eur', 'cost_basis_eur']

    st.dataframe(
        df_rows[_tbl_cols].sort_values('annual_forecast_eur', ascending=False),
        hide_index=True,
        width='stretch',
        column_config={
            'securities_name':        st.column_config.TextColumn("Security"),
            'securities_type':        st.column_config.TextColumn("Type"),
            'annual_forecast_eur':    st.column_config.NumberColumn("Annual Forecast (€)",  format="%,.2f €"),
            'per_payment_eur':        st.column_config.NumberColumn("Per Payment (€)",      format="%,.2f €"),
            'frequency':              st.column_config.TextColumn("Frequency"),
            'dividend_yield':         st.column_config.NumberColumn("Fwd Yield %",          format="%.2f%%"),
            'next_expected_ex_date':  st.column_config.DateColumn(
                "Next Expected Ex-Div", format="DD/MM/YYYY",
                help="Projected next ex-dividend date — last known date advanced by the "
                     "payment frequency. Must hold shares before this date to qualify."),
            'next_expected_pay_date': st.column_config.DateColumn(
                "Next Expected Pay Date", format="DD/MM/YYYY",
                help="Projected date cash arrives in your account, estimated from the "
                     "payment date stored in Yahoo Finance advanced by the frequency. "
                     "Where no payment date is available, equals the ex-dividend date."),
            'last_known_ex_date':     st.column_config.DateColumn(
                "Last Known Ex-Div", format="DD/MM/YYYY",
                help="Most recent ex-dividend date from Yahoo Finance (may be in the past)."),
            'last_known_pay_date':    st.column_config.DateColumn(
                "Last Known Pay Date", format="DD/MM/YYYY",
                help="Most recent payment date from Yahoo Finance (may be in the past)."),
            'pay_lag_days':           st.column_config.NumberColumn(
                "Pay Lag (days)", format="%d days",
                help="Days between ex-dividend date and payment date for this security."),
            'method':                 st.column_config.TextColumn("Basis",
                                          help="Dividend Rate = from broker data (most accurate); "
                                               "Fwd Yield = market price × yield; "
                                               "Trailing 12m = last year's actual income"),
            'market_value_eur':       st.column_config.NumberColumn("Market Value (€)",     format="%,.2f €"),
            'cost_basis_eur':         st.column_config.NumberColumn("Cost Basis (€)",       format="%,.2f €"),
        }
    )
    copy_df_button(df_rows[_tbl_cols], key="dl_rpt_div_forecast")

    # ── Payment calendar (next 3 months detail) ───────────────────────────────
    with st.expander("📅 Upcoming Payments Detail (next 3 months)", expanded=False):
        _upcoming = []
        cutoff_3m = today + relativedelta(months=3)
        for _, r in df_rows.iterrows():
            for ex_d, pay_d in zip(r['ex_dates'], r['next_pay_dates']):
                if ex_d <= cutoff_3m:
                    _upcoming.append({
                        'ex_date':          ex_d,
                        'pay_date':         pay_d,
                        'security':         r['securities_name'],
                        'per_payment_eur':  r['per_payment_eur'],
                        'frequency':        r['frequency'],
                        'method':           r['method'],
                    })
        if _upcoming:
            df_up = pd.DataFrame(_upcoming).sort_values('ex_date')
            st.dataframe(
                df_up,
                hide_index=True,
                width='stretch',
                column_config={
                    'ex_date':         st.column_config.DateColumn("Ex-Date",        format="DD/MM/YYYY",
                                           help="Must hold shares before this date to receive the dividend."),
                    'pay_date':        st.column_config.DateColumn("Pay Date",        format="DD/MM/YYYY",
                                           help="Estimated date cash arrives in your account."),
                    'security':        st.column_config.TextColumn("Security"),
                    'per_payment_eur': st.column_config.NumberColumn("Amount (€)",   format="%,.2f €"),
                    'frequency':       st.column_config.TextColumn("Frequency"),
                    'method':          st.column_config.TextColumn("Basis"),
                }
            )
        else:
            st.info("No payments expected in the next 3 months.")


def render_dividend_tracker():
    st.subheader("💸 Dividend & Interest Income Tracker")

    # ── Actual vs Forecast selector ───────────────────────────────────────────
    import datetime as _dt
    today = pd.Timestamp.now().date()

    view_mode = st.radio(
        "View:",
        ["📋 Actual", "🔮 Forecast"],
        horizontal=True,
        key="div_view_mode",
    )

    if view_mode == "🔮 Forecast":
        _render_dividend_forecast()
        return

    # ── ACTUAL view ───────────────────────────────────────────────────────────
    ctrl_col1, ctrl_col2 = st.columns([2, 2])
    with ctrl_col1:
        period_opt = st.radio(
            "Period:",
            ["YTD", "Previous Year", "1 Year", "2 Years", "3 Years", "5 Years", "All Time", "Custom"],
            index=0,
            key="div_period",
            horizontal=True,
        )

    _period_days = {"1 Year": 365, "2 Years": 730, "3 Years": 1095, "5 Years": 1825}
    if period_opt == "Custom":
        with ctrl_col2:
            start_date = st.date_input("From", value=today - pd.Timedelta(days=365), min_value=pd.Timestamp("1900-01-01").date(), key="div_from")
            end_date   = st.date_input("To",   value=today,                          key="div_to")
    elif period_opt == "All Time":
        start_date = pd.Timestamp("1900-01-01").date()
        end_date   = today
    elif period_opt == "YTD":
        start_date = _dt.date(today.year, 1, 1)
        end_date   = today
    elif period_opt == "Previous Year":
        start_date = _dt.date(today.year - 1, 1, 1)
        end_date   = _dt.date(today.year - 1, 12, 31)
    else:
        start_date = today - pd.Timedelta(days=_period_days[period_opt])
        end_date   = today

    period_label = {
        "All Time": "All Time", "Custom": "Custom",
        "YTD": f"YTD {today.year}",
        "Previous Year": str(today.year - 1),
    }.get(period_opt, f"Last {period_opt}")

    df = get_dividend_tracker_data(str(start_date), str(end_date))

    if df.empty:
        st.info("No dividend or interest transactions found for the selected period.")
        return

    df['month'] = pd.to_datetime(df['month'])

    # Drop income events where the position was closed at that date (cost_basis = 0).
    # This excludes e.g. MiscExp charged a few days after a full sell, when no shares
    # are held and the expense cannot be attributed to any open position.
    df = df[df['cost_basis_eur'] > 0].copy()
    if df.empty:
        st.info("No income found for open positions in the selected period.")
        return

    # Cap the income span per security to (N×12 − 1) months.
    # For a 1-year window the oldest included event must be no more than 11 months
    # before the most recent one; for 2 years no more than 23 months, etc.
    # This prevents accidentally collecting N+1 payment cycles when a dividend
    # lands right at the edge of the calendar window.
    _period_months = {"1 Year": 12, "2 Years": 24, "3 Years": 36, "5 Years": 60,
                      "Previous Year": 12}
    if period_opt in _period_months:
        _max_span_days = (_period_months[period_opt] - 1) * 365.25 / 12
    elif period_opt in ("Custom", "YTD"):
        _custom_months = (end_date - start_date).days / (365.25 / 12)
        _max_span_days = max(_custom_months - 1, 0) * 365.25 / 12
    else:  # All Time — no cap
        _max_span_days = None

    if _max_span_days is not None:
        _last_per_sec = df.groupby('securities_name')['date'].transform('max')
        df = df[(_last_per_sec - df['date']).dt.days <= _max_span_days].copy()
        if df.empty:
            st.info("No income found for the selected period after span cap.")
            return

    # Monthly bar chart
    df_monthly = df.groupby('month')['income_eur'].sum().reset_index()
    fig_bar = px.bar(
        df_monthly, x='month', y='income_eur',
        title=f"<b>Monthly Dividend & Interest Income (€) — {period_label}</b>",
        labels={'income_eur': 'Income (€)', 'month': 'Month'},
        template='plotly_dark',
        color_discrete_sequence=['#2ECC71'],
    )
    fig_bar.update_layout(margin=dict(l=0, r=0, t=50, b=0))
    pf_plotly_chart(fig_bar)

    # Income by security for the selected period
    st.markdown(f"#### Income by Security — {period_label}")

    # ── YoC: total income / cost × (365 / holding days) ──────────────────────
    # Annualise over the actual holding period of the position, not the calendar
    # period or the gap between dividends.
    #
    # cost_basis_eur and position_start_date come from the LATERAL FIFO for each
    # income row.  We use the LAST payment's values (sorted ascending by date) so
    # the cost and position-start reflect the most-recent portfolio state.
    #
    # holding_days = last_income_date − position_start_date
    # (position_start_date = date of the oldest FIFO lot still held at the time of
    #  the last income payment for this security)
    df_sorted = df.sort_values(['securities_name', 'date'])

    def _wtd_cost(g):
        """Income-weighted average FIFO cost across all payments in the period.
        Each payment carries the cost at its own date, so this correctly weights
        larger positions (higher cost) that generated more income."""
        abs_inc = g['income_eur'].abs()
        total_w = abs_inc.sum()
        if total_w == 0:
            return g['cost_basis_eur'].iloc[-1]
        return (g['cost_basis_eur'] * abs_inc).sum() / total_w

    df_t12 = (
        df_sorted.groupby(['securities_name', 'securities_type'])
        .apply(lambda g: pd.Series({
            'period_income_eur':   g['income_eur'].sum(),
            'cost_basis_eur':      _wtd_cost(g),
            'position_start_date': g['position_start_date'].min(),
            'last_income_date':    g['date'].max(),
        }), include_groups=False)
        .reset_index()
        .sort_values('period_income_eur', ascending=False)
    )

    # Annualised YoC = (income / cost) × (365 / period_days).
    # Dividends are paid on a per-share periodic basis, so annualise over the
    # selected calendar window — NOT over inter-dividend gaps or buy-to-dividend spans.
    # Exception: "All Time" has no fixed window, so use the actual holding period
    # (position_start_date → last income date) as the denominator.
    _ann_days_map = {"1 Year": 365, "2 Years": 730, "3 Years": 1095, "5 Years": 1825,
                     "Previous Year": 365}
    if period_opt == "All Time":
        ann_days = (
            (df_t12['last_income_date'] - df_t12['position_start_date'])
            .dt.days
            .clip(lower=1)
        )
    elif period_opt in _ann_days_map:
        ann_days = _ann_days_map[period_opt]
    else:  # Custom or YTD — use actual calendar days
        ann_days = max((end_date - start_date).days, 1)

    df_t12['yoc_pct'] = (
        df_t12['period_income_eur']
        / df_t12['cost_basis_eur'].replace(0, float('nan'))
        * 100 * 365
        / ann_days
    ).fillna(0)

    # ── Enrich with Yahoo Finance security metadata ───────────────────────────
    # Join Dividend_Yield (fwd market yield), Ex_Dividend_Date, Dividend_Frequency
    # so the user can compare their actual YOC vs what a new buyer would earn today.
    from database.connection import get_db as _get_db
    with _get_db() as _conn_yh:
        df_sec_meta = pd.read_sql("""
            SELECT Securities_Name,
                   Dividend_Yield    AS fwd_yield_pct,
                   Ex_Dividend_Date  AS ex_div_date,
                   Dividend_Frequency AS div_frequency
            FROM   Securities
            WHERE  Dividend_Yield IS NOT NULL
               OR  Ex_Dividend_Date IS NOT NULL
               OR  Dividend_Frequency IS NOT NULL
        """, _conn_yh)

    if not df_sec_meta.empty:
        df_sec_meta.columns = df_sec_meta.columns.str.lower()
        df_t12 = df_t12.merge(
            df_sec_meta,
            left_on='securities_name', right_on='securities_name',
            how='left',
        )
    else:
        df_t12['fwd_yield_pct']  = None
        df_t12['ex_div_date']    = None
        df_t12['div_frequency']  = None

    m1, m2, m3 = st.columns(3)
    m1.metric(f"Total ({period_label})", f"€ {df_t12['period_income_eur'].sum():,.2f}")
    m2.metric("Securities paying", str(len(df_t12)))
    _avg_yoc = df_t12[df_t12['yoc_pct'] > 0]['yoc_pct'].mean()
    m3.metric("Avg Ann. YOC", f"{_avg_yoc:.2f}%" if not pd.isna(_avg_yoc) else "N/A")

    _disp_cols = ['securities_name', 'securities_type',
                  'period_income_eur', 'cost_basis_eur', 'yoc_pct',
                  'fwd_yield_pct', 'ex_div_date', 'div_frequency']
    _disp_cols = [c for c in _disp_cols if c in df_t12.columns]

    st.dataframe(
        df_t12[_disp_cols].style.format({
            'period_income_eur': '{:,.2f} €',
            'cost_basis_eur':    '{:,.2f} €',
            'yoc_pct':           '{:.2f}%',
            'fwd_yield_pct':     lambda v: f'{v:.2f}%' if pd.notna(v) else '—',
        }),
        hide_index=True, width='stretch',
        column_config={
            'securities_name':   'Security',
            'securities_type':   'Type',
            'period_income_eur': st.column_config.NumberColumn(
                f'Income ({period_label})', format='%,.2f €'),
            'cost_basis_eur':    st.column_config.NumberColumn(
                'Cost Basis (€)', format='%,.2f €',
                help='Average FIFO cost basis across all income payments in the period (EUR). '
                     'Expire/Reinvest on the same date as the income payment are excluded so '
                     'closed positions show their true cost.'),
            'yoc_pct':           st.column_config.NumberColumn(
                'Ann. YOC %', format='%.2f%%',
                help='Annualised yield on cost: (total period income / FIFO cost at last '
                     'payment) × (365 / days). YOC > Fwd Yield = you bought at a better '
                     'price than today.'),
            'fwd_yield_pct':     st.column_config.NumberColumn(
                'Fwd. Yield %', format='%.2f%%',
                help='Current forward dividend yield from Yahoo Finance (based on today\'s '
                     'market price). Compare with Ann. YOC to see if you beat the market.'),
            'ex_div_date':       st.column_config.DateColumn(
                'Ex-Div Date', format='DD/MM/YYYY',
                help='Next ex-dividend date from Yahoo Finance.'),
            'div_frequency':     st.column_config.TextColumn(
                'Frequency',
                help='Dividend payment frequency from Yahoo Finance.'),
        }
    )
    copy_df_button(df_t12[_disp_cols], key="dl_rpt_div_t12")

    # ── Pie chart: income allocation by Security Type ─────────────────────────
    df_by_type = (
        df_t12.groupby('securities_type')['period_income_eur']
        .sum()
        .reset_index()
        .sort_values('period_income_eur', ascending=False)
    )
    fig_pie = px.pie(
        df_by_type,
        names='securities_type',
        values='period_income_eur',
        title=f"<b>Income Allocation by Security Type — {period_label}</b>",
        template='plotly_dark',
        hole=0.35,
    )
    fig_pie.update_traces(
        textposition='inside',
        textinfo='percent+label',
        hovertemplate='<b>%{label}</b><br>€ %{value:,.2f}<br>%{percent}<extra></extra>',
    )
    fig_pie.update_layout(margin=dict(l=0, r=0, t=50, b=0), showlegend=True)
    pf_plotly_chart(fig_pie)

    # Full detail table
    with st.expander("Full transaction detail"):
        st.dataframe(
            df[['month', 'securities_name', 'accounts_name', 'action', 'income_eur']].style.format({'income_eur': '{:,.2f} €'}),
            hide_index=True, width='stretch',
            column_config={
                'month':          'Month',
                'securities_name':'Security',
                'accounts_name':  'Account',
                'action':         'Action',
                'income_eur':     st.column_config.NumberColumn('Income (€)', format='%,.2f €'),
            }
        )
        copy_df_button(df, key="dl_rpt_div_detail")


# ======================================================
# CASH FLOW FORECAST
# ======================================================

def render_cash_flow_forecast():
    st.subheader("🔮 Cash Flow Forecast")

    horizon     = st.sidebar.radio("Forecast horizon:", ["30 days", "60 days", "90 days"], index=1, key="cf_horizon")
    days        = int(horizon.split()[0])
    months_back = st.sidebar.slider(
        "Recurring detection window (months):",
        min_value=2, max_value=6, value=2, step=1,
        key="cf_months_back",
        help="A payee must appear in every one of these last complete calendar months to be flagged as recurring.",
    )

    df_future, df_recurring = get_cash_flow_forecast(months_back=months_back)

    today  = pd.Timestamp.now().normalize()
    cutoff = today + pd.Timedelta(days=days)

    # ── Filter scheduled transactions to the horizon ──────────────────────────
    df_f = (
        df_future[df_future['date'] <= cutoff].copy()
        if not df_future.empty else pd.DataFrame()
    )

    # ── Deduplicate: drop recurring patterns whose payee already has explicit
    #    future transactions — the scheduled entries take precedence.
    if not df_recurring.empty and not df_f.empty:
        _scheduled_payees = set(
            df_f['payees_name'].dropna().str.strip().str.lower().unique()
        )
        df_recurring = df_recurring[
            ~df_recurring['payees_name'].str.strip().str.lower().isin(_scheduled_payees)
        ].copy()

    # ── Project recurring patterns as concrete future occurrences ─────────────
    # Start from next_expected_date; if it is today-or-earlier, step forward
    # until it lands strictly in the future, then emit every occurrence up to cutoff.
    recur_rows = []
    if not df_recurring.empty:
        for _, row in df_recurring.iterrows():
            avg_days = (
                float(row['avg_days_between'])
                if pd.notna(row['avg_days_between']) else None
            )
            if not avg_days or avg_days < 1:
                continue

            next_dt = pd.Timestamp(row['next_expected_date'])
            while next_dt <= today:
                next_dt += pd.Timedelta(days=avg_days)

            while next_dt <= cutoff:
                recur_rows.append({
                    'date':             next_dt,
                    'amount_eur':       float(row['avg_amount_eur']),
                    'payees_name':      row['payees_name'],
                    'category':         row.get('category', ''),
                    'avg_days_between': avg_days,
                    'currency':         row['currency'],
                })
                next_dt += pd.Timedelta(days=avg_days)

    df_recur_proj = (
        pd.DataFrame(recur_rows).sort_values('date').reset_index(drop=True)
        if recur_rows else pd.DataFrame()
    )

    # ── Build combined rows then aggregate by month for the chart ─────────────
    chart_rows = []
    if not df_f.empty:
        for _, row in df_f.iterrows():
            amt = float(row['amount_eur']) if row['amount_eur'] is not None else 0.0
            chart_rows.append({
                'date':       row['date'],
                'amount_eur': amt,
                'source':     'Scheduled',
                'flow':       'Income' if amt >= 0 else 'Expense',
            })
    if not df_recur_proj.empty:
        for _, row in df_recur_proj.iterrows():
            amt = float(row['amount_eur']) if row['amount_eur'] is not None else 0.0
            chart_rows.append({
                'date':       row['date'],
                'amount_eur': amt,
                'source':     'Recurring (est.)',
                'flow':       'Income' if amt >= 0 else 'Expense',
            })

    # ── Combined chart + metrics ──────────────────────────────────────────────
    if chart_rows:
        df_combined = pd.DataFrame(chart_rows)
        df_combined['series'] = df_combined['flow'] + ' · ' + df_combined['source']

        # Aggregate to calendar month
        df_combined['month'] = (
            pd.to_datetime(df_combined['date'])
            .dt.to_period('M')
            .dt.to_timestamp()          # first day of each month — sorts correctly
        )
        df_monthly = (
            df_combined
            .groupby(['month', 'series'], sort=True)['amount_eur']
            .sum()
            .reset_index()
        )

        _COLOR_MAP = {
            'Income · Scheduled':          '#2ECC71',
            'Expense · Scheduled':         '#E74C3C',
            'Income · Recurring (est.)':   '#82E0AA',
            'Expense · Recurring (est.)':  '#F1948A',
        }

        sched_in  = df_f['amount_eur'][df_f['amount_eur'] > 0].sum()                   if not df_f.empty          else 0.0
        sched_out = df_f['amount_eur'][df_f['amount_eur'] < 0].sum()                   if not df_f.empty          else 0.0
        recur_in  = df_recur_proj['amount_eur'][df_recur_proj['amount_eur'] > 0].sum() if not df_recur_proj.empty else 0.0
        recur_out = df_recur_proj['amount_eur'][df_recur_proj['amount_eur'] < 0].sum() if not df_recur_proj.empty else 0.0
        net_total = sched_in + sched_out + recur_in + recur_out

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Scheduled In",  f"€ {sched_in:,.2f}")
        c2.metric("Scheduled Out", f"€ {sched_out:,.2f}")
        c3.metric("Recurring In",  f"€ {recur_in:,.2f}")
        c4.metric("Recurring Out", f"€ {recur_out:,.2f}")
        c5.metric("Total Net",     f"€ {net_total:,.2f}")

        fig = px.bar(
            df_monthly,
            x='month', y='amount_eur',
            color='series',
            color_discrete_map=_COLOR_MAP,
            barmode='relative',
            title=f"<b>Monthly cash flow forecast — next {days} days</b>",
            labels={'amount_eur': 'Amount (€)', 'month': 'Month', 'series': ''},
            template='plotly_dark',
        )
        fig.update_xaxes(tickformat='%b %Y', dtick='M1')
        fig.update_layout(
            margin=dict(l=0, r=0, t=50, b=0),
            legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
        )
        pf_plotly_chart(fig)
    else:
        st.info(f"No cash flows found within the next {days} days.")

    # ── Detail: Scheduled transactions ───────────────────────────────────────
    st.divider()
    st.markdown("#### 📅 Explicitly Scheduled Future Transactions")
    if df_f.empty:
        st.info(f"No transactions scheduled within {days} days.")
    else:
        st.dataframe(
            df_f[['date', 'payees_name', 'accounts_name', 'category', 'amount_eur', 'currency']]
            .style.format({'amount_eur': '{:,.2f} €'}),
            hide_index=True, width='stretch',
        )
        copy_df_button(df_f, key="dl_rpt_cf_future")

    # ── Detail: Projected recurring occurrences ───────────────────────────────
    st.divider()
    st.markdown("#### 🔁 Projected Recurring Payments")
    st.caption(
        f"Payee + Category combinations detected in **every one** of the last **{months_back} complete months**, "
        "projected forward at their average payment interval. "
        "Payees with explicit scheduled entries are excluded to avoid double-counting."
    )
    if df_recur_proj.empty:
        st.info(f"No recurring payments projected within {days} days.")
    else:
        st.dataframe(
            df_recur_proj[['date', 'payees_name', 'category', 'amount_eur', 'avg_days_between', 'currency']]
            .style.format({'amount_eur': '{:,.2f} €', 'avg_days_between': '{:.0f}'}),
            hide_index=True, width='stretch',
            column_config={
                'date':             st.column_config.DateColumn('Projected Date', format='DD/MM/YYYY'),
                'payees_name':      'Payee',
                'category':         'Category',
                'amount_eur':       st.column_config.NumberColumn('Est. Amount (€)', format='%,.2f €'),
                'avg_days_between': 'Interval (days)',
                'currency':         'Currency',
            }
        )
        copy_df_button(df_recur_proj, key="dl_rpt_cf_recurring")


# ======================================================
# ASSET ALLOCATION
# ======================================================

def render_asset_allocation():
    # ── Target allocation editor ──────────────────────────────────────────────
    with st.expander("⚙️ Edit Target Allocations", expanded=False):
        st.caption(
            "Rows are pre-filled from your current holdings and any previously saved targets. "
            "Add new rows for asset types not yet in your portfolio. All changes are saved on click."
        )
        df_alloc      = get_asset_allocation_data()
        df_targets_db = get_allocation_targets()

        # Build a complete picture: union of holding types and DB-saved types
        actual_map = dict(zip(df_alloc['securities_type'], df_alloc['actual_pct'])) if not df_alloc.empty else {}
        target_map = dict(zip(df_targets_db['securities_type'], df_targets_db['target_pct'])) if not df_targets_db.empty else {}
        all_types  = sorted(set(actual_map) | set(target_map))

        editor_rows = [
            {
                'Asset Type': t,
                'Actual %':   float(actual_map.get(t, 0.0)),
                'Target %':   float(target_map.get(t, 0.0)),
            }
            for t in all_types
        ]
        df_editor = pd.DataFrame(editor_rows) if editor_rows else pd.DataFrame(columns=['Asset Type', 'Actual %', 'Target %'])

        edited = st.data_editor(
            df_editor,
            column_config={
                'Asset Type': st.column_config.TextColumn('Asset Type'),
                'Actual %':   st.column_config.NumberColumn('Actual %',  format='%.2f%%', disabled=True),
                'Target %':   st.column_config.NumberColumn('Target %',  format='%.2f%%', min_value=0.0, max_value=100.0, step=0.5),
            },
            num_rows="dynamic",
            hide_index=True,
            width="stretch",
            key="alloc_target_editor",
        )

        valid_rows = edited.dropna(subset=['Asset Type'])
        valid_rows = valid_rows[valid_rows['Asset Type'].str.strip() != '']
        total_target = valid_rows['Target %'].sum()
        col_sum, col_btn = st.columns([3, 1])
        col_sum.caption(f"Sum of targets: **{total_target:.1f}%** {'✅' if abs(total_target - 100) < 0.1 else '⚠️ should sum to 100%'}")
        if col_btn.button("💾 Save Targets", key="alloc_save_btn", type="primary"):
            targets_to_save = dict(zip(valid_rows['Asset Type'].str.strip(), valid_rows['Target %'].fillna(0.0)))
            save_allocation_targets(targets_to_save)
            get_asset_allocation_data.clear()
            st.success("Target allocations saved.")
            st.rerun()

    df = get_asset_allocation_data()

    if df.empty:
        st.info("No holdings found.")
        return

    total_eur = df['value_eur'].sum()
    st.metric("Total Portfolio Value", f"€ {total_eur:,.2f}")

    col_pie, col_bar = st.columns(2)

    with col_pie:
        fig = px.pie(
            df, names='securities_type', values='value_eur',
            title="<b>Current Allocation</b>",
            template='plotly_dark',
            hole=0.4,
        )
        fig.update_traces(textinfo='percent+label')
        fig.update_layout(margin=dict(l=0, r=0, t=50, b=0), showlegend=False)
        pf_plotly_chart(fig)

    with col_bar:
        fig2 = px.bar(
            df, x='securities_type', y=['actual_pct', 'target_pct'],
            barmode='group',
            title="<b>Actual vs. Target (%)</b>",
            labels={'value': '%', 'securities_type': 'Type', 'variable': ''},
            template='plotly_dark',
            color_discrete_map={'actual_pct': '#457B9D', 'target_pct': '#F1A208'},
        )
        fig2.update_layout(margin=dict(l=0, r=0, t=50, b=0))
        pf_plotly_chart(fig2)

    st.markdown("#### Rebalancing Delta")
    df_display = df.copy()
    df_display['delta_pct']     = df_display['target_pct'] - df_display['actual_pct']
    df_display['rebalance_eur'] = df_display['delta_pct'] / 100 * total_eur

    st.dataframe(
        df_display.style.format({
            'value_eur':     '{:,.2f} €',
            'actual_pct':    '{:.2f}%',
            'target_pct':    '{:.2f}%',
            'delta_pct':     '{:+.2f}%',
            'rebalance_eur': '{:+,.2f} €',
        }).map(color_negative_red, subset=['delta_pct', 'rebalance_eur']),
        hide_index=True, width='stretch',
        column_config={
            'securities_type': 'Asset Type',
            'value_eur':       st.column_config.NumberColumn('Value (€)',   format='%,.2f €'),
            'actual_pct':      st.column_config.NumberColumn('Actual %',    format='%.2f%%'),
            'target_pct':      st.column_config.NumberColumn('Target %',    format='%.2f%%'),
            'delta_pct':       st.column_config.NumberColumn('Delta %',     format='%+.2f%%'),
            'rebalance_eur':   st.column_config.NumberColumn('Rebalance €', format='%+,.2f €'),
        }
    )
    copy_df_button(df_display, key="dl_rpt_alloc")

    # ── Rebalancing Action Plan ───────────────────────────────────────────────
    st.divider()
    st.markdown("#### ⚖️ Rebalancing Action Plan")
    st.caption(
        "Trades are distributed proportionally within each asset type. "
        "Only types with a saved target are included. "
        "Positive = buy, negative = sell."
    )

    available_cash = st.number_input(
        "Available cash to deploy (€)",
        min_value=0.0, value=0.0, step=100.0,
        format="%.2f",
        key="rebal_cash_input",
        help="Extra cash you can invest. It is added to under-weight types first.",
    )

    df_plan = get_rebalancing_plan()

    if df_plan.empty:
        st.info("No holdings or no allocation targets saved yet.")
    else:
        # Apply cash top-up: distribute available_cash proportionally to under-weight types
        if available_cash > 0 and not df_plan.empty:
            underweight_types = df_plan[df_plan['type_delta_pct'] > 0]['securities_type'].unique()
            total_under_delta = df_plan[df_plan['securities_type'].isin(underweight_types)]['suggested_delta_eur'].clip(lower=0).sum()
            if total_under_delta > 0:
                df_plan = df_plan.copy()
                mask = df_plan['securities_type'].isin(underweight_types)
                df_plan.loc[mask, 'suggested_delta_eur'] = (
                    df_plan.loc[mask, 'suggested_delta_eur']
                    + (df_plan.loc[mask, 'suggested_delta_eur'].clip(lower=0)
                       / total_under_delta * available_cash)
                )

        df_plan_display = df_plan.copy()
        df_plan_display['trade_action'] = df_plan_display['suggested_delta_eur'].apply(
            lambda x: '🟢 BUY' if x > 1 else ('🔴 SELL' if x < -1 else '✅ OK')
        )
        # Suggested shares to trade (approximate)
        df_plan_display['suggested_qty'] = (
            df_plan_display['suggested_delta_eur']
            / (df_plan_display['current_price'] * df_plan_display['fx_rate'])
        ).where(df_plan_display['current_price'] > 0).round(4)

        # Only show rows with meaningful action
        df_show = df_plan_display[df_plan_display['suggested_delta_eur'].abs() > 0.5].copy()

        if df_show.empty:
            st.success("Portfolio is within balance — no trades needed.")
        else:
            _cols = ['trade_action', 'securities_name', 'securities_type', 'ticker',
                     'currency', 'current_qty', 'current_price',
                     'value_eur', 'current_pct',
                     'type_actual_pct', 'type_target_pct', 'type_delta_pct',
                     'suggested_delta_eur', 'suggested_qty']

            st.dataframe(
                df_show[_cols].style.map(
                    lambda v: 'color:#27AE60' if isinstance(v, str) and 'BUY' in v
                    else ('color:#E74C3C' if isinstance(v, str) and 'SELL' in v else ''),
                    subset=['trade_action']
                ).map(
                    color_negative_red, subset=['suggested_delta_eur', 'type_delta_pct']
                ),
                hide_index=True,
                width='stretch',
                column_config={
                    'trade_action':       st.column_config.TextColumn('Action'),
                    'securities_name':    st.column_config.TextColumn('Security'),
                    'securities_type':    st.column_config.TextColumn('Type'),
                    'ticker':             st.column_config.TextColumn('Ticker'),
                    'currency':           st.column_config.TextColumn('Ccy'),
                    'current_qty':        st.column_config.NumberColumn('Qty',          format='%,.4f'),
                    'current_price':      st.column_config.NumberColumn('Price',        format='%,.4f'),
                    'value_eur':          st.column_config.NumberColumn('Value (€)',    format='%,.2f €'),
                    'current_pct':        st.column_config.NumberColumn('Weight %',     format='%.2f%%'),
                    'type_actual_pct':    st.column_config.NumberColumn('Type Act %',   format='%.2f%%'),
                    'type_target_pct':    st.column_config.NumberColumn('Type Tgt %',   format='%.2f%%'),
                    'type_delta_pct':     st.column_config.NumberColumn('Type Δ %',     format='%+.2f%%'),
                    'suggested_delta_eur':st.column_config.NumberColumn('Trade (€)',    format='%+,.2f €'),
                    'suggested_qty':      st.column_config.NumberColumn('Est. Shares',  format='%+,.4f'),
                },
            )
            copy_df_button(df_show[_cols], key="dl_rpt_rebal_plan")

            # Summary
            total_buy  = df_show[df_show['suggested_delta_eur'] > 0]['suggested_delta_eur'].sum()
            total_sell = df_show[df_show['suggested_delta_eur'] < 0]['suggested_delta_eur'].sum()
            sb, ss, sc = st.columns(3)
            sb.metric("Total to Buy",  f"€ {total_buy:,.2f}")
            ss.metric("Total to Sell", f"€ {total_sell:,.2f}")
            sc.metric("Net Cash Needed", f"€ {(total_buy + total_sell):,.2f}")


# ======================================================
# SECTOR & INDUSTRY ALLOCATION
# ======================================================

def render_sector_allocation():
    df = get_sector_allocation_data()

    if df.empty:
        st.info("No holdings with sector/industry data found.")
        return

    total_eur = df['value_eur'].sum()
    st.metric("Total Portfolio Value", f"€ {total_eur:,.2f}")

    # ── Sector-level rollup ───────────────────────────────────────────────────
    df_sector = (
        df.groupby('sector', as_index=False)['value_eur'].sum()
        .assign(actual_pct=lambda d: (d['value_eur'] / total_eur * 100).round(2))
        .sort_values('value_eur', ascending=False)
    )

    st.markdown("#### By Sector")
    col_pie, col_bar = st.columns(2)
    with col_pie:
        fig_s_pie = px.pie(
            df_sector, names='sector', values='value_eur',
            title="<b>Sector Allocation</b>",
            template='plotly_dark', hole=0.4,
        )
        fig_s_pie.update_traces(textinfo='percent+label')
        fig_s_pie.update_layout(margin=dict(l=0, r=0, t=50, b=0), showlegend=False)
        pf_plotly_chart(fig_s_pie)

    with col_bar:
        fig_s_bar = px.bar(
            df_sector, x='sector', y='actual_pct',
            title="<b>Sector Weight (%)</b>",
            labels={'actual_pct': '%', 'sector': 'Sector'},
            template='plotly_dark',
            text=df_sector['actual_pct'].apply(lambda v: f"{v:.1f}%"),
        )
        fig_s_bar.update_traces(textposition='outside')
        fig_s_bar.update_layout(margin=dict(l=0, r=0, t=50, b=0), yaxis_title='%')
        pf_plotly_chart(fig_s_bar)

    st.dataframe(
        df_sector.style.format({'value_eur': '{:,.2f} €', 'actual_pct': '{:.2f}%'}),
        hide_index=True, width='stretch',
        column_config={
            'sector':     'Sector',
            'value_eur':  st.column_config.NumberColumn('Value (€)',  format='%,.2f €'),
            'actual_pct': st.column_config.NumberColumn('Weight %',   format='%.2f%%'),
        }
    )
    copy_df_button(df_sector, key="dl_rpt_sector_by_sector")

    # ── Industry-level detail ─────────────────────────────────────────────────
    st.divider()
    st.markdown("#### By Industry")

    # Sector filter
    all_sectors = sorted(df['sector'].unique())
    selected_sector = st.selectbox(
        "Filter by Sector:", ['All'] + all_sectors, key="sector_alloc_filter"
    )
    df_ind = df if selected_sector == 'All' else df[df['sector'] == selected_sector]
    df_industry = (
        df_ind.groupby(['sector', 'industry'], as_index=False)['value_eur'].sum()
        .assign(actual_pct=lambda d: (d['value_eur'] / total_eur * 100).round(2))
        .sort_values('value_eur', ascending=False)
    )

    col_ind_pie, col_ind_bar = st.columns(2)
    with col_ind_pie:
        fig_i_pie = px.pie(
            df_industry, names='industry', values='value_eur',
            title="<b>Industry Allocation</b>",
            template='plotly_dark', hole=0.4,
        )
        fig_i_pie.update_traces(textinfo='percent+label')
        fig_i_pie.update_layout(margin=dict(l=0, r=0, t=50, b=0), showlegend=False)
        pf_plotly_chart(fig_i_pie)

    with col_ind_bar:
        fig_i_bar = px.bar(
            df_industry.head(20), x='industry', y='actual_pct',
            color='sector',
            title="<b>Top Industries by Weight (%)</b>",
            labels={'actual_pct': '%', 'industry': 'Industry', 'sector': 'Sector'},
            template='plotly_dark',
        )
        fig_i_bar.update_layout(
            margin=dict(l=0, r=0, t=50, b=0),
            xaxis_tickangle=-35,
            legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
        )
        pf_plotly_chart(fig_i_bar)

    st.dataframe(
        df_industry.style.format({'value_eur': '{:,.2f} €', 'actual_pct': '{:.2f}%'}),
        hide_index=True, width='stretch',
        column_config={
            'sector':     'Sector',
            'industry':   'Industry',
            'value_eur':  st.column_config.NumberColumn('Value (€)',  format='%,.2f €'),
            'actual_pct': st.column_config.NumberColumn('Weight %',   format='%.2f%%'),
        }
    )
    copy_df_button(df_industry, key="dl_rpt_sector_alloc")


# ======================================================
# FX EXPOSURE
# ======================================================

def render_fx_exposure():
    st.subheader("🌍 FX Exposure Report")

    df = get_fx_exposure_data()

    if df.empty:
        st.info("No FX exposure data found.")
        return

    total_eur = df['eur_exposure'].sum()
    st.metric("Total Net Worth (EUR)", f"€ {total_eur:,.2f}")

    fig = px.bar(
        df, x='currency', y='eur_exposure',
        title="<b>Net Exposure by Currency (€)</b>",
        template='plotly_dark',
        color='eur_exposure',
        color_continuous_scale='RdYlGn',
        labels={'eur_exposure': 'Net Exposure (€)', 'currency': 'Currency'},
        text=df['eur_exposure'].apply(lambda x: f"€ {x:,.0f}"),
    )
    fig.update_traces(textposition='outside')
    fig.update_layout(coloraxis_showscale=False, margin=dict(l=0, r=0, t=50, b=0))
    pf_plotly_chart(fig)

    st.markdown("#### Sensitivity to ±5% FX Move")
    df_display = df.copy()
    df_display['impact_up_5pct']   =  df_display['sensitivity_5pct_eur']
    df_display['impact_down_5pct'] = -df_display['sensitivity_5pct_eur']

    st.dataframe(
        df_display[['currency', 'native_exposure', 'eur_exposure', 'impact_up_5pct', 'impact_down_5pct']]
        .style.format({
            'native_exposure':   '{:,.2f}',
            'eur_exposure':      '{:,.2f} €',
            'impact_up_5pct':    '{:+,.2f} €',
            'impact_down_5pct':  '{:+,.2f} €',
        }).map(color_negative_red, subset=['impact_down_5pct']),
        hide_index=True, width='stretch',
        column_config={
            'currency':          'Currency',
            'native_exposure':   st.column_config.NumberColumn('Native Exposure', format='%,.2f'),
            'eur_exposure':      st.column_config.NumberColumn('EUR Exposure',    format='%,.2f €'),
            'impact_up_5pct':    st.column_config.NumberColumn('+5% FX Impact',   format='%+,.2f €'),
            'impact_down_5pct':  st.column_config.NumberColumn('-5% FX Impact',   format='%+,.2f €'),
        }
    )
    copy_df_button(df_display, key="dl_rpt_fx_exposure")


# ======================================================
# BOND SCHEDULE
# ======================================================

def render_bond_schedule():
    st.subheader("📅 Bond Maturity & Coupon Schedule")

    df = get_bond_schedule_data()

    if df.empty:
        st.info("No bond holdings found. Make sure Securities of type 'Bond' have Maturity_Date, Coupon_Rate, and Face_Value filled in.")
        return

    # Summary metrics
    total_face  = df['total_face_eur'].sum()
    total_ann   = df['annual_coupon_eur'].sum()
    next_12m    = df[df['days_to_maturity'] <= 365]['total_face_eur'].sum()

    m1, m2, m3 = st.columns(3)
    m1.metric("Total Face Value (€)", f"€ {total_face:,.2f}")
    m2.metric("Annual Coupon Income (€)", f"€ {total_ann:,.2f}")
    m3.metric("Maturing in 12m (€)", f"€ {next_12m:,.2f}")

    # Maturity timeline
    df_mat = df.dropna(subset=['maturity_date']).copy()
    if not df_mat.empty:
        fig = px.bar(
            df_mat.sort_values('maturity_date'),
            x='maturity_date', y='total_face_eur',
            color='securities_name',
            title="<b>Bond Maturities Timeline</b>",
            labels={'total_face_eur': 'Face Value (€)', 'maturity_date': 'Maturity Date'},
            template='plotly_dark',
            text=df_mat['securities_name'],
        )
        fig.update_traces(textposition='outside')
        fig.update_layout(showlegend=False, margin=dict(l=0, r=0, t=50, b=0))
        pf_plotly_chart(fig)

    st.markdown("#### Bond Holdings Detail")
    st.dataframe(
        df.style.format({
            'quantity':           '{:,.4f}',
            'face_value':         '{:,.2f}',
            'total_face_eur':     '{:,.2f} €',
            'coupon_rate':        '{:.4f}%',
            'next_coupon_eur':    '{:,.2f} €',
            'annual_coupon_eur':  '{:,.2f} €',
            'days_to_maturity':   '{:,.0f}',
        }, na_rep="—"),
        hide_index=True, width='stretch',
        column_config={
            'securities_name':   'Bond',
            'quantity':          st.column_config.NumberColumn('Quantity',       format='%,.4f'),
            'face_value':        st.column_config.NumberColumn('Face Value',     format='%,.2f'),
            'total_face_eur':    st.column_config.NumberColumn('Total Face (€)', format='%,.2f €'),
            'coupon_rate':       st.column_config.NumberColumn('Coupon %',       format='%.4f%%'),
            'coupon_frequency':  'Frequency',
            'next_coupon_eur':   st.column_config.NumberColumn('Next Coupon (€)',format='%,.2f €'),
            'annual_coupon_eur': st.column_config.NumberColumn('Annual Income (€)', format='%,.2f €'),
            'maturity_date':     'Maturity Date',
            'days_to_maturity':  st.column_config.NumberColumn('Days to Maturity', format='%,d'),
            'currency':          'Currency',
        }
    )
    copy_df_button(df, key="dl_rpt_bonds")


# ======================================================
# NET WORTH REPORT  (Quicken-style)
# ======================================================

def render_net_worth_report():
    st.subheader("📋 Net Worth Report")

    # ── Sidebar controls ──────────────────────────────────────────────────
    last_day_prev_month = pd.Timestamp.now().replace(day=1) - pd.Timedelta(days=1)
    start_date = st.sidebar.date_input(
        "📅 Start Date",
        value=st.session_state.nwr_date_val,
        min_value=datetime(1900, 1, 1),
        max_value=last_day_prev_month,
        key="nwr_date",
    )
    st.session_state.nwr_date_val = start_date
    interval  = st.sidebar.radio("Interval:", ["Year", "Quarter", "Month"], key="nwr_interval")
    show_zero = st.sidebar.checkbox("Show zero-balance accounts", value=False, key="nwr_show_zero")

    st.sidebar.divider()
    if st.sidebar.button("🔄 Refresh Data", key="nwr_refresh",
                         help="Recalculates all account balances and clears the data cache."):
        from database.crud import update_accounts_balances, update_investment_balances, update_pension_balances
        update_accounts_balances()
        update_investment_balances()
        update_pension_balances()
        get_net_worth_report_data.clear()
        st.rerun()
    # ── Account selection (main area, full-width) ─────────────────────────
    df_accounts = get_all_accounts_for_nwr()
    all_ids     = df_accounts['accounts_id'].tolist()
    id_to_name  = dict(zip(df_accounts['accounts_id'], df_accounts['accounts_name']))
    id_to_type  = dict(zip(df_accounts['accounts_id'], df_accounts['accounts_type']))

    saved_ids = get_nwr_account_selection()
    init_sel  = set(saved_ids) if saved_ids is not None else set(all_ids)

    df_sel = df_accounts.copy()
    df_sel.insert(0, 'Include', df_sel['accounts_id'].isin(init_sel))

    with st.expander("⚙️ Account Selection", expanded=False):
        edited_df = st.data_editor(
            df_sel.rename(columns={'accounts_name': 'Account', 'accounts_type': 'Type'}),
            column_config={
                'Include':     st.column_config.CheckboxColumn('Include', default=True),
                'accounts_id': None,
            },
            hide_index=True,
            width="stretch",
            disabled=['Account', 'Type'],
            key="nwr_account_editor",
        )
        selected_ids = edited_df[edited_df['Include']]['accounts_id'].tolist()
        col_save, _ = st.columns([1, 4])
        if col_save.button("💾 Save Selection", key="nwr_save"):
            save_nwr_account_selection(selected_ids)
            st.success("Account selection saved!")

    # ── Data ──────────────────────────────────────────────────────────────
    account_ids_tuple = tuple(sorted(selected_ids)) if selected_ids else None
    with st.spinner("Loading net worth data…"):
        df = get_net_worth_report_data(start_date.isoformat(), interval, account_ids_tuple)

    # ── Zero-balance diagnostic ────────────────────────────────────────────
    # Show accounts that are selected but have zero balance at all periods
    # (hidden by show_zero=False). Helps diagnose why an account is missing.
    if not show_zero and not df.empty and selected_ids:
        from database.connection import get_connection as _gc
        _conn = _gc()
        _df_bal = pd.read_sql(
            """SELECT Accounts_Id AS accounts_id,
                      Accounts_Name AS accounts_name,
                      Accounts_Type AS accounts_type,
                      Accounts_Balance AS accounts_balance
               FROM Accounts
               WHERE Accounts_Id = ANY(%s)
               ORDER BY Accounts_Name""",
            _conn,
            params=(selected_ids,),
        )
        _conn.close()

        _zero_accs = _df_bal[_df_bal["accounts_balance"].abs() < 0.005]
        if not _zero_accs.empty:
            with st.expander(
                f"⚠️ {len(_zero_accs)} selected account(s) have zero balance and might be hidden "
                f"(enable 'Show zero-balance accounts' or click 🔄 Refresh Data)",
                expanded=False,
            ):
                st.dataframe(
                    _zero_accs,
                    hide_index=True,
                    column_config={
                        "accounts_id":      None,
                        "accounts_name":    st.column_config.TextColumn("Account"),
                        "accounts_type":    st.column_config.TextColumn("Type"),
                        "accounts_balance": st.column_config.NumberColumn("Stored Balance", format="%.4f"),
                    },
                )
                st.caption(
                    "These accounts have `Accounts_Balance = 0` in the database. "
                    "Click **🔄 Refresh Data** in the sidebar to recalculate all balances, "
                    "or enable **Show zero-balance accounts** to see them at 0."
                )

    if df.empty:
        st.info("No data found for the selected period.")
        return

    df['period_end'] = pd.to_datetime(df['period_end'])
    period_cols = sorted(df['period_end'].unique())

    def fmt_period(dt):
        dt = pd.Timestamp(dt)
        if interval == 'Year':    return dt.strftime('%Y')
        if interval == 'Quarter': return f"{dt.year} Q{(dt.month - 1) // 3 + 1}"
        return dt.strftime('%b %Y')

    period_labels = [fmt_period(p) for p in period_cols]
    label_map     = dict(zip(period_cols, period_labels))

    # ── Shared helpers ────────────────────────────────────────────────────
    def fmt_val(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ''
        if abs(v) < 0.005:
            return '—'
        return f'€ {v:,.2f}'

    def color_neg(v):
        if isinstance(v, (int, float)) and not pd.isna(v) and v < -0.005:
            return 'color: #E74C3C'
        return ''

    # ── Tabs ──────────────────────────────────────────────────────────────
    tab_overview, tab_account_balances, tab_summary, tab_detail = st.tabs(["📊 Overview", "💰 Account Balances", "📋 Summary per Type", "🔍 Detail Analysis"])

    # ═══════════════════════════════════════════════════════════════════════
    with tab_overview:
        # ── Chart ─────────────────────────────────────────────────────────
        chart_rows = []
        for p in period_cols:
            p_df   = df[df['period_end'] == p]
            assets = p_df[p_df['section'] == 'Assets']['balance_eur'].sum()
            liabs  = p_df[p_df['section'] == 'Liabilities']['balance_eur'].sum()
            chart_rows.append({
                'Period':      label_map[p],
                'Assets':      max(assets, 0),
                'Liabilities': abs(min(liabs, 0)),
                'Net Worth':   assets + liabs,
            })
        df_chart = pd.DataFrame(chart_rows)

        fig = go.Figure()
        fig.add_trace(go.Bar(
            name='Assets', x=df_chart['Period'], y=df_chart['Assets'],
            marker_color='#2ECC71',
        ))
        fig.add_trace(go.Bar(
            name='Liabilities', x=df_chart['Period'], y=df_chart['Liabilities'],
            marker_color='#E74C3C',
        ))
        fig.add_trace(go.Scatter(
            name='Net Worth', x=df_chart['Period'], y=df_chart['Net Worth'],
            mode='lines+markers',
            line=dict(color='white', width=2),
            marker=dict(color='#E74C3C', size=8),
        ))
        fig.update_layout(
            barmode='group', template='plotly_dark',
            title='<b>Net Worth — Assets vs Liabilities</b>',
            yaxis_tickformat=',.0f', hovermode='x unified',
            margin=dict(l=0, r=0, t=50, b=0),
            legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
        )
        pf_plotly_chart(fig)

        # ── Hierarchical table ─────────────────────────────────────────────
        ASSET_GROUPS     = ['Investments', 'Pension', 'Cash & Bank', 'Other Assets', 'Other']
        LIABILITY_GROUPS = ['Credit Cards', 'Loans', 'Other Liabilities', 'Other']
        SECTIONS = [('Assets', ASSET_GROUPS), ('Liabilities', LIABILITY_GROUPS)]

        display_rows   = []
        section_totals = {}

        def _row(label, row_type, vals_by_period):
            return {'Account': label, 'row_type': row_type,
                    **{label_map[p]: vals_by_period.get(p, 0) for p in period_cols}}

        def _empty_row():
            return {'Account': '', 'row_type': 'separator',
                    **{lbl: None for lbl in period_labels}}

        for section, group_list in SECTIONS:
            display_rows.append({'Account': section.upper(), 'row_type': 'section_header',
                                 **{lbl: None for lbl in period_labels}})
            sec_total = {p: 0.0 for p in period_cols}

            for group in group_list:
                grp_df = df[(df['section'] == section) & (df['group_name'] == group)]
                if grp_df.empty:
                    continue

                grp_pivot = grp_df.pivot_table(
                    index=['accounts_id', 'accounts_name'],
                    columns='period_end',
                    values='balance_eur',
                    fill_value=0,
                    aggfunc='sum',
                )

                if not show_zero:
                    row_max = grp_pivot.reindex(columns=period_cols, fill_value=0).abs().max(axis=1)
                    visible_pivot = grp_pivot[row_max >= 0.005]
                else:
                    visible_pivot = grp_pivot

                if visible_pivot.empty and not show_zero:
                    continue

                grp_total = {p: float(visible_pivot[p].sum()) if p in visible_pivot.columns else 0.0 for p in period_cols}

                for (acc_id, acc_name), row in visible_pivot.iterrows():
                    vals = {p: float(row.get(p, 0)) for p in period_cols}
                    display_rows.append(_row(f'    {acc_name}', 'account', vals))

                display_rows.append(_row(f'  TOTAL {group}', 'group_subtotal', grp_total))
                for p in period_cols:
                    sec_total[p] += grp_total[p]

            display_rows.append(_row(f'TOTAL {section.upper()}', 'section_total', sec_total))
            section_totals[section] = sec_total
            display_rows.append(_empty_row())

        nw_vals = {p: section_totals.get('Assets', {}).get(p, 0)
                      + section_totals.get('Liabilities', {}).get(p, 0)
                   for p in period_cols}
        display_rows.append(_row('NET WORTH', 'net_worth', nw_vals))

        df_display = pd.DataFrame(display_rows)

        def row_styler(row):
            rt = row.get('row_type', 'account')
            if rt == 'net_worth':
                s = 'font-weight: bold; border-top: 2px solid rgba(255,255,255,0.6)'
            elif rt == 'section_total':
                s = 'font-weight: bold; border-top: 1px solid rgba(255,255,255,0.4)'
            elif rt == 'group_subtotal':
                s = 'font-weight: bold'
            elif rt == 'section_header':
                s = 'color: rgba(200,200,200,0.8); font-weight: bold'
            else:
                s = ''
            return [s] * len(row)

        format_dict = {lbl: fmt_val for lbl in period_labels}

        styled = (
            df_display.style
            .apply(row_styler, axis=1)
            .format(format_dict, na_rep='')
            .map(color_neg, subset=period_labels)
            .hide(axis='index')
            .hide(['row_type'], axis='columns')
        )

        st.dataframe(
            styled, width='stretch', hide_index=True,
            column_config={'Account': st.column_config.TextColumn("Account", pinned=True)},
        )
        copy_df_button(df_display, key="dl_rpt_nwr_overview")

        # ── Investment account drilldown ───────────────────────────────────
        inv_types    = {'Brokerage', 'Margin'}
        inv_accounts = df_accounts[
            df_accounts['accounts_id'].isin(selected_ids) &
            df_accounts['accounts_type'].isin(inv_types)
        ]

        if not inv_accounts.empty:
            st.markdown("---")
            st.subheader("🔍 Investment Account Detail")

            detail_acc_id = st.selectbox(
                "Select account:",
                options=inv_accounts['accounts_id'].tolist(),
                format_func=lambda x: f"{id_to_name[x]} ({id_to_type[x]})",
                key="nwr_detail_acc",
            )

            with st.spinner("Loading security detail…"):
                df_detail = get_nwr_security_detail(
                    start_date.isoformat(), interval, int(detail_acc_id)
                )

            if df_detail.empty:
                st.info("No investment transactions found for this account.")
            else:
                df_detail['period_end'] = pd.to_datetime(df_detail['period_end'])

                detail_pivot = df_detail.pivot_table(
                    index='security_name',
                    columns='period_end',
                    values='value_eur',
                    fill_value=0,
                    aggfunc='sum',
                )

                if not show_zero:
                    row_max = detail_pivot.reindex(columns=period_cols, fill_value=0).abs().max(axis=1)
                    detail_pivot = detail_pivot[row_max >= 0.005]

                if detail_pivot.empty:
                    st.info("All securities have zero value in the selected period.")
                else:
                    detail_pivot.columns = [label_map.get(c, str(c)) for c in detail_pivot.columns]
                    present_labels = [lbl for lbl in period_labels if lbl in detail_pivot.columns]
                    detail_pivot = detail_pivot[present_labels].reset_index()
                    detail_pivot.rename(columns={'security_name': 'Security'}, inplace=True)

                    total_row = {'Security': 'TOTAL'}
                    for lbl in present_labels:
                        total_row[lbl] = detail_pivot[lbl].sum()
                    detail_pivot = pd.concat(
                        [detail_pivot, pd.DataFrame([total_row])], ignore_index=True
                    )

                    def style_total_row(row):
                        if row.get('Security') == 'TOTAL':
                            return ['font-weight: bold; border-top: 1px solid rgba(255,255,255,0.4)'] * len(row)
                        return [''] * len(row)

                    styled_detail = (
                        detail_pivot.style
                        .apply(style_total_row, axis=1)
                        .format({lbl: fmt_val for lbl in present_labels}, na_rep='')
                        .map(color_neg, subset=present_labels)
                        .hide(axis='index')
                    )

                    st.dataframe(
                        styled_detail, width='stretch', hide_index=True,
                        column_config={'Security': st.column_config.TextColumn("Security", pinned=True)},
                    )
                    copy_df_button(detail_pivot, key="dl_rpt_nwr_detail")

    # ═══════════════════════════════════════════════════════════════════════
    with tab_summary:
        GROUP_ORDER = ['Cash & Bank', 'Investments', 'Pension', 'Other Assets',
                       'Credit Cards', 'Loans', 'Other Liabilities']

        summary_rows = []
        for p in period_cols:
            p_df = df[df['period_end'] == p]
            row  = {'Period': label_map[p]}
            for grp in GROUP_ORDER:
                row[grp] = p_df[p_df['group_name'] == grp]['balance_eur'].sum()
            row['Total Assets']      = p_df[p_df['section'] == 'Assets']['balance_eur'].sum()
            row['Total Liabilities'] = p_df[p_df['section'] == 'Liabilities']['balance_eur'].sum()
            row['Net Worth']         = row['Total Assets'] + row['Total Liabilities']
            summary_rows.append(row)

        df_summary = pd.DataFrame(summary_rows[::-1])  # latest first

        num_cols = [c for c in df_summary.columns if c != 'Period']

        def style_summary_row(row):
            if row.get('Period') == df_summary['Period'].iloc[0]:
                return [''] * len(row)
            return [''] * len(row)

        def color_summary_neg(v):
            if isinstance(v, (int, float)) and not pd.isna(v) and v < -0.005:
                return 'color: #E74C3C'
            return ''

        styled_summary = (
            df_summary.style
            .format({c: fmt_val for c in num_cols}, na_rep='')
            .map(color_summary_neg, subset=num_cols)
            .hide(axis='index')
        )

        st.dataframe(
            styled_summary, width='stretch', hide_index=True,
            column_config={'Period': st.column_config.TextColumn('Period', pinned=True)},
        )
        copy_df_button(df_summary, key="dl_rpt_nwr_summary")

    # ═══════════════════════════════════════════════════════════════════════
    with tab_detail:
        selected_period = st.selectbox(
            "Select Period:",
            options=period_cols[::-1],
            format_func=fmt_period,
            key="nwr_snapshot_period",
        )

        p_df   = df[df['period_end'] == selected_period]
        assets = p_df[p_df['section'] == 'Assets']['balance_eur'].sum()
        liabs  = p_df[p_df['section'] == 'Liabilities']['balance_eur'].sum()
        nw     = assets + liabs

        grp_sums = p_df.groupby('group_name')['balance_eur'].sum()

        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("Net Worth",       f"€ {nw:,.2f}")
        m2.metric("Cash & Bank",     f"€ {grp_sums.get('Cash & Bank', 0):,.2f}")
        m3.metric("Investments",     f"€ {grp_sums.get('Investments', 0):,.2f}")
        m4.metric("Pension",         f"€ {grp_sums.get('Pension', 0):,.2f}")
        m5.metric("Other Assets",    f"€ {grp_sums.get('Other Assets', 0):,.2f}")
        m6.metric("Liabilities",     f"€ {(liabs):,.2f}")

        df_breakdown = (
            grp_sums[grp_sums.abs() >= 0.005]
            .reset_index()
            .rename(columns={'group_name': 'Category', 'balance_eur': 'Value (€)'})
        )

        if not df_breakdown.empty and nw != 0:
            df_breakdown['% of Net Worth'] = (df_breakdown['Value (€)'] / nw * 100).round(2)

            col_pie, col_table = st.columns([1, 1])

            with col_pie:
                fig_pie = px.pie(
                    df_breakdown,
                    values='Value (€)',
                    names='Category',
                    title=f"<b>Net Worth Breakdown — {fmt_period(selected_period)}</b>",
                    template='plotly_dark',
                    hole=0.4,
                    color_discrete_sequence=[
                        '#457B9D', '#2ECC71', '#A8DADC', '#5D6D7E',
                        '#E74C3C', '#E67E22', '#FFD700',
                    ],
                )
                fig_pie.update_traces(textinfo='percent+label')
                fig_pie.update_layout(showlegend=False, margin=dict(l=0, r=0, t=50, b=0))
                pf_plotly_chart(fig_pie)

            with col_table:
                st.dataframe(
                    df_breakdown.style.format({
                        'Value (€)':      fmt_val,
                        '% of Net Worth': '{:.2f}%',
                    }).map(color_neg, subset=['Value (€)']).hide(axis='index'),
                    hide_index=True,
                    width='stretch',
                    column_config={
                        'Category':       st.column_config.TextColumn('Category'),
                        'Value (€)':      st.column_config.NumberColumn('Value (€)',  format='€ %,.2f'),
                        '% of Net Worth': st.column_config.NumberColumn('% of NW',   format='%.2f%%'),
                    },
                )
                copy_df_button(df_breakdown, key="dl_rpt_nwr_breakdown")

    # ═══════════════════════════════════════════════════════════════════════
    with tab_account_balances:
        # ── Stacked bar chart by account group + Balance line ──────────────
        GROUP_COLORS = {
            'Cash & Bank':       '#FFD700',
            'Investments':       '#457B9D',
            'Pension':           '#A8DADC',
            'Other Assets':      '#9B59B6',
            'Other':             '#95A5A6',
            'Credit Cards':      '#E74C3C',
            'Loans':             '#E67E22',
            'Other Liabilities': '#C0392B',
        }
        GROUP_ORDER = ['Cash & Bank', 'Investments', 'Pension', 'Other Assets', 'Other',
                       'Credit Cards', 'Loans', 'Other Liabilities']

        fig_ab = go.Figure()
        for grp in GROUP_ORDER:
            if grp not in df['group_name'].values:
                continue
            y_vals = [
                df[(df['period_end'] == p) & (df['group_name'] == grp)]['balance_eur'].sum()
                for p in period_cols
            ]
            fig_ab.add_trace(go.Bar(
                name=grp,
                x=period_labels,
                y=y_vals,
                marker_color=GROUP_COLORS.get(grp, '#95A5A6'),
            ))

        nw_line = [df[df['period_end'] == p]['balance_eur'].sum() for p in period_cols]
        fig_ab.add_trace(go.Scatter(
            name='Balance',
            x=period_labels,
            y=nw_line,
            mode='lines+markers',
            line=dict(color='#FF69B4', width=2),
            marker=dict(color='#FF69B4', size=8),
        ))
        fig_ab.update_layout(
            barmode='stack', template='plotly_dark',
            title='<b>Account Balances</b>',
            yaxis_tickformat=',.0f', hovermode='x unified',
            margin=dict(l=0, r=0, t=50, b=0),
            legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
        )
        pf_plotly_chart(fig_ab)

        # ── Table — period columns labelled with explicit end dates ─────────
        def fmt_period_date(dt):
            return pd.Timestamp(dt).strftime('%d/%m/%Y')

        date_labels   = [fmt_period_date(p) for p in period_cols]
        date_label_map = dict(zip(period_cols, date_labels))

        ACCOUNT_TYPE_GROUPS = [
            ('Assets',      'Cash & Bank',       'Bank & Cash Accounts'),
            ('Assets',      'Investments',        'Investment Accounts'),
            ('Assets',      'Pension',            'Pension Accounts'),
            ('Assets',      'Other Assets',       'Asset Accounts'),
            ('Assets',      'Other',              'Other Accounts'),
            ('Liabilities', 'Credit Cards',       'Credit Card Accounts'),
            ('Liabilities', 'Loans',              'Loan Accounts'),
            ('Liabilities', 'Other Liabilities',  'Liability Accounts'),
        ]

        ab_rows    = []
        ending_vals = {p: 0.0 for p in period_cols}

        def _ab_row(label, row_type, vals_by_period):
            return {'Account': label, 'row_type': row_type,
                    **{date_label_map[p]: vals_by_period.get(p, 0) for p in period_cols}}

        def _ab_empty():
            return {'Account': '', 'row_type': 'separator',
                    **{lbl: None for lbl in date_labels}}

        for section, group_name, display_name in ACCOUNT_TYPE_GROUPS:
            grp_df = df[(df['section'] == section) & (df['group_name'] == group_name)]
            if grp_df.empty:
                continue

            ab_rows.append({'Account': display_name, 'row_type': 'group_header',
                            **{lbl: None for lbl in date_labels}})

            grp_pivot = grp_df.pivot_table(
                index=['accounts_id', 'accounts_name'],
                columns='period_end',
                values='balance_eur',
                fill_value=0,
                aggfunc='sum',
            )

            visible_pivot = grp_pivot
            if not show_zero:
                row_max = grp_pivot.reindex(columns=period_cols, fill_value=0).abs().max(axis=1)
                visible_pivot = grp_pivot[row_max >= 0.005]

            grp_total = {p: float(visible_pivot[p].sum()) if p in visible_pivot.columns else 0.0
                         for p in period_cols}

            for (acc_id, acc_name), row in visible_pivot.iterrows():
                vals = {p: float(row.get(p, 0)) for p in period_cols}
                ab_rows.append(_ab_row(f'    {acc_name}', 'account', vals))

            ab_rows.append(_ab_row(f'TOTAL {display_name}', 'group_total', grp_total))
            ab_rows.append(_ab_empty())

            for p in period_cols:
                ending_vals[p] += grp_total[p]

        ab_rows.append(_ab_row('Ending Balance', 'ending_balance', ending_vals))

        df_ab = pd.DataFrame(ab_rows)

        def ab_row_styler(row):
            rt = row.get('row_type', 'account')
            if rt == 'ending_balance':
                return ['font-weight: bold; border-top: 2px solid rgba(255,255,255,0.6)'] * len(row)
            elif rt == 'group_total':
                return ['font-weight: bold; border-top: 1px solid rgba(255,255,255,0.4)'] * len(row)
            elif rt == 'group_header':
                return ['font-weight: bold; color: rgba(220,220,220,0.9)'] * len(row)
            return [''] * len(row)

        styled_ab = (
            df_ab.style
            .apply(ab_row_styler, axis=1)
            .format({lbl: fmt_val for lbl in date_labels}, na_rep='')
            .map(color_neg, subset=date_labels)
            .hide(axis='index')
            .hide(['row_type'], axis='columns')
        )

        st.dataframe(
            styled_ab, width='stretch', hide_index=True,
            column_config={'Account': st.column_config.TextColumn("Account", pinned=True)},
        )
        copy_df_button(df_ab, key="dl_rpt_nwr_balances")

        # ── Investment account drilldown ───────────────────────────────────
        inv_types_ab    = {'Brokerage', 'Margin'}
        inv_accounts_ab = df_accounts[
            df_accounts['accounts_id'].isin(selected_ids) &
            df_accounts['accounts_type'].isin(inv_types_ab)
        ]

        if not inv_accounts_ab.empty:
            st.markdown("---")
            st.subheader("🔍 Investment Account Detail")

            detail_acc_id_ab = st.selectbox(
                "Select account:",
                options=inv_accounts_ab['accounts_id'].tolist(),
                format_func=lambda x: f"{id_to_name[x]} ({id_to_type[x]})",
                key="nwr_detail_acc_ab",
            )

            with st.spinner("Loading security detail…"):
                df_detail_ab = get_nwr_security_detail(
                    start_date.isoformat(), interval, int(detail_acc_id_ab)
                )

            if df_detail_ab.empty:
                st.info("No investment transactions found for this account.")
            else:
                df_detail_ab['period_end'] = pd.to_datetime(df_detail_ab['period_end'])

                detail_pivot_ab = df_detail_ab.pivot_table(
                    index='security_name',
                    columns='period_end',
                    values='value_eur',
                    fill_value=0,
                    aggfunc='sum',
                )

                if not show_zero:
                    row_max_ab = detail_pivot_ab.reindex(columns=period_cols, fill_value=0).abs().max(axis=1)
                    detail_pivot_ab = detail_pivot_ab[row_max_ab >= 0.005]

                if detail_pivot_ab.empty:
                    st.info("All securities have zero value in the selected period.")
                else:
                    detail_pivot_ab.columns = [label_map.get(c, str(c)) for c in detail_pivot_ab.columns]
                    present_labels_ab = [lbl for lbl in period_labels if lbl in detail_pivot_ab.columns]
                    detail_pivot_ab = detail_pivot_ab[present_labels_ab].reset_index()
                    detail_pivot_ab.rename(columns={'security_name': 'Security'}, inplace=True)

                    total_row_ab = {'Security': 'TOTAL'}
                    for lbl in present_labels_ab:
                        total_row_ab[lbl] = detail_pivot_ab[lbl].sum()
                    detail_pivot_ab = pd.concat(
                        [detail_pivot_ab, pd.DataFrame([total_row_ab])], ignore_index=True
                    )

                    def style_total_row_ab(row):
                        if row.get('Security') == 'TOTAL':
                            return ['font-weight: bold; border-top: 1px solid rgba(255,255,255,0.4)'] * len(row)
                        return [''] * len(row)

                    styled_detail_ab = (
                        detail_pivot_ab.style
                        .apply(style_total_row_ab, axis=1)
                        .format({lbl: fmt_val for lbl in present_labels_ab}, na_rep='')
                        .map(color_neg, subset=present_labels_ab)
                        .hide(axis='index')
                    )

                    st.dataframe(
                        styled_detail_ab, width='stretch', hide_index=True,
                        column_config={'Security': st.column_config.TextColumn("Security", pinned=True)},
                    )
                    copy_df_button(detail_pivot_ab, key="dl_rpt_nwr_detail_ab")

# ======================================================
# B3. SAVINGS RATE
# ======================================================

def render_savings_rate():
    """Render the Savings Rate report."""
    st.subheader("\U0001f4b0 Savings Rate")
    st.caption(
        "**Savings Rate = (Income − Expenses) ÷ Income × 100.** "
        "A rate above 20% is generally considered healthy; above 50% is excellent for building long-term wealth."
    )

    ctrl1, ctrl2, ctrl3 = st.columns([3, 3, 1])
    months = ctrl1.slider(
        "Months back", min_value=3, max_value=60, value=12, key="sr_months",
        help="How many complete past calendar months to include in the chart.",
    )
    if ctrl3.button("\U0001f504 Refresh", key="sr_refresh"):
        get_savings_rate_data.clear()
        st.rerun()

    df = get_savings_rate_data(months)
    if df.empty:
        st.info("No savings rate data available for the selected period.")
        return

    avg_income   = df["income_eur"].mean()
    avg_expenses = df["expenses_eur"].mean()
    avg_rate     = ((avg_income - avg_expenses) / avg_income * 100) if avg_income > 0 else 0

    m1, m2, m3 = st.columns(3)
    m1.metric("Avg Monthly Income",   f"€ {avg_income:,.2f}")
    m2.metric("Avg Monthly Expenses", f"€ {avg_expenses:,.2f}")
    m3.metric("Avg Savings Rate",     f"{avg_rate:.1f}%")

    df_plot = df.copy()
    df_plot["month_str"] = df_plot["month"].dt.strftime("%b %Y")

    fig = go.Figure()
    fig.add_bar(x=df_plot["month_str"], y=df_plot["income_eur"],   name="Income",   marker_color="#2ECC71")
    fig.add_bar(x=df_plot["month_str"], y=df_plot["expenses_eur"], name="Expenses", marker_color="#E74C3C")
    fig.add_scatter(x=df_plot["month_str"], y=df_plot["savings_rate_pct"],
                    name="Savings Rate %", yaxis="y2",
                    mode="lines+markers", line=dict(color="#F39C12", width=2))
    fig.update_layout(
        template="plotly_dark",
        barmode="group",
        title="<b>Monthly Income vs Expenses & Savings Rate</b>",
        yaxis=dict(title="Amount (€)"),
        yaxis2=dict(title="Savings Rate (%)", overlaying="y", side="right", tickformat=".1f"),
        legend=dict(orientation="h", yanchor="bottom", y=-0.25, xanchor="center", x=0.5),
        margin=dict(l=0, r=0, t=50, b=0),
    )
    pf_plotly_chart(fig)

    df_display = df_plot[["month_str", "income_eur", "expenses_eur", "savings_eur", "savings_rate_pct"]].copy()
    df_display.columns = ["Month", "Income (€)", "Expenses (€)", "Savings (€)", "Savings Rate (%)"]
    st.dataframe(
        df_display.style.format({
            "Income (€)":      "€ {:,.2f}",
            "Expenses (€)":    "€ {:,.2f}",
            "Savings (€)":     "€ {:,.2f}",
            "Savings Rate (%)": "{:.1f}%",
        }).map(color_negative_red, subset=["Savings (€)"]),
        hide_index=True, width="stretch",
    )
    copy_df_button(df_display, key="dl_rpt_budget_monthly")


# ======================================================
# B4. BUDGET VS ACTUAL
# ======================================================

def render_budget_vs_actual():
    """Render the Annual Budget vs Actual report."""
    st.subheader("\U0001f4ca Budget vs Actual")
    now = datetime.now()

    # ---- Controls (on-screen, not sidebar) ----
    ctrl1, ctrl2 = st.columns([1, 2])
    year = int(ctrl1.number_input(
        "Year", min_value=2000, max_value=2100, value=now.year, step=1, key="bva_year",
        help="Calendar year to display. For the current year, actuals are year-to-date.",
    ))
    ref_years = int(ctrl2.slider(
        "Reference years (for historical avg)", min_value=1, max_value=5, value=2, key="bva_ref_years",
        help="Number of full past calendar years used to compute the Avg/Year column.",
    ))

    ytd_label    = "YTD Actual (€)"  if year == now.year else "Actual (€)"
    ytd_inc_label = "YTD Income (€)" if year == now.year else "Income (€)"
    prior_label  = f"{year - 1} Actual (€)"
    avg_col      = f"Avg/Year ({ref_years}y) €"

    st.caption(
        f"Annual budget vs actual for **{year}**. "
        + ("Actuals are year-to-date. " if year == now.year else "")
        + f"**{avg_col}** is the mean of your annual spend over {year - ref_years}–{year - 1}. "
        "Edit **Budget (€)** and press **Save All Budgets** to persist."
    )

    df = get_budget_vs_actual(int(year), ref_years)
    if df.empty:
        st.info("No expense data found for this year or reference period.")
        return

    # ---- Summary metrics ----
    total_avg    = df["avg_annual_hist"].sum()
    total_prior  = df["prior_year_amount"].sum()
    total_budget = df["budget_amount"].sum()
    total_actual = df["actual_amount"].sum()
    variance     = total_budget - total_actual
    total_income = get_annual_income(int(year))

    sm1, sm2, sm3, sm4, sm5, sm6 = st.columns(6)
    sm1.metric(f"Avg/Year ({ref_years}y)", f"€ {total_avg:,.0f}")
    sm2.metric(f"{year - 1} Total",        f"€ {total_prior:,.0f}")
    sm3.metric("Annual Budget",            f"€ {total_budget:,.0f}")
    sm4.metric(ytd_label,                  f"€ {total_actual:,.0f}")
    sm5.metric("Variance",                 f"€ {variance:,.0f}", delta=f"{variance:+,.0f}")
    sm6.metric(ytd_inc_label,              f"€ {total_income:,.0f}")

    # ---- Editable budget table ----
    st.markdown("#### Set Annual Budgets")
    df_editor = df[[
        "categories_name", "avg_annual_hist", "prior_year_amount",
        "budget_amount", "actual_amount", "variance_eur", "variance_pct",
    ]].copy()
    df_editor.columns = [
        "Category", avg_col, prior_label,
        "Budget (€)", ytd_label, "Variance (€)", "Variance %",
    ]

    edited = st.data_editor(
        df_editor,
        disabled=["Category", avg_col, prior_label, ytd_label, "Variance (€)", "Variance %"],
        column_config={
            avg_col:        st.column_config.NumberColumn(format="€ %,.2f"),
            prior_label:    st.column_config.NumberColumn(format="€ %,.2f"),
            "Budget (€)":   st.column_config.NumberColumn(format="€ %,.2f", min_value=0.0, step=100.0),
            ytd_label:      st.column_config.NumberColumn(format="€ %,.2f"),
            "Variance (€)": st.column_config.NumberColumn(format="€ %,.2f"),
            "Variance %":   st.column_config.NumberColumn(format="%.1f%%"),
        },
        hide_index=True,
        width="stretch",
        key="bva_editor",
    )

    if st.button("\U0001f4be Save All Budgets", key="bva_save_all"):
        cat_id_map = dict(zip(df["categories_name"], df["categories_id"]))
        for _, row in edited.iterrows():
            cat_id = cat_id_map.get(row["Category"])
            if cat_id is not None:
                upsert_budget(int(year), int(cat_id), float(row["Budget (€)"]))
        get_budget_vs_actual.clear()
        st.success("Budgets saved!")
        st.rerun()

    # ---- Grouped bar chart (budgeted categories only) ----
    df_budgeted = df[df["budget_amount"] > 0]
    if not df_budgeted.empty:
        fig = go.Figure()
        fig.add_bar(x=df_budgeted["categories_name"], y=df_budgeted["avg_annual_hist"],
                    name=f"Avg ({ref_years}y)", marker_color="#95A5A6")
        fig.add_bar(x=df_budgeted["categories_name"], y=df_budgeted["prior_year_amount"],
                    name=f"{year - 1} Actual", marker_color="#F39C12")
        fig.add_bar(x=df_budgeted["categories_name"], y=df_budgeted["budget_amount"],
                    name="Budget", marker_color="#3498DB")
        fig.add_bar(x=df_budgeted["categories_name"], y=df_budgeted["actual_amount"],
                    name=ytd_label.replace(" (€)", ""), marker_color="#E74C3C")
        fig.update_layout(
            template="plotly_dark", barmode="group",
            title=f"<b>Annual Budget vs Actual — {year}</b>",
            xaxis_tickangle=-40,
            margin=dict(l=0, r=0, t=50, b=130),
        )
        pf_plotly_chart(fig)

    # ---- Progress bars for budgeted categories ----
    if not df_budgeted.empty:
        st.markdown("#### Progress per Category")
        pct_of_year = now.timetuple().tm_yday / 365.25 if year == now.year else 1.0
        for _, row in df_budgeted.iterrows():
            budget = float(row["budget_amount"])
            actual = float(row["actual_amount"])
            pct    = min(actual / budget, 1.0)
            icon   = "\U0001f534" if row["over_budget"] else "\U0001f7e2"
            pace_note = ""
            if year == now.year and budget > 0:
                expected = budget * pct_of_year
                pace_note = f" · expected YTD € {expected:,.2f}"
            st.write(
                f"{icon} **{row['categories_name']}** — "
                f"€ {actual:,.2f} / € {budget:,.2f}{pace_note}"
            )
            st.progress(pct)

    # ---- Transactions drill-down by category ----
    st.markdown(f"#### {year} Transactions by Category")
    df_tx = get_ytd_expense_transactions(int(year))
    if df_tx.empty:
        st.info("No transactions found.")
    else:
        all_cats = sorted(df_tx["category"].unique())
        # Build label with total so the user can see amounts in the dropdown
        cat_totals = df_tx.groupby("category")["amount_eur"].sum()
        cat_options = [
            f"{cat}  —  € {cat_totals[cat]:,.2f}"
            for cat in all_cats
        ]
        selected_label = st.selectbox(
            "Select category", cat_options, key="bva_cat_drilldown",
        )
        selected_cat = all_cats[cat_options.index(selected_label)]
        df_cat = df_tx[df_tx["category"] == selected_cat].copy()
        df_cat["date"] = df_cat["date"].dt.strftime("%Y-%m-%d")
        st.caption(f"{len(df_cat)} transaction(s) · total € {cat_totals[selected_cat]:,.2f}")
        st.dataframe(
            df_cat[["date", "payee", "amount_eur", "notes"]].style.format(
                {"amount_eur": "€ {:,.2f}"}
            ),
            hide_index=True,
            width="stretch",
            column_config={
                "date":       "Date",
                "payee":      "Payee",
                "amount_eur": st.column_config.NumberColumn("Amount (€)", format="€ %,.2f"),
                "notes":      "Notes / Memo",
            },
        )
        copy_df_button(df_cat, key="dl_rpt_bva_drilldown")


# ======================================================
# B5. SPENDING TRENDS
# ======================================================

def render_spending_trends():
    """Render the Spending Trends report."""
    st.subheader("\U0001f4c8 Spending Trends")
    st.caption(
        "Visualise how your spending has evolved over time, broken down by top-level category. "
        "The area chart shows the cumulative monthly total per category — a growing band means rising spend in that area. "
        "Use the **Year-over-Year** section below to compare the same month across different years for a selected category."
    )

    ctrl1, ctrl2, ctrl3 = st.columns([3, 3, 1])
    months = ctrl1.slider(
        "Months back", min_value=3, max_value=60, value=24, key="st_months",
        help="Number of complete past calendar months to include.",
    )
    if ctrl3.button("\U0001f504 Refresh", key="st_refresh"):
        get_spending_trends.clear()
        get_spending_by_payee.clear()
        st.rerun()

    df = get_spending_trends(months)
    if df.empty:
        st.info("No spending data available.")
        return

    fig = px.area(
        df, x="month", y="amount_eur", color="category",
        title="<b>Monthly Spending by Category</b>",
        template="plotly_dark",
        labels={"amount_eur": "Amount (€)", "month": "Month", "category": "Category"},
    )
    fig.update_layout(margin=dict(l=0, r=0, t=50, b=0))
    pf_plotly_chart(fig)

    st.markdown("#### Year-over-Year Comparison")
    all_categories    = sorted(df["category"].unique().tolist())
    selected_category = st.selectbox("Select Category for YoY", options=all_categories, key="st_yoy_cat")

    df_cat = df[df["category"] == selected_category].copy()
    df_cat["year"]      = df_cat["month"].dt.year
    df_cat["month_num"] = df_cat["month"].dt.month
    month_names = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
                   7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}
    df_cat["month_name"] = df_cat["month_num"].map(month_names)

    fig_yoy = px.bar(
        df_cat, x="month_name", y="amount_eur", color="year",
        barmode="group",
        title=f"<b>{selected_category} — Year-over-Year</b>",
        template="plotly_dark",
        labels={"amount_eur": "Amount (€)", "month_name": "Month", "year": "Year"},
        category_orders={"month_name": list(month_names.values())},
    )
    fig_yoy.update_layout(margin=dict(l=0, r=0, t=50, b=0))
    pf_plotly_chart(fig_yoy)

    # ── Payee breakdown ───────────────────────────────────────────────────────
    st.markdown("#### Payee Breakdown")
    st.caption("Top payees within the selected category over the same lookback window.")

    df_payee = get_spending_by_payee(selected_category, months)
    if df_payee.empty:
        st.info("No payee data for this category.")
        return

    col_tbl, col_chart = st.columns([1, 1])

    with col_tbl:
        df_pay_disp = df_payee.copy()
        df_pay_disp['first_seen'] = df_pay_disp['first_seen'].dt.strftime("%Y-%m-%d")
        df_pay_disp['last_seen']  = df_pay_disp['last_seen'].dt.strftime("%Y-%m-%d")
        df_pay_disp['share_pct']  = (df_pay_disp['amount_eur'] / df_pay_disp['amount_eur'].sum() * 100).round(1)
        st.dataframe(
            df_pay_disp.style.format({
                "amount_eur": "{:,.2f} €",
                "share_pct":  "{:.1f}%",
            }),
            hide_index=True, width="stretch",
            column_config={
                "payee":      "Payee",
                "tx_count":   st.column_config.NumberColumn("# Txns", format="%d"),
                "amount_eur": st.column_config.NumberColumn("Total (€)", format="%,.2f €"),
                "share_pct":  st.column_config.NumberColumn("Share",    format="%.1f%%"),
                "first_seen": "First",
                "last_seen":  "Last",
            },
        )
        copy_df_button(df_pay_disp, key="dl_spending_payee_drill")

    with col_chart:
        top_n = df_payee.head(15)
        fig_pay = px.bar(
            top_n.sort_values('amount_eur'),
            x='amount_eur', y='payee', orientation='h',
            title=f"<b>Top Payees — {selected_category}</b>",
            template="plotly_dark",
            labels={"amount_eur": "Total (€)", "payee": ""},
            text='amount_eur',
        )
        fig_pay.update_traces(texttemplate="€%{x:,.0f}", textposition="outside")
        fig_pay.update_layout(margin=dict(l=0, r=0, t=50, b=0), showlegend=False)
        pf_plotly_chart(fig_pay)

    # ── Payee transaction detail + edit ──────────────────────────────────────
    st.markdown("#### Transaction Detail & Edit")
    st.caption(
        "Select a payee to view individual transactions. "
        "You can change the **Category** or edit the **Notes** inline and press **Save Changes**."
    )

    payees_list = df_payee["payee"].tolist()
    selected_payee = st.selectbox(
        "Select Payee", payees_list, key="st_payee_select",
        help="Transactions for this payee over the same lookback window (including current month).",
    )

    df_txn = get_payee_transactions(selected_payee, selected_category, months)
    if df_txn.empty:
        st.info("No transactions found for this payee in the selected period.")
        return

    df_cats = get_expense_categories()
    cat_options = df_cats["full_path"].tolist()
    cat_id_map  = dict(zip(df_cats["full_path"], df_cats["categories_id"]))

    total = df_txn["amount_eur"].sum()
    st.caption(f"{len(df_txn)} transaction(s) · total € {total:,.2f}")

    df_edit = df_txn[["splits_id", "date", "category", "amount_eur", "notes"]].copy()
    df_edit["date"] = df_edit["date"].dt.strftime("%Y-%m-%d")

    edited = st.data_editor(
        df_edit,
        disabled=["splits_id", "date", "amount_eur"],
        column_config={
            "splits_id":  st.column_config.NumberColumn("ID",          format="%d"),
            "date":       st.column_config.TextColumn("Date"),
            "category":   st.column_config.SelectboxColumn("Category", options=cat_options, required=True),
            "amount_eur": st.column_config.NumberColumn("Amount (€)",  format="€ %,.2f"),
            "notes":      st.column_config.TextColumn("Notes"),
        },
        hide_index=True,
        width="stretch",
        key=f"st_txn_editor_{selected_payee}_{selected_category}",
    )

    if st.button("\U0001f4be Save Changes", key="st_save_txn"):
        changes = 0
        errors  = []
        for i, row in edited.iterrows():
            orig         = df_txn.iloc[i]
            orig_cat_id  = int(orig["categories_id"])
            orig_notes   = orig["notes"] or ""
            new_cat_id   = cat_id_map.get(row["category"])
            new_notes    = row["notes"] or ""
            if new_cat_id is None:
                errors.append(f"Row {i+1}: unknown category '{row['category']}'")
                continue
            if new_cat_id != orig_cat_id or new_notes != orig_notes:
                update_split(int(row["splits_id"]), new_cat_id, new_notes or None)
                changes += 1
        for e in errors:
            st.error(e)
        if changes:
            get_spending_trends.clear()
            get_spending_by_payee.clear()
            st.success(f"Saved {changes} change(s).")
            st.rerun()
        elif not errors:
            st.info("No changes detected.")


# ======================================================
# B6. BUDGET & SPENDING PAGE
# ======================================================

def render_budget_and_spending():
    """Budget & Spending page with 3 tabs."""
    st.subheader("Budget & Spending")
    tab_budget, tab_trends, tab_savings = st.tabs([
        "\U0001f4ca Budget vs Actual",
        "\U0001f4c8 Spending Trends",
        "\U0001f4b0 Savings Rate",
    ])
    with tab_budget:
        render_budget_vs_actual()
    with tab_trends:
        render_spending_trends()
    with tab_savings:
        render_savings_rate()


# ======================================================
# B7. CAPITAL GAINS
# ======================================================

def _compute_lot_gains(df_all: pd.DataFrame, tax_year: int,
                       method: str = 'FIFO') -> pd.DataFrame:
    """Compute capital gains using FIFO or LIFO lot matching.

    Processes ALL transactions (not just the tax year) so that lot queues are
    correct for positions opened before the tax year.  Only sells that fall
    within *tax_year* are included in the returned DataFrame.

    method: 'FIFO' — consume oldest lots first (default)
            'LIFO' — consume most-recently-added lots first
    """
    from collections import deque

    BUY_ACTIONS  = {'Buy', 'Reinvest', 'ShrIn', 'CashIn'}
    SELL_ACTIONS = {'Sell', 'Expire', 'ShrOut', 'CashOut'}
    is_lifo = (method == 'LIFO')

    results = []

    for (sec_id, acc_id), grp in df_all.groupby(['securities_id', 'accounts_id'], sort=False):
        lot_q: deque = deque()   # items: {'date': pd.Timestamp, 'qty': float, 'cost_ps': float}

        for _, row in grp.sort_values(['date', 'investments_id']).iterrows():
            action = row['action']
            qty    = float(row['quantity'])   if pd.notna(row['quantity'])   else 0.0
            amount = float(row['amount_eur']) if pd.notna(row['amount_eur']) else 0.0

            if action in BUY_ACTIONS and qty > 1e-9:
                lot_q.append({'date': row['date'], 'qty': qty,
                               'cost_ps': amount / qty})

            elif action in SELL_ACTIONS and qty > 1e-9:
                sell_date = row['date']
                qty_left  = qty
                cost_basis = 0.0
                first_buy_date = None   # date of the first lot consumed (oldest for FIFO, newest for LIFO)

                while qty_left > 1e-9 and lot_q:
                    # LIFO: take from the right (newest); FIFO: take from the left (oldest)
                    lot = lot_q[-1] if is_lifo else lot_q[0]
                    if first_buy_date is None:
                        first_buy_date = lot['date']
                    consumed = min(lot['qty'], qty_left)
                    cost_basis += consumed * lot['cost_ps']
                    lot['qty']  -= consumed
                    qty_left    -= consumed
                    if lot['qty'] < 1e-9:
                        if is_lifo:
                            lot_q.pop()
                        else:
                            lot_q.popleft()

                if sell_date.year == tax_year:
                    days_held  = (sell_date - first_buy_date).days if first_buy_date else 0
                    gain_loss  = amount - cost_basis
                    results.append({
                        'securities_name':  row['securities_name'],
                        'account_name':     row['account_name'],
                        'sell_date':        sell_date,
                        'quantity':         qty,
                        'sell_price':       row.get('price_per_share'),
                        'sell_amount_eur':  amount,
                        'cost_basis_eur':   cost_basis,
                        'gain_loss_eur':    gain_loss,
                        'days_held':        days_held,
                        'holding_type':     'Long-term' if days_held >= 365 else 'Short-term',
                        'is_tax_exempt':    bool(row.get('is_tax_exempt', False)),
                        'instrument_type':  row.get('instrument_type'),
                        'securities_type':  row.get('securities_type'),
                    })

    return pd.DataFrame(results) if results else pd.DataFrame(
        columns=['securities_name','account_name','sell_date','quantity',
                 'sell_price','sell_amount_eur','cost_basis_eur','gain_loss_eur',
                 'days_held','holding_type','is_tax_exempt','instrument_type',
                 'securities_type'])


def render_capital_gains():
    """Render the Capital Gains report."""
    st.subheader("\U0001f4cb Capital Gains Report")
    st.caption(
        "Lists all sell transactions for the selected tax year, showing the realised gain or loss per position. "
        "**Short-term** gains (held ≤ 1 year) are typically taxed at a higher rate than **Long-term** gains (held > 1 year). "
        "**WAC** (Weighted Average Cost) uses the average cost of all open lots. "
        "**FIFO** (First-In First-Out) matches the oldest lots first. "
        "**LIFO** (Last-In First-Out) matches the most recently purchased lots first — "
        "often produces the largest cost basis for recently-bought positions."
    )

    current_year = datetime.now().year
    year_options = list(range(current_year, current_year - 10, -1))

    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        tax_year = st.selectbox("Tax Year", options=year_options, key="cg_tax_year",
                                help="Select the tax year for which to report realised gains and losses.")
    with c2:
        method = st.radio(
            "Cost Basis Method",
            ["WAC (Weighted Avg)", "FIFO (First-In First-Out)", "LIFO (Last-In First-Out)"],
            key="cg_method", horizontal=True,
            help=(
                "WAC: average cost of all open lots (single pool). "
                "FIFO: oldest lots consumed first — maximises holding period for long-held positions. "
                "LIFO: newest lots consumed first — reflects the cost of the most recent purchase."
            ),
        )
    with c3:
        tax_rate = st.number_input(
            "Derivatives Tax Rate (%)",
            min_value=0.0, max_value=100.0,
            value=15.0, step=0.5, key="cg_tax_rate",
            help=(
                "Applied to net gains from **CFDs** (Category 2) and **precious-metal FX spots** "
                "(Category 3). Greek rate is 15% (Art. 42, L.4172/2013). "
                "Real shares & UCITS funds (Category 1) are always 0% CGT in Greece."
            ),
        )

    use_lot_method = method.startswith("FIFO") or method.startswith("LIFO")
    lot_method = "LIFO" if method.startswith("LIFO") else "FIFO"

    if use_lot_method:
        df_all = get_all_inv_txns_for_gains()
        if df_all.empty:
            st.info("No investment transactions found.")
            return
        df = _compute_lot_gains(df_all, int(tax_year), method=lot_method)
    else:
        df = get_capital_gains_report(int(tax_year))
        # Add days_held to WAC results using sell_date and last_buy_date proxy
        # (holding_type already set by the query; derive days_held from it for display)
        if not df.empty and 'days_held' not in df.columns:
            df['days_held'] = df['holding_type'].map({'Short-term': 180, 'Long-term': 400})

    if df.empty:
        st.info(f"No sell transactions found for {tax_year}.")
        return

    # ── Guard: ensure all classification columns exist ────────────────────────
    for _col, _default in [('is_tax_exempt', False), ('instrument_type', None),
                            ('securities_type', None)]:
        if _col not in df.columns:
            df[_col] = _default

    # ── Greek CGT category classification ─────────────────────────────────────
    # Priority: Instrument_Type on the transaction (explicit) →
    #           fallback to Securities_Type on the master record (when blank).
    #
    # Cat 1: real shares / UCITS / bonds  → 0% CGT (E1 Codes 867–868)
    # Cat 2: CfdOn* / CFD                 → 15%    (E1 Codes 865–866 / 869–870)
    # Cat 3: FxSpot                       → 15%    (same E1 codes as Cat 2)
    #
    # IMPORTANT: is_tax_exempt is irrelevant for Cat 2/3 — trading a tax-exempt
    # security as a CFD or FxSpot makes the gain fully taxable.

    def _classify(row) -> int:
        instr = (row.get('instrument_type') or '').strip().lower()
        stype = (row.get('securities_type') or '').strip().lower()
        # Transaction-level instrument type takes priority
        if instr.startswith('cfdon') or instr == 'cfd':
            return 2
        if instr == 'fxspot':
            return 3
        # Fallback to security master type when instrument_type is not recorded
        if stype == 'cfd':
            return 2
        if stype == 'fx spot':
            return 3
        return 1

    df['_tax_cat'] = df.apply(_classify, axis=1)

    # ── Split into exempt / taxable ───────────────────────────────────────────
    # CFD/FxSpot trades are ALWAYS taxable — instrument type overrides is_tax_exempt.
    # Only Cat 1 rows of tax-exempt securities go to the exempt bucket.
    _is_derivative = df['_tax_cat'].isin([2, 3])
    df_exempt  = df[ df['is_tax_exempt'] & ~_is_derivative].copy()
    df_taxable = df[~df['is_tax_exempt'] |  _is_derivative].copy()
    df_cat1  = df_taxable[df_taxable['_tax_cat'] == 1].copy()
    df_cat2  = df_taxable[df_taxable['_tax_cat'] == 2].copy()
    df_cat3  = df_taxable[df_taxable['_tax_cat'] == 3].copy()
    df_deriv = df_taxable[df_taxable['_tax_cat'].isin([2, 3])].copy()

    if not df_exempt.empty:
        st.success(
            f"✅ **{len(df_exempt)} tax-exempt sale(s) excluded** "
            f"(€ {df_exempt['gain_loss_eur'].sum():+,.2f} net G/L) — shown in expander below."
        )

    # ── Top headline: key numbers at a glance ─────────────────────────────────
    net_cat1  = df_cat1['gain_loss_eur'].sum()  if not df_cat1.empty  else 0.0
    net_deriv = df_deriv['gain_loss_eur'].sum() if not df_deriv.empty else 0.0
    tax_est   = max(net_deriv, 0) * (tax_rate / 100)

    hm1, hm2, hm3 = st.columns(3)
    hm1.metric("🟢 Cat 1 Net G/L — Real Shares",     f"€ {net_cat1:+,.2f}",
               help="Direct equity / UCITS positions — 0% CGT in Greece.")
    hm2.metric("🔴 Cat 2+3 Net G/L — Derivatives",   f"€ {net_deriv:+,.2f}",
               help="CFDs and precious-metal FX spots — taxed at the rate shown.")
    hm3.metric(f"Est. Derivatives Tax @ {tax_rate:.0f}%", f"€ {tax_est:,.2f}",
               help="Only on net positive derivative gains. Category 1 is always 0%.")

    st.divider()

    # ── Shared table helper ────────────────────────────────────────────────────
    def _gains_table(data: pd.DataFrame, key: str):
        disp = data.copy()
        disp['sell_date'] = pd.to_datetime(disp['sell_date']).dt.strftime('%Y-%m-%d')
        fmt = {'quantity': '{:,.4f}', 'sell_amount_eur': '{:,.2f} €',
               'cost_basis_eur': '{:,.2f} €', 'gain_loss_eur': '{:+,.2f} €'}
        if 'sell_price' in disp.columns:
            fmt['sell_price'] = '{:,.4f}'
        _has_instr = (
            'instrument_type' in disp.columns
            and disp['instrument_type'].notna().any()
            and (disp['instrument_type'].astype(str).str.strip() != '').any()
        )
        col_cfg = {
            'securities_name':  'Security',
            'account_name':     'Account',
            'sell_date':        'Sell Date',
            'quantity':         st.column_config.NumberColumn('Quantity',        format='%,.4f'),
            'sell_price':       st.column_config.NumberColumn('Sell Price',      format='%,.4f'),
            'sell_amount_eur':  st.column_config.NumberColumn('Proceeds (€)',    format='%,.2f €'),
            'cost_basis_eur':   st.column_config.NumberColumn('Cost Basis (€)',  format='%,.2f €'),
            'gain_loss_eur':    st.column_config.NumberColumn('Gain / Loss (€)', format='%+,.2f €'),
            'days_held':        st.column_config.NumberColumn('Days Held',       format='%d'),
            'holding_type':     'Term',
            'instrument_type':  st.column_config.TextColumn(
                                    'Instrument', width='small',
                                    help='Specific traded instrument (e.g. CFDOnETF, Stock, ETF).'
                                ) if _has_instr else None,
            # Classification / internal columns — hidden from display
            'is_tax_exempt':    None,
            '_tax_cat':         None,
            'securities_type':  None,
        }
        st.dataframe(
            disp.style.format(fmt).map(color_negative_red, subset=['gain_loss_eur']),
            hide_index=True, width="stretch", column_config=col_cfg,
        )
        copy_df_button(disp, key=key)

    # ══════════════════════════════════════════════════════════════════════════
    # CATEGORY 1 — Real Shares & UCITS Funds (0% CGT)
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown('### 🟢 Category 1 — Real Shares & UCITS Funds (0% CGT)')
    st.caption(
        'Direct investments in equities, ETFs, UCITS funds and bonds. '
        'Capital gains are **0% tax-free** in Greece for individual investors holding < 0.5% of the company '
        '(Art. 42, L.4172/2013). '
        'Net profits must still be declared in **E1 Table 4E, Codes 867–868** to clear *τεκμήρια* '
        '(living-standard presumptions).'
    )
    if df_cat1.empty:
        st.info(f'No Category 1 sell transactions found for {tax_year}.')
    else:
        g1  = df_cat1[df_cat1['gain_loss_eur'] > 0]['gain_loss_eur'].sum()
        l1  = df_cat1[df_cat1['gain_loss_eur'] < 0]['gain_loss_eur'].sum()
        st1 = df_cat1[df_cat1['holding_type'] == 'Short-term']['gain_loss_eur'].sum()
        lt1 = df_cat1[df_cat1['holding_type'] == 'Long-term']['gain_loss_eur'].sum()
        c1a, c1b, c1c, c1d, c1e, c1f = st.columns(6)
        c1a.metric('Total Gains',  f'€ {g1:,.2f}')
        c1b.metric('Total Losses', f'€ {l1:,.2f}')
        c1c.metric('Net G/L',      f'€ {net_cat1:+,.2f}')
        c1d.metric('Short-term',   f'€ {st1:+,.2f}')
        c1e.metric('Long-term',    f'€ {lt1:+,.2f}')
        c1f.metric('Tax',          '€ 0.00', help='Category 1 is always 0% CGT in Greece.')
        _gains_table(df_cat1, 'dl_rpt_cg_cat1')
        if len(df_cat1) > 1:
            fig1 = px.bar(
                df_cat1, x='securities_name', y='gain_loss_eur', color='holding_type',
                title='<b>Category 1 — Gains / Losses by Security</b>',
                template='plotly_dark',
                labels={'gain_loss_eur': 'Gain / Loss (€)', 'securities_name': 'Security'},
                barmode='group',
            )
            fig1.update_layout(margin=dict(l=0, r=0, t=50, b=0))
            pf_plotly_chart(fig1)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # CATEGORY 2 — CFDs & Financial Derivatives (15%)
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown(f'### 🔴 Category 2 — CFDs & Financial Derivatives ({tax_rate:.0f}% Tax)')
    st.caption(
        'Contracts for Difference on any underlying — stocks, ETFs, indices, commodities. '
        'Even if the underlying is a UCITS ETF, trading it **as a CFD** removes the 0% exemption. '
        f'Net gains taxed at **{tax_rate:.0f}%**. '
        'E1 reporting: **net profit → Codes 865–866** · **net loss → Codes 869–870** '
        '(carry-forward up to 5 years).'
    )
    if df_cat2.empty:
        st.info(f'No Category 2 (CFD) sell transactions found for {tax_year}.')
    else:
        g2   = df_cat2[df_cat2['gain_loss_eur'] > 0]['gain_loss_eur'].sum()
        l2   = df_cat2[df_cat2['gain_loss_eur'] < 0]['gain_loss_eur'].sum()
        net2 = df_cat2['gain_loss_eur'].sum()
        c2a, c2b, c2c = st.columns(3)
        c2a.metric('Total Gains',  f'€ {g2:,.2f}')
        c2b.metric('Total Losses', f'€ {l2:,.2f}')
        c2c.metric('Net G/L',      f'€ {net2:+,.2f}')
        _gains_table(df_cat2, 'dl_rpt_cg_cat2')

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # CATEGORY 3 — FX Precious Metal Spots (15%)
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown(f'### ⚠️ Category 3 — FX Precious Metal Spots ({tax_rate:.0f}% Tax)')
    st.caption(
        'Transactions recorded with **Instrument Type = FxSpot** — precious-metal spot contracts '
        '(e.g. Silver, Gold) traded on Saxo or similar platforms. '
        'Treated as financial commodity derivatives under Greek law — '
        f'same **{tax_rate:.0f}%** rate and same E1 form codes as Category 2: '
        '**net profit → Codes 865–866** · **net loss → Codes 869–870**.'
    )
    if df_cat3.empty:
        st.info(f'No Category 3 (FX precious metal) sell transactions found for {tax_year}.')
    else:
        g3   = df_cat3[df_cat3['gain_loss_eur'] > 0]['gain_loss_eur'].sum()
        l3   = df_cat3[df_cat3['gain_loss_eur'] < 0]['gain_loss_eur'].sum()
        net3 = df_cat3['gain_loss_eur'].sum()
        c3a, c3b, c3c = st.columns(3)
        c3a.metric('Total Gains',  f'€ {g3:,.2f}')
        c3b.metric('Total Losses', f'€ {l3:,.2f}')
        c3c.metric('Net G/L',      f'€ {net3:+,.2f}')
        _gains_table(df_cat3, 'dl_rpt_cg_cat3')

    # ══════════════════════════════════════════════════════════════════════════
    # COMBINED DERIVATIVES SUMMARY (Cat 2 + Cat 3 share the same E1 codes)
    # ══════════════════════════════════════════════════════════════════════════
    if not df_deriv.empty:
        st.divider()
        st.markdown('### 📊 Combined Derivatives Summary (Categories 2 + 3)')
        st.caption(
            'Categories 2 and 3 are **netted together** on the Greek tax return '
            '(they share the same E1 form codes).'
        )
        net_d2 = df_cat2['gain_loss_eur'].sum() if not df_cat2.empty else 0.0
        net_d3 = df_cat3['gain_loss_eur'].sum() if not df_cat3.empty else 0.0
        tax_d  = max(net_deriv, 0) * (tax_rate / 100)
        sd1, sd2, sd3, sd4 = st.columns(4)
        sd1.metric('CFDs Net G/L (Cat 2)',            f'€ {net_d2:+,.2f}')
        sd2.metric('Precious Metals Net G/L (Cat 3)', f'€ {net_d3:+,.2f}')
        sd3.metric('Combined Net G/L',                f'€ {net_deriv:+,.2f}')
        sd4.metric(f'Est. Tax @ {tax_rate:.0f}%',     f'€ {tax_d:,.2f}',
                   help='Applied only on net positive combined derivative gains.')
        if net_deriv >= 0:
            st.success(
                f'📝 **E1 Declaration (Derivatives):** Report **€ {net_deriv:,.2f}** net profit '
                f'in **Table 4E, Codes 865–866**.'
            )
        else:
            st.warning(
                f'📝 **E1 Declaration (Derivatives):** Report **€ {abs(net_deriv):,.2f}** net loss '
                f'in **Table 4E, Codes 869–870** — carry-forward for up to 5 years.'
            )

    # ── Tax-exempt gains (expander) ───────────────────────────────────────────
    if not df_exempt.empty:
        st.divider()
        net_ex = df_exempt['gain_loss_eur'].sum()
        with st.expander(
            f'🟢 Tax-Exempt Sales — {len(df_exempt)} transaction(s), '
            f'€ {net_ex:+,.2f} net G/L (excluded from all totals)',
            expanded=False,
        ):
            st.caption(
                'These securities are marked **Tax Exempt** in the Securities master data '
                '(e.g. Hellenic T-Bills purchased at primary market). '
                'Shown here for reference only — not included in any category above.'
            )
            _gains_table(df_exempt, 'dl_rpt_capital_gains_exempt')

    # ── Reference note ────────────────────────────────────────────────────────
    st.info(
        'ℹ️ **Greek CGT Quick Reference (L.4172/2013, Art. 42):** '
        '**Cat 1** — Direct purchase (Instrument Type blank or non-derivative; '
        'falls back to Security Type when Instrument Type is not set): **0% CGT**; '
        'declare profits in E1 Codes **867–868**. '
        '**Cat 2** — Instrument Type **CfdOn*** or **CFD** (or Security Type = CFD): **15%**. '
        '**Cat 3** — Instrument Type **FxSpot** (or Security Type = FX Spot): **15%**, same codes as Cat 2. '
        'Note: trading a **tax-exempt** security (e.g. T-Bill) as a CFD or FxSpot makes the gain **fully taxable** — '
        'instrument type always overrides the tax-exempt flag. '
        'Categories 2 & 3 are netted together: profits → E1 Codes **865–866** · '
        'losses → E1 Codes **869–870** (carry-forward ≤ 5 years). '
        'All figures are indicative — consult a certified Greek tax advisor.'
    )


# ======================================================
# B8. TAX-LOSS HARVESTING
# ======================================================

def render_tax_loss_harvesting():
    """Render Tax-Loss Harvesting opportunities."""
    st.subheader("\U0001f33f Tax-Loss Harvesting Opportunities")
    st.caption(
        "**Tax-loss harvesting** is the practice of selling positions that are currently at a loss in order to realise "
        "that loss and offset it against capital gains (or, in some jurisdictions, ordinary income up to a limit). "
        "The table below shows all current holdings with an unrealised loss. "
        "Selling these positions before year-end can reduce your tax liability. "
        "Always consult a qualified tax advisor, and pay close attention to the **wash-sale** warning below."
    )

    df = get_tax_loss_opportunities()
    if df.empty:
        st.info("No positions with unrealized losses found. Great job!")
        return

    total_harvestable = df["unrealized_loss_eur"].sum()
    st.metric("Total Harvestable Loss", f"€ {total_harvestable:,.2f}")

    st.info(
        "⚠️ **Wash-sale rules**: In many jurisdictions, if you sell a security at a loss "
        "and repurchase the same (or substantially identical) security within 30 days before or "
        "after the sale, the loss may be disallowed. Consult a tax advisor before harvesting losses."
    )

    st.dataframe(
        df.style.format({
            "quantity":            "{:,.4f}",
            "current_price":       "€ {:,.4f}",
            "cost_basis":          "€ {:,.4f}",
            "current_value_eur":   "€ {:,.2f}",
            "cost_basis_eur":      "€ {:,.2f}",
            "unrealized_loss_eur": "€ {:,.2f}",
            "loss_pct":            "{:.2f}%",
        }).map(color_negative_red, subset=["unrealized_loss_eur", "loss_pct"]),
        hide_index=True, width="stretch",
    )
    copy_df_button(df, key="dl_rpt_tax_harvest")


# ======================================================
# B8b. DIVIDEND & INTEREST INCOME
# ======================================================

def render_investment_income():
    """Render the Dividend & Interest Income report."""
    st.subheader("\U0001f4b0 Dividend & Interest Income")
    st.caption(
        "Taxable income for the selected tax year across two sources: "
        "**Investment income** (dividends, reinvested dividends, and interest from your securities) "
        "and **Bank & Savings interest** (interest credited to Checking, Savings, and Cash accounts). "
        "**Return of Capital (RtrnCap)** events are listed for reference only — they are not taxable income "
        "but reduce cost basis and affect capital gains on eventual sale. "
        "All amounts are converted to EUR. Consult your tax advisor for the applicable rates."
    )

    current_year = datetime.now().year
    year_options = list(range(current_year, current_year - 5, -1))
    tax_year = st.selectbox("Tax Year", options=year_options, key="inc_tax_year",
                            help="Select the tax year for which to report income.")

    df_inv  = get_investment_income_report(int(tax_year))
    df_bank = get_bank_interest_report(int(tax_year))

    # Split investment income into taxable and tax-exempt rows.
    _income_actions = ["Dividend", "Reinvest", "IntInc"]
    if not df_inv.empty:
        df_inv_taxable = df_inv[~df_inv["is_tax_exempt"] & df_inv["action"].isin(_income_actions)]
        df_inv_exempt  = df_inv[ df_inv["is_tax_exempt"] & df_inv["action"].isin(_income_actions)]
        df_inv_roc     = df_inv[df_inv["action"] == "RtrnCap"]
    else:
        df_inv_taxable = df_inv_exempt = df_inv_roc = df_inv

    total_div     = df_inv_taxable[df_inv_taxable["action"].isin(["Dividend","Reinvest"])]["amount_eur"].sum() if not df_inv_taxable.empty else 0.0
    total_int_inv = df_inv_taxable[df_inv_taxable["action"] == "IntInc"]["amount_eur"].sum()                   if not df_inv_taxable.empty else 0.0
    total_exempt  = df_inv_exempt["amount_eur"].sum()                                                           if not df_inv_exempt.empty  else 0.0
    total_roc     = df_inv_roc["amount_eur"].sum()                                                              if not df_inv_roc.empty     else 0.0
    total_bank    = df_bank["amount_eur"].sum()                                                                 if not df_bank.empty        else 0.0
    # Grand total = taxable investment income + bank interest (RtrnCap and tax-exempt excluded).
    grand_total   = total_div + total_int_inv + total_bank

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Dividends (incl. Reinvested)", f"€ {total_div:,.2f}")
    m2.metric("Investment Interest",          f"€ {total_int_inv:,.2f}")
    m3.metric("Bank / Savings Interest",      f"€ {total_bank:,.2f}")
    m4.metric("Taxable Total",                f"€ {grand_total:,.2f}")

    if total_exempt:
        st.success(
            f"✅ **Tax-Exempt Investment Income: € {total_exempt:,.2f}** — "
            "Income from securities marked **Tax Exempt** (e.g. Hellenic T-Bills at primary market) "
            "is excluded from the Taxable Total above. "
            "It is shown in a separate section below."
        )
    if total_roc:
        st.info(
            f"ℹ️ **Return of Capital (RtrnCap): € {total_roc:,.2f}** — "
            "This is a return of your own invested capital, **not taxable income**. "
            "It is shown below for reference only. "
            "Each RtrnCap event reduces the cost basis of the holding; "
            "the tax impact appears as a higher capital gain when you eventually sell."
        )

    # ── Section 1a: Taxable Investment Income ────────────────────────────────
    st.markdown("#### 📈 Taxable Investment Income (Securities)")
    if df_inv_taxable.empty:
        st.info(f"No taxable dividend or interest income from securities found for {tax_year}.")
    else:
        df_t_disp = df_inv_taxable.copy()
        df_t_disp["date"] = df_t_disp["date"].dt.strftime("%Y-%m-%d")
        st.dataframe(
            df_t_disp.style.format({"amount_eur": "{:,.2f} €"}),
            hide_index=True,
            width="stretch",
            column_config={
                "securities_name": "Security",
                "account_name":    "Account",
                "date":            "Date",
                "action":          "Type",
                "amount_eur":      st.column_config.NumberColumn("Amount (€)", format="%,.2f €"),
                "is_tax_exempt":   None,
            },
        )
        copy_df_button(df_t_disp, key="dl_rpt_inv_income_taxable")

        df_by_sec = df_inv_taxable.groupby(["securities_name", "action"], as_index=False)["amount_eur"].sum()
        fig = px.bar(
            df_by_sec, x="securities_name", y="amount_eur", color="action",
            title="<b>Taxable Investment Income by Security</b>",
            template="plotly_dark",
            labels={"amount_eur": "Amount (€)", "securities_name": "Security", "action": "Type"},
            barmode="stack",
        )
        fig.update_layout(margin=dict(l=0, r=0, t=50, b=0))
        pf_plotly_chart(fig)

    # ── Section 1b: Tax-Exempt Investment Income ──────────────────────────────
    if not df_inv_exempt.empty:
        st.markdown("#### 🟢 Tax-Exempt Investment Income (reference only — not included in taxable total)")
        df_e_disp = df_inv_exempt.copy()
        df_e_disp["date"] = df_e_disp["date"].dt.strftime("%Y-%m-%d")
        st.dataframe(
            df_e_disp.style.format({"amount_eur": "{:,.2f} €"}),
            hide_index=True,
            width="stretch",
            column_config={
                "securities_name": "Security",
                "account_name":    "Account",
                "date":            "Date",
                "action":          "Type",
                "amount_eur":      st.column_config.NumberColumn("Amount (€)", format="%,.2f €"),
                "is_tax_exempt":   None,
            },
        )
        copy_df_button(df_e_disp, key="dl_rpt_inv_income_exempt")

    # ── Section 1c: Return of Capital (reference) ────────────────────────────
    if not df_inv_roc.empty:
        with st.expander(f"🔄 Return of Capital events — € {total_roc:,.2f} (not taxable income)"):
            df_r_disp = df_inv_roc.copy()
            df_r_disp["date"] = df_r_disp["date"].dt.strftime("%Y-%m-%d")
            st.dataframe(
                df_r_disp.style.format({"amount_eur": "{:,.2f} €"}),
                hide_index=True,
                width="stretch",
                column_config={
                    "securities_name": "Security",
                    "account_name":    "Account",
                    "date":            "Date",
                    "action":          "Type",
                    "amount_eur":      st.column_config.NumberColumn("Amount (€)", format="%,.2f €"),
                    "is_tax_exempt":   None,
                },
            )
            copy_df_button(df_r_disp, key="dl_rpt_inv_roc")

    # ── Section 2: Bank & Savings Interest ───────────────────────────────────
    st.markdown("#### 🏦 Bank & Savings Interest")
    if df_bank.empty:
        st.info(f"No bank or savings interest found for {tax_year}.")
    else:
        df_bank_disp = df_bank.copy()
        df_bank_disp["date"] = df_bank_disp["date"].dt.strftime("%Y-%m-%d")
        st.dataframe(
            df_bank_disp.style.format({"amount_eur": "{:,.2f} €"}),
            hide_index=True,
            width="stretch",
            column_config={
                "date":         "Date",
                "account_name": "Account",
                "account_type": "Account Type",
                "payee":        "Bank / Payee",
                "category":     "Category",
                "currency":     "Currency",
                "amount_eur":   st.column_config.NumberColumn("Amount (€)", format="%,.2f €"),
            },
        )
        copy_df_button(df_bank_disp, key="dl_rpt_bank_interest")

        df_by_acc = df_bank.groupby("account_name", as_index=False)["amount_eur"].sum().sort_values("amount_eur", ascending=False)
        fig2 = px.bar(
            df_by_acc, x="account_name", y="amount_eur",
            title="<b>Bank & Savings Interest by Account</b>",
            template="plotly_dark",
            labels={"amount_eur": "Amount (€)", "account_name": "Account"},
        )
        fig2.update_layout(margin=dict(l=0, r=0, t=50, b=0))
        pf_plotly_chart(fig2)


# ======================================================
# B9. Investment Tax Report PAGE
# ======================================================

def render_tax_report():
    """Investment Tax Report page."""
    st.subheader("Investment Tax Report")
    tab_cg, tab_tlh, tab_inc = st.tabs([
        "\U0001f4cb Capital Gains",
        "\U0001f33f Tax-Loss Harvesting",
        "\U0001f4b0 Dividend & Interest Income",
    ])
    with tab_cg:
        render_capital_gains()
    with tab_tlh:
        render_tax_loss_harvesting()
    with tab_inc:
        render_investment_income()


# ======================================================
# B10. GOALS
# ======================================================

def render_goals():
    """Render the Financial Goals tracker."""
    st.subheader("\U0001f3af Financial Goals")
    st.caption(
        "Track progress towards your personal financial milestones — such as building an emergency fund, "
        "saving for a house deposit, or funding a holiday. "
        "For each active goal you can see how much has been saved, how much remains, and how much you need to set aside "
        "each month to reach the target on time. Use the expander below to add or update goals."
    )

    df = get_goals()

    with st.expander("➕ Add / Edit Goal"):
        goal_ids    = [None] + (df["goal_id"].tolist() if not df.empty else [])
        goal_labels = ["New Goal"] + (df["goal_name"].tolist() if not df.empty else [])
        goal_sel_label = st.selectbox("Select Goal to Edit", options=goal_labels, key="goals_edit_sel")
        goal_sel_idx   = goal_labels.index(goal_sel_label)
        goal_id_edit   = goal_ids[goal_sel_idx]

        default_row = (
            df[df["goal_id"] == goal_id_edit].iloc[0]
            if (goal_id_edit is not None and not df.empty and goal_id_edit in df["goal_id"].values)
            else None
        )

        g_name    = st.text_input("Goal Name",
                                   value=default_row["goal_name"] if default_row is not None else "",
                                   key="goals_name")
        g_target  = st.number_input("Target Amount (€)", min_value=0.0, step=100.0,
                                     value=float(default_row["target_amount"]) if default_row is not None else 0.0,
                                     key="goals_target")
        g_current = st.number_input("Current Amount (€)", min_value=0.0, step=100.0,
                                     value=float(default_row["current_amount"]) if default_row is not None else 0.0,
                                     key="goals_current")
        _default_date = None
        if default_row is not None and default_row["target_date"] is not None:
            try:
                _d = default_row["target_date"]
                _default_date = _d.date() if hasattr(_d, "date") else _d
            except Exception:
                _default_date = None
        g_date  = st.date_input("Target Date (optional)", value=_default_date, key="goals_date")
        g_notes = st.text_area("Notes",
                                value=(default_row["notes"] or "") if default_row is not None else "",
                                key="goals_notes")

        col_save, col_del = st.columns([1, 1])
        if col_save.button("\U0001f4be Save Goal", key="goals_save"):
            upsert_goal(goal_id_edit, g_name, g_target, g_date, g_current, g_notes)
            st.success("Goal saved!")
            st.rerun()
        if goal_id_edit is not None and col_del.button("\U0001f5d1️ Delete Goal", key="goals_delete"):
            delete_goal(int(goal_id_edit))
            st.success("Goal deleted.")
            st.rerun()

    if df.empty:
        st.info("No active goals. Add your first goal above!")
        return

    for _, row in df.iterrows():
        pct       = float(row["progress_pct"])
        remaining = row["target_amount"] - row["current_amount"]
        st.markdown(
            f"**{row['goal_name']}** — "
            f"€ {row['current_amount']:,.2f} / € {row['target_amount']:,.2f} ({pct:.1f}%)"
        )
        st.progress(min(pct / 100, 1.0))

        if row["target_date"] is not None:
            try:
                td = row["target_date"]
                if hasattr(td, "date"):
                    td = td.date()
                days_left = (td - datetime.now().date()).days
                if days_left > 0 and remaining > 0:
                    months_left    = max(days_left / 30.44, 1)
                    monthly_needed = remaining / months_left
                    st.caption(f"\U0001f4c5 {days_left} days remaining · Need € {monthly_needed:,.2f}/month")
                elif days_left <= 0:
                    st.caption("⚠️ Target date has passed.")
            except Exception:
                pass

        if row["notes"]:
            st.caption(f"\U0001f4dd {row['notes']}")
        st.divider()


# ======================================================
# B11. FIRE CALCULATOR
# ======================================================

def render_fire_calculator():
    """FIRE Calculator."""
    st.subheader("\U0001f525 FIRE Calculator")
    st.caption(
        "**FIRE** (Financial Independence, Retire Early) is based on the idea that once your investment portfolio "
        "is large enough to sustain your annual expenses indefinitely, you no longer *need* to work. "
        "The **FIRE Number** is your target portfolio size: **Annual Expenses ÷ Safe Withdrawal Rate**. "
        "At a 4% SWR (the classic \"4% rule\" from the Trinity Study), a portfolio of 25× your annual spending is considered sufficient. "
        "Adjust the inputs below to model your own scenario — the chart shows how long it will take to cross the FIRE line."
    )

    _default_portfolio = get_investable_portfolio_value()

    col1, col2 = st.columns(2)
    with col1:
        current_portfolio = st.number_input("Current Portfolio (€)", min_value=0.0, step=1000.0, value=_default_portfolio, key="fire_portfolio",
                                            help="Total value of your investable assets today (holdings + pension + investment accounts). Pre-filled from live data.")
        monthly_savings   = st.number_input("Monthly Savings (€)",   min_value=0.0, step=100.0,  value=1000.0,  key="fire_savings",
                                            help="Amount added to the portfolio each month going forward.")
        annual_return     = st.number_input("Expected Annual Return (%)", min_value=0.0, max_value=30.0, value=7.0, step=0.1, key="fire_return",
                                            help="Expected average annual nominal return. Historically a diversified equity portfolio has returned ~7-10% nominal.")
    with col2:
        withdrawal_rate = st.number_input("Safe Withdrawal Rate (%)", min_value=1.0, max_value=10.0, value=4.0, step=0.1, key="fire_withdrawal",
                                          help="Annual percentage of portfolio withdrawn in retirement. The '4% rule' is the most widely cited guideline, though lower rates (3–3.5%) are more conservative.")
        annual_expenses = st.number_input("Annual Expenses (€)", min_value=0.0, step=1000.0, value=30000.0, key="fire_expenses",
                                          help="Your expected annual spending in retirement (in today's euros).")

    fire_number = annual_expenses / (withdrawal_rate / 100)
    st.metric("FIRE Number", f"€ {fire_number:,.2f}")

    r_monthly     = (1 + annual_return / 100) ** (1 / 12) - 1
    portfolio     = current_portfolio
    years_to_fire = None
    pv            = [portfolio]
    for m in range(1, 60 * 12 + 1):
        portfolio = portfolio * (1 + r_monthly) + monthly_savings
        pv.append(portfolio)
        if portfolio >= fire_number and years_to_fire is None:
            years_to_fire = m / 12

    if years_to_fire:
        st.success(f"\U0001f389 You could reach FIRE in **{years_to_fire:.1f} years**.")
    else:
        st.warning("At current savings rate, FIRE target may not be reached within 60 years.")

    max_months = min(len(pv), 30 * 12 + 1)
    df_fire = pd.DataFrame({"Years": [m / 12 for m in range(max_months)], "Portfolio (€)": pv[:max_months]})
    fig = go.Figure()
    fig.add_scatter(x=df_fire["Years"], y=df_fire["Portfolio (€)"],
                    name="Portfolio", mode="lines", line=dict(color="#2ECC71", width=2))
    fig.add_hline(y=fire_number, line_dash="dash", line_color="#F39C12",
                  annotation_text=f"FIRE: € {fire_number:,.0f}")
    fig.update_layout(
        template="plotly_dark", title="<b>Projected Portfolio Value</b>",
        xaxis_title="Years", yaxis_title="Portfolio (€)",
        margin=dict(l=0, r=0, t=50, b=0),
    )
    pf_plotly_chart(fig)

    swr_rates = [3.0, 3.5, 4.0, 4.5, 5.0]
    swr_data  = [{"SWR (%)": r, "FIRE Number (€)": f"€ {annual_expenses / (r / 100):,.0f}"} for r in swr_rates]
    st.markdown("#### Safe Withdrawal Rate Sensitivity")
    df_swr = pd.DataFrame(swr_data)
    st.dataframe(df_swr, hide_index=True, width="stretch")
    copy_df_button(df_swr, key="dl_rpt_fire_swr")


# ======================================================
# B12. LOAN AMORTIZATION
# ======================================================

def render_loan_amortization():
    """Loan Amortization Calculator."""
    import calendar as _cal

    st.subheader("\U0001f3e6 Loan Amortization")
    st.caption(
        "Enter your loan details to see the full repayment schedule. "
        "Each monthly payment is split into **Principal** (reduces the outstanding balance) and **Interest** (cost of borrowing). "
        "In a standard fixed-payment (annuity) loan, early payments are mostly interest; the principal share grows over time. "
        "The stacked bar chart illustrates this shift. "
        "The **Total Interest** metric shows the total extra cost of borrowing over the life of the loan."
    )

    col1, col2 = st.columns(2)
    with col1:
        loan_amount = st.number_input("Loan Amount (€)", min_value=0.0, step=1000.0, value=100000.0, key="loan_amount",
                                      help="The original principal amount borrowed.")
        annual_rate = st.number_input("Annual Interest Rate (%)", min_value=0.0, max_value=50.0, value=3.5, step=0.1, key="loan_rate",
                                      help="Nominal annual interest rate. For variable-rate loans use the current rate as an approximation.")
    with col2:
        term_months = st.number_input("Term (months)", min_value=1, max_value=480, value=120, step=1, key="loan_term",
                                      help="Total number of monthly payments. E.g. 120 = 10 years, 360 = 30 years.")
        start_date  = st.date_input("Start Date", value=datetime.now().date(), key="loan_start",
                                    help="Date of the first payment.")

    if loan_amount <= 0 or term_months <= 0:
        st.info("Please enter valid loan parameters.")
        return

    monthly_rate = (annual_rate / 100) / 12
    if monthly_rate > 0:
        payment = loan_amount * (monthly_rate * (1 + monthly_rate) ** term_months) / ((1 + monthly_rate) ** term_months - 1)
    else:
        payment = loan_amount / term_months

    balance  = loan_amount
    rows     = []
    pay_date = start_date
    for i in range(1, int(term_months) + 1):
        interest  = balance * monthly_rate
        principal = payment - interest
        balance   = max(balance - principal, 0)
        rows.append({
            "Payment #":       i,
            "Date":            pay_date.strftime("%Y-%m-%d"),
            "Payment (€)":   payment,
            "Principal (€)": principal,
            "Interest (€)":  interest,
            "Balance (€)":   balance,
        })
        nxt_month = pay_date.month + 1
        nxt_year  = pay_date.year + (nxt_month - 1) // 12
        nxt_month = ((nxt_month - 1) % 12) + 1
        last_day  = _cal.monthrange(nxt_year, nxt_month)[1]
        pay_date  = pay_date.replace(year=nxt_year, month=nxt_month, day=min(pay_date.day, last_day))

    df_amort       = pd.DataFrame(rows)
    total_paid     = df_amort["Payment (€)"].sum()
    total_interest = df_amort["Interest (€)"].sum()

    sm1, sm2 = st.columns(2)
    sm1.metric("Total Paid",     f"€ {total_paid:,.2f}")
    sm2.metric("Total Interest", f"€ {total_interest:,.2f}")

    fig = go.Figure()
    fig.add_bar(x=df_amort["Payment #"], y=df_amort["Principal (€)"], name="Principal", marker_color="#2ECC71")
    fig.add_bar(x=df_amort["Payment #"], y=df_amort["Interest (€)"],  name="Interest",  marker_color="#E74C3C")
    fig.update_layout(
        template="plotly_dark", barmode="stack",
        title="<b>Principal vs Interest per Payment</b>",
        xaxis_title="Payment #", yaxis_title="Amount (€)",
        margin=dict(l=0, r=0, t=50, b=0),
    )
    pf_plotly_chart(fig)

    fmts = {
        "Payment (€)":   "€ {:,.2f}",
        "Principal (€)": "€ {:,.2f}",
        "Interest (€)":  "€ {:,.2f}",
        "Balance (€)":   "€ {:,.2f}",
    }
    st.dataframe(df_amort.style.format(fmts), hide_index=True, width="stretch")
    copy_df_button(df_amort, key="dl_rpt_loan_amort")


# ======================================================
# B13. FINANCIAL PLANNING PAGE
# ======================================================

def render_financial_planning():
    """Financial Planning page."""
    st.subheader("Financial Planning")
    tab_goals, tab_fire, tab_loan = st.tabs([
        "\U0001f3af Goals",
        "\U0001f525 FIRE Calculator",
        "\U0001f3e6 Loan Amortization",
    ])
    with tab_goals:
        render_goals()
    with tab_fire:
        render_fire_calculator()
    with tab_loan:
        render_loan_amortization()


# ======================================================
# B13b. BENCHMARK COMPARISON
# ======================================================

def render_benchmark_comparison(account_ids: tuple = None, preset_label: str = "Full Portfolio"):
    """Render the Benchmark Comparison (price-return indexed chart)."""
    st.subheader("📊 Benchmark Comparison (Price Performance)")
    st.caption(
        "Compares the **cumulative price return** of your holdings (value-weighted by position size) "
        "against a chosen market index. Cash flows — buys, sells, dividends received as cash, "
        "and bond maturities — are excluded so the comparison is apples-to-apples."
    )

    df_bm_list  = get_benchmark_candidates(min_days=30)
    if df_bm_list.empty:
        st.info(
            "No **Market Index** securities found with sufficient price history. "
            "Go to **Market Data → Securities** and set the type to *Market Index* "
            "for the indices you want to use as benchmarks (e.g. S&P 500, ATHEX)."
        )
        return
    bm_idx_opts = {"— None —": None}
    bm_idx_opts.update({row["name"]: int(row["id"]) for _, row in df_bm_list.iterrows()})

    _cc1, _cc2, _cc3 = st.columns([3, 2, 1])
    with _cc1:
        bm_idx_label = st.selectbox(
            "Overlay benchmark",
            list(bm_idx_opts.keys()),
            key="perf_chart_benchmark",
            help="Market index to compare against.",
        )
    with _cc2:
        bm_start_date = st.date_input(
            "Start date",
            value=(datetime.today() - timedelta(days=365 * 3)).date(),
            key="perf_bm_chart_start_date",
            help="Start date for the comparison (both lines are indexed to 100 here).",
        )
    with _cc3:
        bm_smooth = st.selectbox(
            "Resample",
            ["Daily", "Weekly", "Monthly"],
            index=2,
            key="perf_chart_bm_resample",
        )

    bm_idx_sec_id = bm_idx_opts[bm_idx_label]
    resample_map  = {"Daily": "D", "Weekly": "W", "Monthly": "ME"}
    resample_freq = resample_map[bm_smooth]
    bm_earliest   = pd.to_datetime(bm_start_date)
    lookback_bm   = max(60, (pd.Timestamp.today() - bm_earliest).days + 30)

    df_px   = get_price_returns(lookback_bm, account_ids)
    fig_idx = go.Figure()
    port_ret = None

    if df_px is not None and not df_px.empty:
        df_w = get_portfolio_weights(account_ids)
        if not df_w.empty:
            wmap  = dict(zip(df_w["ticker"], df_w["weight"]))
            avail = [c for c in df_px.columns if c in wmap]
            if avail:
                w = pd.Series([wmap[t] for t in avail], index=avail)
                w = w / w.sum()
                port_ret = df_px[avail].pct_change(fill_method=None).dropna().dot(w)
            else:
                port_ret = df_px.pct_change(fill_method=None).dropna().mean(axis=1)
        else:
            port_ret = df_px.pct_change(fill_method=None).dropna().mean(axis=1)

        port_cum = (1 + port_ret).cumprod()
        port_cum = port_cum[port_cum.index >= bm_earliest]
        port_cum = port_cum.resample(resample_freq).last().dropna()
        if not port_cum.empty:
            port_indexed  = port_cum / port_cum.iloc[0] * 100
            port_total_ret = port_indexed.iloc[-1] - 100
            fig_idx.add_trace(go.Scatter(
                x=port_indexed.index, y=port_indexed.values,
                name=f"{preset_label}  {port_total_ret:+.1f}%", line=dict(color="white", width=3),
            ))

    if bm_idx_sec_id is not None:
        bm_prices = get_benchmark_returns(bm_idx_sec_id, lookback_bm)
        if not bm_prices.empty:
            if port_ret is not None:
                all_dates  = port_ret.index.union(bm_prices.index).sort_values()
                bm_aligned = bm_prices.reindex(all_dates).ffill().reindex(port_ret.index)
                bm_ret     = bm_aligned.pct_change(fill_method=None).dropna()
                bm_cum     = (1 + bm_ret).cumprod()
            else:
                bm_cum = (1 + bm_prices.pct_change(fill_method=None).dropna()).cumprod()
            bm_cum = bm_cum[bm_cum.index >= bm_earliest]
            bm_cum = bm_cum.resample(resample_freq).last().dropna()
            if not bm_cum.empty:
                bm_indexed    = bm_cum / bm_cum.iloc[0] * 100
                bm_total_ret  = bm_indexed.iloc[-1] - 100
                fig_idx.add_trace(go.Scatter(
                    x=bm_indexed.index, y=bm_indexed.values,
                    name=f"{bm_idx_label}  {bm_total_ret:+.1f}%", line=dict(dash="dash", width=2),
                ))
        else:
            st.caption(f"No price history found for **{bm_idx_label}** in the selected period.")

    if fig_idx.data:
        fig_idx.update_layout(
            title=(
                f"<b>Price Return vs Benchmark (Indexed to 100)</b>"
                f"<br><sup>Preset: {preset_label} · from {bm_start_date}</sup>"
            ),
            template="plotly_dark",
            yaxis_title="Cumulative return (start = 100)",
            margin=dict(l=0, r=0, t=70, b=0),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        pf_plotly_chart(fig_idx)
    elif df_px is None or df_px.empty:
        st.info("No price history found for the selected accounts. Try a different preset or wider date range.")


# ======================================================
# B14. RISK METRICS
# ======================================================

def render_risk_metrics(account_ids: tuple = None):
    """Render Portfolio Risk Metrics."""
    st.subheader("⚡ Portfolio Risk Metrics")
    st.caption(
        "Quantifies the risk profile of your current portfolio using historical price data. "
        "Returns are **value-weighted** by current position size and use a **3% risk-free rate**.\n\n"
        "- **Ann. Volatility** — annualised standard deviation of daily returns; higher = more volatile.\n"
        "- **Sharpe Ratio** — excess return per unit of total risk. Above 1.0 is good; above 2.0 is excellent.\n"
        "- **Sortino Ratio** — like Sharpe but only penalises downside volatility. Preferred when returns are skewed.\n"
        "- **Max Drawdown** — largest peak-to-trough decline in the period; indicates the worst-case loss an investor would have suffered.\n"
        "- **VaR 95%** — on a typical day there is only a 5% chance the portfolio loses *more* than this percentage.\n"
        "- **CVaR 95%** — average loss on the worst 5% of days (also called Expected Shortfall); a more conservative tail-risk measure.\n"
        "- **Beta** — sensitivity of portfolio returns to the chosen benchmark. Beta > 1 means more volatile than the market; < 1 means less.\n"
        "- **Alpha (Jensen's)** — annualised excess return above what CAPM predicts given your Beta. Positive alpha means outperformance.\n\n"
        "The rolling Sharpe chart shows how risk-adjusted performance has changed over time — dips below zero indicate periods of underperformance vs. the risk-free rate."
    )

    col_lb, col_bm = st.columns([2, 2])
    with col_lb:
        lookback = st.slider("Lookback days", min_value=60, max_value=3650, value=756, step=92, key="risk_lookback",
                             help=(
                                 "Number of **calendar days** of price history to include.\n\n"
                                 "| Slider | Calendar | Trading days |\n"
                                 "|--------|----------|--------------|\n"
                                 "| 365 | 1 year | ~252 |\n"
                                 "| 756 | ~3 years | ~540 |\n"
                                 "| 1 260 | ~3.5 years | ~900 |\n"
                                 "| 1 825 | 5 years | ~1 300 |\n"
                                 "| 3 650 | 10 years | ~2 600 |\n\n"
                                 "**Basel III** internal models require ≥ 1 000 trading days (~4 years). "
                                 "**3 years is the practitioner minimum** for Historical Simulation VaR to capture a full market cycle."
                             ))
    with col_bm:
        df_bm_cands = get_benchmark_candidates(min_days=30)
        bm_options  = {"— None —": None}
        bm_options.update({row["name"]: int(row["id"]) for _, row in df_bm_cands.iterrows()})
        bench_label = st.selectbox("Benchmark for Beta / Alpha", list(bm_options.keys()), key="risk_benchmark",
                                   help="Select any security with price history to compute Beta and Jensen's Alpha.")
        bench_sec_id = bm_options[bench_label]

    df_prices = get_price_returns(lookback, account_ids)

    if df_prices is None or df_prices.empty or df_prices.shape[1] < 1:
        st.info("Insufficient price history to compute risk metrics. Need at least 30 days of data for current holdings.")
        return

    try:
        # Forward-fill up to 5 trading days to bridge exchange-calendar gaps
        # (e.g. Greek holidays absent from US price feed), then only drop rows
        # where every security is still NaN — preserving the full date range.
        daily_returns = df_prices.ffill(limit=5).pct_change(fill_method=None).dropna(how='all')
        if daily_returns.empty or len(daily_returns) < 10:
            st.info("Not enough return data to compute risk metrics.")
            return

        # Value-weighted portfolio returns; fall back to equal-weight when weights unavailable
        df_weights = get_portfolio_weights(account_ids)
        if not df_weights.empty:
            wmap  = dict(zip(df_weights["ticker"], df_weights["weight"]))
            avail = [c for c in daily_returns.columns if c in wmap]
            if avail:
                w = pd.Series([wmap[t] for t in avail], index=avail)
                w = w / w.sum()
                # fillna(0): on days a security didn't trade, treat its return
                # as 0% rather than propagating NaN into the portfolio return.
                port_returns = daily_returns[avail].fillna(0).dot(w)
            else:
                port_returns = daily_returns.mean(axis=1)
        else:
            port_returns = daily_returns.mean(axis=1)

        portfolio_value = get_investable_portfolio_value(account_ids)

        ann_vol    = port_returns.std() * np.sqrt(252)
        ann_return = (1 + port_returns.mean()) ** 252 - 1
        rf_rate    = 0.03
        excess     = ann_return - rf_rate
        sharpe     = excess / ann_vol if ann_vol > 0 else 0

        down_ret = port_returns[port_returns < 0]
        down_dev = down_ret.std() * np.sqrt(252) if len(down_ret) > 0 else 0
        sortino  = excess / down_dev if down_dev > 0 else 0

        cum_ret  = (1 + port_returns).cumprod()
        roll_max = cum_ret.cummax()
        drawdown = (cum_ret - roll_max) / roll_max
        max_dd   = drawdown.min()

        var_95      = np.percentile(port_returns, 5)
        cvar_95     = port_returns[port_returns <= var_95].mean()
        var_95_eur  = abs(var_95)  * portfolio_value
        cvar_95_eur = abs(cvar_95) * portfolio_value

        # Beta & Alpha vs chosen benchmark
        # Benchmark is forward-filled onto the portfolio's date grid so that
        # different trading calendars (e.g. Greek vs US market holidays) don't
        # produce an empty intersection.
        beta  = None
        alpha = None
        if bench_sec_id is not None:
            bench_prices = get_benchmark_returns(bench_sec_id, lookback)
            if not bench_prices.empty:
                all_dates     = port_returns.index.union(bench_prices.index).sort_values()
                bench_aligned = bench_prices.reindex(all_dates).ffill().reindex(port_returns.index)
                bench_ret     = bench_aligned.pct_change(fill_method=None).dropna()
                common_idx    = port_returns.index.intersection(bench_ret.index)
                if len(common_idx) >= 30:
                    p = port_returns.loc[common_idx].values
                    b = bench_ret.loc[common_idx].values
                    bench_var = np.var(b)
                    if bench_var > 0:
                        beta  = float(np.cov(p, b)[0, 1] / bench_var)
                        bench_ann_ret = (1 + bench_ret.mean()) ** 252 - 1
                        alpha = float(ann_return - (rf_rate + beta * (bench_ann_ret - rf_rate)))

        n_days  = len(port_returns)
        date_from = port_returns.index.min().strftime("%Y-%m-%d")
        date_to   = port_returns.index.max().strftime("%Y-%m-%d")
        if n_days < lookback * 0.5:   # actual data covers less than half the requested window
            st.warning(
                f"⚠️ Only **{n_days} trading days** of data available ({date_from} → {date_to}), "
                f"covering less than half the requested {lookback}-day window. "
                "Download more historical prices to extend the analysis."
            )
        else:
            st.caption(f"Using **{n_days} trading days** of return data ({date_from} → {date_to}).")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Ann. Volatility", f"{ann_vol * 100:.2f}%")
        m2.metric("Sharpe Ratio",    f"{sharpe:.2f}")
        m3.metric("Sortino Ratio",   f"{sortino:.2f}")
        m4.metric("Max Drawdown",    f"{max_dd * 100:.2f}%")

        m5, m6, m7, m8 = st.columns(4)
        m5.metric("VaR 95% (daily)",  f"{var_95 * 100:.2f}%  ·  € {var_95_eur:,.0f}")
        m6.metric("CVaR 95% (daily)", f"{cvar_95 * 100:.2f}%  ·  € {cvar_95_eur:,.0f}")
        m7.metric("Beta",  f"{beta:.2f}"  if beta  is not None else "—",
                  help=f"vs {bench_label}" if bench_sec_id else None)
        m8.metric("Alpha (annualised)", f"{alpha * 100:.2f}%" if alpha is not None else "—",
                  help=f"Jensen's Alpha vs {bench_label}" if bench_sec_id else None)

        rolling_sharpe = port_returns.rolling(30).apply(
            lambda x: (x.mean() * 252 - rf_rate) / (x.std() * np.sqrt(252)) if x.std() > 0 else 0,
            raw=True,
        )
        df_rs = pd.DataFrame({"Date": port_returns.index, "Rolling 30d Sharpe": rolling_sharpe.values})
        fig = px.line(df_rs, x="Date", y="Rolling 30d Sharpe",
                      title="<b>Rolling 30-Day Sharpe Ratio</b>",
                      template="plotly_dark",
                      labels={"Rolling 30d Sharpe": "Sharpe Ratio"})
        fig.add_hline(y=0, line_dash="dash", line_color="#E74C3C")
        fig.update_layout(margin=dict(l=0, r=0, t=50, b=0))
        pf_plotly_chart(fig)
        if portfolio_value > 0:
            st.caption(
                f"Returns are value-weighted by current position size (total: € {portfolio_value:,.0f}). "
                "VaR/CVaR EUR figures assume this portfolio size."
            )

    except Exception as e:
        st.info(f"Could not compute risk metrics: {e}")


# ======================================================
# B15. CORRELATION MATRIX
# ======================================================

def render_correlation_matrix(account_ids: tuple = None):
    """Render the Price Correlation Matrix."""
    st.subheader("\U0001f517 Correlation Matrix")
    st.caption(
        "Shows how closely the daily price returns of your holdings move together over the selected period. "
        "Values range from **−1** (perfectly inverse) to **+1** (perfectly in sync), with **0** meaning no linear relationship.\n\n"
        "- **Dark blue (near +1):** the two assets tend to rise and fall together — low diversification benefit.\n"
        "- **Dark red (near −1):** the two assets tend to move in opposite directions — strong diversification benefit.\n"
        "- **White / near 0:** weak or no linear relationship — good for portfolio diversification.\n\n"
        "A well-diversified portfolio should have many near-zero or negative off-diagonal entries. "
        "High positive correlations across all holdings mean the portfolio behaves like a single concentrated bet."
    )

    lookback  = st.slider("Lookback days", min_value=60, max_value=756, value=252, step=20, key="corr_lookback",
                          help="Number of calendar days of price history used to compute correlations. 252 ≈ 1 trading year.")
    df_prices  = get_price_returns(lookback, account_ids)
    df_weights = get_portfolio_weights(account_ids)

    if df_prices is None or df_prices.empty or df_prices.shape[1] < 2:
        st.info("Insufficient price history to compute correlation. Need at least 2 securities with 30+ days of data.")
        return

    try:
        # Reorder columns by position value (largest exposure first) so the
        # n_max slider always keeps the most significant holdings.
        if not df_weights.empty:
            ordered = [t for t in df_weights["ticker"].tolist() if t in df_prices.columns]
            rest    = [c for c in df_prices.columns if c not in ordered]
            df_prices = df_prices[ordered + rest]

        n_max = st.slider(
            "Max holdings to show",
            min_value=2,
            max_value=min(df_prices.shape[1], 30),
            value=min(df_prices.shape[1], 15),
            key="corr_n",
            help="Keeps the top N holdings by position value.",
        )
        df_sub      = df_prices.iloc[:, :n_max]
        daily_rets  = df_sub.pct_change(fill_method=None).dropna()
        corr_matrix = daily_rets.corr()

        # Scale cell size so the matrix stays readable; clamp so it fits most screens
        cell_px    = max(36, min(55, 500 // max(n_max, 1)))
        fig_px     = max(380, n_max * cell_px)
        label_font = max(9, min(13, cell_px - 22))

        fig = go.Figure(data=go.Heatmap(
            z=corr_matrix.values,
            x=corr_matrix.columns.tolist(),
            y=corr_matrix.index.tolist(),
            colorscale="RdBu",
            zmid=0,
            text=np.round(corr_matrix.values, 2),
            texttemplate="%{text}",
            textfont=dict(size=label_font),
            showscale=True,
        ))
        fig.update_layout(
            template="plotly_dark",
            title="<b>Price Return Correlation Matrix</b>",
            height=fig_px,
            margin=dict(l=0, r=0, t=50, b=0),
            xaxis=dict(tickangle=-45, tickfont=dict(size=label_font)),
            yaxis=dict(tickfont=dict(size=label_font)),
        )
        if n_max > 10:
            st.caption(
                f"Tip: {n_max} holdings — pinch to zoom or use the toolbar to pan on mobile."
            )
        pf_plotly_chart(fig)

    except Exception as e:
        st.info(f"Could not compute correlation matrix: {e}")


# ======================================================
# B16. MONTE CARLO
# ======================================================

def _xirr(cashflows: list, dates: list) -> float | None:
    """Compute annualised XIRR given cashflows and matching dates.

    cashflows : list of floats  (negative = money out, positive = money in)
    dates     : list of datetime-like objects
    Returns annualised rate or None if no solution found.

    Uses a pure-Python bisection root-finder so scipy is not required.
    """
    if len(cashflows) < 2:
        return None

    t0 = min(dates)

    def day_frac(d):
        delta = d - t0
        days  = delta.days if hasattr(delta, 'days') else float(delta) / 86400
        return days / 365.25

    def npv(rate):
        return sum(cf / (1.0 + rate) ** day_frac(d) for cf, d in zip(cashflows, dates))

    try:
        lo, hi = -0.9999, 100.0
        f_lo, f_hi = npv(lo), npv(hi)
        if f_lo * f_hi > 0:
            return None          # no sign change → no root in interval
        for _ in range(300):
            mid   = (lo + hi) / 2.0
            f_mid = npv(mid)
            if abs(f_mid) < 1e-12 or (hi - lo) < 1e-12:
                return mid
            if f_lo * f_mid < 0:
                hi, f_hi = mid, f_mid
            else:
                lo, f_lo = mid, f_mid
        return (lo + hi) / 2.0
    except Exception:
        return None


def render_twr_mwr(account_ids: tuple = None):
    """Render Time-Weighted Return (TWR) and Money-Weighted Return (MWR / XIRR)."""
    st.subheader("📐 Time-Weighted & Money-Weighted Return")
    st.caption(
        "Two complementary measures of portfolio performance:\n\n"
        "- **TWR (Time-Weighted Return)**: eliminates the effect of *when* you deposited or withdrew money. "
        "It measures the portfolio manager's performance — directly comparable to an index return.\n"
        "- **MWR (Money-Weighted Return / XIRR)**: reflects *your actual experience* — the return you "
        "personally earned given the size and timing of your deposits and withdrawals. "
        "If you invested heavily before a downturn, MWR will be lower than TWR.\n\n"
        "TWR is computed from daily price-based portfolio returns. "
        "MWR uses all recorded Buy/Sell/Dividend/IntInc cash flows plus the current portfolio value."
    )

    lookback_twr = st.slider(
        "TWR lookback (calendar days)", min_value=30, max_value=3650, value=756, step=92,
        key="twr_lookback",
        help="Number of calendar days of price history to use for TWR. MWR always uses all available history."
    )

    # ── TWR ───────────────────────────────────────────────────────────────────
    df_prices  = get_price_returns(lookback_twr, account_ids)
    df_weights = get_portfolio_weights(account_ids)

    twr = None
    twr_ann = None
    n_days = 0

    if df_prices is not None and not df_prices.empty:
        try:
            dr = df_prices.ffill(limit=5).pct_change(fill_method=None).dropna(how='all')
            if not dr.empty:
                if not df_weights.empty:
                    wmap  = dict(zip(df_weights["ticker"], df_weights["weight"]))
                    avail = [c for c in dr.columns if c in wmap]
                    if avail:
                        w = pd.Series([wmap[t] for t in avail], index=avail)
                        w = w / w.sum()
                        port_ret = dr[avail].fillna(0).dot(w)
                    else:
                        port_ret = dr.mean(axis=1)
                else:
                    port_ret = dr.mean(axis=1)

                n_days   = len(port_ret)
                twr      = float((1 + port_ret).prod() - 1)
                # Annualise: (1 + twr)^(252/n_days) - 1
                twr_ann  = float((1 + twr) ** (252.0 / n_days) - 1) if n_days > 0 else None

                # Cumulative TWR chart
                cum = (1 + port_ret).cumprod()
                df_cum = cum.reset_index()
                df_cum.columns = ['date', 'value']
                df_cum['twr_pct'] = (df_cum['value'] - 1) * 100

        except Exception as e:
            st.warning(f"TWR computation error: {e}")

    # ── MWR / XIRR ───────────────────────────────────────────────────────────
    mwr    = None
    cf_count = 0
    df_cf  = pd.DataFrame()

    try:
        df_cf = get_investment_cashflows(account_ids)
        portfolio_value = get_investable_portfolio_value(account_ids)

        if not df_cf.empty and portfolio_value > 0:
            cf_dates  = list(df_cf['date'])
            cf_values = list(df_cf['cashflow_eur'].astype(float))
            cf_count  = len(cf_dates)

            # Add current portfolio value as final positive cash flow (today)
            cf_dates.append(pd.Timestamp.today())
            cf_values.append(portfolio_value)

            mwr = _xirr(cf_values, cf_dates)
    except Exception as e:
        st.warning(f"MWR computation error: {e}")

    # ── Metrics ───────────────────────────────────────────────────────────────
    m1, m2, m3, m4 = st.columns(4)
    m1.metric(
        f"TWR ({lookback_twr}d window)",
        f"{twr*100:+.2f}%" if twr is not None else "—",
        help="Cumulative time-weighted return over the selected lookback period."
    )
    m2.metric(
        "TWR (Annualised)",
        f"{twr_ann*100:+.2f}%" if twr_ann is not None else "—",
        help="TWR scaled to an annual rate using (1+r)^(252/n_days) − 1."
    )
    m3.metric(
        "MWR / XIRR (All-time)",
        f"{mwr*100:+.2f}%" if mwr is not None else "—",
        help=f"Internal rate of return across all {cf_count} recorded cash flows. "
             "Reflects your personal experience accounting for investment timing."
    )
    m4.metric(
        "Trading Days Used (TWR)",
        f"{n_days}",
        help="Number of daily return observations used to compute TWR."
    )

    # ── Cumulative TWR chart ──────────────────────────────────────────────────
    if twr is not None and 'df_cum' in locals():
        fig = px.line(
            df_cum, x='date', y='twr_pct',
            title="<b>Cumulative Time-Weighted Return (%)</b>",
            template="plotly_dark",
            labels={'twr_pct': 'TWR (%)', 'date': 'Date'},
        )
        fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
        fig.update_layout(margin=dict(l=0, r=0, t=50, b=0))
        pf_plotly_chart(fig)

    # ── Cash flow table ───────────────────────────────────────────────────────
    if not df_cf.empty:
        with st.expander("📋 Cash Flow Detail (MWR inputs)", expanded=False):
            df_cf_disp = df_cf.copy()
            df_cf_disp['date'] = df_cf_disp['date'].dt.strftime("%Y-%m-%d")
            df_cf_disp['cashflow_eur'] = df_cf_disp['cashflow_eur'].astype(float)
            st.dataframe(
                df_cf_disp.style.format({"cashflow_eur": "{:+,.2f} €"})
                    .map(color_negative_red, subset=['cashflow_eur']),
                hide_index=True, width="stretch",
                column_config={
                    "date":         "Date",
                    "action":       "Action",
                    "cashflow_eur": st.column_config.NumberColumn("Cash Flow (€)", format="%+,.2f €"),
                },
            )
            copy_df_button(df_cf_disp, key="dl_mwr_cashflows")

    st.info(
        "**Interpretation guide:** "
        "If TWR > MWR, you tended to invest more capital *before* underperforming periods. "
        "If MWR > TWR, your larger investments coincided with stronger performance — "
        "good market timing added personal value beyond the portfolio's intrinsic return."
    )


def render_monte_carlo(account_ids: tuple = None):
    """Render the Monte Carlo simulation."""
    st.subheader("\U0001f3b2 Monte Carlo Simulation")
    st.caption(
        "Runs thousands of randomised future scenarios to model the range of possible portfolio outcomes. "
        "Returns are calibrated from your actual portfolio's recent history (last 252 trading days), "
        "weighted by current position value, so the simulation reflects your real allocation — not a hypothetical equal-weight mix.\n\n"
        "**How to read the chart:**\n"
        "- The **green line (90th percentile)** represents an optimistic outcome — only 10% of simulations do better.\n"
        "- The **blue line (median / 50th percentile)** is the most likely outcome — half of simulations end above it, half below.\n"
        "- The **red line (10th percentile)** represents a pessimistic outcome — only 10% of simulations do worse.\n\n"
        "The **probability table** below shows the likelihood of ending above various wealth targets. "
        "Note that this model assumes returns are normally distributed and stationary — "
        "real markets exhibit fat tails, regime changes, and sequence-of-returns risk not captured here."
    )

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        years_ahead     = st.slider("Years ahead", min_value=1, max_value=30, value=10, key="mc_years",
                                    help="Investment horizon in years for the simulation.")
    with col2:
        num_sims        = st.slider("Simulations", min_value=100, max_value=2000, value=500, step=100, key="mc_sims",
                                    help="Number of independent random paths to simulate. More simulations give smoother percentile bands but take slightly longer.")
    with col3:
        monthly_contrib = st.number_input("Monthly Contribution (€)", min_value=0.0, step=100.0, value=500.0, key="mc_contrib",
                                          help="Fixed amount added to the portfolio each month throughout the simulation.")
    with col4:
        lookback_mc = st.slider("Calibration lookback (days)", min_value=252, max_value=1260, value=756, step=63,
                                key="mc_lookback",
                                help="Historical price window used to estimate mean return and volatility. Longer = more stable estimate. 252≈1yr, 756≈3yr, 1260≈5yr.")

    _mc_default_portfolio = get_investable_portfolio_value(account_ids)
    # Key includes account_ids so the widget resets when the preset changes,
    # forcing Streamlit to pick up the freshly-computed default value.
    _mc_key_suffix = str(account_ids)
    initial_value = st.number_input(
        "Starting Portfolio Value (€)", min_value=0.0, step=1000.0,
        value=_mc_default_portfolio, key=f"mc_initial_{_mc_key_suffix}",
        help="Pre-filled from your live portfolio value (holdings + pension + investment accounts).",
    )

    df_prices  = get_price_returns(lookback_mc, account_ids)
    df_weights = get_portfolio_weights(account_ids)

    if df_prices is None or df_prices.empty or df_prices.shape[1] < 1:
        st.info("Insufficient price history to run Monte Carlo simulation.")
        return

    try:
        daily_returns = df_prices.pct_change(fill_method=None).dropna()

        # Value-weighted portfolio returns
        if not df_weights.empty:
            wmap  = dict(zip(df_weights["ticker"], df_weights["weight"]))
            avail = [c for c in daily_returns.columns if c in wmap]
            if avail:
                w = pd.Series([wmap[t] for t in avail], index=avail)
                w = w / w.sum()
                port_returns = daily_returns[avail].dot(w)
            else:
                port_returns = daily_returns.mean(axis=1)
        else:
            port_returns = daily_returns.mean(axis=1)

        hist_ann_return = (1 + port_returns.mean()) ** 252 - 1
        hist_ann_vol    = port_returns.std() * np.sqrt(252)

        # Clamp calibrated values to sensible display range before passing to widgets.
        # Extreme values (e.g. -41%) come from stale/bad price data; the user can
        # see the raw figure in the caption and override freely.
        _RETURN_MIN, _RETURN_MAX = -99.0, 100.0
        _VOL_MIN,    _VOL_MAX    =   0.1, 200.0
        _default_ret = max(_RETURN_MIN, min(_RETURN_MAX, round(hist_ann_return * 100, 1)))
        _default_vol = max(_VOL_MIN,    min(_VOL_MAX,    round(hist_ann_vol    * 100, 1)))

        _looks_bad = abs(hist_ann_return) > 0.20   # flag anything beyond ±20% as suspect

        # Show calibrated parameters and allow manual override.
        # Widgets inside a collapsed expander still run and return their values.
        with st.expander(
            "⚙️ Return Assumptions (calibrated from history — click to override)",
            expanded=_looks_bad,   # auto-open when calibration looks unrealistic
        ):
            if _looks_bad:
                st.warning(
                    f"⚠️ Calibrated return is **{hist_ann_return * 100:+.1f}%** — this is likely driven by "
                    "bad or stale price data for one or more securities in the selected accounts. "
                    "Please override with realistic values below (long-run equity average: ~7–10%)."
                )
            else:
                st.caption(
                    f"Calibrated from the last **{lookback_mc} days** of price history: "
                    f"**{hist_ann_return * 100:+.1f}% annual return**, "
                    f"**{hist_ann_vol * 100:.1f}% annual volatility**. "
                    "If this looks wrong, increase the lookback slider or override manually here."
                )
            ov_col1, ov_col2 = st.columns(2)
            with ov_col1:
                override_return = st.number_input(
                    "Expected annual return (%)",
                    min_value=_RETURN_MIN, max_value=_RETURN_MAX,
                    value=_default_ret, step=0.5, key=f"mc_override_ret_{_mc_key_suffix}",
                    help="Override the calibrated value. Long-run equity average is ~7-10% nominal."
                )
            with ov_col2:
                override_vol = st.number_input(
                    "Annual volatility (%)",
                    min_value=_VOL_MIN, max_value=_VOL_MAX,
                    value=_default_vol, step=0.5, key=f"mc_override_vol_{_mc_key_suffix}",
                    help="Override the calibrated volatility. Broad equity index is typically 15-20%."
                )

        ann_return_used = override_return / 100
        ann_vol_used    = override_vol / 100
        mean_daily      = (1 + ann_return_used) ** (1 / 252) - 1
        std_daily       = ann_vol_used / np.sqrt(252)

        # Show active parameters as metrics
        mc1, mc2, mc3 = st.columns(3)
        mc1.metric("Annual Return (used)", f"{ann_return_used * 100:+.1f}%",
                   help="Expected annual return driving the simulation.")
        mc2.metric("Annual Volatility (used)", f"{ann_vol_used * 100:.1f}%",
                   help="Standard deviation of annual returns.")
        mc3.metric("Calibration window", f"{lookback_mc}d  ({lookback_mc // 252:.0f}–{lookback_mc // 252 + 1:.0f} yrs)")

        if std_daily == 0:
            st.info("Portfolio volatility is zero — cannot run simulation.")
            return

        n_steps      = years_ahead * 12
        mean_monthly = (1 + mean_daily) ** 21 - 1
        std_monthly  = std_daily * np.sqrt(21)

        rng         = np.random.default_rng(42)
        sim_returns = rng.normal(mean_monthly, std_monthly, size=(num_sims, n_steps))

        paths       = np.zeros((num_sims, n_steps + 1))
        paths[:, 0] = initial_value
        for t in range(1, n_steps + 1):
            paths[:, t] = paths[:, t - 1] * (1 + sim_returns[:, t - 1]) + monthly_contrib

        p10 = np.percentile(paths, 10, axis=0)
        p50 = np.percentile(paths, 50, axis=0)
        p90 = np.percentile(paths, 90, axis=0)

        time_axis = [i / 12 for i in range(n_steps + 1)]

        fig = go.Figure()
        fig.add_scatter(x=time_axis, y=p90, name="90th Percentile", mode="lines",
                        line=dict(color="#2ECC71", width=2))
        fig.add_scatter(x=time_axis, y=p50, name="Median (50th)", mode="lines",
                        line=dict(color="#3498DB", width=2))
        fig.add_scatter(x=time_axis, y=p10, name="10th Percentile", mode="lines",
                        line=dict(color="#E74C3C", width=2),
                        fill="tonexty", fillcolor="rgba(231,76,60,0.1)")
        fig.update_layout(
            template="plotly_dark",
            title=f"<b>Monte Carlo: {num_sims} Simulations over {years_ahead} Years</b>",
            xaxis_title="Years", yaxis_title="Portfolio Value (€)",
            margin=dict(l=0, r=0, t=50, b=0),
        )
        pf_plotly_chart(fig)

        final_values = paths[:, -1]
        targets      = [50_000, 100_000, 250_000, 500_000, 1_000_000]
        prob_data    = [
            {"Target (€)": f"€ {t:,.0f}", "Probability": f"{(final_values >= t).mean() * 100:.1f}%"}
            for t in targets
        ]
        st.markdown("#### Probability of Reaching Target Amounts")
        df_prob = pd.DataFrame(prob_data)
        st.dataframe(df_prob, hide_index=True, width="stretch")
        copy_df_button(df_prob, key="dl_rpt_monte_prob")

    except Exception as e:
        st.info(f"Monte Carlo simulation error: {e}")
