"""Margin positions (FX Spot, CFDs): what they do to cash.

A broker books a margin product — XAGEUR on Saxo, a CFD on an ETF — very differently from a share. Opening one moves only its cost (the spread or commission) through the
cash account; holding it costs the financing charges (already imported as their own MiscExp rows); closing it moves only the realised profit or loss. The notional — the
10 oz of silver at 48 — never leaves the cash. Oikos used to book every Buy and Sell of these like a share purchase, with the whole notional as the cash leg, so the cash
account of such a broker read lower than the broker's own figure by the notional of the open positions.

Now, for every Investments row in a Margin account (Accounts_Type = 'Margin') whose Instrument_Type is a margin one (MARGIN_TYPES) and that has a linked cash leg — both
conditions, so a CFD row in an ordinary brokerage account is left as it was until the account itself is marked Margin:

  Buy   cash leg  = - the commission              (Total_Amount_AccCur minus the commission is the notional, which stays out of the cash)
        offset leg = - the notional                 (on the account's "Margin financing" liability account)
  Sell  cash leg  = proceeds - cost of the lots closed (first in, first out): the realised profit or loss
        offset leg = + the cost of the lots closed

When the broker says what it booked for a trade (Investments.Booked_Cash_AccCur — Saxo's "Booked amount"), that signed amount is the cash leg, for margin trades and for every
other trade that carries one; the rules above are only the derivation used when it is not known. It is set by the importer, and for Saxo's history from its Transaction and
balance report; editing a trade by hand (quantity, price, amounts, date, action) drops it again.

The positions themselves are untouched — quantity, price, cost basis, holdings, every report — so the position still shows at its market value, as the broker's own
Market value / Exposure does. The Margin financing account holds minus the open cost, so cash + positions + financing = the broker's equity: net worth is exactly what it
was, and the cash is the broker's cash. Each Investments row's offset leg is kept in Margin_Legs.

`resync_all()` recomputes all of it from the Investments rows and fixes whatever differs, so it is safe to call after any write, and a repeat changes nothing.
"""
from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_EVEN

from database.connection import get_connection

log = logging.getLogger(__name__)

MARGIN_TYPES = ("FX Spot", "CFD", "CFDOnETF", "CFDOnStock", "CFDOnIndex", "CFDOnFutures", "CFDOnFund")
SUFFIX_COMMISSION = " — margin: commission"
SUFFIX_PNL = " — margin: realised P&L"
_CENT = Decimal("0.01")
_READY = False


def _q(x: Decimal) -> Decimal:
    return x.quantize(_CENT, rounding=ROUND_HALF_EVEN)


def plan_series(rows: list[dict]) -> dict[int, tuple[Decimal, Decimal]]:
    """The cash and offset amounts of one (account, security) series of margin rows, oldest first.

    rows: dicts with id, action, quantity, commission, total (Total_Amount_AccCur). Returns {investments_id: (cash, offset)}, signed as they are booked
    (cash: negative = leaves the cash account; offset: negative = the financing liability grows).
    Lots are matched first in, first out; a sale of more than is held closes what is there and treats the rest as costing nothing."""
    lots: list[list[Decimal]] = []          # [quantity, cost]
    out: dict[int, tuple[Decimal, Decimal]] = {}
    for r in rows:
        qty, comm, total = Decimal(r["quantity"] or 0), Decimal(r["commission"] or 0), Decimal(r["total"] or 0)
        act = r["action"]
        if act in ("Buy", "ShrIn"):
            notional = total - comm if act == "Buy" else total
            lots.append([qty, notional])
            if act == "Buy":
                out[r["id"]] = (_q(-comm), _q(-notional))
        elif act in ("Sell", "ShrOut"):
            need, cost = qty, Decimal(0)
            while need > 0 and lots:
                take = min(need, lots[0][0])
                part = lots[0][1] * (take / lots[0][0])
                cost += part
                lots[0][0] -= take
                lots[0][1] -= part
                need -= take
                if lots[0][0] <= 0:
                    lots.pop(0)
            if act == "Sell":
                out[r["id"]] = (_q(total - cost), _q(cost))
    return out


def ensure_schema() -> None:
    """Make the two helper tables (once per process), on a connection of their own so that a caller's rollback cannot leave the flag set without them."""
    global _READY
    if _READY:
        return
    conn = get_connection()
    try:
        _make_tables(conn.cursor())
        conn.commit()
    finally:
        conn.close()
    _READY = True


def _make_tables(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS Margin_Offset_Accounts (
            Accounts_Id        INTEGER PRIMARY KEY REFERENCES Accounts(Accounts_Id) ON DELETE CASCADE,
            Offset_Accounts_Id INTEGER NOT NULL REFERENCES Accounts(Accounts_Id)
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS Margin_Legs (
            Transactions_Id INTEGER PRIMARY KEY REFERENCES Transactions(Transactions_Id) ON DELETE CASCADE,
            Investments_Id  INTEGER UNIQUE REFERENCES Investments(Investments_Id) ON DELETE SET NULL
        )""")


def _offset_account(cur, inv_account_id: int, cache: dict) -> int:
    """The liability account that carries the open notional of an investment account's margin positions (made on first use)."""
    if inv_account_id in cache:
        return cache[inv_account_id]
    cur.execute("SELECT Offset_Accounts_Id FROM Margin_Offset_Accounts WHERE Accounts_Id = %s", (inv_account_id,))
    row = cur.fetchone()
    if row:
        cache[inv_account_id] = row[0]
        return row[0]
    cur.execute("SELECT Accounts_Name, Currencies_Id, Institutions_Id FROM Accounts WHERE Accounts_Id = %s", (inv_account_id,))
    name, cur_id, inst_id = cur.fetchone()
    cur.execute("""INSERT INTO Accounts (Accounts_Name, Accounts_Type, Institutions_Id, Currencies_Id, Is_Active, Accounts_Balance, Notes)
                   VALUES (%s, 'Liability', %s, %s, TRUE, 0, %s) RETURNING Accounts_Id""",
                (f"{name} (Margin financing)", inst_id, cur_id,
                 "Kept by Oikos: minus the open cost of this account's margin positions (FX Spot, CFDs), so that cash + positions + this account = the broker's equity. See database/margin.py."))
    off_id = cur.fetchone()[0]
    cur.execute("INSERT INTO Margin_Offset_Accounts (Accounts_Id, Offset_Accounts_Id) VALUES (%s, %s)", (inv_account_id, off_id))
    cache[inv_account_id] = off_id
    return off_id


def resync_all(conn=None) -> dict:
    """Bring every margin position's cash leg and offset leg in line with the rules above. Own connection and commit unless `conn` is given (then the caller commits).
    Returns counts. Never raises into a caller's write: a failure is logged and reported in the result."""
    own = conn is None
    if own:
        conn = get_connection()
    result = {"cash_legs": 0, "offset_legs": 0, "removed": 0, "accounts": []}
    try:
        ensure_schema()
        cur = conn.cursor()
        types = list(MARGIN_TYPES)
        cur.execute("""
            SELECT i.Investments_Id, i.Accounts_Id, i.Securities_Id, s.Ticker, i.Date, i.Action, i.Quantity, i.Price_Per_Share, COALESCE(i.Commission, 0),
                   i.Total_Amount_AccCur, i.Transactions_Id, t.Accounts_Id, t.Total_Amount, t.Description, i.Booked_Cash_AccCur
            FROM Investments i
            JOIN Securities s ON s.Securities_Id = i.Securities_Id
            JOIN Accounts a ON a.Accounts_Id = i.Accounts_Id AND a.Accounts_Type = 'Margin'
            LEFT JOIN Transactions t ON t.Transactions_Id = i.Transactions_Id
            WHERE i.Instrument_Type::text = ANY(%s) AND i.Action IN ('Buy', 'Sell', 'ShrIn', 'ShrOut')
            ORDER BY i.Accounts_Id, i.Securities_Id, i.Date, i.Investments_Id""", (types,))
        rows = cur.fetchall()
        cur.execute("SELECT l.Investments_Id, l.Transactions_Id, t.Total_Amount, t.Date, t.Description FROM Margin_Legs l JOIN Transactions t USING (Transactions_Id) WHERE l.Investments_Id IS NOT NULL")
        legs = {r[0]: r[1:] for r in cur.fetchall()}

        series: dict[tuple, list[dict]] = {}
        for (iid, acc, sec, tick, dt, act, qty, px, comm, total, txid, cash_acc, tx_amt, tx_desc, booked) in rows:
            series.setdefault((acc, sec), []).append(dict(id=iid, acc=acc, sec=sec, ticker=tick, date=dt, action=act, quantity=qty, price=px, commission=comm,
                                                         total=total, tx=txid, cash_acc=cash_acc, tx_amt=tx_amt, tx_desc=tx_desc, booked=booked))
        touched, wanted, qualified, off_cache = set(), set(), set(), {}
        for rs in series.values():
            plan = plan_series(rs)
            for r in rs:
                if r["id"] not in plan or not r["tx"]:
                    continue                                   # not a cash-moving row, or no linked cash leg to adjust
                cash, offset = plan[r["id"]]
                if r["booked"] is not None:                    # what the broker booked beats the derivation (commission estimates, FX on the P&L, credits)
                    cash = _q(Decimal(r["booked"]))
                qualified.add(r["id"])
                # cash leg
                base = (r["tx_desc"] or "").replace(SUFFIX_COMMISSION, "").replace(SUFFIX_PNL, "")
                desc = base + (SUFFIX_COMMISSION if r["action"] == "Buy" else SUFFIX_PNL)
                if Decimal(r["tx_amt"]) != cash or r["tx_desc"] != desc:
                    cur.execute("UPDATE Transactions SET Total_Amount = %s, Total_Amount_Target = %s, Description = %s WHERE Transactions_Id = %s",
                                (cash, abs(cash), desc, r["tx"]))
                    result["cash_legs"] += 1
                    touched.add(r["cash_acc"])
                touched.add(r["acc"])
                # offset leg
                off_acc = _offset_account(cur, r["acc"], off_cache)
                leg = legs.get(r["id"])
                label = f"Margin financing — {r['action']} {r['ticker']} {float(r['quantity']):g}x @ {float(r['price']):g}"
                if offset == 0:
                    continue
                wanted.add(r["id"])
                if leg is None:
                    cur.execute("""INSERT INTO Transactions (Accounts_Id, Date, Description, Total_Amount, Cleared, Accounts_Id_Target, Total_Amount_Target, Transfers_Id)
                                   VALUES (%s, %s, %s, %s, TRUE, NULL, NULL, NULL) RETURNING Transactions_Id""", (off_acc, r["date"], label, offset))
                    cur.execute("INSERT INTO Margin_Legs (Transactions_Id, Investments_Id) VALUES (%s, %s)", (cur.fetchone()[0], r["id"]))
                    result["offset_legs"] += 1
                    touched.add(off_acc)
                elif Decimal(leg[1]) != offset or leg[2] != r["date"] or leg[3] != label:
                    cur.execute("UPDATE Transactions SET Total_Amount = %s, Date = %s, Description = %s WHERE Transactions_Id = %s", (offset, r["date"], label, leg[0]))
                    result["offset_legs"] += 1
                    touched.add(off_acc)
        # every other trade that carries a booked amount: its cash leg is that amount
        cur.execute("""SELECT i.Investments_Id, t.Transactions_Id, t.Accounts_Id, i.Accounts_Id, i.Booked_Cash_AccCur, t.Total_Amount
                       FROM Investments i JOIN Transactions t ON t.Transactions_Id = i.Transactions_Id WHERE i.Booked_Cash_AccCur IS NOT NULL""")
        for iid, txid, cash_acc, inv_acc, booked, tx_amt in cur.fetchall():
            if iid in qualified:
                continue
            want = _q(Decimal(booked))
            if Decimal(tx_amt) != want:
                cur.execute("UPDATE Transactions SET Total_Amount = %s, Total_Amount_Target = %s WHERE Transactions_Id = %s", (want, abs(want), txid))
                result["cash_legs"] += 1
                touched.add(cash_acc)
                touched.add(inv_acc)
        # legs of rows that are gone, no longer margin, or now offset nothing
        cur.execute("SELECT l.Transactions_Id, l.Investments_Id, t.Accounts_Id FROM Margin_Legs l JOIN Transactions t USING (Transactions_Id)")
        for txid, iid, acc in cur.fetchall():
            if iid is None or iid not in wanted:
                if iid is not None and iid not in qualified:
                    _restore_cash_leg(cur, iid, touched)             # the row no longer qualifies (account type or Instrument changed): its cash leg is the ordinary one again
                cur.execute("DELETE FROM Transactions WHERE Transactions_Id = %s", (txid,))
                result["removed"] += 1
                touched.add(acc)
        if touched:
            from database.crud import refresh_account_balance
            refresh_account_balance(cur, *sorted(a for a in touched if a))
            result["accounts"] = sorted(a for a in touched if a)
        if own:
            conn.commit()
    except Exception as e:                                                # a margin recompute never breaks the write it follows
        log.exception("margin resync failed")
        if own:
            conn.rollback()
        result["error"] = str(e)
    finally:
        if own:
            conn.close()
    return result


def _restore_cash_leg(cur, inv_id: int, touched: set) -> None:
    """Give a trade that is no longer a margin position the cash leg of an ordinary one: the whole amount (net of withholding tax), out for a Buy, in for a Sell."""
    cur.execute("""SELECT i.Action, i.Total_Amount_AccCur, COALESCE(i.Tax_Amount, 0), i.Transactions_Id, t.Accounts_Id, t.Description, i.Booked_Cash_AccCur
                   FROM Investments i JOIN Transactions t ON t.Transactions_Id = i.Transactions_Id WHERE i.Investments_Id = %s""", (inv_id,))
    row = cur.fetchone()
    if not row or row[0] not in ("Buy", "Sell"):
        return
    action, total, tax, txid, cash_acc, desc, booked = row
    net = abs(Decimal(total or 0) + Decimal(tax or 0))
    signed = -net if action == "Buy" else net
    if booked is not None:
        signed, net = Decimal(booked), abs(Decimal(booked))
    cur.execute("UPDATE Transactions SET Total_Amount = %s, Total_Amount_Target = %s, Description = %s WHERE Transactions_Id = %s",
                (signed, net, (desc or "").replace(SUFFIX_COMMISSION, "").replace(SUFFIX_PNL, ""), txid))
    touched.add(cash_acc)


# ── what the Cash Register may not change ───────────────────────────────────────────────────────
# The legs this module keeps are derived from the trades: the offset legs on a Margin financing account, and the cash legs of the margin trades themselves. Changing their
# amount, date or description would only be undone at the next recompute, so the Cash Register refuses it and says where the change belongs: the trade.

def managed_sql(alias: str = "t") -> str:
    """A SQL boolean: the transaction `alias` is one of the legs above. Needs ensure_schema() to have run."""
    types = ", ".join(f"'{t}'" for t in MARGIN_TYPES)
    return (f"(EXISTS (SELECT 1 FROM Margin_Legs ml WHERE ml.Transactions_Id = {alias}.Transactions_Id) "
            f"OR EXISTS (SELECT 1 FROM Investments mi JOIN Accounts ma ON ma.Accounts_Id = mi.Accounts_Id AND ma.Accounts_Type = 'Margin' "
            f"WHERE mi.Transactions_Id = {alias}.Transactions_Id AND mi.Instrument_Type::text IN ({types}) AND mi.Action IN ('Buy', 'Sell')))")


def managed_kinds(cur, tx_ids) -> dict[int, str]:
    """{transaction id: 'financing' (an offset leg) or 'cash' (the cash leg of a margin trade)} for those of `tx_ids` that are managed."""
    ids = [int(i) for i in tx_ids if i is not None]
    if not ids:
        return {}
    ensure_schema()
    types = list(MARGIN_TYPES)
    cur.execute("SELECT Transactions_Id FROM Margin_Legs WHERE Transactions_Id = ANY(%s)", (ids,))
    out = {r[0]: "financing" for r in cur.fetchall()}
    cur.execute("""SELECT DISTINCT mi.Transactions_Id FROM Investments mi JOIN Accounts ma ON ma.Accounts_Id = mi.Accounts_Id AND ma.Accounts_Type = 'Margin'
                   WHERE mi.Transactions_Id = ANY(%s) AND mi.Instrument_Type::text = ANY(%s) AND mi.Action IN ('Buy', 'Sell')""", (ids, types))
    for (t,) in cur.fetchall():
        out.setdefault(t, "cash")
    return out


def is_financing_account(cur, accounts_id) -> bool:
    if accounts_id in (None, ""):
        return False
    ensure_schema()
    cur.execute("SELECT 1 FROM Margin_Offset_Accounts WHERE Offset_Accounts_Id = %s", (int(accounts_id),))
    return cur.fetchone() is not None


# What may still be changed on a managed leg: flags always; on a cash leg also payee and category (its amount, date, description and accounts follow the trade).
_ALWAYS_OK = {"cleared", "reconciled"}
_CASH_LOCKED = {"date", "description", "total_amount", "accounts_id_target", "is_draft", "accounts_id"}


def refuse_if_managed(cur, tx_ids, changing: set | None = None) -> None:
    """Raise a 409 when one of `tx_ids` is a managed leg and the change is not one that is still allowed. `changing` is the set of fields being changed (None: the transaction is
    being deleted or moved, which is never allowed)."""
    from fastapi import HTTPException
    kinds = managed_kinds(cur, tx_ids)
    if not kinds:
        return
    for kind in kinds.values():
        if changing is not None:
            if kind == "financing" and changing <= _ALWAYS_OK:
                continue
            if kind == "cash" and not (changing & _CASH_LOCKED):
                continue
        where = "a Margin financing entry" if kind == "financing" else "the cash side of a margin (FX Spot / CFD) trade"
        raise HTTPException(409, f"This is {where}, kept by Oikos from the trade itself: change the trade in Investments instead (its amount, date and description cannot be edited here).")


def refuse_managed(tx_ids, changing: set | None = None) -> None:
    """refuse_if_managed on a short connection of its own, to be called at the top of a write endpoint (before its own try block, whose `except Exception` would turn the 409 into a 500)."""
    conn = get_connection()
    try:
        refuse_if_managed(conn.cursor(), tx_ids, changing)
    finally:
        conn.close()


def refuse_financing_account(*account_ids) -> None:
    """A 409 when a new transaction or transfer is aimed at a Margin financing account: nothing is entered there by hand."""
    from fastapi import HTTPException
    conn = get_connection()
    try:
        cur = conn.cursor()
        if any(is_financing_account(cur, a) for a in account_ids):
            raise HTTPException(409, "A Margin financing account is kept by Oikos from the margin trades: nothing is entered in it by hand.")
    finally:
        conn.close()


def resync_quietly() -> None:
    """For the end of a write endpoint or an import: resync, ignore the outcome."""
    try:
        resync_all()
    except Exception:
        log.exception("margin resync failed")
