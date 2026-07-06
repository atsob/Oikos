"""Investments and Holdings API endpoints."""
from fastapi import APIRouter, Query, Body
from typing import Optional, List, Dict, Any
import math
import pandas as pd
from database.connection import get_db
from api.routers.register import _refresh_balance

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
                              payee_id: int | None = None,
                              tax_amount: float = 0.0):
    """Create or update the linked cash transaction for an investment entry.

    tax_amount is the withholding tax (negative, account currency).
    The transfer amount is net = total_acc_cur + tax_amount.
    """
    if action not in _VIABLE_CASH_ACTIONS or not total_acc_cur:
        return
    cash_out = action in _CASH_OUT_ACTIONS
    gross = float(total_acc_cur)
    tax   = float(tax_amount or 0.0)
    net   = gross + tax          # tax is negative, so this reduces the receipt
    signed = -abs(net) if cash_out else abs(net)

    if existing_tx_id:
        cur.execute(
            "UPDATE Transactions SET date=%s, total_amount=%s, total_amount_target=%s, accounts_id_target=%s, payees_id=%s, description=%s WHERE transactions_id=%s",
            (date, signed, abs(net), inv_account_id, payee_id, description, existing_tx_id),
        )
    else:
        cur.execute("""
            INSERT INTO Transactions
                (Accounts_Id, Date, Description, Total_Amount, Cleared,
                 Accounts_Id_Target, Total_Amount_Target, Transfers_Id, Payees_Id)
            VALUES (%s,%s,%s,%s,TRUE,%s,%s,NULL,%s)
            RETURNING Transactions_Id
        """, (cash_account_id, date, description, signed, inv_account_id, abs(net), payee_id))
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
            s.securities_id AS securities_id,
            s.ticker AS ticker,
            s.securities_name AS security,
            i.quantity AS quantity,
            i.price_per_share AS price,
            i.total_amount_acccur AS total,
            i.total_amount_seccur AS total_seccur,
            i.fx_rate AS fx_rate,
            i.commission AS commission,
            i.tax_amount AS tax_amount,
            i.instrument_type AS instrument_type,
            a.accounts_name AS account,
            c.currencies_shortname AS currency,
            ac.currencies_shortname AS account_currency,
            s.securities_type AS security_type,
            i.description AS notes,
            i.transactions_id AS transactions_id,
            tx.accounts_id AS cash_account_id,
            a_cash.accounts_name AS cash_account
        FROM Investments i
        LEFT JOIN Securities s ON i.securities_id = s.securities_id
        JOIN Accounts a ON i.accounts_id = a.accounts_id
        LEFT JOIN Currencies c ON s.currencies_id = c.currencies_id
        LEFT JOIN Currencies ac ON a.currencies_id = ac.currencies_id
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
                 Instrument_Type, Description, Tax_Amount)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING Investments_Id
        """, (
            data.get("accounts_id"), data.get("securities_id"), data.get("date"),
            data.get("action"), data.get("quantity"), data.get("price_per_share"),
            data.get("commission", 0), data.get("fx_rate", 1),
            data.get("total_amount_acccur"), data.get("total_amount_seccur"),
            data.get("instrument_type") or None, data.get("description"),
            data.get("tax_amount") or None,
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
                tax_amount=float(data.get("tax_amount") or 0),
            )
            _refresh_balance(cur, int(cash_account_id))

        conn.commit()
        from database.crud import update_holdings
        update_holdings()
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
                Description          = %s,
                Tax_Amount           = %s
            WHERE Investments_Id = %s
            RETURNING Transactions_Id
        """, (
            data.get("accounts_id"), data.get("securities_id"), data.get("date"),
            data.get("action"), data.get("quantity"), data.get("price_per_share"),
            data.get("commission", 0), data.get("fx_rate", 1),
            data.get("total_amount_acccur"), data.get("total_amount_seccur"),
            data.get("instrument_type") or None, data.get("description"),
            data.get("tax_amount") or None, inv_id,
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
                tax_amount=float(data.get("tax_amount") or 0),
            )
            _refresh_balance(cur, int(cash_account_id))

        conn.commit()
        from database.crud import update_holdings
        update_holdings()
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
        # Fetch the linked cash transaction before deleting
        cur.execute(
            "SELECT Transactions_Id FROM Investments WHERE Investments_Id = %s", (inv_id,)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Not found")
        linked_tx_id = row[0]

        cur.execute("SELECT Accounts_Id, Securities_Id FROM Investments WHERE Investments_Id = %s", (inv_id,))
        inv_row = cur.fetchone()
        if not inv_row:
            raise HTTPException(404, "Not found")
        inv_account_id = inv_row[0]

        cur.execute("DELETE FROM Investments WHERE Investments_Id = %s", (inv_id,))

        # Remove the linked cash transaction and refresh its account balance
        if linked_tx_id:
            cur.execute(
                "SELECT Accounts_Id FROM Transactions WHERE Transactions_Id = %s", (linked_tx_id,)
            )
            tx_row = cur.fetchone()
            cash_account_id = tx_row[0] if tx_row else None
            cur.execute("DELETE FROM Transactions WHERE Transactions_Id = %s", (linked_tx_id,))
            if cash_account_id:
                _refresh_balance(cur, cash_account_id)

        conn.commit()

        # Recalculate holdings after the commit so the deleted row is gone
        from database.crud import update_holdings
        update_holdings()

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


# ── Investment Transfer / Convert ────────────────────────────────────────────
# Moves a holding from one account to another — same security (a pure custody
# transfer, cost basis carried over, no gain/loss) or different security (a
# conversion/swap, which realizes gain/loss on the source and establishes a
# fresh cost basis on the destination, since exchanging one asset for another
# is a taxable disposal in virtually every crypto/securities tax regime).
# Optional fee, payable in the source security, the destination security, or
# cash from any account. preview() and execute() share the same planning
# logic so what you confirm is exactly what gets written.

def _account_info(cur, account_id: int):
    cur.execute("""
        SELECT a.Accounts_Name, a.Currencies_Id, c.Currencies_ShortName
        FROM Accounts a JOIN Currencies c ON c.Currencies_Id = a.Currencies_Id
        WHERE a.Accounts_Id = %s
    """, (account_id,))
    row = cur.fetchone()
    if not row:
        raise ValueError(f"Account {account_id} not found")
    return {"id": account_id, "name": row[0], "currencies_id": row[1], "currency": row[2]}


def _security_info(cur, securities_id: int):
    cur.execute("""
        SELECT s.Securities_Name, s.Ticker, s.Currencies_Id, c.Currencies_ShortName
        FROM Securities s JOIN Currencies c ON c.Currencies_Id = s.Currencies_Id
        WHERE s.Securities_Id = %s
    """, (securities_id,))
    row = cur.fetchone()
    if not row:
        raise ValueError(f"Security {securities_id} not found")
    return {"id": securities_id, "name": row[0], "ticker": row[1], "currencies_id": row[2], "currency": row[3]}


def _cost_basis(cur, account_id: int, securities_id: int) -> float:
    """Current cost per share (security currency) — Simple Avg (a running WAC
    that resets on full closure) preferred, falling back to FIFO Avg."""
    cur.execute(
        "SELECT COALESCE(Simple_Avg_Price, Fifo_Avg_Price, 0) FROM Holdings WHERE Accounts_Id=%s AND Securities_Id=%s",
        (account_id, securities_id),
    )
    row = cur.fetchone()
    return float(row[0]) if row and row[0] is not None else 0.0


def _convert_currency(cur, amount: float, from_ccy_id: int, to_ccy_id: int, date: str) -> float:
    """Convert `amount` (in from_ccy_id) into to_ccy_id using the same
    Historical_FX lookup every Investments row already relies on."""
    from database.crud import resolve_investment_fx
    if from_ccy_id == to_ccy_id or not amount:
        return amount
    converted, _fx = resolve_investment_fx(cur, amount, from_ccy_id, to_ccy_id, date)
    return converted


def _row_amounts(cur, acc: dict, sec: dict, quantity: float, price_per_share: float, date: str) -> dict:
    """Compute FX_Rate/Total_Amount_SecCur/Total_Amount_AccCur for one planned
    row exactly the way a manually-entered transaction would."""
    from database.crud import resolve_investment_fx
    total_seccur = quantity * price_per_share
    # resolve_investment_fx's `fx_rate` is account-currency-units per 1 security-currency-unit
    # regardless of the placeholder total passed in, so 1.0 is enough to extract just the rate.
    _, fx_rate = resolve_investment_fx(cur, 1.0, acc["currencies_id"], sec["currencies_id"], date)
    total_acccur = total_seccur * fx_rate
    return {
        "accounts_id": acc["id"], "account_name": acc["name"], "account_currency": acc["currency"],
        "securities_id": sec["id"], "security_name": sec["name"], "security_ticker": sec["ticker"],
        "security_currency": sec["currency"], "date": date,
        "quantity": quantity, "price_per_share": price_per_share, "fx_rate": fx_rate,
        "total_amount_seccur": total_seccur, "total_amount_acccur": total_acccur,
    }


def _plan_transfer(cur, p: dict) -> dict:
    date = p["date"]
    from_acc_id = int(p["from_account_id"])
    to_acc_id = int(p["to_account_id"])
    from_sec_id = int(p["from_securities_id"])
    to_sec_id = int(p["to_securities_id"])
    qty_sent = float(p["quantity"])
    fee_type = p.get("fee_type") or "none"
    fee_qty = float(p["fee_quantity"]) if p.get("fee_quantity") not in (None, "") else 0.0
    fee_cash_amount = float(p["fee_cash_amount"]) if p.get("fee_cash_amount") not in (None, "") else 0.0
    fee_cash_account_id = p.get("fee_cash_account_id")
    description = p.get("description") or "Transfer"

    if qty_sent <= 0:
        raise ValueError("Quantity must be greater than zero")
    if from_acc_id == to_acc_id and from_sec_id == to_sec_id:
        raise ValueError("From and To are identical — nothing to transfer")
    if fee_type in ("source", "destination") and fee_qty <= 0:
        raise ValueError("Enter a fee quantity, or set fee to None")
    if fee_type == "cash" and (fee_cash_amount <= 0 or not fee_cash_account_id):
        raise ValueError("Enter a fee amount and account, or set fee to None")

    from database.crud import _lookup_hist_price

    from_acc = _account_info(cur, from_acc_id)
    to_acc = _account_info(cur, to_acc_id)
    from_sec = _security_info(cur, from_sec_id)
    to_sec = _security_info(cur, to_sec_id)
    cost_basis_from = _cost_basis(cur, from_acc_id, from_sec_id)
    market_price_from = _lookup_hist_price(cur, from_sec_id, date)
    is_same_security = from_sec_id == to_sec_id

    rows: list[dict] = []

    def add_row(acc, sec, action, quantity, price_per_share, desc, pnl=None, pnl_ccy=None):
        r = _row_amounts(cur, acc, sec, quantity, price_per_share, date)
        r.update({"action": action, "description": desc, "estimated_pnl": pnl, "estimated_pnl_currency": pnl_ccy})
        rows.append(r)

    if is_same_security:
        # ── Mode A: custody transfer — cost basis carries over unchanged ────
        transferred_qty = qty_sent
        if fee_type == "source":
            transferred_qty = qty_sent - fee_qty
            if transferred_qty <= 0:
                raise ValueError("Fee quantity must be less than the quantity sent")
            pnl = (market_price_from - cost_basis_from) * fee_qty
            add_row(from_acc, from_sec, "Sell", fee_qty, market_price_from, f"{description} — transfer fee",
                    pnl * _fx_rate_only(cur, from_acc["currencies_id"], from_sec["currencies_id"], date), from_acc["currency"])

        add_row(from_acc, from_sec, "ShrOut", transferred_qty, cost_basis_from, description)
        add_row(to_acc, to_sec, "ShrIn", transferred_qty, cost_basis_from, description)

        if fee_type == "destination":
            if fee_qty >= transferred_qty:
                raise ValueError("Fee quantity must be less than the quantity received")
            pnl = (market_price_from - cost_basis_from) * fee_qty
            add_row(to_acc, to_sec, "Sell", fee_qty, market_price_from, f"{description} — transfer fee",
                    pnl * _fx_rate_only(cur, to_acc["currencies_id"], to_sec["currencies_id"], date), to_acc["currency"])
    else:
        # ── Mode B: conversion/swap — a real disposal of the source security ─
        market_price_to = _lookup_hist_price(cur, to_sec_id, date)
        if market_price_to <= 0:
            raise ValueError(f"No price data available for {to_sec['name']} on {date}")

        net_qty = qty_sent - fee_qty if fee_type == "source" else qty_sent
        if net_qty <= 0:
            raise ValueError("Fee quantity must be less than the quantity sent")

        # Source leg: split into the converting portion and the fee portion (when the fee
        # is taken from the source) so the fee shows as its own labeled row instead of
        # being silently folded into a single lump-sum Sell.
        pnl_rate_from = market_price_from - cost_basis_from
        add_row(from_acc, from_sec, "Sell", net_qty, market_price_from, description,
                pnl_rate_from * net_qty * _fx_rate_only(cur, from_acc["currencies_id"], from_sec["currencies_id"], date), from_acc["currency"])
        if fee_type == "source":
            add_row(from_acc, from_sec, "Sell", fee_qty, market_price_from, f"{description} — transfer fee",
                    pnl_rate_from * fee_qty * _fx_rate_only(cur, from_acc["currencies_id"], from_sec["currencies_id"], date), from_acc["currency"])

        # Bridge value: from-security ccy -> from-account ccy -> to-account ccy -> to-security ccy
        value = net_qty * market_price_from
        value = _convert_currency(cur, value, from_sec["currencies_id"], from_acc["currencies_id"], date)
        value = _convert_currency(cur, value, from_acc["currencies_id"], to_acc["currencies_id"], date)
        value = _convert_currency(cur, value, to_acc["currencies_id"], to_sec["currencies_id"], date)
        buy_qty = value / market_price_to

        if fee_type == "destination":
            if fee_qty >= buy_qty:
                raise ValueError("Fee quantity must be less than the converted amount")
            # Destination leg: record the full converted amount, then an explicit fee
            # disposal at the same (just-established) market price, instead of silently
            # buying a smaller quantity with no record of why.
            add_row(to_acc, to_sec, "Buy", buy_qty, market_price_to, description)
            add_row(to_acc, to_sec, "Sell", fee_qty, market_price_to, f"{description} — transfer fee", 0.0, to_acc["currency"])
        else:
            add_row(to_acc, to_sec, "Buy", buy_qty, market_price_to, description)

    cash_fee = None
    if fee_type == "cash":
        fee_acc = _account_info(cur, int(fee_cash_account_id))
        cash_fee = {
            "accounts_id": int(fee_cash_account_id), "account_name": fee_acc["name"],
            "account_currency": fee_acc["currency"],
            "amount": -abs(fee_cash_amount), "description": f"{description} — transfer fee",
        }

    return {
        "rows": rows, "cash_fee": cash_fee,
        "from_account": from_acc["name"], "to_account": to_acc["name"],
        "from_security": from_sec["name"], "to_security": to_sec["name"],
        "is_conversion": not is_same_security,
    }


def _fx_rate_only(cur, acc_ccy: int, sec_ccy: int, date: str) -> float:
    from database.crud import resolve_investment_fx
    _, fx = resolve_investment_fx(cur, 1.0, acc_ccy, sec_ccy, date)
    return fx


@router.post("/transfer/preview")
def preview_transfer(data: dict = Body(...)):
    from database.connection import get_connection
    from fastapi import HTTPException
    conn = get_connection()
    try:
        cur = conn.cursor()
        try:
            return _plan_transfer(cur, data)
        except ValueError as e:
            raise HTTPException(400, str(e))
    finally:
        conn.close()


@router.post("/transfer/execute")
def execute_transfer(data: dict = Body(...)):
    from database.connection import get_connection
    from fastapi import HTTPException
    conn = get_connection()
    try:
        cur = conn.cursor()
        try:
            plan = _plan_transfer(cur, data)
        except ValueError as e:
            raise HTTPException(400, str(e))

        cur.execute("SELECT nextval('transfers_id_seq')")
        shared_tid = cur.fetchone()[0]

        inserted_ids = []
        for r in plan["rows"]:
            cur.execute("""
                INSERT INTO Investments
                    (Accounts_Id, Securities_Id, Date, Action, Quantity, Price_Per_Share,
                     Commission, FX_Rate, Total_Amount_AccCur, Total_Amount_SecCur,
                     Description, Transfers_Id)
                VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s, %s)
                RETURNING Investments_Id
            """, (
                r["accounts_id"], r["securities_id"], r["date"], r["action"],
                r["quantity"], r["price_per_share"], r["fx_rate"],
                r["total_amount_acccur"], r["total_amount_seccur"],
                r["description"], shared_tid,
            ))
            inserted_ids.append(cur.fetchone()[0])

        if plan["cash_fee"]:
            cf = plan["cash_fee"]
            cur.execute("""
                INSERT INTO Transactions (Accounts_Id, Date, Description, Total_Amount, Cleared)
                VALUES (%s, %s, %s, %s, TRUE)
            """, (cf["accounts_id"], data["date"], cf["description"], cf["amount"]))
            _refresh_balance(cur, cf["accounts_id"])

        conn.commit()
        from database.crud import update_holdings
        update_holdings()
        return {"investments_ids": inserted_ids, "transfers_id": shared_tid}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()
