"""Bank Import & Reconciliation API endpoints."""
from __future__ import annotations

import io
import math
import pandas as pd
from datetime import date as _date
from fastapi import APIRouter, UploadFile, File, HTTPException, Query
from database.connection import get_connection, get_db

router = APIRouter()


def _df(df: pd.DataFrame) -> list:
    df = df.copy()
    for col in df.select_dtypes(include=["datetime", "datetimetz"]).columns:
        df[col] = df[col].astype(str)
    records = df.where(pd.notnull(df), other=None).to_dict(orient="records")
    return [{k: None if isinstance(v, float) and math.isnan(v) else v for k, v in r.items()} for r in records]


# ── Accounts ──────────────────────────────────────────────────────────────────

@router.get("/accounts")
def get_bank_accounts():
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT a.Accounts_Id AS id, a.Accounts_Name AS name,
                   a.Accounts_Type AS type, a.Accounts_Balance AS balance,
                   c.Currencies_ShortName AS currency
            FROM Accounts a
            LEFT JOIN Currencies c ON c.Currencies_Id = a.Currencies_Id
            WHERE a.Accounts_Type IN ('Checking','Savings','Cash','Credit Card')
              AND a.Is_Active = TRUE
            ORDER BY a.Accounts_Name
        """, conn)
    return _df(df)


@router.get("/all-accounts")
def get_all_accounts():
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT a.Accounts_Id AS id, a.Accounts_Name AS name,
                   a.Accounts_Type AS type, a.Accounts_Balance AS balance,
                   c.Currencies_ShortName AS currency
            FROM Accounts a
            LEFT JOIN Currencies c ON c.Currencies_Id = a.Currencies_Id
            WHERE a.Is_Active = TRUE
            ORDER BY a.Accounts_Name
        """, conn)
    return _df(df)


# ── Import Profiles ───────────────────────────────────────────────────────────

@router.get("/import-profiles")
def list_import_profiles():
    from database.queries import get_import_profiles, _ensure_import_tables
    conn = get_connection()
    _ensure_import_tables(conn)
    conn.close()
    df = get_import_profiles()
    return _df(df) if not df.empty else []


@router.post("/import-profiles")
def create_import_profile(data: dict):
    from database.queries import save_import_profile
    try:
        save_import_profile(data)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.delete("/import-profiles/{profile_id}")
def delete_import_profile_endpoint(profile_id: int):
    from database.queries import delete_import_profile
    try:
        delete_import_profile(profile_id)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Payee Rules ───────────────────────────────────────────────────────────────

@router.get("/payee-rules")
def list_payee_rules():
    from database.queries import get_payee_rules, _ensure_import_tables
    conn = get_connection()
    _ensure_import_tables(conn)
    conn.close()
    df = get_payee_rules()
    return _df(df) if not df.empty else []


@router.post("/payee-rules")
def create_payee_rule(data: dict):
    from database.queries import save_payee_rule
    try:
        save_payee_rule(
            data["pattern"],
            data.get("match_type", "contains"),
            data.get("payees_id"),
            data.get("categories_id"),
            int(data.get("priority", 0)),
        )
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.put("/payee-rules/{rule_id}")
def update_payee_rule_endpoint(rule_id: int, data: dict):
    from database.queries import update_payee_rule
    try:
        update_payee_rule(
            rule_id,
            data["pattern"],
            data.get("match_type", "contains"),
            data.get("payees_id"),
            data.get("categories_id"),
            int(data.get("priority", 0)),
        )
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.delete("/payee-rules/{rule_id}")
def delete_payee_rule_endpoint(rule_id: int):
    from database.queries import delete_payee_rule
    try:
        delete_payee_rule(rule_id)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Payees & Categories ───────────────────────────────────────────────────────

@router.get("/payees")
def list_payees():
    with get_db() as conn:
        df = pd.read_sql(
            "SELECT Payees_Id AS id, Payees_Name AS name FROM Payees ORDER BY Payees_Name",
            conn,
        )
    return _df(df)


@router.get("/categories")
def list_categories():
    with get_db() as conn:
        df = pd.read_sql("""
            WITH RECURSIVE ch AS (
                SELECT Categories_Id, Categories_Name::TEXT AS full_path
                FROM Categories WHERE Categories_Id_Parent IS NULL
                UNION ALL
                SELECT c.Categories_Id, ch.full_path || ' : ' || c.Categories_Name
                FROM Categories c JOIN ch ON c.Categories_Id_Parent = ch.Categories_Id
            )
            SELECT ch.Categories_Id AS id, ch.full_path AS name,
                   COUNT(s.Splits_Id) AS usage_count
            FROM ch
            LEFT JOIN Splits s ON s.Categories_Id = ch.Categories_Id
            GROUP BY ch.Categories_Id, ch.full_path
            ORDER BY usage_count DESC, ch.full_path
        """, conn)
    return _df(df)


@router.get("/payee-category-usage")
def get_payee_category_usage():
    """Return category usage counts grouped by payee name, for smart category pre-selection."""
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT p.Payees_Name AS payee_name,
                   s.Categories_Id AS category_id,
                   COUNT(*) AS usage_count
            FROM Transactions t
            JOIN Payees p ON p.Payees_Id = t.Payees_Id
            JOIN Splits s ON s.Transactions_Id = t.Transactions_Id
            WHERE s.Categories_Id IS NOT NULL
            GROUP BY p.Payees_Name, s.Categories_Id
            ORDER BY p.Payees_Name, usage_count DESC
        """, conn)
    return _df(df)


# ── Statement Parse ───────────────────────────────────────────────────────────

@router.post("/parse-statement")
async def parse_statement_endpoint(
    file: UploadFile = File(...),
    profile_id: int = Query(...),
):
    """Parse uploaded bank statement using the given import profile.
    Returns rows: date, description, amount, balance.
    """
    from database.queries import get_import_profiles
    profiles_df = get_import_profiles()
    row = profiles_df[profiles_df["profile_id"] == profile_id]
    if row.empty:
        raise HTTPException(404, f"Profile {profile_id} not found")
    profile = row.iloc[0].to_dict()

    file_bytes = await file.read()
    from api.bank_parse import parse_statement
    df = parse_statement(file_bytes, file.filename or "upload", profile)
    if df.empty:
        return {"rows": [], "error": "No valid transactions found"}
    df["date"] = df["date"].astype(str)
    return {"rows": _df(df)}


# ── App Transactions for matching ─────────────────────────────────────────────

@router.get("/app-transactions")
def get_app_transactions(account_id: int, date_from: str, date_to: str):
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT t.Transactions_Id AS id, t.Date::text AS date,
                   COALESCE(p.Payees_Name, t.Description) AS payee,
                   t.Total_Amount AS amount, t.Reconciled AS reconciled
            FROM Transactions t
            LEFT JOIN Payees p ON p.Payees_Id = t.Payees_Id
            WHERE t.Accounts_Id = %(aid)s AND t.Date BETWEEN %(d0)s AND %(d1)s
            ORDER BY t.Date
        """, conn, params={"aid": account_id, "d0": date_from, "d1": date_to})
    return _df(df)


# ── Apply Import ──────────────────────────────────────────────────────────────

@router.post("/apply-import")
def apply_import(data: dict):
    """Apply bank reconciliation: reconcile matched + import new transactions.

    data = {
      account_id: int,
      stmt_date: str | null,
      stmt_balance: float | null,
      app_balance: float | null,
      notes: str,
      rows: [{
        date: str, description: str, amount: float,
        action: 'Reconcile' | 'Import' | 'Skip',
        match_tx_id: int | null,
        already_reconciled: bool,
        payee_name: str | null,
        category_id: int | null,
      }]
    }
    """
    account_id = data["account_id"]
    rows = data.get("rows", [])
    notes = data.get("notes", "")
    stmt_date = data.get("stmt_date")
    stmt_balance = data.get("stmt_balance")
    app_balance = data.get("app_balance")

    reconcile_ids = []
    imported_count = 0
    errors = []

    conn = get_connection()
    try:
        cur = conn.cursor()
        payee_name_to_id: dict = {}
        # Load existing payees
        cur.execute("SELECT Payees_Name, Payees_Id FROM Payees")
        for pname, pid in cur.fetchall():
            payee_name_to_id[pname] = pid

        for row in rows:
            action = row.get("action", "Skip")
            if action == "Skip":
                continue
            elif action == "Reconcile":
                tx_id = row.get("match_tx_id")
                if tx_id and not row.get("already_reconciled"):
                    reconcile_ids.append(int(tx_id))
            elif action == "Import":
                try:
                    payee_name = (row.get("payee_name") or "").strip()
                    cat_id = row.get("category_id")
                    payee_id = None
                    if payee_name:
                        if payee_name not in payee_name_to_id:
                            cur.execute(
                                "INSERT INTO Payees (Payees_Name) VALUES (%s) RETURNING Payees_Id",
                                (payee_name,),
                            )
                            payee_id = cur.fetchone()[0]
                            payee_name_to_id[payee_name] = payee_id
                        else:
                            payee_id = int(payee_name_to_id[payee_name])

                    cur.execute("""
                        INSERT INTO Transactions
                            (Accounts_Id, Date, Payees_Id, Description, Total_Amount, Cleared, Reconciled)
                        VALUES (%s, %s, %s, %s, %s, TRUE, TRUE)
                        RETURNING Transactions_Id
                    """, (account_id, row["date"], payee_id, row["description"], float(row["amount"])))
                    new_tx_id = cur.fetchone()[0]
                    if cat_id:
                        cur.execute(
                            "INSERT INTO Splits (Transactions_Id, Categories_Id, Amount) VALUES (%s, %s, %s)",
                            (new_tx_id, int(cat_id), float(row["amount"])),
                        )
                    imported_count += 1
                except Exception as e:
                    errors.append(str(e))

        # Ensure import tables exist
        from database.queries import _ensure_import_tables
        _ensure_import_tables(conn)

        diff = (stmt_balance - app_balance) if stmt_balance is not None and app_balance is not None else None
        total_count = len(reconcile_ids) + imported_count

        # Insert reconciliation session
        cur.execute("""
            INSERT INTO Reconciliation_Sessions
                (Accounts_Id, Statement_Date, Statement_Balance, App_Balance, Difference, Transactions_Count, Notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING Session_Id
        """, (account_id, stmt_date, stmt_balance, app_balance, diff, total_count, notes or ""))
        session_id = cur.fetchone()[0]

        if reconcile_ids:
            placeholders = ", ".join(["%s"] * len(reconcile_ids))
            cur.execute(f"""
                UPDATE Transactions SET Reconciled = TRUE, Reconciliation_Session_Id = %s
                WHERE Transactions_Id IN ({placeholders})
            """, [session_id] + reconcile_ids)

        # Refresh balance for the account after all inserts/reconciles
        cur.execute("""
            UPDATE Accounts
               SET Accounts_Balance = COALESCE((
                   SELECT SUM(Total_Amount)
                   FROM Transactions
                   WHERE Accounts_Id = %s AND Is_Draft = FALSE
               ), 0)
             WHERE Accounts_Id = %s
        """, (account_id, account_id))

        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()

    if errors:
        return {"reconciled": len(reconcile_ids), "imported": imported_count, "errors": errors}
    return {"reconciled": len(reconcile_ids), "imported": imported_count, "errors": []}


# ── Reconciliation History ────────────────────────────────────────────────────

@router.get("/reconciliation-history-accounts")
def get_reconciliation_history_accounts():
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT DISTINCT a.accounts_id AS id, a.accounts_name AS name
            FROM Reconciliation_Sessions rs
            JOIN accounts a ON a.accounts_id = rs.Accounts_Id
            ORDER BY a.accounts_name
        """, conn)
    return _df(df)


@router.get("/reconciliation-history/{account_id}")
def get_reconciliation_history(account_id: int):
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT Session_Id AS id,
                   Session_Date::text AS session_date,
                   Statement_Date::text AS statement_date,
                   Statement_Balance AS statement_balance,
                   App_Balance AS app_balance,
                   Difference AS difference,
                   Transactions_Count AS tx_count,
                   Notes AS notes
            FROM Reconciliation_Sessions
            WHERE Accounts_Id = %(aid)s
            ORDER BY Session_Date DESC
        """, conn, params={"aid": account_id})
    return _df(df)


# ── IB Flex ───────────────────────────────────────────────────────────────────

@router.post("/ib-flex-fetch")
def ib_flex_fetch(data: dict):
    """Fetch IB Flex XML using token + query_id.

    IB's own documentation states Activity Statement Flex Queries only refresh
    once per day at close of business, and in practice a second SendRequest for
    the same query later the same day reliably fails — there's nothing to
    regenerate. To avoid that failure, the last successfully-fetched XML for
    this query is cached for the calendar day and reused automatically; pass
    force_refresh to bypass the cache and hit IB again anyway.
    """
    import datetime as _dt
    from database.queries import get_app_setting, save_app_setting

    token = data.get("token", "").strip()
    query_id = data.get("query_id", "").strip()
    force_refresh = bool(data.get("force_refresh", False))
    if not token or not query_id:
        raise HTTPException(400, "token and query_id are required")

    today_str = _dt.date.today().isoformat()
    date_key = f"ib_flex_cache_date_{query_id}"
    xml_key = f"ib_flex_cache_xml_{query_id}"

    if not force_refresh and get_app_setting(date_key) == today_str:
        cached_xml = get_app_setting(xml_key)
        if cached_xml:
            return {"xml": cached_xml, "cached": True}

    try:
        from data.ib_flex_connector import fetch_flex_xml
        xml = fetch_flex_xml(token, query_id)
        save_app_setting(date_key, today_str)
        save_app_setting(xml_key, xml)
        return {"xml": xml, "cached": False}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/ib-flex-parse")
def ib_flex_parse(data: dict):
    """Parse IB Flex XML → preview records."""
    xml = data.get("xml", "")
    account_id = data.get("account_id")
    cash_account_id = data.get("cash_account_id")
    if not xml:
        raise HTTPException(400, "xml is required")
    try:
        from data.ib_flex_connector import (
            parse_flex_xml, check_existing_records, check_fuzzy_duplicates,
            preview_security_matches,
        )
        inv_records, tx_records, meta = parse_flex_xml(xml)
        existing_inv, existing_tx = check_existing_records(
            inv_records, tx_records, account_id, cash_account_id=cash_account_id
        )
        fuzzy_inv, fuzzy_tx = check_fuzzy_duplicates(
            inv_records, tx_records, account_id, cash_account_id=cash_account_id
        )
        fuzzy_inv -= existing_inv
        fuzzy_tx -= existing_tx
        sec_matches = preview_security_matches(inv_records)

        def _annotate(records, existing, fuzzy):
            out = []
            for r in records:
                d = dict(r)
                if r["desc"] in existing:
                    d["status"] = "exists"
                elif r["desc"] in fuzzy:
                    d["status"] = "likely_dup"
                else:
                    d["status"] = "new"
                # Convert date to string
                if hasattr(d.get("date"), "isoformat"):
                    d["date"] = d["date"].isoformat()
                out.append(d)
            return out

        return {
            "inv_records": _annotate(inv_records, existing_inv, fuzzy_inv),
            "tx_records": _annotate(tx_records, existing_tx, fuzzy_tx),
            "meta": {k: str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v
                     for k, v in (meta or {}).items()},
            "sec_matches": {
                k: {"sec_id": v[0], "match_type": v[1]}
                for k, v in sec_matches.items()
            },
            "diag_dedup": {
                "existing_inv": len(existing_inv),
                "fuzzy_inv": len(fuzzy_inv),
                "existing_tx": len(existing_tx),
                "fuzzy_tx": len(fuzzy_tx),
            },
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/save-security-mappings")
def save_security_mappings_endpoint(data: dict):
    """Persist user-defined security mappings for a given importer source."""
    source   = data.get("source", "")
    mappings = data.get("mappings", {})   # {isin_or_name → securities_id}
    if not source:
        raise HTTPException(400, "source required")
    try:
        from database.queries import save_security_mappings
        save_security_mappings(source, {k: int(v) for k, v in mappings.items() if v})
        return {"saved": len(mappings)}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/ib-flex-import")
def ib_flex_import(data: dict):
    """Import IB Flex records (new only, skipping existing/fuzzy)."""
    xml = data.get("xml", "")
    account_id = data.get("account_id")
    cash_account_id = data.get("cash_account_id")
    replace_mode = data.get("replace_mode", False)
    import_inv = data.get("import_inv", True)
    import_tx = data.get("import_tx", True)
    filter_from = data.get("filter_from")
    filter_to = data.get("filter_to")
    exclude_fx_spot = data.get("exclude_fx_spot", False)

    if not xml or not account_id:
        raise HTTPException(400, "xml and account_id are required")
    try:
        from data.ib_flex_connector import (
            parse_flex_xml, check_existing_records, check_fuzzy_duplicates, run_import,
            is_fx_spot_record,
        )
        inv_records, tx_records, _ = parse_flex_xml(xml)

        if exclude_fx_spot:
            inv_records = [r for r in inv_records if not is_fx_spot_record(r)]

        if filter_from or filter_to:
            from datetime import date as _dt
            def _in_range(r):
                d = r.get("date")
                if isinstance(d, str):
                    try: d = _dt.fromisoformat(d)
                    except: return True
                if filter_from and d < _dt.fromisoformat(filter_from): return False
                if filter_to and d > _dt.fromisoformat(filter_to): return False
                return True
            inv_records = [r for r in inv_records if _in_range(r)]
            tx_records = [r for r in tx_records if _in_range(r)]

        if not replace_mode:
            existing_inv, existing_tx = check_existing_records(
                inv_records, tx_records, account_id, cash_account_id=cash_account_id
            )
            fuzzy_inv, fuzzy_tx = check_fuzzy_duplicates(
                inv_records, tx_records, account_id, cash_account_id=cash_account_id
            )
            fuzzy_inv -= existing_inv
            fuzzy_tx -= existing_tx
            inv_records = [r for r in inv_records
                           if r["desc"] not in existing_inv and r["desc"] not in fuzzy_inv]
            tx_records = [r for r in tx_records
                          if r["desc"] not in existing_tx and r["desc"] not in fuzzy_tx]

        counts = run_import(
            inv_records if import_inv else [],
            tx_records if import_tx else [],
            account_id,
            cash_account_id=cash_account_id,
            replace_mode=replace_mode,
        )
        return counts
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Generic importer settings ─────────────────────────────────────────────────

@router.get("/importer-settings/{key}")
def get_importer_settings(key: str):
    import json
    from database.queries import get_app_setting
    raw = get_app_setting(f"importer_{key}") or "{}"
    try:
        return json.loads(raw)
    except Exception:
        return {}


@router.post("/importer-settings/{key}")
def save_importer_settings(key: str, data: dict):
    import json
    from database.queries import save_app_setting
    save_app_setting(f"importer_{key}", json.dumps(data))
    return {"ok": True}


# ── Saxo Bank ─────────────────────────────────────────────────────────────────

@router.get("/saxo-settings")
def saxo_get_settings():
    import json, time
    from database.queries import get_app_setting
    expiry_str = get_app_setting("saxo_token_expiry") or "0"
    expiry = int(expiry_str) if expiry_str.isdigit() else 0
    refresh_token = get_app_setting("saxo_refresh_token") or ""
    try:
        account_map = json.loads(get_app_setting("saxo_account_map") or "{}")
    except Exception:
        account_map = {}
    return {
        "app_key":       get_app_setting("saxo_app_key") or "",
        "app_secret":    get_app_setting("saxo_app_secret") or "",
        "use_sim":       get_app_setting("saxo_use_sim") == "1",
        "redirect_uri":  get_app_setting("saxo_redirect_uri") or "http://localhost:8501",
        "refresh_token": refresh_token,
        "expires_at":    expiry,
        "token_valid":   bool(refresh_token) and expiry > int(time.time()),
        "account_map":   account_map,
    }


@router.post("/saxo-save-account-map")
def saxo_save_account_map(data: dict):
    import json
    from database.queries import save_app_setting
    save_app_setting("saxo_account_map", json.dumps(data.get("account_map", {})))
    return {"ok": True}


@router.post("/saxo-auth-url")
def saxo_auth_url(data: dict):
    try:
        from data.saxo_connector import get_auth_url
        url = get_auth_url(data["app_key"], data["redirect_uri"], use_sim=data.get("use_sim", False))
        return {"url": url}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/saxo-exchange-code")
def saxo_exchange_code(data: dict):
    try:
        from data.saxo_connector import exchange_code
        from database.queries import save_app_setting
        import time
        tok = exchange_code(
            data["app_key"], data["app_secret"], data["code"],
            data["redirect_uri"], use_sim=data.get("use_sim", False)
        )
        expiry = int(time.time()) + int(tok.get("expires_in", 1200))
        if data.get("remember"):
            save_app_setting("saxo_app_key",      data["app_key"])
            save_app_setting("saxo_app_secret",   data["app_secret"])
            save_app_setting("saxo_use_sim",      "1" if data.get("use_sim") else "0")
            save_app_setting("saxo_redirect_uri", data["redirect_uri"])
            save_app_setting("saxo_refresh_token", tok.get("refresh_token", ""))
            save_app_setting("saxo_token_expiry",  str(expiry))
        return {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token", ""), "expires_at": expiry}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@router.post("/saxo-refresh-token")
def saxo_refresh(data: dict):
    try:
        from data.saxo_connector import refresh_access_token
        from database.queries import save_app_setting
        import time
        tok = refresh_access_token(
            data["app_key"], data["app_secret"],
            data["refresh_token"], use_sim=data.get("use_sim", False)
        )
        expiry = int(time.time()) + int(tok.get("expires_in", 1200))
        save_app_setting("saxo_refresh_token", tok.get("refresh_token", data["refresh_token"]))
        save_app_setting("saxo_token_expiry", str(expiry))
        return {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token", data["refresh_token"]), "expires_at": expiry}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@router.post("/saxo-fetch-accounts")
def saxo_fetch_accounts(data: dict):
    try:
        from data.saxo_connector import fetch_client_key, fetch_accounts
        ck = fetch_client_key(data["access_token"], use_sim=data.get("use_sim", False))
        accs = fetch_accounts(data["access_token"], use_sim=data.get("use_sim", False))
        return {"client_key": ck, "accounts": accs}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/saxo-fetch-trades")
def saxo_fetch_trades(data: dict):
    """Fetch + parse trades for all mapped accounts, return preview records."""
    try:
        from data.saxo_connector import (
            fetch_trades, fetch_instrument_details, parse_trades, parse_charges,
            check_existing_records, check_fuzzy_duplicates, preview_security_matches,
        )
        access_token = data["access_token"]
        client_key = data["client_key"]
        saxo_accounts = data["saxo_accounts"]
        account_map = {a["AccountId"]: a["app_account_id"] for a in saxo_accounts if a.get("app_account_id")}
        date_from = data["date_from"]
        date_to = data["date_to"]
        use_sim = data.get("use_sim", False)

        from datetime import date as _dt, timedelta
        today = _dt.today()
        d_from = _dt.fromisoformat(date_from) if date_from else today - timedelta(days=365)
        d_to = _dt.fromisoformat(date_to) if date_to else today

        all_raw = []
        mapped_accs = [a for a in saxo_accounts if a.get("app_account_id")]
        for acc in mapped_accs:
            all_raw.extend(fetch_trades(access_token, client_key, acc["AccountKey"], d_from, d_to, use_sim=use_sim))

        uic_pairs = list({(t.get("Uic"), t.get("AssetType", "Stock")) for t in all_raw if t.get("Uic")})
        instr_cache = fetch_instrument_details(access_token, uic_pairs, use_sim=use_sim)

        inv_records = parse_trades(all_raw, instr_cache)
        charge_records = parse_charges(all_raw, instr_cache)
        existing_inv = check_existing_records(inv_records, account_map)
        fuzzy_inv = check_fuzzy_duplicates(inv_records, account_map)
        fuzzy_inv -= existing_inv
        sec_matches = preview_security_matches(inv_records)

        def _annotate(records, existing, fuzzy):
            out = []
            for r in records:
                d = dict(r)
                if r["desc"] in existing:
                    d["status"] = "exists"
                elif r["desc"] in fuzzy:
                    d["status"] = "likely_dup"
                else:
                    d["status"] = "new"
                if hasattr(d.get("date"), "isoformat"):
                    d["date"] = d["date"].isoformat()
                out.append(d)
            return out

        return {
            "inv_records": _annotate(inv_records, existing_inv, fuzzy_inv),
            "charge_records": [dict(r) for r in charge_records],
            "sec_matches": {k: {"sec_id": v[0], "match_type": v[1]} for k, v in sec_matches.items()},
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/saxo-import")
def saxo_import(data: dict):
    try:
        from data.saxo_connector import (
            fetch_trades, fetch_instrument_details, parse_trades, parse_charges,
            check_existing_records, check_fuzzy_duplicates,
            run_import, run_charges_import,
        )
        access_token = data["access_token"]
        client_key = data["client_key"]
        saxo_accounts = data["saxo_accounts"]
        account_map = {a["AccountId"]: a["app_account_id"] for a in saxo_accounts if a.get("app_account_id")}
        date_from = data["date_from"]
        date_to = data["date_to"]
        use_sim = data.get("use_sim", False)
        replace_mode = data.get("replace_mode", False)
        import_inv = data.get("import_inv", True)
        import_charges = data.get("import_charges", True)

        from datetime import date as _dt, timedelta
        today = _dt.today()
        d_from = _dt.fromisoformat(date_from) if date_from else today - timedelta(days=365)
        d_to = _dt.fromisoformat(date_to) if date_to else today

        all_raw = []
        mapped_accs = [a for a in saxo_accounts if a.get("app_account_id")]
        for acc in mapped_accs:
            all_raw.extend(fetch_trades(access_token, client_key, acc["AccountKey"], d_from, d_to, use_sim=use_sim))

        uic_pairs = list({(t.get("Uic"), t.get("AssetType", "Stock")) for t in all_raw if t.get("Uic")})
        instr_cache = fetch_instrument_details(access_token, uic_pairs, use_sim=use_sim)
        inv_records = parse_trades(all_raw, instr_cache)
        charge_records = parse_charges(all_raw, instr_cache)

        selected_descs = data.get("selected_descs")  # list of desc keys the UI wants to import; None = auto-filter

        if not replace_mode:
            if selected_descs is not None:
                # Frontend made the fuzzy-dup decision: import exactly what was checked
                selected_set = set(selected_descs)
                existing_inv = check_existing_records(inv_records, account_map)
                inv_records = [r for r in inv_records if r["desc"] in selected_set and r["desc"] not in existing_inv]
            else:
                existing_inv = check_existing_records(inv_records, account_map)
                fuzzy_inv = check_fuzzy_duplicates(inv_records, account_map)
                fuzzy_inv -= existing_inv
                inv_records = [r for r in inv_records if r["desc"] not in existing_inv and r["desc"] not in fuzzy_inv]

        counts = {}
        if import_inv and inv_records:
            counts.update(run_import(inv_records, account_map, replace_mode=replace_mode))
        if import_charges and charge_records:
            counts.update(run_charges_import(charge_records, account_map))
        return counts
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Saxo PDF ──────────────────────────────────────────────────────────────────

@router.post("/saxo-pdf-preview")
async def saxo_pdf_preview(
    file: UploadFile = File(...),
    account_id_override: str = Query(""),
):
    """Parse a Saxo Transaction & Balance Report PDF and return charge preview."""
    import tempfile, os
    try:
        from data.saxo_pdf_parser import parse_saxo_transactions_pdf
        from data.saxo_connector import preview_pdf_charge_security_matches, check_existing_records
        data = await file.read()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(data); tmp_path = tmp.name
        try:
            records = parse_saxo_transactions_pdf(tmp_path, account_id_override=account_id_override)
        finally:
            os.unlink(tmp_path)
        # Annotate with existing status
        from database.connection import get_db
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT Description FROM Investments WHERE Description LIKE 'SAXO|CHARGE|%'")
            in_db = {row[0] for row in cur.fetchall()}
        sec_matches = preview_pdf_charge_security_matches(records)
        annotated = []
        for r in records:
            d = dict(r)
            d["status"] = "exists" if d.get("desc", "") in in_db else "new"
            if hasattr(d.get("date"), "isoformat"):
                d["date"] = d["date"].isoformat()
            annotated.append(d)
        return {"records": annotated, "sec_matches": {k: {"sec_id": v[0], "match_label": v[1]} for k, v in sec_matches.items()}}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/saxo-pdf-import")
async def saxo_pdf_import(
    file: UploadFile = File(...),
    account_id: int = Query(...),
    account_id_saxo: str = Query(""),
    replace_mode: bool = Query(False),
):
    """Parse and import charges from a Saxo Transaction & Balance Report PDF."""
    import tempfile, os
    try:
        from data.saxo_pdf_parser import parse_saxo_transactions_pdf
        from data.saxo_connector import run_charges_import
        data = await file.read()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(data); tmp_path = tmp.name
        try:
            records = parse_saxo_transactions_pdf(tmp_path, account_id_override=account_id_saxo)
        finally:
            os.unlink(tmp_path)
        # Build account_map: saxo account id str → db account id
        # Extract the saxo account id from records if not overridden
        saxo_ids = {r.get("account_id_str", "") for r in records if r.get("account_id_str")}
        account_map = {sid: account_id for sid in saxo_ids} if saxo_ids else {"": account_id}
        counts = run_charges_import(records, account_map, replace_mode=replace_mode)
        return counts
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Coinbase ──────────────────────────────────────────────────────────────────

@router.get("/coinbase-settings")
def coinbase_get_settings():
    from database.queries import get_app_setting
    return {
        "api_key":    get_app_setting("cb_api_key")    or "",
        "api_secret": get_app_setting("cb_api_secret") or "",
        "account_id":      get_app_setting("cb_account_id")      or "",
        "cash_account_id": get_app_setting("cb_cash_account_id") or "",
    }


@router.post("/coinbase-test")
def coinbase_test(data: dict):
    try:
        from data.coinbase_connector import test_connection
        accounts = test_connection(data["api_key"].strip(), data["api_secret"].strip())
        if data.get("remember"):
            from database.queries import save_app_setting
            save_app_setting("cb_api_key",    data["api_key"].strip())
            save_app_setting("cb_api_secret", data["api_secret"].strip())
        return {"accounts": accounts}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/coinbase-fetch")
def coinbase_fetch(data: dict):
    try:
        from data.coinbase_connector import (
            test_connection, fetch_all_transactions, build_coinbase_records,
            check_existing_records, check_fuzzy_duplicates, preview_security_matches,
        )
        from datetime import date as _dt
        api_key    = data["api_key"].strip()
        api_secret = data["api_secret"].strip()
        acc_id     = data.get("account_id")
        cash_acc_id = data.get("cash_account_id") or None
        date_from  = data.get("date_from") or None
        date_to    = data.get("date_to")   or None

        if data.get("remember"):
            from database.queries import save_app_setting
            save_app_setting("cb_api_key",    api_key)
            save_app_setting("cb_api_secret", api_secret)
            if acc_id:      save_app_setting("cb_account_id",      str(acc_id))
            if cash_acc_id: save_app_setting("cb_cash_account_id", str(cash_acc_id))

        d_from = _dt.fromisoformat(date_from) if date_from else None
        d_to   = _dt.fromisoformat(date_to)   if date_to   else None

        accounts  = test_connection(api_key, api_secret)
        all_txns  = fetch_all_transactions(api_key, api_secret, accounts, start_date=d_from, end_date=d_to)
        inv_records, tx_records = build_coinbase_records(all_txns)

        existing_inv, existing_tx = check_existing_records(inv_records, tx_records, acc_id, cash_acc_id)
        fuzzy_inv, fuzzy_tx       = check_fuzzy_duplicates(inv_records, tx_records, acc_id, cash_acc_id)
        fuzzy_inv -= existing_inv
        fuzzy_tx  -= existing_tx
        sec_matches = preview_security_matches(inv_records)

        def _ser(r):
            d = dict(r)
            if hasattr(d.get("date"), "isoformat"): d["date"] = d["date"].isoformat()
            return d

        def _annotate(records, existing, fuzzy):
            out = []
            for r in records:
                d = _ser(r)
                d["status"] = "exists" if r["desc"] in existing else ("likely_dup" if r["desc"] in fuzzy else "new")
                out.append(d)
            return out

        def _annotate_inv(records):
            # CashIn/CashOut (no symbol) are checked against existing_tx when cash_account_id is set
            out = []
            for r in records:
                d = _ser(r)
                is_cash_flow = not r.get("symbol")
                if is_cash_flow and cash_acc_id:
                    ex, fz = existing_tx, fuzzy_tx
                else:
                    ex, fz = existing_inv, fuzzy_inv
                d["status"] = "exists" if r["desc"] in ex else ("likely_dup" if r["desc"] in fz else "new")
                out.append(d)
            return out

        return {
            "raw_count":   len(all_txns),
            "inv_records": _annotate_inv(inv_records),
            "tx_records":  _annotate(tx_records, existing_tx, fuzzy_tx),
            "sec_matches": {k: {"sec_id": v[0], "match_type": v[1]} for k, v in sec_matches.items()},
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/coinbase-import")
def coinbase_import(data: dict):
    try:
        from data.coinbase_connector import (
            test_connection, fetch_all_transactions, build_coinbase_records,
            check_existing_records, check_fuzzy_duplicates, run_coinbase_import,
        )
        from datetime import date as _dt
        api_key     = data["api_key"].strip()
        api_secret  = data["api_secret"].strip()
        acc_id      = data["account_id"]
        cash_acc_id = data.get("cash_account_id") or None
        date_from   = data.get("date_from") or None
        date_to     = data.get("date_to")   or None
        replace_mode     = data.get("replace_mode", False)
        selected_inv  = set(data["selected_inv"])  if data.get("selected_inv")  is not None else None
        selected_tx   = set(data["selected_tx"])   if data.get("selected_tx")   is not None else None

        d_from = _dt.fromisoformat(date_from) if date_from else None
        d_to   = _dt.fromisoformat(date_to)   if date_to   else None

        accounts   = test_connection(api_key, api_secret)
        all_txns   = fetch_all_transactions(api_key, api_secret, accounts, start_date=d_from, end_date=d_to)
        inv_records, tx_records = build_coinbase_records(all_txns)

        if not replace_mode:
            existing_inv, existing_tx = check_existing_records(inv_records, tx_records, acc_id, cash_acc_id)
            def _inv_existing(r):
                # CashIn/CashOut (no symbol) tracked in existing_tx when cash account set
                return r["desc"] in (existing_tx if (not r.get("symbol") and cash_acc_id) else existing_inv)
            if selected_inv is not None:
                inv_records = [r for r in inv_records if r["desc"] in selected_inv and not _inv_existing(r)]
            else:
                inv_records = [r for r in inv_records if not _inv_existing(r)]
            if selected_tx is not None:
                tx_records  = [r for r in tx_records  if r["desc"] in selected_tx  and r["desc"] not in existing_tx]
            else:
                tx_records  = [r for r in tx_records  if r["desc"] not in existing_tx]

        counts = run_coinbase_import(
            inv_records, tx_records, acc_id,
            replace_mode=replace_mode,
            cash_account_id=cash_acc_id,
        )
        return counts
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Revolut Savings ───────────────────────────────────────────────────────────

@router.post("/revolut-savings-parse")
async def revolut_savings_parse(
    file: UploadFile = File(...),
    account_id: int = Query(...),
    mode: str = Query("inv"),   # 'inv' or 'tx'
):
    try:
        from data.revolut_importer import (
            parse_revolut_savings_csv, build_savings_records, build_savings_records_as_tx,
            check_existing_records, check_fuzzy_duplicates, preview_savings_security,
        )
        file_bytes = await file.read()
        df_raw = parse_revolut_savings_csv(file_bytes)
        if mode == "inv":
            inv_records, tx_records = build_savings_records(df_raw)
        else:
            inv_records, tx_records = build_savings_records_as_tx(df_raw)

        existing_inv, existing_tx = check_existing_records(inv_records, tx_records, account_id)
        fuzzy_inv, fuzzy_tx = check_fuzzy_duplicates(inv_records, tx_records, account_id)
        fuzzy_inv -= existing_inv
        fuzzy_tx -= existing_tx

        def _annotate(records, existing, fuzzy):
            out = []
            for r in records:
                d = dict(r)
                if r["desc"] in existing:
                    d["status"] = "exists"
                elif r["desc"] in fuzzy:
                    d["status"] = "likely_dup"
                else:
                    d["status"] = "new"
                if hasattr(d.get("date"), "isoformat"):
                    d["date"] = d["date"].isoformat()
                out.append(d)
            return out

        summary = {
            "rows": int(len(df_raw)),
            "date_from": str(df_raw["date"].min()) if not df_raw.empty else None,
            "date_to":   str(df_raw["date"].max()) if not df_raw.empty else None,
        }
        result = {
            "inv_records": _annotate(inv_records, existing_inv, fuzzy_inv),
            "tx_records":  _annotate(tx_records, existing_tx, fuzzy_tx),
            "summary": summary,
        }
        if mode == "inv":
            result["sec_info"] = preview_savings_security()
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/revolut-savings-import")
async def revolut_savings_import(
    file: UploadFile = File(...),
    account_id: int = Query(...),
    mode: str = Query("inv"),
    replace_mode: bool = Query(False),
):
    try:
        from data.revolut_importer import (
            parse_revolut_savings_csv, build_savings_records, build_savings_records_as_tx,
            check_existing_records, check_fuzzy_duplicates, run_savings_import,
        )
        file_bytes = await file.read()
        df_raw = parse_revolut_savings_csv(file_bytes)
        if mode == "inv":
            inv_records, tx_records = build_savings_records(df_raw)
        else:
            inv_records, tx_records = build_savings_records_as_tx(df_raw)

        if not replace_mode:
            existing_inv, existing_tx = check_existing_records(inv_records, tx_records, account_id)
            fuzzy_inv, fuzzy_tx = check_fuzzy_duplicates(inv_records, tx_records, account_id)
            fuzzy_inv -= existing_inv
            fuzzy_tx -= existing_tx
            inv_records = [r for r in inv_records if r["desc"] not in existing_inv and r["desc"] not in fuzzy_inv]
            tx_records  = [r for r in tx_records  if r["desc"] not in existing_tx  and r["desc"] not in fuzzy_tx]

        counts = run_savings_import(inv_records, tx_records, account_id, replace_mode=replace_mode)
        return counts
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Revolut Trading ───────────────────────────────────────────────────────────

@router.post("/revolut-trading-parse")
async def revolut_trading_parse(
    file: UploadFile = File(...),
    account_id: int = Query(...),
):
    try:
        from data.revolut_importer import (
            parse_revolut_trading_csv, build_trading_records,
            check_existing_records, check_fuzzy_duplicates,
            preview_security_matches,
        )
        file_bytes = await file.read()
        df_raw = parse_revolut_trading_csv(file_bytes)
        inv_records, tx_records = build_trading_records(df_raw)
        existing_inv, existing_tx = check_existing_records(inv_records, tx_records, account_id)
        fuzzy_inv, fuzzy_tx = check_fuzzy_duplicates(inv_records, tx_records, account_id)
        fuzzy_inv -= existing_inv
        fuzzy_tx -= existing_tx
        sec_matches = preview_security_matches(inv_records)

        def _annotate(records, existing, fuzzy):
            out = []
            for r in records:
                d = dict(r)
                if r["desc"] in existing:
                    d["status"] = "exists"
                elif r["desc"] in fuzzy:
                    d["status"] = "likely_dup"
                else:
                    d["status"] = "new"
                if hasattr(d.get("date"), "isoformat"):
                    d["date"] = d["date"].isoformat()
                out.append(d)
            return out

        summary = {
            "rows": int(len(df_raw)),
            "date_from": str(df_raw["date"].min()) if not df_raw.empty else None,
            "date_to": str(df_raw["date"].max()) if not df_raw.empty else None,
        }
        return {
            "inv_records": _annotate(inv_records, existing_inv, fuzzy_inv),
            "tx_records": _annotate(tx_records, existing_tx, fuzzy_tx),
            "summary": summary,
            "sec_matches": {k: {"sec_id": v[0], "match_type": v[1]} for k, v in sec_matches.items()},
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/revolut-trading-import")
async def revolut_trading_import(
    file: UploadFile = File(...),
    account_id: int = Query(...),
    replace_mode: bool = Query(False),
    import_inv: bool = Query(True),
    import_tx: bool = Query(True),
):
    try:
        from data.revolut_importer import (
            parse_revolut_trading_csv, build_trading_records,
            check_existing_records, check_fuzzy_duplicates,
            run_trading_import,
        )
        file_bytes = await file.read()
        df_raw = parse_revolut_trading_csv(file_bytes)
        inv_records, tx_records = build_trading_records(df_raw)
        if not replace_mode:
            existing_inv, existing_tx = check_existing_records(inv_records, tx_records, account_id)
            fuzzy_inv, fuzzy_tx = check_fuzzy_duplicates(inv_records, tx_records, account_id)
            fuzzy_inv -= existing_inv
            fuzzy_tx -= existing_tx
            inv_records = [r for r in inv_records
                           if r["desc"] not in existing_inv and r["desc"] not in fuzzy_inv]
            tx_records = [r for r in tx_records
                          if r["desc"] not in existing_tx and r["desc"] not in fuzzy_tx]
        counts = run_trading_import(
            inv_records if import_inv else [],
            tx_records if import_tx else [],
            account_id,
            replace_mode=replace_mode,
        )
        return counts
    except Exception as e:
        raise HTTPException(500, str(e))
