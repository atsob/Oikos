"""Groups, stable keys and dismissal for the Dashboard's alerts.

Every alert from check_triggered_alerts() gets a `group` and a stable `key`:

  sr         Support / Resistance — database/sr_alerts.py
  price      Price Alerts         — your own above/below thresholds
  signals    Signals              — signal changes, golden/death cross, trailing stop, Z-Score zone changes
  events     Upcoming Events      — bond maturities/coupons, dividend payments, loan payments
  corporate  Corporate Actions & Data — stock splits, split-amount mismatches
  portfolio  Portfolio            — allocation drift

Dismissal keeps each type's own mechanism where it has one (Signal_Notifications,
Fundamentals_Notifications and Corporate_Action_Notifications acknowledge a change;
Live_Alert_Dismissals silences a cross / trailing stop until it clears). Every other
type is dismissed by key in Alert_Dismissals: its key names the event (the level of a
support break, the date of a dividend), so dismissing it silences that event only, and a
dismissal is dropped once its alert is no longer active — a price that recrosses a
threshold, or a level broken again later, alerts afresh.
"""
from __future__ import annotations

import logging
import re

from database.connection import get_connection

log = logging.getLogger(__name__)

GROUPS = [
    ("sr", "Support / Resistance"),
    ("price", "Price Alerts"),
    ("signals", "Signals"),
    ("events", "Upcoming Events"),
    ("corporate", "Corporate Actions & Data"),
    ("portfolio", "Portfolio"),
]

TYPE_GROUP = {
    "price_alert": "price",
    "allocation_drift": "portfolio",
    "signal_change": "signals", "ma_cross": "signals", "trailing_stop": "signals", "zone_change": "signals",
    "bond_maturity": "events", "bond_coupon": "events", "dividend_payment": "events", "loan_payment": "events",
    "stock_split": "corporate", "split_mismatch": "corporate",
}

# Types dismissed by key in Alert_Dismissals, and the source (check_triggered_alerts' own
# label for the code that produces them) — a dismissal is only dropped as stale when its
# source ran without error this time.
KEYED_SOURCE = {
    "price_alert": "main", "allocation_drift": "main",
    "bond_maturity": "bond", "bond_coupon": "bond", "dividend_payment": "dividend",
    "loan_payment": "loan", "split_mismatch": "split_mismatch",
}

_DATE = re.compile(r"\((\d{1,2} \w{3} \d{4})\)")


def group_of(alert_type: str | None) -> str:
    if alert_type and alert_type.startswith("sr_"):
        return "sr"
    return TYPE_GROUP.get(alert_type or "", "portfolio")


def is_keyed(alert_type: str | None) -> bool:
    return bool(alert_type) and (alert_type.startswith("sr_") or alert_type in KEYED_SOURCE)


def source_of(alert_type: str) -> str:
    return "sr" if alert_type.startswith("sr_") else KEYED_SOURCE.get(alert_type, "main")


def key_of(a: dict) -> str:
    """A stable identity for an alert — the event, not its wording (which can carry a
    countdown or today's P&L)."""
    if a.get("key"):
        return a["key"]
    t = a.get("type") or "alert"
    sid = a.get("securities_id")
    msg = a.get("message", "")
    if t == "signal_change":
        m = re.search(r"→ \*\*(.+?)\*\*", msg)
        return f"signal_change:{sid}:{m.group(1) if m else ''}"
    if t == "zone_change":
        m = re.search(r"→ \*\*(.+?)\*\* Zone", msg)
        return f"zone_change:{sid}:{m.group(1) if m else ''}"
    if t == "stock_split":
        return f"stock_split:{a.get('corporate_actions_id')}"
    if t == "ma_cross":
        return f"ma_cross:{sid}:{'golden' if 'Golden' in msg else 'death'}"
    if t == "trailing_stop":
        return f"trailing_stop:{sid}"
    if t == "split_mismatch":
        return f"split_mismatch:{a.get('transactions_id')}"
    if t in ("bond_maturity", "bond_coupon", "dividend_payment", "loan_payment"):
        m = _DATE.search(msg)
        who = sid if sid is not None else a.get("accounts_id")
        return f"{t}:{who}:{m.group(1) if m else ''}"
    return f"{t}:{sid}:{msg[:80]}"


def _ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS Alert_Dismissals (
                Alert_Key    TEXT PRIMARY KEY,
                Alert_Type   TEXT NOT NULL,
                Dismissed_At TIMESTAMP DEFAULT NOW()
            )
        """)
    conn.commit()


def dismiss_keys(items: list[tuple[str, str]]) -> None:
    """Dismiss (key, type) pairs in Alert_Dismissals."""
    if not items:
        return
    conn = get_connection()
    try:
        _ensure_table(conn)
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO Alert_Dismissals (Alert_Key, Alert_Type, Dismissed_At) VALUES (%s, %s, NOW())
                ON CONFLICT (Alert_Key) DO UPDATE SET Dismissed_At = NOW()
            """, items)
        conn.commit()
    finally:
        conn.close()


def finalize(results: list[dict], sources_ok: set[str]) -> list[dict]:
    """Add group and key to every alert, drop the key-dismissed ones, and forget
    dismissals whose alert has cleared (only for sources that ran cleanly)."""
    for a in results:
        a["group"] = a.get("group") or group_of(a.get("type"))
        a["key"] = key_of(a)
    try:
        dismissed = _sync_dismissals(results, sources_ok)
    except Exception as e:  # a dismissal-table problem must never hide the alerts themselves
        log.warning("Alert dismissals unavailable: %s", e)
        return results
    return [a for a in results if not (is_keyed(a.get("type")) and a["key"] in dismissed)]


def _sync_dismissals(results: list[dict], sources_ok: set[str]) -> dict:
    conn = get_connection()
    try:
        _ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT Alert_Key, Alert_Type FROM Alert_Dismissals")
            dismissed = {k: t for k, t in cur.fetchall()}
            active = {a["key"] for a in results if is_keyed(a.get("type"))}
            stale = [k for k, t in dismissed.items() if k not in active and source_of(t) in sources_ok]
            if stale:
                cur.execute("DELETE FROM Alert_Dismissals WHERE Alert_Key = ANY(%s)", (stale,))
        conn.commit()
    finally:
        conn.close()
    return dismissed


def dismiss(alerts: list[dict]) -> int:
    """Dismiss these alerts, each by its own type's mechanism. Returns how many."""
    from database.queries import (acknowledge_signal_notification, acknowledge_split_notification,
                                  acknowledge_fundamentals_notification, dismiss_live_alert)
    keyed = []
    n = 0
    for a in alerts:
        t, sid = a.get("type"), a.get("securities_id")
        if t == "signal_change" and sid is not None:
            acknowledge_signal_notification(int(sid))
        elif t == "zone_change" and sid is not None:
            acknowledge_fundamentals_notification(int(sid))
        elif t == "stock_split" and a.get("corporate_actions_id") is not None:
            acknowledge_split_notification(int(a["corporate_actions_id"]))
        elif t in ("ma_cross", "trailing_stop") and sid is not None:
            dismiss_live_alert(int(sid), t)
        elif is_keyed(t):
            keyed.append((key_of(a), t))
        else:
            continue
        n += 1
    dismiss_keys(keyed)
    return n


MAX_TELEGRAM_ITEMS = 15


def _plain(text: str) -> str:
    """Telegram messages are plain text — drop the **bold** / _italic_ markers."""
    return re.sub(r"(^|\s)_(.+?)_(?=\s|$)", r"\1\2", text.replace("**", ""))


def send_new_alert_telegrams() -> int:
    """Send the Dashboard alerts of the groups switched on under Tools → App Settings →
    Telegram Alerts (`telegramAlertGroups`) that haven't been sent yet — one message per
    run listing them, each alert once (Alert_Telegram_Sent). An alert sent, then cleared
    (or dismissed) and later active again, is sent again. Returns how many were sent."""
    from database import notify
    from database.queries import check_triggered_alerts
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT Pref_Value FROM User_Preferences WHERE Pref_Key = 'app-settings'")
            row = cur.fetchone()
    finally:
        conn.close()
    settings = (row[0] or {}) if row else {}
    groups = {g for g, on in (settings.get("telegramAlertGroups") or {}).items() if on}
    if not groups or settings.get("telegramAlerts") is False or not notify.telegram_configured():
        return 0

    alerts = check_triggered_alerts()
    active = {a["key"] for a in alerts}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS Alert_Telegram_Sent (
                               Alert_Key TEXT PRIMARY KEY, Sent_At TIMESTAMP DEFAULT NOW())""")
            cur.execute("SELECT Alert_Key FROM Alert_Telegram_Sent")
            sent = {r[0] for r in cur.fetchall()}
            gone = [k for k in sent if k not in active]  # cleared: may be sent again if it recurs
            if gone:
                cur.execute("DELETE FROM Alert_Telegram_Sent WHERE Alert_Key = ANY(%s)", (gone,))
        conn.commit()
    finally:
        conn.close()

    rank = {"error": 0, "warning": 1, "info": 2}
    new = sorted((a for a in alerts if a.get("group") in groups and a["key"] not in sent),
                 key=lambda a: rank.get(a.get("level"), 3))
    if not new:
        return 0
    lines = [f"Oikos — {len(new)} new alert{'s' if len(new) != 1 else ''}"]
    for g, label in GROUPS:
        items = [a for a in new if a.get("group") == g]
        if not items:
            continue
        lines.append(f"\n{label}:")
        lines += [f"• {_plain(a['message'])}" for a in items[:MAX_TELEGRAM_ITEMS]]
        if len(items) > MAX_TELEGRAM_ITEMS:
            lines.append(f"• …and {len(items) - MAX_TELEGRAM_ITEMS} more on the Dashboard")
    if not notify.notify("\n".join(lines)):
        return 0
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO Alert_Telegram_Sent (Alert_Key) VALUES (%s) ON CONFLICT DO NOTHING",
                            [(a["key"],) for a in new])
        conn.commit()
    finally:
        conn.close()
    return len(new)
