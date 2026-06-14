"""GoCardless Bank Account Data — Streamlit import tab.

OAuth flow (Streamlit-friendly)
--------------------------------
1. User enters GoCardless credentials (Secret ID + Secret Key).
2. User picks a country and institution.
3. App creates a requisition and shows a "Connect to bank" link (opens new tab).
4. User authenticates at their bank and is redirected back to the app (or any URL).
5. User clicks "I've connected — fetch transactions".
6. App retrieves accounts, lets user pick one, fetches transactions.
7. Transactions flow into the existing _render_statement_pipeline() for matching
   and reconciliation — no duplicated logic.

Credentials are stored only in st.session_state for the duration of the session.
They are never written to disk or the database.
"""

from __future__ import annotations

import streamlit as st
import pandas as pd
from datetime import date, timedelta

from database.connection import get_connection
from data.gocardless import GoCardlessClient, GoCardlessError, normalise_transactions
from config.settings import ENV_CONFIG


# ── Country list (common EU + UK) ─────────────────────────────────────────────

_COUNTRIES = {
    "Greece (GR)":          "GR",
    "Germany (DE)":         "DE",
    "France (FR)":          "FR",
    "Italy (IT)":           "IT",
    "Spain (ES)":           "ES",
    "Netherlands (NL)":     "NL",
    "Belgium (BE)":         "BE",
    "Austria (AT)":         "AT",
    "Portugal (PT)":        "PT",
    "Poland (PL)":          "PL",
    "Romania (RO)":         "RO",
    "Czech Republic (CZ)":  "CZ",
    "Hungary (HU)":         "HU",
    "Sweden (SE)":          "SE",
    "Denmark (DK)":         "DK",
    "Finland (FI)":         "FI",
    "Norway (NO)":          "NO",
    "United Kingdom (GB)":  "GB",
    "Ireland (IE)":         "IE",
    "Switzerland (CH)":     "CH",
}

_SESSION_KEY = "gc_state"  # top-level session state key


def _state() -> dict:
    if _SESSION_KEY not in st.session_state:
        st.session_state[_SESSION_KEY] = {}
    return st.session_state[_SESSION_KEY]


def _client() -> GoCardlessClient | None:
    s = _state()
    sid = s.get("secret_id", "")
    skey = s.get("secret_key", "")
    if not sid or not skey:
        return None
    return GoCardlessClient(sid, skey)


def _reset() -> None:
    st.session_state[_SESSION_KEY] = {}


def render_gocardless_import() -> None:
    """Full GoCardless import tab — called from bank_import.render_bank_section()."""

    st.markdown(
        "Connect directly to your bank via the **GoCardless Bank Account Data** (PSD2 open banking) "
        "API. No file upload needed — transactions are fetched automatically."
    )
    st.info(
        "You need a **free GoCardless Bank Account Data account** to use this feature. "
        "Sign up at [bankaccountdata.gocardless.com](https://bankaccountdata.gocardless.com) "
        "and create API keys under *User Secrets*.",
        icon="ℹ️",
    )

    s = _state()

    # ── Step 1: Credentials ──────────────────────────────────────────────────
    with st.expander("🔑 Step 1 — API Credentials", expanded=not s.get("authenticated")):
        col1, col2 = st.columns(2)
        with col1:
            sid = st.text_input(
                "Secret ID",
                value=s.get("secret_id", "") or ENV_CONFIG.get("gocardless_secret_id", ""),
                type="password",
                key="gc_secret_id",
            )
        with col2:
            skey = st.text_input(
                "Secret Key",
                value=s.get("secret_key", "") or ENV_CONFIG.get("gocardless_secret_key", ""),
                type="password",
                key="gc_secret_key",
            )

        if st.button("🔐 Authenticate", key="gc_auth"):
            if not sid or not skey:
                st.error("Both Secret ID and Secret Key are required.")
            else:
                try:
                    client = GoCardlessClient(sid, skey)
                    client.authenticate()
                    s["secret_id"]    = sid
                    s["secret_key"]   = skey
                    s["authenticated"] = True
                    # Store token inside state so the client can be rebuilt
                    s["access_token"]  = client._access_token
                    s["refresh_token"] = client._refresh_token
                    st.success("✅ Authenticated with GoCardless.")
                    st.rerun()
                except GoCardlessError as e:
                    st.error(f"Authentication failed: {e}")

    if not s.get("authenticated"):
        return

    # ── Step 2: Select institution ───────────────────────────────────────────
    with st.expander("🏦 Step 2 — Select Bank", expanded=not s.get("requisition_id")):
        country_label = st.selectbox(
            "Country",
            list(_COUNTRIES.keys()),
            index=list(_COUNTRIES.keys()).index("Greece (GR)"),
            key="gc_country",
        )
        country_code = _COUNTRIES[country_label]

        if st.button("🔍 Load institutions", key="gc_load_inst"):
            try:
                client = _rebuild_client(s)
                institutions = client.list_institutions(country_code)
                s["institutions"] = institutions
                s["country_code"] = country_code
                st.rerun()
            except GoCardlessError as e:
                st.error(f"Failed to load institutions: {e}")

        if s.get("institutions") and s.get("country_code") == country_code:
            inst_list = s["institutions"]
            inst_opts = {i["name"]: i["id"] for i in inst_list}
            sel_name = st.selectbox(
                "Bank / Institution",
                list(inst_opts.keys()),
                key="gc_institution",
            )
            sel_inst_id = inst_opts[sel_name]

            redirect_url = st.text_input(
                "Redirect URL (any URL you own, or leave as-is)",
                value="https://localhost:8501",
                key="gc_redirect",
                help="GoCardless redirects the user here after bank authentication. "
                     "Use your Streamlit app's URL if it's accessible, or any URL you own. "
                     "You just need to come back and click the button below.",
            )

            if st.button("🔗 Create connection link", key="gc_create_req"):
                try:
                    client = _rebuild_client(s)
                    req = client.create_requisition(
                        institution_id=sel_inst_id,
                        redirect_url=redirect_url,
                        reference=f"pf-{date.today().isoformat()}",
                    )
                    s["requisition_id"]   = req["id"]
                    s["requisition_link"] = req["link"]
                    s["institution_name"] = sel_name
                    st.rerun()
                except GoCardlessError as e:
                    st.error(f"Failed to create connection: {e}")

    if not s.get("requisition_id"):
        return

    # ── Step 3: Bank OAuth ───────────────────────────────────────────────────
    req_link = s["requisition_link"]
    inst_name = s.get("institution_name", "your bank")

    st.markdown("### Step 3 — Authenticate at your bank")
    st.markdown(
        f"Click the button below to open **{inst_name}**'s authentication page in a new tab. "
        "Complete the login, then come back here and click **I've connected**."
    )
    st.link_button(f"🏦 Open {inst_name} authentication", req_link)

    if st.button("✅ I've connected — fetch my accounts", key="gc_fetch_accounts", type="primary"):
        try:
            client = _rebuild_client(s)
            req = client.get_requisition(s["requisition_id"])
            accounts = req.get("accounts", [])
            if not accounts:
                st.warning(
                    "No accounts linked yet. Make sure you completed the bank authentication, "
                    "then try again. It may take a few seconds after the redirect."
                )
            else:
                s["gc_accounts"] = accounts
                s["req_status"]  = req.get("status", "")
                st.rerun()
        except GoCardlessError as e:
            st.error(f"Failed to fetch accounts: {e}")

    st.divider()
    if st.button("🔄 Start over (pick a different bank)", key="gc_reset"):
        _reset()
        st.rerun()

    if not s.get("gc_accounts"):
        return

    # ── Step 4: Pick account + date range ───────────────────────────────────
    st.markdown("### Step 4 — Select account and date range")

    gc_accounts = s["gc_accounts"]

    # Load account details to show human-readable names
    if "gc_account_details" not in s:
        try:
            client = _rebuild_client(s)
            details = {}
            for acc_id in gc_accounts:
                try:
                    d = client.get_account_details(acc_id)
                    details[acc_id] = d.get("account", {})
                except Exception:
                    details[acc_id] = {}
            s["gc_account_details"] = details
        except GoCardlessError as e:
            st.error(f"Failed to fetch account details: {e}")
            return

    details = s["gc_account_details"]

    def _acc_label(acc_id: str) -> str:
        d = details.get(acc_id, {})
        name = d.get("name") or d.get("product") or d.get("resourceId") or acc_id
        iban = d.get("iban", "")
        ccy  = d.get("currency", "")
        parts = [name]
        if iban:
            parts.append(f"IBAN: {iban[-4:]}")
        if ccy:
            parts.append(ccy)
        return " · ".join(parts)

    acc_opts = {_acc_label(a): a for a in gc_accounts}
    sel_label = st.selectbox("Bank account", list(acc_opts.keys()), key="gc_sel_acc")
    sel_gc_acc_id = acc_opts[sel_label]

    col1, col2 = st.columns(2)
    with col1:
        date_from = st.date_input(
            "From date",
            value=date.today() - timedelta(days=90),
            key="gc_date_from",
        )
    with col2:
        date_to = st.date_input(
            "To date",
            value=date.today(),
            key="gc_date_to",
        )

    # App account to reconcile against
    conn = get_connection()
    df_accs = pd.read_sql("""
        SELECT Accounts_Id AS id, Accounts_Name AS name, Accounts_Type AS type,
               Accounts_Balance AS balance
        FROM Accounts
        WHERE Accounts_Type IN ('Checking','Savings','Cash','Credit Card')
          AND Is_Active = TRUE
        ORDER BY Accounts_Name
    """, conn)
    conn.close()

    if df_accs.empty:
        st.warning("No bank accounts found in the app. Add accounts first.")
        return

    app_acc_opts = {
        f"{r['name']} ({r['type']})": (int(r['id']), float(r['balance']))
        for _, r in df_accs.iterrows()
    }
    sel_app = st.selectbox("App account to reconcile against", list(app_acc_opts.keys()), key="gc_app_acc")
    sel_app_id, sel_app_bal = app_acc_opts[sel_app]

    if st.button("📥 Fetch transactions", key="gc_fetch_txns", type="primary"):
        with st.spinner("Fetching transactions from GoCardless…"):
            try:
                client = _rebuild_client(s)
                raw = client.get_transactions(sel_gc_acc_id, date_from=date_from, date_to=date_to)
                rows = normalise_transactions(raw)
                if not rows:
                    st.warning("No booked transactions found for the selected date range.")
                else:
                    s["txn_rows"]    = rows
                    s["app_acc_id"]  = sel_app_id
                    s["app_acc_bal"] = sel_app_bal
                    st.rerun()
            except GoCardlessError as e:
                st.error(f"Failed to fetch transactions: {e}")

    if not s.get("txn_rows"):
        return

    # ── Step 5: Match & Reconcile (reuse existing pipeline) ─────────────────
    rows     = s["txn_rows"]
    acc_id   = s["app_acc_id"]
    acc_bal  = s["app_acc_bal"]

    df_stmt = pd.DataFrame(rows)
    df_stmt["date"]    = pd.to_datetime(df_stmt["date"]).dt.date
    df_stmt["amount"]  = df_stmt["amount"].astype(float)

    st.success(f"✅ Fetched **{len(df_stmt)} transactions** from GoCardless.")

    from ui.bank_import import _render_statement_pipeline
    _render_statement_pipeline(df_stmt, acc_id, acc_bal, kp="gc")

    st.divider()
    if st.button("🔄 Start over", key="gc_done_reset"):
        _reset()
        st.rerun()


def _rebuild_client(s: dict) -> GoCardlessClient:
    """Reconstruct a GoCardlessClient with cached tokens to avoid re-authenticating."""
    client = GoCardlessClient(s["secret_id"], s["secret_key"])
    client._access_token  = s.get("access_token")
    client._refresh_token = s.get("refresh_token")
    return client
