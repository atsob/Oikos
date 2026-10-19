"""Support / Resistance alerts for the Dashboard (group "sr").

Scope: current holdings, the Watchlist, and the currencies you are exposed to
(non-EUR cash in active non-Brokerage/Margin accounts, plus securities quoted in them),
each worth at least the App Settings minimum (`srAlertMinValue`, €500 by default). Not
every security in the database: those are reference data, and Market Watch covers the
broad market.

Levels come from database/technical.py — the same calculation the price charts draw —
over a fixed one-year window of daily closes (not whatever period a chart shows), and
only meaningful ones count: at least two touches, or the period's own high or low.

Events, on daily closes:
  * support broken / resistance broken — two consecutive closes beyond a level (by
    more than half its zone) within the last 10 trading days, with the latest close
    still beyond it. Levels for this are taken from the year before those 10 days, so
    a level the price has just broken is still the level it was. The price chart, which
    draws the levels as they are today, shows that same price on the other side (a
    broken support as Resistance), so the alert says so, and names the next level.
  * near support / near resistance — the latest close within max(0.75%, a quarter of
    the reversal threshold) of the nearest level on that side, and not through it.

Wording follows the scope: a holding gets risk / profit context from its unrealized
P&L (a broken support while at a loss is an error; near resistance while 30% or more
up suggests taking profit), a Watchlist security entry wording (near support — a
possible entry; resistance broken — a breakout), a currency the euro value of your
exposure to it. Each alert carries a stable `key` naming the level, so dismissing one
silences that event at that level only.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from database import technical
from database.connection import get_connection

log = logging.getLogger(__name__)

WINDOW_DAYS = 365
BREAK_BARS = 10
PROFIT_TAKE_PCT = 30.0


def _settings() -> dict:
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT Pref_Value FROM User_Preferences WHERE Pref_Key = 'app-settings'")
            row = cur.fetchone()
        conn.close()
        return (row[0] or {}) if row else {}
    except Exception:
        return {}


def _meaningful(levels: list[dict]) -> list[dict]:
    return [l for l in levels if l["touches"] >= 2 or l.get("extreme")]


def _fmt(v: float) -> str:
    return f"{v:,.4f}"


def events(points: list[dict]) -> list[dict]:
    """Support / resistance events of one series (see the module docstring):
    [{event, level, touches, extreme, close, distance_pct}]."""
    if len(points) < technical.MIN_POINTS + BREAK_BARS:
        return []
    closes = [float(p["close"]) for p in points]
    c0 = closes[-1]
    out: list[dict] = []
    broke = set()

    prior = technical.analyse(points[:-BREAK_BARS])
    if prior:
        half = prior["zone"] / 2
        ref = closes[-BREAK_BARS - 1]
        recent = closes[-BREAK_BARS:]
        sup = [l for l in _meaningful(prior["levels"]) if l["price"] < ref]
        res = [l for l in _meaningful(prior["levels"]) if l["price"] > ref]

        def two_beyond(test) -> bool:
            return any(test(recent[i]) and test(recent[i + 1]) for i in range(len(recent) - 1))

        # The nearest broken level on each side (the highest broken support, lowest broken resistance).
        for l in sorted(sup, key=lambda l: -l["price"]):
            below = lambda c, p=l["price"]: c < p - half
            if two_beyond(below) and below(c0):
                out.append({"event": "support_break", **l}); broke.add("support"); break
        for l in sorted(res, key=lambda l: l["price"]):
            above = lambda c, p=l["price"]: c > p + half
            if two_beyond(above) and above(c0):
                out.append({"event": "resistance_break", **l}); broke.add("resistance"); break

    full = technical.analyse(points)
    if full:
        near = max(0.0075, full["threshold"] / 4)
        lv = _meaningful(full["levels"])
        sup = sorted((l for l in lv if l["price"] < c0), key=lambda l: -l["price"])
        res = sorted((l for l in lv if l["price"] > c0), key=lambda l: l["price"])
        if sup and "support" not in broke and (c0 - sup[0]["price"]) / c0 <= near:
            out.append({"event": "support_near", **sup[0]})
        if res and "resistance" not in broke and (res[0]["price"] - c0) / c0 <= near:
            out.append({"event": "resistance_near", **res[0]})
        for e in out:      # after a break, the next level the chart shows beyond the price (S1 / R1 there)
            if e["event"] == "support_break" and sup:
                e["next_level"] = sup[0]
            elif e["event"] == "resistance_break" and res:
                e["next_level"] = res[0]
    for e in out:
        e["close"] = c0
        e["distance_pct"] = (e["price"] / c0 - 1) * 100
    return out


def _level_text(e: dict) -> str:
    what = (f"{e['touches']} touches" if e["touches"] > 1 else "") if not e.get("extreme") else \
        ("1-year high" if e["extreme"] == "high" else "1-year low") + (f", {e['touches']} touches" if e["touches"] > 1 else "")
    return f"**{_fmt(e['price'])}**" + (f" ({what})" if what else "")


def _beyond(e: dict) -> str:
    """How far the last close is past a broken level, as a % of the level."""
    return f"{abs(e['close'] / e['price'] - 1) * 100:.1f}"


def _after_break(e: dict) -> str:
    """What a broken level has become — a broken support is resistance now (price below it), a broken resistance support — and the next level
    the chart shows, so the alert and the chart's own Support / Resistance lines read the same."""
    nxt = e.get("next_level")       # only a level that has held before counts (2+ touches, or the year's high / low), as for the break itself
    if e["event"] == "support_break":
        return " That level is now resistance" + (f"; the next support that has held is {_level_text(nxt)}." if nxt else ".")
    return " That level is now support" + (f"; the next resistance that has held is {_level_text(nxt)}." if nxt else ".")


def _pnl_clause(pnl: float | None, cost: float | None) -> str:
    if pnl is None or not cost or cost <= 0:
        return ""
    pct = pnl / cost * 100
    return f" You're {'📈 up' if pnl >= 0 else '📉 down'} {abs(pct):.1f}% (€{abs(pnl):,.2f}) vs cost."


def _holding_alert(name: str, e: dict, pnl: float | None, cost: float | None) -> tuple[str, str]:
    ev, lvl, close = e["event"], _level_text(e), _fmt(e["close"])
    pct = (pnl / cost * 100) if pnl is not None and cost else None
    clause = _pnl_clause(pnl, cost)
    if ev == "support_break":
        level = "error" if pnl is not None and pnl < 0 else "warning"
        return level, (f"🔻 **Support broken** — {name} closed below support {lvl} on two days running; now {close}, "
                       f"{_beyond(e)}% below it.{_after_break(e)}{clause} Worth reviewing the position — your stop or thesis.")
    if ev == "support_near":
        level = "warning" if pnl is not None and pnl < 0 else "info"
        return level, (f"🟢 **Near support** — {name} at {close}, {abs(e['distance_pct']):.1f}% above support {lvl}.{clause}")
    if ev == "resistance_near":
        if pct is not None and pct >= PROFIT_TAKE_PCT:
            return "warning", (f"🔴 **Near resistance** — {name} at {close}, {abs(e['distance_pct']):.1f}% below resistance {lvl}."
                               f"{clause} Consider taking profit or rebalancing.")
        return "info", f"🔴 **Near resistance** — {name} at {close}, {abs(e['distance_pct']):.1f}% below resistance {lvl}.{clause}"
    return "info", (f"🚀 **Resistance broken** — {name} closed above resistance {lvl} on two days running; now {close}, "
                    f"{_beyond(e)}% above it.{_after_break(e)}{clause}")


def _watch_alert(name: str, e: dict) -> tuple[str, str]:
    ev, lvl, close = e["event"], _level_text(e), _fmt(e["close"])
    if ev == "support_near":
        return "info", f"👀 **Near support** (Watchlist) — {name} at {close}, {abs(e['distance_pct']):.1f}% above support {lvl}: a possible entry."
    if ev == "resistance_break":
        return "info", f"🚀 **Breakout** (Watchlist) — {name} closed above resistance {lvl} on two days running; now {close}.{_after_break(e)}"
    if ev == "support_break":
        return "info", f"🔻 **Support broken** (Watchlist) — {name} closed below support {lvl}; now {close}.{_after_break(e)} It may have further to fall."
    return "info", f"🔴 **Near resistance** (Watchlist) — {name} at {close}, {abs(e['distance_pct']):.1f}% below resistance {lvl}."


def _currency_alert(code: str, e: dict, exposure_eur: float) -> tuple[str, str]:
    ev, lvl, close = e["event"], _level_text(e), _fmt(e["close"])
    exp = f" Affects €{exposure_eur:,.0f} of your exposure."
    if ev == "support_break":
        return "warning", f"🔻 **{code} broke support** vs EUR — closed below {lvl} on two days running; now {close}.{_after_break(e)}{exp}"
    if ev == "resistance_break":
        return "info", f"🚀 **{code} broke resistance** vs EUR — closed above {lvl} on two days running; now {close}.{_after_break(e)}{exp}"
    if ev == "support_near":
        return "info", f"🟢 **{code} near support** vs EUR — at {close}, {abs(e['distance_pct']):.1f}% above {lvl}.{exp}"
    return "info", f"🔴 **{code} near resistance** vs EUR — at {close}, {abs(e['distance_pct']):.1f}% below {lvl}.{exp}"


def get_sr_alerts(today: date | None = None) -> list[dict]:
    today = today or date.today()
    start = today - timedelta(days=WINDOW_DAYS)
    min_value = float(_settings().get("srAlertMinValue") or 500)
    conn = get_connection()
    try:
        fx = pd.read_sql("""SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1 AS cid, FX_Rate::float AS rate
                            FROM Historical_FX WHERE Date <= %(t)s ORDER BY Currencies_Id_1, Date DESC""", conn, params={"t": today})
        holdings = pd.read_sql("""
            WITH px AS (SELECT DISTINCT ON (Securities_Id) Securities_Id, Close FROM Historical_Prices
                        WHERE Date <= %(t)s ORDER BY Securities_Id, Date DESC),
                 fx AS (SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate FROM Historical_FX
                        WHERE Date <= %(t)s ORDER BY Currencies_Id_1, Date DESC)
            SELECT s.Securities_Id AS sid, s.Securities_Name AS name, s.Currencies_Id AS cid,
                   SUM(h.Quantity)::float AS qty,
                   SUM(h.Quantity * px.Close * COALESCE(fx.FX_Rate, 1))::float AS value_eur,
                   SUM(h.Quantity * COALESCE(h.Fifo_Avg_Cost_EUR, 0))::float AS cost_eur,
                   SUM(h.Quantity * px.Close)::float AS value_local
            FROM Holdings h JOIN Securities s ON s.Securities_Id = h.Securities_Id
            JOIN px ON px.Securities_Id = h.Securities_Id
            LEFT JOIN fx ON fx.Currencies_Id_1 = s.Currencies_Id
            WHERE h.Quantity > 0
            GROUP BY s.Securities_Id, s.Securities_Name, s.Currencies_Id
        """, conn, params={"t": today})
        watch = pd.read_sql("""SELECT w.Securities_Id AS sid, s.Securities_Name AS name FROM Watchlist w
                               JOIN Securities s ON s.Securities_Id = w.Securities_Id""", conn)
        cash = pd.read_sql("""SELECT Currencies_Id AS cid, SUM(Accounts_Balance)::float AS bal FROM Accounts
                              WHERE Is_Active = TRUE AND Accounts_Type NOT IN ('Brokerage','Margin') GROUP BY Currencies_Id""", conn)
        cur_names = pd.read_sql("SELECT Currencies_Id AS cid, TRIM(Currencies_ShortName) AS code FROM Currencies", conn)
        base = pd.read_sql("""SELECT Currencies_Id_2 AS cid FROM Historical_FX GROUP BY Currencies_Id_2
                              ORDER BY COUNT(*) DESC LIMIT 1""", conn)

        held = holdings[holdings["value_eur"].abs() >= min_value]
        held_ids = set(int(x) for x in held["sid"])
        watch = watch[~watch["sid"].isin(holdings["sid"])]  # held positions get holding wording
        sec_ids = sorted(held_ids | set(int(x) for x in watch["sid"]))

        rate = {int(r.cid): float(r.rate) for r in fx.itertuples()}
        base_id = int(base["cid"].iloc[0]) if not base.empty else None
        exposure: dict[int, float] = {}
        for r in cash.itertuples():
            exposure[int(r.cid)] = exposure.get(int(r.cid), 0.0) + float(r.bal or 0) * rate.get(int(r.cid), 1.0)
        for r in holdings.itertuples():
            exposure[int(r.cid)] = exposure.get(int(r.cid), 0.0) + float(r.value_eur or 0)
        cur_ids = sorted(c for c, v in exposure.items() if c != base_id and c in rate and abs(v) >= min_value)

        prices = pd.read_sql("""SELECT Securities_Id AS sid, Date::text AS date, Close::float AS close, High::float AS high, Low::float AS low
                                FROM Historical_Prices WHERE Securities_Id = ANY(%(ids)s) AND Date > %(s)s AND Date <= %(t)s
                                  AND Close IS NOT NULL ORDER BY Securities_Id, Date""",
                             conn, params={"ids": sec_ids or [-1], "s": start, "t": today})
        rates = pd.read_sql("""SELECT Currencies_Id_1 AS cid, Date::text AS date, FX_Rate::float AS close FROM Historical_FX
                               WHERE Currencies_Id_1 = ANY(%(ids)s) AND Date > %(s)s AND Date <= %(t)s ORDER BY Currencies_Id_1, Date""",
                            conn, params={"ids": cur_ids or [-1], "s": start, "t": today})
    finally:
        conn.close()

    def series(df: pd.DataFrame, key: str, ident: int) -> list[dict]:
        g = df[df[key] == ident]
        pts = g.drop(columns=[key]).to_dict("records")
        for p in pts:
            for k in ("high", "low"):
                if k in p and p[k] is not None and pd.isna(p[k]):
                    p[k] = None
        return pts

    results: list[dict] = []

    def add(level: str, message: str, e: dict, scope: str, **ids):
        ident = ids.get("securities_id") or ids.get("currencies_id")
        results.append({
            "level": level, "message": message, "type": f"sr_{e['event']}", "group": "sr", "scope": scope,
            "key": f"sr:{scope}:{ident}:{e['event']}:{e['price']:.6g}", **ids,
        })

    for r in held.itertuples():
        try:
            pnl = (float(r.value_eur) - float(r.cost_eur)) if r.cost_eur and r.cost_eur > 0 else None
            for e in events(series(prices, "sid", int(r.sid))):
                level, msg = _holding_alert(r.name, e, pnl, float(r.cost_eur) if r.cost_eur else None)
                add(level, msg, e, "holding", securities_id=int(r.sid))
        except Exception as ex:  # one bad series never hides the others
            log.warning("S/R alerts for security %s failed: %s", r.sid, ex)
    for r in watch.itertuples():
        try:
            for e in events(series(prices, "sid", int(r.sid))):
                level, msg = _watch_alert(r.name, e)
                add(level, msg, e, "watchlist", securities_id=int(r.sid))
        except Exception as ex:
            log.warning("S/R alerts for watchlist security %s failed: %s", r.sid, ex)
    codes = {int(r.cid): r.code for r in cur_names.itertuples()}
    for cid in cur_ids:
        try:
            for e in events(series(rates, "cid", cid)):
                level, msg = _currency_alert(codes.get(cid, str(cid)), e, exposure[cid])
                add(level, msg, e, "currency", currencies_id=cid)
        except Exception as ex:
            log.warning("S/R alerts for currency %s failed: %s", cid, ex)
    return results
