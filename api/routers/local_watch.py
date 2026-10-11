"""Local Market Watch — the health check of the market where the user lives (Market Data -> Local Market Watch, and a Dashboard tile).

The country is a setting (Tools -> App Settings -> Country of usage); database/local_markets.py says what the local market is for
each country. The method is Market Watch's (api/routers/market_watch.py): a status per indicator — good / warn / bad — a trend, 3
trading days in a row to turn red, a margin to clear, a banner that is red only when two or more are red, and the day's readings
logged.

  1. Trend      the benchmark index: distance from its 52-week high and from its 200-day average
  2. Breadth    the share of ~20 large caps above their 200- and 50-day averages (Yahoo, computed here)
  3. Banks      an equal-weight basket of the systemic banks
  4. Sovereign  the country's 10-year government yield minus Germany's (FRED, monthly, ~6-week lag; needs FRED_API_KEY)

Context only (not in the banner): the country against Europe, the index's turnover, and how much of the user's portfolio is in the country.
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pandas as pd
from fastapi import APIRouter

from api.routers.market_watch import PERSIST_DAYS, _chg, _decide, _fred, _pts, _rel, _series, _upto
from config.settings import ENV_CONFIG
from database.local_markets import get_profile, home_country, supported
from database.queries import get_app_setting, save_app_setting

router = APIRouter()
log = logging.getLogger(__name__)

CACHE_KEY, CACHE_HOURS = "local_watch_cache", 6

# Starting thresholds — to be tuned by the user, and by the daily log in a few months. Same idea as Market Watch's RULES.
RULES_LOCAL = {
    "logic": 1, "persist_days": PERSIST_DAYS,
    "trend": {"red_drawdown": 15.0, "amber_drawdown": 8.0, "clear_drawdown": 10.0, "worse_pts_20d": 1.5},
    "breadth": {"near_high_pct": 5, "red_200d": 40, "red_50d": 25, "red_50d_needs_200d_under": 50, "amber_200d": 50, "amber_50d": 30,
                "clear_200d": 45, "clear_50d": 30, "good_200d": 50},
    "banks": {"red_drawdown": 20.0, "amber_drawdown": 10.0, "clear_drawdown": 12.0},
    "sovereign": {"amber_pp": 1.0, "red_pp": 1.8, "red_3m_pp": 0.5},
}


def rule_version() -> str:
    import hashlib
    return hashlib.sha1(json.dumps(RULES_LOCAL, sort_keys=True).encode()).hexdigest()[:8]


def _labels(p: dict) -> dict:
    return {"trend": f"{p['index_name']} trend", "breadth": f"{p['name']} breadth", "banks": f"{p['name']} banks",
            "sovereign": f"{p['name']} sovereign spread", "overall": "Overall"}


# ── 1. Trend ────────────────────────────────────────────────────────────────────

def _trend(p: dict, prev: dict | None = None) -> dict:
    R = RULES_LOCAL["trend"]
    s = _series(p["index"], "3y")
    sma = s.rolling(200).mean()
    dd = lambda x: float((1 - x.iloc[-1] / x.tail(252).max()) * 100)

    def red(k):
        x = _upto(s, k)
        return bool(dd(x) >= R["red_drawdown"] and x.iloc[-1] < x.rolling(200).mean().iloc[-1])
    now, d = float(s.iloc[-1]), dd(s)
    below = bool(now < sma.iloc[-1])
    status, pending = _decide([red(k) for k in range(PERSIST_DAYS)], bool(below or d >= R["amber_drawdown"]), (prev or {}).get("status") == "bad",
                              not (d < R["clear_drawdown"] and not below))
    d20 = dd(_upto(s, 20))
    trend = "worsening" if d - d20 >= R["worse_pts_20d"] else ("improving" if d20 - d >= R["worse_pts_20d"] else "flat")
    try:
        rel3 = (_rel(s, 63) or 0) - (_rel(_series("^STOXX", "1y"), 63) or 0)
    except Exception:
        rel3 = None
    return {"status": status, "red_pending": pending, "trend": trend, "level": round(now, 0), "below_high_pct": round(d, 1),
            "vs_200d_pct": round((now / float(sma.iloc[-1]) - 1) * 100, 1), "ret_3m_pct": round((_rel(s, 63) or 0) * 100, 1),
            "vs_europe_3m_pct": round(rel3 * 100, 1) if rel3 is not None else None,
            "series": {p["index_name"]: _pts(s.tail(520), 520), "200-day average": _pts(sma.dropna().tail(520), 520)}}


# ── 2. Breadth ──────────────────────────────────────────────────────────────────

def _panel(symbols: list[str], period: str = "2y") -> pd.DataFrame:
    cols = {}
    for t in dict.fromkeys(symbols):
        try:
            cols[t] = _series(t, period)
        except Exception as e:
            log.warning("Local Watch: %s failed: %s", t, e)
    return pd.DataFrame(cols).ffill()


def _breadth(p: dict, prev: dict | None = None) -> dict:
    B = RULES_LOCAL["breadth"]
    px = _panel(p["caps"])
    idx = _series(p["index"], "2y")
    n = int(px.shape[1])
    sma200, sma50 = px.rolling(200).mean(), px.rolling(50).mean()
    p200 = ((px > sma200).astype(float).where(sma200.notna()).mean(axis=1) * 100).dropna()
    p50 = ((px > sma50).astype(float).where(sma50.notna()).mean(axis=1) * 100).dropna()
    now200, now50 = float(p200.iloc[-1]), float(p50.iloc[-1])
    d20 = _chg(p200, 20)
    near = lambda k: float((1 - _upto(idx, k).iloc[-1] / _upto(idx, k).tail(252).max()) * 100) <= B["near_high_pct"]

    def red(k):
        a, b = float(_upto(p200, k).iloc[-1]), float(_upto(p50, k).iloc[-1])
        return bool(near(k) and (a < B["red_200d"] or (b < B["red_50d"] and a < B["red_50d_needs_200d_under"])))
    amber = bool(now200 < B["amber_200d"] or now50 < B["amber_50d"])
    stay = not (now200 > B["clear_200d"] and now50 > B["clear_50d"])
    status, pending = _decide([red(k) for k in range(PERSIST_DAYS)], amber, (prev or {}).get("status") == "bad", stay)
    if status == "warn" and not pending and now200 >= B["good_200d"] and now50 >= B["amber_50d"]:
        status = "good"
    trend = "flat" if d20 is None else ("improving" if d20 >= 3 else ("worsening" if d20 <= -3 else "flat"))
    return {"status": status, "red_pending": pending, "trend": trend, "stocks": n, "pct_above_200d": round(now200, 1), "pct_above_50d": round(now50, 1),
            "pct_above_200d_chg_20d": round(d20, 1) if d20 is not None else None, "index_near_high": bool(near(0)),
            "series": {f"{n} large caps above 200-day average (%)": _pts(p200), "…above 50-day average (%)": _pts(p50)}}


# ── 3. Banks ────────────────────────────────────────────────────────────────────

def _banks(p: dict, prev: dict | None = None) -> dict:
    R = RULES_LOCAL["banks"]
    px = _panel(list(p["banks"]), "3y")
    basket = ((px / px.iloc[-260:].iloc[0]).mean(axis=1) * 100).dropna()          # equal weight, rebased to 100 a year ago
    sma = basket.rolling(200).mean()
    dd = lambda x: float((1 - x.iloc[-1] / x.tail(252).max()) * 100)

    def red(k):
        x = _upto(basket, k)
        return bool(dd(x) >= R["red_drawdown"] and x.iloc[-1] < x.rolling(200).mean().iloc[-1])
    d, below = dd(basket), bool(basket.iloc[-1] < sma.iloc[-1])
    status, pending = _decide([red(k) for k in range(PERSIST_DAYS)], bool(below or d >= R["amber_drawdown"]), (prev or {}).get("status") == "bad",
                              not (d < R["clear_drawdown"] and not below))
    d20 = dd(_upto(basket, 20))
    trend = "worsening" if d - d20 >= 1.5 else ("improving" if d20 - d >= 1.5 else "flat")
    rel = (_rel(basket, 63) or 0) - (_rel(_series(p["index"], "1y"), 63) or 0)
    banks = [{"name": n, "ticker": t, "ret_3m_pct": round((_rel(px[t].dropna(), 63) or 0) * 100, 1), "below_high_pct": round(dd(px[t].dropna()), 1)}
             for t, n in p["banks"].items() if t in px]
    return {"status": status, "red_pending": pending, "trend": trend, "below_high_pct": round(d, 1), "below_200d": below,
            "ret_1m_pct": round((_rel(basket, 21) or 0) * 100, 1), "ret_3m_pct": round((_rel(basket, 63) or 0) * 100, 1), "vs_market_3m_pct": round(rel * 100, 1),
            "banks": banks, "series": {"Bank basket (rebased 100)": _pts(basket.tail(520), 520), "200-day average": _pts(sma.dropna().tail(520), 520)}}


# ── 4. Sovereign spread (monthly) ───────────────────────────────────────────────

def _sovereign(p: dict, prev: dict | None = None) -> dict:
    R = RULES_LOCAL["sovereign"]
    if not p.get("euro", True):
        return {"status": "na", "note": f"The spread to Germany is a euro-area measure; {p['name']}'s yield sits structurally above the Bund, so there is no spread leg here."}
    if not p.get("fred"):
        return {"status": "na", "note": f"{p['name']}'s yield is the euro-area benchmark the other countries are measured against — no spread to show."}
    key = ENV_CONFIG.get("fred_api_key", "")
    if not key:
        return {"status": "na", "note": "Needs a free FRED_API_KEY (see Help → Market Watch)."}
    peer = "IT" if p["fred"] != "IT" else "ES"
    code = lambda c: f"IRLTLT01{c}M156N"
    home, de, pr = _fred(code(p["fred"]), key), _fred(code("DE"), key), _fred(code(peer), key)
    sp, spp = (home - de).dropna(), (home - pr).dropna()
    now = float(sp.iloc[-1])
    d3 = float(sp.iloc[-1] - sp.iloc[-4]) if len(sp) > 3 else None
    status = "bad" if now >= R["red_pp"] or (d3 is not None and d3 >= R["red_3m_pp"]) else ("warn" if now >= R["amber_pp"] else "good")
    trend = "flat" if d3 is None else ("worsening" if d3 > 0.1 else ("improving" if d3 < -0.1 else "flat"))
    peer_name = get_profile(peer)["name"]
    return {"status": status, "trend": trend, "spread_pp": round(now, 2), "chg_3m": round(d3, 2) if d3 is not None else None,
            "home_yield": round(float(home.iloc[-1]), 2), "vs_peer_pp": round(float(spp.iloc[-1]), 2), "peer": peer_name, "as_of": sp.index[-1].strftime("%Y-%m"),
            "series": {f"{p['name']} − Germany, 10-year (pp)": _pts(sp), f"{p['name']} − {peer_name} (pp)": _pts(spp)}}


# ── Context ─────────────────────────────────────────────────────────────────────

def _liquidity(p: dict) -> dict:
    """The index's traded volume: 20 days against 120 days. A market that dries up is one that is hard to leave."""
    import yfinance as yf
    h = yf.Ticker(p["index"]).history(period="1y")["Volume"].dropna()
    h = h[h > 0]
    if len(h) < 130:
        return {"status": "na", "note": "This index has no volume data on Yahoo."}
    r = float(h.tail(20).mean() / h.tail(120).mean())
    return {"status": "bad" if r < 0.6 else ("warn" if r < 0.8 else "good"), "ratio_20_120": round(r, 2), "avg_20d_m": round(float(h.tail(20).mean()) / 1e6, 1),
            "series": {"Volume, 20-day average (m shares)": _pts((h.rolling(20).mean() / 1e6).dropna(), 250)}}


def _exposure(cc: str, name: str) -> dict:
    """How much of the user's portfolio is in this country, from the Country Exposure report (stocks, bonds, cash, funds' look-through)."""
    from api.routers.country_exposure import get_country_exposure
    d = get_country_exposure(None)
    row = next((c for c in d["countries"] if c["code"] == cc), None)
    if not row:
        return {"status": "na", "note": f"No exposure to {name} found."}
    return {"status": "na", "value_eur": row["value_eur"], "pct": row["pct"], "total_eur": d["total_eur"],
            "categories": [{"category": k["category"], "value_eur": k["value_eur"], "pct": k["pct"]} for k in row["categories"]]}


# ── Assemble, cache, serve ──────────────────────────────────────────────────────

def _prev_snapshot() -> dict:
    try:
        return json.loads(get_app_setting(CACHE_KEY) or "{}")
    except Exception:
        return {}


def _no_country() -> dict | None:
    """A reply for the cases where there is nothing to compute: no country chosen, or one without a profile (yet)."""
    cc = home_country()
    if not cc:
        return {"country": None, "supported": supported()}
    if not get_profile(cc):
        return {"country": cc, "unsupported": True, "supported": supported()}
    return None


def compute(prev: dict | None = None) -> dict:
    cc = home_country()
    p = get_profile(cc)
    prev = prev if (prev is not None and prev.get("country") == cc) else {}
    jobs = {"trend": lambda: _trend(p, prev.get("trend")), "breadth": lambda: _breadth(p, prev.get("breadth")), "banks": lambda: _banks(p, prev.get("banks")),
            "sovereign": lambda: _sovereign(p, prev.get("sovereign")), "liquidity": lambda: _liquidity(p), "exposure": lambda: _exposure(cc, p["name"])}
    out: dict = {}

    def run(k):
        try:
            return k, jobs[k]()
        except Exception as e:
            log.warning("Local Watch (%s): %s failed: %s", cc, k, e)
            return k, {"status": "na", "note": f"Could not be fetched: {e}"[:200]}

    with ThreadPoolExecutor(max_workers=4) as pool:
        for k, v in pool.map(run, jobs):
            out[k] = v
    live = [out[k] for k in ("trend", "breadth", "banks", "sovereign") if out[k].get("status") != "na"]
    bad = sum(v["status"] == "bad" for v in live)
    warn = sum(v["status"] == "warn" for v in live)
    imp = sum(v.get("trend") == "improving" for v in live)
    wor = sum(v.get("trend") == "worsening" for v in live)
    n = p["name"]
    if not live:
        verdict, level = "No data could be fetched.", "na"
    elif bad >= 2:
        verdict, level = f"Two or more {n} indicators are in Warning — the local crash rules are on.", "bad"
    elif len(live) >= 3 and wor == len(live):
        verdict = (f"All the {n} indicators are worsening together — none has crossed its red line yet." if bad == 0
                   else f"All the {n} indicators are worsening together and one is already in Warning.")
        level = "warn"
    elif bad == 1 or warn >= 2 or wor >= 2:
        verdict, level = f"Mixed: some {n} indicators are flashing a warning — worth watching.", "warn"
    elif imp >= 2 and wor == 0:
        verdict, level = f"The {n} market is healthy and improving.", "good"
    else:
        verdict, level = f"Broadly healthy, no {n} indicator is flashing a warning.", "good"
    out["overall"] = {"level": level, "verdict": verdict, "bad": bad, "warn": warn, "improving": imp, "worsening": wor}
    out["country"] = cc
    out["profile"] = {"code": cc, "name": n, "index_name": p["index_name"], "bank_note": p.get("bank_note", ""), "banks": list(p["banks"].values())}
    out["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out["rule_version"] = rule_version()
    return out


_NAMES = {"good": "healthy", "warn": "on watch", "bad": "in warning", "na": "no data"}


def _record(prev: dict, data: dict) -> None:
    """The day's readings to Local_Watch_Log (one row per day and country), and a change of status (once) to Market_Watch_Events as 'local_*'."""
    from database.connection import get_db
    cc, labels = data["country"], _labels(get_profile(data["country"]))
    levels = {k: data[k].get("status") for k in ("trend", "breadth", "banks", "sovereign")}
    levels["overall"] = data["overall"]["level"]
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""INSERT INTO Local_Watch_Log (Log_Date, Country, Overall, Trend, Breadth, Banks, Sovereign, Readings)
                           VALUES (CURRENT_DATE, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (Log_Date, Country) DO UPDATE SET Overall = EXCLUDED.Overall, Trend = EXCLUDED.Trend, Breadth = EXCLUDED.Breadth,
                               Banks = EXCLUDED.Banks, Sovereign = EXCLUDED.Sovereign, Readings = EXCLUDED.Readings, Created_At = NOW()""",
                        (cc, levels["overall"], levels["trend"], levels["breadth"], levels["banks"], levels["sovereign"],
                         json.dumps({k: {x: y for x, y in data[k].items() if x not in ("series", "banks", "categories")} for k in ("trend", "breadth", "banks", "sovereign", "liquidity", "exposure")})))
            if prev.get("generated_at") and prev.get("country") == cc:
                before = {k: prev.get(k, {}).get("status") for k in ("trend", "breadth", "banks", "sovereign")}
                before["overall"] = (prev.get("overall") or {}).get("level")
                if before["overall"] == "bad" and sum(v == "bad" for k, v in before.items() if k != "overall") < 2:
                    before["overall"] = "warn"          # a stored red with fewer than two indicators in Warning was not really red
                for k, to in levels.items():
                    frm = before.get(k)
                    if frm and to and frm != to and to != "na" and frm != "na":
                        cur.execute("INSERT INTO Market_Watch_Events (Indicator, From_Status, To_Status, Message) VALUES (%s, %s, %s, %s)",
                                    (f"local_{k}", frm, to, f"{labels[k]}: {_NAMES.get(frm, frm)} → {_NAMES.get(to, to)}"))
    except Exception as e:
        log.warning("Local Watch: could not record the log/events: %s", e)


def refresh_cache() -> dict:
    if (empty := _no_country()) is not None:
        return empty
    prev = _prev_snapshot()
    data = compute(prev)
    save_app_setting(CACHE_KEY, json.dumps(data))
    _record(prev, data)
    return data


def _cached(max_age_h: float = CACHE_HOURS) -> dict | None:
    raw = get_app_setting(CACHE_KEY)
    if not raw:
        return None
    try:
        d = json.loads(raw)
        if d.get("rule_version") != rule_version() or d.get("country") != home_country():     # other rules, or another country chosen since
            return None
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(d["generated_at"])).total_seconds() / 3600
        return d if age <= max_age_h else None
    except Exception:
        return None


def _playbook_key() -> str:
    return f"local_watch_playbook:{home_country() or 'none'}"


def _default_playbook() -> str:
    n = (get_profile(home_country()) or {}).get("name", "local")
    return (f"When the {n} banner turns RED:\n"
            "1. Don't sell in a panic — decide with the plan, not the day's price.\n"
            f"2. Pause new money into {n} stocks and bonds for 1–2 months; put it in the cash fund.\n"
            f"3. Check what share of the portfolio is in {n} (the tile above) against the limit you have set.\n"
            "4. Resume when the banner has been out of red for 10 trading days.\n"
            "5. Review the plan 3 months after any red episode.\n"
            "(Edit this to your own pre-agreed plan.)")


@router.get("")
def get_local_watch(refresh: bool = False):
    if (empty := _no_country()) is not None:
        return empty
    if not refresh:
        d = _cached()
        if d:
            return d
    return refresh_cache()


@router.get("/summary")
def get_local_watch_summary():
    from database.connection import get_db
    if (empty := _no_country()) is not None:
        return empty
    d = _cached(max_age_h=24 * 7) or refresh_cache()
    recent = 0
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM Market_Watch_Events WHERE Event_At > NOW() - INTERVAL '7 days' AND Indicator LIKE 'local_%' AND Indicator <> 'local_overall'")
            recent = cur.fetchone()[0]
    except Exception:
        pass
    return {"country": d["country"], "profile": d["profile"], "generated_at": d["generated_at"], "overall": d["overall"], "recent_changes": recent,
            "indicators": {k: {"status": d[k].get("status"), "trend": d[k].get("trend")} for k in ("trend", "breadth", "banks", "sovereign")}}


@router.get("/events")
def get_local_watch_events(limit: int = 15):
    from database.connection import get_db
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT Event_At, Indicator, From_Status, To_Status, Message FROM Market_Watch_Events WHERE Indicator LIKE 'local_%%' ORDER BY Event_At DESC LIMIT %s", (limit,))
        return [{"at": r[0].isoformat(timespec="seconds"), "indicator": r[1], "from": r[2], "to": r[3], "message": r[4]} for r in cur.fetchall()]


@router.get("/playbook")
def get_playbook():
    saved = get_app_setting(_playbook_key())
    return {"text": saved or _default_playbook(), "is_default": not saved}


@router.put("/playbook")
def save_playbook(data: dict):
    save_app_setting(_playbook_key(), (data.get("text") or "").strip())
    return {"ok": True}


# ── Back-test: stored runs compared by rule version, run from the UI and by the scheduler ───────────────────────

LOG_READY_DAYS = 180
BACKTEST_ALERT = {"share_red_pts": 2.0, "episode_days": 30}


def _scope() -> str:
    return f"local:{home_country()}"


def _summary(res: dict) -> dict:
    out = {"share_red": {}, "spells": {}, "episodes": {}, "log_days": (res.get("log") or {}).get("days", 0)}
    for k, v in (res.get("rules") or {}).items():
        out["share_red"][k] = v.get("share_red_pct")
        out["spells"][k] = len(v.get("spells") or [])
        out["episodes"][k] = {e["episode"]: e["first_red"] for e in v.get("episodes") or []}
    return out


def _material(prev: dict | None, new: dict, prev_ver: str | None, ver: str) -> str | None:
    """Only a change that matters: a new red spell, a fall caught or missed differently (or its first red date moving), or the share of red
    days moving by more than the set number of points. Otherwise None — the run is stored and its date updated, quietly."""
    if not prev:
        return None
    notes = []
    for k in new["share_red"]:
        d = (new["share_red"].get(k) or 0) - (prev.get("share_red", {}).get(k) or 0)
        if abs(d) > BACKTEST_ALERT["share_red_pts"]:
            notes.append(f"{k}: red on {new['share_red'][k]}% of days ({d:+.1f} points)")
        if new["spells"].get(k, 0) > prev.get("spells", {}).get(k, 0):
            notes.append(f"{k}: a new red spell")
        for ep, first in new["episodes"].get(k, {}).items():
            before = prev.get("episodes", {}).get(k, {}).get(ep)
            if before is None and ep not in prev.get("episodes", {}).get(k, {}):
                continue                                           # a fall that was not in the previous run's list
            if (before is None) != (first is None):
                notes.append(f"{k}: {ep} now {'caught' if first else 'missed'}")
            elif before and first and abs((pd.Timestamp(first) - pd.Timestamp(before)).days) > BACKTEST_ALERT["episode_days"]:
                notes.append(f"{k}: {ep} first red moved {before} → {first}")
    if prev_ver and prev_ver != ver:
        notes.insert(0, f"rule version {prev_ver} → {ver}" + ("" if notes else ": no material difference in the back-test"))
    return "; ".join(notes) or None


def run_and_store(trigger: str = "manual") -> dict:
    from database.connection import get_db
    from data.local_watch_backtest import run_backtest
    res = run_backtest()
    ver, scope = rule_version(), _scope()
    res["rule_version"], res["trigger"] = ver, trigger
    summ = _summary(res)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT Rule_Version, Summary FROM Market_Watch_Backtests WHERE Scope = %s ORDER BY Run_At DESC LIMIT 1", (scope,))
        last = cur.fetchone()
        change = _material(last[1] if last else None, summ, last[0] if last else None, ver)
        cur.execute("INSERT INTO Market_Watch_Backtests (Trigger, Rule_Version, Summary, Result, Material_Change, Scope) VALUES (%s, %s, %s, %s, %s, %s)",
                    (trigger, ver, json.dumps(summ), json.dumps(res), change, scope))
    res["material_change"] = change
    save_app_setting(f"local_watch_backtest:{home_country()}", json.dumps(res))
    return res


def backtest_due(now: datetime | None = None) -> str | None:
    """Why a local back-test should run now, or None: no run yet for this country, a rule changed, the first Sunday of the month (after 03:00),
    or the daily log reaching six months (the first run that can test breadth)."""
    from database.connection import get_db
    if _no_country() is not None:
        return None
    now = now or datetime.now()
    scope, cc = _scope(), home_country()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT Run_At, Rule_Version FROM Market_Watch_Backtests WHERE Scope = %s ORDER BY Run_At DESC LIMIT 1", (scope,))
        last = cur.fetchone()
        if last is None:
            return "first run"
        if last[1] != rule_version():
            return "rule change"
        if now.weekday() == 6 and now.day <= 7 and now.hour >= 3 and last[0].astimezone().replace(tzinfo=None) < now.replace(hour=3, minute=0, second=0, microsecond=0):
            return "monthly"
        cur.execute("SELECT COUNT(*) FROM Local_Watch_Log WHERE Country = %s", (cc,))
        if cur.fetchone()[0] >= LOG_READY_DAYS:
            cur.execute("SELECT 1 FROM Market_Watch_Backtests WHERE Scope = %s AND Trigger = 'log 6 months' LIMIT 1", (scope,))
            if cur.fetchone() is None:
                return "log 6 months"
    return None


@router.get("/backtest")
def get_backtest():
    """The last stored back-test for the current country (or null)."""
    if _no_country() is not None:
        return None
    raw = get_app_setting(f"local_watch_backtest:{home_country()}")
    return json.loads(raw) if raw else None


@router.get("/backtest/history")
def get_backtest_history(limit: int = 20):
    from database.connection import get_db
    if _no_country() is not None:
        return []
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT Run_At, Trigger, Rule_Version, Summary, Material_Change FROM Market_Watch_Backtests WHERE Scope = %s ORDER BY Run_At DESC LIMIT %s", (_scope(), limit))
        return [{"at": r[0].isoformat(timespec="seconds"), "trigger": r[1], "rule_version": r[2], "summary": r[3], "material_change": r[4]} for r in cur.fetchall()]


@router.post("/backtest")
def run_backtest_endpoint():
    """Run the local back-test now (the "Run again" button): a few seconds — Yahoo history of the index and banks, FRED for the spread."""
    from fastapi import HTTPException
    if _no_country() is not None:
        raise HTTPException(400, "Choose a country with a profile under Tools → App Settings → Country of Usage first.")
    try:
        return run_and_store("manual")
    except RuntimeError as e:
        raise HTTPException(400, str(e))


def get_local_watch_insights() -> list:
    """Dashboard insights: the local banner turning red (while it is red) or leaving red (a week). Reads the stored snapshot, never fetches."""
    from database.connection import get_db
    d = _prev_snapshot()
    cc = home_country()
    if not d.get("overall") or not cc or d.get("country") != cc:
        return []
    p = get_profile(cc)
    labels = _labels(p)
    out_bt = []
    try:                                                                  # a back-test whose result changed materially (last 7 days)
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT Material_Change FROM Market_Watch_Backtests WHERE Scope = %s AND Material_Change IS NOT NULL AND Run_At > NOW() - INTERVAL '7 days' ORDER BY Run_At DESC LIMIT 1", (_scope(),))
            row = cur.fetchone()
        if row:
            out_bt = [{"type": "info", "icon": "local_watch", "title": f"Local Market Watch ({p['name']}) back-test changed",
                       "message": f"{row[0][:300]} — see the Back-test card on Market Data → Local Market Watch."}]
    except Exception:
        pass
    reds = [labels[k] for k in ("trend", "breadth", "banks", "sovereign") if d.get(k, {}).get("status") == "bad"]
    if len(reds) >= 2:
        return out_bt + [{"type": "danger", "icon": "local_watch", "title": f"Local Market Watch ({p['name']}) is RED — the local crash rules are on",
                 "message": f"In warning: {', '.join(reds)}. Open Market Data → Local Market Watch for your playbook."}]
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT Event_At FROM Market_Watch_Events WHERE Indicator = 'local_overall' AND From_Status = 'bad' AND Event_At > NOW() - INTERVAL '7 days' ORDER BY Event_At DESC LIMIT 1")
            row = cur.fetchone()
        if row:
            return out_bt + [{"type": "info", "icon": "local_watch", "title": f"Local Market Watch ({p['name']}) is out of red", "message": f"Left red on {row[0].strftime('%d %b')}."}]
    except Exception:
        pass
    return out_bt
