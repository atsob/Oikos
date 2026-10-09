"""Account-level cash-flow alerts for the Cash Flow Forecast (Reports -> Cash Flow).

The forecast itself is a portfolio-wide total. This module projects each day-to-day account's
balance forward and warns where one is going to run short, with a suggestion for which other
account could cover it.

Per monitored account (active Checking / Savings / Credit Card), in EUR:
    balance(day) = balance today + everything dated after today up to `day`
"Balance today" is Accounts_Balance less the transactions already dated in the future (the stored
balance includes those, and they are added back day by day below). Flows that can be tied to an
account:
    - transactions dated in the future, and drafts still waiting to be confirmed (transfers included)
    - every occurrence of the active Recurring Templates (a template with a target account is a
      transfer: it takes money out of one account and puts it into the other)
    - credit-card payment templates, whose amount is not fixed: it is what the card owes on the
      statement the payment settles, simulated from the card's own projected balance
      (database/card_statements.py)
    - statistically-detected recurring payments (attributed to the account they most often hit)
    - projected interest on Savings / Checking accounts
Dividends and bond coupons/maturities are not attributed to an account in this view.

An alert is raised when the projected balance falls below the account's floor — the threshold for
cash accounts (default 0), the credit limit for a credit card that has one — unless the account is
marked Exclude_Balance_Alerts.
"""
from __future__ import annotations

import datetime as _dt
from collections import defaultdict

import pandas as pd
from dateutil.relativedelta import relativedelta

from database.card_statements import issue_date_on_or_before

MONITORED_TYPES = ("Checking", "Savings", "Credit Card")
# An alert closer than this is "critical"; further out it is a "warning".
CRITICAL_WITHIN_DAYS = 7
HISTORY_DAYS = 70          # how far back a card's own transactions are read to rebuild past statement balances

_PERIOD_STEP = {
    "Daily": relativedelta(days=1), "Weekly": relativedelta(weeks=1),
    "Bi-Weekly": relativedelta(weeks=2), "Monthly": relativedelta(months=1),
    "Bi-Monthly": relativedelta(months=2), "Quarterly": relativedelta(months=3),
    "Semi-Annual": relativedelta(months=6), "Annual": relativedelta(years=1),
}

_LATEST_FX = """(SELECT h.FX_Rate FROM Historical_FX h WHERE h.Currencies_Id_1 = c.Currencies_Id
                 ORDER BY h.Date DESC LIMIT 1)"""


def _is_brokerage_cash(name: str) -> bool:
    """Settlement accounts for a brokerage ("IBKR … (Cash)") hold money meant for trading, so they
    are never suggested as the source of a transfer."""
    return "(cash)" in (name or "").lower()


def _occurrences(r, today_ts, cutoff_ts):
    """Dates a template fires after today and up to the cutoff (installment series expanded)."""
    step = _PERIOD_STEP.get(str(r.periodicity), relativedelta(months=1))
    total_occ = int(r.total_occ) if pd.notna(r.total_occ) else None
    inst_step = _PERIOD_STEP.get(str(r.inst_freq), relativedelta(months=1))
    end = pd.Timestamp(r.end_date) if pd.notna(r.end_date) else None
    occ = pd.Timestamp(r.next_due)
    out, guard = [], 0
    while occ <= today_ts and guard < 2000:
        occ += step
        guard += 1
    while occ <= cutoff_ts and guard < 4000:
        guard += 1
        for seq in range(total_occ or 1):
            d = occ if seq == 0 else occ + inst_step * seq
            if d > cutoff_ts or (end is not None and d > end):
                break
            out.append(d.date())
        occ += step
    return out


def build_account_alerts(conn, today: _dt.date, cutoff: _dt.date, acct_ids, min_balance: float,
                         recurring_rows: list, interest_rows: list, rates: dict | None = None,
                         _debug_events: dict | None = None) -> dict:
    rates = rates or {}
    scope_clause, scope_params = "", {}
    if acct_ids:
        scope_clause = "AND a.Accounts_Id = ANY(%(ids)s)"
        scope_params = {"ids": [int(i) for i in acct_ids]}

    # Credit-card payment templates first: the cards they pay must be tracked even outside the scope.
    tmpl = pd.read_sql("""
        SELECT rt.Templates_Id AS id, COALESCE(py.Payees_Name, rt.Name) AS label,
               rt.Accounts_Id AS acc, rt.Accounts_Id_Target AS target, rt.Total_Amount::float AS amount,
               COALESCE(rt.Next_Due_Date, CURRENT_DATE) AS next_due, rt.Periodicity AS periodicity,
               rt.Total_Occurrences AS total_occ, rt.Installment_Frequency AS inst_freq, rt.End_Date AS end_date,
               rt.Is_Card_Payment AS card_payment
        FROM Recurring_Templates rt LEFT JOIN Payees py ON py.Payees_Id = rt.Payees_Id
        WHERE rt.Active = TRUE AND (rt.End_Date IS NULL OR rt.End_Date >= CURRENT_DATE)
    """, conn)
    card_ids = sorted({int(t) for t, cp in zip(tmpl["target"], tmpl["card_payment"]) if cp and pd.notna(t)})

    accounts = pd.read_sql(f"""
        SELECT a.Accounts_Id AS id, a.Accounts_Name AS name, a.Accounts_Type::text AS type,
               c.Currencies_ShortName AS currency,
               (COALESCE(a.Accounts_Balance, 0) - COALESCE((
                    SELECT SUM(t.Total_Amount) FROM Transactions t
                    WHERE t.Accounts_Id = a.Accounts_Id AND t.Date > %(today)s AND NOT COALESCE(t.Is_Draft, FALSE)), 0))::float AS balance,
               COALESCE(a.Credit_Limit, 0)::float AS credit_limit,
               a.Exclude_Balance_Alerts AS excluded, a.Statement_Day AS statement_day,
               (a.Accounts_Type::text = ANY(%(types)s) {scope_clause}) AS monitored,
               CASE WHEN c.Currencies_ShortName = 'EUR' THEN 1.0
                    ELSE COALESCE({_LATEST_FX}, 1.0) END::float AS fx
        FROM Accounts a JOIN Currencies c ON c.Currencies_Id = a.Currencies_Id
        WHERE a.Is_Active AND ((a.Accounts_Type::text = ANY(%(types)s) {scope_clause}) OR a.Accounts_Id = ANY(%(cards)s))
    """, conn, params={"types": list(MONITORED_TYPES), "today": today, "cards": card_ids or [-1], **scope_params})
    if accounts.empty:
        return _empty(min_balance)
    accounts["balance_eur"] = accounts["balance"] * accounts["fx"]
    accounts["limit_eur"] = accounts["credit_limit"] * accounts["fx"]
    tracked = {int(r.id): r for r in accounts.itertuples(index=False)}
    info = {k: r for k, r in tracked.items() if r.monitored}
    if not info:
        return _empty(min_balance)
    fx_of = {k: float(r.fx) for k, r in tracked.items()}

    # events[account_id] -> list of (date, amount_eur, label)
    events: dict[int, list] = defaultdict(list)
    tomorrow = today + _dt.timedelta(days=1)

    def add(acc, d, amt, label):
        if acc in tracked and amt:
            d = max(d, tomorrow)                         # a due, unconfirmed item lands on the next day
            if d <= cutoff:
                events[acc].append((d, float(amt), label))

    # 1. Transactions dated after today and drafts still waiting to be confirmed (due ones included).
    fut = pd.read_sql("""
        SELECT t.Date AS date, t.Accounts_Id AS acc, COALESCE(p.Payees_Name, t.Description, '') AS label,
               COALESCE((SELECT SUM(s.Amount) FROM Splits s WHERE s.Transactions_Id = t.Transactions_Id),
                        t.Total_Amount, 0)::float AS amount,
               COALESCE(t.Is_Draft, FALSE) AS is_draft, t.Accounts_Id_Target AS target, t.Transfers_Id AS transfers_id
        FROM Transactions t LEFT JOIN Payees p ON p.Payees_Id = t.Payees_Id
        WHERE (t.Date > %(today)s AND t.Date <= %(cutoff)s) OR (COALESCE(t.Is_Draft, FALSE) AND t.Date <= %(today)s)
    """, conn, params={"today": today, "cutoff": cutoff})
    for r in fut.itertuples(index=False):
        a = int(r.acc)
        d = pd.Timestamp(r.date).date()
        add(a, d, r.amount * fx_of.get(a, 1.0), str(r.label))
        # A draft transfer generated from a template has only its source leg until it is confirmed.
        if r.is_draft and pd.notna(r.target) and pd.isna(r.transfers_id):
            add(int(r.target), d, -r.amount * fx_of.get(a, 1.0), str(r.label))

    # 2. Recurring templates. Card-payment ones are collected for the simulation below.
    today_ts, cutoff_ts = pd.Timestamp(today), pd.Timestamp(cutoff)
    card_payments: list = []                             # (date, source account, card, label)
    for r in tmpl.itertuples(index=False):
        src = int(r.acc)
        tgt = int(r.target) if pd.notna(r.target) else None
        if src not in tracked and (tgt is None or tgt not in tracked):
            continue
        dates = _occurrences(r, today_ts, cutoff_ts)
        if r.card_payment and tgt in tracked and tracked[tgt].statement_day:
            for d in dates:
                card_payments.append((d, src, tgt, str(r.label)))
            continue
        for d in dates:
            amt_eur = r.amount * fx_of.get(src, 1.0)     # the template's amount is in its own account's currency
            add(src, d, amt_eur, str(r.label))
            if tgt is not None:
                add(tgt, d, -amt_eur, str(r.label))

    # 3. Detected recurring payments and 4. projected interest — only where an account is known.
    for r in recurring_rows or []:
        if r.get("accounts_id") is not None:
            add(int(r["accounts_id"]), _dt.date.fromisoformat(r["date"]), r["amount_eur"], str(r.get("payees_name") or "recurring"))
    for r in interest_rows or []:
        if r.get("accounts_id") is not None:
            add(int(r["accounts_id"]), _dt.date.fromisoformat(r["date"]), r["amount_eur"], f"Interest · {r.get('payees_name') or ''}")

    # A card's own recent history, to rebuild its balance at a statement date that has already passed.
    past: dict[int, list] = {}
    if card_payments:
        hist = pd.read_sql("""
            SELECT t.Accounts_Id AS acc, t.Date AS date, t.Total_Amount::float AS amount
            FROM Transactions t
            WHERE t.Accounts_Id = ANY(%(cards)s) AND t.Date > %(since)s AND t.Date <= %(today)s AND NOT COALESCE(t.Is_Draft, FALSE)
        """, conn, params={"cards": sorted({c for _, _, c, _ in card_payments}),
                           "since": today - _dt.timedelta(days=HISTORY_DAYS), "today": today})
        for r in hist.itertuples(index=False):
            past.setdefault(int(r.acc), []).append((pd.Timestamp(r.date).date(), r.amount * fx_of.get(int(r.acc), 1.0)))

    if _debug_events is not None:
        _debug_events.update(events)

    # Day-by-day simulation, today .. cutoff, end-of-day balances.
    days = [today + _dt.timedelta(days=i) for i in range((cutoff - today).days + 1)]
    idx_of = {d: i for i, d in enumerate(days)}
    delta = {acc: [0.0] * len(days) for acc in tracked}
    credits_sim = {acc: [0.0] * len(days) for acc in tracked}
    for acc, evs in events.items():
        for d, amt, _ in evs:
            delta[acc][idx_of[d]] += amt
            if amt > 0:
                credits_sim[acc][idx_of[d]] += amt
    pay_on: dict[int, list] = defaultdict(list)
    for d, src, card, label in card_payments:
        pay_on[idx_of[d]].append((src, card, label, d))

    run = {acc: float(r.balance_eur) for acc, r in tracked.items()}
    bal0 = dict(run)
    hist_post = {acc: [0.0] * len(days) for acc in tracked}
    balances: dict[int, list] = {acc: [] for acc in tracked}
    simulated: list = []
    for i, d in enumerate(days):
        for acc in tracked:
            run[acc] += delta[acc][i]
        for src, card, label, due in pay_on.get(i, []):
            s = issue_date_on_or_before(int(tracked[card].statement_day), due)
            if s >= today:
                j = idx_of[s]
                bal_s = run[card] if j == i else hist_post[card][j]
                credits = sum(credits_sim[card][k] for k in range(1, i) if days[k] > s)
            else:
                bal_s = bal0[card] - sum(a for dd, a in past.get(card, []) if dd > s)
                credits = sum(a for dd, a in past.get(card, []) if dd > s and a > 0) + sum(credits_sim[card][k] for k in range(1, i))
            owed = max(0.0, -bal_s - credits)
            if owed >= 0.005:
                run[src] -= owed
                run[card] += owed
                credits_sim[card][i] += owed
                events[src].append((due, -owed, label))
                events[card].append((due, owed, label))
            simulated.append({
                "date": due.isoformat(), "statement_date": s.isoformat(), "label": label,
                "from_account_id": src, "from_name": tracked[src].name,
                "card_account_id": card, "card_name": tracked[card].name, "amount_eur": round(owed, 2),
            })
        for acc in tracked:
            hist_post[acc][i] = run[acc]
            balances[acc].append(run[acc])

    def floor_of(acc: int):
        r = info[acc]
        if r.type == "Credit Card":
            return float(r.limit_eur) if r.limit_eur < 0 else None       # no limit recorded -> nothing to breach
        return float(min_balance)

    summary, alerts = [], []
    for acc, r in info.items():
        series = balances[acc]
        floor = floor_of(acc)
        lo = min(series)
        lo_i = series.index(lo)
        excluded = bool(r.excluded)
        below_now = floor is not None and r.balance_eur < floor - 0.005
        breach_i = None if excluded else next((i for i, v in enumerate(series) if floor is not None and v < floor - 0.005), None)
        status = "excluded" if excluded else "ok"
        if breach_i is not None:
            status = "critical" if (below_now or breach_i <= CRITICAL_WITHIN_DAYS) else "warning"
        summary.append({
            "account_id": acc, "name": r.name, "type": r.type, "currency": r.currency,
            "balance_eur": round(float(r.balance_eur), 2),
            "floor_eur": None if floor is None else round(floor, 2),
            "min_balance_eur": round(lo, 2), "min_date": days[lo_i].isoformat(),
            "end_balance_eur": round(series[-1], 2), "status": status,
            "rate_pct": round(float(rates.get(acc, 0.0)), 3),
        })
        if breach_i is None:
            continue
        # Is it a dip or a hole? The first day after the low point back at or above the floor.
        recovery_i = next((i for i in range(lo_i + 1, len(series)) if series[i] >= floor - 0.005), None)
        days_below = sum(1 for v in series if v < floor - 0.005)
        before = sorted((e for e in events.get(acc, []) if e[0] <= days[breach_i] and e[1] < 0), key=lambda e: e[1])[:3]
        alerts.append({
            "account_id": acc, "name": r.name, "type": r.type, "currency": r.currency,
            "severity": status, "already_below": bool(below_now),
            "balance_eur": round(float(r.balance_eur), 2), "floor_eur": round(floor, 2),
            "breach_date": days[breach_i].isoformat(), "days_until": breach_i,
            "min_balance_eur": round(lo, 2), "min_date": days[lo_i].isoformat(),
            "shortfall_eur": round(floor - lo, 2),
            "recovery_date": None if recovery_i is None else days[recovery_i].isoformat(),
            "days_below": days_below,
            "drivers": [{"date": e[0].isoformat(), "label": e[2], "amount_eur": round(e[1], 2)} for e in before],
            "suggested_transfers": [], "unfunded_eur": 0.0,
            "series": [[d.isoformat(), round(v, 2)] for d, v in zip(days, series)],
        })
    alerts.sort(key=lambda a: (a["severity"] != "critical", a["days_until"]))

    # Who can cover it? Another Checking / Savings account that stays above its own floor for the
    # whole horizon even after giving the money up — the one earning the least interest first, so
    # the money that costs least to move goes. Brokerage settlement accounts are never used.
    spare = {}
    alerted = {a["account_id"] for a in alerts}
    for acc, r in info.items():
        if acc in alerted or r.type not in ("Checking", "Savings") or _is_brokerage_cash(r.name):
            continue
        spare[acc] = min(balances[acc]) - (floor_of(acc) or 0.0)
    for a in alerts:
        needed = a["shortfall_eur"]
        by = max(today, _dt.date.fromisoformat(a["breach_date"]) - _dt.timedelta(days=1))
        order = sorted((k for k, v in spare.items() if v >= 1.0), key=lambda k: (rates.get(k, 0.0), -spare[k]))
        # One source that can cover the whole amount beats splitting it.
        single = next((k for k in order if spare[k] >= needed), None)
        for src in ([single] if single is not None else order):
            if needed < 1.0:
                break
            take = round(min(spare[src], needed), 2)
            if take < 1.0:
                continue
            a["suggested_transfers"].append({
                "from_account_id": src, "from_name": info[src].name, "from_type": info[src].type,
                "amount_eur": take, "by_date": by.isoformat(), "rate_pct": round(float(rates.get(src, 0.0)), 3),
            })
            spare[src] -= take
            needed -= take
        a["unfunded_eur"] = round(max(needed, 0.0), 2)

    return {
        "min_balance": float(min_balance), "accounts": summary, "alerts": alerts,
        "card_payments": sorted(simulated, key=lambda x: x["date"]),
        "notes": [
            "Each account's balance is projected forward from its balance today, using the transactions already dated in the future and pending drafts, every Recurring Template occurrence (transfer templates move money between their two accounts), the detected recurring payments that can be tied to the account they usually hit, and projected interest.",
            "A credit-card payment template whose card has a statement day is not a fixed amount: each payment is what the card owes on the statement it settles — the card's balance at the latest statement date on or before the due date, less payments already made since — projected from the card's own spending.",
            "Dividends and bond coupons/maturities are not attributed to an account here, and one-off spending that isn't scheduled or recurring is not predicted — treat a warning as a prompt to look, not a certainty.",
            "Physical cash accounts are not monitored: cash withdrawals aren't predicted, so they would always look like they run dry. Accounts marked \"Exclude from balance alerts\" (Static Data → Accounts) are shown but never alerted.",
            f"A cash account is flagged when its projected balance falls below the threshold ({min_balance:,.0f} €); a credit card when it would go beyond its credit limit (cards with no limit recorded are not checked).",
            "Suggested transfers come from other Checking or Savings accounts that would stay above the threshold throughout, lowest interest rate first (one account that can cover it all is preferred to splitting); brokerage settlement accounts (\"… (Cash)\") are never suggested. Amounts are in EUR.",
        ],
    }


def _empty(min_balance: float) -> dict:
    return {"min_balance": float(min_balance), "accounts": [], "alerts": [], "card_payments": [], "notes": []}
