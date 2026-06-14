"""Security detail endpoints: transactions, holdings, dividends, corporate actions, price anomalies."""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional
import math
import pandas as pd
from database.connection import get_db, get_connection

router = APIRouter()


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
                   a.accounts_name AS account,
                   i.date::text AS date,
                   i.action,
                   i.quantity,
                   i.price_per_share,
                   i.commission,
                   i.total_amount_seccur AS total_sec_cur,
                   i.total_amount_acccur AS total_acc_cur,
                   c.currencies_shortname AS currency,
                   i.description
            FROM investments i
            JOIN accounts a ON a.accounts_id = i.accounts_id
            LEFT JOIN currencies c ON c.currencies_id = a.currencies_id
            WHERE i.securities_id = %(sid)s
            ORDER BY i.date DESC
        """, conn, params={"sid": sec_id})
    return _df(df)


@router.get("/{sec_id}/holdings")
def get_security_holdings(sec_id: int):
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT a.accounts_name AS account,
                   COALESCE(SUM(CASE
                       WHEN i.action IN ('Buy','ShrIn','Reinvest','Grant','Vest','Exercise') THEN i.quantity
                       WHEN i.action IN ('Sell','ShrOut','Expire') THEN -i.quantity
                       ELSE 0 END), 0) AS qty_held,
                   COALESCE(SUM(CASE
                       WHEN i.action = 'Buy' THEN i.total_amount_acccur
                       WHEN i.action IN ('Sell','ShrOut') THEN -i.total_amount_acccur
                       ELSE 0 END), 0) AS cost_basis
            FROM investments i
            JOIN accounts a ON a.accounts_id = i.accounts_id
            WHERE i.securities_id = %(sid)s
            GROUP BY a.accounts_name
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
        cur.execute("""
            UPDATE corporate_actions
            SET action_type=%s, effective_date=%s, ratio_new=%s, ratio_old=%s, description=%s
            WHERE corporate_actions_id=%s AND securities_id=%s
        """, (
            data["type"], data["date"],
            data.get("ratio_new") or None,
            data.get("ratio_old") or None,
            data.get("description") or None,
            ca_id, sec_id,
        ))
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

def _get_holdings_for_ca(conn, sec_id: int, account_names: list | None):
    """Return per-account current holdings != 0 for a security, optionally filtered by account name."""
    params: dict = {"sid": sec_id}
    if account_names:
        placeholders = ", ".join(f"%(an{i})s" for i in range(len(account_names)))
        clause = f"AND a.accounts_name IN ({placeholders})"
        for i, name in enumerate(account_names):
            params[f"an{i}"] = name
    else:
        clause = ""
    df = pd.read_sql(f"""
        SELECT a.accounts_id,
               a.accounts_name AS account,
               c.currencies_shortname AS currency,
               COALESCE(SUM(CASE
                   WHEN i.action IN ('Buy','ShrIn','Reinvest','Grant','Vest','Exercise') THEN i.quantity
                   WHEN i.action IN ('Sell','ShrOut','Expire') THEN -i.quantity
                   ELSE 0 END), 0) AS qty_held
        FROM investments i
        JOIN accounts a ON a.accounts_id = i.accounts_id
        LEFT JOIN currencies c ON c.currencies_id = a.currencies_id
        WHERE i.securities_id = %(sid)s {clause}
        GROUP BY a.accounts_id, a.accounts_name, c.currencies_shortname
    """, conn, params=params)
    return df[df["qty_held"] != 0]


@router.post("/{sec_id}/corporate-actions/preview")
def preview_corporate_action(sec_id: int, data: dict):
    """Preview investment transactions that a corporate action would generate."""
    event_group = data.get("event_group")  # 'split' | 'default_delisting' | 'dividend'
    account_names = data.get("account_names") or None

    with get_db() as conn:
        df = _get_holdings_for_ca(conn, sec_id, account_names)

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
                rows.append({
                    "account": r["account"],
                    "currency": r.get("currency"),
                    "action": "Dividend",
                    "qty_held": qty,
                    "gross_per_share": gross_per_share,
                    "net_per_share": round(net_per_share, 6),
                    "gross_total": gross,
                    "tax": tax_amt,
                    "net_total": net,
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
    }
    db_action_type = action_type_map.get(event_group, event_group)

    conn = get_connection()
    try:
        cur = conn.cursor()

        # 1. Get current holdings
        if account_names:
            placeholders = ", ".join(["%s"] * len(account_names))
            name_clause = f"AND a.accounts_name IN ({placeholders})"
            params_list = [sec_id] + list(account_names)
        else:
            name_clause = ""
            params_list = [sec_id]
        cur.execute(f"""
            SELECT a.accounts_id,
                   COALESCE(SUM(CASE
                       WHEN i.action IN ('Buy','ShrIn','Reinvest','Grant','Vest','Exercise') THEN i.quantity
                       WHEN i.action IN ('Sell','ShrOut','Expire') THEN -i.quantity
                       ELSE 0 END), 0) AS qty_held
            FROM investments i
            JOIN accounts a ON a.accounts_id = i.accounts_id
            WHERE i.securities_id = %s {name_clause}
            GROUP BY a.accounts_id
        """, params_list)
        holdings = [(row[0], float(row[1])) for row in cur.fetchall() if row[1] != 0]

        # 2. Insert corporate_action record
        cur.execute("""
            INSERT INTO corporate_actions
                (securities_id, action_type, effective_date, ratio_new, ratio_old, description)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING corporate_actions_id
        """, (
            sec_id, db_action_type, date,
            data.get("ratio_new") or None,
            data.get("ratio_old") or None,
            description or None,
        ))
        ca_id = cur.fetchone()[0]

        # 3. Insert investment transactions
        for accounts_id, qty in holdings:
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
                net_per_share = gross_per_share * (1 - tax_rate)
                inv_action = "Dividend"
                inv_qty = qty
                price = net_per_share
                total = round(qty * net_per_share, 6)

            else:
                continue

            cur.execute("""
                INSERT INTO investments
                    (accounts_id, securities_id, date, action, quantity,
                     price_per_share, commission, total_amount_acccur, total_amount_seccur,
                     fx_rate, description)
                VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s, 1.0, %s)
            """, (accounts_id, sec_id, date, inv_action, inv_qty, price, total, total, description))

        conn.commit()
        return {"ok": True, "corporate_action_id": ca_id, "transactions_inserted": len(holdings)}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


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
