"""Investments and Holdings API endpoints."""
from fastapi import APIRouter, Query, Body
from typing import Optional, List, Dict, Any
import math
import pandas as pd
from database.connection import get_db

router = APIRouter()

_VIABLE_CASH_ACTIONS = frozenset({'Buy', 'Sell', 'Dividend', 'IntInc', 'RtrnCap', 'MiscExp', 'MiscInc', 'CashOut', 'CashIn'})
_CASH_OUT_ACTIONS    = frozenset({'Buy', 'MiscExp', 'CashOut'})


def _find_or_create_payee(cur, name: str) -> int:
    """Return the Payees_Id for the given name, creating it if necessary."""
    cur.execute("SELECT Payees_Id FROM Payees WHERE Payees_Name = %s LIMIT 1", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("INSERT INTO Payees (Payees_Name) VALUES (%s) RETURNING Payees_Id", (name,))
    return cur.fetchone()[0]


def _build_inv_description(action: str, security: str | None, ticker: str | None,
                            quantity, price) -> str:
    """Build a human-readable description for the linked cash transaction."""
    label = ticker or security or ''
    parts = [action]
    if label:
        parts.append(label)
    if quantity is not None:
        parts.append(f"{quantity:g}x" if isinstance(quantity, float) else f"{quantity}x")
    if price is not None:
        try:
            parts.append(f"@ {float(price):,.4f}".rstrip('0').rstrip('.'))
        except (TypeError, ValueError):
            pass
    return ' '.join(parts)


def _upsert_cash_transaction(cur, inv_id: int, cash_account_id: int, inv_account_id: int,
                              date: str, action: str, total_acc_cur: float,
                              description: str | None, existing_tx_id: int | None,
                              payee_id: int | None = None):
    """Create or update the linked cash transaction for an investment entry."""
    if action not in _VIABLE_CASH_ACTIONS or not total_acc_cur:
        return
    cash_out = action in _CASH_OUT_ACTIONS
    signed = -abs(float(total_acc_cur)) if cash_out else abs(float(total_acc_cur))

    if existing_tx_id:
        cur.execute(
            "UPDATE Transactions SET date=%s, total_amount=%s, total_amount_target=%s, payees_id=%s, description=%s WHERE transactions_id=%s",
            (date, signed, abs(float(total_acc_cur)), payee_id, description, existing_tx_id),
        )
    else:
        cur.execute("""
            INSERT INTO Transactions
                (Accounts_Id, Date, Description, Total_Amount, Cleared,
                 Accounts_Id_Target, Total_Amount_Target, Transfers_Id, Payees_Id)
            VALUES (%s,%s,%s,%s,TRUE,%s,%s,NULL,%s)
            RETURNING Transactions_Id
        """, (cash_account_id, date, description, signed, inv_account_id, abs(float(total_acc_cur)), payee_id))
        tx_id = cur.fetchone()[0]
        cur.execute("UPDATE Investments SET Transactions_Id=%s WHERE Investments_Id=%s", (tx_id, inv_id))


def _df(df: pd.DataFrame) -> list:
    df = df.copy()
    for col in df.select_dtypes(include=["datetime", "datetimetz"]).columns:
        df[col] = df[col].astype(str)
    records = df.where(pd.notnull(df), other=None).to_dict(orient="records")
    return [{k: None if isinstance(v, float) and math.isnan(v) else v for k, v in r.items()} for r in records]


@router.get("/list")
def get_investments(
    account_id: Optional[int] = Query(None),
    from_date: str = Query("2000-01-01"),
    to_date: str = Query("2099-12-31"),
    action: Optional[str] = Query(None),
    ticker: Optional[str] = Query(None),
    limit: int = Query(500),
    offset: int = Query(0),
):
    acc_clause = "AND i.Accounts_Id = %(acc)s" if account_id else ""
    action_clause = "AND i.Action = %(action)s" if action else ""
    ticker_clause = "AND LOWER(s.Ticker) LIKE %(ticker)s" if ticker else ""

    query = f"""
        SELECT
            i.investments_id AS id,
            i.date::text AS date,
            i.action AS action,
            s.ticker AS ticker,
            s.securities_name AS security,
            i.quantity AS quantity,
            i.price_per_share AS price,
            i.total_amount_acccur AS total,
            i.total_amount_seccur AS total_seccur,
            i.fx_rate AS fx_rate,
            i.commission AS commission,
            i.instrument_type AS instrument_type,
            a.accounts_name AS account,
            c.currencies_shortname AS currency,
            s.securities_type AS security_type,
            i.description AS notes,
            i.transactions_id AS transactions_id,
            tx.accounts_id AS cash_account_id,
            a_cash.accounts_name AS cash_account
        FROM Investments i
        LEFT JOIN Securities s ON i.securities_id = s.securities_id
        JOIN Accounts a ON i.accounts_id = a.accounts_id
        LEFT JOIN Currencies c ON s.currencies_id = c.currencies_id
        LEFT JOIN Transactions tx ON i.transactions_id = tx.transactions_id
        LEFT JOIN Accounts a_cash ON tx.accounts_id = a_cash.accounts_id
        WHERE i.date BETWEEN %(from_date)s AND %(to_date)s
          {acc_clause} {action_clause} {ticker_clause}
        ORDER BY i.date DESC, i.investments_id DESC
        LIMIT %(limit)s OFFSET %(offset)s
    """
    params: dict = {"from_date": from_date, "to_date": to_date, "limit": limit, "offset": offset}
    if account_id:
        params["acc"] = account_id
    if action:
        params["action"] = action
    if ticker:
        params["ticker"] = f"%{ticker.lower()}%"

    with get_db() as conn:
        df = pd.read_sql(query, conn, params=params)

    count_query = f"""
        SELECT COUNT(*) AS total
        FROM Investments i
        LEFT JOIN Securities s ON i.Securities_Id = s.Securities_Id
        WHERE i.Date BETWEEN %(from_date)s AND %(to_date)s
          {acc_clause} {action_clause} {ticker_clause}
    """
    with get_db() as conn:
        total = pd.read_sql(count_query, conn, params=params).iloc[0]["total"]

    return {"total": int(total), "investments": _df(df)}


@router.get("/linked-account/{account_id}")
def get_linked_account(account_id: int):
    with get_db() as conn:
        df = pd.read_sql(
            "SELECT Accounts_Id_Linked FROM Accounts WHERE Accounts_Id = %s",
            conn, params=(account_id,)
        )
    if df.empty or pd.isna(df.iloc[0]['accounts_id_linked']):
        return {"linked_account_id": None}
    return {"linked_account_id": int(df.iloc[0]['accounts_id_linked'])}


@router.post("/transactions")
def create_investment(data: dict):
    from database.connection import get_connection
    from fastapi import HTTPException
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO Investments
                (Accounts_Id, Securities_Id, Date, Action, Quantity, Price_Per_Share,
                 Commission, FX_Rate, Total_Amount_AccCur, Total_Amount_SecCur,
                 Instrument_Type, Description)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING Investments_Id
        """, (
            data.get("accounts_id"), data.get("securities_id"), data.get("date"),
            data.get("action"), data.get("quantity"), data.get("price_per_share"),
            data.get("commission", 0), data.get("fx_rate", 1),
            data.get("total_amount_acccur"), data.get("total_amount_seccur"),
            data.get("instrument_type") or None, data.get("description"),
        ))
        inv_id = cur.fetchone()[0]

        cash_account_id = data.get("cash_account_id")
        if cash_account_id:
            sec_name, ticker = None, None
            if data.get("securities_id"):
                cur.execute("SELECT Securities_Name, Ticker FROM Securities WHERE Securities_Id = %s", (data["securities_id"],))
                sec_row = cur.fetchone()
                if sec_row:
                    sec_name, ticker = sec_row
            payee_id = _find_or_create_payee(cur, sec_name) if sec_name else None
            cash_desc = _build_inv_description(
                data.get("action", ""), sec_name, ticker,
                data.get("quantity"), data.get("price_per_share"),
            )
            _upsert_cash_transaction(
                cur, inv_id, int(cash_account_id), int(data["accounts_id"]),
                data.get("date"), data.get("action"),
                data.get("total_amount_acccur") or 0,
                cash_desc, None, payee_id,
            )

        conn.commit()
        return {"id": inv_id}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.put("/transactions/{inv_id}")
def update_investment(inv_id: int, data: dict):
    from database.connection import get_connection
    from fastapi import HTTPException
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE Investments SET
                Accounts_Id          = %s,
                Securities_Id        = %s,
                Date                 = %s,
                Action               = %s,
                Quantity             = %s,
                Price_Per_Share      = %s,
                Commission           = %s,
                FX_Rate              = %s,
                Total_Amount_AccCur  = %s,
                Total_Amount_SecCur  = %s,
                Instrument_Type      = %s,
                Description          = %s
            WHERE Investments_Id = %s
            RETURNING Transactions_Id
        """, (
            data.get("accounts_id"), data.get("securities_id"), data.get("date"),
            data.get("action"), data.get("quantity"), data.get("price_per_share"),
            data.get("commission", 0), data.get("fx_rate", 1),
            data.get("total_amount_acccur"), data.get("total_amount_seccur"),
            data.get("instrument_type") or None, data.get("description"), inv_id,
        ))
        if cur.rowcount == 0:
            raise HTTPException(404, "Not found")
        existing_tx_id = (cur.fetchone() or [None])[0]

        cash_account_id = data.get("cash_account_id")
        if cash_account_id:
            sec_name, ticker = None, None
            if data.get("securities_id"):
                cur.execute("SELECT Securities_Name, Ticker FROM Securities WHERE Securities_Id = %s", (data["securities_id"],))
                sec_row = cur.fetchone()
                if sec_row:
                    sec_name, ticker = sec_row
            payee_id = _find_or_create_payee(cur, sec_name) if sec_name else None
            cash_desc = _build_inv_description(
                data.get("action", ""), sec_name, ticker,
                data.get("quantity"), data.get("price_per_share"),
            )
            _upsert_cash_transaction(
                cur, inv_id, int(cash_account_id), int(data["accounts_id"]),
                data.get("date"), data.get("action"),
                data.get("total_amount_acccur") or 0,
                cash_desc, existing_tx_id, payee_id,
            )

        conn.commit()
        return {"id": inv_id}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.delete("/transactions/{inv_id}")
def delete_investment(inv_id: int):
    from database.connection import get_connection
    from fastapi import HTTPException
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM Investments WHERE Investments_Id = %s", (inv_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Not found")
        conn.commit()
        return {"deleted": inv_id}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.put("/holdings/{holding_id}")
def update_holding(holding_id: int, data: dict):
    from database.connection import get_connection
    from fastapi import HTTPException
    allowed = {"quantity", "staking", "simple_avg_price", "fifo_avg_price"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        raise HTTPException(400, "No valid fields")
    conn = get_connection()
    try:
        cur = conn.cursor()
        set_clause = ", ".join(f"{k} = %s" for k in updates)
        cur.execute(f"UPDATE Holdings SET {set_clause} WHERE Holdings_Id = %s",
                    list(updates.values()) + [holding_id])
        if cur.rowcount == 0:
            raise HTTPException(404, "Holding not found")
        conn.commit()
        return {"updated": holding_id}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.post("/staking-reinvest")
def staking_reinvest(entries: List[Dict[str, Any]] = Body(...)):
    """Insert Reinvest entries for staking quantity increases."""
    from database.connection import get_connection
    from fastapi import HTTPException
    try:
        from database.crud import insert_staking_reinvest
        insert_staking_reinvest(entries)
        return {"inserted": len(entries)}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/holdings")
def get_holdings(account_id: Optional[int] = Query(None), include_closed: bool = Query(False)):
    acc_clause = "AND h.Accounts_Id = %(acc)s" if account_id else ""
    params: dict = {"include_closed": include_closed}
    if account_id:
        params["acc"] = account_id

    with get_db() as conn:
        df = pd.read_sql(f"""
            SELECT
                h.Holdings_Id AS id,
                h.Accounts_Id AS account_id,
                h.Securities_Id AS securities_id,
                a.Accounts_Name AS account,
                s.Ticker AS ticker,
                s.Securities_Name AS security,
                s.Securities_Type AS security_type,
                h.Quantity AS quantity,
                h.Staking AS staking,
                h.simple_avg_price AS simple_avg_price,
                h.fifo_avg_price AS fifo_avg_price,
                c.Currencies_ShortName AS currency,
                COALESCE(
                    (SELECT Close FROM Historical_Prices WHERE Securities_Id = h.Securities_Id ORDER BY Date DESC LIMIT 1), 0
                ) AS last_price,
                COALESCE(
                    (SELECT Date FROM Historical_Prices WHERE Securities_Id = h.Securities_Id ORDER BY Date DESC LIMIT 1), NULL
                ) AS price_date,
                COALESCE(
                    (SELECT FX_Rate FROM Historical_FX WHERE Currencies_Id_1 = s.Currencies_Id ORDER BY Date DESC LIMIT 1), 1
                ) AS fx_rate,
                h.Quantity * COALESCE(
                    (SELECT Close FROM Historical_Prices WHERE Securities_Id = h.Securities_Id ORDER BY Date DESC LIMIT 1), 0
                ) * COALESCE(
                    (SELECT FX_Rate FROM Historical_FX WHERE Currencies_Id_1 = s.Currencies_Id ORDER BY Date DESC LIMIT 1), 1
                ) AS value_eur,
                h.staking AS is_staking,
                h.simple_avg_price AS avg_price,
                h.fifo_avg_price AS fifo_price
            FROM Holdings h
            JOIN Securities s ON h.Securities_Id = s.Securities_Id
            JOIN Accounts a ON h.Accounts_Id = a.Accounts_Id
            JOIN Currencies c ON s.Currencies_Id = c.Currencies_Id
            WHERE (%(include_closed)s OR h.Quantity != 0) {acc_clause}
            ORDER BY value_eur DESC
        """, conn, params=params if params else None)
    return _df(df)
