import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from database.connection import get_db
from database.crud import (
    save_changes, save_changes_no_serial, save_changes_mid,
    delete_historical_prices, insert_prices_from_transactions, normalize_investment_prices,
    apply_stock_split,
    apply_writeoff,
    apply_dividend,
)
from database.queries import (get_price_anomalies, get_split_preview, get_all_accounts_for_nwr,
                               get_corporate_actions, get_watchlist, add_watchlist_item,
                               remove_watchlist_item, get_alerts, save_alert, delete_alert,
                               toggle_alert, get_asset_allocation_data)
from ui.components import copy_df_button
from data.downloaders import (
    download_historical_fx,
    download_historical_prices_from_tradingview,
    download_historical_prices_from_yahoo,
    download_bond_prices_from_solidus,
    download_securities_info_from_yahoo,
    download_securities_info_from_tradingview,
    download_dividend_history,
)


# ── Cached reference-data loaders ─────────────────────────────────────────────

@st.cache_data(ttl=600)
def _md_load_currencies():
    with get_db() as conn:
        return pd.read_sql(
            "SELECT Currencies_Id, Currencies_ShortName FROM Currencies ORDER BY Currencies_ShortName ASC", conn)

@st.cache_data(ttl=600)
def _md_load_securities_list():
    with get_db() as conn:
        return pd.read_sql(
            "SELECT Securities_Id, Securities_Name FROM Securities ORDER BY Securities_Name ASC", conn)

@st.cache_data(ttl=3600)
def _md_load_credit_ratings():
    with get_db() as conn:
        return pd.read_sql(
            "SELECT Moodys, S_P, Fitch FROM Credit_Ratings_LT ORDER BY Credit_Ratings_LT_Id ASC", conn)

@st.cache_data(ttl=120)
def _md_load_securities_with_price_stats():
    """Securities with price count and latest price date — for the security selector dropdown."""
    with get_db() as conn:
        return pd.read_sql("""
            SELECT s.Securities_Id, s.Securities_Name,
                   s.Yahoo_Ticker, s.TV_Symbol, s.TV_Exchange, s.ISIN,
                   COALESCE(hp.NoOfRecords, 0)          AS NoOfRecords,
                   COALESCE(hp.MaxDate, '1900-01-01')   AS MaxDate
            FROM Securities s
            LEFT JOIN (
                SELECT Securities_Id,
                       COUNT(*)   AS NoOfRecords,
                       MAX(Date)  AS MaxDate
                FROM   Historical_Prices
                GROUP  BY Securities_Id
            ) hp ON hp.Securities_Id = s.Securities_Id
            ORDER BY s.Securities_Name ASC
        """, conn)


def render_market_data():
    """Render the Market Data page.

    Tab 1 — Currencies: currency master data editor + historical FX rates.
    Tab 2 — Securities: security master data editor + per-security price history,
             price anomaly detection, and dummy-price normalization.
    """
    st.title("Market Data")
    t_curr, t_sec, t_watch, t_alerts = st.tabs(["💱 Currencies", "📈 Securities", "👀 Watchlist", "🔔 Alerts"])

    # ── Shared lookups (all cached) ───────────────────────────────────────────
    df_curr_list = _md_load_currencies()
    df_sec_list  = _md_load_securities_list()
    df_ratings   = _md_load_credit_ratings()

    curr_options   = df_curr_list.set_index('currencies_id')['currencies_shortname'].to_dict()
    sec_options    = df_sec_list.set_index('securities_id')['securities_name'].to_dict()
    moodys_options = dict(zip(df_ratings['moodys'], df_ratings['moodys']))
    s_p_options    = dict(zip(df_ratings['s_p'],    df_ratings['s_p']))
    fitch_options  = dict(zip(df_ratings['fitch'],  df_ratings['fitch']))

    period_options = {
        "1 Day": "1d", "5 Days": "5d", "1 Month": "1mo", "3 Months": "3mo",
        "6 Months": "6mo", "1 Year": "1y", "3 Years": "3y", "5 Years": "5y",
        "10 Years": "10y", "15 Years": "15y", "20 Years": "20y",
        "25 Years": "25y", "30 Years": "30y",
    }

    # =========================================================================
    # TAB 1 — CURRENCIES
    # =========================================================================
    with t_curr:

        # ── Currency master data editor ───────────────────────────────────────
        st.subheader("🪙 Currency Master Data")
        with get_db() as conn:
            df_curr = pd.read_sql("SELECT * FROM Currencies ORDER BY Currencies_ShortName ASC", conn)
        edited_curr = st.data_editor(
            df_curr,
            num_rows="dynamic",
            key="mkt_curr",
            column_config={
                "currencies_id":        None,
                "currencies_shortname": st.column_config.TextColumn("Currency ISO Code", width="small"),
                "currencies_name":      st.column_config.TextColumn("Currency Name",     width="large"),
                "embedding":            None,
            },
        )
        if not edited_curr.equals(df_curr):
            save_changes(df_curr, edited_curr, "Currencies", "currencies_id")
            _md_load_currencies.clear()

        st.divider()

        # ── FX Rates ──────────────────────────────────────────────────────────
        st.subheader("💱 Historical FX Rates")
        with get_db() as conn:
            df_fx = pd.read_sql(
                "SELECT * FROM Historical_FX ORDER BY Date DESC, Currencies_Id_1 ASC", conn)
        edited_hfx = st.data_editor(
            df_fx,
            num_rows="dynamic",
            key="mkt_hfx",
            column_config={
                "currencies_id_1": st.column_config.SelectboxColumn(
                    "Base Currency",
                    options=list(curr_options.keys()),
                    format_func=lambda x: curr_options.get(x, "Unknown"),
                ),
                "currencies_id_2": st.column_config.SelectboxColumn(
                    "Target Currency",
                    options=list(curr_options.keys()),
                    format_func=lambda x: curr_options.get(x, "Unknown"),
                ),
            },
        )
        save_changes_no_serial(df_fx, edited_hfx, "Historical_FX", "date")
        copy_df_button(df_fx, key="mkt_dl_fx")

        # _sel_ccy_id / _sel_ccy_name track the currency of the pair shown in the
        # chart so the single-pair download button can reference it.
        _sel_ccy_id   = None
        _sel_ccy_name = None

        if not df_fx.empty:
            st.subheader("📈 Exchange Rate Chart")
            df_plot = df_fx.copy()
            df_plot['Pair'] = df_plot.apply(
                lambda row: (
                    f"{curr_options.get(row['currencies_id_1'], '??')}"
                    f"/{curr_options.get(row['currencies_id_2'], '??')}"
                ),
                axis=1,
            )
            available_pairs = df_plot['Pair'].unique()
            selected_pair   = st.selectbox("Select pair to display:", available_pairs, key="mkt_fx_pair_select")
            chart_data      = df_plot[df_plot['Pair'] == selected_pair].sort_values('date')
            if not chart_data.empty:
                st.line_chart(
                    data=chart_data, x='date', y='fx_rate',
                    x_label="Date", y_label="Rate", width='stretch',
                )

            # Capture the base currency of the selected pair for the targeted download button
            _pair_rows = df_plot[df_plot['Pair'] == selected_pair]
            if not _pair_rows.empty:
                _sel_ccy_id   = int(_pair_rows['currencies_id_1'].iloc[0])
                _sel_ccy_name = selected_pair.split('/')[0]   # e.g. "USD"

        st.subheader("🔄 Update Exchange Rates")
        col1, col2 = st.columns([2, 1])
        with col1:
            selected_label_fx = st.selectbox(
                "Select time period:", list(period_options.keys()), index=1, key="mkt_fx_period_select")
            ts_period_fx = period_options[selected_label_fx]
        with col2:
            if st.button("🚀 Download All Rates", key="mkt_download_all_fx_btn", width="stretch"):
                with st.spinner("Downloading rates for all currencies…"):
                    download_historical_fx(ts_period_fx)
                    st.balloons()
                    st.rerun()
            if _sel_ccy_id is not None:
                if st.button(
                        f"🚀 Download Rates for {_sel_ccy_name}",
                        key="mkt_download_one_fx_btn", width="stretch"):
                    with st.spinner(f"Downloading rates for {_sel_ccy_name}…"):
                        download_historical_fx(ts_period_fx, currencies_id=_sel_ccy_id)
                        st.balloons()
                        st.rerun()

    # =========================================================================
    # TAB 2 — SECURITIES
    # =========================================================================
    with t_sec:

        # ── Securities master data editor ─────────────────────────────────────
        st.subheader("📋 Securities Master Data")
        with get_db() as conn:
            df_sec = pd.read_sql("""
                SELECT s.Securities_Id, s.Ticker, s.Securities_Name, s.Securities_Type,
                       s.Currencies_Id, s.Sector, s.Industry, s.Analyst_Rating,
                       s.Analyst_Target_Price, s.Is_Active,
                       COALESCE(s.Is_Tax_Exempt, FALSE) AS Is_Tax_Exempt,
                       s.Yahoo_Ticker, s.TV_Symbol, s.TV_Exchange, s.ISIN,
                       s.Maturity_Date, s.Coupon_Rate, s.Face_Value, s.Coupon_Frequency,
                       s.Dividend_Yield, s.Dividend_Frequency, s.Ex_Dividend_Date,
                       s.embedding,
                       COALESCE(ic.cnt, 0) AS investment_count
                FROM Securities s
                LEFT JOIN (
                    SELECT Securities_Id, COUNT(*) AS cnt
                    FROM   Investments
                    GROUP  BY Securities_Id
                ) ic ON ic.Securities_Id = s.Securities_Id
                ORDER BY s.Securities_Name
            """, conn)

        _sec_sort_labels = {
            "ticker":             "Ticker",
            "securities_name":    "Security Name",
            "securities_type":    "Type",
            "investment_count":   "Investment Count",
            "dividend_yield":     "Div. Yield (%)",
            "dividend_frequency": "Div. Frequency",
            "ex_dividend_date":   "Ex-Div Date",
        }
        _c1, _c2 = st.columns([2, 1])
        with _c1:
            _sec_sort_col = st.selectbox(
                "Sort by", options=list(_sec_sort_labels.keys()),
                format_func=lambda x: _sec_sort_labels[x], index=1, key="mkt_sec_sort_col")
        with _c2:
            _sec_sort_asc = st.radio(
                "Direction", options=["Ascending", "Descending"],
                horizontal=True, key="mkt_sec_sort_dir") == "Ascending"

        df_sec = df_sec.sort_values(_sec_sort_col, ascending=_sec_sort_asc).reset_index(drop=True)

        edited_sec = st.data_editor(
            df_sec,
            num_rows="dynamic",
            key="mkt_set_sec",
            column_config={
                "securities_id":        None,
                "ticker":               st.column_config.TextColumn("Ticker Symbol",  width="medium", pinned=True),
                "securities_name":      st.column_config.TextColumn("Security Name",  width="medium", pinned=True),
                "securities_type":      st.column_config.SelectboxColumn("Type",
                    options=['Stock','ETF','Bond','CD','CFD','Closed-End Fund',
                             'Emp. Stock Opt.','FX Spot','Market Index','Mutual Fund',
                             'Crypto','Option','Commodity','PF_Unit','Other'],
                    width="small"),
                "currencies_id":        st.column_config.SelectboxColumn("Currency",
                    options=list(curr_options.keys()),
                    format_func=lambda x: curr_options.get(x, "Unknown"), width="small"),
                "sector":               st.column_config.TextColumn("Sector",           width="small"),
                "industry":             st.column_config.TextColumn("Industry",         width="small"),
                "analyst_rating":       st.column_config.TextColumn("Rating",           width="small"),
                "analyst_target_price": st.column_config.NumberColumn("Target Price",   width="auto", format="%,.2f"),
                "is_active":            st.column_config.CheckboxColumn("Is Active",    width="small"),
                "is_tax_exempt":        st.column_config.CheckboxColumn("Tax Exempt",   width="small",
                    help="Income (dividends, interest) from this security is exempt from "
                         "income tax — e.g. Hellenic T-Bills purchased at the primary market."),
                "yahoo_ticker":         st.column_config.TextColumn("Yahoo Ticker",     width="small"),
                "tv_symbol":            st.column_config.TextColumn("TV Symbol",        width="small"),
                "tv_exchange":          st.column_config.TextColumn("TV Exchange",      width="small"),
                "isin":                 st.column_config.TextColumn("ISIN",             width="small"),
                "maturity_date":        st.column_config.DateColumn("Maturity Date",    width="small"),
                "coupon_rate":          st.column_config.NumberColumn("Coupon Rate (%)",width="small", format="%.4f"),
                "face_value":           st.column_config.NumberColumn("Face Value",     width="small", format="%,.2f"),
                "coupon_frequency":     st.column_config.SelectboxColumn("Coupon Frequency",
                    options=["Annual","Semi-Annual","Quarterly","Monthly","At Maturity"], width="small"),
                "dividend_yield":       st.column_config.NumberColumn("Div. Yield (%)",   width="small", format="%.4f", disabled=True),
                "dividend_frequency":   st.column_config.TextColumn("Div. Frequency",     width="small", disabled=True),
                "ex_dividend_date":     st.column_config.DateColumn("Ex-Div Date",        width="small", disabled=True),
                "investment_count":     st.column_config.NumberColumn("Investment Count", width="small", disabled=True),
                "embedding":            None,
            },
        )
        _sec_computed   = ["investment_count", "dividend_yield", "dividend_frequency", "ex_dividend_date"]
        df_sec_save     = df_sec.drop(columns=[c for c in _sec_computed if c in df_sec.columns])
        edited_sec_save = edited_sec.drop(columns=[c for c in _sec_computed if c in edited_sec.columns])
        if not edited_sec_save.equals(df_sec_save):
            save_changes(df_sec_save, edited_sec_save, "Securities", "securities_id")
            _md_load_securities_list.clear()
            _md_load_securities_with_price_stats.clear()

        if st.button("🚀 Update Securities Information from Yahoo", key="mkt_download_sec_info", width="stretch"):
            with st.spinner("Fetching sector, industry, analyst rating, target price & dividend summary from Yahoo…"):
                download_securities_info_from_yahoo()
            _md_load_securities_list.clear()
            st.rerun()

        if st.button("📅 Download Dividend History from Yahoo", key="mkt_download_div_history", width="stretch"):
            with st.spinner("Fetching full dividend history & inferring frequency from Yahoo…"):
                download_dividend_history()
            _md_load_securities_list.clear()
            st.rerun()

        if st.button("🚀 Update Securities Information from TradingView", key="mkt_download_tv_info", width="stretch"):
            with st.spinner("Fetching sector, industry, rating & target price from TradingView…"):
                download_securities_info_from_tradingview(target_sec_id=None, overwrite=False)
            _md_load_securities_list.clear()
            st.rerun()

        st.divider()

        # ── Per-security selector ─────────────────────────────────────────────
        st.subheader("📊 Security Details")
        df_inv_secs = _md_load_securities_with_price_stats()

        if df_inv_secs.empty:
            st.warning("⚠️ No Securities found. Add one in the master data editor above.")
        else:
            records = df_inv_secs.to_dict('records')
            default_sec_idx = 0
            if "mkt_selected_sec_id" in st.session_state:
                for i, rec in enumerate(records):
                    if rec['securities_id'] == st.session_state.mkt_selected_sec_id:
                        default_sec_idx = i
                        break

            selected_inv_sec = st.selectbox(
                "Select Security:",
                records,
                index=default_sec_idx,
                format_func=lambda x: (
                    f"{x['securities_name']} "
                    f"({x['noofrecords']:,.0f} prices · last: {x['maxdate']})"
                ),
                key="mkt_security_select",
            )
            st.session_state.mkt_selected_sec_id = selected_inv_sec['securities_id']
            inv_sec_id = selected_inv_sec['securities_id']

            # ── Inner sub-tabs ────────────────────────────────────────────────
            st_prices, st_inv_txs, st_anomalies, st_divs, st_corp = st.tabs([
                "📈 Prices",
                "🧾 Investment Transactions",
                "🔍 Price Anomalies",
                "💰 Dividends",
                "🏢 Corporate Actions",
            ])

            # ─────────────────────────────────────────────────────────────────
            # SUB-TAB: Prices
            # ─────────────────────────────────────────────────────────────────
            with st_prices:
                with get_db() as conn:
                    df_hpr_tx = pd.read_sql(
                        "SELECT Securities_Id, Date, Close, High, Low, Volume, "
                        "       Source, Downloaded_At, embedding "
                        "FROM Historical_Prices "
                        f"WHERE Securities_Id = {inv_sec_id} ORDER BY Date DESC",
                        conn,
                    )
                edited_hpr_tx = st.data_editor(
                    df_hpr_tx,
                    num_rows="dynamic",
                    key=f"mkt_hpr_editor_{inv_sec_id}",
                    width="stretch",
                    column_config={
                        'securities_id':  None,
                        'date':           st.column_config.DateColumn('Date', format="DD/MM/YYYY"),
                        'close':          st.column_config.NumberColumn('Close',  format="%,.8f"),
                        'high':           st.column_config.NumberColumn('High',   format="%,.8f"),
                        'low':            st.column_config.NumberColumn('Low',    format="%,.8f"),
                        'volume':         st.column_config.NumberColumn('Volume', format="%,.0f"),
                        'source':         st.column_config.TextColumn('Source'),
                        'downloaded_at':  st.column_config.DatetimeColumn(
                                              'Downloaded At',
                                              format='YYYY-MM-DD HH:mm',
                                          ),
                        'embedding':      None,
                    },
                )
                save_changes_mid(
                    edited_hpr_tx, "Historical_Prices",
                    id_cols=["securities_id", "date"],
                    filter_col="securities_id", filter_val=inv_sec_id,
                )
                copy_df_button(df_hpr_tx, key=f"mkt_dl_prices_{inv_sec_id}")

                if not df_hpr_tx.empty:
                    st.subheader("📈 Price Chart")
                    with get_db() as conn:
                        df_tx_marks = pd.read_sql(
                            "SELECT Date AS Transaction_Date, Action, Quantity "
                            "FROM Investments "
                            f"WHERE Securities_Id = {inv_sec_id} AND Action IN ('Buy','Sell')",
                            conn,
                        )
                    df_tx_marks['transaction_date'] = pd.to_datetime(df_tx_marks['transaction_date'])

                    c1, c2 = st.columns([1, 1])
                    with c1:
                        chart_view_options = {
                            "1 Week": 7, "1 Month": 30, "3 Months": 90, "6 Months": 180,
                            "YTD": "YTD", "1 Year": 365, "3 Years": 1095, "5 Years": 1825,
                            "All Time": None,
                        }
                        selected_view = st.selectbox(
                            "Chart Range:", list(chart_view_options.keys()),
                            index=8, key=f"mkt_chart_range_{inv_sec_id}",
                        )
                    with c2:
                        window_size = st.slider(
                            "Moving Average (MA) Days:", 2, 30, 5,
                            key=f"mkt_ma_{inv_sec_id}",
                        )

                    chart_data = df_hpr_tx.sort_values('date').copy()
                    chart_data['date'] = pd.to_datetime(chart_data['date'])
                    if chart_view_options[selected_view] is not None:
                        max_date = chart_data['date'].max()
                        if chart_view_options[selected_view] == "YTD":
                            start_date = pd.Timestamp(year=max_date.year, month=1, day=1)
                        else:
                            start_date = max_date - pd.Timedelta(days=chart_view_options[selected_view])
                        chart_data = chart_data[chart_data['date'] >= start_date]

                    chart_data = chart_data.merge(
                        df_tx_marks, left_on='date', right_on='transaction_date', how='left')
                    chart_data['MA'] = chart_data['close'].rolling(window=window_size).mean()

                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=chart_data['date'], y=chart_data['close'],
                        mode='lines', name='Price', line=dict(color='blue', width=2),
                    ))
                    fig.add_trace(go.Scatter(
                        x=chart_data['date'], y=chart_data['MA'],
                        mode='lines', name=f'MA ({window_size}d)',
                        line=dict(color='red', width=1.5, dash='dot'),
                    ))
                    for t_type, t_color, t_symbol in [
                        ('Buy',  'green',  'triangle-up'),
                        ('Sell', 'orange', 'triangle-down'),
                    ]:
                        subset = chart_data[chart_data['action'] == t_type]
                        if not subset.empty:
                            fig.add_trace(go.Scatter(
                                x=subset['date'], y=subset['close'],
                                mode='markers', name=t_type,
                                marker=dict(color=t_color, size=12, symbol=t_symbol,
                                            line=dict(width=1, color='black')),
                                hovertemplate=(
                                    "Date: %{x}<br>Price: %{y}<br>"
                                    f"Action: {t_type}<br>Qty: %{{customdata}}"
                                ),
                                customdata=subset['quantity'],
                            ))
                    fig.update_layout(
                        margin=dict(l=0, r=0, t=0, b=0),
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                        hovermode="x unified", template="plotly_white", height=450,
                    )
                    st.plotly_chart(fig, width='stretch')

                st.subheader("🔄 Update Prices")
                col1, col2 = st.columns([2, 1])
                with col1:
                    period_list = list(period_options.keys())
                    default_period_idx = 1
                    if ("mkt_last_ts_period" in st.session_state
                            and st.session_state.mkt_last_ts_period in period_list):
                        default_period_idx = period_list.index(st.session_state.mkt_last_ts_period)
                    selected_label_price = st.selectbox(
                        "Select download period:", period_list,
                        index=default_period_idx, key="mkt_price_period_select",
                    )
                    st.session_state.mkt_last_ts_period = selected_label_price
                    ts_period_price = period_options[selected_label_price]
                with col2:
                    # pd.notna guards against NULL → NaN coming from the DB;
                    # bool(NaN) is True in Python, which would be a false positive.
                    _yh        = selected_inv_sec.get('yahoo_ticker')
                    _tv        = selected_inv_sec.get('tv_symbol')
                    _has_yahoo = bool(pd.notna(_yh) and _yh)
                    _has_tv    = bool(pd.notna(_tv) and _tv)

                    if st.button("🚀 Download All from Yahoo", key="mkt_dl_all_yahoo", width="stretch"):
                        download_historical_prices_from_yahoo(ts_period_price)
                        _md_load_securities_with_price_stats.clear()
                        st.rerun()
                    if _has_yahoo:
                        if st.button(
                                f"🚀 Update {selected_inv_sec['securities_name']} from Yahoo",
                                key="mkt_dl_one_yahoo", width="stretch"):
                            download_historical_prices_from_yahoo(ts_period_price, inv_sec_id)
                            _md_load_securities_with_price_stats.clear()
                            st.rerun()
                    else:
                        st.caption(f"ℹ️ No Yahoo ticker defined for {selected_inv_sec['securities_name']}")

                    if st.button("🚀 Download All from TradingView", key="mkt_dl_all_tv", width="stretch"):
                        download_historical_prices_from_tradingview(ts_period_price)
                        _md_load_securities_with_price_stats.clear()
                        st.rerun()
                    if _has_tv:
                        if st.button(
                                f"🚀 Update {selected_inv_sec['securities_name']} from TradingView",
                                key="mkt_dl_one_tv", width="stretch"):
                            download_historical_prices_from_tradingview(ts_period_price, inv_sec_id)
                            _md_load_securities_with_price_stats.clear()
                            st.rerun()
                    else:
                        st.caption(f"ℹ️ No TradingView symbol defined for {selected_inv_sec['securities_name']}")

                    if st.button("🚀 Download Greek Bond Prices from Solidus", key="mkt_dl_solidus", width="stretch"):
                        download_bond_prices_from_solidus()
                        _md_load_securities_with_price_stats.clear()
                        st.rerun()

                # ── Import from file ──────────────────────────────────────────
                if st.session_state.get("_mkt_import_last_sec_id") != inv_sec_id:
                    st.session_state["_mkt_import_last_sec_id"] = inv_sec_id
                    st.session_state["_mkt_file_upload_counter"] = (
                        st.session_state.get("_mkt_file_upload_counter", 0) + 1)
                    st.session_state["mkt_import_sec_select"] = inv_sec_id

                with st.expander("📂 Import Prices from File", expanded=False):
                    st.markdown(
                        "Upload a tab-separated **`.txt` / `.csv` / `.tsv`** file with historical "
                        "prices. The file may contain metadata lines above the header — the importer "
                        "will find the `Date` header row automatically."
                    )
                    _import_sec_keys = list(sec_options.keys())
                    _import_default_idx = (
                        _import_sec_keys.index(inv_sec_id) if inv_sec_id in _import_sec_keys else 0)
                    import_sec_id = st.selectbox(
                        "Target Security:", _import_sec_keys,
                        format_func=lambda x: sec_options.get(x, str(x)),
                        index=_import_default_idx, key="mkt_import_sec_select",
                    )
                    _upload_key   = f"mkt_price_uploader_{st.session_state.get('_mkt_file_upload_counter', 0)}"
                    uploaded_file = st.file_uploader("Choose file:", type=["txt","csv","tsv"], key=_upload_key)
                    conflict_mode = st.radio(
                        "If a date already exists in the database:",
                        ["Skip (keep existing)", "Overwrite (replace existing)"],
                        horizontal=True, key="mkt_import_conflict_mode",
                    )
                    if uploaded_file is not None:
                        try:
                            import io as _io
                            raw   = uploaded_file.read().decode("utf-8", errors="replace")
                            lines = raw.splitlines()
                            header_idx = None
                            for _i, _line in enumerate(lines):
                                if _line.strip().lower().startswith("date"):
                                    header_idx = _i
                                    break
                            if header_idx is None:
                                st.error("❌ Could not find a 'Date' header row in the file.")
                            else:
                                data_text  = "\n".join(lines[header_idx:])
                                df_import  = pd.read_csv(
                                    _io.StringIO(data_text), sep="\t", dayfirst=True,
                                    parse_dates=["Date"])
                                df_import.columns = df_import.columns.str.strip().str.lower()
                                if "price" in df_import.columns and "close" not in df_import.columns:
                                    df_import.rename(columns={"price": "close"}, inplace=True)
                                df_import["close"] = pd.to_numeric(df_import["close"], errors="coerce")
                                df_import = df_import[
                                    df_import["close"].notna() & (df_import["close"] != 0)].copy()
                                for _col in ("high", "low", "volume"):
                                    if _col in df_import.columns:
                                        df_import[_col] = pd.to_numeric(df_import[_col], errors="coerce")
                                        df_import[_col] = df_import[_col].where(
                                            df_import[_col] != 0, other=None)
                                df_import["date"] = pd.to_datetime(df_import["date"]).dt.date
                                _keep = [c for c in ("date","close","high","low","volume")
                                         if c in df_import.columns]
                                df_import = df_import[_keep].dropna(
                                    subset=["date","close"]).reset_index(drop=True)

                                st.write(f"**Preview** — {len(df_import):,} rows parsed (showing first 20):")
                                st.dataframe(df_import.head(20), width='stretch')
                                copy_df_button(df_import, key="mkt_dl_import_preview")

                                if st.button("⬆️ Import into Database", key="mkt_do_import", type="primary"):
                                    from database.connection import get_connection as _get_conn
                                    _conn = _get_conn()
                                    _cur  = _conn.cursor()
                                    _inserted = _skipped = _overwritten = 0
                                    _has_high   = "high"   in df_import.columns
                                    _has_low    = "low"    in df_import.columns
                                    _has_volume = "volume" in df_import.columns

                                    def _db_val(v):
                                        try:
                                            import math
                                            if math.isnan(float(v)): return None
                                        except (TypeError, ValueError):
                                            pass
                                        return None if v is None else v

                                    for _, _row in df_import.iterrows():
                                        _vals = (
                                            import_sec_id, _row["date"],
                                            _db_val(_row["close"]),
                                            _db_val(_row["high"])   if _has_high   else None,
                                            _db_val(_row["low"])    if _has_low    else None,
                                            _db_val(_row["volume"]) if _has_volume else None,
                                        )
                                        if conflict_mode.startswith("Skip"):
                                            _cur.execute(
                                                "INSERT INTO Historical_Prices"
                                                " (Securities_Id, Date, Close, High, Low, Volume, Source, Downloaded_At)"
                                                " VALUES (%s,%s,%s,%s,%s,%s,'Manual',NOW())"
                                                " ON CONFLICT (Securities_Id, Date) DO NOTHING",
                                                _vals)
                                            if _cur.rowcount == 1: _inserted += 1
                                            else:                  _skipped  += 1
                                        else:
                                            _cur.execute(
                                                "INSERT INTO Historical_Prices"
                                                " (Securities_Id, Date, Close, High, Low, Volume, Source, Downloaded_At)"
                                                " VALUES (%s,%s,%s,%s,%s,%s,'Manual',NOW())"
                                                " ON CONFLICT (Securities_Id, Date) DO UPDATE"
                                                " SET Close=EXCLUDED.Close, High=EXCLUDED.High,"
                                                "     Low=EXCLUDED.Low, Volume=EXCLUDED.Volume,"
                                                "     Source=EXCLUDED.Source, Downloaded_At=EXCLUDED.Downloaded_At",
                                                _vals)
                                            _overwritten += 1

                                    _conn.commit(); _cur.close(); _conn.close()
                                    _parts = []
                                    if conflict_mode.startswith("Skip"):
                                        _parts.append(f"{_inserted:,} inserted")
                                        if _skipped:
                                            _parts.append(f"{_skipped:,} skipped (already exist)")
                                    else:
                                        _parts.append(
                                            f"{_overwritten:,} rows written (inserted or overwritten)")
                                    st.success(f"✅ Import complete — {', '.join(_parts)}.")
                                    _md_load_securities_with_price_stats.clear()
                                    st.rerun()
                        except Exception as _exc:
                            st.error(f"❌ Error parsing file: {_exc}")

            # ─────────────────────────────────────────────────────────────────
            # SUB-TAB: Investment Transactions
            # ─────────────────────────────────────────────────────────────────
            with st_inv_txs:
                with get_db() as _conn_det:
                    df_inv_det = pd.read_sql("""
                        SELECT
                            a.accounts_name                        AS "Account",
                            i.date                                 AS "Date",
                            i.action                               AS "Action",
                            COALESCE(i.quantity, 0)                AS "Quantity",
                            COALESCE(i.price_per_share, 0)         AS "Price/Share",
                            COALESCE(i.commission, 0)              AS "Commission",
                            COALESCE(i.total_amount_seccur, 0)     AS "Total (Sec. Cur.)",
                            COALESCE(i.total_amount_acccur, 0)     AS "Total (Acc. Cur.)",
                            i.description                          AS "Description"
                        FROM Investments i
                        JOIN Accounts a ON a.accounts_id = i.accounts_id
                        WHERE i.securities_id = %(sid)s
                        ORDER BY i.date DESC, i.investments_id DESC
                    """, _conn_det, params={"sid": inv_sec_id})

                    df_price = pd.read_sql("""
                        SELECT close AS current_price, date AS price_date
                        FROM Historical_Prices
                        WHERE securities_id = %(sid)s ORDER BY date DESC LIMIT 1
                    """, _conn_det, params={"sid": inv_sec_id})

                    df_hold = pd.read_sql("""
                        SELECT
                            a.accounts_name AS "Account",
                            SUM(CASE
                                WHEN i.action IN ('Buy','ShrIn','Reinvest','Vest','Grant','Exercise')
                                     THEN  COALESCE(i.quantity, 0)
                                WHEN i.action IN ('Sell','ShrOut','Expire')
                                     THEN -COALESCE(i.quantity, 0)
                                ELSE 0 END)                                           AS "Qty Held",
                            SUM(CASE
                                WHEN i.action IN ('Buy','ShrIn','Reinvest','Vest','Grant','Exercise')
                                     THEN  COALESCE(i.total_amount_seccur, i.total_amount_acccur, 0)
                                WHEN i.action IN ('Sell','ShrOut','Expire')
                                     THEN -COALESCE(i.total_amount_seccur, i.total_amount_acccur, 0)
                                ELSE 0 END)                                           AS "Cost Basis"
                        FROM Investments i
                        JOIN Accounts a ON a.accounts_id = i.accounts_id
                        WHERE i.securities_id = %(sid)s
                        GROUP BY a.accounts_id, a.accounts_name ORDER BY a.accounts_name
                    """, _conn_det, params={"sid": inv_sec_id})

                _cur_price  = (
                    float(df_price["current_price"].iloc[0])
                    if not df_price.empty and df_price["current_price"].iloc[0] is not None
                    else None)
                _price_date = str(df_price["price_date"].iloc[0]) if not df_price.empty else "N/A"
                _total_qty  = float(df_hold["Qty Held"].sum())   if not df_hold.empty else 0.0
                _total_cost = float(df_hold["Cost Basis"].sum()) if not df_hold.empty else 0.0

                _m1, _m2, _m3, _m4 = st.columns(4)
                with _m1: st.metric("Transactions",  f"{len(df_inv_det):,}")
                with _m2: st.metric("Total Qty Held", f"{_total_qty:,.4f}")
                with _m3: st.metric(f"Price ({_price_date})", f"{_cur_price:,.4f}" if _cur_price else "N/A")
                with _m4:
                    if _cur_price and _total_qty:
                        _cur_val    = _total_qty * _cur_price
                        _unrealised = _cur_val - _total_cost
                        st.metric("Est. Current Value", f"{_cur_val:,.2f}",
                                  delta=f"{_unrealised:+,.2f} P&L")
                    else:
                        st.metric("Est. Current Value", "N/A")

                if not df_hold.empty:
                    st.markdown("**Holdings by Account**")
                    _df_hold_disp = df_hold.copy()
                    if _cur_price:
                        _df_hold_disp["Cur. Value"]     = _df_hold_disp["Qty Held"] * _cur_price
                        _df_hold_disp["Unrealised P&L"] = _df_hold_disp["Cur. Value"] - _df_hold_disp["Cost Basis"]
                    st.dataframe(_df_hold_disp, width="stretch", hide_index=True,
                        column_config={
                            "Qty Held":       st.column_config.NumberColumn(format="%,.4f"),
                            "Cost Basis":     st.column_config.NumberColumn(format="%,.2f", help="Cost basis in the security's trading currency (same as the price)"),
                            "Cur. Value":     st.column_config.NumberColumn(format="%,.2f"),
                            "Unrealised P&L": st.column_config.NumberColumn(format="%,.2f", help="Unrealised P&L in the security's trading currency"),
                        })
                    copy_df_button(_df_hold_disp, key=f"mkt_dl_holdings_{inv_sec_id}")

                st.markdown(f"**All Transactions ({len(df_inv_det):,})**")
                if not df_inv_det.empty:
                    st.dataframe(df_inv_det, width="stretch", hide_index=True,
                        column_config={
                            "Date":              st.column_config.DateColumn(format="DD/MM/YYYY"),
                            "Quantity":          st.column_config.NumberColumn(format="%,.4f"),
                            "Price/Share":       st.column_config.NumberColumn(format="%,.4f"),
                            "Commission":        st.column_config.NumberColumn(format="%,.4f"),
                            "Total (Sec. Cur.)": st.column_config.NumberColumn(format="%,.2f", help="Total amount in the security's trading currency"),
                            "Total (Acc. Cur.)": st.column_config.NumberColumn(format="%,.2f", help="Total amount in the account's base currency"),
                        })
                    copy_df_button(df_inv_det, key=f"mkt_dl_inv_txns_{inv_sec_id}")
                else:
                    st.info("No investment transactions recorded for this security.")

            # ─────────────────────────────────────────────────────────────────
            # SUB-TAB: Price Anomalies (scoped to the selected security)
            # ─────────────────────────────────────────────────────────────────
            with st_anomalies:
                st.caption(
                    "Flags prices that changed by more than the chosen threshold vs the previous "
                    "or next trading day for **this security**. "
                    "The nearest buy/sell transaction is shown for context."
                )
                threshold = st.slider(
                    "Flag when move exceeds (%):",
                    min_value=10, max_value=1000, value=100, step=10,
                    key=f"mkt_pq_threshold_{inv_sec_id}",
                    help="100% = flag any price that is more than 2× or less than ½ of its neighbour",
                )

                with st.spinner("Scanning price history…"):
                    df_anomalies = get_price_anomalies(float(threshold), securities_ids=(inv_sec_id,))

                if df_anomalies.empty:
                    st.success(f"No prices flagged at the {threshold}% threshold for this security.")
                else:
                    st.warning(f"{len(df_anomalies):,} suspicious price record(s).")
                    df_anomalies = df_anomalies.copy()
                    df_anomalies['date']    = pd.to_datetime(df_anomalies['date']).dt.date
                    df_anomalies['tx_date'] = pd.to_datetime(df_anomalies['tx_date'], errors='coerce').dt.date
                    df_anomalies.insert(0, 'Delete', False)

                    edited_anom = st.data_editor(
                        df_anomalies,
                        column_config={
                            'Delete':        st.column_config.CheckboxColumn('🗑 Delete', default=False, pinned=True),
                            'securities_id': None,
                            'security_name': st.column_config.TextColumn('Security',         pinned=True),
                            'date':          st.column_config.DateColumn('Date'),
                            'price':         st.column_config.NumberColumn('Price',           format='%.4f'),
                            'prev_close':    st.column_config.NumberColumn('Prev Close',      format='%.4f'),
                            'next_close':    st.column_config.NumberColumn('Next Close',      format='%.4f'),
                            'pct_vs_prev':   st.column_config.NumberColumn('% vs Prev',      format='%+.1f %%'),
                            'pct_vs_next':   st.column_config.NumberColumn('% vs Next',      format='%+.1f %%'),
                            'tx_date':       st.column_config.DateColumn('Nearest Tx Date'),
                            'tx_action':     st.column_config.TextColumn('Tx Action'),
                            'tx_price':      st.column_config.NumberColumn('Tx Price',        format='%.4f'),
                            'days_diff':     st.column_config.NumberColumn('Days to Tx',      format='%d'),
                            'pct_vs_tx':     st.column_config.NumberColumn('% vs Tx',        format='%+.1f %%'),
                        },
                        disabled=[c for c in df_anomalies.columns if c != 'Delete'],
                        hide_index=True, width="stretch",
                        key=f"mkt_pq_editor_{inv_sec_id}",
                    )
                    copy_df_button(df_anomalies, key=f"mkt_dl_anomalies_{inv_sec_id}")

                    to_delete  = edited_anom[edited_anom['Delete']]
                    n_selected = len(to_delete)
                    n_visible  = len(df_anomalies)

                    col_del, col_del_all, col_info = st.columns([1, 1, 3])
                    with col_del:
                        del_btn = st.button(
                            f"🗑 Delete {n_selected} selected" if n_selected else "🗑 Delete selected",
                            type="primary" if n_selected else "secondary",
                            disabled=(n_selected == 0), width="stretch",
                            key=f"mkt_pq_del_btn_{inv_sec_id}",
                        )
                    with col_del_all:
                        del_all_btn = st.button(
                            f"🗑 Delete all {n_visible} listed",
                            type="primary", width="stretch",
                            key=f"mkt_pq_del_all_btn_{inv_sec_id}",
                        )
                    with col_info:
                        if n_selected:
                            st.info(f"{n_selected} row(s) checked — or use 'Delete all listed' to remove every visible row.")

                    _del_ck     = f"mkt_pq_del_confirm_{inv_sec_id}"
                    _del_all_ck = f"mkt_pq_del_all_confirm_{inv_sec_id}"

                    if del_btn and n_selected:
                        st.session_state[_del_ck] = True
                    if del_all_btn:
                        st.session_state[_del_all_ck] = True

                    if st.session_state.get(_del_ck):
                        st.warning(f"⚠️ Delete **{n_selected}** selected price record(s)? This cannot be undone.")
                        _cn, _cy, _ = st.columns([1, 1, 3])
                        with _cn:
                            if st.button("✖ Cancel", key=f"mkt_pq_del_cancel_{inv_sec_id}", width="stretch"):
                                st.session_state[_del_ck] = False
                                st.rerun()
                        with _cy:
                            if st.button("✔ Yes, delete", type="primary", key=f"mkt_pq_del_yes_{inv_sec_id}", width="stretch"):
                                delete_historical_prices(to_delete[['securities_id', 'date']].to_dict('records'))
                                get_price_anomalies.clear()
                                st.session_state[_del_ck] = False
                                st.success(f"Deleted {n_selected} price record(s).")
                                st.rerun()

                    if st.session_state.get(_del_all_ck):
                        st.warning(f"⚠️ Delete **all {n_visible}** listed price record(s)? This cannot be undone.")
                        _cn, _cy, _ = st.columns([1, 1, 3])
                        with _cn:
                            if st.button("✖ Cancel", key=f"mkt_pq_del_all_cancel_{inv_sec_id}", width="stretch"):
                                st.session_state[_del_all_ck] = False
                                st.rerun()
                        with _cy:
                            if st.button("✔ Yes, delete all", type="primary", key=f"mkt_pq_del_all_yes_{inv_sec_id}", width="stretch"):
                                delete_historical_prices(df_anomalies[['securities_id', 'date']].to_dict('records'))
                                get_price_anomalies.clear()
                                st.session_state[_del_all_ck] = False
                                st.success(f"Deleted {n_visible} price record(s).")
                                st.rerun()

            # ─────────────────────────────────────────────────────────────────
            # ─────────────────────────────────────────────────────────────────
            # SUB-TAB: Dividends
            # ─────────────────────────────────────────────────────────────────
            with st_divs:
                # Load dividend summary + history for the selected security
                with get_db() as _conn_div:
                    df_div_info = pd.read_sql("""
                        SELECT Dividend_Yield, Dividend_Rate, Five_Year_Avg_Yield,
                               Payout_Ratio, Ex_Dividend_Date, Dividend_Pay_Date,
                               Dividend_Frequency
                        FROM   Securities
                        WHERE  Securities_Id = %(sid)s
                    """, _conn_div, params={"sid": inv_sec_id})

                    df_div_hist = pd.read_sql("""
                        SELECT Ex_Date AS "Ex-Date", Amount AS "Amount"
                        FROM   Securities_Dividends
                        WHERE  Securities_Id = %(sid)s
                        ORDER  BY Ex_Date DESC
                    """, _conn_div, params={"sid": inv_sec_id})

                # ── Summary metric cards ──────────────────────────────────────
                _has_div_info = (
                    not df_div_info.empty
                    and not df_div_info.iloc[0].isnull().all()
                )
                if _has_div_info:
                    _di = df_div_info.iloc[0]

                    def _fmt_pct(v):
                        return f"{v:.2f}%" if pd.notna(v) else "—"
                    def _fmt_num(v):
                        return f"{v:,.4f}" if pd.notna(v) else "—"
                    def _fmt_date(v):
                        return str(v) if pd.notna(v) else "—"
                    def _fmt_str(v):
                        return str(v) if pd.notna(v) else "—"

                    _d1, _d2, _d3, _d4 = st.columns(4)
                    with _d1: st.metric("Dividend Yield",  _fmt_pct(_di.get("dividend_yield")))
                    with _d2: st.metric("Annual Rate",     _fmt_num(_di.get("dividend_rate")))
                    with _d3: st.metric("5Y Avg Yield",    _fmt_pct(_di.get("five_year_avg_yield")))
                    with _d4: st.metric("Payout Ratio",    _fmt_pct(_di.get("payout_ratio")))

                    _d5, _d6, _d7, _ = st.columns(4)
                    with _d5: st.metric("Ex-Dividend Date", _fmt_date(_di.get("ex_dividend_date")))
                    with _d6: st.metric("Payment Date",     _fmt_date(_di.get("dividend_pay_date")))
                    with _d7: st.metric("Frequency",        _fmt_str(_di.get("dividend_frequency")))
                else:
                    st.info(
                        "No dividend summary data stored for this security. "
                        "Use the download button below to fetch it from Yahoo Finance."
                    )

                # ── Historical dividend bar chart + table ─────────────────────
                if not df_div_hist.empty:
                    st.subheader("📅 Dividend History")

                    _df_chart = df_div_hist.copy()
                    _df_chart["Ex-Date"] = pd.to_datetime(_df_chart["Ex-Date"])
                    _df_chart["Year"]    = _df_chart["Ex-Date"].dt.year
                    _df_annual = (
                        _df_chart.groupby("Year")["Amount"]
                        .sum().reset_index().sort_values("Year")
                    )
                    fig_div = go.Figure(go.Bar(
                        x=_df_annual["Year"].astype(str),
                        y=_df_annual["Amount"],
                        marker_color="steelblue",
                        hovertemplate="Year: %{x}<br>Total: %{y:.4f}<extra></extra>",
                    ))
                    fig_div.update_layout(
                        xaxis_title="Year", yaxis_title="Total Dividend per Share",
                        margin=dict(l=0, r=0, t=0, b=0), height=280,
                        template="plotly_white",
                    )
                    st.plotly_chart(fig_div, width="stretch")

                    st.dataframe(
                        df_div_hist,
                        hide_index=True, width="stretch",
                        column_config={
                            "Ex-Date": st.column_config.DateColumn("Ex-Date", format="DD/MM/YYYY"),
                            "Amount":  st.column_config.NumberColumn("Amount",  format="%,.4f"),
                        },
                    )
                    copy_df_button(df_div_hist, key=f"mkt_dl_div_hist_{inv_sec_id}")
                else:
                    st.info("No historical dividend records found for this security.")

                # ── Targeted download button ──────────────────────────────────
                st.divider()
                _yh_ticker_div = selected_inv_sec.get("yahoo_ticker")
                _has_yahoo_div = bool(pd.notna(_yh_ticker_div) and _yh_ticker_div)
                if _has_yahoo_div:
                    if st.button(
                            "📅 Update dividend data for this security",
                            key=f"mkt_div_update_{inv_sec_id}", width="stretch"):
                        with st.spinner("Fetching dividend data from Yahoo…"):
                            download_securities_info_from_yahoo(target_sec_id=inv_sec_id)
                            download_dividend_history(target_sec_id=inv_sec_id)
                        _md_load_securities_list.clear()
                        st.rerun()
                else:
                    st.caption(
                        f"ℹ️ No Yahoo ticker defined for {selected_inv_sec['securities_name']} "
                        "— cannot fetch dividend data."
                    )

            # ── Corporate Actions tab ─────────────────────────────────────────
            with st_corp:
                st.subheader("🏢 Corporate Actions")

                # ── History (editable) ────────────────────────────────────────
                df_ca = get_corporate_actions(inv_sec_id)
                if df_ca.empty:
                    st.info("No corporate actions recorded for this security yet.")
                else:
                    st.markdown(f"**{len(df_ca)}** corporate action(s) on record — edit inline then click **Save**, or delete individual rows:")
                    _ca_types = ['Split', 'Reverse Split', 'Merger', 'Acquisition', 'Spinoff',
                                 'Rights Issue', 'Name Change', 'Delisting', 'Other', 'Default', 'Dividend']
                    df_ca_edit = df_ca.copy()
                    df_ca_edit["effective_date"] = pd.to_datetime(df_ca_edit["effective_date"]).dt.date
                    _ca_orig_key = f"ca_orig_{inv_sec_id}"
                    if _ca_orig_key not in st.session_state:
                        st.session_state[_ca_orig_key] = df_ca_edit.copy()
                    df_ca_orig = st.session_state[_ca_orig_key]

                    edited_ca = st.data_editor(
                        df_ca_orig,
                        hide_index=True,
                        width='stretch',
                        key=f"ca_editor_{inv_sec_id}",
                        column_config={
                            "id":             st.column_config.NumberColumn("ID",          width="small",  disabled=True),
                            "action_type":    st.column_config.SelectboxColumn("Type",     width="small",  options=_ca_types),
                            "effective_date": st.column_config.DateColumn("Date",          width="small",  format="YYYY-MM-DD"),
                            "ratio_new":      st.column_config.NumberColumn("Ratio New",   width="small",  format="%.4g"),
                            "ratio_old":      st.column_config.NumberColumn("Ratio Old",   width="small",  format="%.4g"),
                            "description":    st.column_config.TextColumn("Description"),
                            "created_at":     st.column_config.TextColumn("Recorded At",   width="medium", disabled=True),
                        },
                        column_order=["id", "effective_date", "action_type", "ratio_new", "ratio_old", "description", "created_at"],
                    )

                    def _linked_inv_count(cur, ca_id: int, sec_id: int, eff_date, desc: str) -> int:
                        """Count ShrIn/ShrOut investment rows that were created by this corporate action."""
                        cur.execute(
                            """SELECT COUNT(*) FROM Investments
                                WHERE Securities_Id = %s
                                  AND Date          = %s
                                  AND Action        IN ('ShrIn', 'ShrOut')
                                  AND Description   = %s""",
                            (sec_id, eff_date, desc),
                        )
                        return cur.fetchone()[0]

                    _ca_changed = not edited_ca[["action_type","effective_date","ratio_new","ratio_old","description"]].equals(
                        df_ca_orig[["action_type","effective_date","ratio_new","ratio_old","description"]]
                    )
                    if _ca_changed:
                        if st.button("💾 Save Changes", key=f"ca_save_{inv_sec_id}", type="primary"):
                            try:
                                from database.connection import get_db as _get_db
                                with _get_db() as _conn:
                                    _cur = _conn.cursor()
                                    inv_updates = 0
                                    for _, row in edited_ca.iterrows():
                                        orig_rows = df_ca_orig[df_ca_orig["id"] == row["id"]]
                                        if orig_rows.empty:
                                            continue
                                        orig = orig_rows.iloc[0]
                                        date_changed = row["effective_date"] != orig["effective_date"]
                                        desc_changed = str(row["description"]) != str(orig["description"])
                                        if (row["action_type"]   != orig["action_type"] or
                                            date_changed or
                                            str(row["ratio_new"]) != str(orig["ratio_new"]) or
                                            str(row["ratio_old"]) != str(orig["ratio_old"]) or
                                            desc_changed):
                                            _cur.execute(
                                                """UPDATE Corporate_Actions
                                                      SET Action_Type    = %s::corporate_action_type,
                                                          Effective_Date = %s,
                                                          Ratio_New      = %s,
                                                          Ratio_Old      = %s,
                                                          Description    = %s
                                                    WHERE Corporate_Actions_Id = %s""",
                                                (row["action_type"], row["effective_date"],
                                                 row["ratio_new"] if pd.notna(row["ratio_new"]) else None,
                                                 row["ratio_old"] if pd.notna(row["ratio_old"]) else None,
                                                 row["description"] if pd.notna(row["description"]) else None,
                                                 int(row["id"])),
                                            )
                                            # Sync linked ShrIn/ShrOut investment rows
                                            new_desc = row["description"] if pd.notna(row["description"]) else orig["description"]
                                            ratio_changed = (
                                                str(row["ratio_new"]) != str(orig["ratio_new"]) or
                                                str(row["ratio_old"]) != str(orig["ratio_old"])
                                            )
                                            is_split_type = row["action_type"] in ("Split", "Reverse Split")

                                            if date_changed or desc_changed or ratio_changed:
                                                # Fetch the linked rows so we know which accounts are affected
                                                _cur.execute(
                                                    """SELECT i.Investments_Id, i.Accounts_Id, i.Action, i.Quantity
                                                         FROM Investments i
                                                        WHERE i.Securities_Id = %s
                                                          AND i.Date          = %s
                                                          AND i.Action        IN ('ShrIn', 'ShrOut')
                                                          AND i.Description   = %s""",
                                                    (inv_sec_id, orig["effective_date"], orig["description"]),
                                                )
                                                linked_rows = _cur.fetchall()

                                                for inv_id, acct_id, action, old_qty in linked_rows:
                                                    new_qty = old_qty  # default: no change

                                                    if ratio_changed and is_split_type and pd.notna(row["ratio_new"]) and pd.notna(row["ratio_old"]) and float(row["ratio_old"]) != 0:
                                                        new_ratio = float(row["ratio_new"]) / float(row["ratio_old"])
                                                        # Recompute pre-split holdings for this account
                                                        _cur.execute(
                                                            """SELECT COALESCE(SUM(
                                                                   CASE WHEN Action IN ('Buy','Reinvest','ShrIn') THEN  Quantity
                                                                        WHEN Action IN ('Sell','ShrOut')          THEN -Quantity
                                                                        ELSE 0 END), 0)
                                                                 FROM Investments
                                                                WHERE Securities_Id = %s
                                                                  AND Accounts_Id   = %s
                                                                  AND Date          < %s
                                                                  AND Action        IN ('Buy','Sell','Reinvest','ShrIn','ShrOut')""",
                                                            (inv_sec_id, acct_id, orig["effective_date"]),
                                                        )
                                                        pre_qty = float(_cur.fetchone()[0])

                                                        if new_ratio >= 1:   # forward split → ShrIn
                                                            new_qty = round(pre_qty * (new_ratio - 1), 10)
                                                        else:                # reverse split → ShrOut
                                                            new_qty = round(pre_qty * (1 - new_ratio), 10)

                                                    new_action = action
                                                    if ratio_changed and is_split_type and pd.notna(row["ratio_new"]) and pd.notna(row["ratio_old"]):
                                                        new_action = "ShrIn" if float(row["ratio_new"]) >= float(row["ratio_old"]) else "ShrOut"

                                                    _cur.execute(
                                                        """UPDATE Investments
                                                              SET Date        = %s,
                                                                  Description = %s,
                                                                  Quantity    = %s,
                                                                  Action      = %s
                                                            WHERE Investments_Id = %s""",
                                                        (row["effective_date"], new_desc,
                                                         abs(new_qty), new_action, inv_id),
                                                    )
                                                    inv_updates += 1

                                if inv_updates:
                                    from database.crud import update_holdings
                                    update_holdings()
                                st.session_state.pop(_ca_orig_key, None)
                                st.session_state.pop(f"ca_editor_{inv_sec_id}", None)
                                msg = "Corporate actions updated."
                                if inv_updates:
                                    msg += f" {inv_updates} linked investment row(s) also updated (quantity, date, description, action)."
                                st.success(msg)
                                st.rerun()
                            except Exception as _e:
                                st.error(f"Save failed: {_e}")

                    st.markdown("**Delete a corporate action:**")
                    _del_options = {
                        row["id"]: f"#{row['id']} — {row['effective_date']} {row['action_type']} {row.get('description','') or ''}"
                        for _, row in df_ca.iterrows()
                    }
                    _del_id = st.selectbox("Select to delete", options=[None] + list(_del_options.keys()),
                                           format_func=lambda x: "— choose —" if x is None else _del_options[x],
                                           key=f"ca_del_sel_{inv_sec_id}")
                    if _del_id is not None:
                        # Count linked investment rows so we can warn before deleting
                        _del_row = df_ca[df_ca["id"] == _del_id].iloc[0]
                        from database.connection import get_db as _get_db
                        with _get_db() as _chk_conn:
                            _chk_cur = _chk_conn.cursor()
                            _linked_n = _linked_inv_count(
                                _chk_cur, int(_del_id), inv_sec_id,
                                _del_row["effective_date"], str(_del_row["description"] or ""),
                            )
                        if _linked_n:
                            st.warning(
                                f"⚠️ This corporate action has **{_linked_n} linked investment "
                                f"row(s)** (ShrIn/ShrOut on {_del_row['effective_date']}). "
                                "Deleting will also remove those investment entries and recalculate holdings."
                            )
                        _del_confirm = st.checkbox(
                            f"Confirm deletion of corporate action #{_del_id}" +
                            (f" and its {_linked_n} investment row(s)" if _linked_n else ""),
                            key=f"ca_del_confirm_{inv_sec_id}_{_del_id}",
                        )
                        if st.button("🗑️ Delete", key=f"ca_del_btn_{inv_sec_id}",
                                     type="secondary", disabled=not _del_confirm):
                            try:
                                with _get_db() as _conn:
                                    _cur = _conn.cursor()
                                    if _linked_n:
                                        _cur.execute(
                                            """DELETE FROM Investments
                                                WHERE Securities_Id = %s
                                                  AND Date          = %s
                                                  AND Action        IN ('ShrIn', 'ShrOut')
                                                  AND Description   = %s""",
                                            (inv_sec_id, _del_row["effective_date"],
                                             str(_del_row["description"] or "")),
                                        )
                                    _cur.execute(
                                        "DELETE FROM Corporate_Actions WHERE Corporate_Actions_Id = %s",
                                        (int(_del_id),),
                                    )
                                if _linked_n:
                                    from database.crud import update_holdings
                                    update_holdings()
                                st.session_state.pop(_ca_orig_key, None)
                                st.session_state.pop(f"ca_editor_{inv_sec_id}", None)
                                st.success(
                                    f"Corporate action #{_del_id} deleted" +
                                    (f" along with {_linked_n} investment row(s). Holdings recalculated." if _linked_n else ".")
                                )
                                st.rerun()
                            except Exception as _e:
                                st.error(f"Delete failed: {_e}")
                st.divider()

                # ── Event type selector ───────────────────────────────────────
                import datetime

                sec_name = selected_inv_sec.get("securities_name", "")
                ticker = selected_inv_sec.get("ticker", "") or ""
                sec_label = ticker if ticker else sec_name

                corp_event_type = st.radio(
                    "Event type",
                    ["Stock Split / Reverse Split", "Default / Delisting", "Dividend"],
                    horizontal=True,
                    key=f"corp_event_type_{inv_sec_id}",
                )

                # Account filter (shared by both event types)
                all_accts = get_all_accounts_for_nwr()
                acct_name_to_id = dict(zip(all_accts["accounts_name"], all_accts["accounts_id"]))
                sel_acct_names = st.multiselect(
                    "Limit to accounts (leave empty = all accounts holding this security)",
                    sorted(acct_name_to_id),
                    key=f"corp_accts_{inv_sec_id}",
                )
                account_ids = [acct_name_to_id[n] for n in sel_acct_names] or None

                # ── Branch: Split ─────────────────────────────────────────────
                if corp_event_type == "Stock Split / Reverse Split":
                    st.caption(
                        "Records a split by inserting a **ShrIn** (forward split) or **ShrOut** "
                        "(reverse split) entry for the delta shares on the effective date. "
                        "All existing broker-imported records are left untouched."
                    )

                    st.markdown("#### Split ratio")
                    rc1, rc2 = st.columns(2)
                    new_shares = rc1.number_input(
                        "New shares", min_value=0.001, value=2.0, step=1.0,
                        key=f"corp_new_{inv_sec_id}",
                    )
                    old_shares = rc2.number_input(
                        "Old shares", min_value=0.001, value=1.0, step=1.0,
                        key=f"corp_old_{inv_sec_id}",
                    )
                    ratio = new_shares / old_shares
                    is_forward = new_shares >= old_shares
                    action_label = "ShrIn" if is_forward else "ShrOut"

                    if is_forward:
                        auto_desc = f"{sec_label} {int(new_shares)}-for-{int(old_shares)} Stock Split"
                    else:
                        auto_desc = f"{sec_label} {int(old_shares)}-for-{int(new_shares)} Reverse Split"

                    split_date = st.date_input(
                        "Effective date", value=datetime.date.today(),
                        min_value=datetime.date(1900, 1, 1),
                        key=f"corp_date_{inv_sec_id}",
                    )
                    description = st.text_input(
                        "Description",
                        value=auto_desc,
                        key=f"corp_desc_{inv_sec_id}_{int(new_shares)}_{int(old_shares)}",
                        help="Auto-filled from the ratio above. Edit if needed.",
                    )

                    st.divider()
                    if st.button("Preview", key=f"corp_preview_btn_{inv_sec_id}"):
                        df_prev = get_split_preview(inv_sec_id, split_date, account_ids)
                        st.session_state[f"corp_preview_df_{inv_sec_id}"] = df_prev

                    if f"corp_preview_df_{inv_sec_id}" in st.session_state:
                        df_prev = st.session_state[f"corp_preview_df_{inv_sec_id}"].copy()
                        if df_prev.empty:
                            st.info(
                                "No current holdings found for this security — no ShrIn/ShrOut "
                                "entries will be created. The event will still be recorded in "
                                "Corporate Actions so that split-adjusted price history works correctly."
                            )
                            st.divider()
                            confirmed_no_hold = st.checkbox(
                                "Record this split/reverse split event with no holdings adjustment",
                                key=f"corp_confirm_nohold_{inv_sec_id}",
                            )
                            if st.button(
                                "Apply", type="primary",
                                key=f"corp_apply_nohold_{inv_sec_id}",
                                disabled=not confirmed_no_hold,
                            ):
                                try:
                                    n = apply_stock_split(
                                        securities_id=inv_sec_id,
                                        split_date=split_date,
                                        new_shares=new_shares,
                                        old_shares=old_shares,
                                        holdings_by_account=[],
                                        description=description.strip(),
                                    )
                                    st.success(
                                        "Corporate Action recorded (0 investment rows — no holdings to adjust)."
                                    )
                                    st.session_state.pop(f"corp_preview_df_{inv_sec_id}", None)
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Error recording split: {e}")
                        else:
                            if is_forward:
                                df_prev["delta_qty"] = (df_prev["current_qty"] * (ratio - 1)).round(6)
                            else:
                                df_prev["delta_qty"] = (df_prev["current_qty"] * (1 - ratio)).round(6)
                            df_prev["new_total"] = (
                                df_prev["current_qty"] + df_prev["delta_qty"]
                                if is_forward
                                else df_prev["current_qty"] - df_prev["delta_qty"]
                            )
                            df_prev["action"] = action_label

                            st.markdown(
                                f"The following **{action_label}** entr{'ies' if len(df_prev)>1 else 'y'} "
                                f"will be inserted on **{split_date}**:"
                            )
                            st.dataframe(
                                df_prev[["account", "current_qty", "delta_qty", "new_total", "action"]],
                                column_config={
                                    "account":     st.column_config.TextColumn("Account"),
                                    "current_qty": st.column_config.NumberColumn("Current Qty",    format="%.6f"),
                                    "delta_qty":   st.column_config.NumberColumn(f"{action_label} Qty", format="%.6f"),
                                    "new_total":   st.column_config.NumberColumn("New Total Qty",  format="%.6f"),
                                    "action":      st.column_config.TextColumn("Action", width="small"),
                                },
                                hide_index=True,
                                width='stretch',
                            )

                            st.divider()
                            confirmed = st.checkbox(
                                "I understand this cannot be undone — apply the split",
                                key=f"corp_confirm_{inv_sec_id}",
                            )
                            if st.button(
                                "Apply", type="primary",
                                key=f"corp_apply_{inv_sec_id}",
                                disabled=not confirmed,
                            ):
                                try:
                                    holdings = df_prev[["accounts_id", "current_qty"]].to_dict("records")
                                    n = apply_stock_split(
                                        securities_id=inv_sec_id,
                                        split_date=split_date,
                                        new_shares=new_shares,
                                        old_shares=old_shares,
                                        holdings_by_account=holdings,
                                        description=description.strip(),
                                    )
                                    st.success(
                                        f"Done: {n} {action_label} row(s) inserted. "
                                        "Holdings have been refreshed."
                                    )
                                    st.session_state.pop(f"corp_preview_df_{inv_sec_id}", None)
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Error applying split: {e}")

                # ── Branch: Default / Delisting ───────────────────────────────
                elif corp_event_type == "Default / Delisting":
                    st.caption(
                        "Records a company default or delisting by inserting a **ShrOut** for "
                        "the **full remaining quantity** in each account, bringing the position "
                        "to zero. All existing broker-imported records are left untouched."
                    )

                    wo_action_type = st.selectbox(
                        "Event type",
                        ["Default", "Delisting"],
                        key=f"corp_wo_type_{inv_sec_id}",
                    )
                    wo_date = st.date_input(
                        "Effective date", value=datetime.date.today(),
                        min_value=datetime.date(1900, 1, 1),
                        key=f"corp_wo_date_{inv_sec_id}",
                    )
                    auto_wo_desc = f"{sec_label} — {wo_action_type}"
                    wo_description = st.text_input(
                        "Description",
                        value=auto_wo_desc,
                        key=f"corp_wo_desc_{inv_sec_id}_{wo_action_type}",
                        help="Auto-filled. Edit if needed.",
                    )

                    st.divider()
                    if st.button("Preview", key=f"corp_wo_preview_btn_{inv_sec_id}"):
                        df_wo_prev = get_split_preview(inv_sec_id, wo_date, account_ids)
                        st.session_state[f"corp_wo_preview_df_{inv_sec_id}"] = df_wo_prev

                    if f"corp_wo_preview_df_{inv_sec_id}" in st.session_state:
                        df_wo_prev = st.session_state[f"corp_wo_preview_df_{inv_sec_id}"].copy()
                        if df_wo_prev.empty:
                            st.warning(
                                "No holdings found for this security before the selected date "
                                "in the chosen accounts."
                            )
                        else:
                            df_wo_prev["shrout_qty"] = df_wo_prev["current_qty"].round(6)
                            df_wo_prev["new_total"]  = 0.0
                            df_wo_prev["action"]     = "ShrOut"

                            st.markdown(
                                f"The following **ShrOut** entr{'ies' if len(df_wo_prev)>1 else 'y'} "
                                f"will be inserted on **{wo_date}** (position → 0):"
                            )
                            st.dataframe(
                                df_wo_prev[["account", "current_qty", "shrout_qty", "new_total", "action"]],
                                column_config={
                                    "account":     st.column_config.TextColumn("Account"),
                                    "current_qty": st.column_config.NumberColumn("Current Qty",  format="%.6f"),
                                    "shrout_qty":  st.column_config.NumberColumn("ShrOut Qty",   format="%.6f"),
                                    "new_total":   st.column_config.NumberColumn("New Total Qty", format="%.6f"),
                                    "action":      st.column_config.TextColumn("Action", width="small"),
                                },
                                hide_index=True,
                                width='stretch',
                            )

                            st.divider()
                            wo_confirmed = st.checkbox(
                                "I understand this cannot be undone — write off all shares to zero",
                                key=f"corp_wo_confirm_{inv_sec_id}",
                            )
                            if st.button(
                                "Apply", type="primary",
                                key=f"corp_wo_apply_{inv_sec_id}",
                                disabled=not wo_confirmed,
                            ):
                                try:
                                    holdings = df_wo_prev[["accounts_id", "current_qty"]].to_dict("records")
                                    n = apply_writeoff(
                                        securities_id=inv_sec_id,
                                        event_date=wo_date,
                                        action_type=wo_action_type,
                                        holdings_by_account=holdings,
                                        description=wo_description.strip(),
                                    )
                                    st.success(
                                        f"Done: {n} ShrOut row(s) inserted. "
                                        "Holdings have been refreshed."
                                    )
                                    st.session_state.pop(f"corp_wo_preview_df_{inv_sec_id}", None)
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Error applying write-off: {e}")

                # ── Branch: Dividend ──────────────────────────────────────────
                elif corp_event_type == "Dividend":
                    st.caption(
                        "Records a dividend payment by inserting a **Dividend** entry "
                        "per account based on current holdings. "
                        "Enter the gross amount per share and the applicable withholding "
                        "tax rate; the net amount is computed automatically."
                    )

                    div_dc1, div_dc2 = st.columns(2)
                    div_gross = div_dc1.number_input(
                        "Gross amount per share",
                        min_value=0.0, value=0.0, step=0.01, format="%.10f",
                        key=f"corp_div_gross_{inv_sec_id}",
                    )
                    div_tax = div_dc2.number_input(
                        "Withholding tax rate (%)",
                        min_value=0.0, max_value=100.0, value=15.0, step=0.5,
                        format="%.2f",
                        key=f"corp_div_tax_{inv_sec_id}",
                    )
                    div_net = div_gross * (1.0 - div_tax / 100.0)
                    st.caption(f"Net per share: **{div_net:.10f}**  (gross {div_gross:.10f} − {div_tax:.2f}% tax)")

                    div_date = st.date_input(
                        "Payment date", value=datetime.date.today(),
                        min_value=datetime.date(1900, 1, 1),
                        key=f"corp_div_date_{inv_sec_id}",
                    )
                    auto_div_desc = (
                        f"{sec_label} Dividend — {div_gross} gross / {div_net} net per share"
                    )
                    div_description = st.text_input(
                        "Description",
                        value=auto_div_desc,
                        key=f"corp_div_desc_{inv_sec_id}_{div_gross}_{div_tax}",
                        help="Auto-filled. Edit if needed.",
                    )

                    st.divider()
                    if st.button("Preview", key=f"corp_div_preview_btn_{inv_sec_id}"):
                        df_div_prev = get_split_preview(inv_sec_id, div_date, account_ids)
                        st.session_state[f"corp_div_preview_df_{inv_sec_id}"] = df_div_prev

                    if f"corp_div_preview_df_{inv_sec_id}" in st.session_state:
                        df_div_prev = st.session_state[f"corp_div_preview_df_{inv_sec_id}"].copy()
                        if df_div_prev.empty:
                            st.warning(
                                "No holdings found for this security before the selected date "
                                "in the chosen accounts."
                            )
                        else:
                            net_factor = 1.0 - div_tax / 100.0
                            df_div_prev["gross_per_share"] = div_gross
                            df_div_prev["net_per_share"]   = round(div_gross * net_factor, 10)
                            df_div_prev["gross_total"]     = (df_div_prev["current_qty"] * div_gross).round(10)
                            df_div_prev["net_total"]       = (df_div_prev["gross_total"] * net_factor).round(10)

                            st.markdown(
                                f"The following **Dividend** entr{'ies' if len(df_div_prev) > 1 else 'y'} "
                                f"will be inserted on **{div_date}**:"
                            )
                            st.dataframe(
                                df_div_prev[["account", "current_qty",
                                             "gross_per_share", "net_per_share",
                                             "gross_total", "net_total"]],
                                column_config={
                                    "account":         st.column_config.TextColumn("Account"),
                                    "current_qty":     st.column_config.NumberColumn("Shares",          format="%.6f"),
                                    "gross_per_share": st.column_config.NumberColumn("Gross / Share",   format="%.10f"),
                                    "net_per_share":   st.column_config.NumberColumn("Net / Share",     format="%.10f"),
                                    "gross_total":     st.column_config.NumberColumn("Gross Total",     format="%.4f"),
                                    "net_total":       st.column_config.NumberColumn("Net Total",       format="%.4f"),
                                },
                                hide_index=True,
                                width='stretch',
                            )

                            st.divider()
                            div_confirmed = st.checkbox(
                                "I understand this cannot be undone — record the dividend",
                                key=f"corp_div_confirm_{inv_sec_id}",
                            )
                            if st.button(
                                "Apply", type="primary",
                                key=f"corp_div_apply_{inv_sec_id}",
                                disabled=not div_confirmed,
                            ):
                                try:
                                    holdings = df_div_prev[["accounts_id", "current_qty"]].to_dict("records")
                                    n = apply_dividend(
                                        securities_id=inv_sec_id,
                                        event_date=div_date,
                                        gross_per_share=div_gross,
                                        tax_rate_pct=div_tax,
                                        holdings_by_account=holdings,
                                        description=div_description.strip(),
                                    )
                                    st.success(
                                        f"Done: {n} Dividend row(s) inserted. "
                                        "Holdings have been refreshed."
                                    )
                                    st.session_state.pop(f"corp_div_preview_df_{inv_sec_id}", None)
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Error recording dividend: {e}")

    # =========================================================================
    # TAB 3 — WATCHLIST
    # =========================================================================
    with t_watch:
        st.subheader("👀 Watchlist")
        st.caption(
            "Track securities you're considering buying. "
            "Set a target entry price and/or a stop-loss to monitor distance."
        )

        df_wl = get_watchlist()

        if not df_wl.empty:
            # ── colour helpers ────────────────────────────────────────────────
            def _colour_pct(val):
                if val is None or (isinstance(val, float) and pd.isna(val)):
                    return ''
                return 'color:#E74C3C' if float(val) > 0 else 'color:#27AE60'

            def _colour_upside(val):
                if val is None or (isinstance(val, float) and pd.isna(val)):
                    return ''
                return 'color:#27AE60' if float(val) > 0 else 'color:#E74C3C'

            _display_cols = [
                'securities_name', 'securities_type', 'ticker', 'currency',
                'current_price', 'price_date',
                'target_price', 'pct_from_target',
                'stop_loss', 'pct_from_stop',
                'analyst_target', 'upside_to_analyst',
                'dividend_yield', 'already_held', 'note', 'added_date',
            ]
            st.dataframe(
                df_wl[_display_cols].style
                    .map(_colour_pct,    subset=['pct_from_target', 'pct_from_stop'])
                    .map(_colour_upside, subset=['upside_to_analyst']),
                hide_index=True,
                width='stretch',
                column_config={
                    'securities_name':   st.column_config.TextColumn('Security'),
                    'securities_type':   st.column_config.TextColumn('Type'),
                    'ticker':            st.column_config.TextColumn('Ticker'),
                    'currency':          st.column_config.TextColumn('Ccy'),
                    'current_price':     st.column_config.NumberColumn('Current Price', format='%,.4f'),
                    'price_date':        st.column_config.DateColumn('Price Date', format='DD/MM/YYYY'),
                    'target_price':      st.column_config.NumberColumn('Target Entry', format='%,.4f'),
                    'pct_from_target':   st.column_config.NumberColumn('% vs Target',  format='%+.2f%%',
                                             help='Positive = price is above your target (expensive); '
                                                  'Negative = price is below target (on sale).'),
                    'stop_loss':         st.column_config.NumberColumn('Stop Loss',    format='%,.4f'),
                    'pct_from_stop':     st.column_config.NumberColumn('% vs Stop',    format='%+.2f%%',
                                             help='Negative = price is below stop-loss.'),
                    'analyst_target':    st.column_config.NumberColumn('Analyst Target', format='%,.4f'),
                    'upside_to_analyst': st.column_config.NumberColumn('Upside %',     format='%+.2f%%'),
                    'dividend_yield':    st.column_config.NumberColumn('Div Yield %',  format='%.2f%%'),
                    'already_held':      st.column_config.CheckboxColumn('In Portfolio'),
                    'note':              st.column_config.TextColumn('Note'),
                    'added_date':        st.column_config.DateColumn('Added', format='DD/MM/YYYY'),
                },
            )

            # ── Remove button ─────────────────────────────────────────────────
            with st.expander("🗑️ Remove from Watchlist"):
                names = df_wl['securities_name'].tolist()
                to_remove = st.selectbox("Select security to remove", names,
                                         key="wl_remove_sel")
                if st.button("Remove", key="wl_remove_btn", type="secondary"):
                    wid = int(df_wl[df_wl['securities_name'] == to_remove]['watchlist_id'].iloc[0])
                    remove_watchlist_item(wid)
                    st.success(f"{to_remove} removed from watchlist.")
                    st.rerun()
        else:
            st.info("Your watchlist is empty. Add securities below.")

        # ── Add to Watchlist ──────────────────────────────────────────────────
        st.divider()
        st.markdown("#### ➕ Add to Watchlist")

        # Use the already-loaded securities list
        sec_opts = df_sec_list.set_index('securities_id')['securities_name'].to_dict()
        # Exclude already-watched
        watched_ids = set(df_wl['securities_id'].tolist()) if not df_wl.empty else set()
        avail = {k: v for k, v in sec_opts.items() if k not in watched_ids}

        if avail:
            add_col1, add_col2, add_col3 = st.columns(3)
            with add_col1:
                sel_add = st.selectbox(
                    "Security", options=list(avail.keys()),
                    format_func=lambda x: avail[x],
                    key="wl_add_sec",
                )
            with add_col2:
                wl_target = st.number_input("Target Entry Price (optional)",
                                            min_value=0.0, value=0.0, step=0.01,
                                            format="%.4f", key="wl_add_target")
                wl_stop   = st.number_input("Stop Loss Price (optional)",
                                            min_value=0.0, value=0.0, step=0.01,
                                            format="%.4f", key="wl_add_stop")
            with add_col3:
                wl_note = st.text_area("Note (optional)", key="wl_add_note", height=98)

            if st.button("➕ Add to Watchlist", key="wl_add_btn", type="primary"):
                add_watchlist_item(
                    securities_id=sel_add,
                    target_price=wl_target if wl_target > 0 else None,
                    stop_loss=wl_stop   if wl_stop   > 0 else None,
                    note=wl_note.strip() or None,
                )
                st.success(f"{avail[sel_add]} added to watchlist.")
                st.rerun()
        else:
            st.info("All securities are already on your watchlist.")

    # =========================================================================
    # TAB 4 — ALERTS
    # =========================================================================
    with t_alerts:
        st.subheader("🔔 Alerts")
        st.caption(
            "Active alerts are checked every time you open the Dashboard and shown as banners. "
            "Supported types: **Price Above**, **Price Below**, **Allocation Drift**."
        )

        df_alerts = get_alerts()

        if not df_alerts.empty:
            _TYPE_LABEL = {
                'price_above':      '🔔 Above',
                'price_below':      '🔻 Below',
                'allocation_drift': '⚖️ Drift',
            }
            _ALERT_TYPES = {
                'price_above':      '🔔 Price Above Threshold',
                'price_below':      '🔻 Price Below Threshold',
                'allocation_drift': '⚖️ Allocation Drift from Target',
            }
            sec_opts = df_sec_list.set_index('securities_id')['securities_name'].to_dict()

            # Header row
            hc = st.columns([2, 3, 1.5, 1.5, 1.5, 2, 1, 1, 1])
            for col, hdr in zip(hc, ['Type', 'Security / Asset Type', 'Threshold',
                                      'Current Price', 'Price Date', 'Note', '', '', '']):
                col.markdown(f"<small><b>{hdr}</b></small>", unsafe_allow_html=True)
            st.divider()

            for _, row in df_alerts.iterrows():
                aid    = int(row['alert_id'])
                active = bool(row['is_active'])
                label  = _TYPE_LABEL.get(str(row['alert_type']), str(row['alert_type']))

                if row['alert_type'] in ('price_above', 'price_below'):
                    subject = row.get('securities_name') or '—'
                else:
                    subject = row.get('asset_type') or '—'

                thr = f"{float(row['threshold']):,.4f}" if row['threshold'] is not None else '—'

                cur_px = row.get('current_price')
                px_str = f"{float(cur_px):,.4f}" if cur_px is not None else '—'

                px_date = row.get('price_date')
                pd_str  = pd.Timestamp(px_date).strftime('%d/%m/%Y') if px_date is not None else '—'

                note = row.get('note') or ''
                dim  = '' if active else 'opacity:0.45;'

                c1, c2, c3, c4, c5, c6, c7, c8, c9 = st.columns([2, 3, 1.5, 1.5, 1.5, 2, 1, 1, 1])
                c1.markdown(f"<span style='{dim}'>{label}</span>", unsafe_allow_html=True)
                c2.markdown(f"<span style='{dim}'>{subject}</span>", unsafe_allow_html=True)
                c3.markdown(f"<span style='{dim}'>`{thr}`</span>", unsafe_allow_html=True)
                c4.markdown(f"<span style='{dim}'>`{px_str}`</span>", unsafe_allow_html=True)
                c5.markdown(f"<span style='{dim}'>{pd_str}</span>", unsafe_allow_html=True)
                c6.markdown(f"<span style='{dim};font-style:italic'>{note}</span>",
                            unsafe_allow_html=True)
                with c7:
                    tog_label = "✅" if active else "⏸"
                    if st.button(tog_label, key=f"alert_tog_{aid}",
                                 help="Toggle active/inactive"):
                        toggle_alert(aid, not active)
                        st.rerun()
                with c8:
                    if st.button("✏️", key=f"alert_edit_btn_{aid}", help="Edit alert"):
                        current = st.session_state.get("alert_editing")
                        st.session_state["alert_editing"] = None if current == aid else aid
                        st.rerun()
                with c9:
                    if st.button("🗑️", key=f"alert_del_{aid}", help="Delete alert"):
                        delete_alert(aid)
                        st.session_state.pop("alert_editing", None)
                        st.rerun()

                # ── Inline edit form (shown only for the row being edited) ────
                if st.session_state.get("alert_editing") == aid:
                    with st.container(border=True):
                        st.markdown(f"**Edit Alert #{aid}**")
                        ea1, ea2 = st.columns(2)
                        with ea1:
                            e_type = st.selectbox(
                                "Type",
                                options=list(_ALERT_TYPES.keys()),
                                format_func=lambda x: _ALERT_TYPES[x],
                                index=list(_ALERT_TYPES.keys()).index(row['alert_type'])
                                      if row['alert_type'] in _ALERT_TYPES else 0,
                                key=f"e_type_{aid}",
                            )
                        with ea2:
                            e_note = st.text_input(
                                "Note",
                                value=row.get('note') or '',
                                key=f"e_note_{aid}",
                            )

                        e_sec_id = row.get('securities_id')
                        e_asset  = row.get('asset_type')
                        e_thresh = float(row['threshold']) if row['threshold'] is not None else 0.0

                        eb1, eb2 = st.columns(2)
                        if e_type in ('price_above', 'price_below'):
                            with eb1:
                                e_sec_id = st.selectbox(
                                    "Security",
                                    options=list(sec_opts.keys()),
                                    format_func=lambda x: sec_opts[x],
                                    index=(list(sec_opts.keys()).index(int(e_sec_id))
                                           if e_sec_id and int(e_sec_id) in sec_opts else 0),
                                    key=f"e_sec_{aid}",
                                )
                            with eb2:
                                e_thresh = st.number_input(
                                    "Price Threshold",
                                    min_value=0.0, value=e_thresh,
                                    step=0.01, format="%.4f",
                                    key=f"e_thresh_{aid}",
                                )
                            e_asset = None
                            e_dir   = 'above' if e_type == 'price_above' else 'below'
                        else:
                            df_alloc_e = get_asset_allocation_data()
                            asset_types_e = (df_alloc_e['securities_type'].tolist()
                                             if not df_alloc_e.empty else [])
                            with eb1:
                                e_asset = st.selectbox(
                                    "Asset Type", asset_types_e,
                                    index=(asset_types_e.index(e_asset)
                                           if e_asset in asset_types_e else 0),
                                    key=f"e_asset_{aid}",
                                ) if asset_types_e else st.text_input(
                                    "Asset Type", value=e_asset or '',
                                    key=f"e_asset_{aid}")
                            with eb2:
                                e_thresh = st.number_input(
                                    "Max drift (% points)",
                                    min_value=0.1, value=e_thresh,
                                    step=0.5, format="%.1f",
                                    key=f"e_thresh_{aid}",
                                )
                            e_sec_id = None
                            e_dir    = None

                        save_col, cancel_col = st.columns([1, 5])
                        if save_col.button("💾 Save", type="primary",
                                           key=f"e_save_{aid}"):
                            if e_thresh and e_thresh > 0:
                                save_alert(
                                    alert_type=e_type,
                                    securities_id=e_sec_id,
                                    asset_type=e_asset,
                                    threshold=e_thresh,
                                    direction=e_dir,
                                    note=e_note.strip() or None,
                                    alert_id=aid,
                                )
                                st.session_state.pop("alert_editing", None)
                                st.success("Alert updated.")
                                st.rerun()
                            else:
                                st.error("Threshold must be greater than 0.")
                        if cancel_col.button("Cancel", key=f"e_cancel_{aid}"):
                            st.session_state.pop("alert_editing", None)
                            st.rerun()
        else:
            st.info("No alerts configured yet.")

        # ── Add new alert ─────────────────────────────────────────────────────
        st.markdown("#### ➕ New Alert")

        _ALERT_TYPES = {
            'price_above':      '🔔 Price Above Threshold',
            'price_below':      '🔻 Price Below Threshold',
            'allocation_drift': '⚖️ Allocation Drift from Target',
        }
        new_type = st.selectbox(
            "Alert Type",
            options=list(_ALERT_TYPES.keys()),
            format_func=lambda x: _ALERT_TYPES[x],
            key="new_alert_type",
        )

        na_sec_id = None
        na_asset  = None
        na_thresh = None
        na_dir    = None

        if new_type in ('price_above', 'price_below'):
            # Reuse the already-loaded securities list
            sec_opts = df_sec_list.set_index('securities_id')['securities_name'].to_dict()
            na_sec_id = st.selectbox(
                "Security",
                options=list(sec_opts.keys()),
                format_func=lambda x: sec_opts[x],
                key="new_alert_sec",
            )
            na_thresh = st.number_input(
                "Price Threshold (in security's native currency)",
                min_value=0.0, value=0.0, step=0.01, format="%.4f",
                key="new_alert_thresh",
            )
            na_dir = 'above' if new_type == 'price_above' else 'below'

        elif new_type == 'allocation_drift':
            df_alloc = get_asset_allocation_data()
            asset_types = df_alloc['securities_type'].tolist() if not df_alloc.empty else []
            if asset_types:
                na_asset = st.selectbox("Asset Type", asset_types,
                                        key="new_alert_asset")
            else:
                st.warning("No holdings found — add assets first.")
            na_thresh = st.number_input(
                "Max acceptable drift (% points)",
                min_value=0.1, value=5.0, step=0.5, format="%.1f",
                key="new_alert_drift",
            )

        na_note = st.text_input("Note (optional)", key="new_alert_note")

        if st.button("➕ Add Alert", type="primary", key="new_alert_add_btn"):
            if na_thresh and na_thresh > 0:
                save_alert(
                    alert_type=new_type,
                    securities_id=na_sec_id,
                    asset_type=na_asset,
                    threshold=na_thresh,
                    direction=na_dir,
                    note=na_note.strip() or None,
                )
                st.success("Alert saved.")
                st.rerun()
            else:
                st.error("Please enter a threshold greater than 0.")
