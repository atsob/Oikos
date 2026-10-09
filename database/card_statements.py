"""Credit-card statements and the payment templates that settle them.

A credit card has a *statement day* (Accounts.Statement_Day): the day of the month on which the
outstanding balance to be paid is issued. A Recurring Template that pays the card (a transfer into
it, flagged Is_Card_Payment) does not carry a fixed amount — the amount is whatever is owed on the
statement that payment settles:

    S      = the latest statement date on or before the payment's due date D
    owed   = the card's balance at the end of day S          (everything up to S, future-dated
                                                              entries included)
             less any credits after S and before D             (payments or refunds already made)
    amount = -owed                                            (an outflow from the paying account)

Everything bought after S belongs to the next statement. The refresh job (scheduler, daily) keeps
each flagged template's Total_Amount at this figure; draft generation recomputes it at the moment
the draft is created, and the Cash Flow Forecast's account alerts simulate it for every future
payment (api/routers/cash_alerts.py).
"""
from __future__ import annotations

import calendar
import logging
from datetime import date

log = logging.getLogger(__name__)


def _clamped(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def issue_date_on_or_before(statement_day: int, d: date) -> date:
    """Latest statement issue date <= d (a statement day past the month's end falls on its last day)."""
    c = _clamped(d.year, d.month, statement_day)
    if c <= d:
        return c
    return _clamped(d.year - 1, 12, statement_day) if d.month == 1 else _clamped(d.year, d.month - 1, statement_day)


def issue_date_after(statement_day: int, d: date) -> date:
    """First statement issue date strictly after d."""
    c = _clamped(d.year, d.month, statement_day)
    if c > d:
        return c
    return _clamped(d.year + 1, 1, statement_day) if d.month == 12 else _clamped(d.year, d.month + 1, statement_day)


def amount_owed(cur, card_id: int, due_date: date, statement_day: int) -> dict:
    """What the statement settled by a payment due on `due_date` comes to (see module docstring).
    Confirmed transactions only — a pending draft isn't posted yet."""
    s = issue_date_on_or_before(statement_day, due_date)
    cur.execute("""
        SELECT COALESCE(SUM(Total_Amount), 0)::float FROM Transactions
        WHERE Accounts_Id = %s AND Date <= %s AND NOT COALESCE(Is_Draft, FALSE)
    """, (card_id, s))
    balance_at_statement = float(cur.fetchone()[0])
    cur.execute("""
        SELECT COALESCE(SUM(Total_Amount), 0)::float FROM Transactions
        WHERE Accounts_Id = %s AND Date > %s AND Date < %s AND Total_Amount > 0 AND NOT COALESCE(Is_Draft, FALSE)
    """, (card_id, s, due_date))
    credits_since = float(cur.fetchone()[0])
    owed = max(0.0, -balance_at_statement - credits_since)
    return {
        "statement_date": s, "due_date": due_date, "statement_day": statement_day,
        "balance_at_statement": round(balance_at_statement, 2), "credits_since": round(credits_since, 2),
        "owed": round(owed, 2), "issued": s <= date.today(),
    }


def template_payment(cur, templates_id: int, today: date | None = None) -> dict | None:
    """The calculated payment for one template, or None when it isn't a card-payment template.
    The result carries `error` instead of an amount when the card has no statement day yet."""
    today = today or date.today()
    cur.execute("""
        SELECT rt.Is_Card_Payment, rt.Accounts_Id_Target, COALESCE(rt.Next_Due_Date, %s), c.Statement_Day, c.Accounts_Name
        FROM Recurring_Templates rt LEFT JOIN Accounts c ON c.Accounts_Id = rt.Accounts_Id_Target
        WHERE rt.Templates_Id = %s
    """, (today, templates_id))
    row = cur.fetchone()
    if not row or not row[0]:
        return None
    _, card_id, due, statement_day, card_name = row
    if card_id is None:
        return {"error": "A credit-card payment needs a target account (the card)."}
    if not statement_day:
        return {"error": f"Set the statement day of {card_name} (Static Data → Accounts) first."}
    due = due if isinstance(due, date) else date.fromisoformat(str(due)[:10])
    return amount_owed(cur, int(card_id), due, int(statement_day))


def validate_card_payment(cur, target_account_id) -> str | None:
    """Reason a template can't be a card payment, or None when it can."""
    if not target_account_id:
        return "A credit-card payment must transfer into the card — pick it as the target account."
    cur.execute("SELECT Accounts_Type::text, Statement_Day, Accounts_Name FROM Accounts WHERE Accounts_Id = %s", (target_account_id,))
    r = cur.fetchone()
    if not r or r[0] != "Credit Card":
        return "The target account must be a Credit Card."
    if not r[1]:
        return f"Set the statement day of {r[2]} (Static Data → Accounts) first."
    return None


def refresh_card_payment_templates(conn=None) -> dict:
    """Recompute Total_Amount of every active card-payment template. Returns {updated, unchanged, skipped, details}."""
    from database.connection import get_connection
    own = conn is None
    conn = conn or get_connection()
    out = {"updated": 0, "unchanged": 0, "skipped": 0, "details": []}
    try:
        cur = conn.cursor()
        cur.execute("SELECT Templates_Id, Name, Total_Amount FROM Recurring_Templates WHERE Is_Card_Payment AND Active")
        for tid, name, stored in cur.fetchall():
            res = template_payment(cur, int(tid))
            if not res or "error" in (res or {}):
                out["skipped"] += 1
                out["details"].append(f"{name}: {res['error'] if res else 'not a card payment'}")
                continue
            new = -res["owed"]
            if stored is not None and abs(float(stored) - new) < 0.005:
                out["unchanged"] += 1
                continue
            cur.execute("UPDATE Recurring_Templates SET Total_Amount = %s WHERE Templates_Id = %s", (new, tid))
            out["updated"] += 1
            out["details"].append(f"{name}: {float(stored or 0):,.2f} → {new:,.2f} (statement {res['statement_date']})")
        conn.commit()
        return out
    except Exception:
        conn.rollback()
        raise
    finally:
        if own:
            conn.close()
