"""Broker API Importers UI.

Entry points
------------
  render_brokerage_section() — called from ui/importers.py (Brokerage tab)
  render_revolut_import()    — called from ui/bank_import.py (Revolut Personal tab)
"""

from __future__ import annotations

import traceback

import pandas as pd
import streamlit as st

from database.connection import get_connection


# ---------------------------------------------------------------------------
# Brand logo helper (used by all importer sections)
# ---------------------------------------------------------------------------

_LOGO_HTML = """
<div style="display:flex;align-items:center;gap:12px;margin-bottom:4px;">
  <img src="{url}" style="height:36px;border-radius:6px;object-fit:contain;"
       onerror="this.style.display='none'">
  <span style="font-size:1.25rem;font-weight:600;">{name}</span>
</div>
"""


def _brand_header(url: str, name: str) -> None:
    """Render a brand logo + name header, falling back gracefully if the image is unavailable."""
    st.markdown(_LOGO_HTML.format(url=url, name=name), unsafe_allow_html=True)
    st.divider()


# ---------------------------------------------------------------------------
# Shared DB helpers
# ---------------------------------------------------------------------------

@st.cache_data(ttl=120)
def _load_accounts() -> pd.DataFrame:
    conn = get_connection()
    return pd.read_sql(
        """SELECT a.Accounts_Id   AS accounts_id,
                  a.Accounts_Name AS accounts_name,
                  a.Accounts_Type AS accounts_type,
                  c.Currencies_ShortName AS currency
           FROM   Accounts a
           LEFT JOIN Currencies c ON a.Currencies_Id = c.Currencies_Id
           ORDER  BY a.Accounts_Name""",
        conn,
    )


def _account_selectbox(label: str, key: str,
                       type_filter: list[str] | None = None) -> tuple[int | None, str]:
    """Render an account selectbox; return (accounts_id, accounts_name)."""
    df = _load_accounts()
    if type_filter:
        df = df[df["accounts_type"].isin(type_filter)]
    if df.empty:
        st.warning("No accounts found. Please create one in Static Data first.")
        return None, ""
    options = df["accounts_name"].tolist()
    sel = st.selectbox(label, options, key=key)
    row = df[df["accounts_name"] == sel].iloc[0]
    return int(row["accounts_id"]), sel


def _import_summary(counts: dict) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Investments imported", counts.get("investments", 0))
    c2.metric("Investments skipped",  counts.get("investments_skip", 0))
    c3.metric("Transactions imported", counts.get("transactions", 0))
    c4.metric("Transactions skipped",  counts.get("transactions_skip", 0))


@st.cache_data(ttl=120)
def _load_all_securities() -> pd.DataFrame:
    """Return all Securities rows for use in mapping selectboxes."""
    conn = get_connection()
    try:
        return pd.read_sql(
            """SELECT s.Securities_Id   AS securities_id,
                      s.Ticker          AS ticker,
                      s.Securities_Name AS name,
                      s.Securities_Type AS type
               FROM   Securities s
               ORDER  BY s.Securities_Name""",
            conn,
        )
    finally:
        conn.close()


def _preview_records(inv_records: list, tx_records: list,
                     inv_df_fn, tx_df_fn) -> None:
    """Show expandable previews of investments and transactions."""
    tab_inv, tab_tx = st.tabs([
        f"📈 Investments ({len(inv_records)})",
        f"💳 Cash Transactions ({len(tx_records)})",
    ])
    with tab_inv:
        if inv_records:
            df = inv_df_fn(inv_records)
            st.dataframe(df, hide_index=True, width="stretch",
                         column_config={
                             "date":           "Date",
                             "action":         "Action",
                             "symbol":         "Symbol",
                             "name":           "Name",
                             "quantity":       st.column_config.NumberColumn("Qty",     format="%.4f"),
                             "price":          st.column_config.NumberColumn("Price",   format="%.4f"),
                             "total_eur":      st.column_config.NumberColumn("Total (€)", format="%.2f"),
                             "currency":       "Ccy",
                             "commission":     st.column_config.NumberColumn("Commission", format="%.4f"),
                             "asset_category": "Asset",
                             "desc":           "Dedup Key",
                         })
        else:
            st.info("No investment records in this file.")
    with tab_tx:
        if tx_records:
            df = tx_df_fn(tx_records)
            st.dataframe(df, hide_index=True, width="stretch",
                         column_config={
                             "date":        "Date",
                             "description": "Description",
                             "amount":      st.column_config.NumberColumn("Amount", format="%.2f"),
                             "currency":    "Ccy",
                         })
        else:
            st.info("No cash transaction records in this file.")


# ===========================================================================
# Interactive Brokers — preview helper with reconciliation status
# ===========================================================================

def _ib_preview_with_status(
    inv_records: list,
    tx_records: list,
    existing_inv: set,
    existing_tx: set,
    fuzzy_inv: set,
    fuzzy_tx: set,
    sec_matches: dict,
    ignored_descs: set | None = None,
) -> None:
    """Show IB preview tables annotated with Status and Security Match columns.

    Status values:
      ✅ Exists          — description key already in DB (exact dedup match)
      ⚠️ Likely duplicate — same date/action/qty found in DB under a different key
      ⏭️ Ignored         — user-marked as permanently ignored
      🆕 New             — no match found
    """
    ignored_descs = ignored_descs or set()
    tab_inv, tab_tx = st.tabs([
        f"📈 Investments ({len(inv_records)})",
        f"💳 Cash Transactions ({len(tx_records)})",
    ])

    with tab_inv:
        if inv_records:
            rows = []
            for r in inv_records:
                if r["desc"] in existing_inv:
                    status = "✅ Exists"
                elif r["desc"] in fuzzy_inv:
                    status = "⚠️ Likely duplicate"
                elif r["desc"] in ignored_descs:
                    status = "⏭️ Ignored"
                else:
                    status = "🆕 New"
                isin_or_name = r.get("isin") or r.get("name", "")
                match_info   = sec_matches.get(isin_or_name, (None, "new"))
                match_type   = match_info[1]
                if match_type.startswith("mapped:"):
                    sec_label = f"🗺️ {match_type[7:]}"
                elif match_type.startswith("isin:"):
                    sec_label = f"🔗 {match_type[5:]}"   # matched security name
                elif match_type == "name":
                    sec_label = "🔗 Name match"
                else:
                    sec_label = "🆕 New security"
                rows.append({**r, "status": status, "security_match": sec_label})

            df_inv = pd.DataFrame(rows)
            cols = ["status", "date", "action", "symbol", "name",
                    "quantity", "price", "total_eur", "currency",
                    "commission", "asset_category", "security_match", "desc"]
            df_inv = df_inv[[c for c in cols if c in df_inv.columns]]
            st.dataframe(
                df_inv,
                hide_index=True,
                width="stretch",
                column_config={
                    "status":         "Status",
                    "date":           "Date",
                    "action":         "Action",
                    "symbol":         "Symbol",
                    "name":           "Name",
                    "quantity":       st.column_config.NumberColumn("Qty",          format="%.4f"),
                    "price":          st.column_config.NumberColumn("Price",        format="%.4f"),
                    "total_eur":      st.column_config.NumberColumn("Total (€)",    format="%.2f"),
                    "currency":       "Ccy",
                    "commission":     st.column_config.NumberColumn("Commission",   format="%.4f"),
                    "asset_category": "Asset",
                    "security_match": "Security Match",
                    "desc":           "Dedup Key",
                },
            )
        else:
            st.info("No investment records in this statement.")

    with tab_tx:
        if tx_records:
            rows = []
            for r in tx_records:
                if r["desc"] in existing_tx:
                    status = "✅ Exists"
                elif r["desc"] in fuzzy_tx:
                    status = "⚠️ Likely duplicate"
                elif r["desc"] in ignored_descs:
                    status = "⏭️ Ignored"
                else:
                    status = "🆕 New"
                rows.append({**r, "status": status})
            df_tx = pd.DataFrame(rows)
            cols = ["status", "date", "description", "amount", "currency"]
            df_tx = df_tx[[c for c in cols if c in df_tx.columns]]
            st.dataframe(
                df_tx,
                hide_index=True,
                width="stretch",
                column_config={
                    "status":      "Status",
                    "date":        "Date",
                    "description": "Description",
                    "amount":      st.column_config.NumberColumn("Amount", format="%.2f"),
                    "currency":    "Ccy",
                },
            )
        else:
            st.info("No cash transaction records in this statement.")


# ===========================================================================
# Revolut Trading — preview helper with reconciliation status
# ===========================================================================

def _revt_preview_with_status(
    inv_records: list,
    tx_records: list,
    existing_inv: set,
    existing_tx: set,
    fuzzy_inv: set,
    fuzzy_tx: set,
    sec_matches: dict,
    ignored_descs: set | None = None,
) -> None:
    """Show Revolut Trading preview tables annotated with Status and Security Match.

    Status values:
      ✅ Exists          — description key already in DB
      ⚠️ Likely duplicate — same date/action/qty found under a different key
      ⏭️ Ignored         — user-marked as permanently ignored
      🆕 New             — no match found
    """
    ignored_descs = ignored_descs or set()
    tab_inv, tab_tx = st.tabs([
        f"📈 Investments ({len(inv_records)})",
        f"💳 Cash Transactions ({len(tx_records)})",
    ])

    with tab_inv:
        if inv_records:
            rows = []
            for r in inv_records:
                if r["desc"] in existing_inv:
                    status = "✅ Exists"
                elif r["desc"] in fuzzy_inv:
                    status = "⚠️ Likely duplicate"
                elif r["desc"] in ignored_descs:
                    status = "⏭️ Ignored"
                else:
                    status = "🆕 New"
                key        = (r.get("symbol") or r.get("name", "")).strip()
                match_info = sec_matches.get(key, (None, "new"))
                match_type = match_info[1]
                if match_type.startswith("mapped:"):
                    sec_label = f"🗺️ {match_type[7:]}"
                elif match_type == "ticker":
                    sec_label = "🔗 Ticker match"
                elif match_type == "name":
                    sec_label = "🔗 Name match"
                else:
                    sec_label = "🆕 New security"
                rows.append({**r, "status": status, "security_match": sec_label})

            df_inv = pd.DataFrame(rows)
            cols = ["status", "date", "action", "symbol", "name",
                    "quantity", "price", "total_eur", "currency",
                    "asset_category", "security_match", "desc"]
            df_inv = df_inv[[c for c in cols if c in df_inv.columns]]
            st.dataframe(
                df_inv,
                hide_index=True,
                width="stretch",
                column_config={
                    "status":         "Status",
                    "date":           "Date",
                    "action":         "Action",
                    "symbol":         "Symbol",
                    "name":           "Name",
                    "quantity":       st.column_config.NumberColumn("Qty",        format="%.4f"),
                    "price":          st.column_config.NumberColumn("Price",      format="%.4f"),
                    "total_eur":      st.column_config.NumberColumn("Total (€)",  format="%.2f"),
                    "currency":       "Ccy",
                    "asset_category": "Asset",
                    "security_match": "Security Match",
                    "desc":           "Dedup Key",
                },
            )
        else:
            st.info("No investment records in this file.")

    with tab_tx:
        if tx_records:
            rows = []
            for r in tx_records:
                if r["desc"] in existing_tx:
                    status = "✅ Exists"
                elif r["desc"] in fuzzy_tx:
                    status = "⚠️ Likely duplicate"
                elif r["desc"] in ignored_descs:
                    status = "⏭️ Ignored"
                else:
                    status = "🆕 New"
                rows.append({**r, "status": status})
            df_tx = pd.DataFrame(rows)
            cols = ["status", "date", "description", "amount", "currency"]
            df_tx = df_tx[[c for c in cols if c in df_tx.columns]]
            st.dataframe(
                df_tx,
                hide_index=True,
                width="stretch",
                column_config={
                    "status":      "Status",
                    "date":        "Date",
                    "description": "Description",
                    "amount":      st.column_config.NumberColumn("Amount", format="%.2f"),
                    "currency":    "Ccy",
                },
            )
        else:
            st.info("No cash transaction records in this file.")


# ===========================================================================
# Security Mapping UI  (Revolut Trading)
# ===========================================================================

def _revt_security_mapping_ui(sec_matches: dict) -> None:
    """Expander UI for mapping unmapped Revolut Trading tickers to DB securities.

    Shows only tickers whose match_type is 'new' (i.e. not found by ticker,
    name, or saved mapping).  On save the mapping is persisted to the DB and
    the session-state sec_matches dict is updated in-place so the preview
    refreshes without a full re-parse.
    """
    unmapped = {sym: info for sym, info in sec_matches.items() if info[1] == "new"}
    if not unmapped:
        return

    with st.expander(
        f"🗺️ Security Mappings — {len(unmapped)} unmapped ticker(s) — click to configure",
        expanded=True,
    ):
        st.caption(
            "These tickers were not found in your Securities database by ticker or name. "
            "Select the matching security for each one below, then click **💾 Save Mappings**. "
            "Saved mappings are permanent and will be used for all future imports."
        )

        all_secs = _load_all_securities()
        if all_secs.empty:
            st.warning("No securities found in the database. Create them in Static Data first.")
            return

        sec_options = ["(create new — will be added on import)"] + all_secs["name"].tolist()
        pending_mappings: dict[str, int] = {}  # ticker → securities_id

        for ticker in unmapped:
            c1, c2 = st.columns([1, 3])
            with c1:
                st.markdown(f"**{ticker}**")
            with c2:
                chosen = st.selectbox(
                    f"Map {ticker} to",
                    sec_options,
                    key=f"revt_map_{ticker}",
                    label_visibility="collapsed",
                )
                if not chosen.startswith("(create new"):
                    sec_row = all_secs[all_secs["name"] == chosen]
                    if not sec_row.empty:
                        pending_mappings[ticker] = int(sec_row.iloc[0]["securities_id"])

        if pending_mappings:
            if st.button("💾 Save Mappings", key="revt_save_mappings", type="primary"):
                from database.queries import save_security_mappings
                try:
                    save_security_mappings("Revolut Trading", pending_mappings)
                    # Update sec_matches in session state immediately (avoid full re-parse)
                    _updated = dict(st.session_state.get("revt_sec_matches", {}))
                    for sym, sec_id in pending_mappings.items():
                        sec_row = all_secs[all_secs["securities_id"] == sec_id]
                        sec_name = sec_row.iloc[0]["name"] if not sec_row.empty else sym
                        _updated[sym] = (sec_id, f"mapped:{sec_name}")
                    st.session_state["revt_sec_matches"] = _updated
                    _load_all_securities.clear()
                    st.success(f"✅ Saved {len(pending_mappings)} mapping(s). Re-parse to see updated Security Match column.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Failed to save mappings: {exc}")
        else:
            st.info("Select at least one mapping above and click Save.")


# ===========================================================================
# Security Mapping UI  (Interactive Brokers)
# ===========================================================================

def _ib_security_mapping_ui(sec_matches: dict) -> None:
    """Expander UI for mapping unmapped IB securities to DB securities.

    Shows only entries whose match_type is 'new' (not resolved by ISIN, name,
    or a previously saved mapping).  The mapping key is the ISIN when the
    record has one; otherwise the security name.  On save the mapping is
    persisted to import_security_mappings and sec_matches in session state is
    updated immediately so the preview reflects the change without a re-fetch.
    """
    unmapped = {key: info for key, info in sec_matches.items() if info[1] == "new"}
    if not unmapped:
        return

    with st.expander(
        f"🗺️ Security Mappings — {len(unmapped)} unmapped security(s) — click to configure",
        expanded=True,
    ):
        st.caption(
            "These securities could not be matched in your database by ISIN or name. "
            "Select the matching DB security for each one, then click **💾 Save Mappings**. "
            "The key used is the **ISIN** when available, otherwise the security name. "
            "Saved mappings are permanent and take priority on all future IB imports."
        )

        all_secs = _load_all_securities()
        if all_secs.empty:
            st.warning("No securities found in the database. Create them in Static Data first.")
            return

        sec_options = ["(create new — will be added on import)"] + all_secs["name"].tolist()
        pending_mappings: dict[str, int] = {}  # key (isin/name) → securities_id

        for key in unmapped:
            c1, c2 = st.columns([2, 3])
            with c1:
                st.markdown(f"**{key}**")
            with c2:
                widget_key = f"ib_map_{key.replace(' ', '_').replace('/', '_')}"
                chosen = st.selectbox(
                    f"Map {key}",
                    sec_options,
                    key=widget_key,
                    label_visibility="collapsed",
                )
                if not chosen.startswith("(create new"):
                    sec_row = all_secs[all_secs["name"] == chosen]
                    if not sec_row.empty:
                        pending_mappings[key] = int(sec_row.iloc[0]["securities_id"])

        if pending_mappings:
            if st.button("💾 Save Mappings", key="ib_save_mappings", type="primary"):
                from database.queries import save_security_mappings
                try:
                    save_security_mappings("Interactive Brokers", pending_mappings)
                    # Update sec_matches in session state — no re-fetch needed
                    _updated = dict(st.session_state.get("ib_sec_matches", {}))
                    for map_key, sec_id in pending_mappings.items():
                        sec_row = all_secs[all_secs["securities_id"] == sec_id]
                        sec_name = sec_row.iloc[0]["name"] if not sec_row.empty else map_key
                        _updated[map_key] = (sec_id, f"mapped:{sec_name}")
                    st.session_state["ib_sec_matches"] = _updated
                    st.success(f"✅ Saved {len(pending_mappings)} mapping(s).")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Failed to save mappings: {exc}")
        else:
            st.info("Select at least one mapping above and click Save.")


# ===========================================================================
# Ignore-record Manager UI  (shared between IB and Revolut Trading)
# ===========================================================================

def _ignore_manager_ui(
    source: str,
    inv_records: list,
    tx_records: list,
    existing_inv: set,
    existing_tx: set,
    fuzzy_inv: set,
    fuzzy_tx: set,
    ignored_descs: set,
    session_key: str,
) -> None:
    """Expander UI for permanently marking records as 'ignored'.

    Shows only records that are genuinely new (not existing, fuzzy, or already
    ignored), so the user can decide to never be asked about them again.
    Also lets the user un-ignore previously saved keys.

    Parameters
    ----------
    source      : DB source name, e.g. 'Interactive Brokers' or 'Revolut Trading'
    session_key : 'ib' or 'revt' — prefix for session-state keys
    """
    # Records that are genuinely new and not already ignored
    new_inv = [r for r in inv_records
               if r["desc"] not in existing_inv
               and r["desc"] not in fuzzy_inv
               and r["desc"] not in ignored_descs]
    new_tx  = [r for r in tx_records
               if r["desc"] not in existing_tx
               and r["desc"] not in fuzzy_tx
               and r["desc"] not in ignored_descs]

    total_new     = len(new_inv) + len(new_tx)
    total_ignored = len(ignored_descs)

    with st.expander(
        f"⏭️ Ignore Records — {total_ignored} saved · {total_new} new available to ignore",
        expanded=False,
    ):
        # ── Mark new records as ignored ───────────────────────────────────────
        if total_new:
            st.markdown("**Tick the records you want to permanently ignore:**")
            rows: list[dict] = []
            for r in new_inv:
                rows.append({
                    "desc":     r["desc"],
                    "type":     "📈 Investment",
                    "record":   (
                        f"{r.get('action', '')} {r.get('symbol', '')} "
                        f"on {r.get('date', '')}"
                    ).strip(),
                    "ignore":   False,
                })
            for r in new_tx:
                rows.append({
                    "desc":   r["desc"],
                    "type":   "💳 Transaction",
                    "record": (
                        f"{r.get('description', r.get('desc', ''))} "
                        f"· {r.get('amount', '')} on {r.get('date', '')}"
                    ).strip(),
                    "ignore": False,
                })

            df_new = pd.DataFrame(rows)
            edited = st.data_editor(
                df_new[["type", "record", "ignore"]],
                hide_index=True,
                width="stretch",
                column_config={
                    "type":   st.column_config.TextColumn("Type",   disabled=True),
                    "record": st.column_config.TextColumn("Record", disabled=True),
                    "ignore": st.column_config.CheckboxColumn("Ignore?"),
                },
                key=f"{session_key}_ignore_editor",
            )

            # Map edited "ignore" column back to desc keys
            to_ignore = [
                df_new.iloc[i]["desc"]
                for i, sel in enumerate(edited["ignore"].tolist())
                if sel
            ]

            if to_ignore:
                if st.button(
                    f"⏭️ Mark {len(to_ignore)} record(s) as ignored",
                    key=f"{session_key}_mark_ignored",
                    type="primary",
                ):
                    from database.queries import save_ignored_records
                    save_ignored_records(source, to_ignore)
                    _new_set = set(st.session_state.get(f"{session_key}_ignored", set()))
                    _new_set.update(to_ignore)
                    st.session_state[f"{session_key}_ignored"] = _new_set
                    st.success(f"✅ {len(to_ignore)} record(s) marked as ignored and will be skipped in future imports.")
                    st.rerun()
            else:
                st.info("Check at least one box above, then click the button to save.")
        else:
            st.info("No new records available to add to the ignore list.")

        # ── Un-ignore previously saved keys ──────────────────────────────────
        if total_ignored:
            st.divider()
            st.markdown(f"**Currently ignored** — {total_ignored} record(s):")
            ig_sorted = sorted(ignored_descs)
            to_unignore = st.multiselect(
                "Select key(s) to remove from the ignore list:",
                ig_sorted,
                key=f"{session_key}_unignore_select",
            )
            if to_unignore:
                if st.button(
                    f"✅ Un-ignore {len(to_unignore)} record(s)",
                    key=f"{session_key}_unignore_btn",
                ):
                    from database.queries import remove_ignored_records
                    remove_ignored_records(source, to_unignore)
                    _new_set = set(st.session_state.get(f"{session_key}_ignored", set()))
                    _new_set -= set(to_unignore)
                    st.session_state[f"{session_key}_ignored"] = _new_set
                    st.success(f"✅ Removed {len(to_unignore)} key(s) from the ignore list.")
                    st.rerun()


# ===========================================================================
# Interactive Brokers — Flex Web Service
# ===========================================================================

def render_ib_import() -> None:
    st.markdown(
        "Import trades, dividends, interest and cash transfers directly from "
        "Interactive Brokers using the **Flex Web Service**."
    )

    with st.expander("ℹ️ One-time IB setup (click to expand)", expanded=False):
        st.markdown("""
1. Log in to [IB Client Portal](https://www.interactivebrokers.com) →
   **Reports → Flex Queries**.
2. Click **+** → *Activity Flex Query*.
3. Under **Sections**, enable:
   - **Trades** (buySell, assetCategory, symbol, description, currency,
     fxRateToBase, quantity, tradePrice, netCash, tradeDate, ibOrderID, isin)
   - **Cash Transactions** (type, currency, fxRateToBase, amount,
     description, symbol, dateTime, isin)
4. Date Format: `yyyyMMdd`  ·  Time Format: `HHmmss`  ·  Separator: `;`
5. **Save** — note the numeric **Query ID** shown next to the query name.
6. ⚠️ **Critical** — still on the same query: click **"Edit"** →
   scroll to the bottom → tick **"Allow Web Service Access"** → Save.
   *Without this flag the API always returns error 1019.*
7. Go to **Reports → Flex Web Service** → create / copy your **Token**.
""")

    st.divider()

    # ── Pre-populate credentials and account from saved settings (first render only) ──
    from database.queries import get_app_setting, save_app_setting
    if "ib_token" not in st.session_state:
        _saved_tok = get_app_setting("ib_flex_token") or ""
        st.session_state["ib_token"] = _saved_tok
        if _saved_tok:
            st.session_state.setdefault("ib_remember", True)
    if "ib_query_id" not in st.session_state:
        st.session_state["ib_query_id"] = get_app_setting("ib_flex_query_id") or ""
    if "ib_account" not in st.session_state:
        _saved_acc_id = get_app_setting("ib_account_id")
        if _saved_acc_id:
            _all_accs = _load_accounts()
            _ib_acc_types = ["Brokerage", "Margin", "Other Investment", "Pension"]
            _ib_accs = _all_accs[_all_accs["accounts_type"].isin(_ib_acc_types)]
            _ib_acc_row = _ib_accs[_ib_accs["accounts_id"] == int(_saved_acc_id)]
            if not _ib_acc_row.empty:
                st.session_state["ib_account"] = _ib_acc_row.iloc[0]["accounts_name"]
    if "ib_cash_account" not in st.session_state:
        _saved_cash_id = get_app_setting("ib_cash_account_id")
        if _saved_cash_id:
            try:
                st.session_state["ib_cash_account"] = int(_saved_cash_id)
            except (ValueError, TypeError):
                st.session_state["ib_cash_account"] = None
        else:
            st.session_state["ib_cash_account"] = None

    # ── Source: API vs paste ─────────────────────────────────────────────────
    st.markdown("### 📡 Statement Source")
    xml_source_mode = st.radio(
        "source_mode",
        ["🌐 Fetch via API (Token + Query ID)", "📋 Paste XML from IB portal"],
        horizontal=True,
        label_visibility="collapsed",
        key="ib_source_mode",
        help=(
            "Use 'Paste XML' if the API keeps returning error 1019 — "
            "download the statement manually from IB and paste it here."
        ),
    )

    raw_xml  = ""
    token    = ""
    query_id = ""
    remember = False

    if xml_source_mode.startswith("🌐"):
        col1, col2 = st.columns(2)
        with col1:
            token    = st.text_input(
                "Flex Token", type="password", key="ib_token",
                help="Long alphanumeric string from IB → Reports → Flex Web Service.",
            )
        with col2:
            query_id = st.text_input(
                "Query ID", key="ib_query_id",
                help="Numeric ID shown next to your Activity Flex Query.",
            )
        remember = st.checkbox(
            "💾 Remember credentials",
            key="ib_remember",
            help="Save Token and Query ID to the database so they are pre-filled on your next visit.",
        )
    else:
        st.caption(
            "Run your Flex Query on the IB website → choose **XML** output format → "
            "copy the entire XML text and paste it below."
        )
        raw_xml = st.text_area(
            "Flex XML",
            height=160,
            key="ib_raw_xml",
            placeholder="<FlexQueryResponse queryName=...>...</FlexQueryResponse>",
        )

    st.divider()

    # ── Account mapping ──────────────────────────────────────────────────────
    st.markdown("### 🏦 Account Mapping")
    acc_id, acc_name = _account_selectbox(
        "Import into account",
        key="ib_account",
        type_filter=["Brokerage", "Margin", "Other Investment", "Pension"],
    )

    st.markdown("#### 💵 Cash Account (optional)")
    st.caption(
        "If set, **Deposits/Withdrawals** and **Fees** from IB are routed to this "
        "account instead of the margin account above. Leave blank to keep the "
        "previous behaviour (all transactions in the margin account)."
    )
    _ib_inv_types = {"Brokerage", "Margin", "Other Investment", "Pension"}
    _cash_df = _load_accounts()
    _cash_df = _cash_df[~_cash_df["accounts_type"].isin(_ib_inv_types)]
    _cash_opt_ids: list = [None] + [int(r["accounts_id"]) for _, r in _cash_df.iterrows()]
    _cash_opt_labels: dict = {None: "— None (use margin account) —"}
    _cash_opt_labels.update(
        {int(r["accounts_id"]): r["accounts_name"] for _, r in _cash_df.iterrows()}
    )
    cash_acc_id = st.selectbox(
        "Cash Account for Deposits / Withdrawals / Fees",
        options=_cash_opt_ids,
        format_func=lambda x: _cash_opt_labels.get(x, str(x)),
        key="ib_cash_account",
        help="Select the dedicated IBRK cash account. Set Accounts → Linked Account to this account on the margin account.",
    )

    st.divider()

    # ── Date filter ──────────────────────────────────────────────────────────
    st.markdown("### 📅 Date Filter")
    st.caption(
        "IB's Flex API does not accept date parameters — the date range is "
        "configured inside the Flex Query on IB's website.  Use these filters "
        "to narrow the import to a specific period after the statement is fetched."
    )
    df_col1, df_col2 = st.columns(2)
    with df_col1:
        ib_filter_from = st.date_input(
            "Import from",
            value=None,
            key="ib_filter_from",
            help="Only import records on or after this date.  Leave blank for no lower limit.",
        )
    with df_col2:
        ib_filter_to = st.date_input(
            "Import to",
            value=None,
            key="ib_filter_to",
            help="Only import records on or before this date.  Leave blank for no upper limit.",
        )

    st.divider()

    # ── Options ──────────────────────────────────────────────────────────────
    st.markdown("### ⚙️ Options")
    replace_mode = st.checkbox(
        "Replace mode — delete all existing IB records for this account before importing",
        value=False, key="ib_replace",
        help="Use this to do a clean re-import.  All Investment and Transaction rows "
             "whose Description starts with 'IB|' will be deleted first.",
    )

    st.divider()

    # ── Fetch / Parse ────────────────────────────────────────────────────────
    _is_paste_mode = xml_source_mode.startswith("📋")
    _btn_label     = "🔍 Parse XML" if _is_paste_mode else "📡 Fetch & Preview"
    fetch_btn = st.button(_btn_label, key="ib_fetch",
                          type="primary", disabled=acc_id is None)

    if fetch_btn or st.session_state.get("ib_parsed"):
        from data.ib_flex_connector import (
            fetch_flex_xml, parse_flex_xml,
            check_existing_records, check_fuzzy_duplicates,
            preview_security_matches,
        )

        # ── Fetch (or use pasted XML) ────────────────────────────────────────
        if fetch_btn:
            xml_source = raw_xml.strip() if raw_xml and raw_xml.strip() else None

            if not xml_source:
                if _is_paste_mode:
                    st.error("Please paste the Flex XML above before parsing.")
                    return
                if not token.strip() or not query_id.strip():
                    st.error("Please enter both Token and Query ID.")
                    return

                # ── Save credentials at click time (before fetch) ─────────────
                # Do this now so credentials are persisted even if the fetch fails.
                if remember:
                    try:
                        save_app_setting("ib_flex_token",    token.strip())
                        save_app_setting("ib_flex_query_id", query_id.strip())
                    except Exception:
                        pass  # non-fatal

                status_box = st.empty()
                try:
                    def _cb(msg):
                        status_box.info(f"⏳ {msg}")

                    with st.spinner("Contacting Interactive Brokers…"):
                        xml_source = fetch_flex_xml(
                            token.strip(), query_id.strip(), progress_cb=_cb
                        )
                    status_box.empty()
                except Exception as exc:
                    err_str = str(exc)
                    st.error(f"Failed to fetch statement: {exc}")
                    if "could not be generated" in err_str.lower() or "1019" in err_str:
                        st.warning(
                            "💡 **Error 1019 — statement could not be generated.**\n\n"
                            "Switch to **📋 Paste XML** mode above: run the Flex Query "
                            "manually on IB's website (choose XML output format), "
                            "then paste the result here. No API token needed."
                        )
                    return

            # Always persist the selected accounts so they're pre-filled on next visit.
            if acc_id is not None:
                try:
                    save_app_setting("ib_account_id", str(acc_id))
                    save_app_setting("ib_cash_account_id",
                                     str(cash_acc_id) if cash_acc_id is not None else "")
                except Exception:
                    pass  # non-fatal

            try:
                inv_records, tx_records, meta = parse_flex_xml(xml_source)
            except Exception as exc:
                st.error(f"Failed to parse Flex XML: {exc}")
                st.code(traceback.format_exc())
                return

            # ── Reconciliation & securities matching (done once at fetch) ─────
            existing_inv, existing_tx = check_existing_records(
                inv_records, tx_records, acc_id, cash_account_id=cash_acc_id
            )
            fuzzy_inv, fuzzy_tx = check_fuzzy_duplicates(
                inv_records, tx_records, acc_id, cash_account_id=cash_acc_id
            )
            # Fuzzy set should only flag records NOT already caught by exact match
            fuzzy_inv -= existing_inv
            fuzzy_tx  -= existing_tx
            sec_matches = preview_security_matches(inv_records)

            from database.queries import get_ignored_records as _get_ignored
            ignored_descs = _get_ignored("Interactive Brokers")

            st.session_state["ib_inv_records"]  = inv_records
            st.session_state["ib_tx_records"]   = tx_records
            st.session_state["ib_meta"]         = meta
            st.session_state["ib_existing_inv"] = existing_inv
            st.session_state["ib_existing_tx"]  = existing_tx
            st.session_state["ib_fuzzy_inv"]    = fuzzy_inv
            st.session_state["ib_fuzzy_tx"]     = fuzzy_tx
            st.session_state["ib_sec_matches"]  = sec_matches
            st.session_state["ib_ignored"]      = ignored_descs
            st.session_state["ib_parsed"]       = True

        inv_records   = st.session_state.get("ib_inv_records",  [])
        tx_records    = st.session_state.get("ib_tx_records",   [])
        meta          = st.session_state.get("ib_meta",         {})
        existing_inv  = st.session_state.get("ib_existing_inv", set())
        existing_tx   = st.session_state.get("ib_existing_tx",  set())
        fuzzy_inv     = st.session_state.get("ib_fuzzy_inv",    set())
        fuzzy_tx      = st.session_state.get("ib_fuzzy_tx",     set())
        sec_matches   = st.session_state.get("ib_sec_matches",  {})
        ignored_descs = st.session_state.get("ib_ignored",      set())

        # ── Statement summary ─────────────────────────────────────────────────
        if meta:
            m1, m2, m3 = st.columns(3)
            m1.metric("IB Account",     meta.get("account_id", "—"))
            m2.metric("Statement from", str(meta.get("from_date", "—")))
            m3.metric("Statement to",   str(meta.get("to_date", "—")))

        # ── Apply date filter ────────────────────────────────────────────────
        def _in_range(r: dict) -> bool:
            d = r.get("date")
            if d is None:
                return True
            if isinstance(d, str):
                try:
                    from datetime import date as _dt
                    d = _dt.fromisoformat(d)
                except ValueError:
                    return True
            if ib_filter_from and d < ib_filter_from:
                return False
            if ib_filter_to and d > ib_filter_to:
                return False
            return True

        inv_total   = len(inv_records)
        tx_total    = len(tx_records)
        inv_records = [r for r in inv_records if _in_range(r)]
        tx_records  = [r for r in tx_records  if _in_range(r)]

        if (ib_filter_from or ib_filter_to) and (inv_total + tx_total) > 0:
            hidden = (inv_total - len(inv_records)) + (tx_total - len(tx_records))
            if hidden:
                _range_parts = []
                if ib_filter_from:
                    _range_parts.append(f"from {ib_filter_from}")
                if ib_filter_to:
                    _range_parts.append(f"to {ib_filter_to}")
                st.info(
                    f"📅 Date filter active ({', '.join(_range_parts)}) — "
                    f"**{hidden}** record(s) outside the range excluded."
                )

        if not inv_records and not tx_records:
            st.warning("No importable records found in this statement.")
            with st.expander("🔍 Diagnostics — why is the statement empty?"):
                trade_els = meta.get("diag_trade_els", "?")
                cash_els  = meta.get("diag_cash_els",  "?")
                sections  = meta.get("diag_sections",  [])
                st.markdown(f"""
**Raw elements found in the Flex XML**

| Section | Elements found |
|---------|---------------|
| `<Trade>` (raw, before filtering) | **{trade_els}** |
| `<CashTransaction>` (raw, before filtering) | **{cash_els}** |

**XML sections present in this statement:**
{", ".join(f"`{s}`" for s in sections) if sections else "_(none detected)_"}

**Common reasons for an empty import:**

1. **Date filter too narrow** — Check the *Import from / Import to* fields
   above.  If the Flex Query covers a wide range but the filter cuts it down
   to a period with no activity, all records will be excluded.

2. **No activity on this date range** — The Flex Query covers a period
   with no trades or cash transactions (e.g. a weekend or holiday).
   → Try a wider date range in your IB Flex Query settings.

3. **Sections not enabled in your Flex Query** — Both **Trades** and
   **Cash Transactions** must be enabled under *Sections* when creating
   the Flex Query.  Check *Reports → Flex Queries → Edit*.

4. **`buySell` attribute value not recognised** — If you see trades above
   but they're not imported, the `buySell` field may contain an unexpected
   value.  Use *"Paste XML"* mode (above) and share the raw XML
   so the parser can be updated.

5. **Today's date not yet settled** — IB typically makes intraday data
   available in Flex statements after market close.  Try again tomorrow or
   choose *"Last N days"* in the Flex Query date settings.
""")
            return

        # ── Reconciliation summary ─────────────────────────────────────────────
        truly_new_inv   = [r for r in inv_records
                           if r["desc"] not in existing_inv
                           and r["desc"] not in fuzzy_inv
                           and r["desc"] not in ignored_descs]
        truly_new_tx    = [r for r in tx_records
                           if r["desc"] not in existing_tx
                           and r["desc"] not in fuzzy_tx
                           and r["desc"] not in ignored_descs]
        fuzzy_only_inv  = [r for r in inv_records if r["desc"] in fuzzy_inv]
        fuzzy_only_tx   = [r for r in tx_records  if r["desc"] in fuzzy_tx]
        exist_inv_count = sum(1 for r in inv_records if r["desc"] in existing_inv)
        exist_tx_count  = sum(1 for r in tx_records  if r["desc"] in existing_tx)
        ignored_inv_count = sum(1 for r in inv_records if r["desc"] in ignored_descs)
        ignored_tx_count  = sum(1 for r in tx_records  if r["desc"] in ignored_descs)

        _skip_inv = exist_inv_count + len(fuzzy_only_inv)
        _skip_tx  = exist_tx_count  + len(fuzzy_only_tx)

        if not truly_new_inv and not truly_new_tx and not replace_mode:
            st.info(
                f"✅ Nothing genuinely new — "
                f"**{exist_inv_count}** inv exact + **{len(fuzzy_only_inv)}** likely-dup, "
                f"**{exist_tx_count}** tx exact + **{len(fuzzy_only_tx)}** likely-dup."
            )
        else:
            st.success(
                f"Found **{len(truly_new_inv)}** new investment record(s) and "
                f"**{len(truly_new_tx)}** new transaction(s) to import."
            )
        if fuzzy_only_inv or fuzzy_only_tx:
            st.warning(
                f"⚠️ **{len(fuzzy_only_inv)}** investment(s) and "
                f"**{len(fuzzy_only_tx)}** transaction(s) match existing records by "
                "date/action/amount but have a different description key — "
                "likely already entered manually or via another importer. "
                "They are marked **⚠️ Likely duplicate** in the preview and will be skipped."
            )
        if ignored_inv_count or ignored_tx_count:
            st.info(
                f"⏭️ **{ignored_inv_count}** investment(s) and "
                f"**{ignored_tx_count}** transaction(s) are on the ignore list and will be skipped. "
                "Use the **Ignore Records** panel below to manage them."
            )

        r1, r2, r3, r4 = st.columns(4)
        r1.metric("🆕 New investments",   len(truly_new_inv))
        r2.metric("🔄 Skip investments",  _skip_inv + ignored_inv_count,
                  help="✅ Exact key match + ⚠️ Likely duplicates + ⏭️ Ignored")
        r3.metric("🆕 New transactions",  len(truly_new_tx))
        r4.metric("🔄 Skip transactions", _skip_tx + ignored_tx_count,
                  help="✅ Exact key match + ⚠️ Likely duplicates + ⏭️ Ignored")

        # ── Preview ──────────────────────────────────────────────────────────
        st.markdown("### 👁️ Preview")
        _ib_preview_with_status(
            inv_records, tx_records,
            existing_inv, existing_tx,
            fuzzy_inv, fuzzy_tx,
            sec_matches,
            ignored_descs=ignored_descs,
        )

        # ── Security Mappings ─────────────────────────────────────────────────
        _ib_security_mapping_ui(sec_matches)

        # ── Ignore manager ────────────────────────────────────────────────────
        _ignore_manager_ui(
            "Interactive Brokers",
            inv_records, tx_records,
            existing_inv, existing_tx,
            fuzzy_inv, fuzzy_tx,
            ignored_descs,
            session_key="ib",
        )

        # ── Import ───────────────────────────────────────────────────────────
        st.divider()
        st.markdown("### 💾 Import")
        _imp_caption = f"Investments → **{acc_name}** (ID {acc_id})"
        if cash_acc_id:
            _imp_caption += f"  ·  Cash → **{_cash_opt_labels.get(cash_acc_id, str(cash_acc_id))}**"
        st.caption(_imp_caption)

        _icol1, _icol2 = st.columns(2)
        _import_inv = _icol1.checkbox("📈 Import investments",       value=True, key="ib_import_inv")
        _import_tx  = _icol2.checkbox("💳 Import cash transactions", value=True, key="ib_import_tx")

        _base_inv  = truly_new_inv if not replace_mode else inv_records
        _base_tx   = truly_new_tx  if not replace_mode else tx_records
        _imp_inv   = _base_inv if _import_inv else []
        _imp_tx    = _base_tx  if _import_tx  else []
        _new_total = len(_imp_inv) + len(_imp_tx)

        if _new_total == 0 and not replace_mode:
            st.info(
                "No records to import.  "
                "Enable **Replace mode** above if you want to force a clean re-import."
            )
        else:
            _btn_suffix = (
                f" ({_new_total} record{'s' if _new_total != 1 else ''})"
                if not replace_mode else " (replace mode)"
            )
            if st.button(f"✅ Confirm Import{_btn_suffix}", key="ib_confirm", type="primary"):
                from data.ib_flex_connector import run_import
                prog = st.progress(0.0, text="Importing…")
                try:
                    counts = run_import(
                        _imp_inv, _imp_tx, acc_id,
                        cash_account_id=cash_acc_id,
                        replace_mode=replace_mode,
                        progress_cb=lambda p: prog.progress(p, text="Importing…"),
                    )
                    prog.empty()
                    st.success("✅ Import complete!")
                    _import_summary(counts)
                    # Clear parsed state so next visit starts fresh
                    for k in ("ib_parsed", "ib_inv_records", "ib_tx_records", "ib_meta",
                              "ib_existing_inv", "ib_existing_tx",
                              "ib_fuzzy_inv", "ib_fuzzy_tx", "ib_sec_matches", "ib_ignored"):
                        st.session_state.pop(k, None)
                    _load_accounts.clear()
                    st.cache_data.clear()
                except Exception as exc:
                    prog.empty()
                    st.error(f"Import failed: {exc}")
                    st.code(traceback.format_exc())


# ===========================================================================
# Revolut Trading — brokerage CSV export
# ===========================================================================

def render_revolut_brokerage_import() -> None:
    st.markdown(
        "Import trades, dividends and cash events from a **Revolut Trading** "
        "(stocks/ETF brokerage) account using the CSV export from the Revolut app."
    )

    # ── Restore last-used account (first render only) ────────────────────────
    from database.queries import get_app_setting, save_app_setting
    if "revt_account" not in st.session_state:
        _saved = get_app_setting("revt_account_id")
        if _saved:
            try:
                _row = _load_accounts()
                _row = _row[_row["accounts_id"] == int(_saved)]
                if not _row.empty:
                    st.session_state["revt_account"] = _row.iloc[0]["accounts_name"]
            except Exception:
                pass
    if "revt_replace" not in st.session_state:
        st.session_state["revt_replace"] = get_app_setting("revt_replace") == "true"
    if "revt_import_inv" not in st.session_state:
        _v = get_app_setting("revt_import_inv")
        st.session_state["revt_import_inv"] = (_v != "false")  # default True
    if "revt_import_tx" not in st.session_state:
        _v = get_app_setting("revt_import_tx")
        st.session_state["revt_import_tx"] = (_v != "false")   # default True

    with st.expander("ℹ️ How to export from Revolut Trading (click to expand)", expanded=False):
        st.markdown("""
1. Open the **Revolut app** → tap **Stocks** (or **Investing**).
2. Tap the **clock / History** icon (top-right).
3. Tap the **↓ Download** / **Export** button.
4. Choose format **CSV** and a date range → tap **Download**.
5. Upload the downloaded `.csv` file below.

> **CSV columns:** Date · Ticker · Type · Quantity · Price per share ·
> Total amount · Currency · FX Rate
>
> **Supported types:** BUY · SELL · DIVIDEND · CUSTODY FEE ·
> CASH TOP-UP · CASH WITHDRAWAL
""")

    st.divider()

    # ── File upload ──────────────────────────────────────────────────────────
    st.markdown("### 📂 Statement File")
    uploaded = st.file_uploader(
        "Upload Revolut Trading CSV export",
        type=["csv"],
        key="revt_csv_upload",
        help="Export from Revolut → Stocks → History → Export CSV",
    )

    st.divider()

    # ── Account mapping ──────────────────────────────────────────────────────
    st.markdown("### 🏦 Account Mapping")
    acc_id, acc_name = _account_selectbox(
        "Import into account",
        key="revt_account",
        type_filter=["Brokerage", "Margin", "Other Investment", "Pension"],
    )

    st.divider()

    # ── Options ──────────────────────────────────────────────────────────────
    st.markdown("### ⚙️ Options")
    replace_mode = st.checkbox(
        "Replace mode — delete all existing Revolut Trading records for this account before importing",
        value=False, key="revt_replace",
        help="All Investment and Transaction rows whose Description starts with "
             "'REVT|' will be deleted first.",
    )

    st.divider()

    if uploaded is None:
        st.info("Upload a Revolut Trading CSV file above to continue.")
        return

    # ── Parse ────────────────────────────────────────────────────────────────
    parse_btn = st.button("🔍 Parse & Preview", key="revt_parse", type="primary",
                          disabled=acc_id is None)

    if parse_btn or st.session_state.get("revt_parsed"):
        from data.revolut_importer import (
            parse_revolut_trading_csv, build_trading_records,
            check_existing_records as _revt_check,
            check_fuzzy_duplicates as _revt_fuzzy,
            preview_security_matches as _revt_sec_matches,
        )

        if parse_btn:
            try:
                df_raw = parse_revolut_trading_csv(uploaded.read())
            except Exception as exc:
                st.error(f"Failed to parse CSV: {exc}")
                return

            try:
                inv_records, tx_records = build_trading_records(df_raw)
            except Exception as exc:
                st.error(f"Failed to build records: {exc}")
                st.code(traceback.format_exc())
                return

            # ── Reconciliation & securities matching (done once at parse) ──
            existing_inv, existing_tx = _revt_check(inv_records, tx_records, acc_id)
            fuzzy_inv, fuzzy_tx       = _revt_fuzzy(inv_records, tx_records, acc_id)
            fuzzy_inv -= existing_inv
            fuzzy_tx  -= existing_tx
            sec_matches = _revt_sec_matches(inv_records)

            from database.queries import get_ignored_records as _get_ignored_revt
            ignored_descs = _get_ignored_revt("Revolut Trading")

            st.session_state["revt_inv_records"]  = inv_records
            st.session_state["revt_tx_records"]   = tx_records
            st.session_state["revt_df_raw"]       = df_raw
            st.session_state["revt_existing_inv"] = existing_inv
            st.session_state["revt_existing_tx"]  = existing_tx
            st.session_state["revt_fuzzy_inv"]    = fuzzy_inv
            st.session_state["revt_fuzzy_tx"]     = fuzzy_tx
            st.session_state["revt_sec_matches"]  = sec_matches
            st.session_state["revt_ignored"]      = ignored_descs
            st.session_state["revt_parsed"]       = True

        inv_records   = st.session_state.get("revt_inv_records",  [])
        tx_records    = st.session_state.get("revt_tx_records",   [])
        df_raw        = st.session_state.get("revt_df_raw",       pd.DataFrame())
        existing_inv  = st.session_state.get("revt_existing_inv", set())
        existing_tx   = st.session_state.get("revt_existing_tx",  set())
        fuzzy_inv     = st.session_state.get("revt_fuzzy_inv",    set())
        fuzzy_tx      = st.session_state.get("revt_fuzzy_tx",     set())
        sec_matches   = st.session_state.get("revt_sec_matches",  {})
        ignored_descs = st.session_state.get("revt_ignored",      set())

        # ── File summary ──────────────────────────────────────────────────
        if not df_raw.empty:
            s1, s2, s3 = st.columns(3)
            s1.metric("Rows in file",    len(df_raw))
            s2.metric("Date range from", str(df_raw["date"].min()))
            s3.metric("Date range to",   str(df_raw["date"].max()))

        if not inv_records and not tx_records:
            st.warning("No importable records found in this file.")
            return

        # ── Transaction type breakdown ─────────────────────────────────────
        if not df_raw.empty:
            with st.expander("📊 Transaction type breakdown"):
                type_counts = (
                    df_raw.groupby("raw_type")["total_amount"]
                    .agg(count="count", total="sum")
                    .reset_index()
                    .sort_values("count", ascending=False)
                )
                st.dataframe(
                    type_counts,
                    hide_index=True,
                    width="stretch",
                    column_config={
                        "raw_type": "Type",
                        "count":    st.column_config.NumberColumn("# Rows",    format="%d"),
                        "total":    st.column_config.NumberColumn("Net Amount", format="%.2f"),
                    },
                )

        # ── Reconciliation summary ─────────────────────────────────────────
        truly_new_inv   = [r for r in inv_records
                           if r["desc"] not in existing_inv
                           and r["desc"] not in fuzzy_inv
                           and r["desc"] not in ignored_descs]
        truly_new_tx    = [r for r in tx_records
                           if r["desc"] not in existing_tx
                           and r["desc"] not in fuzzy_tx
                           and r["desc"] not in ignored_descs]
        fuzzy_only_inv  = [r for r in inv_records if r["desc"] in fuzzy_inv]
        fuzzy_only_tx   = [r for r in tx_records  if r["desc"] in fuzzy_tx]
        exist_inv_count = sum(1 for r in inv_records if r["desc"] in existing_inv)
        exist_tx_count  = sum(1 for r in tx_records  if r["desc"] in existing_tx)
        ignored_inv_count = sum(1 for r in inv_records if r["desc"] in ignored_descs)
        ignored_tx_count  = sum(1 for r in tx_records  if r["desc"] in ignored_descs)

        _skip_inv = exist_inv_count + len(fuzzy_only_inv)
        _skip_tx  = exist_tx_count  + len(fuzzy_only_tx)

        if not truly_new_inv and not truly_new_tx and not replace_mode:
            st.info(
                f"✅ Nothing genuinely new — "
                f"**{exist_inv_count}** inv exact + **{len(fuzzy_only_inv)}** likely-dup, "
                f"**{exist_tx_count}** tx exact + **{len(fuzzy_only_tx)}** likely-dup."
            )
        else:
            st.success(
                f"Found **{len(truly_new_inv)}** new investment record(s) and "
                f"**{len(truly_new_tx)}** new transaction(s) to import."
            )
        if fuzzy_only_inv or fuzzy_only_tx:
            st.warning(
                f"⚠️ **{len(fuzzy_only_inv)}** investment(s) and "
                f"**{len(fuzzy_only_tx)}** transaction(s) match existing records by "
                "date/action/amount but have a different description key — "
                "likely already entered manually or via another importer. "
                "They are marked **⚠️ Likely duplicate** in the preview and will be skipped."
            )
        if ignored_inv_count or ignored_tx_count:
            st.info(
                f"⏭️ **{ignored_inv_count}** investment(s) and "
                f"**{ignored_tx_count}** transaction(s) are on the ignore list and will be skipped. "
                "Use the **Ignore Records** panel below to manage them."
            )

        r1, r2, r3, r4 = st.columns(4)
        r1.metric("🆕 New investments",   len(truly_new_inv))
        r2.metric("🔄 Skip investments",  _skip_inv + ignored_inv_count,
                  help="✅ Exact key match + ⚠️ Likely duplicates + ⏭️ Ignored")
        r3.metric("🆕 New transactions",  len(truly_new_tx))
        r4.metric("🔄 Skip transactions", _skip_tx + ignored_tx_count,
                  help="✅ Exact key match + ⚠️ Likely duplicates + ⏭️ Ignored")

        # ── Preview ──────────────────────────────────────────────────────────
        st.markdown("### 👁️ Preview")
        _revt_preview_with_status(
            inv_records, tx_records,
            existing_inv, existing_tx,
            fuzzy_inv, fuzzy_tx,
            sec_matches,
            ignored_descs=ignored_descs,
        )

        # ── Security Mappings ─────────────────────────────────────────────────
        _revt_security_mapping_ui(sec_matches)

        # ── Ignore manager ────────────────────────────────────────────────────
        _ignore_manager_ui(
            "Revolut Trading",
            inv_records, tx_records,
            existing_inv, existing_tx,
            fuzzy_inv, fuzzy_tx,
            ignored_descs,
            session_key="revt",
        )

        # ── Import ───────────────────────────────────────────────────────────
        st.divider()
        st.markdown("### 💾 Import")
        st.caption(f"Target account: **{acc_name}** (ID {acc_id})")

        _rcol1, _rcol2 = st.columns(2)
        _import_inv = _rcol1.checkbox("📈 Import investments",       value=True, key="revt_import_inv")
        _import_tx  = _rcol2.checkbox("💳 Import cash transactions", value=True, key="revt_import_tx")

        _base_inv  = truly_new_inv if not replace_mode else inv_records
        _base_tx   = truly_new_tx  if not replace_mode else tx_records
        _imp_inv   = _base_inv if _import_inv else []
        _imp_tx    = _base_tx  if _import_tx  else []
        _new_total = len(_imp_inv) + len(_imp_tx)

        if _new_total == 0 and not replace_mode:
            st.info(
                "No records to import.  "
                "Enable **Replace mode** above if you want to force a clean re-import."
            )
        else:
            _btn_suffix = (
                f" ({_new_total} record{'s' if _new_total != 1 else ''})"
                if not replace_mode else " (replace mode)"
            )
            if st.button(f"✅ Confirm Import{_btn_suffix}", key="revt_confirm", type="primary"):
                from data.revolut_importer import run_trading_import
                prog = st.progress(0.0, text="Importing…")
                try:
                    counts = run_trading_import(
                        _imp_inv, _imp_tx, acc_id,
                        replace_mode=replace_mode,
                        progress_cb=lambda p: prog.progress(p, text="Importing…"),
                    )
                    prog.empty()
                    st.success("✅ Import complete!")
                    _import_summary(counts)
                    try:
                        save_app_setting("revt_account_id", str(acc_id))
                        save_app_setting("revt_replace",    "true" if replace_mode else "false")
                        save_app_setting("revt_import_inv", "true" if _import_inv  else "false")
                        save_app_setting("revt_import_tx",  "true" if _import_tx   else "false")
                    except Exception:
                        pass
                    for k in ("revt_parsed", "revt_inv_records", "revt_tx_records",
                              "revt_df_raw", "revt_existing_inv", "revt_existing_tx",
                              "revt_fuzzy_inv", "revt_fuzzy_tx", "revt_sec_matches",
                              "revt_ignored"):
                        st.session_state.pop(k, None)
                    _load_accounts.clear()
                    st.cache_data.clear()
                except Exception as exc:
                    prog.empty()
                    st.error(f"Import failed: {exc}")
                    st.code(traceback.format_exc())


# ===========================================================================
# Revolut Savings — preview helper with reconciliation status
# ===========================================================================

def _revs_preview_with_status(
    inv_records: list,
    tx_records: list,
    existing_inv: set,
    existing_tx: set,
    fuzzy_inv: set,
    fuzzy_tx: set,
    ignored_descs: set | None = None,
) -> None:
    """Preview table for Revolut Savings records with 4-state status.

    Simpler than Trading/IB preview — the security is always the same
    money-market fund (ISIN IE000AZVL3K0), so no per-record mapping column.
    """
    ignored_descs = ignored_descs or set()
    tab_inv, tab_tx = st.tabs([
        f"📈 Investments ({len(inv_records)})",
        f"💳 Cash Transactions ({len(tx_records)})",
    ])

    with tab_inv:
        if inv_records:
            rows = []
            for r in inv_records:
                if r["desc"] in existing_inv:
                    status = "✅ Exists"
                elif r["desc"] in fuzzy_inv:
                    status = "⚠️ Likely duplicate"
                elif r["desc"] in ignored_descs:
                    status = "⏭️ Ignored"
                else:
                    status = "🆕 New"
                rows.append({**r, "status": status})
            df_inv = pd.DataFrame(rows)
            cols = ["status", "date", "action", "symbol", "name",
                    "quantity", "price", "total_eur", "currency",
                    "asset_category", "desc"]
            df_inv = df_inv[[c for c in cols if c in df_inv.columns]]
            st.dataframe(
                df_inv, hide_index=True, width="stretch",
                column_config={
                    "status":         "Status",
                    "date":           "Date",
                    "action":         "Action",
                    "symbol":         "Symbol / ISIN",
                    "name":           "Name",
                    "quantity":       st.column_config.NumberColumn("Qty",       format="%.4f"),
                    "price":          st.column_config.NumberColumn("Price",     format="%.6f"),
                    "total_eur":      st.column_config.NumberColumn("Total (€)", format="%.4f"),
                    "currency":       "Ccy",
                    "asset_category": "Asset",
                    "desc":           "Dedup Key",
                },
            )
        else:
            st.info("No investment records in this file.")

    with tab_tx:
        if tx_records:
            rows = []
            for r in tx_records:
                if r["desc"] in existing_tx:
                    status = "✅ Exists"
                elif r["desc"] in fuzzy_tx:
                    status = "⚠️ Likely duplicate"
                elif r["desc"] in ignored_descs:
                    status = "⏭️ Ignored"
                else:
                    status = "🆕 New"
                rows.append({**r, "status": status})
            df_tx = pd.DataFrame(rows)
            cols = ["status", "date", "description", "amount", "currency"]
            df_tx = df_tx[[c for c in cols if c in df_tx.columns]]
            st.dataframe(
                df_tx, hide_index=True, width="stretch",
                column_config={
                    "status":      "Status",
                    "date":        "Date",
                    "description": "Description",
                    "amount":      st.column_config.NumberColumn("Amount", format="%.2f"),
                    "currency":    "Ccy",
                },
            )
        else:
            st.info("No cash transaction records in this file.")


# ===========================================================================
# Security Mapping UI  (Revolut Savings)
# ===========================================================================

def _revs_security_mapping_ui() -> None:
    """Single-security mapping UI for Revolut Savings (Investment mode only).

    Called only when preview_savings_security() returns None — i.e. the fund
    ISIN IE000AZVL3K0 is not yet in the database.  Shows one selectbox to pin
    the ISIN to an existing DB security and a save button that persists the
    mapping via import_security_mappings so future imports resolve it instantly.
    """
    _SAVINGS_ISIN = "IE000AZVL3K0"

    with st.expander(
        "🗺️ Security Mapping — ISIN IE000AZVL3K0 not matched — click to configure",
        expanded=True,
    ):
        st.caption(
            "The Revolut EUR Money Market Fund (ISIN **IE000AZVL3K0**) was not found "
            "in your Securities database.  Select the matching security below to create "
            "a permanent mapping, or leave as *(create new)* to have it added automatically "
            "on import."
        )

        all_secs = _load_all_securities()
        if all_secs.empty:
            st.warning("No securities found in the database. Create one in Static Data first.")
            return

        sec_options = ["(create new — will be added on import)"] + all_secs["name"].tolist()
        chosen = st.selectbox(
            "Map IE000AZVL3K0 (Revolut EUR Money Market Fund) to:",
            sec_options,
            key="revs_map_security",
        )

        if not chosen.startswith("(create new"):
            sec_row = all_secs[all_secs["name"] == chosen]
            if not sec_row.empty:
                sec_id = int(sec_row.iloc[0]["securities_id"])
                if st.button("💾 Save Mapping", key="revs_save_mapping", type="primary"):
                    from database.queries import save_security_mappings as _save_map
                    try:
                        _save_map("Revolut Savings", {_SAVINGS_ISIN: sec_id})
                        _load_all_securities.clear()
                        st.success(
                            f"✅ Mapping saved: `{_SAVINGS_ISIN}` → **{chosen}**. "
                            "Re-parse to see the updated security confirmation above."
                        )
                        st.rerun()
                    except Exception as _exc:
                        st.error(f"Failed to save mapping: {_exc}")
        else:
            st.info(
                "A new security will be created automatically during import. "
                "Select an existing one above if you prefer to link to a security "
                "already in your database (e.g. the Fidelity fund you may have added "
                "under a different ticker)."
            )


# ===========================================================================
# Revolut Savings — main render function  (called from ui/bank_import.py)
# ===========================================================================

def render_revolut_savings_import() -> None:
    _brand_header("https://logo.clearbit.com/revolut.com", "Revolut Savings")
    st.markdown(
        "Import buys, daily interest and service fees from a **Revolut Savings** "
        "(Flexible Cash Funds / money-market) account using the savings statement CSV."
    )

    # ── Restore last-used settings (first render only) ───────────────────────
    from database.queries import get_app_setting, save_app_setting
    if "revs_account" not in st.session_state:
        _saved = get_app_setting("revs_account_id")
        if _saved:
            try:
                _row = _load_accounts()
                _row = _row[_row["accounts_id"] == int(_saved)]
                if not _row.empty:
                    st.session_state["revs_account"] = _row.iloc[0]["accounts_name"]
            except Exception:
                pass
    if "revs_mode" not in st.session_state:
        _saved_mode = get_app_setting("revs_mode")
        if _saved_mode:
            st.session_state["revs_mode"] = _saved_mode
    if "revs_replace" not in st.session_state:
        st.session_state["revs_replace"] = get_app_setting("revs_replace") == "true"

    with st.expander("ℹ️ How to export from Revolut Savings (click to expand)", expanded=False):
        st.markdown("""
1. Open the **Revolut app** → tap **Savings** (or **Flexible account**).
2. Tap the **⋮** (three-dot) menu → **Statement**.
3. Choose a date range → tap **Download CSV**.
4. Upload the downloaded `.csv` file below.

> **CSV columns:** Date · Description · Value, EUR · Price per share · Quantity of shares
>
> **Supported types:** BUY · Interest PAID · Service Fee Charged
>
> **Note:** *Interest Reinvested* rows are skipped — the paired BUY on the
> following day already records the reinvestment as a fund purchase.
""")

    st.divider()

    # ── Import mode ──────────────────────────────────────────────────────────
    st.markdown("### 🔀 Import Mode")
    _MODE_TX  = "💳 Transaction mode — cash in/out (for Savings / Checking accounts)"
    _MODE_INV = "📈 Investment mode  — fund units  (for Brokerage / Investment accounts)"
    import_mode = st.radio(
        "How should Revolut Savings records be stored?",
        [_MODE_TX, _MODE_INV],
        key="revs_mode",
        help=(
            "**Transaction mode** — maps all events to plain cash transactions visible "
            "in the standard account register. Best if you already have this account "
            "defined as *Savings* and treat it as a cash savings pot.\n\n"
            "**Investment mode** — creates Investment records (Buy / Dividend / MiscExp) "
            "tracking fund units at NAV ≈ €1.00. Requires a *Brokerage* or *Other Investment* "
            "account type so the investment register shows the records."
        ),
    )
    _tx_mode = import_mode == _MODE_TX

    if _tx_mode:
        st.info(
            "💳 **Transaction mode:** BUY → deposit, Interest PAID → income, "
            "Service Fee → expense. All visible in the standard Transactions register."
        )
    else:
        st.info(
            "📈 **Investment mode:** records fund units (ISIN IE000AZVL3K0) as "
            "Buy / Reinvest / Sell in the Investments table. "
            "Make sure to select a *Brokerage* or *Other Investment* account below."
        )

    st.divider()

    # ── File upload ──────────────────────────────────────────────────────────
    st.markdown("### 📂 Statement File")
    uploaded = st.file_uploader(
        "Upload Revolut Savings CSV statement",
        type=["csv"],
        key="revs_csv_upload",
        help="Export from Revolut app → Savings → ⋮ → Statement → CSV",
    )

    st.divider()

    # ── Account mapping (filter by mode) ────────────────────────────────────
    st.markdown("### 🏦 Account Mapping")
    if _tx_mode:
        _acc_types = ["Checking", "Savings", "Cash", "Other"]
        _acc_hint  = "Select the Savings / Checking account this fund is linked to."
    else:
        _acc_types = ["Brokerage", "Other Investment", "Pension", "Savings"]
        _acc_hint  = "Select a Brokerage or Investment account. Investment records will not appear in Savings account registers."
    acc_id, acc_name = _account_selectbox(
        "Import into account",
        key="revs_account",
        type_filter=_acc_types,
    )
    if _acc_hint:
        st.caption(_acc_hint)

    st.divider()

    # ── Options ──────────────────────────────────────────────────────────────
    st.markdown("### ⚙️ Options")
    replace_mode = st.checkbox(
        "Replace mode — delete all existing Revolut Savings records for this account before importing",
        value=False, key="revs_replace",
        help="All Investment and Transaction rows whose Description starts with "
             "'REVS|' will be deleted first.",
    )

    st.divider()

    if uploaded is None:
        st.info("Upload a Revolut Savings CSV file above to continue.")
        return

    # ── Parse ────────────────────────────────────────────────────────────────
    parse_btn = st.button("🔍 Parse & Preview", key="revs_parse", type="primary",
                          disabled=acc_id is None)

    # Clear stale parsed state when mode changes so re-parse is always fresh
    _cached_mode = st.session_state.get("revs_parsed_mode")
    if _cached_mode is not None and _cached_mode != import_mode:
        for _k in ("revs_parsed", "revs_inv_records", "revs_tx_records", "revs_df_raw",
                   "revs_existing_inv", "revs_existing_tx",
                   "revs_fuzzy_inv", "revs_fuzzy_tx", "revs_ignored"):
            st.session_state.pop(_k, None)

    if parse_btn or st.session_state.get("revs_parsed"):
        from data.revolut_importer import (
            parse_revolut_savings_csv,
            build_savings_records, build_savings_records_as_tx,
            check_existing_records as _revs_check,
            check_fuzzy_duplicates  as _revs_fuzzy,
        )

        if parse_btn:
            try:
                df_raw = parse_revolut_savings_csv(uploaded.read())
            except Exception as exc:
                st.error(f"Failed to parse CSV: {exc}")
                return

            try:
                _builder = build_savings_records_as_tx if _tx_mode else build_savings_records
                inv_records, tx_records = _builder(df_raw)
            except Exception as exc:
                st.error(f"Failed to build records: {exc}")
                st.code(traceback.format_exc())
                return

            existing_inv, existing_tx = _revs_check(inv_records, tx_records, acc_id)
            fuzzy_inv, fuzzy_tx       = _revs_fuzzy(inv_records, tx_records, acc_id)
            fuzzy_inv -= existing_inv
            fuzzy_tx  -= existing_tx

            from database.queries import get_ignored_records as _get_ign_revs
            ignored_descs = _get_ign_revs("Revolut Savings")

            st.session_state["revs_inv_records"]  = inv_records
            st.session_state["revs_tx_records"]   = tx_records
            st.session_state["revs_df_raw"]       = df_raw
            st.session_state["revs_existing_inv"] = existing_inv
            st.session_state["revs_existing_tx"]  = existing_tx
            st.session_state["revs_fuzzy_inv"]    = fuzzy_inv
            st.session_state["revs_fuzzy_tx"]     = fuzzy_tx
            st.session_state["revs_ignored"]      = ignored_descs
            st.session_state["revs_parsed"]       = True
            st.session_state["revs_parsed_mode"]  = import_mode

        inv_records   = st.session_state.get("revs_inv_records",  [])
        tx_records    = st.session_state.get("revs_tx_records",   [])
        df_raw        = st.session_state.get("revs_df_raw",       pd.DataFrame())
        existing_inv  = st.session_state.get("revs_existing_inv", set())
        existing_tx   = st.session_state.get("revs_existing_tx",  set())
        fuzzy_inv     = st.session_state.get("revs_fuzzy_inv",    set())
        fuzzy_tx      = st.session_state.get("revs_fuzzy_tx",     set())
        ignored_descs = st.session_state.get("revs_ignored",      set())

        # ── File summary ──────────────────────────────────────────────────
        if not df_raw.empty:
            s1, s2, s3 = st.columns(3)
            s1.metric("Rows in file",    len(df_raw))
            s2.metric("Date range from", str(df_raw["date"].min()))
            s3.metric("Date range to",   str(df_raw["date"].max()))

        if not inv_records and not tx_records:
            st.warning("No importable records found in this file.")
            return

        # ── Security preview (Investment mode only) ────────────────────────
        if not _tx_mode:
            from data.revolut_importer import preview_savings_security as _prev_sec
            _sec_info = _prev_sec()
            if _sec_info:
                _match_icon  = {"mapped": "🗺️", "isin": "🔗", "ticker": "🔗"}.get(
                    _sec_info["match_type"], "🔗"
                )
                _match_label = {
                    "mapped": "saved mapping",
                    "isin":   "ISIN IE000AZVL3K0",
                    "ticker": "ticker match",
                }.get(_sec_info["match_type"], "ISIN match")
                st.success(
                    f"{_match_icon} **Security resolved:** **{_sec_info['name']}** "
                    f"(`{_sec_info['ticker']}`) — matched by {_match_label}. "
                    "All investment records will be linked to this security."
                )
            else:
                st.warning(
                    "⚠️ **Security not found:** ISIN `IE000AZVL3K0` "
                    "(Revolut EUR Money Market Fund) was not matched in your Securities "
                    "database. A new security will be created on import, or use the "
                    "mapping tool below to link it to an existing entry."
                )
                _revs_security_mapping_ui()

        # ── Transaction type breakdown ─────────────────────────────────────
        if not df_raw.empty:
            with st.expander("📊 Transaction type breakdown"):
                def _cls(desc: str) -> str:
                    d = str(desc).upper()
                    if d.startswith("BUY"):               return "BUY"
                    if d.startswith("INTEREST PAID"):     return "INTEREST PAID"
                    if d.startswith("SERVICE FEE"):       return "SERVICE FEE CHARGED"
                    if d.startswith("INTEREST REINVEST"): return "INTEREST REINVESTED (SELL)"
                    return "OTHER"
                df_tc = df_raw.copy()
                df_tc["type"] = df_tc["description"].apply(_cls)
                type_counts = (
                    df_tc.groupby("type")["value_eur"]
                    .agg(count="count", total="sum")
                    .reset_index()
                    .sort_values("count", ascending=False)
                )
                st.dataframe(
                    type_counts, hide_index=True, width="stretch",
                    column_config={
                        "type":  "Type",
                        "count": st.column_config.NumberColumn("# Rows",     format="%d"),
                        "total": st.column_config.NumberColumn("Net (€)",    format="%.4f"),
                    },
                )

        # ── Reconciliation summary ─────────────────────────────────────────
        truly_new_inv = [r for r in inv_records
                         if r["desc"] not in existing_inv
                         and r["desc"] not in fuzzy_inv
                         and r["desc"] not in ignored_descs]
        truly_new_tx  = [r for r in tx_records
                         if r["desc"] not in existing_tx
                         and r["desc"] not in fuzzy_tx
                         and r["desc"] not in ignored_descs]
        fuzzy_only_inv  = [r for r in inv_records if r["desc"] in fuzzy_inv]
        fuzzy_only_tx   = [r for r in tx_records  if r["desc"] in fuzzy_tx]
        exist_inv_count = sum(1 for r in inv_records if r["desc"] in existing_inv)
        exist_tx_count  = sum(1 for r in tx_records  if r["desc"] in existing_tx)
        ign_inv_count   = sum(1 for r in inv_records if r["desc"] in ignored_descs)
        ign_tx_count    = sum(1 for r in tx_records  if r["desc"] in ignored_descs)

        _skip_inv = exist_inv_count + len(fuzzy_only_inv)
        _skip_tx  = exist_tx_count  + len(fuzzy_only_tx)

        if not truly_new_inv and not truly_new_tx and not replace_mode:
            st.info(
                f"✅ Nothing genuinely new — "
                + (f"**{exist_inv_count}** inv exact + **{len(fuzzy_only_inv)}** likely-dup, "
                   if not _tx_mode else "")
                + f"**{exist_tx_count}** tx exact + **{len(fuzzy_only_tx)}** likely-dup."
            )
        else:
            _new_label = (
                f"**{len(truly_new_tx)}** new transaction(s)"
                if _tx_mode else
                f"**{len(truly_new_inv)}** new investment record(s) and "
                f"**{len(truly_new_tx)}** new transaction(s)"
            )
            st.success(f"Found {_new_label} to import.")

        if fuzzy_only_inv or fuzzy_only_tx:
            st.warning(
                (f"⚠️ **{len(fuzzy_only_inv)}** investment(s) and " if not _tx_mode else "⚠️ ")
                + f"**{len(fuzzy_only_tx)}** transaction(s) match existing records by "
                "date/amount but have a different description key — "
                "marked **⚠️ Likely duplicate** and will be skipped."
            )
        if ign_inv_count or ign_tx_count:
            st.info(
                (f"⏭️ **{ign_inv_count}** investment(s) and " if not _tx_mode else "⏭️ ")
                + f"**{ign_tx_count}** transaction(s) are on the ignore list and will be skipped."
            )

        # Show mode-appropriate metrics
        if _tx_mode:
            m1, m2 = st.columns(2)
            m1.metric("🆕 New transactions",  len(truly_new_tx))
            m2.metric("🔄 Skip transactions", _skip_tx + ign_tx_count,
                      help="✅ Exact key match + ⚠️ Likely duplicates + ⏭️ Ignored")
        else:
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("🆕 New investments",   len(truly_new_inv))
            r2.metric("🔄 Skip investments",  _skip_inv + ign_inv_count,
                      help="✅ Exact key match + ⚠️ Likely duplicates + ⏭️ Ignored")
            r3.metric("🆕 New transactions",  len(truly_new_tx))
            r4.metric("🔄 Skip transactions", _skip_tx  + ign_tx_count,
                      help="✅ Exact key match + ⚠️ Likely duplicates + ⏭️ Ignored")

        # ── Preview ──────────────────────────────────────────────────────────
        st.markdown("### 👁️ Preview")
        _revs_preview_with_status(
            inv_records, tx_records,
            existing_inv, existing_tx,
            fuzzy_inv, fuzzy_tx,
            ignored_descs=ignored_descs,
        )

        # ── Ignore manager ────────────────────────────────────────────────────
        _ignore_manager_ui(
            "Revolut Savings",
            inv_records, tx_records,
            existing_inv, existing_tx,
            fuzzy_inv, fuzzy_tx,
            ignored_descs,
            session_key="revs",
        )

        # ── Import ───────────────────────────────────────────────────────────
        st.divider()
        st.markdown("### 💾 Import")
        st.caption(
            f"Target account: **{acc_name}** (ID {acc_id}) — "
            f"{'💳 Transaction mode' if _tx_mode else '📈 Investment mode'}"
        )

        if _tx_mode:
            # Transaction mode: only transactions exist, single checkbox
            _import_tx  = st.checkbox("💳 Import cash transactions", value=True, key="revs_import_tx")
            _imp_inv    = []
            _imp_tx     = (truly_new_tx if not replace_mode else tx_records) if _import_tx else []
        else:
            # Investment mode: both record types may exist
            _rcol1, _rcol2 = st.columns(2)
            _import_inv = _rcol1.checkbox("📈 Import investments",       value=True, key="revs_import_inv")
            _import_tx  = _rcol2.checkbox("💳 Import cash transactions", value=True, key="revs_import_tx")
            _imp_inv    = (truly_new_inv if not replace_mode else inv_records) if _import_inv else []
            _imp_tx     = (truly_new_tx  if not replace_mode else tx_records)  if _import_tx  else []

        _new_total = len(_imp_inv) + len(_imp_tx)

        if _new_total == 0 and not replace_mode:
            st.info(
                "No records to import.  "
                "Enable **Replace mode** above if you want to force a clean re-import."
            )
        else:
            _btn_suffix = (
                f" ({_new_total} record{'s' if _new_total != 1 else ''})"
                if not replace_mode else " (replace mode)"
            )
            if st.button(f"✅ Confirm Import{_btn_suffix}", key="revs_confirm", type="primary"):
                from data.revolut_importer import run_savings_import
                prog = st.progress(0.0, text="Importing…")
                try:
                    counts = run_savings_import(
                        _imp_inv, _imp_tx, acc_id,
                        replace_mode=replace_mode,
                        progress_cb=lambda p: prog.progress(p, text="Importing…"),
                    )
                    prog.empty()
                    st.success("✅ Import complete!")
                    _import_summary(counts)
                    try:
                        save_app_setting("revs_account_id", str(acc_id))
                        save_app_setting("revs_mode", import_mode)
                        save_app_setting("revs_replace", "true" if replace_mode else "false")
                    except Exception:
                        pass
                    for k in ("revs_parsed", "revs_parsed_mode",
                              "revs_inv_records", "revs_tx_records",
                              "revs_df_raw", "revs_existing_inv", "revs_existing_tx",
                              "revs_fuzzy_inv", "revs_fuzzy_tx", "revs_ignored"):
                        st.session_state.pop(k, None)
                    _load_accounts.clear()
                    st.cache_data.clear()
                except Exception as exc:
                    prog.empty()
                    st.error(f"Import failed: {exc}")
                    st.code(traceback.format_exc())


# ===========================================================================
# Coinbase — security mapping UI
# ===========================================================================

def _cb_security_mapping_ui(sec_matches: dict) -> None:
    """Expander UI for mapping unmapped Coinbase crypto tickers to DB securities.

    Only shown when one or more assets could not be matched by ticker, name,
    or a previously saved mapping.  The mapping key is the crypto symbol (e.g.
    "BTC", "ETH").  Saved mappings are persisted to import_security_mappings
    and applied on all future Coinbase imports.
    """
    unmapped = {sym: info for sym, info in sec_matches.items() if info[1] == "new"}
    if not unmapped:
        return

    with st.expander(
        f"🗺️ Security Mappings — {len(unmapped)} unmapped asset(s) — click to configure",
        expanded=True,
    ):
        st.caption(
            "These crypto symbols were not found in your Securities database by ticker "
            "or name.  Select the matching security for each one, then click "
            "**💾 Save Mappings**.  Saved mappings are permanent and take priority "
            "on all future Coinbase imports."
        )

        all_secs = _load_all_securities()
        if all_secs.empty:
            st.warning("No securities found in the database. Create them in Static Data first.")
            return

        sec_options = ["(create new — will be added on import)"] + all_secs["name"].tolist()
        pending_mappings: dict[str, int] = {}

        for sym in sorted(unmapped):
            c1, c2 = st.columns([1, 3])
            with c1:
                st.markdown(f"**{sym}**")
            with c2:
                chosen = st.selectbox(
                    f"Map {sym} to",
                    sec_options,
                    key=f"cb_map_{sym}",
                    label_visibility="collapsed",
                )
                if not chosen.startswith("(create new"):
                    sec_row = all_secs[all_secs["name"] == chosen]
                    if not sec_row.empty:
                        pending_mappings[sym] = int(sec_row.iloc[0]["securities_id"])

        if pending_mappings:
            if st.button("💾 Save Mappings", key="cb_save_mappings", type="primary"):
                from database.queries import save_security_mappings
                try:
                    save_security_mappings("Coinbase", pending_mappings)
                    _updated = dict(st.session_state.get("cb_sec_matches", {}))
                    for sym, sec_id in pending_mappings.items():
                        sec_row  = all_secs[all_secs["securities_id"] == sec_id]
                        sec_name = sec_row.iloc[0]["name"] if not sec_row.empty else sym
                        _updated[sym] = (sec_id, f"mapped:{sec_name}")
                    st.session_state["cb_sec_matches"] = _updated
                    _load_all_securities.clear()
                    st.success(
                        f"✅ Saved {len(pending_mappings)} mapping(s). "
                        "Re-fetch to see the updated Security Match column."
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(f"Failed to save mappings: {exc}")
        else:
            st.info("Select at least one mapping above and click Save.")


# ===========================================================================
# Coinbase — preview helper with reconciliation status
# ===========================================================================

def _cb_preview_with_status(
    inv_records:  list,
    tx_records:   list,
    existing_inv: set,
    existing_tx:  set,
    fuzzy_inv:    set,
    fuzzy_tx:     set,
    sec_matches:  dict,
    ignored_descs: set | None = None,
) -> None:
    """Preview table for Coinbase records with 4-state Status + Security Match."""
    ignored_descs = ignored_descs or set()
    tab_inv, tab_tx = st.tabs([
        f"📈 Investments ({len(inv_records)})",
        f"💳 Cash Transactions ({len(tx_records)})",
    ])

    with tab_inv:
        if inv_records:
            rows = []
            for r in inv_records:
                if r["desc"] in existing_inv:
                    status = "✅ Exists"
                elif r["desc"] in fuzzy_inv:
                    status = "⚠️ Likely duplicate"
                elif r["desc"] in ignored_descs:
                    status = "⏭️ Ignored"
                else:
                    status = "🆕 New"
                sym        = (r.get("symbol") or "").strip()
                match_info = sec_matches.get(sym, (None, "new"))
                match_type = match_info[1]
                if match_type.startswith("mapped:"):
                    sec_label = f"🗺️ {match_type[7:]}"
                elif match_type in ("ticker", "name"):
                    sec_label = f"🔗 {match_type.capitalize()} match"
                else:
                    sec_label = "🆕 New security"
                rows.append({**r, "status": status, "security_match": sec_label})

            df_inv = pd.DataFrame(rows)
            cols = ["status", "date", "action", "symbol", "name",
                    "quantity", "price", "total_eur", "commission",
                    "currency", "asset_category", "security_match", "desc"]
            df_inv = df_inv[[c for c in cols if c in df_inv.columns]]
            st.dataframe(
                df_inv, hide_index=True, width="stretch",
                column_config={
                    "status":         "Status",
                    "date":           "Date",
                    "action":         "Action",
                    "symbol":         "Symbol",
                    "name":           "Name",
                    "quantity":       st.column_config.NumberColumn("Qty",          format="%.8f"),
                    "price":          st.column_config.NumberColumn("Price (€)",    format="%.4f"),
                    "total_eur":      st.column_config.NumberColumn("Total (€)",    format="%.4f"),
                    "commission":     st.column_config.NumberColumn("Commission",   format="%.4f"),
                    "currency":       "Ccy",
                    "asset_category": "Asset",
                    "security_match": "Security Match",
                    "desc":           "Dedup Key",
                },
            )
        else:
            st.info("No investment records fetched.")

    with tab_tx:
        if tx_records:
            rows = []
            for r in tx_records:
                if r["desc"] in existing_tx:
                    status = "✅ Exists"
                elif r["desc"] in fuzzy_tx:
                    status = "⚠️ Likely duplicate"
                elif r["desc"] in ignored_descs:
                    status = "⏭️ Ignored"
                else:
                    status = "🆕 New"
                rows.append({**r, "status": status})
            df_tx = pd.DataFrame(rows)
            cols  = ["status", "date", "description", "amount", "currency"]
            df_tx = df_tx[[c for c in cols if c in df_tx.columns]]
            st.dataframe(
                df_tx, hide_index=True, width="stretch",
                column_config={
                    "status":      "Status",
                    "date":        "Date",
                    "description": "Description",
                    "amount":      st.column_config.NumberColumn("Amount", format="%.4f"),
                    "currency":    "Ccy",
                },
            )
        else:
            st.info("No cash transaction records fetched.")


# ===========================================================================
# Crypto.com — security mapping UI  (mirrors _cb_security_mapping_ui)
# ===========================================================================

def _cdc_security_mapping_ui(sec_matches: dict) -> None:
    """Expander UI for mapping unmapped Crypto.com crypto tickers to DB securities."""
    unmapped = {sym: info for sym, info in sec_matches.items() if info[1] == "new"}
    if not unmapped:
        return

    with st.expander(
        f"🗺️ Security Mappings — {len(unmapped)} unmapped asset(s) — click to configure",
        expanded=True,
    ):
        st.caption(
            "These crypto symbols were not found in your Securities database by ticker "
            "or name. Select the matching security for each one, then click "
            "**💾 Save Mappings**. Saved mappings apply to all future Crypto.com imports."
        )

        all_secs = _load_all_securities()
        if all_secs.empty:
            st.warning("No securities found in the database. Create them in Static Data first.")
            return

        sec_options     = ["(create new — will be added on import)"] + all_secs["name"].tolist()
        pending_mappings: dict[str, int] = {}

        for sym in sorted(unmapped):
            c1, c2 = st.columns([1, 3])
            with c1:
                st.markdown(f"**{sym}**")
            with c2:
                chosen = st.selectbox(
                    f"Map {sym} to",
                    sec_options,
                    key=f"cdc_map_{sym}",
                    label_visibility="collapsed",
                )
                if not chosen.startswith("(create new"):
                    sec_row = all_secs[all_secs["name"] == chosen]
                    if not sec_row.empty:
                        pending_mappings[sym] = int(sec_row.iloc[0]["securities_id"])

        if pending_mappings:
            if st.button("💾 Save Mappings", key="cdc_save_mappings", type="primary"):
                from database.queries import save_security_mappings
                try:
                    save_security_mappings("Crypto.com", pending_mappings)
                    _updated = dict(st.session_state.get("cdc_sec_matches", {}))
                    for sym, sec_id in pending_mappings.items():
                        sec_row  = all_secs[all_secs["securities_id"] == sec_id]
                        sec_name = sec_row.iloc[0]["name"] if not sec_row.empty else sym
                        _updated[sym] = (sec_id, f"mapped:{sec_name}")
                    st.session_state["cdc_sec_matches"] = _updated
                    _load_all_securities.clear()
                    st.success(
                        f"✅ Saved {len(pending_mappings)} mapping(s). "
                        "Re-fetch to apply in the Security Match column."
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(f"Failed to save mappings: {exc}")
        else:
            st.info("Select at least one mapping above and click Save.")


# ===========================================================================
# Crypto.com — preview helper with reconciliation status
# ===========================================================================

def _cdc_preview_with_status(
    inv_records:   list,
    tx_records:    list,
    existing_inv:  set,
    existing_tx:   set,
    fuzzy_inv:     set,
    fuzzy_tx:      set,
    sec_matches:   dict,
    ignored_descs: set | None = None,
) -> None:
    """Preview table for Crypto.com records with 4-state Status + Security Match."""
    ignored_descs = ignored_descs or set()
    tab_inv, tab_tx = st.tabs([
        f"📈 Investments ({len(inv_records)})",
        f"💳 Cash Transactions ({len(tx_records)})",
    ])

    with tab_inv:
        if inv_records:
            rows = []
            for r in inv_records:
                if r["desc"] in existing_inv:
                    status = "✅ Exists"
                elif r["desc"] in fuzzy_inv:
                    status = "⚠️ Likely duplicate"
                elif r["desc"] in ignored_descs:
                    status = "⏭️ Ignored"
                else:
                    status = "🆕 New"
                sym        = (r.get("symbol") or "").strip()
                match_info = sec_matches.get(sym, (None, "new"))
                match_type = match_info[1]
                if match_type.startswith("mapped:"):
                    sec_label = f"🗺️ {match_type[7:]}"
                elif match_type in ("ticker", "name"):
                    sec_label = f"🔗 {match_type.capitalize()} match"
                elif sym:
                    sec_label = "🆕 New security"
                else:
                    sec_label = "—"
                fx_note = r.get("_fx_note", "")
                rows.append({**r, "status": status, "security_match": sec_label,
                              "fx_note": fx_note})

            df_inv = pd.DataFrame(rows)
            cols   = ["status", "date", "action", "symbol", "name",
                      "quantity", "price", "total_eur", "commission",
                      "currency", "asset_category", "security_match", "fx_note", "desc"]
            df_inv = df_inv[[c for c in cols if c in df_inv.columns]]
            st.dataframe(
                df_inv, hide_index=True, width="stretch",
                column_config={
                    "status":         "Status",
                    "date":           "Date",
                    "action":         "Action",
                    "symbol":         "Symbol",
                    "name":           "Name",
                    "quantity":       st.column_config.NumberColumn("Qty",         format="%.8f"),
                    "price":          st.column_config.NumberColumn("Price (€)",   format="%.4f"),
                    "total_eur":      st.column_config.NumberColumn("Total (€)",   format="%.4f"),
                    "commission":     st.column_config.NumberColumn("Commission",  format="%.4f"),
                    "currency":       "Ccy",
                    "asset_category": "Asset",
                    "security_match": "Security Match",
                    "fx_note":        "FX Note",
                    "desc":           "Dedup Key",
                },
            )
        else:
            st.info("No investment records fetched.")

    with tab_tx:
        if tx_records:
            rows = []
            for r in tx_records:
                if r["desc"] in existing_tx:
                    status = "✅ Exists"
                elif r["desc"] in fuzzy_tx:
                    status = "⚠️ Likely duplicate"
                elif r["desc"] in ignored_descs:
                    status = "⏭️ Ignored"
                else:
                    status = "🆕 New"
                rows.append({**r, "status": status})
            df_tx = pd.DataFrame(rows)
            cols  = ["status", "date", "description", "amount", "currency"]
            df_tx = df_tx[[c for c in cols if c in df_tx.columns]]
            st.dataframe(
                df_tx, hide_index=True, width="stretch",
                column_config={
                    "status":      "Status",
                    "date":        "Date",
                    "description": "Description",
                    "amount":      st.column_config.NumberColumn("Amount", format="%.4f"),
                    "currency":    "Ccy",
                },
            )
        else:
            st.info("No cash transaction records fetched.")


# ===========================================================================
# Crypto.com — main render function
# ===========================================================================

def render_cryptocom_import() -> None:
    """Crypto.com Exchange API importer — trades, deposits, withdrawals."""
    from database.queries import get_app_setting, save_app_setting

    st.markdown(
        "Import filled trades, crypto deposits/withdrawals, and fiat deposits/withdrawals "
        "from **Crypto.com Exchange** using the REST API v1."
    )

    with st.expander("ℹ️ How to create Crypto.com API keys (click to expand)", expanded=False):
        st.markdown("""
1. Log in to [crypto.com/exchange](https://crypto.com/exchange).
2. Go to **Settings → API Management → Create New API Key**.
3. Set a label, leave IP whitelist empty (or add your server IP).
4. Under **Permissions**, enable **View** only — read-only is sufficient.
5. Copy the **API Key** and **Secret Key** shown on creation.

> The secret is shown **only once** — copy it immediately.
> Store it safely; the app encrypts it in the database when you tick *Remember credentials*.

**Note**: The Crypto.com *App* (earn, cashback, card rewards) does not have a public API.
Only Exchange trades, deposits, and withdrawals are imported via API.
For App transactions, export a CSV from the App and use a CSV importer.
""")

    st.divider()

    # ── Credentials ───────────────────────────────────────────────────────────
    st.markdown("### 🔑 API Credentials")

    if "cdc_api_key" not in st.session_state:
        st.session_state["cdc_api_key"]    = get_app_setting("cdc_api_key")    or ""
        st.session_state["cdc_api_secret"] = get_app_setting("cdc_api_secret") or ""
        if st.session_state["cdc_api_key"]:
            st.session_state.setdefault("cdc_remember", True)

    col1, col2 = st.columns(2)
    with col1:
        api_key    = st.text_input("API Key",    key="cdc_api_key")
    with col2:
        api_secret = st.text_input("Secret Key", key="cdc_api_secret", type="password")

    remember = st.checkbox(
        "💾 Remember credentials (stored encrypted in app settings)",
        key="cdc_remember",
        help="Saves Key + Secret to the database so they are pre-filled next visit.",
    )

    st.divider()

    # ── Date filter ───────────────────────────────────────────────────────────
    st.markdown("### 📅 Date Filter")
    st.caption(
        "The Crypto.com API supports server-side date filtering via timestamps. "
        "Set a range to limit how many pages are fetched."
    )
    _df_col1, _df_col2 = st.columns(2)
    with _df_col1:
        cdc_from = st.date_input("Fetch from", value=None, key="cdc_filter_from")
    with _df_col2:
        cdc_to   = st.date_input("Fetch to",   value=None, key="cdc_filter_to")

    st.divider()

    # ── Account mapping ───────────────────────────────────────────────────────
    st.markdown("### 🏦 Account Mapping")

    if "cdc_account" not in st.session_state:
        _saved = get_app_setting("cdc_account_id")
        if _saved:
            try:
                _row = _load_accounts()
                _row = _row[_row["accounts_id"] == int(_saved)]
                if not _row.empty:
                    st.session_state["cdc_account"] = _row.iloc[0]["accounts_name"]
            except Exception:
                pass

    if "cdc_cash_account" not in st.session_state:
        _saved_cash = get_app_setting("cdc_cash_account_id")
        if _saved_cash:
            try:
                _row = _load_accounts()
                _row = _row[_row["accounts_id"] == int(_saved_cash)]
                if not _row.empty:
                    st.session_state["cdc_cash_account"] = _row.iloc[0]["accounts_name"]
            except Exception:
                pass

    _am_col1, _am_col2 = st.columns(2)
    with _am_col1:
        st.caption("Investment account (trades, crypto deposits/withdrawals)")
        acc_id, acc_name = _account_selectbox(
            "Investment account",
            key="cdc_account",
            type_filter=["Brokerage", "Other Investment", "Pension", "Savings"],
        )
    with _am_col2:
        st.caption(
            "Cash account (fiat deposits/withdrawals) — optional. "
            "When set, fiat movements go here as Transactions instead of "
            "CashIn/CashOut entries in the brokerage account."
        )
        _df_accounts  = _load_accounts()
        _cash_options = ["— None (use investment account) —"] + _df_accounts["accounts_name"].tolist()
        _cash_sel     = st.selectbox("Cash account (optional)", _cash_options, key="cdc_cash_account")
        if _cash_sel and _cash_sel != "— None (use investment account) —":
            _cash_row     = _df_accounts[_df_accounts["accounts_name"] == _cash_sel]
            cash_acc_id   = int(_cash_row.iloc[0]["accounts_id"]) if not _cash_row.empty else None
            cash_acc_name = _cash_sel
        else:
            cash_acc_id   = None
            cash_acc_name = None

    st.divider()

    # ── Options ───────────────────────────────────────────────────────────────
    st.markdown("### ⚙️ Options")
    replace_mode = st.checkbox(
        "Replace mode — delete all existing Crypto.com records for this account before importing",
        value=False, key="cdc_replace",
        help="All Investment and Transaction rows whose Description starts with 'CDC|' "
             "will be deleted first.",
    )

    st.divider()

    # ── Test Connection / Fetch ───────────────────────────────────────────────
    if not api_key.strip() or not api_secret.strip():
        st.info("Enter your API Key and Secret Key above to continue.")
        return

    _test_btn  = st.button("🔌 Test Connection", key="cdc_test")
    _fetch_btn = st.button("📡 Fetch & Preview", key="cdc_fetch",
                           type="primary", disabled=acc_id is None)

    if _test_btn:
        if remember:
            try:
                save_app_setting("cdc_api_key",    api_key.strip())
                save_app_setting("cdc_api_secret", api_secret.strip())
            except Exception:
                pass
        try:
            from data.cryptocom_connector import test_connection as _cdc_test
            with st.spinner("Connecting to Crypto.com Exchange…"):
                balances = _cdc_test(api_key.strip(), api_secret.strip())
            st.success(f"✅ Connected — {len(balances)} non-zero balance(s):")
            st.dataframe(
                pd.DataFrame(balances),
                hide_index=True, width="stretch",
                column_config={
                    "currency":  "Currency",
                    "balance":   st.column_config.NumberColumn("Balance",   format="%.8f"),
                    "available": st.column_config.NumberColumn("Available", format="%.8f"),
                    "order":     st.column_config.NumberColumn("In Orders", format="%.8f"),
                    "is_fiat":   "Fiat?",
                },
            )
        except Exception as exc:
            st.error(f"Connection failed: {exc}")
        return

    if _fetch_btn or st.session_state.get("cdc_parsed"):
        from data.cryptocom_connector import (
            fetch_all_transactions   as _cdc_fetch,
            build_cryptocom_records  as _cdc_build,
            check_existing_records   as _cdc_exist,
            check_fuzzy_duplicates   as _cdc_fuzzy,
            preview_security_matches as _cdc_sec,
        )

        if _fetch_btn:
            if remember:
                try:
                    save_app_setting("cdc_api_key",    api_key.strip())
                    save_app_setting("cdc_api_secret", api_secret.strip())
                except Exception:
                    pass

            status_box = st.empty()
            def _status(msg: str):
                status_box.info(f"⏳ {msg}")

            try:
                orders, deposits, withdrawals = _cdc_fetch(
                    api_key.strip(), api_secret.strip(),
                    start_date=cdc_from,
                    end_date=cdc_to,
                    progress_cb=_status,
                )
                status_box.empty()
            except Exception as exc:
                status_box.empty()
                st.error(f"Failed to fetch from Crypto.com: {exc}")
                import traceback
                st.code(traceback.format_exc())
                return

            try:
                inv_records, tx_records = _cdc_build(orders, deposits, withdrawals)
            except Exception as exc:
                st.error(f"Failed to build records: {exc}")
                import traceback
                st.code(traceback.format_exc())
                return

            existing_inv, existing_tx = _cdc_exist(inv_records, tx_records, acc_id, cash_acc_id)
            fuzzy_inv,    fuzzy_tx    = _cdc_fuzzy(inv_records, tx_records, acc_id, cash_acc_id)
            fuzzy_inv -= existing_inv
            fuzzy_tx  -= existing_tx
            sec_matches = _cdc_sec(inv_records)

            from database.queries import get_ignored_records as _get_ign_cdc
            ignored_descs = _get_ign_cdc("Crypto.com")

            st.session_state.update({
                "cdc_inv_records":    inv_records,
                "cdc_tx_records":     tx_records,
                "cdc_cash_acc_id":    cash_acc_id,
                "cdc_raw_counts":     (len(orders), len(deposits), len(withdrawals)),
                "cdc_existing_inv":   existing_inv,
                "cdc_existing_tx":    existing_tx,
                "cdc_fuzzy_inv":      fuzzy_inv,
                "cdc_fuzzy_tx":       fuzzy_tx,
                "cdc_sec_matches":    sec_matches,
                "cdc_ignored":        ignored_descs,
                "cdc_parsed":         True,
            })

        inv_records    = st.session_state.get("cdc_inv_records",  [])
        orig_tx_records= st.session_state.get("cdc_tx_records",   [])
        tx_records     = list(orig_tx_records)
        cash_acc_id    = st.session_state.get("cdc_cash_acc_id",  cash_acc_id)
        existing_inv   = st.session_state.get("cdc_existing_inv", set())
        existing_tx    = st.session_state.get("cdc_existing_tx",  set())
        fuzzy_inv      = st.session_state.get("cdc_fuzzy_inv",    set())
        fuzzy_tx       = st.session_state.get("cdc_fuzzy_tx",     set())
        sec_matches    = st.session_state.get("cdc_sec_matches",  {})
        ignored_descs  = st.session_state.get("cdc_ignored",      set())
        raw_counts     = st.session_state.get("cdc_raw_counts",   (0, 0, 0))

        # Split CashIn/CashOut out of inv_records for the preview display,
        # but keep orig_inv_records intact for the import call.
        orig_inv_records = list(inv_records)
        cash_flow_recs:  list[dict] = []
        if cash_acc_id:
            cash_flow_recs = [r for r in inv_records if not r.get("symbol")]
            inv_records    = [r for r in inv_records if r.get("symbol")]
            for cf in cash_flow_recs:
                amount = cf["total_eur"] if cf["action"] == "CashIn" else -cf["total_eur"]
                tx_records.append({
                    "record_type": "transaction",
                    "source":      "Crypto.com",
                    "desc":        cf["desc"],
                    "date":        cf["date"],
                    "amount":      amount,
                    "description": f"Crypto.com {cf['action']}",
                    "currency":    "EUR",
                })

        # ── Fetch summary ──────────────────────────────────────────────────
        n_orders, n_dep, n_wdr = raw_counts
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Filled orders",      n_orders)
        m2.metric("Deposits",           n_dep)
        m3.metric("Withdrawals",        n_wdr)
        m4.metric("Investment records", len(inv_records))

        if not inv_records and not tx_records:
            st.warning(
                "No importable records found. "
                "Check your date filter and that your API key has View permission."
            )
            return

        if cash_acc_id:
            st.info(
                f"**Investments** → **{acc_name}** (ID {acc_id})   |   "
                f"**Cash transactions** → **{cash_acc_name}** (ID {cash_acc_id})"
            )

        # ── Action breakdown ───────────────────────────────────────────────
        if inv_records:
            with st.expander("📊 Investment action breakdown"):
                import collections
                action_counts = collections.Counter(r["action"] for r in inv_records)
                df_ac = pd.DataFrame(
                    [{"action": k, "count": v} for k, v in action_counts.most_common()]
                )
                st.dataframe(df_ac, hide_index=True, width="stretch",
                             column_config={
                                 "action": "Action",
                                 "count":  st.column_config.NumberColumn("# Records", format="%d"),
                             })

        # ── Reconciliation summary ─────────────────────────────────────────
        truly_new_inv  = [r for r in inv_records
                          if r["desc"] not in existing_inv
                          and r["desc"] not in fuzzy_inv
                          and r["desc"] not in ignored_descs]
        truly_new_tx   = [r for r in tx_records
                          if r["desc"] not in existing_tx
                          and r["desc"] not in fuzzy_tx
                          and r["desc"] not in ignored_descs]
        fuzzy_only_inv = [r for r in inv_records if r["desc"] in fuzzy_inv]
        fuzzy_only_tx  = [r for r in tx_records  if r["desc"] in fuzzy_tx]
        exist_inv_count= sum(1 for r in inv_records if r["desc"] in existing_inv)
        exist_tx_count = sum(1 for r in tx_records  if r["desc"] in existing_tx)
        ign_inv_count  = sum(1 for r in inv_records if r["desc"] in ignored_descs)
        ign_tx_count   = sum(1 for r in tx_records  if r["desc"] in ignored_descs)
        _skip_inv = exist_inv_count + len(fuzzy_only_inv)
        _skip_tx  = exist_tx_count  + len(fuzzy_only_tx)

        if not truly_new_inv and not truly_new_tx and not replace_mode:
            st.info(
                f"✅ Nothing genuinely new — "
                f"**{exist_inv_count}** inv exact + **{len(fuzzy_only_inv)}** likely-dup, "
                f"**{exist_tx_count}** tx exact + **{len(fuzzy_only_tx)}** likely-dup."
            )
        else:
            st.success(
                f"Found **{len(truly_new_inv)}** new investment record(s) and "
                f"**{len(truly_new_tx)}** new cash transaction(s) to import."
            )
        if fuzzy_only_inv or fuzzy_only_tx:
            st.warning(
                f"⚠️ **{len(fuzzy_only_inv)}** investment(s) and "
                f"**{len(fuzzy_only_tx)}** transaction(s) match by date/amount but have "
                "a different key — marked **⚠️ Likely duplicate** and will be skipped."
            )
        if ign_inv_count or ign_tx_count:
            st.info(
                f"⏭️ **{ign_inv_count}** investment(s) and "
                f"**{ign_tx_count}** transaction(s) are on the ignore list."
            )

        r1, r2, r3, r4 = st.columns(4)
        r1.metric("🆕 New investments",   len(truly_new_inv))
        r2.metric("🔄 Skip investments",  _skip_inv + ign_inv_count,
                  help="✅ Exact + ⚠️ Likely duplicate + ⏭️ Ignored")
        r3.metric("🆕 New transactions",  len(truly_new_tx))
        r4.metric("🔄 Skip transactions", _skip_tx + ign_tx_count,
                  help="✅ Exact + ⚠️ Likely duplicate + ⏭️ Ignored")

        # ── Preview ────────────────────────────────────────────────────────
        st.markdown("### 👁️ Preview")
        _cdc_preview_with_status(
            inv_records, tx_records,
            existing_inv, existing_tx,
            fuzzy_inv, fuzzy_tx,
            sec_matches,
            ignored_descs=ignored_descs,
        )

        # ── Security mappings ──────────────────────────────────────────────
        _cdc_security_mapping_ui(sec_matches)

        # ── Ignore manager ─────────────────────────────────────────────────
        _ignore_manager_ui(
            "Crypto.com",
            inv_records, tx_records,
            existing_inv, existing_tx,
            fuzzy_inv, fuzzy_tx,
            ignored_descs,
            session_key="cdc",
        )

        # ── Import ─────────────────────────────────────────────────────────
        st.divider()
        st.markdown("### 💾 Import")
        if cash_acc_id:
            st.caption(
                f"Investments → **{acc_name}** (ID {acc_id})   |   "
                f"Cash transactions → **{cash_acc_name}** (ID {cash_acc_id})"
            )
        else:
            st.caption(f"Target account: **{acc_name}** (ID {acc_id})")

        _cicol1, _cicol2 = st.columns(2)
        _import_inv = _cicol1.checkbox("📈 Import investments",       value=True, key="cdc_import_inv")
        _import_tx  = _cicol2.checkbox("💳 Import cash transactions", value=True, key="cdc_import_tx")

        def _is_cash_flow(r: dict) -> bool:
            return r["action"] in ("CashIn", "CashOut") and not r.get("symbol")

        if cash_acc_id and not replace_mode:
            new_cash_flow = [cf for cf in cash_flow_recs if cf["desc"] not in existing_tx]
            _imp_inv = (
                (truly_new_inv if _import_inv else [])
                + (new_cash_flow if _import_tx else [])
            )
        elif replace_mode:
            _imp_inv = []
            if _import_inv:
                _imp_inv += [r for r in orig_inv_records
                             if not (cash_acc_id and _is_cash_flow(r))]
            if _import_tx and cash_acc_id:
                _imp_inv += [r for r in orig_inv_records if _is_cash_flow(r)]
        else:
            _imp_inv = truly_new_inv if _import_inv else []

        _imp_tx = (
            ([r for r in orig_tx_records
              if r["desc"] not in existing_tx and r["desc"] not in ignored_descs]
             if not replace_mode else orig_tx_records)
            if _import_tx else []
        )
        _new_total = len(_imp_inv) + len(_imp_tx)

        if _new_total == 0 and not replace_mode:
            st.info(
                "No records to import. "
                "Enable **Replace mode** above if you want a clean re-import."
            )
        else:
            _btn_suffix = (
                f" ({_new_total} record{'s' if _new_total != 1 else ''})"
                if not replace_mode else " (replace mode)"
            )
            if st.button(f"✅ Confirm Import{_btn_suffix}",
                         key="cdc_confirm", type="primary"):
                from data.cryptocom_connector import run_cryptocom_import
                prog = st.progress(0.0, text="Importing…")
                try:
                    counts = run_cryptocom_import(
                        _imp_inv, _imp_tx, acc_id,
                        replace_mode=replace_mode,
                        progress_cb=lambda p: prog.progress(p, text="Importing…"),
                        cash_account_id=cash_acc_id,
                    )
                    prog.empty()
                    st.success("✅ Import complete!")
                    _import_summary(counts)
                    try:
                        save_app_setting("cdc_account_id", str(acc_id))
                        if cash_acc_id:
                            save_app_setting("cdc_cash_account_id", str(cash_acc_id))
                    except Exception:
                        pass
                    for k in ("cdc_parsed", "cdc_inv_records", "cdc_tx_records",
                              "cdc_cash_acc_id", "cdc_raw_counts",
                              "cdc_existing_inv", "cdc_existing_tx",
                              "cdc_fuzzy_inv", "cdc_fuzzy_tx", "cdc_sec_matches",
                              "cdc_ignored"):
                        st.session_state.pop(k, None)
                    _load_accounts.clear()
                    st.cache_data.clear()
                    st.rerun()
                except Exception as exc:
                    prog.empty()
                    st.error(f"Import failed: {exc}")
                    import traceback
                    st.code(traceback.format_exc())


# ===========================================================================
# Coinbase — main render function
# ===========================================================================

def render_coinbase_import() -> None:
    """Coinbase API importer — buys, sells, staking rewards, deposits/withdrawals."""
    from database.queries import get_app_setting, save_app_setting

    st.markdown(
        "Import trades, staking rewards, and transfers from **Coinbase** using the "
        "REST API.  Staking rewards are recorded as **Reinvest** (new units received "
        "as income, cost basis set at FMV on receipt date)."
    )

    with st.expander("ℹ️ How to create Coinbase API keys (click to expand)", expanded=False):
        st.markdown("""
Both **CDP keys** (current) and **Legacy keys** (older) are supported — the importer
detects the format automatically.

---

#### Option A — CDP / Cloud API keys *(recommended, current Coinbase default)*
1. Go to [cloud.coinbase.com/access/api](https://cloud.coinbase.com/access/api) → **Create API Key**.
2. Under **Permissions**, enable:
   - ✅ `wallet:accounts:read`
   - ✅ `wallet:transactions:read`
3. Click **Create & Download** — you receive:
   - **Key Name** — looks like `organizations/abc.../apiKeys/xyz...`
   - **Private Key** — a PEM block starting with `-----BEGIN EC PRIVATE KEY-----`
4. Paste the **Key Name** into *API Key* and the full **PEM block** into *API Secret*.

> The PEM private key spans multiple lines — paste it exactly as shown in the portal
> (newlines included).  It is stored encrypted in the app database when you tick
> *Remember credentials*.

---

#### Option B — Legacy API keys *(older format)*
1. Log in to [coinbase.com](https://www.coinbase.com) → avatar → **Settings** → **API** → **New API Key**.
2. Tick `wallet:accounts:read` + `wallet:transactions:read` → **Create**.
3. Copy the short **API Key** and **API Secret** strings and paste them below.
""")

    st.divider()

    # ── Credentials ──────────────────────────────────────────────────────────
    st.markdown("### 🔑 API Credentials")

    if "cb_api_key" not in st.session_state:
        st.session_state["cb_api_key"]    = get_app_setting("cb_api_key")    or ""
        st.session_state["cb_api_secret"] = get_app_setting("cb_api_secret") or ""
        if st.session_state["cb_api_key"]:
            st.session_state.setdefault("cb_remember", True)

    # ── Restore last-used account (first render only) ────────────────────────
    if "cb_account" not in st.session_state:
        _saved_cb = get_app_setting("cb_account_id")
        if _saved_cb:
            try:
                _cb_row = _load_accounts()
                _cb_row = _cb_row[_cb_row["accounts_id"] == int(_saved_cb)]
                if not _cb_row.empty:
                    st.session_state["cb_account"] = _cb_row.iloc[0]["accounts_name"]
            except Exception:
                pass

    col1, col2 = st.columns(2)
    with col1:
        api_key    = st.text_input(
            "API Key (or CDP Key Name)",
            key="cb_api_key",
            help=(
                "CDP keys: paste the full key name — "
                "organizations/{org_id}/apiKeys/{key_id}.\n\n"
                "Legacy keys: paste the short alphanumeric key string."
            ),
        )
    with col2:
        api_secret = st.text_input(
            "API Secret (or CDP Private Key PEM)",
            key="cb_api_secret",
            type="password",
            help=(
                "CDP keys: paste the entire PEM block including the "
                "-----BEGIN / -----END lines.\n\n"
                "Legacy keys: paste the secret string shown on key creation."
            ),
        )

    remember = st.checkbox(
        "💾 Remember credentials (stored encrypted in app settings)",
        key="cb_remember",
        help="Saves Key + Secret to the database so they are pre-filled next visit.",
    )

    st.divider()

    # ── Date filter ──────────────────────────────────────────────────────────
    st.markdown("### 📅 Date Filter")
    st.caption(
        "The Coinbase API has no server-side date filter — all pages are fetched "
        "then filtered on the client.  Set a narrow range to reduce fetch time."
    )
    _df_col1, _df_col2 = st.columns(2)
    with _df_col1:
        cb_from = st.date_input("Fetch from", value=None, key="cb_filter_from",
                                help="Only import records on or after this date.")
    with _df_col2:
        cb_to   = st.date_input("Fetch to",   value=None, key="cb_filter_to",
                                help="Only import records on or before this date.")

    st.divider()

    # ── Account mapping ──────────────────────────────────────────────────────
    st.markdown("### 🏦 Account Mapping")

    # Restore last-used cash account (first render only)
    if "cb_cash_account" not in st.session_state:
        _saved_cb_cash = get_app_setting("cb_cash_account_id")
        if _saved_cb_cash:
            try:
                _cb_cash_row = _load_accounts()
                _cb_cash_row = _cb_cash_row[_cb_cash_row["accounts_id"] == int(_saved_cb_cash)]
                if not _cb_cash_row.empty:
                    st.session_state["cb_cash_account"] = _cb_cash_row.iloc[0]["accounts_name"]
            except Exception:
                pass

    _am_col1, _am_col2 = st.columns(2)
    with _am_col1:
        st.caption("Investment account (buys, sells, staking rewards)")
        acc_id, acc_name = _account_selectbox(
            "Investment account",
            key="cb_account",
            type_filter=["Brokerage", "Other Investment", "Pension", "Savings"],
        )
    with _am_col2:
        st.caption(
            "Cash account (fiat deposits/withdrawals) — optional.  "
            "When set, CashIn/CashOut records go here as Transactions "
            "instead of as investment entries in the brokerage account."
        )
        _df_accounts = _load_accounts()
        _cash_options = ["— None (use investment account) —"] + _df_accounts["accounts_name"].tolist()
        _cash_sel = st.selectbox("Cash account (optional)", _cash_options, key="cb_cash_account")
        if _cash_sel and _cash_sel != "— None (use investment account) —":
            _cash_row  = _df_accounts[_df_accounts["accounts_name"] == _cash_sel]
            cash_acc_id   = int(_cash_row.iloc[0]["accounts_id"]) if not _cash_row.empty else None
            cash_acc_name = _cash_sel
        else:
            cash_acc_id   = None
            cash_acc_name = None

    st.divider()

    # ── Options ──────────────────────────────────────────────────────────────
    st.markdown("### ⚙️ Options")
    replace_mode = st.checkbox(
        "Replace mode — delete all existing Coinbase records for this account before importing",
        value=False, key="cb_replace",
        help="All Investment and Transaction rows whose Description starts with 'CB|' "
             "will be deleted first.",
    )

    st.divider()

    # ── Test Connection / Fetch ───────────────────────────────────────────────
    if not api_key.strip() or not api_secret.strip():
        st.info("Enter your API Key and Secret above to continue.")
        return

    _test_btn  = st.button("🔌 Test Connection", key="cb_test")
    _fetch_btn = st.button("📡 Fetch & Preview", key="cb_fetch",
                           type="primary", disabled=acc_id is None)

    if _test_btn:
        if remember:
            try:
                save_app_setting("cb_api_key",    api_key.strip())
                save_app_setting("cb_api_secret", api_secret.strip())
            except Exception:
                pass
        try:
            from data.coinbase_connector import test_connection as _cb_test
            with st.spinner("Connecting to Coinbase…"):
                accounts = _cb_test(api_key.strip(), api_secret.strip())
            st.success(f"✅ Connected — {len(accounts)} account(s) found:")
            df_accs = pd.DataFrame(accounts)[
                ["currency_code", "name", "balance", "native_balance",
                 "native_currency", "type"]
            ]
            st.dataframe(
                df_accs, hide_index=True, width="stretch",
                column_config={
                    "currency_code":   "Currency",
                    "name":            "Account Name",
                    "balance":         st.column_config.NumberColumn("Balance",  format="%.8f"),
                    "native_balance":  st.column_config.NumberColumn("Value",    format="%.2f"),
                    "native_currency": "Ccy",
                    "type":            "Type",
                },
            )
            st.session_state["cb_accounts"] = accounts
        except Exception as exc:
            st.error(f"Connection failed: {exc}")
        return

    if _fetch_btn or st.session_state.get("cb_parsed"):
        from data.coinbase_connector import (
            test_connection     as _cb_conn,
            fetch_all_transactions as _cb_fetch,
            build_coinbase_records as _cb_build,
            check_existing_records as _cb_exist,
            check_fuzzy_duplicates  as _cb_fuzzy,
            preview_security_matches as _cb_sec,
        )

        if _fetch_btn:
            # Save credentials if requested
            if remember:
                try:
                    save_app_setting("cb_api_key",    api_key.strip())
                    save_app_setting("cb_api_secret", api_secret.strip())
                except Exception:
                    pass

            status_box = st.empty()

            try:
                def _status(msg):
                    status_box.info(f"⏳ {msg}")

                _status("Fetching Coinbase account list…")
                accounts = _cb_conn(api_key.strip(), api_secret.strip())

                crypto_accs = [a for a in accounts if not a["is_fiat"]]
                fiat_accs   = [a for a in accounts if a["is_fiat"]]
                _status(
                    f"Found {len(accounts)} accounts "
                    f"({len(crypto_accs)} crypto, {len(fiat_accs)} fiat). "
                    "Fetching transactions — this may take a moment…"
                )

                all_txns = _cb_fetch(
                    api_key.strip(), api_secret.strip(),
                    accounts,
                    start_date=cb_from,
                    end_date=cb_to,
                    progress_cb=_status,
                )
                status_box.empty()

            except Exception as exc:
                status_box.empty()
                st.error(f"Failed to fetch from Coinbase: {exc}")
                st.code(traceback.format_exc())
                return

            try:
                inv_records, tx_records = _cb_build(all_txns)
            except Exception as exc:
                st.error(f"Failed to build records: {exc}")
                st.code(traceback.format_exc())
                return

            existing_inv, existing_tx = _cb_exist(inv_records, tx_records, acc_id, cash_acc_id)
            fuzzy_inv, fuzzy_tx       = _cb_fuzzy(inv_records, tx_records, acc_id, cash_acc_id)
            fuzzy_inv -= existing_inv
            fuzzy_tx  -= existing_tx
            sec_matches = _cb_sec(inv_records)

            from database.queries import get_ignored_records as _get_ign_cb
            ignored_descs = _get_ign_cb("Coinbase")

            st.session_state["cb_inv_records"]   = inv_records
            st.session_state["cb_tx_records"]    = tx_records
            st.session_state["cb_cash_acc_id"]   = cash_acc_id
            st.session_state["cb_raw_txn_count"] = len(all_txns)
            st.session_state["cb_existing_inv"] = existing_inv
            st.session_state["cb_existing_tx"]  = existing_tx
            st.session_state["cb_fuzzy_inv"]    = fuzzy_inv
            st.session_state["cb_fuzzy_tx"]     = fuzzy_tx
            st.session_state["cb_sec_matches"]  = sec_matches
            st.session_state["cb_ignored"]      = ignored_descs
            st.session_state["cb_parsed"]       = True

        inv_records    = st.session_state.get("cb_inv_records",  [])
        orig_tx_records = st.session_state.get("cb_tx_records", [])  # for import
        tx_records     = list(orig_tx_records)
        cash_acc_id    = st.session_state.get("cb_cash_acc_id",  cash_acc_id)
        existing_inv  = st.session_state.get("cb_existing_inv", set())
        existing_tx   = st.session_state.get("cb_existing_tx",  set())
        fuzzy_inv     = st.session_state.get("cb_fuzzy_inv",    set())
        fuzzy_tx      = st.session_state.get("cb_fuzzy_tx",     set())
        sec_matches   = st.session_state.get("cb_sec_matches",  {})
        ignored_descs = st.session_state.get("cb_ignored",      set())
        raw_count     = st.session_state.get("cb_raw_txn_count", 0)

        # When a separate cash account is configured, split CashIn/CashOut out of
        # inv_records so the preview shows them under "Cash Transactions" (the
        # correct tab).  The original list is preserved for the import call
        # because run_coinbase_import handles the routing internally.
        orig_inv_records = list(inv_records)  # used by import button
        cash_flow_recs: list[dict] = []
        if cash_acc_id:
            cash_flow_recs = [cf for cf in inv_records if not cf.get("symbol")]
            inv_records    = [cf for cf in inv_records if cf.get("symbol")]
            tx_records     = list(tx_records)
            for cf in cash_flow_recs:
                amount = cf["total_eur"] if cf["action"] == "CashIn" else -cf["total_eur"]
                tx_records.append({
                    "record_type": "transaction",
                    "source":      "Coinbase",
                    "desc":        cf["desc"],
                    "date":        cf["date"],
                    "amount":      amount,
                    "description": f"Coinbase {cf['action']}",
                    "currency":    "EUR",
                })

        # ── Fetch summary ─────────────────────────────────────────────────
        m1, m2, m3 = st.columns(3)
        m1.metric("Raw API transactions", raw_count)
        m2.metric("Investment records",   len(inv_records))
        m3.metric("Cash transactions",    len(tx_records))

        if not inv_records and not tx_records:
            st.warning(
                "No importable records found. "
                "Check your date filter and that your API key has "
                "`wallet:transactions:read` permission."
            )
            return

        # ── Account info ──────────────────────────────────────────────────
        if cash_acc_id:
            st.info(
                f"**Investments** → **{acc_name}** (ID {acc_id})   |   "
                f"**Cash transactions** → **{cash_acc_name}** (ID {cash_acc_id})"
            )

        # ── Action breakdown ──────────────────────────────────────────────
        if inv_records:
            with st.expander("📊 Investment action breakdown"):
                import collections
                action_counts = collections.Counter(r["action"] for r in inv_records)
                df_ac = pd.DataFrame(
                    [{"action": k, "count": v} for k, v in action_counts.most_common()]
                )
                st.dataframe(df_ac, hide_index=True, width="stretch",
                             column_config={
                                 "action": "Action",
                                 "count":  st.column_config.NumberColumn("# Records", format="%d"),
                             })

        # ── Reconciliation summary ────────────────────────────────────────
        truly_new_inv   = [r for r in inv_records
                           if r["desc"] not in existing_inv
                           and r["desc"] not in fuzzy_inv
                           and r["desc"] not in ignored_descs]
        truly_new_tx    = [r for r in tx_records
                           if r["desc"] not in existing_tx
                           and r["desc"] not in fuzzy_tx
                           and r["desc"] not in ignored_descs]
        fuzzy_only_inv  = [r for r in inv_records if r["desc"] in fuzzy_inv]
        fuzzy_only_tx   = [r for r in tx_records  if r["desc"] in fuzzy_tx]
        exist_inv_count = sum(1 for r in inv_records if r["desc"] in existing_inv)
        exist_tx_count  = sum(1 for r in tx_records  if r["desc"] in existing_tx)
        ign_inv_count   = sum(1 for r in inv_records if r["desc"] in ignored_descs)
        ign_tx_count    = sum(1 for r in tx_records  if r["desc"] in ignored_descs)
        _skip_inv = exist_inv_count + len(fuzzy_only_inv)
        _skip_tx  = exist_tx_count  + len(fuzzy_only_tx)

        if not truly_new_inv and not truly_new_tx and not replace_mode:
            st.info(
                f"✅ Nothing genuinely new — "
                f"**{exist_inv_count}** inv exact + **{len(fuzzy_only_inv)}** likely-dup, "
                f"**{exist_tx_count}** tx exact + **{len(fuzzy_only_tx)}** likely-dup."
            )
        else:
            st.success(
                f"Found **{len(truly_new_inv)}** new investment record(s) and "
                f"**{len(truly_new_tx)}** new cash transaction(s) to import."
            )
        if fuzzy_only_inv or fuzzy_only_tx:
            st.warning(
                f"⚠️ **{len(fuzzy_only_inv)}** investment(s) and "
                f"**{len(fuzzy_only_tx)}** transaction(s) match by date/amount but have "
                "a different key — marked **⚠️ Likely duplicate** and will be skipped."
            )
        if ign_inv_count or ign_tx_count:
            st.info(
                f"⏭️ **{ign_inv_count}** investment(s) and "
                f"**{ign_tx_count}** transaction(s) are on the ignore list and will be skipped."
            )

        r1, r2, r3, r4 = st.columns(4)
        r1.metric("🆕 New investments",   len(truly_new_inv))
        r2.metric("🔄 Skip investments",  _skip_inv + ign_inv_count,
                  help="✅ Exact key match + ⚠️ Likely duplicates + ⏭️ Ignored")
        r3.metric("🆕 New transactions",  len(truly_new_tx))
        r4.metric("🔄 Skip transactions", _skip_tx + ign_tx_count,
                  help="✅ Exact key match + ⚠️ Likely duplicates + ⏭️ Ignored")

        # ── Preview ───────────────────────────────────────────────────────
        st.markdown("### 👁️ Preview")
        _cb_preview_with_status(
            inv_records, tx_records,
            existing_inv, existing_tx,
            fuzzy_inv, fuzzy_tx,
            sec_matches,
            ignored_descs=ignored_descs,
        )

        # ── Security Mappings ─────────────────────────────────────────────
        _cb_security_mapping_ui(sec_matches)

        # ── Ignore manager ────────────────────────────────────────────────
        _ignore_manager_ui(
            "Coinbase",
            inv_records, tx_records,
            existing_inv, existing_tx,
            fuzzy_inv, fuzzy_tx,
            ignored_descs,
            session_key="cb",
        )

        # ── Import ────────────────────────────────────────────────────────
        st.divider()
        st.markdown("### 💾 Import")
        if cash_acc_id:
            st.caption(
                f"Investments → **{acc_name}** (ID {acc_id})   |   "
                f"Cash transactions → **{cash_acc_name}** (ID {cash_acc_id})"
            )
        else:
            st.caption(f"Target account: **{acc_name}** (ID {acc_id})")

        _cicol1, _cicol2 = st.columns(2)
        _import_inv = _cicol1.checkbox("📈 Import investments",       value=True, key="cb_import_inv")
        _import_tx  = _cicol2.checkbox("💳 Import cash transactions", value=True, key="cb_import_tx")

        # Pass records to run_coinbase_import.
        # CashIn/CashOut records (no symbol) are routed by the connector to the
        # cash account as Transactions — so they must be gated on _import_tx,
        # not _import_inv.  Real investment records are gated on _import_inv.
        def _is_cash_flow(r: dict) -> bool:
            return r["action"] in ("CashIn", "CashOut") and not r.get("symbol")

        if cash_acc_id and not replace_mode:
            new_cash_flow = [cf for cf in cash_flow_recs if cf["desc"] not in existing_tx]
            _imp_inv = (
                (truly_new_inv if _import_inv else [])
                + (new_cash_flow if _import_tx else [])
            )
        elif replace_mode:
            _imp_inv = []
            if _import_inv:
                # Real investments (exclude cash-flow rows when a cash account exists)
                _imp_inv += [r for r in orig_inv_records
                             if not (cash_acc_id and _is_cash_flow(r))]
            if _import_tx and cash_acc_id:
                # Cash-flow rows that the connector will route to the cash account
                _imp_inv += [r for r in orig_inv_records if _is_cash_flow(r)]
        else:
            _imp_inv = truly_new_inv if _import_inv else []
        _imp_tx = (
            ([r for r in orig_tx_records if r["desc"] not in existing_tx and r["desc"] not in ignored_descs]
             if not replace_mode else orig_tx_records)
            if _import_tx else []
        )
        _new_total = len(_imp_inv) + len(_imp_tx)

        if _new_total == 0 and not replace_mode:
            st.info(
                "No records to import.  "
                "Enable **Replace mode** above if you want a clean re-import."
            )
        else:
            _btn_suffix = (
                f" ({_new_total} record{'s' if _new_total != 1 else ''})"
                if not replace_mode else " (replace mode)"
            )
            if st.button(f"✅ Confirm Import{_btn_suffix}",
                         key="cb_confirm", type="primary"):
                from data.coinbase_connector import run_coinbase_import
                prog = st.progress(0.0, text="Importing…")
                try:
                    counts = run_coinbase_import(
                        _imp_inv, _imp_tx, acc_id,
                        replace_mode=replace_mode,
                        progress_cb=lambda p: prog.progress(p, text="Importing…"),
                        cash_account_id=cash_acc_id,
                    )
                    prog.empty()
                    st.success("✅ Import complete!")
                    _import_summary(counts)
                    try:
                        save_app_setting("cb_account_id", str(acc_id))
                        if cash_acc_id:
                            save_app_setting("cb_cash_account_id", str(cash_acc_id))
                    except Exception:
                        pass
                    for k in ("cb_parsed", "cb_inv_records", "cb_tx_records",
                              "cb_cash_acc_id", "cb_raw_txn_count",
                              "cb_existing_inv", "cb_existing_tx",
                              "cb_fuzzy_inv", "cb_fuzzy_tx", "cb_sec_matches",
                              "cb_ignored", "cb_accounts"):
                        st.session_state.pop(k, None)
                    _load_accounts.clear()
                    st.cache_data.clear()
                except Exception as exc:
                    prog.empty()
                    st.error(f"Import failed: {exc}")
                    st.code(traceback.format_exc())


# ===========================================================================
# Saxo Bank — security mapping panel
# ===========================================================================

def _render_saxo_security_mapping(inv_records: list, sec_matches: dict) -> None:
    """Security mapping panel for SAXO instruments.

    Shows instruments that could not be matched automatically and lets the
    user link them to existing DB securities.  Mappings are persisted in
    ``import_security_mappings`` (source = 'Saxo Bank') and applied on every
    subsequent Saxo import — they are never lost.
    """
    from database.queries import (
        get_security_mappings,
        save_security_mappings,
        delete_security_mapping,
    )
    from database.connection import get_connection

    saved_ids = get_security_mappings("Saxo Bank")   # {symbol/name → sec_id}

    # Build unique instrument list from the fetched records
    seen_syms: dict[str, dict] = {}
    for r in inv_records:
        sym = r.get("symbol", "")
        if sym and sym not in seen_syms:
            seen_syms[sym] = {
                "symbol":   sym,
                "name":     r.get("name", ""),
                "currency": r.get("currency", ""),
            }

    # Load all DB securities for the selectbox options
    conn = get_connection()
    all_secs = pd.read_sql(
        """SELECT Securities_Id AS sid,
                  Ticker         AS ticker,
                  Securities_Name AS name
           FROM   Securities
           ORDER  BY Securities_Name""",
        conn,
    )
    conn.close()

    id_to_name = dict(zip(all_secs["sid"].astype(int), all_secs["name"]))
    name_to_id = dict(zip(all_secs["name"], all_secs["sid"].astype(int)))

    # Identify instruments not resolved by the automatic matching step
    unmapped: list[dict] = []
    for sym, info in seen_syms.items():
        nm = info["name"]
        # Skip if the user already saved a mapping for this symbol or name
        if sym in saved_ids or nm in saved_ids:
            continue
        # Skip if preview_security_matches already found a match
        match_info = sec_matches.get(sym, (None, "new"))
        if match_info[1] != "new":
            continue
        unmapped.append(info)

    saved_count    = len(saved_ids)
    unmapped_count = len(unmapped)

    with st.expander(
        f"🗺️ Security Mappings — {unmapped_count} unmapped · {saved_count} saved",
        expanded=bool(unmapped_count),
    ):
        if unmapped_count == 0 and saved_count == 0:
            st.success("All instruments matched automatically — no manual mapping needed.")
            return

        if unmapped_count:
            st.caption(
                "These SAXO instruments could not be matched automatically by ticker or name. "
                "Select the corresponding security from your database for each one, "
                "then click **💾 Save Mappings**. "
                "Saved mappings are permanent and applied on every future Saxo Bank import."
            )

            sec_opts = ["(create new — will be added on import)"] + all_secs["name"].tolist()
            pending: dict[str, int] = {}

            for item in unmapped:
                sym      = item["symbol"]
                bare     = sym.split(":")[0] if ":" in sym else sym
                safe_key = sym.replace(":", "_").replace(" ", "_").replace("/", "_")

                c1, c2, c3 = st.columns([1, 2, 3])
                with c1:
                    st.markdown(f"**{bare}**")
                    st.caption(sym)
                with c2:
                    st.caption(f"{item['name']}  {item['currency']}")
                with c3:
                    chosen = st.selectbox(
                        f"Map {sym}",
                        sec_opts,
                        key=f"saxo_secmap_{safe_key}",
                        label_visibility="collapsed",
                        help=f"Map SAXO symbol {sym!r} to an existing security.",
                    )
                    if chosen and not chosen.startswith("(create new"):
                        sid = name_to_id.get(chosen)
                        if sid:
                            pending[sym] = int(sid)

            if pending:
                if st.button("💾 Save Mappings", key="saxo_save_mappings_btn", type="primary"):
                    try:
                        save_security_mappings("Saxo Bank", pending)
                        # Invalidate cached sec_matches so preview refreshes on rerun
                        st.session_state.pop("saxo_sec_matches", None)
                        st.success(f"✅ Saved {len(pending)} mapping(s). Refreshing preview…")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Failed to save mappings: {exc}")

        # Show and manage already-saved mappings
        if saved_count:
            st.divider()
            st.markdown(f"**Saved mappings** ({saved_count}):")
            _mapping_rows = [
                {
                    "Symbol": sym,
                    "Mapped to": id_to_name.get(int(sid), f"id={sid}"),
                }
                for sym, sid in saved_ids.items()
            ]
            st.dataframe(pd.DataFrame(_mapping_rows), hide_index=True, width='stretch')

            to_delete = st.multiselect(
                "Remove mappings:", list(saved_ids.keys()), key="saxo_del_mappings"
            )
            if to_delete and st.button("🗑️ Remove selected", key="saxo_del_mappings_btn"):
                for sym in to_delete:
                    try:
                        delete_security_mapping("Saxo Bank", sym)
                    except Exception:
                        pass
                st.session_state.pop("saxo_sec_matches", None)
                st.success(f"Removed {len(to_delete)} mapping(s).")
                st.rerun()


# ===========================================================================
# Saxo Bank — PDF charge security mapping UI
# ===========================================================================

def _render_saxo_pdf_security_mapping(
    pdf_charges:  list,
    sec_matches:  dict,
) -> None:
    """Security mapping panel for PDF-extracted SAXO charge instrument names.

    Shows instrument names that could not be resolved to an existing DB security
    (i.e. those falling back to the account-fee placeholder) and lets the user
    create permanent name→security mappings so future re-imports resolve them
    automatically.

    Mappings are stored in ``import_security_mappings`` under source
    ``'Saxo Bank'`` — the same table used by the API trade importer — so a
    single "Saxo Bank" mapping covers both import paths.

    Parameters
    ----------
    pdf_charges : list of records from ``parse_saxo_transactions_pdf()``
    sec_matches : dict returned by ``preview_pdf_charge_security_matches()``
                  ``{instrument_name: (sec_id | None, match_label)}``
    """
    from database.queries import (
        get_security_mappings,
        save_security_mappings,
        delete_security_mapping,
    )
    from database.connection import get_connection

    saved_ids = get_security_mappings("Saxo Bank")  # {name/sym → sec_id}

    # Load all DB securities once for selectbox options
    conn = get_connection()
    all_secs = pd.read_sql(
        """SELECT Securities_Id AS sid,
                  Ticker          AS ticker,
                  Securities_Name AS name
           FROM   Securities
           ORDER  BY Securities_Name""",
        conn,
    )
    conn.close()

    id_to_name = dict(zip(all_secs["sid"].astype(int), all_secs["name"]))
    name_to_id = dict(zip(all_secs["name"],            all_secs["sid"].astype(int)))

    # Identify instrument names that weren't resolved (using placeholder)
    unresolved: list[str] = []
    for name, (sec_id, label) in sec_matches.items():
        if name in saved_ids:
            continue  # already has a saved mapping — skip
        if label == "placeholder":
            unresolved.append(name)

    unresolved_count = len(unresolved)
    saved_count      = len(saved_ids)

    with st.expander(
        f"🗺️ PDF Security Mappings — {unresolved_count} unresolved · {saved_count} saved",
        expanded=bool(unresolved_count),
    ):
        if unresolved_count == 0 and saved_count == 0:
            st.success(
                "All PDF instrument names resolved automatically — "
                "no manual mapping needed."
            )
            return

        # ── Resolved instruments (info) ────────────────────────────────────
        resolved = [
            (name, label)
            for name, (_, label) in sec_matches.items()
            if not label.startswith("placeholder")
        ]
        if resolved:
            with st.expander(
                f"✅ {len(resolved)} name(s) resolved automatically "
                "(click to inspect)",
                expanded=False,
            ):
                _res_rows = []
                for name, label in resolved:
                    if label.startswith("mapped:"):
                        how = "🗺️ Mapped"
                        matched = label[7:]
                    elif label.startswith("squash:"):
                        how = "🔍 Squash match"
                        matched = label[7:]
                    else:
                        how, matched = label, ""
                    _res_rows.append({
                        "PDF Name":        name,
                        "How":             how,
                        "Matched to":      matched,
                    })
                st.dataframe(
                    pd.DataFrame(_res_rows),
                    hide_index=True,
                    width='stretch',
                )

        # ── Unresolved instruments ─────────────────────────────────────────
        if unresolved_count:
            st.caption(
                "These instrument names were extracted from the PDF but could not "
                "be matched to any security in your database automatically.  "
                "They will be linked to the **Saxo Bank (Account Fees)** placeholder "
                "unless you map them below.  "
                "Select the correct security for each, then click "
                "**💾 Save Mappings**.  Mappings are permanent and apply to all "
                "future SAXO imports."
            )

            sec_opts = ["(use placeholder — Saxo Bank Account Fees)"] + all_secs["name"].tolist()
            pending: dict[str, int] = {}

            for name in unresolved:
                safe_key = (
                    name.replace(" ", "_")
                        .replace(":", "_")
                        .replace("/", "_")
                        .replace(".", "_")
                        .replace("(", "_")
                        .replace(")", "_")
                )[:60]

                c1, c2 = st.columns([2, 3])
                with c1:
                    st.markdown(f"**{name}**")
                    # Show currency hint from first matching record
                    _ccy = next(
                        (r.get("currency", "") for r in pdf_charges
                         if r.get("name") == name),
                        "",
                    )
                    if _ccy:
                        st.caption(f"Currency: {_ccy}")
                with c2:
                    chosen = st.selectbox(
                        f"Map {name!r}",
                        sec_opts,
                        key=f"saxo_pdf_secmap_{safe_key}",
                        label_visibility="collapsed",
                        help=(
                            f"Select the DB security that corresponds to "
                            f"the PDF instrument name {name!r}."
                        ),
                    )
                    if chosen and not chosen.startswith("(use placeholder"):
                        sid = name_to_id.get(chosen)
                        if sid:
                            pending[name] = int(sid)

            if pending:
                if st.button(
                    "💾 Save Mappings",
                    key="saxo_pdf_save_mappings_btn",
                    type="primary",
                ):
                    try:
                        save_security_mappings("Saxo Bank", pending)
                        # Invalidate sec_matches so next parse reflects new mappings
                        st.session_state.pop("saxo_pdf_sec_matches", None)
                        st.session_state.pop("saxo_pdf_charges",     None)
                        st.session_state.pop("saxo_pdf_parsed",      None)
                        st.success(
                            f"✅ Saved {len(pending)} mapping(s).  "
                            "Re-parse the PDF to see updated security assignments."
                        )
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Failed to save mappings: {exc}")
            else:
                st.info(
                    "Select a security for at least one name above, "
                    "then click **💾 Save Mappings**."
                )

        # ── Saved mappings manager ─────────────────────────────────────────
        if saved_count:
            st.divider()
            st.markdown(f"**Saved mappings for Saxo Bank** ({saved_count} total):")
            _mapping_rows = [
                {
                    "Key (name / symbol)": key,
                    "Mapped to":           id_to_name.get(int(sid), f"id={sid}"),
                }
                for key, sid in saved_ids.items()
            ]
            st.dataframe(
                pd.DataFrame(_mapping_rows),
                hide_index=True,
                width='stretch',
            )

            to_delete = st.multiselect(
                "Remove mapping(s):",
                list(saved_ids.keys()),
                key="saxo_pdf_del_mappings",
            )
            if to_delete and st.button(
                "🗑️ Remove selected",
                key="saxo_pdf_del_mappings_btn",
            ):
                for key in to_delete:
                    try:
                        delete_security_mapping("Saxo Bank", key)
                    except Exception:
                        pass
                st.session_state.pop("saxo_pdf_sec_matches", None)
                st.session_state.pop("saxo_pdf_charges",     None)
                st.session_state.pop("saxo_pdf_parsed",      None)
                st.success(f"Removed {len(to_delete)} mapping(s).")
                st.rerun()


# ===========================================================================
# Saxo Bank — preview helper
# ===========================================================================

def _saxo_preview_with_status(
    inv_records:   list,
    existing_inv:  set,
    fuzzy_inv:     set,
    sec_matches:   dict,
    ignored_descs: set | None = None,
) -> None:
    """Show the Saxo trade preview table annotated with import status.

    Status values:
      ✅ Exists          — already in DB (exact dedup key match)
      ⚠️ Likely duplicate — same date/action/qty found under a different key
      ⏭️ Ignored         — marked as permanently ignored
      🆕 New             — no match found, will be imported
    """
    ignored_descs = ignored_descs or set()

    if not inv_records:
        st.info("No trade records were returned for the selected date range and accounts.")
        return

    rows = []
    for r in inv_records:
        if r["desc"] in existing_inv:
            status = "✅ Exists"
        elif r["desc"] in fuzzy_inv:
            status = "⚠️ Likely duplicate"
        elif r["desc"] in ignored_descs:
            status = "⏭️ Ignored"
        else:
            status = "🆕 New"

        symbol     = r.get("symbol", "")
        match_info = sec_matches.get(symbol, (None, "new"))
        match_type = match_info[1]
        if match_type.startswith("mapped:"):
            sec_label = f"🗺️ {match_type[7:]}"
        elif match_type == "ticker":
            sec_label = "🔗 Ticker match"
        elif match_type == "name":
            sec_label = "🔗 Name match"
        else:
            sec_label = "🆕 New security"

        rows.append({**r, "status": status, "security_match": sec_label})

    df_inv = pd.DataFrame(rows)
    # For cross-currency position instruments, total_sec_cur holds the notional
    # in security currency (commission is always in account currency and shown
    # separately).  For all other rows, total_eur is the account-currency total.
    # Use pd.notna() to distinguish real values from NaN (NaN is truthy in Python).
    if "total_sec_cur" in df_inv.columns:
        df_inv["total_display"] = df_inv.apply(
            lambda r: r["total_sec_cur"] if pd.notna(r.get("total_sec_cur")) else r["total_eur"],
            axis=1,
        )
        df_inv["total_label"] = df_inv.apply(
            lambda r: "sec ccy" if pd.notna(r.get("total_sec_cur")) else "acct ccy",
            axis=1,
        )
    else:
        df_inv["total_display"] = df_inv["total_eur"]
        df_inv["total_label"]   = "acct ccy"

    cols = ["status", "date", "action", "symbol", "name",
            "quantity", "price", "commission", "total_display", "total_label",
            "currency", "asset_category", "instrument_type", "security_match",
            "account_id_str", "desc"]
    df_inv = df_inv[[c for c in cols if c in df_inv.columns]]
    st.dataframe(
        df_inv,
        hide_index=True,
        width='stretch',
        column_config={
            "status":          "Status",
            "date":            "Date",
            "action":          "Action",
            "symbol":          "Symbol",
            "name":            "Name",
            "quantity":        st.column_config.NumberColumn("Qty",         format="%.4f"),
            "price":           st.column_config.NumberColumn("Price",       format="%.4f"),
            "commission":      st.column_config.NumberColumn("Commission",  format="%.4f"),
            "total_display":   st.column_config.NumberColumn("Total",       format="%.2f"),
            "total_label":     "Total Ccy",
            "currency":        "Ccy",
            "asset_category":  "Sec Type",
            "instrument_type": "Instr Type",
            "security_match":  "Security Match",
            "account_id_str":  "SAXO Account",
            "desc":            "Dedup Key",
        },
    )


# ===========================================================================
# Saxo Bank — main importer
# ===========================================================================

def render_saxo_import() -> None:
    """Saxo Bank OpenAPI importer — trades via OAuth 2.0 REST API."""
    import time as _time
    from database.queries import get_app_setting, save_app_setting
    from data.saxo_connector import (
        get_auth_url, exchange_code, refresh_access_token,
        fetch_client_key, fetch_accounts, fetch_trades,
        fetch_instrument_details, parse_trades, parse_charges,
        check_existing_records, check_fuzzy_duplicates,
        preview_security_matches, run_import, run_charges_import,
        DEFAULT_REDIRECT_URI,
    )

    # ── Load saved settings into session state (first render only) ────────────
    if "saxo_initialized" not in st.session_state:
        st.session_state["saxo_initialized"] = True
        st.session_state.setdefault("saxo_app_key",
                                    get_app_setting("saxo_app_key") or "")
        st.session_state.setdefault("saxo_app_secret",
                                    get_app_setting("saxo_app_secret") or "")
        st.session_state.setdefault("saxo_use_sim",
                                    get_app_setting("saxo_use_sim") == "1")
        st.session_state.setdefault("saxo_redirect_uri",
                                    get_app_setting("saxo_redirect_uri") or DEFAULT_REDIRECT_URI)
        _saved_refresh = get_app_setting("saxo_refresh_token") or ""
        _saved_expiry  = get_app_setting("saxo_token_expiry")  or "0"
        if _saved_refresh:
            st.session_state.setdefault("saxo_refresh_token", _saved_refresh)
        try:
            st.session_state.setdefault("saxo_token_expiry", int(_saved_expiry))
        except (ValueError, TypeError):
            st.session_state.setdefault("saxo_token_expiry", 0)

    # ── Instructions ──────────────────────────────────────────────────────────
    with st.expander("ℹ️ How to set up Saxo OpenAPI access (click to expand)", expanded=False):
        st.markdown("""
1. Go to [developer.saxo](https://www.developer.saxo) → sign in with your Saxo account.
2. Create a new **Application** (or use an existing one):
   - **Redirect URI**: add exactly `http://localhost:8501` (or the URL of this app).
   - Copy the **AppKey** (= OAuth client_id) and **AppSecret** (= OAuth client_secret).
3. Paste AppKey and AppSecret in the fields below.
4. Click **🔑 Authorize with Saxo** — your browser will open the Saxo login page.
5. After logging in, Saxo redirects back to this app and the token is stored automatically.

**Two margin accounts + cash account**: map each Saxo account to the matching
app account in the *Account Mapping* section below.

**Token lifetime**: access tokens last ~20 minutes; the refresh token (stored in the
app database) lasts ~1 year and is used to auto-renew without asking you to log in again.
""")

    st.divider()

    # ── Credentials ───────────────────────────────────────────────────────────
    st.markdown("### 🔑 Credentials")

    _cred1, _cred2 = st.columns(2)
    with _cred1:
        app_key = st.text_input(
            "AppKey (client_id)", key="saxo_app_key",
            help="Shown on your Saxo Developer Portal application page.",
        )
    with _cred2:
        app_secret = st.text_input(
            "AppSecret (client_secret)", type="password", key="saxo_app_secret",
            help="Shown on your Saxo Developer Portal application page.",
        )

    _env_col, _redir_col = st.columns([1, 3])
    with _env_col:
        use_sim = st.checkbox(
            "Use Simulation Environment", key="saxo_use_sim",
            help="Uses the Saxo sim gateway — no real trades are affected.",
        )
    with _redir_col:
        redirect_uri = st.text_input(
            "Redirect URI", key="saxo_redirect_uri",
            help=(
                "Must exactly match the URI registered in the Saxo Developer Portal. "
                "Default: http://localhost:8501"
            ),
        )

    remember_creds = st.checkbox(
        "💾 Remember AppKey / AppSecret",
        key="saxo_remember",
        help="Saves credentials to the app database so they are pre-filled on your next visit.",
    )

    st.divider()

    # ── Authentication ─────────────────────────────────────────────────────────
    st.markdown("### 🔐 Authentication")

    def _token_valid() -> bool:
        return (
            bool(st.session_state.get("saxo_access_token"))
            and _time.time() < st.session_state.get("saxo_token_expiry", 0) - 30
        )

    def _store_tokens(tok: dict) -> None:
        acc   = tok.get("access_token", "")
        ref   = tok.get("refresh_token", "") or st.session_state.get("saxo_refresh_token", "")
        expiry = int(_time.time()) + int(tok.get("expires_in", 1200))
        st.session_state["saxo_access_token"]  = acc
        st.session_state["saxo_refresh_token"] = ref
        st.session_state["saxo_token_expiry"]  = expiry
        try:
            save_app_setting("saxo_refresh_token", ref)
            save_app_setting("saxo_token_expiry",  str(expiry))
        except Exception:
            pass

    def _try_refresh() -> bool:
        ref  = st.session_state.get("saxo_refresh_token", "")
        _k   = st.session_state.get("saxo_app_key", "").strip()
        _s   = st.session_state.get("saxo_app_secret", "").strip()
        _sim = st.session_state.get("saxo_use_sim", False)
        if not (ref and _k and _s):
            return False
        try:
            tok = refresh_access_token(_k, _s, ref, use_sim=_sim)
            _store_tokens(tok)
            return True
        except Exception:
            return False

    # Auto-detect OAuth2 callback code in URL query params
    _qp_code  = st.query_params.get("code",  "")
    _qp_state = st.query_params.get("state", "")
    if _qp_code and _qp_state == "saxo_import" and not st.session_state.get("saxo_code_processed"):
        _k = app_key.strip()
        _s = app_secret.strip()
        if _k and _s:
            try:
                with st.spinner("Exchanging authorization code with Saxo…"):
                    tok = exchange_code(_k, _s, _qp_code, redirect_uri.strip(), use_sim=use_sim)
                _store_tokens(tok)
                st.session_state["saxo_code_processed"] = True
                if remember_creds:
                    try:
                        save_app_setting("saxo_app_key",      _k)
                        save_app_setting("saxo_app_secret",   _s)
                        save_app_setting("saxo_use_sim",      "1" if use_sim else "0")
                        save_app_setting("saxo_redirect_uri", redirect_uri.strip())
                    except Exception:
                        pass
                st.query_params.clear()
                st.rerun()
            except Exception as exc:
                st.error(f"Failed to exchange authorization code: {exc}")

    if not app_key.strip():
        st.info("Enter your AppKey and AppSecret above to continue.")
        return

    # Try silent refresh if access token is stale
    if not _token_valid() and st.session_state.get("saxo_refresh_token"):
        _try_refresh()

    if _token_valid():
        mins_left = max(0, int((st.session_state["saxo_token_expiry"] - _time.time()) / 60))
        env_badge = "🧪 Simulation" if use_sim else "🔴 Live"
        st.success(f"✅ Authenticated ({env_badge}) — access token valid for ~{mins_left} min")

        if st.button("🚪 Log out / Re-authorize", key="saxo_logout"):
            for _k in ("saxo_access_token", "saxo_refresh_token", "saxo_token_expiry",
                       "saxo_client_key", "saxo_accounts", "saxo_code_processed"):
                st.session_state.pop(_k, None)
            try:
                save_app_setting("saxo_refresh_token", "")
                save_app_setting("saxo_token_expiry",  "0")
            except Exception:
                pass
            st.rerun()

    else:
        # Not authenticated — show authorize link + manual fallback
        if app_key.strip():
            auth_url = get_auth_url(app_key.strip(), redirect_uri.strip(), use_sim=use_sim)
            st.markdown(
                "Click below to open the Saxo login page. After authorizing, "
                "your browser redirects back here and the token is stored automatically."
            )
            st.link_button("🔑 Authorize with Saxo", url=auth_url)
            st.caption(
                "If the redirect doesn't come back here automatically, copy the `code=…` "
                "value from your browser's address bar and paste it below."
            )
            manual_code = st.text_input(
                "Authorization Code (manual fallback)",
                key="saxo_manual_code",
                placeholder="Paste the code= value from the redirect URL",
            )
            if st.button("🔓 Exchange Code", key="saxo_exchange_btn",
                         disabled=not manual_code.strip()):
                try:
                    with st.spinner("Exchanging code…"):
                        tok = exchange_code(
                            app_key.strip(), app_secret.strip(),
                            manual_code.strip(), redirect_uri.strip(),
                            use_sim=use_sim,
                        )
                    _store_tokens(tok)
                    st.session_state["saxo_code_processed"] = True
                    if remember_creds:
                        try:
                            save_app_setting("saxo_app_key",      app_key.strip())
                            save_app_setting("saxo_app_secret",   app_secret.strip())
                            save_app_setting("saxo_use_sim",      "1" if use_sim else "0")
                            save_app_setting("saxo_redirect_uri", redirect_uri.strip())
                        except Exception:
                            pass
                    st.rerun()
                except Exception as exc:
                    st.error(f"Code exchange failed: {exc}")
        return  # everything below requires a valid token

    # ── From here: token is valid ─────────────────────────────────────────────
    access_token = st.session_state["saxo_access_token"]

    # ── Fetch Saxo account list (once per session) ────────────────────────────
    if "saxo_accounts" not in st.session_state or "saxo_client_key" not in st.session_state:
        try:
            with st.spinner("Fetching Saxo account list…"):
                ck   = fetch_client_key(access_token, use_sim=use_sim)
                accs = fetch_accounts(access_token,   use_sim=use_sim)
            st.session_state["saxo_client_key"] = ck
            st.session_state["saxo_accounts"]   = accs
        except Exception as exc:
            st.error(f"Failed to fetch Saxo accounts: {exc}")
            return

    saxo_accounts   = st.session_state["saxo_accounts"]
    saxo_client_key = st.session_state["saxo_client_key"]

    st.divider()

    # ── Account Mapping ───────────────────────────────────────────────────────
    st.markdown("### 🏦 Account Mapping")
    st.caption(
        f"Saxo returned **{len(saxo_accounts)}** account(s). "
        "For each one, pick the matching account in this app. "
        "Trades are imported into the mapped account. "
        "Set *— Skip —* to exclude an account."
    )

    # Load previously saved per-account mappings
    for acc in saxo_accounts:
        _mk = f"saxo_map_{acc['AccountKey']}"
        if _mk not in st.session_state:
            _saved = get_app_setting(f"saxo_acc_map_{acc['AccountKey']}")
            try:
                st.session_state[_mk] = int(_saved) if _saved else None
            except (ValueError, TypeError):
                st.session_state[_mk] = None

    _df_all   = _load_accounts()
    _inv_types = ["Brokerage", "Margin", "Other Investment", "Pension"]
    _inv_df   = _df_all[_df_all["accounts_type"].isin(_inv_types)]
    _inv_opts = {None: "— Skip this account —"}
    _inv_opts.update({
        int(r["accounts_id"]): f"{r['accounts_name']} ({r['currency']})"
        for _, r in _inv_df.iterrows()
    })

    if len(_inv_opts) <= 1:
        st.warning(
            "⚠️ No investment accounts found in the app. "
            "Go to **Accounts** and create accounts with type "
            "*Brokerage*, *Margin*, *Other Investment*, or *Pension* first."
        )

    account_map: dict[str, int] = {}
    for acc in saxo_accounts:
        _mk     = f"saxo_map_{acc['AccountKey']}"
        _label  = (
            f"**{acc['DisplayName']}** — "
            f"{acc['AccountType']} · {acc['Currency']}"
        )
        _saved  = st.session_state.get(_mk)
        _def_i  = list(_inv_opts.keys()).index(_saved) if _saved in _inv_opts else 0
        _sel = st.selectbox(
            _label,
            options=list(_inv_opts.keys()),
            format_func=lambda x, opts=_inv_opts: opts.get(x, "Unknown"),
            index=_def_i,
            key=_mk,
        )
        if _sel is not None:
            account_map[acc["AccountId"]] = int(_sel)

    if not account_map:
        st.warning("Please map at least one Saxo account to an app account to continue.")
        return

    st.divider()

    # ── Date Range ────────────────────────────────────────────────────────────
    st.markdown("### 📅 Date Range")
    st.caption(
        "Saxo's API accepts server-side date filters — only trades within "
        "this range are fetched."
    )
    _d1, _d2 = st.columns(2)
    with _d1:
        saxo_from = st.date_input("From date", key="saxo_from",
                                  help="Inclusive start of the period to import.")
    with _d2:
        saxo_to   = st.date_input("To date",   key="saxo_to",
                                  help="Inclusive end of the period to import.")

    if saxo_from and saxo_to and saxo_from > saxo_to:
        st.error("'From' date must be before or equal to 'To' date.")
        return

    st.divider()

    # ── Options ───────────────────────────────────────────────────────────────
    st.markdown("### ⚙️ Options")
    saxo_replace = st.checkbox(
        "Replace mode — delete all existing Saxo records for mapped accounts before importing",
        value=False, key="saxo_replace",
        help=(
            "All Investments rows whose Description starts with 'SAXO|' "
            "in the mapped accounts are deleted first, then re-imported."
        ),
    )

    st.divider()

    # ── Fetch & Preview ───────────────────────────────────────────────────────
    _fetch_btn = st.button(
        "📡 Fetch & Preview",
        key="saxo_fetch",
        type="primary",
        disabled=not (saxo_from and saxo_to),
    )

    if _fetch_btn or st.session_state.get("saxo_parsed"):

        if _fetch_btn:
            # Persist account mappings
            for acc in saxo_accounts:
                _mk = f"saxo_map_{acc['AccountKey']}"
                _sel_val = st.session_state.get(_mk)
                try:
                    save_app_setting(
                        f"saxo_acc_map_{acc['AccountKey']}",
                        str(_sel_val) if _sel_val is not None else "",
                    )
                except Exception:
                    pass

            try:
                all_raw: list[dict] = []
                mapped_accs = [a for a in saxo_accounts if a["AccountId"] in account_map]

                with st.spinner("Fetching trades from Saxo…"):
                    _prog = st.progress(0.0)
                    for _i, _acc in enumerate(mapped_accs):
                        _prog.progress(
                            _i / len(mapped_accs),
                            text=f"Fetching {_acc['DisplayName']}…",
                        )
                        all_raw.extend(
                            fetch_trades(
                                access_token, saxo_client_key,
                                _acc["AccountKey"],
                                saxo_from, saxo_to,
                                use_sim=use_sim,
                            )
                        )

                    _prog.progress(0.9, text="Fetching instrument details…")
                    _uic_pairs: list[tuple] = list({
                        (t.get("Uic"), t.get("AssetType", "Stock"))
                        for t in all_raw
                        if t.get("Uic")
                    })
                    instr_cache = fetch_instrument_details(
                        access_token, _uic_pairs, use_sim=use_sim
                    )
                    _prog.empty()

                inv_records     = parse_trades(all_raw, instr_cache)
                charge_records  = parse_charges(all_raw, instr_cache)
                existing_inv    = check_existing_records(inv_records, account_map)
                fuzzy_inv       = check_fuzzy_duplicates(inv_records, account_map)
                fuzzy_inv      -= existing_inv
                sec_matches     = preview_security_matches(inv_records)

                try:
                    from database.queries import get_ignored_records as _get_ign
                    ignored_descs = _get_ign("Saxo Bank")
                except Exception:
                    ignored_descs = set()

                st.session_state.update({
                    "saxo_inv_records":     inv_records,
                    "saxo_charge_records":  charge_records,
                    "saxo_existing_inv":    existing_inv,
                    "saxo_fuzzy_inv":       fuzzy_inv,
                    "saxo_sec_matches":     sec_matches,
                    "saxo_ignored":         ignored_descs,
                    "saxo_account_map":     account_map,
                    "saxo_parsed":          True,
                })

            except Exception as exc:
                st.error(f"Failed to fetch trades: {exc}")
                st.code(traceback.format_exc())
                return

        # ── Show preview ──────────────────────────────────────────────────────
        inv_records   = st.session_state.get("saxo_inv_records",  [])
        existing_inv  = st.session_state.get("saxo_existing_inv", set())
        fuzzy_inv     = st.session_state.get("saxo_fuzzy_inv",    set())
        ignored_descs = st.session_state.get("saxo_ignored",      set())
        _imp_map      = st.session_state.get("saxo_account_map",  account_map)

        # sec_matches may be missing from session state when the user saved a
        # new mapping and the cache was intentionally invalidated — re-run it.
        sec_matches = st.session_state.get("saxo_sec_matches")
        if sec_matches is None:
            sec_matches = preview_security_matches(inv_records)
            st.session_state["saxo_sec_matches"] = sec_matches

        _new_count  = sum(
            1 for r in inv_records
            if r["desc"] not in existing_inv and r["desc"] not in ignored_descs
        )
        _skip_count = len(existing_inv)

        st.markdown(
            f"**{len(inv_records)}** trade record(s) — "
            f"🆕 **{_new_count}** new &nbsp;·&nbsp; "
            f"✅ **{_skip_count}** already imported"
        )

        _saxo_preview_with_status(
            inv_records, existing_inv, fuzzy_inv, sec_matches, ignored_descs,
        )

        # ── Security mapping ──────────────────────────────────────────────────
        _render_saxo_security_mapping(inv_records, sec_matches)

        # ── Import ────────────────────────────────────────────────────────────
        st.divider()
        # In replace mode every record will be deleted then re-inserted, so
        # the button must be enabled even when all records already exist.
        _import_count = len(inv_records) if saxo_replace else _new_count
        _import_label = (
            f"🔄 Re-import all {_import_count} trade(s) (replace mode)"
            if saxo_replace
            else f"💾 Import {_import_count} new trade(s)"
        )
        _import_btn = st.button(
            _import_label,
            key="saxo_import_btn",
            type="primary",
            disabled=_import_count == 0,
        )

        if _import_btn:
            # In replace mode pass ALL records — run_import deletes existing
            # SAXO records first and then re-inserts everything cleanly.
            _to_import = (
                inv_records
                if saxo_replace
                else [
                    r for r in inv_records
                    if r["desc"] not in existing_inv and r["desc"] not in ignored_descs
                ]
            )
            _prog2 = st.progress(0.0)
            try:
                counts = run_import(
                    _to_import,
                    _imp_map,
                    replace_mode=saxo_replace,
                    progress_cb=lambda v: _prog2.progress(v),
                )
                _prog2.empty()
                st.success("✅ Import complete!")
                st.metric("Investments imported", counts.get("investments", 0))
                st.metric("Skipped (unmapped account)", counts.get("investments_skip", 0))

                for _k in ("saxo_parsed", "saxo_inv_records", "saxo_charge_records",
                           "saxo_existing_inv", "saxo_fuzzy_inv", "saxo_sec_matches",
                           "saxo_ignored", "saxo_account_map"):
                    st.session_state.pop(_k, None)
                _load_accounts.clear()
                st.cache_data.clear()

            except Exception as exc:
                _prog2.empty()
                st.error(f"Import failed: {exc}")
                st.code(traceback.format_exc())

        # ── Account Charges (API) ─────────────────────────────────────────
        st.divider()
        st.markdown("### 💸 Account Charges (CFD Finance · Dividends · Fees)")

        charge_records = st.session_state.get("saxo_charge_records", [])
        _imp_map       = st.session_state.get("saxo_account_map", account_map)

        if charge_records:
            st.caption(
                f"The API returned **{len(charge_records)}** non-trade "
                "charge/income entries below."
            )
            # Build preview dataframe
            _charge_rows = []
            for _cr in charge_records:
                _charge_rows.append({
                    "date":        _cr["date"],
                    "charge_type": _cr.get("charge_type", _cr["action"]),
                    "action":      _cr["action"],
                    "name":        _cr["name"],
                    "currency":    _cr["currency"],
                    "amount":      _cr["total_eur"],
                    "desc":        _cr["desc"],
                })
            _cdf = pd.DataFrame(_charge_rows)
            st.dataframe(
                _cdf,
                hide_index=True,
                width='stretch',
                column_config={
                    "date":        "Date",
                    "charge_type": "Type",
                    "action":      "DB Action",
                    "name":        "Instrument",
                    "currency":    "Ccy",
                    "amount":      st.column_config.NumberColumn("Amount (EUR)", format="%.2f"),
                    "desc":        "Dedup Key",
                },
            )

            # Check existing
            with get_connection() as _cc:
                _c = _cc.cursor()
                _existing_charges = set()
                for _cr in charge_records:
                    _c.execute(
                        "SELECT 1 FROM Investments WHERE Description = %s LIMIT 1",
                        (_cr["desc"],),
                    )
                    if _c.fetchone():
                        _existing_charges.add(_cr["desc"])

            _new_charges = [r for r in charge_records if r["desc"] not in _existing_charges]
            st.caption(
                f"🆕 {len(_new_charges)} new · "
                f"✅ {len(_existing_charges)} already imported"
            )

            _chrg_replace = st.checkbox(
                "Replace existing charge records before importing",
                value=False, key="saxo_charge_replace",
            )
            _import_charges_btn = st.button(
                f"💾 Import {len(charge_records) if _chrg_replace else len(_new_charges)}"
                " charge record(s)",
                key="saxo_import_charges",
                type="primary",
                disabled=(len(charge_records) if _chrg_replace else len(_new_charges)) == 0,
            )
            if _import_charges_btn:
                _to_import_c = (
                    charge_records if _chrg_replace
                    else _new_charges
                )
                _prog_c = st.progress(0.0)
                try:
                    _cc = run_charges_import(
                        _to_import_c,
                        _imp_map,
                        replace_mode=_chrg_replace,
                        progress_cb=lambda v: _prog_c.progress(v),
                    )
                    _prog_c.empty()
                    st.success(
                        f"✅ {_cc['imported']} charge(s) imported, "
                        f"{_cc['skipped']} skipped."
                    )
                    st.cache_data.clear()
                except Exception as _exc:
                    _prog_c.empty()
                    st.error(f"Charge import failed: {_exc}")
                    st.code(traceback.format_exc())

        else:
            st.info(
                "ℹ️ The SAXO `/cs/v1/reports/trades/` endpoint returns trade "
                "executions only — overnight CFD financing (CFDFinance), custody "
                "fees, dividends, and other account entries are published in the "
                "Account Statement and are **not available via this API endpoint**. "
                "Use the **📄 PDF Reconciliation** section below to import them "
                "from your downloaded Transaction and Balance Report PDF."
            )

        # ── PDF Reconciliation ────────────────────────────────────────────────
        st.divider()
        st.markdown("### 📄 PDF Reconciliation")
        st.caption(
            "Upload your **Transaction and Balance Report** PDF (downloaded from "
            "Saxo → My Portfolio → Reports). The app will parse all charge entries "
            "and show which ones are already imported and which are missing."
        )

        _pdf_file = st.file_uploader(
            "Transaction and Balance Report (PDF)",
            type=["pdf"],
            key="saxo_pdf_upload",
            help="Download from Saxo Bank: My Portfolio → Reports → "
                 "Transaction and Balance Report",
        )

        if _pdf_file is not None:
            import tempfile, os as _os
            _parse_pdf_btn = st.button(
                "🔍 Parse PDF", key="saxo_parse_pdf_btn", type="primary",
            )

            if _parse_pdf_btn or st.session_state.get("saxo_pdf_parsed"):
                if _parse_pdf_btn:
                    # Store PDF bytes in session state so they survive reruns
                    st.session_state["saxo_pdf_bytes"] = _pdf_file.read()

                    # Write to a temp file so pdfplumber can open it
                    with tempfile.NamedTemporaryFile(
                        suffix=".pdf", delete=False
                    ) as _tmp:
                        _tmp.write(st.session_state["saxo_pdf_bytes"])
                        _tmp_path = _tmp.name

                    try:
                        st.session_state["saxo_pdf_tmp_path"] = _tmp_path
                        from data.saxo_pdf_parser import (
                            parse_saxo_transactions_pdf, reconcile_charges,
                        )
                        with st.spinner("Parsing PDF…"):
                            _pdf_charges = parse_saxo_transactions_pdf(_tmp_path)
                    except Exception as _exc:
                        st.error(f"Failed to parse PDF: {_exc}")
                        st.code(traceback.format_exc())
                        _pdf_charges = []
                    finally:
                        try:
                            _os.unlink(_tmp_path)
                        except Exception:
                            pass

                    st.session_state["saxo_pdf_charges"] = _pdf_charges
                    st.session_state["saxo_pdf_parsed"]  = True

                    # Pre-compute security matches so the mapping panel and
                    # the recon table are annotated without an extra DB round-trip.
                    if _pdf_charges:
                        from data.saxo_connector import (
                            preview_pdf_charge_security_matches as _prev_pdf_sec,
                        )
                        with st.spinner("Resolving security names…"):
                            _pdf_sec_matches = _prev_pdf_sec(_pdf_charges)
                    else:
                        _pdf_sec_matches = {}
                    st.session_state["saxo_pdf_sec_matches"] = _pdf_sec_matches

                _pdf_charges     = st.session_state.get("saxo_pdf_charges",     [])
                _pdf_sec_matches = st.session_state.get("saxo_pdf_sec_matches", {})

                if not _pdf_charges:
                    st.warning(
                        "No charge entries found in the PDF. "
                        "Make sure you uploaded the correct file (Transaction and "
                        "Balance Report, not the Account Statement)."
                    )
                else:
                    # Reconcile against already-imported records
                    from data.saxo_pdf_parser import reconcile_charges

                    # Load imported charge records from DB for the mapped accounts
                    _db_charges: list[dict] = []
                    try:
                        with get_connection() as _rconn:
                            _rc = _rconn.cursor()
                            _acc_ids = list(set(_imp_map.values()))
                            _rc.execute(
                                """SELECT Date, Description, Total_Amount_AccCur,
                                          Action, Securities_Id
                                   FROM  Investments
                                   WHERE Accounts_Id = ANY(%s)
                                     AND Description LIKE %s""",
                                (_acc_ids, "SAXO|CHARGE|%"),
                            )
                            for _row in _rc.fetchall():
                                _d, _desc, _amt, _act, _ = _row
                                # Extract charge_type from desc key
                                _parts = _desc.split("|")
                                _ct    = _parts[2] if len(_parts) > 2 else ""
                                _db_charges.append({
                                    "date":        _d,
                                    "charge_type": _ct,
                                    "total_eur":   float(_amt or 0),
                                    "action":      _act,
                                    "desc":        _desc,
                                })
                    except Exception as _exc:
                        st.warning(f"Could not load existing DB charges: {_exc}")

                    _recon = reconcile_charges(_db_charges, _pdf_charges)

                    # Summary
                    _n_matched  = sum(1 for r in _recon if r["recon_status"] == "✅ Matched")
                    _n_missing  = sum(1 for r in _recon if r["recon_status"] == "🆕 Missing")
                    _n_mismatch = sum(1 for r in _recon if r["recon_status"] == "⚠️ Amt mismatch")

                    _s1, _s2, _s3 = st.columns(3)
                    _s1.metric("✅ Matched",       _n_matched)
                    _s2.metric("🆕 Missing",       _n_missing)
                    _s3.metric("⚠️ Amt mismatch",  _n_mismatch)

                    # Reconciliation table
                    _recon_rows = []
                    for _r in _recon:
                        _rname = _r.get("name", "")
                        _match_tuple = _pdf_sec_matches.get(_rname, (None, ""))
                        _match_lbl   = _match_tuple[1] if _match_tuple else ""
                        if _match_lbl.startswith("mapped:"):
                            _sec_disp = f"🗺️ {_match_lbl[7:]}"
                        elif _match_lbl.startswith("squash:"):
                            _sec_disp = f"🔍 {_match_lbl[7:]}"
                        elif _match_lbl == "placeholder":
                            _sec_disp = "📋 Placeholder"
                        else:
                            _sec_disp = ""   # account-level entry — no instrument
                        _recon_rows.append({
                            "status":         _r["recon_status"],
                            "date":           _r["date"],
                            "charge_type":    _r.get("charge_type", ""),
                            "name":           _rname,
                            "security_match": _sec_disp,
                            "pdf_amount":     _r["total_eur"],
                            "api_amount":     _r.get("api_amount"),
                            "action":         _r["action"],
                        })
                    _rdf = pd.DataFrame(_recon_rows)
                    st.dataframe(
                        _rdf,
                        hide_index=True,
                        width='stretch',
                        column_config={
                            "status":         "Status",
                            "date":           "Date",
                            "charge_type":    "Type",
                            "name":           "Instrument (PDF)",
                            "security_match": "Security Match",
                            "pdf_amount":     st.column_config.NumberColumn("PDF Amount", format="%.2f"),
                            "api_amount":     st.column_config.NumberColumn("DB Amount",  format="%.2f"),
                            "action":         "DB Action",
                        },
                    )

                    # ── Security mapping panel ──────────────────────────────
                    _render_saxo_pdf_security_mapping(_pdf_charges, _pdf_sec_matches)

                    # ── Import options ──────────────────────────────────────
                    _missing = [r for r in _recon if r["recon_status"] == "🆕 Missing"]
                    _pdf_replace = st.checkbox(
                        "Replace mode — delete all existing SAXO charge records and "
                        "re-import ALL entries from this PDF",
                        value=False,
                        key="saxo_pdf_replace",
                        help=(
                            "Use this to do a clean re-import after fixing the PDF "
                            "parser or security mappings.  All SAXO|CHARGE|… records "
                            "for the mapped account(s) will be deleted first, then all "
                            f"{len(_pdf_charges)} entries from this PDF are inserted."
                        ),
                    )

                    if _pdf_replace:
                        _to_import_pdf = _pdf_charges
                        _pdf_btn_label = (
                            f"📥 Replace & import all {len(_to_import_pdf)}"
                            " charge(s) from PDF"
                        )
                        st.warning(
                            f"⚠️ Replace mode is ON — **all** existing SAXO charge "
                            f"records for this account will be deleted first, then "
                            f"all **{len(_to_import_pdf)}** entries from this PDF "
                            "will be inserted."
                        )
                    elif _missing:
                        _to_import_pdf = _missing
                        _pdf_btn_label = (
                            f"📥 Import {len(_missing)} missing charge(s) from PDF"
                        )
                        st.caption(
                            f"**{len(_missing)}** charge(s) from the PDF are not yet "
                            "in the database. Click below to import them."
                        )
                    else:
                        _to_import_pdf = []
                        st.success("✅ All PDF charges are already imported.")

                    if _to_import_pdf:
                        _pdf_import_btn = st.button(
                            _pdf_btn_label,
                            key="saxo_pdf_import_btn",
                            type="primary",
                        )
                        if _pdf_import_btn:
                            _prog_pdf = st.progress(0.0)
                            try:
                                _pc = run_charges_import(
                                    _to_import_pdf,
                                    _imp_map,
                                    replace_mode=_pdf_replace,
                                    progress_cb=lambda v: _prog_pdf.progress(v),
                                )
                                _prog_pdf.empty()
                                st.success(
                                    f"✅ {_pc['imported']} charge(s) imported from PDF, "
                                    f"{_pc['skipped']} skipped."
                                )
                                # Reset reconciliation state
                                st.session_state.pop("saxo_pdf_parsed",      None)
                                st.session_state.pop("saxo_pdf_charges",     None)
                                st.session_state.pop("saxo_pdf_sec_matches", None)
                                st.cache_data.clear()
                                st.rerun()
                            except Exception as _exc:
                                _prog_pdf.empty()
                                st.error(f"PDF import failed: {_exc}")
                                st.code(traceback.format_exc())

                    st.divider()
                    st.markdown("**Update stock trade commissions from this PDF**")
                    st.caption(
                        "The Saxo API does not include commission in stock trade "
                        "records. This reads 'Booked Costs' from the PDF and "
                        "updates Commission + Total on already-imported trades."
                    )

                    # ── Step 1: Preview ──────────────────────────────────────
                    if st.button(
                        "🔍 Preview Commissions from PDF",
                        key="saxo_preview_commissions_btn",
                    ):
                        _pdf_bytes = st.session_state.get("saxo_pdf_bytes")
                        if not _pdf_bytes:
                            st.warning("Re-upload and parse the PDF first.")
                        else:
                            with tempfile.NamedTemporaryFile(
                                suffix=".pdf", delete=False
                            ) as _tmp_c:
                                _tmp_c.write(_pdf_bytes)
                                _tmp_prev = _tmp_c.name
                            try:
                                from data.saxo_connector import preview_pdf_commissions
                                with st.spinner("Loading commission preview…"):
                                    _prev_rows, _prev_warns = preview_pdf_commissions(_tmp_prev)
                                st.session_state["saxo_comm_preview"] = _prev_rows
                                st.session_state["saxo_comm_preview_warns"] = _prev_warns
                            except Exception as _exc:
                                st.error(f"Preview failed: {_exc}")
                                st.code(traceback.format_exc())

                    # ── Step 2: Show table & Apply ───────────────────────────
                    _prev_rows = st.session_state.get("saxo_comm_preview")
                    if _prev_rows is not None:
                        import pandas as _pd

                        _prev_warns = st.session_state.get("saxo_comm_preview_warns", [])
                        for _w in _prev_warns:
                            st.warning(_w)

                        _df = _pd.DataFrame([
                            {
                                "Apply": r["selected"],
                                "Trade ID": r["trade_id"],
                                "Security": r["security_name"],
                                "Action": r["action"],
                                "Date": str(r["date"]) if r["date"] else "",
                                "Commission (EUR)": r["pdf_commission"],
                                "Current Total": r["current_total"],
                                "New Total": r["new_total"],
                                "Status": r["status"],
                            }
                            for r in _prev_rows
                        ])

                        _edited = st.data_editor(
                            _df,
                            column_config={
                                "Apply": st.column_config.CheckboxColumn(
                                    "Apply", help="Select rows to update"
                                ),
                                "Commission (EUR)": st.column_config.NumberColumn(format="%.4f"),
                                "Current Total": st.column_config.NumberColumn(format="%.4f"),
                                "New Total": st.column_config.NumberColumn(format="%.4f"),
                                "Status": st.column_config.TextColumn(disabled=True),
                            },
                            disabled=[
                                "Trade ID", "Security", "Action", "Date",
                                "Commission (EUR)", "Current Total", "New Total", "Status",
                            ],
                            width='stretch',
                            key="saxo_comm_preview_editor",
                            hide_index=True,
                        )

                        _ready_count = int(_edited["Apply"].sum())
                        st.caption(f"{_ready_count} of {len(_edited)} trades selected.")

                        if st.button(
                            f"💰 Apply {_ready_count} Selected Commission(s)",
                            key="saxo_apply_commissions_btn",
                            disabled=_ready_count == 0,
                        ):
                            _pdf_bytes = st.session_state.get("saxo_pdf_bytes")
                            if not _pdf_bytes:
                                st.warning("Re-upload and parse the PDF first.")
                            else:
                                _sel_ids = set(
                                    _edited.loc[_edited["Apply"], "Trade ID"].astype(str).tolist()
                                )
                                with tempfile.NamedTemporaryFile(
                                    suffix=".pdf", delete=False
                                ) as _tmp_c:
                                    _tmp_c.write(_pdf_bytes)
                                    _tmp_comm = _tmp_c.name
                                try:
                                    from data.saxo_connector import apply_pdf_commissions
                                    with st.spinner("Updating commissions…"):
                                        _upd, _warns = apply_pdf_commissions(
                                            _tmp_comm, selected_trade_ids=_sel_ids
                                        )
                                    if _upd:
                                        st.success(f"Updated {_upd} investment record(s).")
                                        st.session_state.pop("saxo_comm_preview", None)
                                        st.session_state.pop("saxo_comm_preview_warns", None)
                                    else:
                                        st.info("No records updated.")
                                    for _w in _warns:
                                        st.warning(_w)
                                except Exception as _exc:
                                    st.error(f"Commission update failed: {_exc}")
                                    st.code(traceback.format_exc())

                    if st.button("🔄 Clear PDF / Re-upload", key="saxo_pdf_clear"):
                        st.session_state.pop("saxo_pdf_parsed",        None)
                        st.session_state.pop("saxo_pdf_charges",       None)
                        st.session_state.pop("saxo_pdf_sec_matches",   None)
                        st.session_state.pop("saxo_pdf_tmp_path",      None)
                        st.session_state.pop("saxo_pdf_bytes",         None)
                        st.session_state.pop("saxo_comm_preview",      None)
                        st.session_state.pop("saxo_comm_preview_warns",None)
                        st.rerun()


# ===========================================================================
# Brokerage section — top-level container (called from ui/importers.py)
# ===========================================================================

def render_brokerage_section() -> None:
    """Render brokerage importers as tabs."""
    tab_ib, tab_revt, tab_saxo, tab_cb, tab_cdc, tab_capital, tab_fxpro = st.tabs([
        "📊 Interactive Brokers",
        "💚 Revolut Trading",
        "📈 Saxo Bank",
        "₿ Coinbase",
        "🔷 Crypto.com",
        "📈 Capital.com",
        "📈 FxPro",
    ])

    with tab_ib:
        _brand_header("https://logo.clearbit.com/interactivebrokers.com",
                      "Interactive Brokers")
        render_ib_import()

    with tab_revt:
        _brand_header("https://logo.clearbit.com/revolut.com",
                      "Revolut Trading")
        render_revolut_brokerage_import()

    with tab_saxo:
        _brand_header("https://logo.clearbit.com/home.saxo",
                      "Saxo Bank")
        render_saxo_import()

    with tab_cb:
        _brand_header("https://logo.clearbit.com/coinbase.com", "Coinbase")
        render_coinbase_import()

    with tab_cdc:
        _brand_header("https://logo.clearbit.com/crypto.com", "Crypto.com")
        render_cryptocom_import()

    with tab_capital:
        _brand_header("https://logo.clearbit.com/capital.com",
                      "Capital.com")
        from data.capitalcom_importer import render_capitalcom_importer
        render_capitalcom_importer()

    with tab_fxpro:
        _brand_header("https://logo.clearbit.com/fxpro.com",
                      "FxPro")
        from data.fxpro_importer import render_fxpro_importer
        render_fxpro_importer()



# ===========================================================================
# Revolut Personal — CSV export
# ===========================================================================

def render_revolut_import() -> None:
    _brand_header("https://logo.clearbit.com/revolut.com", "Revolut Personal")
    st.markdown(
        "Import transactions from a **Revolut Personal** account using the "
        "CSV statement export from the Revolut app."
    )

    # ── Restore last-used settings (first render only) ───────────────────────
    from database.queries import get_app_setting, save_app_setting
    if "rev_account" not in st.session_state:
        _saved = get_app_setting("rev_account_id")
        if _saved:
            try:
                _row = _load_accounts()
                _row = _row[_row["accounts_id"] == int(_saved)]
                if not _row.empty:
                    st.session_state["rev_account"] = _row.iloc[0]["accounts_name"]
            except Exception:
                pass
    if "rev_replace" not in st.session_state:
        st.session_state["rev_replace"] = get_app_setting("rev_replace") == "true"

    with st.expander("ℹ️ How to export from Revolut (click to expand)", expanded=False):
        st.markdown("""
1. Open the **Revolut app** → tap your account.
2. Tap the **↓** (statement) icon in the top-right corner.
3. Choose a date range → select format **CSV** → tap **Download**.
4. Upload the downloaded `.csv` file below.

> **Note:** Revolut Personal has no public API, so the CSV export is the
> only programmatic option.  Upload one statement file at a time; repeat
> for each month or date range you want to import.
""")

    st.divider()

    # ── File upload ──────────────────────────────────────────────────────────
    st.markdown("### 📂 Statement File")
    uploaded = st.file_uploader(
        "Upload Revolut CSV export",
        type=["csv"],
        key="rev_csv_upload",
        help="Export from Revolut app → Account → ↓ → CSV",
    )

    st.divider()

    # ── Account mapping ──────────────────────────────────────────────────────
    st.markdown("### 🏦 Account Mapping")
    acc_id, acc_name = _account_selectbox(
        "Import into account",
        key="rev_account",
        type_filter=["Cash", "Checking", "Savings", "Other"],
    )

    st.divider()

    # ── Options ──────────────────────────────────────────────────────────────
    st.markdown("### ⚙️ Options")
    replace_mode = st.checkbox(
        "Replace mode — delete all existing Revolut records for this account before importing",
        value=False, key="rev_replace",
        help="All Investment and Transaction rows whose Description starts with "
             "'REV|' will be deleted first.",
    )

    st.divider()

    if uploaded is None:
        st.info("Upload a Revolut CSV file above to continue.")
        return

    # ── Parse ────────────────────────────────────────────────────────────────
    parse_btn = st.button("🔍 Parse & Preview", key="rev_parse", type="primary",
                          disabled=acc_id is None)

    if parse_btn or st.session_state.get("rev_parsed"):
        from data.revolut_importer import (
            parse_revolut_csv, build_records,
            investments_preview_df, transactions_preview_df,
        )

        if parse_btn:
            try:
                df_raw = parse_revolut_csv(uploaded.read())
            except Exception as exc:
                st.error(f"Failed to parse CSV: {exc}")
                return

            try:
                inv_records, tx_records = build_records(df_raw)
            except Exception as exc:
                st.error(f"Failed to build records: {exc}")
                st.code(traceback.format_exc())
                return

            st.session_state["rev_inv_records"] = inv_records
            st.session_state["rev_tx_records"]  = tx_records
            st.session_state["rev_df_raw"]       = df_raw
            st.session_state["rev_parsed"]        = True

        inv_records = st.session_state.get("rev_inv_records", [])
        tx_records  = st.session_state.get("rev_tx_records",  [])
        df_raw      = st.session_state.get("rev_df_raw",      pd.DataFrame())

        # ── Summary ──────────────────────────────────────────────────────────
        if not df_raw.empty:
            s1, s2, s3 = st.columns(3)
            s1.metric("Rows in file",        len(df_raw))
            s2.metric("Date range from",     str(df_raw["date"].min()))
            s3.metric("Date range to",       str(df_raw["date"].max()))

        if not inv_records and not tx_records:
            st.warning("No importable records found in this file.")
            return

        st.success(
            f"Parsed **{len(inv_records)}** investment records "
            f"and **{len(tx_records)}** transactions."
        )

        # ── Transaction type breakdown ────────────────────────────────────────
        if not df_raw.empty:
            with st.expander("📊 Transaction type breakdown"):
                type_counts = (
                    df_raw.groupby("type")["amount"]
                    .agg(count="count", total="sum")
                    .reset_index()
                    .sort_values("count", ascending=False)
                )
                st.dataframe(
                    type_counts,
                    hide_index=True,
                    width="stretch",
                    column_config={
                        "type":  "Type",
                        "count": st.column_config.NumberColumn("# Rows", format="%d"),
                        "total": st.column_config.NumberColumn("Net Amount", format="%.2f"),
                    },
                )

        # ── Preview ──────────────────────────────────────────────────────────
        st.markdown("### 👁️ Preview")
        _preview_records(inv_records, tx_records,
                         investments_preview_df, transactions_preview_df)

        # ── Import ───────────────────────────────────────────────────────────
        st.divider()
        st.markdown("### 💾 Import")
        st.caption(f"Target account: **{acc_name}** (ID {acc_id})")

        if st.button("✅ Confirm Import", key="rev_confirm", type="primary"):
            from data.revolut_importer import run_import
            prog = st.progress(0.0, text="Importing…")
            try:
                counts = run_import(
                    inv_records, tx_records, acc_id,
                    replace_mode=replace_mode,
                    progress_cb=lambda p: prog.progress(p, text="Importing…"),
                )
                prog.empty()
                st.success("✅ Import complete!")
                _import_summary(counts)
                try:
                    save_app_setting("rev_account_id", str(acc_id))
                    save_app_setting("rev_replace", "true" if replace_mode else "false")
                except Exception:
                    pass
                for k in ("rev_parsed", "rev_inv_records", "rev_tx_records", "rev_df_raw"):
                    st.session_state.pop(k, None)
                _load_accounts.clear()
                st.cache_data.clear()
            except Exception as exc:
                prog.empty()
                st.error(f"Import failed: {exc}")
                st.code(traceback.format_exc())
