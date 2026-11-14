"""More ready-made answers for the AI Assistant (see ai/finance_tools.py for the first ones and ai/routing.py for how a
question reaches them). Each builds its answer from the query or report the app itself uses, so the figures match the
pages: upcoming bills (Reports -> Cash Flow Forecast), subscriptions, two periods compared, spending by payee, income,
dividends & interest, taxes, credit cards, account balances (with the bank's, from Enable Banking), unusual
transactions and portfolio allocation (Portfolio Analysis, currency and country exposure).
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

import pandas as pd

from ai.finance_tools import _spending, parse_period
from database.connection import get_connection


def _eur(v: float) -> str:
    return f"{'−' if v < 0 else ''}€{abs(v):,.2f}"


def _signed(v: float) -> str:
    return f"{'+' if v >= 0 else '−'}€{abs(v):,.2f}"


def _pct(new: float, old: float) -> str:
    if abs(old) < 0.5:
        return "new"
    return f"{(new - old) / abs(old) * 100:+.0f}%".replace("-", "−")


def _sql(sql: str, params: dict | None = None) -> pd.DataFrame:
    conn = get_connection()
    try:
        return pd.read_sql(sql, conn, params=params or {})
    finally:
        conn.close()


def _days_ahead(text: str, default: int = 31) -> int:
    t = text.lower()
    m = re.search(r"(\d{1,3})\s*(day|week|month)s?", t)
    if m:
        n = int(m.group(1))
        return max(1, min(n * {"day": 1, "week": 7, "month": 31}[m.group(2)], 366))
    if re.search(r"next week|this week|7 days", t):
        return 7
    if re.search(r"next (two|2) weeks|fortnight", t):
        return 14
    if re.search(r"next quarter|three months", t):
        return 92
    if re.search(r"(next|coming) year|12 months", t):
        return 365
    return default


def _year_before(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:
        return d.replace(year=d.year - 1, day=28)


# Transactions in EUR (latest FX, as the spending reports), not transfers between own accounts, not drafts — with the
# category type and full path of their first split.
_TX_SQL = """
    WITH RECURSIVE cat_path AS (
        SELECT Categories_Id, Categories_Name::TEXT AS full_path FROM Categories WHERE Categories_Id_Parent IS NULL
        UNION ALL
        SELECT c.Categories_Id, cp.full_path || ' : ' || c.Categories_Name
        FROM Categories c JOIN cat_path cp ON c.Categories_Id_Parent = cp.Categories_Id
    ),
    fx AS (SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC),
    first_split AS (SELECT DISTINCT ON (Transactions_Id) Transactions_Id, Categories_Id FROM Splits ORDER BY Transactions_Id, Splits_Id)
    SELECT t.Transactions_Id AS id, t.Date AS date, t.Accounts_Id AS account_id, a.Accounts_Name AS account,
           COALESCE(p.Payees_Name, NULLIF(t.Description, ''), '(no payee)') AS payee, t.Description AS description,
           (t.Total_Amount * CASE WHEN cur.Currencies_ShortName = 'EUR' THEN 1 ELSE COALESCE(fx.FX_Rate, 1) END)::float AS amount,
           c.Categories_Type AS ctype, cp.full_path AS category
    FROM Transactions t
    JOIN Accounts a ON a.Accounts_Id = t.Accounts_Id
    JOIN Currencies cur ON cur.Currencies_Id = a.Currencies_Id
    LEFT JOIN fx ON fx.Currencies_Id_1 = a.Currencies_Id
    LEFT JOIN Payees p ON p.Payees_Id = t.Payees_Id
    LEFT JOIN first_split fs ON fs.Transactions_Id = t.Transactions_Id
    LEFT JOIN Categories c ON c.Categories_Id = fs.Categories_Id
    LEFT JOIN cat_path cp ON cp.Categories_Id = c.Categories_Id
    WHERE t.Transfers_Id IS NULL AND t.Accounts_Id_Target IS NULL AND NOT COALESCE(t.Is_Draft, FALSE)
      AND t.Date >= %(start)s AND t.Date <= %(end)s
"""


def _transactions(start: date, end: date) -> pd.DataFrame:
    df = _sql(_TX_SQL, {"start": start, "end": end})
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


_STOP = set("""a an and are at be by can could did do does for from get got had has have how i in is it its last me month
months more much my next of on or our over paid pay per since so spend spent spending than that the their them this to too
total upcoming was we week weeks were what when where which who will with year years you your shop shops store stores
merchant merchants payee payees top most biggest largest at all show list tell give compare vs versus bank balance balances
account accounts card cards credit owe due transactions transaction""".split())


def _match_accounts(text: str, types: tuple[str, ...] | None = None) -> pd.DataFrame:
    """Active accounts the question names — by the last 4 digits ("1489") or a word of the name ("IRIS", "Payroll")."""
    acc = _sql("SELECT Accounts_Id AS id, Accounts_Name AS name, Accounts_Type::text AS type FROM Accounts WHERE Is_Active = TRUE")
    if types:
        acc = acc[acc["type"].isin(types)]
    t = text.lower()
    digits = set(re.findall(r"\b\d{4}\b", t))
    words = [w for w in re.findall(r"[a-zͰ-Ͽ]{3,}", t) if w not in _STOP]
    hit = acc[acc["name"].apply(lambda n: any(d in n for d in digits))]
    if hit.empty and words:
        score = acc["name"].str.lower().apply(lambda n: sum(1 for w in words if re.search(rf"\b{re.escape(w)}", n)))
        if score.max() > 0:
            hit = acc[score == score.max()]
    return hit


# ── 1. Upcoming bills & cash forecast ────────────────────────────────────────

def upcoming_bills(text: str = "") -> str:
    """What is due in the coming days (next 31 by default) and whether any account runs short — Reports -> Cash Flow
    Forecast: scheduled transactions, recurring templates, spending patterns it detects, card payments, dividends,
    interest and bond maturities, with each account's lowest projected balance."""
    from api.routers.reports import get_cash_flow_forecast_full

    days = _days_ahead(text)
    f = get_cash_flow_forecast_full(days=days, months_back=2, account_ids=None, min_balance=0.0, with_alerts=True)
    today = date.today()
    rows = []
    for src, label in (("scheduled", "scheduled"), ("templates", "recurring"), ("recurring", "usual pattern")):
        for r in f.get(src, []):
            rows.append({"date": r["date"], "payee": r.get("payees_name") or "", "account": r.get("accounts_name") or "",
                         "amount": float(r.get("amount_eur") or 0), "source": label, "category": r.get("category") or ""})
    for src, label in (("dividends", "dividend"), ("interest", "interest"), ("bonds", "bond")):
        for r in f.get(src, []):
            amt = float(r.get("amount_eur") or 0)
            if abs(amt) >= 0.005:
                rows.append({"date": r["date"], "payee": r.get("payees_name") or "", "account": r.get("accounts_name") or "",
                             "amount": amt, "source": label, "category": ""})
    df = pd.DataFrame(rows)
    acc_text = _match_accounts(text) if re.search(r"\b\d{4}\b|enough|run (out|short)|afford", text.lower()) else pd.DataFrame()
    lines = [f"Next {days} days ({today:%d/%m/%Y} to {today + timedelta(days=days):%d/%m/%Y}) — as in Reports → Cash Flow Forecast."]
    if not df.empty:
        out, inc = df[df["amount"] < 0], df[df["amount"] > 0]
        lines.append(f"Money out: {_eur(-out['amount'].sum())} ({len(out)} payments); money in: {_eur(inc['amount'].sum())} "
                     f"({len(inc)}). Net: {_signed(df['amount'].sum())}.")
        big = out.sort_values("amount").head(15).sort_values("date")
        if not big.empty:
            lines += ["", "Largest payments due:"] + [
                f"- {pd.to_datetime(r.date):%d/%m}: {r.payee or r.category or '—'} {_eur(-r.amount)}{' from ' + r.account if r.account else ''} ({r.source})"
                for r in big.itertuples()]
        if not inc.empty:
            lines += ["", "Coming in:"] + [
                f"- {pd.to_datetime(r.date):%d/%m}: {r.payee or '—'} {_eur(r.amount)} ({r.source})"
                for r in inc.sort_values("amount", ascending=False).head(8).sort_values("date").itertuples()]
    alerts = f.get("account_alerts") or {}
    accs = alerts.get("accounts", [])
    if not acc_text.empty:
        ids = set(acc_text["id"])
        mine = [a for a in accs if a["account_id"] in ids]
        if mine:
            lines += [""] + [f"{a['name']}: {_eur(a['balance_eur'])} now; lowest {_eur(a['min_balance_eur'])} on "
                             f"{date.fromisoformat(a['min_date']):%d/%m/%Y}; {_eur(a['end_balance_eur'])} at the end"
                             + (" — goes below zero." if a['status'] == 'warning' else ".") for a in mine]
    warn = alerts.get("alerts") or []
    if warn:
        lines += ["", "Accounts that would go below zero:"]
        for a in warn:
            s = (f"- {a['name']}: from {date.fromisoformat(a['breach_date']):%d/%m/%Y}, lowest {_eur(a['min_balance_eur'])} on "
                 f"{date.fromisoformat(a['min_date']):%d/%m/%Y}")
            if a.get("recovery_date"):
                s += f", back above on {date.fromisoformat(a['recovery_date']):%d/%m/%Y}"
            drv = [d for d in (a.get("drivers") or []) if float(d.get("amount_eur") or 0) < 0][:3]
            if drv:
                s += " — mainly " + "; ".join(f"{d['label']} {_eur(-float(d['amount_eur']))} on {date.fromisoformat(d['date']):%d/%m}" for d in drv)
            lines.append(s)
    elif accs:
        lines += ["", "No account is projected to go below zero."]
    cards = [c for c in (alerts.get("card_payments") or []) if abs(float(c.get("amount_eur") or 0)) >= 0.005]
    if cards:
        lines += ["", "Credit-card payments:"] + [
            f"- {date.fromisoformat(c['date']):%d/%m}: {c['card_name']} {_eur(abs(float(c['amount_eur'])))} from {c['from_name']}"
            for c in cards[:8]]
    return "\n".join(lines)


# ── 2. Subscriptions & recurring charges ─────────────────────────────────────

def subscriptions(text: str = "") -> str:
    """Recurring charges: the active recurring templates that pay out, and payees charged at a steady amount in at least
    3 of the last 4 months (subscriptions not set up as templates), with the yearly cost and any price rise."""
    today = date.today()
    tpl = _sql("""
        SELECT rt.Name AS name, py.Payees_Name AS payee, a.Accounts_Name AS account, rt.Total_Amount::float AS amount,
               rt.Periodicity AS periodicity, rt.Next_Due_Date AS next_due, COALESCE(rt.Is_Card_Payment, FALSE) AS card,
               rt.Accounts_Id_Target AS target
        FROM Recurring_Templates rt
        LEFT JOIN Payees py ON py.Payees_Id = rt.Payees_Id
        LEFT JOIN Accounts a ON a.Accounts_Id = rt.Accounts_Id
        WHERE rt.Active = TRUE AND (rt.End_Date IS NULL OR rt.End_Date >= CURRENT_DATE)
    """)
    per_year = {"Daily": 365, "Weekly": 52, "Bi-Weekly": 26, "Monthly": 12, "Bi-Monthly": 6, "Quarterly": 4,
                "Semi-Annual": 2, "Annual": 1}
    tpl = tpl[(tpl["amount"] < 0) & ~tpl["card"] & tpl["target"].isna()].copy()
    tpl["yearly"] = -tpl["amount"] * tpl["periodicity"].map(per_year).fillna(12)

    tx = _transactions(today - timedelta(days=400), today)
    found = []
    if not tx.empty:
        out = tx[(tx["amount"] < 0) & ~tx["ctype"].isin(["Tax", "Income", "Investment", "Transfer"])].copy()
        out["month"] = out["date"].apply(lambda d: (d.year, d.month))
        months4 = {((today.replace(day=1) - pd.DateOffset(months=i)).year, (today.replace(day=1) - pd.DateOffset(months=i)).month) for i in range(4)}
        known = set(tpl["payee"].dropna().str.lower())
        for payee, g in out.groupby("payee"):
            if payee.lower() in known or payee == "(no payee)":
                continue
            recent = g[g["month"].isin(months4)]
            per_month = recent.groupby("month").size()
            if len(per_month) < 3 or per_month.max() > 2:
                continue
            amts = -recent["amount"]
            if amts.median() < 1 or amts.std(ddof=0) / amts.median() > 0.15:
                continue
            g = g.sort_values("date")
            last, first = -g["amount"].iloc[-1], -g["amount"].iloc[0]
            found.append({"payee": payee, "amount": float(amts.median()), "last": float(last), "first": float(first),
                          "since": g["date"].iloc[0], "category": g["category"].iloc[-1] or "", "yearly": float(amts.median() * 12)})
    lines = []
    if not tpl.empty:
        lines += [f"Recurring payments set up as templates ({len(tpl)}): {_eur(tpl['yearly'].sum())} a year."]
        for r in tpl.sort_values("yearly", ascending=False).head(20).itertuples():
            lines.append(f"- {r.payee or r.name}: {_eur(-r.amount)} {str(r.periodicity).lower()} from {r.account} ({_eur(r.yearly)}/year)")
    if found:
        fd = pd.DataFrame(found).sort_values("yearly", ascending=False)
        lines += ["", f"Charged every month at a steady amount, without a template ({len(fd)}): about {_eur(fd['yearly'].sum())} a year."]
        for r in fd.head(20).itertuples():
            rise = f"; up from {_eur(r.first)} on {r.since:%d/%m/%Y}" if r.last > r.first * 1.03 and r.first >= 0.5 else ""
            lines.append(f"- {r.payee}: about {_eur(r.amount)}/month ({_eur(r.yearly)}/year){' — ' + r.category if r.category else ''}{rise}")
    if not lines:
        return "No recurring charges found."
    total = (tpl["yearly"].sum() if not tpl.empty else 0) + (sum(x["yearly"] for x in found))
    lines += ["", f"All together about {_eur(total)} a year ({_eur(total / 12)} a month)."]
    return "\n".join(lines)


# ── 3. Two periods compared ──────────────────────────────────────────────────

def _prev_period(start: date, end: date) -> tuple[date, date]:
    """The period just before: the previous month / quarter / year for a whole one, else the same number of days."""
    if start.day == 1 and end.month == start.month and end.year == start.year:     # a month, whole or still running
        p_start = (start - timedelta(days=1)).replace(day=1)
        p_last = calendar.monthrange(p_start.year, p_start.month)[1]
        whole = end.day == calendar.monthrange(end.year, end.month)[1]
        return p_start, p_start.replace(day=p_last if whole else min(p_last, end.day))
    if start.month == 1 and start.day == 1 and end.month == 12 and end.day == 31:
        return start.replace(year=start.year - 1), end.replace(year=end.year - 1)
    n = (end - start).days
    return start - timedelta(days=n + 1), start - timedelta(days=1)


def compare_periods(text: str = "") -> str:
    """Spending per category in two periods side by side ("this month vs last month", "October vs September", "2026 vs
    2025", "Q3 vs Q2"); one period alone is compared with the one before it. A month still running is compared with the
    same days of the other month."""
    today = date.today()
    t = text.lower()
    parts = re.split(r"\bvs\.?\b|\bversus\b|\bcompared (?:to|with)\b|\bagainst\b|\bthan\b|\band\b", t)
    parts = [p for p in parts if re.search(r"\b(19|20)\d{2}\b|q[1-4]|month|year|week|january|february|march|april|may|june|july|"
                                           r"august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec|ytd", p)]
    if len(parts) >= 2:
        a, b = parse_period(parts[0]), parse_period(parts[1])
    else:
        a = parse_period(parts[0] if parts else "this month")
        b = _prev_period(*a)
    note = ""
    for x, y in ((a, b), (b, a)):           # a period still running against the same days of the other one
        if x[1] >= today and x[0] <= today:
            run_days = (today - x[0]).days
            cut = min(y[1], y[0] + timedelta(days=run_days))
            if cut < y[1]:
                y_new = (y[0], cut)
                if x is a:
                    b = y_new
                else:
                    a = y_new
                note = f" ({run_days + 1} days each, as {x[0]:%d/%m}–{today:%d/%m} is still running)"
            if x is a:
                a = (x[0], today)
            else:
                b = (x[0], today)
            break
    s1, s2 = _spending(*a), _spending(*b)
    m = s1[["category", "spent"]].merge(s2[["category", "spent"]], on="category", how="outer", suffixes=("_a", "_b")).fillna(0.0)
    m = m[(m["spent_a"] >= 0.5) | (m["spent_b"] >= 0.5)]          # not a category with only refunds on both sides
    m["diff"] = m["spent_a"] - m["spent_b"]
    tax = m["category"].str.match(r"^tax", case=False)
    ta, tb = m["spent_a"].sum(), m["spent_b"].sum()
    xa, xb = m.loc[~tax, "spent_a"].sum(), m.loc[~tax, "spent_b"].sum()
    la, lb = f"{a[0]:%d/%m/%Y}–{a[1]:%d/%m/%Y}", f"{b[0]:%d/%m/%Y}–{b[1]:%d/%m/%Y}"
    lines = [f"Spending {la} against {lb}{note}:",
             f"Total {_eur(ta)} against {_eur(tb)}: {_signed(ta - tb)} ({_pct(ta, tb)}). Without taxes {_eur(xa)} against {_eur(xb)}: "
             f"{_signed(xa - xb)} ({_pct(xa, xb)})."]
    up = m[~tax & (m["diff"] >= 1)].sort_values("diff", ascending=False).head(8)
    down = m[~tax & (m["diff"] <= -1)].sort_values("diff").head(8)
    if not up.empty:
        lines += ["", "Spent more on:"] + [f"- {r.category}: {_eur(r.spent_a)} against {_eur(r.spent_b)} ({_signed(r.diff)})" for r in up.itertuples()]
    if not down.empty:
        lines += ["", "Spent less on:"] + [f"- {r.category}: {_eur(r.spent_a)} against {_eur(r.spent_b)} ({_signed(r.diff)})" for r in down.itertuples()]
    return "\n".join(lines)


# ── 4. Spending by payee ─────────────────────────────────────────────────────

def payee_spending(text: str = "") -> str:
    """Spending at a payee named in the question ("at Sklavenitis", all payees whose name contains the word), by
    month, for a period (this year by default); without a name, the 15 payees taking the most."""
    start, end = parse_period(text if re.search(r"\b(19|20)\d{2}\b|month|year|q[1-4]|ytd|week", text.lower()) else "this year")
    tx = _transactions(start, end)
    if tx.empty:
        return f"No transactions from {start:%d/%m/%Y} to {end:%d/%m/%Y}."
    out = tx[(tx["amount"] < 0) & ~tx["ctype"].isin(["Tax", "Income", "Investment", "Transfer"])]
    words = [w for w in re.findall(r"[a-zͰ-Ͽ0-9][a-zͰ-Ͽ0-9.&'-]{2,}", text.lower())
             if w not in _STOP and not re.fullmatch(r"(19|20)\d{2}|q[1-4]|ytd|january|february|march|april|june|july|august|"
                                                     r"september|october|november|december|today|yesterday", w)]
    names = out["payee"].str.lower()
    hit = out[names.apply(lambda n: any(re.search(rf"\b{re.escape(w)}", n) for w in words))] if words else out.iloc[0:0]
    period = f"{start:%d/%m/%Y} to {end:%d/%m/%Y}"
    if not hit.empty:
        tot = -hit["amount"].sum()
        by = hit.groupby("payee")["amount"].agg(["sum", "count"]).sort_values("sum")
        lines = [f"Spent at {', '.join(by.index[:6])}{' …' if len(by) > 6 else ''}, {period}: {_eur(tot)} in {len(hit)} "
                 f"payments (average {_eur(tot / len(hit))})."]
        if len(by) > 1:
            lines += [f"- {p}: {_eur(-r['sum'])} ({int(r['count'])})" for p, r in by.head(10).iterrows()]
        hm = hit.assign(m=hit["date"].apply(lambda d: d.strftime("%Y-%m"))).groupby("m")["amount"].sum()
        lines += ["", "By month:"] + [f"- {m}: {_eur(-v)}" for m, v in hm.items()]
        return "\n".join(lines)
    by = out.groupby("payee")["amount"].agg(["sum", "count"]).sort_values("sum").head(15)
    lines = [f"Payees you paid the most, {period} (taxes and transfers between your accounts left out):"]
    lines += [f"{i}. {p} — {_eur(-r['sum'])} in {int(r['count'])} payments" for i, (p, r) in enumerate(by.iterrows(), 1)]
    return "\n".join(lines)


# ── 5. Income ────────────────────────────────────────────────────────────────

def income(text: str = "") -> str:
    """Income by category for a period (this year by default) against the same days a year before, and month by month
    over the last 12 months — gross, with the tax booked against it shown apart."""
    start, end = parse_period(text if re.search(r"\b(19|20)\d{2}\b|month|year|q[1-4]|ytd", text.lower()) else "this year")
    q = """
        WITH RECURSIVE cat_path AS (
            SELECT Categories_Id, Categories_Name::TEXT AS full_path FROM Categories WHERE Categories_Id_Parent IS NULL
            UNION ALL SELECT c.Categories_Id, cp.full_path || ' : ' || c.Categories_Name
            FROM Categories c JOIN cat_path cp ON c.Categories_Id_Parent = cp.Categories_Id)
        SELECT t.Date AS date, cp.full_path AS category, c.Categories_Type AS ctype, s.Amount::float AS amount
        FROM Splits s JOIN Transactions t ON t.Transactions_Id = s.Transactions_Id
        JOIN Categories c ON c.Categories_Id = s.Categories_Id JOIN cat_path cp ON cp.Categories_Id = c.Categories_Id
        WHERE c.Categories_Type IN ('Income', 'Tax') AND NOT COALESCE(t.Is_Draft, FALSE)
          AND t.Date >= %(start)s AND t.Date <= %(end)s
    """
    p0, p1 = _year_before(start), _year_before(end)
    cur, prev = _sql(q, {"start": start, "end": end}), _sql(q, {"start": p0, "end": p1})
    inc, inc_prev = cur[cur["ctype"] == "Income"], prev[prev["ctype"] == "Income"]
    if inc.empty:
        return f"No income booked from {start:%d/%m/%Y} to {end:%d/%m/%Y}."
    by = inc.groupby("category")["amount"].sum().sort_values(ascending=False)
    bp = inc_prev.groupby("category")["amount"].sum()
    tot, totp = by.sum(), bp.sum()
    tax = -cur.loc[cur["ctype"] == "Tax", "amount"].sum()
    lines = [f"Income {start:%d/%m/%Y} to {end:%d/%m/%Y}: {_eur(tot)} gross, against {_eur(totp)} in the same days a year "
             f"before ({_pct(tot, totp)}). Taxes booked in the same time: {_eur(tax)} ({tax / tot * 100:.0f}% of gross income).",
             "", "By source:"]
    lines += [f"- {c}: {_eur(v)} (a year before {_eur(float(bp.get(c, 0)))})" for c, v in by.head(15).items()]
    yr = _sql(q, {"start": date.today().replace(day=1) - pd.DateOffset(months=11), "end": date.today()})
    yr = yr[yr["ctype"] == "Income"]
    if not yr.empty:
        mm = yr.assign(m=pd.to_datetime(yr["date"]).dt.strftime("%Y-%m")).groupby("m")["amount"].sum()
        lines += ["", "Month by month (last 12 months):"] + [f"- {m}: {_eur(v)}" for m, v in mm.items()]
    return "\n".join(lines)


# ── 6. Dividends & interest ──────────────────────────────────────────────────

def dividends_interest(text: str = "") -> str:
    """Dividends received in a period (this year by default) by security and account, interest received, and the
    dividends expected over the next 12 months (Reports -> Dividends forecast)."""
    from api.routers.reports import get_dividends, get_dividends_forecast

    start, end = parse_period(text if re.search(r"\b(19|20)\d{2}\b|month|year|q[1-4]|ytd", text.lower()) else "this year")
    d = pd.DataFrame(get_dividends(start_date=start.isoformat(), end_date=end.isoformat()))
    lines = [f"Dividends and interest {start:%d/%m/%Y} to {end:%d/%m/%Y}:"]
    if not d.empty:
        d["amount_eur"] = pd.to_numeric(d["amount_eur"], errors="coerce").fillna(0)
        lines.append(f"Dividends: {_eur(d['amount_eur'].sum())} from {d['security'].nunique()} securities ({len(d)} payments).")
        lines += ["Largest payers:"] + [f"- {s}: {_eur(v)}" for s, v in d.groupby("security")["amount_eur"].sum().sort_values(ascending=False).head(10).items()]
        lines += ["By account:"] + [f"- {a}: {_eur(v)}" for a, v in d.groupby("account")["amount_eur"].sum().sort_values(ascending=False).items()]
    else:
        lines.append("Dividends: none.")
    i = _sql("""
        SELECT a.Accounts_Name AS account, SUM(s.Amount)::float AS amount
        FROM Splits s JOIN Categories c ON c.Categories_Id = s.Categories_Id JOIN Transactions t ON t.Transactions_Id = s.Transactions_Id
        JOIN Accounts a ON a.Accounts_Id = t.Accounts_Id
        WHERE c.Categories_Type = 'Interest' AND NOT COALESCE(t.Is_Draft, FALSE) AND t.Date >= %(start)s AND t.Date <= %(end)s
        GROUP BY a.Accounts_Name HAVING ABS(SUM(s.Amount)) >= 0.005 ORDER BY 2 DESC
    """, {"start": start, "end": end})
    lines.append(f"Interest: {_eur(i['amount'].sum()) if not i.empty else '€0.00'}"
                 + (" — " + "; ".join(f"{r.account} {_eur(r.amount)}" for r in i.head(8).itertuples()) if not i.empty else "") + ".")
    try:
        fc = get_dividends_forecast(period="12m", account_ids=None)
        s = fc.get("summary") or {}
        lines += ["", f"Expected dividends over the next 12 months: {_eur(float(s.get('total_period_eur') or 0))} "
                      f"(about {_eur(float(s.get('total_monthly_eur') or 0))} a month, yield on cost {float(s.get('portfolio_yoc_pct') or 0):.2f}%)."]
        up = (fc.get("upcoming") or [])[:6]
        if up:
            lines += ["Next payments:"] + [f"- {u['pay_date']}: {u['securities_name']} {_eur(float(u.get('per_payment_eur') or 0))}" for u in up]
    except Exception:
        pass
    return "\n".join(lines)


# ── 7. Taxes ─────────────────────────────────────────────────────────────────

def taxes(text: str = "") -> str:
    """Taxes paid in a period (this year by default) by kind, month by month, against the same days a year before, and
    as a share of gross income."""
    start, end = parse_period(text if re.search(r"\b(19|20)\d{2}\b|month|year|q[1-4]|ytd", text.lower()) else "this year")
    q = """
        WITH RECURSIVE cat_path AS (
            SELECT Categories_Id, Categories_Name::TEXT AS full_path FROM Categories WHERE Categories_Id_Parent IS NULL
            UNION ALL SELECT c.Categories_Id, cp.full_path || ' : ' || c.Categories_Name
            FROM Categories c JOIN cat_path cp ON c.Categories_Id_Parent = cp.Categories_Id)
        SELECT t.Date AS date, cp.full_path AS category, c.Categories_Type AS ctype, s.Amount::float AS amount
        FROM Splits s JOIN Transactions t ON t.Transactions_Id = s.Transactions_Id
        JOIN Categories c ON c.Categories_Id = s.Categories_Id JOIN cat_path cp ON cp.Categories_Id = c.Categories_Id
        WHERE c.Categories_Type IN ('Tax', 'Income') AND NOT COALESCE(t.Is_Draft, FALSE) AND t.Date >= %(start)s AND t.Date <= %(end)s
    """
    cur, prev = _sql(q, {"start": start, "end": end}), _sql(q, {"start": _year_before(start), "end": _year_before(end)})
    tx, txp = cur[cur["ctype"] == "Tax"], prev[prev["ctype"] == "Tax"]
    if tx.empty:
        return f"No taxes booked from {start:%d/%m/%Y} to {end:%d/%m/%Y}."
    tot, totp = -tx["amount"].sum(), -txp["amount"].sum()
    gross = cur.loc[cur["ctype"] == "Income", "amount"].sum()
    lines = [f"Taxes {start:%d/%m/%Y} to {end:%d/%m/%Y}: {_eur(tot)}, against {_eur(totp)} in the same days a year before "
             f"({_pct(tot, totp)})" + (f"; {tot / gross * 100:.0f}% of {_eur(gross)} gross income." if gross > 0 else "."),
             "", "By kind:"]
    bp = (-txp.groupby("category")["amount"].sum())
    lines += [f"- {c}: {_eur(v)} (a year before {_eur(float(bp.get(c, 0)))})"
              for c, v in (-tx.groupby("category")["amount"].sum()).sort_values(ascending=False).items() if abs(v) >= 0.5]
    mm = tx.assign(m=pd.to_datetime(tx["date"]).dt.strftime("%Y-%m")).groupby("m")["amount"].sum()
    lines += ["", "By month:"] + [f"- {m}: {_eur(-v)}" for m, v in mm.items()]
    return "\n".join(lines)


# ── 8. Credit cards ──────────────────────────────────────────────────────────

def credit_cards(text: str = "") -> str:
    """Each active credit card: what is owed today, the credit limit and what is left, the statement day, the next
    payment (its card-payment template: what the statement comes to, and when) and the bank's balance when linked."""
    from data.enablebanking import stored_balances
    from database.card_statements import template_payment

    conn = get_connection()
    try:
        cur = conn.cursor()
        cards = pd.read_sql("""
            SELECT a.Accounts_Id AS id, a.Accounts_Name AS name, a.Credit_Limit::float AS credit_limit, a.Statement_Day AS sday,
                   COALESCE((SELECT SUM(Total_Amount) FROM Transactions t WHERE t.Accounts_Id = a.Accounts_Id
                             AND t.Date <= CURRENT_DATE AND NOT COALESCE(t.Is_Draft, FALSE)), 0)::float AS owed_today,
                   COALESCE((SELECT SUM(Total_Amount) FROM Transactions t WHERE t.Accounts_Id = a.Accounts_Id
                             AND NOT COALESCE(t.Is_Draft, FALSE)), 0)::float AS with_future,
                   (SELECT rt.Templates_Id FROM Recurring_Templates rt WHERE rt.Accounts_Id_Target = a.Accounts_Id
                      AND rt.Is_Card_Payment AND rt.Active ORDER BY rt.Next_Due_Date LIMIT 1) AS tpl
            FROM Accounts a WHERE a.Accounts_Type = 'Credit Card' AND a.Is_Active = TRUE ORDER BY a.Accounts_Name
        """, conn)
        named = _match_accounts(text, ("Credit Card",))
        if not named.empty:
            cards = cards[cards["id"].isin(named["id"])]
        bank = stored_balances()
        lines = []
        for r in cards.itertuples():
            if abs(r.owed_today) < 0.005 and abs(r.with_future) < 0.005 and not r.tpl:
                continue
            limit = abs(r.credit_limit or 0)
            s = f"{r.name}: owes {_eur(-r.owed_today)} today"
            if abs(r.with_future - r.owed_today) >= 0.5:
                s += f" ({_eur(-r.with_future)} with instalments still to come)"
            if limit:
                s += f"; limit {_eur(limit)}, {_eur(limit + r.owed_today)} available"
            if r.sday:
                s += f"; statement on day {int(r.sday)}"
            if pd.notna(r.tpl):
                p = template_payment(cur, int(r.tpl))
                if p and "error" not in p:
                    s += f"; next payment {_eur(p['owed'])} due {p['due_date']:%d/%m/%Y} (statement of {p['statement_date']:%d/%m/%Y}" \
                         f"{'' if p['issued'] else ', not issued yet'})"
            b = bank.get(str(r.id))
            if b and b.get("amount") is not None:
                diff = round(b["amount"] - r.owed_today, 2)
                s += f"; bank: {_eur(-b['amount'])} owed" + (" ✓" if abs(diff) < 0.01 else f" (differs by {_signed(diff)})")
            lines.append("- " + s + ".")
    finally:
        conn.close()
    if not lines:
        return "No credit card with a balance."
    owed = -cards["owed_today"].sum()
    return "\n".join([f"Credit cards: {_eur(owed)} owed today in total."] + lines)


# ── 9. Account balances, with the bank's ─────────────────────────────────────

def account_balances(text: str = "") -> str:
    """Balance of the accounts the question names (or of every bank, cash and card account): to date, with future
    transactions, and the bank's own balance when the account is linked in Enable Banking."""
    from data.enablebanking import stored_balances

    acc = _sql("""
        SELECT a.Accounts_Id AS id, a.Accounts_Name AS name, a.Accounts_Type::text AS type, c.Currencies_ShortName AS ccy,
               COALESCE((SELECT SUM(Total_Amount) FROM Transactions t WHERE t.Accounts_Id = a.Accounts_Id
                         AND t.Date <= CURRENT_DATE AND NOT COALESCE(t.Is_Draft, FALSE)), 0)::float AS to_date,
               a.Accounts_Balance::float AS with_future
        FROM Accounts a JOIN Currencies c ON c.Currencies_Id = a.Currencies_Id
        WHERE a.Is_Active = TRUE AND a.Accounts_Type IN ('Checking', 'Savings', 'Cash', 'Credit Card')
        ORDER BY a.Accounts_Type, a.Accounts_Name
    """)
    named = _match_accounts(text, ("Checking", "Savings", "Cash", "Credit Card"))
    only_bank = bool(re.search(r"\bbank\b|match|reconcil|agree", text.lower()))
    if not named.empty:
        acc = acc[acc["id"].isin(named["id"])]
    bank = stored_balances()
    lines, linked, same = [], 0, 0
    for r in acc.itertuples():
        b = bank.get(str(r.id))
        if only_bank and named.empty and not b:
            continue
        if named.empty and not b and abs(r.to_date) < 0.005:
            continue
        s = f"- {r.name}: {r.to_date:,.2f} {r.ccy}"
        if abs(r.with_future - r.to_date) >= 0.005:
            s += f" ({r.with_future:,.2f} with future transactions)"
        if b and b.get("amount") is not None:
            linked += 1
            diff = round(b["amount"] - r.to_date, 2)
            same += abs(diff) < 0.01
            when = b.get("date") or (b.get("fetched_at") or "")[:10]
            s += f"; bank {b['amount']:,.2f} on {when}" + (" ✓" if abs(diff) < 0.01 else f" — differs by {diff:+,.2f}")
        lines.append(s)
    if not lines:
        return "No matching account."
    head = "Balances to date"
    if linked:
        head += f"; {same} of {linked} accounts linked to the bank agree with it"
    return "\n".join([head + ":"] + lines)


# ── 10. Unusual transactions ─────────────────────────────────────────────────

def unusual_transactions(text: str = "") -> str:
    """Payments that stand out in a period (this month by default): far above what that payee usually takes, large
    first payments to a new payee, possible double charges, and categories well above their usual month."""
    start, end = parse_period(text if re.search(r"\b(19|20)\d{2}\b|month|year|q[1-4]|ytd|week", text.lower()) else "this month")
    if "week" in text.lower() and not re.search(r"\b(19|20)\d{2}\b", text):
        start, end = date.today() - timedelta(days=6), date.today()
    hist_start = start - timedelta(days=365)
    tx = _transactions(hist_start, end)
    if tx.empty:
        return "No transactions to look at."
    out = tx[(tx["amount"] < 0) & ~tx["ctype"].isin(["Tax", "Transfer", "Investment"])].copy()
    out["amt"] = -out["amount"]
    now, before = out[out["date"] >= start], out[out["date"] < start]
    med = before.groupby("payee")["amt"].median()
    cnt = before.groupby("payee")["amt"].size()
    lines = [f"Unusual payments {start:%d/%m/%Y} to {end:%d/%m/%Y} (against the 12 months before):"]
    big = now[now.apply(lambda r: cnt.get(r.payee, 0) >= 3 and r.amt >= 50 and r.amt > 2.5 * med.get(r.payee, r.amt), axis=1)]
    if not big.empty:
        lines += ["", "Much more than usual for that payee:"] + [
            f"- {r.date:%d/%m}: {r.payee} {_eur(r.amt)} (usually about {_eur(med[r.payee])}) — {r.account}" for r in big.sort_values("amt", ascending=False).head(10).itertuples()]
    new = now[now["payee"].map(lambda p: cnt.get(p, 0) == 0) & (now["amt"] >= 100) & (now["payee"] != "(no payee)")]
    new = new.sort_values("amt", ascending=False).drop_duplicates("payee")
    if not new.empty:
        lines += ["", "Large payments to a payee not paid in the year before:"] + [
            f"- {r.date:%d/%m}: {r.payee} {_eur(r.amt)} — {r.category or 'no category'}" for r in new.head(10).itertuples()]
    dup = []
    for (acct, payee, amt), g in now.groupby(["account_id", "payee", now["amt"].round(2)]):
        if len(g) < 2 or payee == "(no payee)":
            continue
        ds = sorted(g["date"])
        if any((b - a).days <= 1 for a, b in zip(ds, ds[1:])):
            dup.append((ds[0], payee, amt, g["account"].iloc[0], len(g)))
    if dup:
        lines += ["", "Same amount to the same payee within a day (possible double charge):"] + [
            f"- {d:%d/%m}: {p} {_eur(a)} ×{n} — {acct}" for d, p, a, acct, n in sorted(dup)[:10]]
    days = (end - start).days + 1
    cat_now = now.groupby("category")["amt"].sum()
    cat_avg = before.groupby("category")["amt"].sum() / 365 * days
    spikes = [(c, v, float(cat_avg.get(c, 0))) for c, v in cat_now.items() if c and v >= 100 and v > 1.8 * float(cat_avg.get(c, 0))]
    if spikes:
        lines += ["", "Categories well above their usual amount for that many days:"] + [
            f"- {c}: {_eur(v)} (usually about {_eur(u)})" for c, v, u in sorted(spikes, key=lambda x: x[2] - x[1])[:8]]
    if len(lines) == 1:
        lines.append("Nothing stands out.")
    return "\n".join(lines)


# ── 11. Portfolio allocation & exposure ──────────────────────────────────────

def allocation(text: str = "") -> str:
    """Asset allocation against the targets (Reports -> Portfolio Analysis), currency exposure and the main countries,
    funds looked through to what they hold."""
    from api.routers.country_exposure import get_country_exposure
    from api.routers.currency_exposure import get_currency_exposure
    from api.routers.reports import get_xray_asset_allocation

    lines = []
    x = get_xray_asset_allocation(account_ids=None, compare_date=None)
    summ = x.get("summary") or []
    if summ:
        tot = sum(float(s.get("value_eur") or 0) for s in summ)
        lines.append(f"Asset allocation ({_eur(tot)}, funds looked through) — as in Reports → Portfolio Analysis:")
        for s in summ:
            tgt = s.get("target_pct")
            extra = f"; target {float(tgt):.1f}%, {'buy' if float(s.get('rebalance_eur') or 0) > 0 else 'sell'} " \
                    f"{_eur(abs(float(s.get('rebalance_eur') or 0)))} to reach it" if tgt is not None and abs(float(s.get('rebalance_eur') or 0)) >= 1 else ""
            lines.append(f"- {s['asset_class']}: {_eur(float(s['value_eur']))} ({float(s['pct']):.1f}%{extra})")
    try:
        ccy = get_currency_exposure(account_ids=None, include_liabilities=False)
        lines += ["", "By currency:"] + [f"- {c['name']}: {_eur(float(c['value_eur']))} ({float(c['pct']):.1f}%)" for c in (ccy.get("countries") or [])[:8]]
    except Exception:
        pass
    try:
        k = get_country_exposure(account_ids=None)
        lines += ["", "By country (top 8):"] + [f"- {c['name']}: {_eur(float(c['value_eur']))} ({float(c['pct']):.1f}%)" for c in (k.get("countries") or [])[:8]]
    except Exception:
        pass
    return "\n".join(lines) if lines else "No holdings to analyse."


# ── 12. Why is a security / market moving ────────────────────────────────────

_MOVE_STOP = _STOP | set("""why falling fell fall falls drop dropping dropped down declining decline declined plunge plunging slump
slumping crash crashing sink sinking rise rising rose rises up rally rallying jump jumping surge surging soar soaring gain gaining
gained lose losing lost moving move moved movement price prices market markets today lately recently going happening happened news
about with behind reason reasons cause causes caused explain doing performing performance value""".split())


def market_move(text: str = "") -> str:
    """Why something is moving: the securities the question names (held ones first, then spot prices), their price
    change over a day, a week, a month and the year, what the user holds of them and its P&L this month, and the news —
    headlines Oikos stored for them over the last two weeks and a news search of the past week. It reports the moves and
    the headlines; it does not invent a cause."""
    from api.routers.reports import get_pnl

    words = [w for w in re.findall(r"[a-zͰ-Ͽ0-9&.-]{2,}", text.lower()) if w not in _MOVE_STOP and not w.isdigit()]
    if not words:
        return "Which security or market do you mean? Name it, e.g. \"why is gold falling?\"."
    sec = _sql("SELECT Securities_Id AS id, Securities_Name AS name, Ticker AS ticker, Securities_Type::text AS type FROM Securities")
    hit = sec[sec.apply(lambda r: any(re.search(rf"\b{re.escape(w)}", str(r["name"]).lower()) or str(r["ticker"]).lower() == w
                                      for w in words), axis=1)]
    if hit.empty:
        return f"No security in Oikos matches \"{' '.join(words)}\"."
    pnl = pd.DataFrame(get_pnl(start_date="1900-01-01", end_date=None))
    held = pnl[pnl["qty_today"].abs() > 1e-9] if not pnl.empty else pnl
    held_ids = set(held["securities_id"]) if not held.empty else set()
    hit = hit.assign(held=hit["id"].isin(held_ids), spot=hit["type"].eq("Commodity") & hit["name"].str.contains("Euro", case=False))
    show = pd.concat([hit[hit["held"]], hit[~hit["held"] & hit["spot"]]]).drop_duplicates("id").head(6)
    if show.empty:
        show = hit.head(4)
    today = date.today()
    marks = {"1 day": None, "1 week": today - timedelta(days=7), "1 month": today - timedelta(days=30), "this year": date(today.year - 1, 12, 31)}
    lines = ["Price moves:"]
    for r in show.itertuples():
        px = _sql("""SELECT Date AS d, Close::float AS c FROM Historical_Prices WHERE Securities_Id = %(id)s AND Date >= %(since)s
                     ORDER BY Date""", {"id": int(r.id), "since": date(today.year - 1, 12, 1)})
        if len(px) < 2:
            continue
        last_d, last = px["d"].iloc[-1], px["c"].iloc[-1]
        moves = []
        for label, d in marks.items():
            base = px["c"].iloc[-2] if d is None else (px.loc[px["d"] <= d, "c"].iloc[-1] if (px["d"] <= d).any() else None)
            if base:
                moves.append(f"{label} {(last / base - 1) * 100:+.1f}%".replace("-", "−"))
        lines.append(f"- {r.name} ({r.ticker}): {last:,.2f} on {pd.to_datetime(last_d):%d/%m/%Y} — " + ", ".join(moves))
    mine = held[held["securities_id"].isin(hit["id"])] if not held.empty else held
    if not mine.empty:
        lines += ["", f"What you hold of them: {_eur(mine['current_value_eur'].sum())}; this month {_signed(mine['pnl_mtd_eur'].sum())}, "
                      f"this year {_signed(mine['pnl_ytd_eur'].sum())}."]
        lines += [f"- {r.securities_name} ({r.accounts_name}): {_eur(r.current_value_eur)}, this month {_signed(r.pnl_mtd_eur)}"
                  for r in mine.sort_values("current_value_eur", ascending=False).itertuples()]
    news = []
    ids = tuple(int(i) for i in hit["id"])
    stored = _sql("""SELECT Title AS title, Publisher AS pub, Published_At AS at, Url AS url FROM News_Items
                     WHERE Source_Type = 'Security' AND Source_Id = ANY(%(ids)s) AND Published_At >= NOW() - INTERVAL '14 days'
                     ORDER BY Published_At DESC LIMIT 10""", {"ids": list(ids)})
    news += [(pd.to_datetime(n.at), n.title, n.pub or "", n.url) for n in stored.itertuples()]
    try:
        from ddgs import DDGS
        from ai.web_search import _scrub_query
        topic = " ".join(dict.fromkeys(words))[:60]
        with DDGS() as d:
            for x in d.news(_scrub_query(f"{topic} price"), timelimit="w", max_results=10) or []:
                # No date: the search's dates are unreliable (relative times converted wrongly), so web headlines keep its order
                news.append((pd.NaT, x.get("title") or "", x.get("source") or "", x.get("url") or ""))
    except Exception:
        pass
    seen, uniq = set(), []
    dated = sorted([n for n in news if pd.notna(n[0])], key=lambda n: n[0], reverse=True)
    for n in dated + [n for n in news if pd.isna(n[0])]:
        k = re.sub(r"\W+", "", n[1].lower())[:60]
        if n[1] and k not in seen:
            seen.add(k)
            uniq.append(n)
    if uniq:
        lines += ["", "What the news says (Oikos's stored news first, dated; then this week's news search):"] + [
            f"- {n[0]:%d/%m}: {n[1]}{' — ' + n[2] if n[2] else ''}" if pd.notna(n[0]) else f"- {n[1]}{' — ' + n[2] if n[2] else ''}"
            for n in uniq[:10]]
        lines.append("These are the headlines of the moment; read them for the reasons given — Oikos does not judge the cause itself.")
    else:
        lines += ["", "No recent news found for them."]
    return "\n".join(lines)
