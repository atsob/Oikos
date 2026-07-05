"""Security detail endpoints: transactions, holdings, dividends, corporate actions, price anomalies."""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional
import math
import pandas as pd
from database.connection import get_db, get_connection
from api.routers.investments import _upsert_cash_transaction, _build_inv_description, _find_or_create_payee
from api.routers.register import _refresh_balance

router = APIRouter()


def _ensure_corporate_action_enum():
    """Add enum values that may not exist in older DB installs."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            DO $$ BEGIN
                ALTER TYPE corporate_action_type ADD VALUE IF NOT EXISTS 'Return of Capital';
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
        """)
        conn.commit()

_ensure_corporate_action_enum()


def _df(df: pd.DataFrame) -> list:
    df = df.copy()
    for col in df.select_dtypes(include=["datetime", "datetimetz"]).columns:
        df[col] = df[col].astype(str)
    records = df.where(pd.notnull(df), other=None).to_dict(orient="records")
    return [{k: None if isinstance(v, float) and math.isnan(v) else v for k, v in r.items()} for r in records]


# ── Investment Transactions ───────────────────────────────────────────────────

@router.get("/{sec_id}/transactions")
def get_security_transactions(sec_id: int):
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT i.investments_id AS id,
                   i.accounts_id,
                   i.securities_id,
                   a.accounts_name AS account,
                   i.date::text AS date,
                   i.action,
                   i.quantity,
                   i.price_per_share,
                   i.commission,
                   i.fx_rate,
                   i.total_amount_seccur AS total_sec_cur,
                   i.total_amount_acccur AS total_acc_cur,
                   i.tax_amount,
                   i.instrument_type,
                   c.currencies_shortname AS currency,
                   i.description,
                   i.transactions_id,
                   tx.accounts_id AS cash_account_id
            FROM investments i
            JOIN accounts a ON a.accounts_id = i.accounts_id
            LEFT JOIN currencies c ON c.currencies_id = a.currencies_id
            LEFT JOIN transactions tx ON tx.transactions_id = i.transactions_id
            WHERE i.securities_id = %(sid)s
            ORDER BY i.date DESC
        """, conn, params={"sid": sec_id})
    return _df(df)


@router.get("/{sec_id}/holdings")
def get_security_holdings(sec_id: int):
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT a.accounts_name AS account,
                   h.quantity AS qty_held,
                   ROUND((h.quantity * COALESCE(h.fifo_avg_price, h.simple_avg_price, 0))::numeric, 2) AS cost_basis
            FROM holdings h
            JOIN accounts a ON a.accounts_id = h.accounts_id
            WHERE h.securities_id = %(sid)s
              AND h.quantity != 0
            ORDER BY a.accounts_name
        """, conn, params={"sid": sec_id})
        price_df = pd.read_sql("""
            SELECT Close AS price, Date::text AS price_date
            FROM Historical_Prices
            WHERE Securities_Id = %(sid)s
            ORDER BY Date DESC LIMIT 1
        """, conn, params={"sid": sec_id})

    latest_price = float(price_df["price"].iloc[0]) if not price_df.empty else None
    price_date = price_df["price_date"].iloc[0] if not price_df.empty else None
    records = _df(df)
    for r in records:
        qty = float(r["qty_held"] or 0)
        cost = float(r["cost_basis"] or 0)
        cur_val = round(qty * latest_price, 2) if latest_price and qty else None
        r["current_value"] = cur_val
        r["unrealised_pnl"] = round(cur_val - cost, 2) if cur_val is not None else None
    return {"holdings": records, "latest_price": latest_price, "price_date": price_date}


# ── Dividend History ──────────────────────────────────────────────────────────

@router.get("/{sec_id}/dividends")
def get_security_dividends(sec_id: int):
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT dividend_id AS id,
                   ex_date::text AS ex_date,
                   pay_date::text AS pay_date,
                   amount
            FROM securities_dividends
            WHERE securities_id = %(sid)s
            ORDER BY ex_date DESC
        """, conn, params={"sid": sec_id})
    return _df(df)


# ── Corporate Actions ─────────────────────────────────────────────────────────

@router.get("/{sec_id}/corporate-actions")
def get_corporate_actions(sec_id: int):
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT corporate_actions_id AS id,
                   effective_date::text AS date,
                   action_type AS type,
                   ratio_new,
                   ratio_old,
                   gross_per_share,
                   tax_rate,
                   description,
                   created_at::text AS recorded_at
            FROM corporate_actions
            WHERE securities_id = %(sid)s
            ORDER BY effective_date DESC
        """, conn, params={"sid": sec_id})
    return _df(df)


@router.post("/{sec_id}/corporate-actions")
def create_corporate_action(sec_id: int, data: dict):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO corporate_actions
                (securities_id, action_type, effective_date, ratio_new, ratio_old, description)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING corporate_actions_id
        """, (
            sec_id, data["type"], data["date"],
            data.get("ratio_new") or None,
            data.get("ratio_old") or None,
            data.get("description") or None,
        ))
        new_id = cur.fetchone()[0]
        conn.commit()
        return {"id": new_id}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.put("/{sec_id}/corporate-actions/{ca_id}")
def update_corporate_action(sec_id: int, ca_id: int, data: dict):
    conn = get_connection()
    try:
        cur = conn.cursor()
        ca_type = data["type"]
        gross_per_share = float(data["gross_per_share"]) if data.get("gross_per_share") not in (None, "", 0) else None
        tax_rate = float(data["tax_rate"]) if data.get("tax_rate") not in (None, "") else None

        cur.execute("""
            UPDATE corporate_actions
            SET action_type=%s, effective_date=%s, ratio_new=%s, ratio_old=%s,
                gross_per_share=%s, tax_rate=%s, description=%s
            WHERE corporate_actions_id=%s AND securities_id=%s
        """, (
            ca_type, data["date"],
            data.get("ratio_new") or None,
            data.get("ratio_old") or None,
            gross_per_share, tax_rate,
            data.get("description") or None,
            ca_id, sec_id,
        ))

        # Cascade to linked investment transactions (Dividend / RtrnCap only)
        if ca_type in ("Dividend", "Return of Capital") and gross_per_share:
            tr = float(tax_rate or 0) / 100
            # Look up security currency once
            cur.execute("SELECT Currencies_Id FROM Securities WHERE Securities_Id = %s", (sec_id,))
            sec_cur_row = cur.fetchone()
            sec_cid = sec_cur_row[0] if sec_cur_row else None
            cur.execute("""
                SELECT i.investments_id, i.quantity, i.accounts_id, i.transactions_id,
                       a.currencies_id AS acc_cid, i.date::text AS inv_date
                FROM investments i
                JOIN accounts a ON a.accounts_id = i.accounts_id
                WHERE i.corporate_actions_id = %s
            """, (ca_id,))
            rows = cur.fetchall()
            for inv_id, qty, acct_id, linked_tx_id, acc_cid, inv_date in rows:
                qty = float(qty)
                gross_total = round(qty * gross_per_share, 6)
                tax_amt = -round(gross_total * tr, 6) if tr else None
                net_total = gross_total + (tax_amt or 0)
                # Resolve FX rate for this account
                if sec_cid and acc_cid and sec_cid != acc_cid:
                    cur.execute("""
                        SELECT FX_Rate FROM Historical_FX
                        WHERE Currencies_Id_1 = %s AND Date <= %s
                        ORDER BY Date DESC LIMIT 1
                    """, (sec_cid, inv_date or data["date"]))
                    fx_row = cur.fetchone()
                    fx = float(fx_row[0]) if fx_row else 1.0
                else:
                    fx = 1.0
                gross_acccur = round(gross_total * fx, 6)
                tax_acccur = round(tax_amt * fx, 6) if tax_amt is not None else None
                net_acccur = gross_acccur + (tax_acccur or 0)
                cur.execute("""
                    UPDATE investments
                    SET price_per_share=%s, total_amount_acccur=%s, total_amount_seccur=%s,
                        fx_rate=%s, tax_amount=%s
                    WHERE investments_id=%s
                """, (gross_per_share, gross_acccur, gross_total, fx, tax_acccur, inv_id))
                # Update linked cash transaction amount to net (in account currency)
                if linked_tx_id:
                    cur.execute(
                        "UPDATE transactions SET total_amount=%s WHERE transactions_id=%s",
                        (net_acccur, linked_tx_id),
                    )
                    cur.execute("SELECT accounts_id FROM transactions WHERE transactions_id=%s", (linked_tx_id,))
                    tx_row = cur.fetchone()
                    if tx_row:
                        _refresh_balance(cur, tx_row[0])

        conn.commit()
        return {"ok": True}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.delete("/{sec_id}/corporate-actions/{ca_id}")
def delete_corporate_action(sec_id: int, ca_id: int):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM corporate_actions WHERE corporate_actions_id=%s AND securities_id=%s",
            (ca_id, sec_id),
        )
        conn.commit()
        return {"deleted": cur.rowcount}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


# ── Corporate Action Preview & Execute ───────────────────────────────────────

def _get_holdings_for_ca(conn, sec_id: int, account_names: list | None, as_of_date: str | None = None):
    """Return per-account holdings as of a given date (or current if None)."""
    params: dict = {"sid": sec_id}
    clauses = []
    if account_names:
        placeholders = ", ".join(f"%(an{i})s" for i in range(len(account_names)))
        clauses.append(f"a.accounts_name IN ({placeholders})")
        for i, name in enumerate(account_names):
            params[f"an{i}"] = name
    if as_of_date:
        clauses.append("i.date <= %(as_of)s")
        params["as_of"] = as_of_date
    where_extra = ("AND " + " AND ".join(clauses)) if clauses else ""
    df = pd.read_sql(f"""
        SELECT a.accounts_id,
               a.accounts_name AS account,
               c.currencies_shortname AS currency,
               a.currencies_id AS acc_currencies_id,
               sc.currencies_shortname AS sec_currency,
               s.currencies_id AS sec_currencies_id,
               COALESCE(SUM(CASE
                   WHEN i.action IN ('Buy','ShrIn','Reinvest','Grant','Vest','Exercise') THEN i.quantity
                   WHEN i.action IN ('Sell','ShrOut','Expire') THEN -i.quantity
                   ELSE 0 END), 0) AS qty_held
        FROM investments i
        JOIN accounts a ON a.accounts_id = i.accounts_id
        LEFT JOIN currencies c ON c.currencies_id = a.currencies_id
        JOIN securities s ON s.securities_id = i.securities_id
        LEFT JOIN currencies sc ON sc.currencies_id = s.currencies_id
        WHERE i.securities_id = %(sid)s {where_extra}
        GROUP BY a.accounts_id, a.accounts_name, c.currencies_shortname, a.currencies_id,
                 sc.currencies_shortname, s.currencies_id
    """, conn, params=params)
    return df[df["qty_held"] != 0]


@router.post("/{sec_id}/corporate-actions/preview")
def preview_corporate_action(sec_id: int, data: dict):
    """Preview investment transactions that a corporate action would generate."""
    event_group = data.get("event_group")  # 'split' | 'default_delisting' | 'dividend' | 'return_of_capital'
    account_names = data.get("account_names") or None
    as_of_date = data.get("date") or None

    with get_db() as conn:
        df = _get_holdings_for_ca(conn, sec_id, account_names, as_of_date)

        # Pre-fetch FX rate if security currency differs from any account currency
        fx_cache: dict[int, float] = {}  # sec_currencies_id → fx_rate (to EUR or acc currency)
        if event_group in ("dividend", "return_of_capital") and not df.empty:
            for _, r in df.iterrows():
                sec_cid = int(r["sec_currencies_id"]) if r.get("sec_currencies_id") is not None else None
                acc_cid = int(r["acc_currencies_id"]) if r.get("acc_currencies_id") is not None else None
                if sec_cid and acc_cid and sec_cid != acc_cid and sec_cid not in fx_cache:
                    with conn.cursor() as _cur:
                        _cur.execute("""
                            SELECT FX_Rate FROM Historical_FX
                            WHERE Currencies_Id_1 = %s AND Date <= %s
                            ORDER BY Date DESC LIMIT 1
                        """, (sec_cid, as_of_date or "9999-12-31"))
                        fx_row = _cur.fetchone()
                        fx_cache[sec_cid] = float(fx_row[0]) if fx_row else 1.0

    rows = []
    if event_group == "split":
        ratio_new = float(data.get("ratio_new") or 1)
        ratio_old = float(data.get("ratio_old") or 1)
        multiplier = ratio_new / ratio_old
        for _, r in df.iterrows():
            qty = float(r["qty_held"])
            delta = round(qty * (multiplier - 1), 8)
            rows.append({
                "account": r["account"],
                "currency": r.get("currency"),
                "action": "ShrIn" if delta >= 0 else "ShrOut",
                "qty_before": qty,
                "delta": abs(delta),
                "qty_after": round(qty + delta, 8),
                "amount": 0,
            })

    elif event_group == "default_delisting":
        for _, r in df.iterrows():
            qty = float(r["qty_held"])
            if qty > 0:
                rows.append({
                    "account": r["account"],
                    "currency": r.get("currency"),
                    "action": "ShrOut",
                    "qty_before": qty,
                    "delta": qty,
                    "qty_after": 0,
                    "amount": 0,
                })

    elif event_group == "dividend":
        gross_per_share = float(data.get("gross_per_share") or 0)
        tax_rate = float(data.get("tax_rate") or 0) / 100
        net_per_share = gross_per_share * (1 - tax_rate)
        for _, r in df.iterrows():
            qty = float(r["qty_held"])
            if qty > 0:
                gross = round(qty * gross_per_share, 6)
                tax_amt = round(gross * tax_rate, 6)
                net = round(gross - tax_amt, 6)
                sec_cid = int(r["sec_currencies_id"]) if r.get("sec_currencies_id") is not None else None
                acc_cid = int(r["acc_currencies_id"]) if r.get("acc_currencies_id") is not None else None
                fx = fx_cache.get(sec_cid, 1.0) if (sec_cid and acc_cid and sec_cid != acc_cid) else 1.0
                rows.append({
                    "account": r["account"],
                    "sec_currency": r.get("sec_currency"),
                    "currency": r.get("currency"),
                    "fx_rate": fx,
                    "action": "Dividend",
                    "qty_held": qty,
                    "gross_per_share": gross_per_share,
                    "net_per_share": round(net_per_share, 6),
                    "gross_total": gross,
                    "tax": tax_amt,
                    "net_total": net,
                    "gross_total_acccur": round(gross * fx, 6),
                    "tax_acccur": round(tax_amt * fx, 6),
                    "net_total_acccur": round(net * fx, 6),
                })

    elif event_group == "return_of_capital":
        amount_per_share = float(data.get("gross_per_share") or 0)
        for _, r in df.iterrows():
            qty = float(r["qty_held"])
            if qty > 0:
                total = round(qty * amount_per_share, 6)
                sec_cid = int(r["sec_currencies_id"]) if r.get("sec_currencies_id") is not None else None
                acc_cid = int(r["acc_currencies_id"]) if r.get("acc_currencies_id") is not None else None
                fx = fx_cache.get(sec_cid, 1.0) if (sec_cid and acc_cid and sec_cid != acc_cid) else 1.0
                rows.append({
                    "account": r["account"],
                    "sec_currency": r.get("sec_currency"),
                    "currency": r.get("currency"),
                    "fx_rate": fx,
                    "action": "RtrnCap",
                    "qty_held": qty,
                    "amount_per_share": amount_per_share,
                    "total": total,
                    "total_acccur": round(total * fx, 6),
                })

    return rows


@router.post("/{sec_id}/corporate-actions/execute")
def execute_corporate_action(sec_id: int, data: dict):
    """Record the corporate action and insert the resulting investment transactions."""
    event_group = data.get("event_group")
    account_names = data.get("account_names") or None
    date = data.get("date")
    description = data.get("description") or ""

    # Map UI event group → DB action_type
    action_type_map = {
        "split": "Split" if float(data.get("ratio_new") or 1) >= float(data.get("ratio_old") or 1) else "Reverse Split",
        "default_delisting": data.get("action_type") or "Default",
        "dividend": "Dividend",
        "return_of_capital": "Return of Capital",
    }
    db_action_type = action_type_map.get(event_group, event_group)

    conn = get_connection()
    try:
        cur = conn.cursor()

        # 1. Get holdings as of the event date
        if account_names:
            placeholders = ", ".join(["%s"] * len(account_names))
            name_clause = f"AND a.accounts_name IN ({placeholders})"
            params_list = [sec_id] + list(account_names) + [date]
        else:
            name_clause = ""
            params_list = [sec_id, date]
        cur.execute(f"""
            SELECT a.accounts_id,
                   COALESCE(SUM(CASE
                       WHEN i.action IN ('Buy','ShrIn','Reinvest','Grant','Vest','Exercise') THEN i.quantity
                       WHEN i.action IN ('Sell','ShrOut','Expire') THEN -i.quantity
                       ELSE 0 END), 0) AS qty_held
            FROM investments i
            JOIN accounts a ON a.accounts_id = i.accounts_id
            WHERE i.securities_id = %s {name_clause}
              AND i.date <= %s
            GROUP BY a.accounts_id
        """, params_list)
        holdings = [(row[0], float(row[1])) for row in cur.fetchall() if row[1] != 0]

        # 2. Insert corporate_action record
        _gps = float(data["gross_per_share"]) if data.get("gross_per_share") not in (None, "", 0) else None
        _tr  = float(data["tax_rate"]) if data.get("tax_rate") not in (None, "") else None
        cur.execute("""
            INSERT INTO corporate_actions
                (securities_id, action_type, effective_date, ratio_new, ratio_old,
                 gross_per_share, tax_rate, description)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING corporate_actions_id
        """, (
            sec_id, db_action_type, date,
            data.get("ratio_new") or None,
            data.get("ratio_old") or None,
            _gps, _tr,
            description or None,
        ))
        ca_id = cur.fetchone()[0]

        # 3. Look up linked cash accounts and account currencies in one query
        acct_ids = [a for a, _ in holdings]
        linked_map: dict[int, int] = {}
        acc_currency_map: dict[int, int] = {}  # accounts_id → currencies_id
        if acct_ids:
            placeholders = ", ".join(["%s"] * len(acct_ids))
            cur.execute(
                f"SELECT Accounts_Id, Accounts_Id_Linked, Currencies_Id FROM Accounts WHERE Accounts_Id IN ({placeholders})",
                acct_ids,
            )
            for row in cur.fetchall():
                if row[1]:
                    linked_map[row[0]] = row[1]
                if row[2]:
                    acc_currency_map[row[0]] = row[2]

        # 4. Look up security name/ticker/currency once; resolve/create payee
        cur.execute("SELECT Securities_Name, Ticker, Currencies_Id FROM Securities WHERE Securities_Id = %s", (sec_id,))
        sec_row = cur.fetchone()
        sec_name = sec_row[0] if sec_row else None
        ticker = sec_row[1] if sec_row else None
        sec_currency_id = sec_row[2] if sec_row else None
        payee_id = _find_or_create_payee(cur, sec_name) if sec_name else None

        # 5. Insert investment transactions (and linked cash transactions where applicable)
        for accounts_id, qty in holdings:
            tax_amount = None
            _needs_fx = False
            if event_group == "split":
                ratio_new = float(data.get("ratio_new") or 1)
                ratio_old = float(data.get("ratio_old") or 1)
                delta = round(qty * (ratio_new / ratio_old - 1), 8)
                if delta == 0:
                    continue
                inv_action = "ShrIn" if delta > 0 else "ShrOut"
                inv_qty, price, total = abs(delta), 0, 0

            elif event_group == "default_delisting":
                if qty <= 0:
                    continue
                inv_action, inv_qty, price, total = "ShrOut", qty, 0, 0

            elif event_group == "dividend":
                if qty <= 0:
                    continue
                gross_per_share = float(data.get("gross_per_share") or 0)
                tax_rate = float(data.get("tax_rate") or 0) / 100
                inv_action = "Dividend"
                inv_qty = qty
                _needs_fx = True
                price = gross_per_share
                total = round(qty * gross_per_share, 6)
                tax_amount = -round(total * tax_rate, 6) if tax_rate else None

            elif event_group == "return_of_capital":
                if qty <= 0:
                    continue
                amount_per_share = float(data.get("gross_per_share") or 0)
                inv_action = "RtrnCap"
                inv_qty = qty
                price = amount_per_share
                total = round(qty * amount_per_share, 6)
                tax_amount = None
                _needs_fx = True

            else:
                continue

            # Resolve FX rate: convert security-currency total → account currency
            acc_currency_id = acc_currency_map.get(accounts_id)
            if _needs_fx and sec_currency_id and acc_currency_id and sec_currency_id != acc_currency_id:
                cur.execute("""
                    SELECT FX_Rate FROM Historical_FX
                    WHERE Currencies_Id_1 = %s AND Date <= %s
                    ORDER BY Date DESC LIMIT 1
                """, (sec_currency_id, date))
                fx_row = cur.fetchone()
                fx_rate = float(fx_row[0]) if fx_row else 1.0
                total_acccur = round(total * fx_rate, 6)
                tax_acccur = round(tax_amount * fx_rate, 6) if tax_amount is not None else None
            else:
                fx_rate = 1.0
                total_acccur = total
                tax_acccur = tax_amount

            cur.execute("""
                INSERT INTO investments
                    (accounts_id, securities_id, date, action, quantity,
                     price_per_share, commission, total_amount_acccur, total_amount_seccur,
                     fx_rate, description, tax_amount, corporate_actions_id)
                VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s, %s, %s)
                RETURNING Investments_Id
            """, (accounts_id, sec_id, date, inv_action, inv_qty, price, total_acccur, total, fx_rate, description, tax_acccur, ca_id))
            inv_id = cur.fetchone()[0]

            # Create linked cash transaction if the investment account has a linked cash account
            cash_account_id = linked_map.get(accounts_id)
            if cash_account_id and total_acccur:
                cash_desc = _build_inv_description(inv_action, sec_name, ticker, inv_qty, price)
                _upsert_cash_transaction(
                    cur, inv_id, cash_account_id, accounts_id,
                    date, inv_action, total_acccur, cash_desc, None, payee_id,
                    tax_amount=tax_acccur or 0.0,
                )
                _refresh_balance(cur, cash_account_id)

        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()

    from database.crud import update_holdings
    update_holdings()
    return {"ok": True, "corporate_action_id": ca_id, "transactions_inserted": len(holdings)}


# ── Price Anomalies ───────────────────────────────────────────────────────────

@router.get("/{sec_id}/price-anomalies")
def get_security_price_anomalies(sec_id: int, threshold_pct: float = Query(100.0)):
    ratio = 1.0 + threshold_pct / 100.0
    with get_db() as conn:
        df = pd.read_sql("""
            WITH pn AS (
                SELECT hp.Date::text AS date, hp.Close,
                       LAG(hp.Close)  OVER (ORDER BY hp.Date) AS prev_close,
                       LEAD(hp.Close) OVER (ORDER BY hp.Date) AS next_close
                FROM Historical_Prices hp
                WHERE hp.Securities_Id = %(sid)s AND hp.Close > 0
            )
            SELECT date, Close AS close, prev_close, next_close,
                   ROUND((Close / NULLIF(prev_close, 0))::numeric, 3) AS ratio_prev,
                   ROUND((Close / NULLIF(next_close, 0))::numeric, 3) AS ratio_next
            FROM pn
            WHERE (Close / NULLIF(prev_close, 0) >= %(r)s
                OR prev_close / NULLIF(Close, 0) >= %(r)s
                OR Close / NULLIF(next_close, 0) >= %(r)s
                OR next_close / NULLIF(Close, 0) >= %(r)s)
            ORDER BY date ASC
        """, conn, params={"sid": sec_id, "r": ratio})
    return _df(df)


@router.delete("/{sec_id}/prices/{date}")
def delete_security_price(sec_id: int, date: str):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM Historical_Prices WHERE Securities_Id=%s AND Date=%s",
            (sec_id, date),
        )
        conn.commit()
        return {"deleted": cur.rowcount}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()
