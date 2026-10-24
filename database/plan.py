"""Plan — dated to-do steps (a rebalancing plan, a tax-year checklist, …) with a morning reminder on Telegram.

A step has a plan name (steps of one plan are shown together), a due date, a title, optional details, and a status: open, done or skipped.
Once a day (the `plan_reminders` scheduler job, 08:00 by default) a message lists the steps due today, the open ones that are overdue and,
as a heads-up, the ones due tomorrow. Nothing is sent when there is nothing to say, and the message goes through the same Telegram bot and
on/off switch as the other alerts (database/notify.py).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from database.connection import get_connection

STATUSES = ("open", "done", "skipped")
DEFAULT_PLAN = "My plan"

COLUMNS = "Plan_Items_Id AS id, Plan_Name AS plan, Due_Date::text AS due_date, Title AS title, Details AS details, Status AS status, Done_At::text AS done_at, Sort_Order AS sort_order"


def ensure_table() -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS Plan_Items (
                    Plan_Items_Id SERIAL PRIMARY KEY,
                    Plan_Name     TEXT NOT NULL DEFAULT 'My plan',
                    Due_Date      DATE NOT NULL,
                    Title         TEXT NOT NULL,
                    Details       TEXT,
                    Status        TEXT NOT NULL DEFAULT 'open',
                    Done_At       TIMESTAMP,
                    Sort_Order    INTEGER NOT NULL DEFAULT 0,
                    Created_At    TIMESTAMP DEFAULT NOW()
                )""")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_plan_items_due ON Plan_Items (Due_Date) WHERE Status = 'open'")
        conn.commit()
    finally:
        conn.close()


def _rows(cur) -> list[dict]:
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _clean(plan, title, details):
    plan = (plan or "").strip() or DEFAULT_PLAN
    title = (title or "").strip()
    if not title:
        raise ValueError("A step needs a title.")
    return plan[:120], title[:300], ((details or "").strip() or None)


def _date(v) -> date:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        raise ValueError(f"'{v}' is not a date (use YYYY-MM-DD).")


def list_items(plan: str | None = None, status: str | None = None) -> list[dict]:
    ensure_table()
    sql, args = f"SELECT {COLUMNS} FROM Plan_Items WHERE TRUE", []
    if plan:
        sql += " AND Plan_Name = %s"; args.append(plan)
    if status:
        sql += " AND Status = %s"; args.append(status)
    sql += " ORDER BY Due_Date, Sort_Order, Plan_Items_Id"
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return _rows(cur)
    finally:
        conn.close()


def plans() -> list[dict]:
    ensure_table()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT Plan_Name AS plan, COUNT(*) AS total, COUNT(*) FILTER (WHERE Status = 'open') AS open,
                                  MIN(Due_Date)::text AS first_due, MAX(Due_Date)::text AS last_due
                           FROM Plan_Items GROUP BY Plan_Name ORDER BY MAX(Due_Date) DESC, Plan_Name""")
            return _rows(cur)
    finally:
        conn.close()


def create_item(plan, due_date, title, details=None, sort_order: int = 0) -> dict:
    ensure_table()
    plan, title, details = _clean(plan, title, details)
    d = _date(due_date)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"INSERT INTO Plan_Items (Plan_Name, Due_Date, Title, Details, Sort_Order) VALUES (%s, %s, %s, %s, %s) RETURNING {COLUMNS}",
                        (plan, d, title, details, sort_order))
            row = _rows(cur)[0]
        conn.commit()
        return row
    finally:
        conn.close()


def update_item(item_id: int, plan, due_date, title, details=None) -> dict | None:
    ensure_table()
    plan, title, details = _clean(plan, title, details)
    d = _date(due_date)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE Plan_Items SET Plan_Name = %s, Due_Date = %s, Title = %s, Details = %s WHERE Plan_Items_Id = %s RETURNING {COLUMNS}",
                        (plan, d, title, details, item_id))
            rows = _rows(cur)
        conn.commit()
        return rows[0] if rows else None
    finally:
        conn.close()


def set_status(item_id: int, status: str) -> dict | None:
    if status not in STATUSES:
        raise ValueError("Status must be open, done or skipped.")
    ensure_table()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE Plan_Items SET Status = %s, Done_At = CASE WHEN %s = 'open' THEN NULL ELSE NOW() END WHERE Plan_Items_Id = %s RETURNING {COLUMNS}",
                        (status, status, item_id))
            rows = _rows(cur)
        conn.commit()
        return rows[0] if rows else None
    finally:
        conn.close()


def delete_item(item_id: int) -> bool:
    ensure_table()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM Plan_Items WHERE Plan_Items_Id = %s", (item_id,))
            n = cur.rowcount
        conn.commit()
        return n > 0
    finally:
        conn.close()


def delete_plan(plan: str) -> int:
    ensure_table()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM Plan_Items WHERE Plan_Name = %s", (plan,))
            n = cur.rowcount
        conn.commit()
        return n
    finally:
        conn.close()


# ── import from pasted text ──────────────────────────────────────────────────────────────────────

_MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def parse_date(text: str, today: date | None = None) -> date | None:
    """2026-10-26, 26/10/2026, 26.10.2026, 26 Oct 2026, Mon 26 Oct 2026 — and without the year (this year, or next when that is long past)."""
    today = today or date.today()
    s = re.sub(r"^(mon|tue|wed|thu|fri|sat|sun)[a-z]*[ ,.]+", "", text.strip(), flags=re.I)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    y = mo = d = None
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
    else:
        m = re.fullmatch(r"(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?", s)
        if m:
            d, mo = int(m[1]), int(m[2])
            y = int(m[3]) if m[3] else None
        else:
            m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?[ -]+([A-Za-z]{3})[a-z]*\.?(?:[ ,-]+(\d{2,4}))?", s)
            if not m or m[2].lower() not in _MONTHS:
                return None
            d, mo = int(m[1]), _MONTHS[m[2].lower()]
            y = int(m[3]) if m[3] else None
    if y is not None and y < 100:
        y += 2000
    try:
        if y is None:
            out = date(today.year, mo, d)
            if out < today - timedelta(days=180):
                out = date(today.year + 1, mo, d)
            return out
        return date(y, mo, d)
    except ValueError:
        return None


def import_text(text: str, plan: str | None = None) -> dict:
    """One step per line:  date | title | details   (details optional; further | stay in the details).
    A line starting with # names the plan for the lines after it; blank lines are skipped.
    -> {"created": n, "errors": ["line 3: …"], "plan": name}"""
    ensure_table()
    current = (plan or "").strip() or DEFAULT_PLAN
    created, errors = 0, []
    for n, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            current = line.lstrip("#").strip()[:120] or current
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            errors.append(f"line {n}: expected 'date | title | details'")
            continue
        d = parse_date(parts[0])
        if d is None:
            errors.append(f"line {n}: '{parts[0]}' is not a date")
            continue
        try:
            create_item(current, d, parts[1], " | ".join(parts[2:]) or None, sort_order=n)
            created += 1
        except ValueError as e:
            errors.append(f"line {n}: {e}")
    return {"created": created, "errors": errors, "plan": current}


# ── what is due, and the reminder ────────────────────────────────────────────────────────────────

def due_summary(today: date | None = None, ahead_days: int = 7) -> dict:
    """The open steps: overdue, due today, and due in the next `ahead_days` days (today excluded)."""
    today = today or date.today()
    out = {"today": today.isoformat(), "overdue": [], "due_today": [], "upcoming": []}
    for it in list_items(status="open"):
        d = date.fromisoformat(it["due_date"])
        if d < today:
            out["overdue"].append(it)
        elif d == today:
            out["due_today"].append(it)
        elif d <= today + timedelta(days=ahead_days):
            out["upcoming"].append(it)
    return out


def _line(it: dict, with_date: bool = False) -> str:
    head = f"• {date.fromisoformat(it['due_date']).strftime('%a %d %b')}: " if with_date else "• "
    s = head + it["title"]
    if it.get("details"):
        d = " ".join(str(it["details"]).split())
        s += "\n   " + (d if len(d) <= 300 else d[:297] + "…")
    if it["plan"] != DEFAULT_PLAN:
        s += f"\n   [{it['plan']}]"
    return s


def reminder_text(today: date | None = None) -> tuple[str, int]:
    """-> (message, number of steps due today or overdue). An empty message when nothing is due today, overdue or tomorrow."""
    today = today or date.today()
    s = due_summary(today, ahead_days=1)
    tomorrow = [i for i in s["upcoming"] if date.fromisoformat(i["due_date"]) == today + timedelta(days=1)]
    if not (s["overdue"] or s["due_today"] or tomorrow):
        return "", 0
    parts = [f"Oikos plan — {today.strftime('%A %d %B %Y')}"]
    if s["due_today"]:
        parts.append(f"\nDue today ({len(s['due_today'])}):\n" + "\n".join(_line(i) for i in s["due_today"]))
    if s["overdue"]:
        parts.append(f"\nOverdue ({len(s['overdue'])}):\n" + "\n".join(_line(i, True) for i in s["overdue"]))
    if tomorrow:
        parts.append(f"\nTomorrow ({len(tomorrow)}):\n" + "\n".join("• " + i["title"] for i in tomorrow))
    parts.append("\nTick them off under Plan in Oikos.")
    return "\n".join(parts), len(s["due_today"]) + len(s["overdue"])


def send_reminders(force: bool = False) -> tuple[bool, str]:
    """Send today's reminder. -> (sent, detail). `force` sends even when the Telegram alerts switch is off (a manual 'send now')."""
    from database.notify import alerts_enabled, send_telegram, telegram_configured
    text, n = reminder_text()
    if not text:
        return False, "Nothing is due today or overdue, and nothing tomorrow."
    if not telegram_configured():
        return False, "Telegram is not configured (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)."
    if not force and not alerts_enabled():
        return False, "Telegram alerts are switched off in Tools → App Settings."
    ok, detail = send_telegram(text)
    return ok, (f"reminder sent ({n} step{'s' if n != 1 else ''})" if ok else detail)


def last_reminder_date() -> date:
    """The day the reminder job last ran (from the scheduler's own record), so a restart later in the day does not send it again."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT last_run FROM Scheduler_Jobs WHERE job_id = 'plan_reminders'")
            row = cur.fetchone()
        conn.close()
        if row and row[0]:
            d = row[0]
            return d.astimezone().date() if isinstance(d, datetime) and d.tzinfo else d.date()
    except Exception:
        pass
    return date.min
