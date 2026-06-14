"""Salt Edge Account Information API — Streamlit import tab.

Connection flow (Streamlit-friendly)
-------------------------------------
1. User enters Salt Edge App-id + Secret.
2. App creates (or reuses) a Salt Edge customer for this session.
3. App creates a connect session → shows a "Connect to bank" link.
   The Salt Edge hosted widget handles bank search + OAuth internally,
   so the user never deals with raw bank credentials.
4. User authenticates at their bank, is redirected back (or any URL).
5. User clicks "I've connected" → app retrieves the connection.
6. User picks a bank account + date range + app account.
7. Transactions flow into the existing _render_statement_pipeline()
   for matching and reconciliation — identical to CSV import.

Credentials are stored only in st.session_state for the session duration.
"""

from __future__ import annotations

import streamlit as st
import pandas as pd
from datetime import date, timedelta

from database.connection import get_connection
from data.saltedge import SaltEdgeClient, SaltEdgeError, normalise_transactions
from config.settings import ENV_CONFIG

_SESSION_KEY = "se_state"


def _state() -> dict:
    if _SESSION_KEY not in st.session_state:
        st.session_state[_SESSION_KEY] = {}
    return st.session_state[_SESSION_KEY]


def _client(s: dict) -> SaltEdgeClient:
    return SaltEdgeClient(s["app_id"], s["secret"])


def _reset() -> None:
    st.session_state[_SESSION_KEY] = {}


# ── Main render ───────────────────────────────────────────────────────────────

def render_saltedge_import() -> None:
    """Full Salt Edge import tab — called from bank_import.render_bank_section()."""

    st.markdown(
        "Connect directly to your bank via **Salt Edge** (PSD2 open banking). "
        "Supports Greek banks (Alpha Bank, Eurobank, Piraeus, NBG and more) "
        "and 5,000+ institutions across Europe."
    )
    st.info(
        "You need a **free Salt Edge developer account** to use this feature. "
        "Sign up at [saltedge.com/dashboard](https://www.saltedge.com/dashboard) "
        "and create an application to get your **App-id** and **Secret**.",
        icon="ℹ️",
    )

    s = _state()

    # ── Step 1: Credentials ──────────────────────────────────────────────────
    with st.expander("🔑 Step 1 — API Credentials", expanded=not s.get("verified")):
        col1, col2 = st.columns(2)
        with col1:
            app_id = st.text_input(
                "App-id",
                value=s.get("app_id", "") or ENV_CONFIG.get("saltedge_app_id", ""),
                type="password",
                key="se_app_id",
            )
        with col2:
            secret = st.text_input(
                "Secret",
                value=s.get("secret", "") or ENV_CONFIG.get("saltedge_secret", ""),
                type="password",
                key="se_secret",
            )

        if st.button("🔐 Verify credentials", key="se_verify"):
            if not app_id or not secret:
                st.error("Both App-id and Secret are required.")
            else:
                try:
                    # A lightweight check: list customers (empty list is fine)
                    SaltEdgeClient(app_id, secret).list_customers()
                    s["app_id"]   = app_id
                    s["secret"]   = secret
                    s["verified"] = True
                    st.success("✅ Credentials verified.")
                    st.rerun()
                except SaltEdgeError as e:
                    st.error(f"Credential check failed: {e}")

    if not s.get("verified"):
        return

    # ── Step 2: Customer ─────────────────────────────────────────────────────
    with st.expander("👤 Step 2 — Salt Edge Customer", expanded=not s.get("customer_id")):
        st.caption(
            "Salt Edge requires an end-user *customer* record to associate connections with. "
            "You only need to create this once — the ID is reused across sessions if you "
            "store it below."
        )

        existing_id = st.text_input(
            "Existing customer ID (leave blank to create new)",
            value=s.get("customer_id", ""),
            key="se_existing_customer",
            help="If you've connected before, paste your Salt Edge customer ID here "
                 "to reuse existing connections.",
        )

        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔍 Use existing customer ID", key="se_use_customer",
                         disabled=not existing_id):
                try:
                    client = _client(s)
                    customer = client.get_customer(existing_id)
                    s["customer_id"]         = str(customer["id"])
                    s["customer_identifier"] = str(customer.get("identifier", ""))
                    st.success(f"✅ Customer loaded: {s['customer_identifier']}")
                    st.rerun()
                except SaltEdgeError as e:
                    st.error(f"Failed to load customer: {e}")

        with col2:
            identifier = st.text_input(
                "New customer identifier (any unique string, e.g. your name)",
                value="personal-finance-user",
                key="se_new_identifier",
            )
            if st.button("➕ Create new customer", key="se_create_customer"):
                try:
                    client = _client(s)
                    customer = client.create_customer(identifier)
                    s["customer_id"]         = str(customer["id"])
                    s["customer_identifier"] = str(customer.get("identifier", ""))
                    st.success(
                        f"✅ Customer created. **Save this ID for future sessions:** "
                        f"`{s['customer_id']}`"
                    )
                    st.rerun()
                except SaltEdgeError as e:
                    st.error(f"Failed to create customer: {e}")

    if not s.get("customer_id"):
        return

    st.caption(f"Customer ID: `{s['customer_id']}`")

    # ── Step 3: Create connection ─────────────────────────────────────────────
    with st.expander("🏦 Step 3 — Connect to your bank", expanded=not s.get("connection_fetched")):

        return_url = st.text_input(
            "Return URL (where Salt Edge redirects after authentication)",
            value="https://localhost:8501",
            key="se_return_url",
            help="Salt Edge redirects here after the user completes bank authentication. "
                 "Use your Streamlit app's URL if it's accessible from the internet, "
                 "or any URL — you just need to come back and click the button below.",
        )

        from_date_connect = st.date_input(
            "Fetch transactions from",
            value=date.today() - timedelta(days=90),
            key="se_connect_from_date",
        )

        if st.button("🔗 Get bank connection link", key="se_get_link"):
            try:
                client = _client(s)
                session = client.create_connect_session(
                    customer_id = s["customer_id"],
                    return_to   = return_url,
                    from_date   = from_date_connect,
                )
                s["connect_url"]      = session["connect_url"]
                s["connect_expires"]  = session.get("expires_at", "")
                st.rerun()
            except SaltEdgeError as e:
                st.error(f"Failed to create connect session: {e}")

        if s.get("connect_url"):
            st.link_button("🏦 Open Salt Edge bank connection widget", s["connect_url"])
            if s.get("connect_expires"):
                st.caption(f"Link expires at: {s['connect_expires']}")

            st.markdown(
                "The Salt Edge widget lets you search for your bank, enter credentials, "
                "and authorise access — all in a secure hosted page. "
                "Once done, come back here and click the button below."
            )

            if st.button("✅ I've connected — find my connection", key="se_fetch_conn", type="primary"):
                try:
                    client = _client(s)
                    connections = client.list_connections(s["customer_id"])
                    if not connections:
                        st.warning(
                            "No connections found yet. Make sure you completed the bank "
                            "authentication in the widget, then try again."
                        )
                    else:
                        s["connections"]        = connections
                        s["connection_fetched"] = True
                        st.rerun()
                except SaltEdgeError as e:
                    st.error(f"Failed to fetch connections: {e}")

    if not s.get("connection_fetched"):
        return

    # ── Step 4: Pick connection + account + date range ────────────────────────
    st.markdown("### Step 4 — Select account and date range")

    connections = s["connections"]
    conn_opts   = {
        f"{c.get('provider_name', c.get('provider_code', 'Unknown'))} "
        f"(status: {c.get('status', '?')})": str(c["id"])
        for c in connections
    }

    sel_conn_label = st.selectbox("Bank connection", list(conn_opts.keys()), key="se_sel_conn")
    sel_conn_id    = conn_opts[sel_conn_label]

    # Load accounts for the selected connection
    acc_cache_key = f"se_accounts_{sel_conn_id}"
    if acc_cache_key not in s:
        try:
            client    = _client(s)
            se_accounts = client.list_accounts(sel_conn_id)
            s[acc_cache_key] = se_accounts
        except SaltEdgeError as e:
            st.error(f"Failed to load accounts: {e}")
            return

    se_accounts = s[acc_cache_key]
    if not se_accounts:
        st.warning("No accounts found for this connection.")
        return

    def _acc_label(acc: dict) -> str:
        name   = acc.get("name") or acc.get("nature") or "Account"
        iban   = acc.get("extra", {}).get("iban", "") if acc.get("extra") else ""
        ccy    = acc.get("currency_code", "")
        bal    = acc.get("balance")
        parts  = [name]
        if iban:
            parts.append(f"…{iban[-4:]}")
        if ccy:
            parts.append(ccy)
        if bal is not None:
            parts.append(f"{float(bal):,.2f}")
        return " · ".join(parts)

    se_acc_opts = {_acc_label(a): str(a["id"]) for a in se_accounts}
    sel_se_acc  = st.selectbox("Bank account", list(se_acc_opts.keys()), key="se_sel_acc")
    sel_se_acc_id = se_acc_opts[sel_se_acc]

    col1, col2 = st.columns(2)
    with col1:
        date_from = st.date_input(
            "From date",
            value=date.today() - timedelta(days=90),
            key="se_date_from",
        )
    with col2:
        date_to = st.date_input(
            "To date",
            value=date.today(),
            key="se_date_to",
        )

    # App account to reconcile against
    conn_db = get_connection()
    df_accs = pd.read_sql("""
        SELECT Accounts_Id AS id, Accounts_Name AS name, Accounts_Type AS type,
               Accounts_Balance AS balance
        FROM Accounts
        WHERE Accounts_Type IN ('Checking','Savings','Cash','Credit Card')
          AND Is_Active = TRUE
        ORDER BY Accounts_Name
    """, conn_db)
    conn_db.close()

    if df_accs.empty:
        st.warning("No bank accounts found in the app. Add accounts first.")
        return

    app_acc_opts = {
        f"{r['name']} ({r['type']})": (int(r['id']), float(r['balance']))
        for _, r in df_accs.iterrows()
    }
    sel_app     = st.selectbox("App account to reconcile against", list(app_acc_opts.keys()), key="se_app_acc")
    sel_app_id, sel_app_bal = app_acc_opts[sel_app]

    if st.button("📥 Fetch transactions", key="se_fetch_txns", type="primary"):
        with st.spinner("Fetching transactions from Salt Edge…"):
            try:
                client   = _client(s)
                raw_txns = client.list_transactions(
                    connection_id = sel_conn_id,
                    account_id    = sel_se_acc_id,
                    date_from     = date_from,
                    date_to       = date_to,
                )
                rows = normalise_transactions(raw_txns)
                if not rows:
                    st.warning("No transactions found for the selected date range.")
                else:
                    s["txn_rows"]    = rows
                    s["app_acc_id"]  = sel_app_id
                    s["app_acc_bal"] = sel_app_bal
                    st.rerun()
            except SaltEdgeError as e:
                st.error(f"Failed to fetch transactions: {e}")

    if not s.get("txn_rows"):
        return

    # ── Step 5: Match & Reconcile ─────────────────────────────────────────────
    rows    = s["txn_rows"]
    acc_id  = s["app_acc_id"]
    acc_bal = s["app_acc_bal"]

    df_stmt = pd.DataFrame(rows)
    df_stmt["date"]   = pd.to_datetime(df_stmt["date"]).dt.date
    df_stmt["amount"] = df_stmt["amount"].astype(float)

    st.success(f"✅ Fetched **{len(df_stmt)} transactions** from Salt Edge.")

    from ui.bank_import import _render_statement_pipeline
    _render_statement_pipeline(df_stmt, acc_id, acc_bal, kp="se")

    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 Fetch different account / date range", key="se_refetch"):
            for k in ["txn_rows", "app_acc_id", "app_acc_bal"]:
                s.pop(k, None)
            st.rerun()
    with col2:
        if st.button("🔌 Disconnect & start over", key="se_full_reset"):
            _reset()
            st.rerun()
