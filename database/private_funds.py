"""Private funds — venture, private equity, real-estate and other funds you commit to and are called on for money.

A fund has a commitment and a list of dated events: a **call** (money you paid in when the manager called capital), a **distribution** (money paid back to you) and a
**nav** (the fund's own valuation of what you still hold, from its statements). From them:

  paid-in      the calls to date
  unfunded     commitment − paid-in, what can still be called
  DPI          distributions ÷ paid-in            (cash back)
  RVPI         latest NAV ÷ paid-in               (value still held)
  TVPI         (distributions + NAV) ÷ paid-in    (total value; the "multiple")
  IRR          the yearly rate that makes the dated flows — calls out, distributions in, the latest NAV as a final inflow on its date — net to zero (XIRR)

A call dated in the future (an expected call, from the manager's notice) is not paid-in yet; it shows as upcoming and raises an alert ahead of its date
(group "Upcoming Events"). Amounts are in the fund's currency; the totals are converted to EUR at the latest rate.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from database.connection import get_connection

TYPES = ("call", "distribution", "nav")
FUND_TYPES = ("Venture", "Private Equity", "Real Estate", "Infrastructure", "Private Credit", "Other")
ALERT_LEAD_DAYS = 14


def ensure_tables() -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS Private_Funds (
                    Private_Funds_Id SERIAL PRIMARY KEY,
                    Name          TEXT NOT NULL,
                    Manager       TEXT,
                    Fund_Type     TEXT NOT NULL DEFAULT 'Private Equity',
                    Vintage_Year  INTEGER,
                    Currency      VARCHAR(3) NOT NULL DEFAULT 'EUR',
                    Commitment    NUMERIC(18,2) NOT NULL DEFAULT 0,
                    Accounts_Id   INTEGER REFERENCES Accounts(Accounts_Id) ON DELETE SET NULL,
                    Status        TEXT NOT NULL DEFAULT 'active',
                    Notes         TEXT,
                    Created_At    TIMESTAMP DEFAULT NOW()
                )""")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS Private_Fund_Events (
                    Private_Fund_Events_Id SERIAL PRIMARY KEY,
                    Private_Funds_Id INTEGER NOT NULL REFERENCES Private_Funds(Private_Funds_Id) ON DELETE CASCADE,
                    Event_Date    DATE NOT NULL,
                    Event_Type    TEXT NOT NULL,
                    Amount        NUMERIC(18,2) NOT NULL,
                    Note          TEXT,
                    Created_At    TIMESTAMP DEFAULT NOW()
                )""")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_private_fund_events_fund ON Private_Fund_Events (Private_Funds_Id, Event_Date)")
        conn.commit()
    finally:
        conn.close()


# ── maths ────────────────────────────────────────────────────────────────────────────────────────

def xirr(flows: list[tuple[date, float]]) -> float | None:
    """The yearly rate at which the dated flows net to zero, or None when there is none to find (needs money out and money in). Found by bisection."""
    flows = [(d, float(a)) for d, a in flows if a]
    if not flows or not any(a < 0 for _, a in flows) or not any(a > 0 for _, a in flows):
        return None
    t0 = min(d for d, _ in flows)

    def npv(r: float) -> float:
        return sum(a / (1.0 + r) ** ((d - t0).days / 365.0) for d, a in flows)

    lo, hi = -0.9999, 100.0
    flo, fhi = npv(lo), npv(hi)
    if flo * fhi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        fm = npv(mid)
        if abs(fm) < 1e-9:
            return mid
        if flo * fm < 0:
            hi = mid
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2


def metrics(fund: dict, events: list[dict], today: date | None = None) -> dict:
    today = today or date.today()
    calls = [e for e in events if e["type"] == "call"]
    paid = [e for e in calls if e["date"] <= today]
    upcoming = sorted((e for e in calls if e["date"] > today), key=lambda e: e["date"])
    dists = [e for e in events if e["type"] == "distribution" and e["date"] <= today]
    navs = sorted((e for e in events if e["type"] == "nav" and e["date"] <= today), key=lambda e: e["date"])
    paid_in = sum(e["amount"] for e in paid)
    distributions = sum(e["amount"] for e in dists)
    nav = navs[-1]["amount"] if navs else 0.0
    nav_date = navs[-1]["date"] if navs else None
    flows = [(e["date"], -e["amount"]) for e in paid] + [(e["date"], e["amount"]) for e in dists]
    if nav_date is not None and nav:
        flows.append((max(nav_date, max((d for d, _ in flows), default=nav_date)), nav))      # the NAV is the last inflow, never before the last flow
    commitment = float(fund["commitment"] or 0)
    return {
        "paid_in": round(paid_in, 2), "distributions": round(distributions, 2), "nav": round(nav, 2), "nav_date": nav_date.isoformat() if nav_date else None,
        "unfunded": round(max(commitment - paid_in, 0.0), 2), "called_pct": round(paid_in / commitment * 100, 1) if commitment else None,
        "dpi": round(distributions / paid_in, 3) if paid_in else None, "rvpi": round(nav / paid_in, 3) if paid_in else None,
        "tvpi": round((distributions + nav) / paid_in, 3) if paid_in else None,
        "gain": round(distributions + nav - paid_in, 2),
        "irr_pct": None if (r := xirr(flows)) is None else round(r * 100, 2),
        "next_call": {"date": upcoming[0]["date"].isoformat(), "amount": upcoming[0]["amount"], "days": (upcoming[0]["date"] - today).days} if upcoming else None,
        "upcoming_calls": [{"id": e["id"], "date": e["date"].isoformat(), "amount": e["amount"], "days": (e["date"] - today).days, "note": e.get("note")} for e in upcoming],
    }


# ── storage ──────────────────────────────────────────────────────────────────────────────────────

FUND_COLS = "Private_Funds_Id, Name, Manager, Fund_Type, Vintage_Year, Currency, Commitment, Accounts_Id, Status, Notes"


def _fund(r) -> dict:
    return {"id": r[0], "name": r[1], "manager": r[2], "fund_type": r[3], "vintage_year": r[4], "currency": r[5], "commitment": float(r[6] or 0),
            "accounts_id": r[7], "status": r[8], "notes": r[9]}


def _event(r) -> dict:
    return {"id": r[0], "funds_id": r[1], "date": r[2], "type": r[3], "amount": float(r[4]), "note": r[5]}


def _eur_rate(cur, code: str) -> float:
    if not code or code.upper() == "EUR":
        return 1.0
    cur.execute("""SELECT h.FX_Rate FROM Historical_FX h JOIN Currencies c ON c.Currencies_Id = h.Currencies_Id_1
                   WHERE c.Currencies_ShortName = %s ORDER BY h.Date DESC LIMIT 1""", (code.upper(),))
    row = cur.fetchone()
    return float(row[0]) if row and row[0] else 1.0


def list_funds() -> dict:
    ensure_tables()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {FUND_COLS} FROM Private_Funds ORDER BY Status, Vintage_Year DESC NULLS LAST, Name")
            funds = [_fund(r) for r in cur.fetchall()]
            cur.execute("SELECT Private_Fund_Events_Id, Private_Funds_Id, Event_Date, Event_Type, Amount, Note FROM Private_Fund_Events ORDER BY Event_Date")
            events = [_event(r) for r in cur.fetchall()]
            rates = {f["currency"]: _eur_rate(cur, f["currency"]) for f in funds}
    finally:
        conn.close()
    out, tot = [], {"commitment": 0.0, "paid_in": 0.0, "unfunded": 0.0, "nav": 0.0, "distributions": 0.0}
    flows_all: list[tuple[date, float]] = []
    for f in funds:
        ev = [e for e in events if e["funds_id"] == f["id"]]
        m = metrics(f, ev)
        r = rates[f["currency"]]
        out.append({**f, **m, "events": len(ev), "rate_eur": r})
        if f["status"] == "active":
            tot["commitment"] += f["commitment"] * r
            for k in ("paid_in", "unfunded", "nav", "distributions"):
                tot[k] += m[k] * r
            today = date.today()
            flows_all += [(e["date"], -e["amount"] * r) for e in ev if e["type"] == "call" and e["date"] <= today]
            flows_all += [(e["date"], e["amount"] * r) for e in ev if e["type"] == "distribution" and e["date"] <= today]
            navs = sorted((e for e in ev if e["type"] == "nav" and e["date"] <= today), key=lambda e: e["date"])
            if navs and navs[-1]["amount"]:
                flows_all.append((max(navs[-1]["date"], max((d for d, _ in flows_all), default=navs[-1]["date"])), navs[-1]["amount"] * r))
    summary = {k: round(v, 2) for k, v in tot.items()}
    summary["tvpi"] = round((tot["distributions"] + tot["nav"]) / tot["paid_in"], 3) if tot["paid_in"] else None
    summary["dpi"] = round(tot["distributions"] / tot["paid_in"], 3) if tot["paid_in"] else None
    irr = xirr(flows_all)
    summary["irr_pct"] = None if irr is None else round(irr * 100, 2)
    upcoming = sorted((c for f in out for c in [{**u, "fund": f["name"], "currency": f["currency"], "funds_id": f["id"]} for u in f["upcoming_calls"]]), key=lambda c: c["date"])
    return {"funds": out, "summary": summary, "upcoming": upcoming}


def get_fund(fund_id: int) -> dict | None:
    ensure_tables()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {FUND_COLS} FROM Private_Funds WHERE Private_Funds_Id = %s", (fund_id,))
            r = cur.fetchone()
            if not r:
                return None
            f = _fund(r)
            cur.execute("SELECT Private_Fund_Events_Id, Private_Funds_Id, Event_Date, Event_Type, Amount, Note FROM Private_Fund_Events WHERE Private_Funds_Id = %s ORDER BY Event_Date, Private_Fund_Events_Id", (fund_id,))
            ev = [_event(x) for x in cur.fetchall()]
    finally:
        conn.close()
    return {**f, **metrics(f, ev), "events": [{**e, "date": e["date"].isoformat()} for e in ev]}


def _clean_fund(d: dict) -> dict:
    name = str(d.get("name") or "").strip()
    if not name:
        raise ValueError("Give the fund a name.")
    ft = d.get("fund_type") if d.get("fund_type") in FUND_TYPES else "Other"
    try:
        commitment = float(d.get("commitment") or 0)
    except (TypeError, ValueError):
        raise ValueError("The commitment must be a number.")
    if commitment < 0:
        raise ValueError("The commitment cannot be negative.")
    vy = d.get("vintage_year")
    return {"name": name, "manager": (d.get("manager") or None), "fund_type": ft, "vintage_year": int(vy) if vy not in (None, "") else None,
            "currency": (str(d.get("currency") or "EUR").upper()[:3]), "commitment": commitment, "accounts_id": d.get("accounts_id") or None,
            "status": "closed" if d.get("status") == "closed" else "active", "notes": d.get("notes") or None}


def save_fund(fund_id: int | None, d: dict) -> int:
    ensure_tables()
    c = _clean_fund(d)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            vals = (c["name"], c["manager"], c["fund_type"], c["vintage_year"], c["currency"], c["commitment"], c["accounts_id"], c["status"], c["notes"])
            if fund_id:
                cur.execute("UPDATE Private_Funds SET Name=%s, Manager=%s, Fund_Type=%s, Vintage_Year=%s, Currency=%s, Commitment=%s, Accounts_Id=%s, Status=%s, Notes=%s "
                            "WHERE Private_Funds_Id=%s RETURNING Private_Funds_Id", vals + (fund_id,))
            else:
                cur.execute("INSERT INTO Private_Funds (Name, Manager, Fund_Type, Vintage_Year, Currency, Commitment, Accounts_Id, Status, Notes) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING Private_Funds_Id", vals)
            row = cur.fetchone()
            if not row:
                raise ValueError("That fund no longer exists.")
        conn.commit()
        return row[0]
    finally:
        conn.close()


def delete_fund(fund_id: int) -> None:
    ensure_tables()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM Private_Funds WHERE Private_Funds_Id = %s", (fund_id,))
        conn.commit()
    finally:
        conn.close()


def save_event(fund_id: int, event_id: int | None, d: dict) -> int:
    ensure_tables()
    typ = d.get("type")
    if typ not in TYPES:
        raise ValueError("The event must be a call, a distribution or a NAV.")
    try:
        when = date.fromisoformat(str(d.get("date"))[:10])
        amount = float(d.get("amount"))
    except (TypeError, ValueError):
        raise ValueError("Give a date and an amount.")
    if amount < 0 or (amount == 0 and typ != "nav"):
        raise ValueError("The amount must be positive (a NAV of 0 means the fund is worth nothing).")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if event_id:
                cur.execute("UPDATE Private_Fund_Events SET Event_Date=%s, Event_Type=%s, Amount=%s, Note=%s WHERE Private_Fund_Events_Id=%s AND Private_Funds_Id=%s RETURNING Private_Fund_Events_Id",
                            (when, typ, amount, d.get("note") or None, event_id, fund_id))
            else:
                cur.execute("INSERT INTO Private_Fund_Events (Private_Funds_Id, Event_Date, Event_Type, Amount, Note) VALUES (%s,%s,%s,%s,%s) RETURNING Private_Fund_Events_Id",
                            (fund_id, when, typ, amount, d.get("note") or None))
            row = cur.fetchone()
            if not row:
                raise ValueError("That entry no longer exists.")
        conn.commit()
        return row[0]
    finally:
        conn.close()


def delete_event(event_id: int) -> None:
    ensure_tables()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM Private_Fund_Events WHERE Private_Fund_Events_Id = %s", (event_id,))
        conn.commit()
    finally:
        conn.close()


# ── the alert ────────────────────────────────────────────────────────────────────────────────────

def get_fund_call_alerts() -> list[dict]:
    """A heads-up for each capital call dated in the next ALERT_LEAD_DAYS days (or today): it stops appearing once the date passes, like the bond and loan reminders."""
    try:
        data = list_funds()
    except Exception:
        return []
    out = []
    for c in data["upcoming"]:
        if 0 <= c["days"] <= ALERT_LEAD_DAYS:
            when = "today" if c["days"] == 0 else f"in {c['days']} day{'s' if c['days'] != 1 else ''}"
            out.append({"level": "warning", "type": "fund_call", "key": f"fund_call:{c['id']}",
                        "message": f"💶 **Capital call** — {c['fund']} expects {c['amount']:,.2f} {c['currency']} {when} ({datetime.fromisoformat(c['date']).strftime('%d %b %Y')})."
                                   + (f" _{c['note']}_" if c.get("note") else "")})
    return out
