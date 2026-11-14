"""Ready-made answers for the AI Assistant's most common questions.

A small local model writing its own SQL gets these wrong — categories are on Splits, not
Transactions, and "overspent" means against the budget, which it has no way to guess (on
"Which categories did I overspend in Q1 2026?" llama3.1:8b joined Payees to Categories and
called every category with spending "overspent"). These tools take only a period, run the
same query the Reports do, and return a short table the model just has to read out.

Both count what Reports -> Budget counts as spending: Expense and Tax categories, by full
path ("Household : Super Market"), net of refunds to the same category, transfers excluded,
non-EUR accounts at the latest FX rate.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

import pandas as pd

from database.connection import get_connection

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m} | \
          {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}

_SPEND_SQL = """
    WITH RECURSIVE cat_path AS (
        SELECT Categories_Id, Categories_Name::TEXT AS full_path FROM Categories WHERE Categories_Id_Parent IS NULL
        UNION ALL
        SELECT c.Categories_Id, cp.full_path || ' : ' || c.Categories_Name
        FROM Categories c JOIN cat_path cp ON c.Categories_Id_Parent = cp.Categories_Id
    ),
    fx AS (SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC)
    SELECT s.Categories_Id AS cid, cp.full_path AS category,
           -SUM(s.Amount * CASE WHEN cur.Currencies_ShortName = 'EUR' THEN 1 ELSE COALESCE(fx.FX_Rate, 1) END)::float AS spent
    FROM Splits s
    JOIN Transactions t ON t.Transactions_Id = s.Transactions_Id
    JOIN Categories c   ON c.Categories_Id = s.Categories_Id
    JOIN cat_path cp    ON cp.Categories_Id = c.Categories_Id
    JOIN Accounts a     ON a.Accounts_Id = t.Accounts_Id
    JOIN Currencies cur ON cur.Currencies_Id = a.Currencies_Id
    LEFT JOIN fx        ON fx.Currencies_Id_1 = a.Currencies_Id
    WHERE t.Transfers_Id IS NULL
      AND c.Categories_Type NOT IN ('Income', 'Transfer', 'Trading', 'Investment', 'Interest', 'Dividend')
      AND t.Date >= %(start)s AND t.Date <= %(end)s
    GROUP BY s.Categories_Id, cp.full_path
"""


def parse_period(text: str, today: date | None = None) -> tuple[date, date]:
    """A period in the words a question uses: "Q1 2026", "March 2026", "2026", "this year", "last month",
    or two dates ("2026-01-01 to 2026-03-31", DD/MM/YYYY too). Returns (first day, last day)."""
    today = today or date.today()
    t = text.strip().strip("'\"`").lower()
    dates = re.findall(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{4}", t)
    if len(dates) >= 2:
        def d(s: str) -> date:
            if "-" in s:
                y, m, dd = map(int, s.split("-"))
            else:
                dd, m, y = map(int, s.split("/"))
            return date(y, m, dd)
        return d(dates[0]), d(dates[1])
    year_m = re.search(r"\b(19|20)\d{2}\b", t)
    year = int(year_m.group(0)) if year_m else today.year
    if "last year" in t or "previous year" in t:
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    if "this month" in t:
        return today.replace(day=1), today
    if "last month" in t or "previous month" in t:
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    q = re.search(r"\bq([1-4])\b|\b([1-4])(?:st|nd|rd|th)? quarter\b", t)
    if q:
        n = int(q.group(1) or q.group(2))
        start = date(year, 3 * n - 2, 1)
        return start, date(year, 3 * n, calendar.monthrange(year, 3 * n)[1])
    if re.search(r"\bh([12])\b", t):
        h = int(re.search(r"\bh([12])\b", t).group(1))
        return (date(year, 1, 1), date(year, 6, 30)) if h == 1 else (date(year, 7, 1), date(year, 12, 31))
    for word in re.findall(r"[a-z]+", t):
        if word in _MONTHS:
            m = _MONTHS[word]
            return date(year, m, 1), date(year, m, calendar.monthrange(year, m)[1])
    if "ytd" in t or "year to date" in t or "this year" in t:
        return date(today.year, 1, 1), today
    return date(year, 1, 1), date(year, 12, 31) if year != today.year else today


def _eur(v: float) -> str:
    return f"€{v:,.2f}"


def _spending(start: date, end: date) -> pd.DataFrame:
    conn = get_connection()
    try:
        return pd.read_sql(_SPEND_SQL, conn, params={"start": start, "end": end})
    finally:
        conn.close()


def spending_by_category(period: str) -> str:
    start, end = parse_period(period)
    allc = _spending(start, end)
    df = allc[allc["spent"] > 0.005].sort_values("spent", ascending=False)
    if df.empty:
        return f"No spending recorded from {start} to {end}."
    refunds = -allc.loc[allc["spent"] < -0.005, "spent"].sum()     # categories where refunds outweighed spending
    net = (f"; {_eur(df['spent'].sum() - refunds)} after {_eur(refunds)} of refunds in categories with no net spending"
           if refunds > 0.005 else "")
    lines = [f"Total spending from {start} to {end}: {_eur(df['spent'].sum())}{net}. By category, largest first:"]
    lines += [f"{i}. {r.category} — {_eur(r.spent)}" for i, r in enumerate(df.head(25).itertuples(), 1)]
    if len(df) > 25:
        lines.append(f"… and {len(df) - 25} smaller categories ({_eur(df['spent'].iloc[25:].sum())}).")
    return "\n".join(lines)


def spending_vs_budget(period: str) -> str:
    """Spending per category against the annual budget pro-rated to the period (Q1 = 90/365 of the year's budget)."""
    start, end = parse_period(period)
    conn = get_connection()
    try:
        budgets = pd.read_sql("""SELECT b.Year AS year, b.Categories_Id AS cid, b.Budget_Amount::float AS budget
                                 FROM Annual_Budgets b
                                 WHERE b.Year BETWEEN %(y0)s AND %(y1)s AND b.Owner_Users_Id IS NULL AND b.Budget_Amount > 0""",
                              conn, params={"y0": start.year, "y1": end.year})
    finally:
        conn.close()
    if budgets.empty:
        return (f"No budget is set for {start.year}" + (f"–{end.year}" if end.year != start.year else "") +
                " (Reports → Budget), so overspending can't be measured. " + spending_by_category(f"{start} to {end}"))
    # Each year's budget counts for the days of the period that fall in that year.
    share = {}
    for y in range(start.year, end.year + 1):
        lo, hi = max(start, date(y, 1, 1)), min(end, date(y, 12, 31))
        share[y] = ((hi - lo).days + 1) / (366 if calendar.isleap(y) else 365)
    budgets["prorated"] = budgets["budget"] * budgets["year"].map(share)
    bud = budgets.groupby("cid", as_index=False)["prorated"].sum()

    spent = _spending(start, end)
    df = spent.merge(bud, on="cid", how="outer")
    if df["category"].isna().any():
        conn = get_connection()
        try:
            names = pd.read_sql("""WITH RECURSIVE cp AS (
                    SELECT Categories_Id, Categories_Name::TEXT AS full_path FROM Categories WHERE Categories_Id_Parent IS NULL
                    UNION ALL SELECT c.Categories_Id, cp.full_path || ' : ' || c.Categories_Name FROM Categories c JOIN cp ON c.Categories_Id_Parent = cp.Categories_Id)
                SELECT Categories_Id AS cid, full_path FROM cp""", conn)
        finally:
            conn.close()
        df = df.merge(names, on="cid", how="left")
        df["category"] = df["category"].fillna(df["full_path"])
    df["spent"] = df["spent"].fillna(0.0)
    df["prorated"] = df["prorated"].fillna(0.0)
    df["over"] = df["spent"] - df["prorated"]

    days = (end - start).days + 1
    head = (f"Spending vs budget from {start} to {end} ({days} days; each category's annual budget is pro-rated to these days, "
            f"as in Reports → Budget):")
    over = df[(df["prorated"] > 0) & (df["over"] > 0.005)].sort_values("over", ascending=False)
    unbudgeted = df[(df["prorated"] <= 0) & (df["spent"] > 0.005)].sort_values("spent", ascending=False)
    budgeted = df[df["prorated"] > 0]
    lines = [head]
    if over.empty:
        lines.append("No category with a budget went over it.")
    else:
        lines.append(f"OVER BUDGET — {len(over)} of {len(budgeted)} budgeted categories, largest overspend first:")
        lines += [f"{i}. {r.category} — spent {_eur(r.spent)} vs budget {_eur(r.prorated)}: over by {_eur(r.over)} "
                  f"({r.spent / r.prorated * 100:.0f}% of budget)" for i, r in enumerate(over.head(20).itertuples(), 1)]
        if len(over) > 20:
            lines.append(f"… and {len(over) - 20} more, over by {_eur(over['over'].iloc[20:].sum())} together.")
    lines.append(f"In total: spent {_eur(budgeted['spent'].sum())} vs budget {_eur(budgeted['prorated'].sum())} in budgeted categories.")
    if not unbudgeted.empty:
        lines.append(f"Spending in categories with no budget: {_eur(unbudgeted['spent'].sum())}, largest: " +
                     "; ".join(f"{r.category} {_eur(r.spent)}" for r in unbudgeted.head(5).itertuples()) + ".")
    return "\n".join(lines)


_PERIOD_WORDS = r"\b(september|december|november|february|january|october|august|april|march|july|june|mar|oct|aug|feb|may|apr|jun|jan|jul|dec|sep|nov|q[1-4]|h[12]|(19|20)\d{2}|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{4}|this|last|previous|month|year|ytd|to|in|of|for|from)\b"


def _category_names(text: str) -> list[str]:
    """Category names in a tool input — "Car Rental, Q1 2026", or the list a model pastes from an earlier answer
    ("1. Healthcare : Dental — spent €1,453.33 …" one per line). Numbering, amounts and period words are dropped."""
    names = []
    for piece in re.split(r"[\n;]", text):
        piece = re.sub(r"^\s*[-*\d.)]+\s*", "", piece).split(" — ")[0]
        if piece.rstrip().endswith(":"):            # a lead-in such as "the categories most over budget, which are:"
            continue
        head, sep, tail = piece.rpartition(",")
        if sep and re.search(_PERIOD_WORDS, tail, re.IGNORECASE):
            piece = head
        piece = re.sub(_PERIOD_WORDS, "", piece, flags=re.IGNORECASE)
        piece = re.sub(r"\s+", " ", piece).strip(" ,.'\"`")
        if len(piece) >= 3 and piece.lower() not in (n.lower() for n in names):
            names.append(piece)
    return names


def category_transactions(text: str) -> str:
    """The transactions behind one or more categories in a period: "Car Rental, Q1 2026". A name matches any part of the
    full path (a parent includes its subcategories); the period is read from anywhere in the input."""
    start, end = parse_period(text)
    names = _category_names(text)
    if not names:
        return "Name the category, then the period, e.g. Car Rental, Q1 2026."
    conn = get_connection()
    try:
        df = pd.read_sql("""
            WITH RECURSIVE cat_path AS (
                SELECT Categories_Id, Categories_Name::TEXT AS full_path FROM Categories WHERE Categories_Id_Parent IS NULL
                UNION ALL
                SELECT c.Categories_Id, cp.full_path || ' : ' || c.Categories_Name
                FROM Categories c JOIN cat_path cp ON c.Categories_Id_Parent = cp.Categories_Id
            ),
            fx AS (SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC)
            SELECT t.Date AS date, cp.full_path AS category, COALESCE(p.Payees_Name, '') AS payee, COALESCE(t.Description, '') AS description,
                   -(s.Amount * CASE WHEN cur.Currencies_ShortName = 'EUR' THEN 1 ELSE COALESCE(fx.FX_Rate, 1) END)::float AS spent
            FROM Splits s
            JOIN Transactions t ON t.Transactions_Id = s.Transactions_Id
            JOIN cat_path cp    ON cp.Categories_Id = s.Categories_Id
            JOIN Accounts a     ON a.Accounts_Id = t.Accounts_Id
            JOIN Currencies cur ON cur.Currencies_Id = a.Currencies_Id
            LEFT JOIN fx        ON fx.Currencies_Id_1 = a.Currencies_Id
            LEFT JOIN Payees p  ON p.Payees_Id = t.Payees_Id
            WHERE t.Transfers_Id IS NULL AND t.Date >= %(start)s AND t.Date <= %(end)s
            ORDER BY t.Date
        """, conn, params={"start": start, "end": end})
    finally:
        conn.close()
    several = len(names) > 1
    per = 5 if several else 15
    out, missing = [], []
    for name in names[:6]:
        g = df[df["category"].str.contains(name, case=False, regex=False)]
        if g.empty:
            missing.append(name)
            continue
        cats = sorted(g["category"].unique())
        lines = [f"{', '.join(cats[:5])}{' and others' if len(cats) > 5 else ''}: {len(g)} transaction{"s" if len(g) != 1 else ""} totalling {_eur(g['spent'].sum())}"
                 + ("" if several else f" from {start} to {end}") + "."]
        top = g.reindex(g["spent"].abs().sort_values(ascending=False).index).head(per).sort_values("date")
        lines += [f"- {r.date:%d/%m/%Y} {r.payee or r.description or '(no payee)'}"
                  + (f" ({r.description})" if r.payee and r.description and r.description != r.payee else "")
                  + (f" [{r.category}]" if len(cats) > 1 else "") + f" — {_eur(r.spent)}" for r in top.itertuples()]
        if len(g) > per:
            lines.append(f"(the {per} largest of {len(g)} shown)")
        out.append("\n".join(lines))
    if not out:
        return f"No transactions from {start} to {end} in a category matching " + ", ".join(f'"{n}"' for n in missing) + "."
    head = f"Transactions from {start} to {end}:\n\n" if several else ""
    tail = ("\n\nNo transactions in: " + ", ".join(missing) + ".") if missing else ""
    return head + "\n\n".join(out) + tail


def holdings_performance(text: str) -> str:
    """Best or worst performing holdings: "worst today", "best this month", "worst 10 since purchase", "top 5 YTD".

    Today is each security's latest close against the close before it; a period (week, month, YTD, a quarter...)
    the latest close against the last close before the period; "since purchase" / "total" the value against its
    FIFO cost. Gains and losses are in EUR at the latest FX rate, for what you hold now, all accounts together."""
    t = text.lower()
    best = bool(re.search(r"\b(best|top[- ]performing|gainers?|winners?|up the most|most up)\b", t))
    n_m = re.search(r"\b(?:top|worst|best|bottom)?\s*(\d{1,2})\b(?!\d)(?!\s*(?:/|-))", re.sub(r"(19|20)\d{2}", "", t))
    n = max(1, min(int(n_m.group(1)), 30)) if n_m else 10
    total = bool(re.search(r"since (purchase|bought|buying)|\btotal\b|overall|all[- ]time|vs cost|unrealized|unrealised", t))
    daily = not total and (bool(re.search(r"\b(today|day|daily|1d)\b", t)) or not re.search(
        r"week|month|year|ytd|q[1-4]|h[12]|\d{4}|" + "|".join(m for m in _MONTHS), t))
    if not total and not daily:
        if re.search(r"\bweek\b", t):
            start = date.today() - timedelta(days=7)
        else:
            start, _ = parse_period(text)
    conn = get_connection()
    try:
        df = pd.read_sql("""
            WITH fx AS (SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC),
                 h AS (SELECT Securities_Id, SUM(Quantity)::float AS qty, SUM(Quantity * COALESCE(Fifo_Avg_Cost_EUR, 0))::float AS cost_eur,
                              BOOL_OR(COALESCE(Fifo_Avg_Cost_EUR, 0) <= 0) AS no_cost
                       FROM Holdings WHERE Quantity > 0 GROUP BY Securities_Id),
                 last2 AS (SELECT Securities_Id, Date, Close::float AS close,
                                  ROW_NUMBER() OVER (PARTITION BY Securities_Id ORDER BY Date DESC) AS rn
                           FROM Historical_Prices WHERE Close IS NOT NULL AND Close > 0
                             AND Securities_Id IN (SELECT Securities_Id FROM h) AND Date > CURRENT_DATE - 40)
            SELECT s.Securities_Id AS sid, s.Securities_Name AS name, h.qty, h.cost_eur, h.no_cost,
                   COALESCE(fx.FX_Rate, 1)::float AS fx,
                   l1.Date AS last_date, l1.close AS last_close, l2.close AS prev_close,
                   (SELECT hp.Close::float FROM Historical_Prices hp WHERE hp.Securities_Id = s.Securities_Id AND hp.Close > 0
                      AND hp.Date < %(start)s ORDER BY hp.Date DESC LIMIT 1) AS start_close
            FROM h JOIN Securities s ON s.Securities_Id = h.Securities_Id
            JOIN last2 l1 ON l1.Securities_Id = h.Securities_Id AND l1.rn = 1
            LEFT JOIN last2 l2 ON l2.Securities_Id = h.Securities_Id AND l2.rn = 2
            LEFT JOIN fx ON fx.Currencies_Id_1 = s.Currencies_Id
        """, conn, params={"start": (date.today() if daily or total else start)})
    finally:
        conn.close()
    if df.empty:
        return "No current holdings with recent prices."
    df["value"] = df["qty"] * df["last_close"] * df["fx"]
    df = df[df["value"] > 1]
    if total:
        df = df[~df["no_cost"] & (df["cost_eur"] > 0)]
        df["chg"] = df["value"] - df["cost_eur"]
        df["pct"] = df["chg"] / df["cost_eur"] * 100
        what = "since purchase (value now vs FIFO cost; positions without a recorded cost left out)"
    else:
        ref = df["prev_close"] if daily else df["start_close"]
        df = df[ref.notna() & (ref > 0)]
        ref = df["prev_close"] if daily else df["start_close"]
        df["pct"] = (df["last_close"] / ref - 1) * 100
        df["chg"] = df["qty"] * (df["last_close"] - ref) * df["fx"]
        what = "on the latest trading day (latest close vs the one before)" if daily else f"since {start} (latest close vs the last close before it)"
    if df.empty:
        return "No holdings with prices to compare for that period."
    df = df.sort_values("pct", ascending=not best)
    common = df["last_date"].mode().iloc[0]
    lines = [f"{'Best' if best else 'Worst'} {min(n, len(df))} of your {len(df)} holdings {what}, by % change:"]
    for i, r in enumerate(df.head(n).itertuples(), 1):
        stale = f" [last price {r.last_date:%d/%m/%Y}]" if r.last_date != common else ""
        lines.append(f"{i}. {r.name} — {r.pct:+.2f}% ({'+' if r.chg >= 0 else '−'}€{abs(r.chg):,.2f}), position €{r.value:,.2f}{stale}")
    lines.append(f"All {len(df)} together: {'+' if df['chg'].sum() >= 0 else '−'}€{abs(df['chg'].sum()):,.2f}"
                 + (f" (prices as of {common:%d/%m/%Y})" if daily else "") + ".")
    return "\n".join(lines)


def portfolio_value(text: str = "") -> str:
    """Investments, cash, pension & savings, assets and net worth now — the Dashboard's own KPI figures (its account
    breakdown), with the same changes since yesterday, the end of last month and the end of last year, and the
    investment accounts one by one."""
    from api.routers.dashboard import _KPI_GROUPS, get_account_breakdown

    def signed(v: float) -> str:
        return f"{'+' if v >= 0 else '−'}€{abs(v):,.2f}"

    groups = {}
    for key in _KPI_GROUPS:
        b = get_account_breakdown(group=key, account_ids=None, include_future=False)
        accs = b["accounts"]
        groups[key] = {
            "label": b["label"], "accounts": accs,
            "now": sum(a["balance_eur"] or 0 for a in accs),
            "d": sum(a.get("delta_daily") or 0 for a in accs),
            "m": sum(a.get("delta_prev_month") or 0 for a in accs),
            "y": sum(a.get("delta_ytd") or 0 for a in accs),
            "as_of": b.get("as_of"),
        }
    inv = groups["investments"]
    lines = [f"Investment portfolio (Brokerage, Margin and Other Investment accounts at the latest prices, in EUR) as of {inv['as_of']}: "
             f"€{inv['now']:,.2f}. Change since yesterday {signed(inv['d'])}, since the end of last month {signed(inv['m'])}, "
             f"since the end of last year {signed(inv['y'])} (as on the Dashboard; these include money moved in or out)."]
    held = sorted((a for a in inv["accounts"] if abs(a["balance_eur"] or 0) >= 0.5), key=lambda a: -(a["balance_eur"] or 0))
    if held:
        lines.append("By account:")
        lines += [f"- {a['name']} — €{a['balance_eur']:,.2f} (today {signed(a.get('delta_daily') or 0)})" for a in held[:12]]
        if len(held) > 12:
            lines.append(f"- … and {len(held) - 12} smaller account{'s' if len(held) > 13 else ''}, €{sum(a['balance_eur'] for a in held[12:]):,.2f} together")
    total = sum(g["now"] for g in groups.values())
    nw = (f"Net worth: €{total:,.2f} = " + " + ".join(f"{g['label']} €{g['now']:,.2f}" for g in groups.values())
          + f". Since yesterday {signed(sum(g['d'] for g in groups.values()))}, since the end of last year {signed(sum(g['y'] for g in groups.values()))}.")
    # Asked about net worth, it comes first; asked about the portfolio, last.
    if re.search(r"net[- ]?worth|wealth|everything|in total|all (my )?(money|accounts)", text.lower()):
        lines.insert(0, nw)
    else:
        lines.append(nw)
    return "\n".join(lines)


def net_worth_history(text: str = "") -> str:
    """Net worth at the end of each month over a span ("over the last 3 months", "this year", "last 12 months"; 3 months
    by default) and today, split into cash / investments / pension & savings / assets, with the change over the span.
    Rebuilt the way the monthly AI summary does it: balances less what was booked later, holdings at that day's prices."""
    from ai.monthly_summary import _net_worth_as_of

    t = text.lower()
    today = date.today()
    m = re.search(r"(\d{1,2})\s*months?", t)
    if m:
        months = max(1, min(int(m.group(1)), 36))
    elif re.search(r"this year|ytd|year to date|since (january|the start of the year)", t):
        months = today.month           # back to 31 Dec
    elif re.search(r"(last|past) year|12 months|a year", t):
        months = 12
    elif re.search(r"(last|past) month|a month", t):
        months = 1
    else:
        months = 3
    ends = []
    y, mo = today.year, today.month
    for _ in range(months):            # the last day of each earlier month, newest first
        first = date(y, mo, 1)
        prev_end = first - timedelta(days=1)
        ends.append(prev_end)
        y, mo = prev_end.year, prev_end.month
    points = sorted(ends) + [today]
    conn = get_connection()
    try:
        rows = [(d, _net_worth_as_of(conn, d.isoformat())) for d in points]
    finally:
        conn.close()
    rows = [(d, r) for d, r in rows if r]
    if len(rows) < 2:
        return "Not enough history to show how net worth changed."

    def signed(v: float) -> str:
        return f"{'+' if v >= 0 else '−'}€{abs(v):,.2f}"

    (d0, a), (d1, b) = rows[0], rows[-1]
    chg = b["total"] - a["total"]
    lines = [f"Net worth went from €{a['total']:,.2f} on {d0:%d/%m/%Y} to €{b['total']:,.2f} today ({d1:%d/%m/%Y}): "
             f"{signed(chg)} ({chg / a['total'] * 100:+.1f}%) over {len(rows) - 1} month{'s' if len(rows) > 2 else ''}.",
             "By part: " + "; ".join(f"{label} {signed(b[k] - a[k])} (now €{b[k]:,.2f})" for k, label in
                                      (("cash", "Cash"), ("investments", "Investments"), ("pension", "Pension & Savings"), ("assets", "Assets"))) + ".",
             "Month by month:"]
    prev = None
    for d, r in rows:
        lines.append(f"- {d:%d/%m/%Y}{' (today)' if d == today else ''}: €{r['total']:,.2f}" + (f" ({signed(r['total'] - prev)})" if prev is not None else ""))
        prev = r["total"]
    lines.append("Changes include money moved in or out, not only market moves.")
    return "\n".join(lines)


def investment_pnl(text: str = "") -> str:
    """Investment P&L in total and per investment account — the figures of Reports -> Inv. Performance (its /pnl query:
    value change less money put in or taken out, dividends and interest included, realized and unrealized), for today,
    this week, month, quarter, year to date (the default) or all time."""
    from api.routers.reports import get_pnl

    t = text.lower()
    if re.search(r"all[- ]time|since (the )?(start|beginning|inception)|ever|overall|lifetime", t):
        key, label, split = "pnl_all_time_eur", "all time", ("realized_pnl_eur", "unrealized_pnl_eur")
    elif re.search(r"\btoday\b|\bdaily\b|\bdtd\b|\bday\b", t):
        key, label, split = "pnl_dtd_eur", "today", None
    elif re.search(r"\bweek\b|\bwtd\b", t):
        key, label, split = "pnl_wtd_eur", "this week", None
    elif re.search(r"\bmonth\b|\bmtd\b", t):
        key, label, split = "pnl_mtd_eur", "this month", None
    elif re.search(r"\bquarter\b|\bqtd\b", t):
        key, label, split = "pnl_qtd_eur", "this quarter", None
    else:
        key, label, split = "pnl_ytd_eur", "year to date", ("realized_pnl_ytd_eur", "unrealized_pnl_ytd_eur")
    df = pd.DataFrame(get_pnl(start_date="1900-01-01", end_date=None))
    if df.empty:
        return "No investment P&L to report."
    cols = [key, "current_value_eur"] + list(split or [])
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    acc = df.groupby("accounts_name", as_index=False)[cols].sum()
    acc = acc[(acc[key].abs() >= 0.005) | (acc["current_value_eur"].abs() >= 0.5)].sort_values(key)

    def signed(v: float) -> str:
        return f"{'+' if v >= 0 else '−'}€{abs(v):,.2f}"

    tot = acc[key].sum()
    head = f"Investment P&L {label}: {signed(tot)} in total"
    if split:
        head += f" (realized {signed(acc[split[0]].sum())}, unrealized {signed(acc[split[1]].sum())})"
    lines = [head + " — as in Reports → Inv. Performance: change in value less money put in or taken out, dividends and interest included.",
             "Per investment account, worst first:"]
    for r in acc.itertuples():
        d = r._asdict()
        extra = f"; realized {signed(d[split[0]])}, unrealized {signed(d[split[1]])}" if split else ""
        lines.append(f"- {r.accounts_name} — {signed(d[key])} (value now €{abs(r.current_value_eur) if abs(r.current_value_eur) < 0.005 else r.current_value_eur:,.2f}{extra})")
    top = df[df[key].abs() >= 0.005].sort_values(key)
    if len(top) > 1:
        lines.append("Largest contributors: worst " + "; ".join(f"{r.securities_name} {signed(getattr(r, key))}" for r in top.head(3).itertuples())
                     + " — best " + "; ".join(f"{r.securities_name} {signed(getattr(r, key))}" for r in top.tail(3).iloc[::-1].itertuples()) + ".")
    return "\n".join(lines)


def net_worth_sources(text: str = "") -> str:
    """Where a change in net worth came from: income less expenses (the categorised cash flows, tax counted as an
    expense) against investment P&L (Reports -> Inv. Performance), over the last 12 months by default, this year to
    date, or the last N years. What neither explains (pension & savings growth, asset values, FX, uncategorised money,
    a pension cash-out moving between parts) is shown as the rest."""
    from ai.monthly_summary import _net_worth_as_of
    from api.routers.reports import get_pnl, get_pnl_period

    t = text.lower()
    today = date.today()
    ny = re.search(r"(\d{1,2})\s*years?", t)
    if re.search(r"this year|ytd|year to date|since (january|the start of the year)", t):
        start, label, years = date(today.year - 1, 12, 31), "this year", None
    else:
        years = max(1, min(int(ny.group(1)), 10)) if ny else 1
        try:
            start = today.replace(year=today.year - years)
        except ValueError:                       # 29 February
            start = today.replace(year=today.year - years, day=28)
        label = "the last 12 months" if years == 1 else f"the last {years} years"

    conn = get_connection()
    try:
        a, b = _net_worth_as_of(conn, start.isoformat()), _net_worth_as_of(conn, today.isoformat())
        cf = pd.read_sql("""
            SELECT
                SUM(CASE WHEN c.Categories_Type = 'Income'   THEN s.Amount ELSE 0 END) AS income,
                SUM(CASE WHEN c.Categories_Type = 'Expense'  THEN s.Amount ELSE 0 END) AS expense,
                SUM(CASE WHEN c.Categories_Type = 'Tax'      THEN s.Amount ELSE 0 END) AS tax,
                SUM(CASE WHEN c.Categories_Type = 'Interest' THEN s.Amount ELSE 0 END) AS interest
            FROM Splits s
            JOIN Categories c ON c.Categories_Id = s.Categories_Id
            JOIN Transactions t ON t.Transactions_Id = s.Transactions_Id
            WHERE t.Date > %(start)s AND t.Date <= %(end)s AND t.Is_Draft = FALSE
        """, conn, params={"start": start, "end": today}).iloc[0]
    finally:
        conn.close()
    if not a or not b:
        return "Not enough history to say where the change in net worth came from."
    income, expense, tax, interest = (float(cf[k] or 0) for k in ("income", "expense", "tax", "interest"))
    saved = income + expense + tax + interest

    if years is None:
        df = pd.DataFrame(get_pnl(start_date="1900-01-01", end_date=None))
        key = "pnl_ytd_eur"
    else:
        df = pd.DataFrame(get_pnl_period(years=years))
        key = "pnl_eur"
    pnl = float(pd.to_numeric(df[key], errors="coerce").fillna(0).sum()) if not df.empty and key in df else 0.0

    change = b["total"] - a["total"]
    rest = change - saved - pnl

    def signed(v: float) -> str:
        return f"{'+' if v >= 0 else '−'}€{abs(v):,.2f}"

    def share(v: float) -> str:
        return f" ({v / change * 100:.0f}% of the change)".replace("-", "−") if abs(change) >= 1 else ""

    parts = sorted((("income less expenses", saved), ("investment gains", pnl), ("everything else", rest)), key=lambda x: -abs(x[1]))
    lines = [
        f"Over {label} ({start:%d/%m/%Y} to {today:%d/%m/%Y}) net worth went from €{a['total']:,.2f} to €{b['total']:,.2f}: {signed(change)}.",
        f"It came mostly from {parts[0][0]} ({signed(parts[0][1])}).",
        "",
        f"- Income less expenses: {signed(saved)}{share(saved)} — income €{income:,.2f}, expenses €{abs(expense):,.2f}, "
        f"tax €{abs(tax):,.2f}" + (f", interest {signed(interest)}" if abs(interest) >= 0.5 else "") + " (categorised transactions; transfers between your own accounts are not income).",
        f"- Investment gains: {signed(pnl)}{share(pnl)} — as in Reports → Inv. Performance: change in value less money put in "
        "or taken out, dividends included.",
        f"- Everything else: {signed(rest)}{share(rest)} — pension & savings growth, asset values, exchange rates and money "
        "without a category, plus money moving between parts (e.g. a pension paid out to cash).",
        "",
        "Net worth by part: " + "; ".join(f"{lbl} {signed(b[k] - a[k])} (now €{b[k]:,.2f})" for k, lbl in
                                         (("cash", "Cash"), ("investments", "Investments"), ("pension", "Pension & Savings"), ("assets", "Assets"))) + ".",
    ]
    return "\n".join(lines)


# Spending that is mostly fixed — shown as one total, not as something to cut. Matched on any level of the category path
# (and Taxes are left out altogether, as are business costs that are reimbursed).
_FIXED_TOP = re.compile(r"^(taxes?|insurance|health ?care|medical|bills?|utilities|rent|mortgage|loans?|education|school)\b", re.I)
_REIMBURSED = re.compile(r"\(reimbursed\)", re.I)


def spending_reduction(text: str = "") -> str:
    """Where spending could be reduced, for a period (this year to date by default): the flexible categories, largest
    first, against the same days a year before, the ones that grew most, and what a 20% cut of the top five would save
    a year. Taxes and reimbursed business costs are left out; mostly fixed costs (insurance, healthcare, bills,
    utilities, rent / loans, education) are one total."""
    start, end = parse_period(text or "this year")
    try:
        p_start, p_end = start.replace(year=start.year - 1), end.replace(year=end.year - 1)
    except ValueError:
        p_start, p_end = start.replace(year=start.year - 1, day=28), end.replace(year=end.year - 1, day=28)
    cur, prev = _spending(start, end), _spending(p_start, p_end)
    conn = get_connection()
    try:
        types = pd.read_sql("SELECT Categories_Id AS cid, Categories_Type AS type FROM Categories", conn)
    finally:
        conn.close()
    if cur.empty:
        return f"No spending recorded from {start} to {end}."
    df = cur.merge(types, on="cid", how="left").merge(prev[["cid", "spent"]].rename(columns={"spent": "before"}), on="cid", how="left")
    df["before"] = df["before"].fillna(0.0)
    df = df[(df["type"] != "Tax") & ~df["category"].str.contains(_REIMBURSED)]
    df = df[df["spent"] > 0.005]
    is_fixed = df["category"].apply(lambda c: any(_FIXED_TOP.match(p.strip()) for p in c.split(" : ")))   # e.g. Business Expenses : Bills
    fixed, flex = df[is_fixed], df[~is_fixed].sort_values("spent", ascending=False)
    days = (end - start).days + 1
    year = 365 / days

    def chg(r) -> str:
        if r.before < 0.5:
            return "new this year" if r.spent >= 1 else ""
        d = r.spent - r.before
        return f"{'+' if d >= 0 else '−'}€{abs(d):,.0f} vs a year before ({d / r.before * 100:+.0f}%)".replace("(-", "(−")

    total = df["spent"].sum()
    lines = [f"Where spending could be reduced, {start:%d/%m/%Y} to {end:%d/%m/%Y} ({days} days). Taxes and reimbursed business "
             f"costs are left out. Of €{total:,.2f} spent, €{fixed['spent'].sum():,.2f} is mostly fixed (insurance, healthcare, "
             f"bills, utilities, rent or loans, education) and €{flex['spent'].sum():,.2f} is flexible.",
             "", "Largest flexible categories (per month, and against the same days a year before):"]
    for i, r in enumerate(flex.head(12).itertuples(), 1):
        c = chg(r)
        lines.append(f"{i}. {r.category} — €{r.spent:,.2f} (€{r.spent / days * 30.4:,.0f}/month){'; ' + c if c else ''}")
    grew = flex.assign(d=flex["spent"] - flex["before"]).sort_values("d", ascending=False)
    grew = grew[(grew["d"] >= 50) & (grew["before"] >= 0.5)].head(5)
    if not grew.empty:
        lines += ["", "Grew most against a year before:"] + [
            f"- {r.category}: €{r.before:,.0f} → €{r.spent:,.0f} ({chg(r).split(' vs ')[0]})" for r in grew.itertuples()]
    top5 = flex.head(5)
    if not top5.empty:
        cut = top5["spent"].sum() * 0.2 * year
        lines += ["", f"Cutting the five largest flexible categories by 20% would save about €{cut:,.0f} a year "
                      f"(they come to €{top5['spent'].sum() * year:,.0f} a year at this pace)."]
    return "\n".join(lines)

