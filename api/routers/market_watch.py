"""Market Watch — the four indicators worth following (Market Data -> Market Watch, and a Dashboard tile).

  1. Free cash flow of the hyperscalers   does the AI capex pay back?        Yahoo quarterly cash flows
  2. Credit spreads                       what does funding cost?            FRED OAS if FRED_API_KEY is set,
                                                                             else HYG/LQD vs Treasuries (price ratio)
  3. 10-year Treasury yield               the cost of capital                Yahoo ^TNX
  4. Market breadth                       how wide is the rally?             TradingView S5TH / S5FI (% of S&P 500
                                                                             stocks above their 200/50-day average)
                                                                             and RSP vs SPY (equal vs cap weight)

Each gets a status — good / warn / bad — from the usual rules of thumb, a trend (improving / worsening / flat) and the
series behind it. "Worsening" means the indicator moved toward its red line over the last 20 trading days. A daily
indicator is red only when its red condition has held for 3 trading days in a row (one bad day is noise), and once red
it only clears when it is back past the line by a margin (hysteresis), so it does not flip on and off. The result is computed from public sources, cached in app_settings for a few hours, and refreshed
by the scheduler's "Market Watch" job, so opening the page is instant.
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pandas as pd
import requests
from fastapi import APIRouter

from config.settings import ENV_CONFIG
from database.queries import get_app_setting, save_app_setting

router = APIRouter()
log = logging.getLogger(__name__)

HYPERSCALERS = {"MSFT": "Microsoft", "AMZN": "Amazon", "GOOGL": "Alphabet", "META": "Meta", "ORCL": "Oracle"}
CACHE_KEY = "market_watch_cache"
CACHE_HOURS = 6


def _series(sym: str, period: str = "2y") -> pd.Series:
    import yfinance as yf
    h = yf.Ticker(sym).history(period=period)
    s = h["Close"].dropna()
    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
    return s


def _pts(s: pd.Series, n: int = 260) -> dict:
    s = s.dropna().tail(n)
    return {"x": [d.strftime("%Y-%m-%d") for d in s.index], "y": [round(float(v), 4) for v in s.values]}


# Every threshold the red/amber rules use, in one place. rule_version() hashes this, so any change to a number here is a new
# "rule version": the back-test re-runs on its own when it changes (data/market_watch_backtest.py) and its stored runs can be compared.
# "logic" is bumped by hand when the way the numbers are combined changes. The back-test never edits this — it only reports.
RULES = {
    "logic": 4,
    "persist_days": 3,
    "fcf": {"capex_share_amber": 0.90, "yoy_amber": -0.10, "negative_quarters_red": 2},
    # 10-year Treasury: what hurts shares is how fast yields rise, not their level (5.5% was normal in the 1990s)
    "ten_year": {"speed_pp_63d": 0.75, "level": 5.5, "level_speed_pp": 0.50, "clear_speed_pp": 0.50, "amber_level": 5.0, "amber_speed_pp": 0.40},
    "credit": {"red_hy_20d_pp": 0.75, "red_hy_level": 4.5, "red_hy_60d_pp": 1.0, "red_ig_level": 1.3,
               "amber_hy_20d_pp": 0.35, "amber_hy_level": 4.0, "amber_hy_60d_pp": 0.6, "amber_ig_level": 1.1,
               "clear_hy_level": 4.0, "clear_ig_level": 1.2,
               "proxy_red_20d": -0.03, "proxy_amber_20d": -0.015, "proxy_below_200d": 0.98,
               "surrogate_20d_pp": 0.50, "surrogate_60d_pp": 1.00, "surrogate_clear_20d_pp": 0.25},      # Moody's Baa − 10-year, back-test only
    "breadth": {"near_high_pct": 5, "red_200d": 40, "red_50d": 25, "red_50d_needs_200d_under": 50, "amber_200d": 50, "amber_50d": 30,
                "amber_rsp_spy_3m": -0.02, "clear_200d": 45, "clear_50d": 30, "good_200d": 50, "good_rising_pts": 5, "good_rising_200d": 45},
    "france": {"amber_pp": 0.9, "red_pp": 1.3, "red_3m_pp": 0.4},
    "erp": {"green_pp": 2.0, "red_pp": 0.0},
    "backtest_alert": {"share_red_pts": 2.0, "episode_days": 30},
}
PERSIST_DAYS = RULES["persist_days"]
_TY = RULES["ten_year"]
TEN_YEAR_SPEED, TEN_YEAR_LEVEL, TEN_YEAR_LEVEL_SPEED, TEN_YEAR_CLEAR_SPEED = _TY["speed_pp_63d"], _TY["level"], _TY["level_speed_pp"], _TY["clear_speed_pp"]


def rule_version() -> str:
    import hashlib
    return hashlib.sha1(json.dumps(RULES, sort_keys=True).encode()).hexdigest()[:8]


def _decide(red_days: list[bool], amber: bool, prev_bad: bool, stay_red: bool, prev_warn: bool = False, stay_warn: bool = True) -> tuple[str, bool]:
    """-> (status, red_pending). red_days = the red condition on each of the last PERSIST_DAYS days, newest first.
    prev_warn / stay_warn: the same hysteresis for the amber line — a reading sitting right on it (a bank basket 10.2% below its high against a
    10% line) would otherwise flip between Healthy and On watch with every price tick."""
    if all(red_days):
        return "bad", False
    if prev_bad and stay_red:                          # hysteresis: not clear until well back past the line
        return "bad", False
    if red_days[0] or amber:
        return "warn", bool(red_days[0])               # red today but not yet confirmed by the 3-day rule
    if prev_warn and stay_warn:                        # still within the margin around the amber line: stay on watch
        return "warn", False
    return "good", False


def _upto(s: pd.Series, k: int) -> pd.Series:
    """The series as it stood k trading days ago."""
    return s.iloc[: len(s) - k] if k else s


def _chg(s: pd.Series, n: int) -> float | None:
    return float(s.iloc[-1] - s.iloc[-1 - n]) if len(s) > n else None


def _rel(s: pd.Series, n: int) -> float | None:
    return float(s.iloc[-1] / s.iloc[-1 - n] - 1) if len(s) > n else None


# ── 1. Free cash flow of the hyperscalers ───────────────────────────────────────

def _fcf(prev: dict | None = None) -> dict:
    import yfinance as yf
    comp = []
    for t, name in HYPERSCALERS.items():
        try:
            cf = yf.Ticker(t).quarterly_cashflow
            if cf is None or cf.empty:
                continue
            ocf = cf.loc["Operating Cash Flow"] if "Operating Cash Flow" in cf.index else None
            capex = cf.loc["Capital Expenditure"] if "Capital Expenditure" in cf.index else None
            fcf = cf.loc["Free Cash Flow"] if "Free Cash Flow" in cf.index else (ocf + capex if ocf is not None and capex is not None else None)
            if fcf is None:
                continue
            q = []                                         # newest first; Yahoo leaves some old quarters empty
            for c in cf.columns[:8]:
                if pd.isna(fcf[c]):
                    break
                q.append({"date": c.strftime("%Y-%m-%d"), "fcf": float(fcf[c]) / 1e9,
                          "capex": abs(float(capex[c])) / 1e9 if capex is not None and pd.notna(capex[c]) else None})
            if q:
                comp.append({"ticker": t, "name": name, "q": q})
        except Exception as e:
            log.warning("Market Watch: cash flow for %s failed: %s", t, e)
    if not comp:
        return {"status": "na", "note": "No cash-flow data could be fetched."}
    for c in comp:
        n = len(c["q"])
        c["ttm"] = round(sum(x["fcf"] for x in c["q"][:4]), 1) if n >= 4 else None
        c["ttm_capex"] = round(sum(x["capex"] or 0 for x in c["q"][:4]), 1) if n >= 4 else None
        c["last_q"] = round(c["q"][0]["fcf"], 1)
        c["last_q_yoy"] = round(c["q"][0]["fcf"] - c["q"][4]["fcf"], 1) if n >= 5 else None
        c["last_quarter"] = c["q"][0]["date"]
    ttm_cos = [c for c in comp if c["ttm"] is not None]
    ttm = sum(c["ttm"] for c in ttm_cos)
    yoy_cos = [c for c in comp if c["last_q_yoy"] is not None]
    now_q = sum(c["q"][0]["fcf"] for c in yoy_cos)
    ago_q = sum(c["q"][4]["fcf"] for c in yoy_cos)
    yoy = (now_q / ago_q - 1) if ago_q > 0 else None       # latest quarter against the same quarter a year before
    nq = RULES["fcf"]["negative_quarters_red"]
    two_neg = all(sum(c["q"][i]["fcf"] for c in comp if len(c["q"]) > i) < 0 for i in range(nq)) if all(len(c["q"]) > nq - 1 for c in comp) else False
    qsum = lambda i: sum(c["q"][i]["fcf"] for c in comp if len(c["q"]) > i)
    s0, s1 = qsum(0), qsum(1)
    capex = sum(c["ttm_capex"] or 0 for c in ttm_cos)
    capex_share = capex / (ttm + capex) if (ttm + capex) > 0 else None       # capex as a share of operating cash flow
    # once red it clears after one positive summed quarter
    if ttm < 0 or two_neg or ((prev or {}).get("status") == "bad" and s0 <= 0):
        status = "bad"
    elif (yoy is not None and yoy < RULES["fcf"]["yoy_amber"]) or (capex_share is not None and capex_share > RULES["fcf"]["capex_share_amber"]):
        status = "warn"
    else:
        status = "good"
    # "worsening" for a quarterly series: the latest quarter's summed free cash flow is below the previous quarter's
    trend = "worsening" if s0 < s1 else ("improving" if s0 > s1 else "flat")
    return {"status": status, "trend": trend, "ttm_bn": round(ttm, 1), "capex_ttm_bn": round(capex, 1), "prev_q_bn": round(s1, 1),
            "capex_share_pct": round(capex_share * 100, 0) if capex_share is not None else None,
            "last_q_bn": round(sum(c["q"][0]["fcf"] for c in comp), 1),
            "last_q_yoy_pct": round(yoy * 100, 1) if yoy is not None else None, "companies": comp}


# ── 2. Credit spreads ───────────────────────────────────────────────────────────

def _fred(series_id: str, key: str) -> pd.Series:
    r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                     params={"series_id": series_id, "api_key": key, "file_type": "json",
                             "observation_start": (datetime.now() - pd.Timedelta(days=800)).strftime("%Y-%m-%d")},
                     timeout=(8, 25))
    r.raise_for_status()
    obs = [(o["date"], float(o["value"])) for o in r.json().get("observations", []) if o.get("value") not in (None, "", ".")]
    return pd.Series([v for _, v in obs], index=pd.to_datetime([d for d, _ in obs]))


def _spreads(prev: dict | None = None) -> dict:
    R = RULES["credit"]
    prev_bad = (prev or {}).get("status") == "bad"
    key = ENV_CONFIG.get("fred_api_key", "")
    if key:
        try:
            hy, ig = _fred("BAMLH0A0HYM2", key), _fred("BAMLC0A0CM", key)

            def red(k):                                  # any one of: fast widening, a high level, a slow big widening, IG stress
                h, g = _upto(hy, k), _upto(ig, k)
                d20, d60 = _chg(h, 20), _chg(h, 60)
                return bool((d20 is not None and d20 >= R["red_hy_20d_pp"]) or float(h.iloc[-1]) >= R["red_hy_level"] or (d60 is not None and d60 >= R["red_hy_60d_pp"]) or float(g.iloc[-1]) >= R["red_ig_level"])
            d20, d60 = _chg(hy, 20), _chg(hy, 60)
            amber = bool((d20 is not None and d20 >= R["amber_hy_20d_pp"]) or float(hy.iloc[-1]) >= R["amber_hy_level"] or (d60 is not None and d60 >= R["amber_hy_60d_pp"]) or float(ig.iloc[-1]) >= R["amber_ig_level"])
            # once red it clears only when back inside the line by a margin: HY under 4.0% (red was at 4.5%) and IG under 1.2%
            stay = not (float(hy.iloc[-1]) < R["clear_hy_level"] and float(ig.iloc[-1]) < R["clear_ig_level"])
            status, pending = _decide([red(k) for k in range(PERSIST_DAYS)], amber, prev_bad, stay)
            trend = "flat" if d20 is None else ("worsening" if d20 > 0.10 else ("improving" if d20 < -0.10 else "flat"))
            return {"status": status, "red_pending": pending, "trend": trend, "source": "ICE BofA option-adjusted spreads (FRED)", "kind": "oas",
                    "hy": round(float(hy.iloc[-1]), 2), "ig": round(float(ig.iloc[-1]), 2), "hy_chg_20d": round(d20, 2) if d20 is not None else None,
                    "hy_chg_60d": round(d60, 2) if d60 is not None else None, "ig_chg_20d": round(_chg(ig, 20) or 0, 2),
                    "series": {"High yield OAS (%)": _pts(hy), "Investment grade OAS (%)": _pts(ig)}}
        except Exception as e:
            log.warning("Market Watch: FRED spreads failed, using the ETF proxy: %s", e)
    ief, hyg, lqd = _series("IEF", "2y"), _series("HYG", "2y"), _series("LQD", "2y")
    r_hy = (hyg / ief).dropna()
    r_ig = (lqd / ief).dropna()
    d20 = _rel(r_hy, 20)
    sma = r_hy.rolling(200).mean().iloc[-1]
    below = bool(pd.notna(sma) and r_hy.iloc[-1] < sma * R["proxy_below_200d"])
    red = lambda k: bool((_rel(_upto(r_hy, k), 20) or 0) <= R["proxy_red_20d"])
    status, pending = _decide([red(k) for k in range(PERSIST_DAYS)], bool((d20 is not None and d20 <= R["proxy_amber_20d"]) or below), prev_bad, bool(d20 is not None and d20 <= R["proxy_amber_20d"]))
    trend = "flat" if d20 is None else ("worsening" if d20 < -0.007 else ("improving" if d20 > 0.007 else "flat"))
    base = lambda x: x / x.iloc[-260:].iloc[0] * 100
    return {"status": status, "red_pending": pending, "trend": trend, "kind": "proxy",
            "source": "Proxy: price of high-yield (HYG) and investment-grade (LQD) bond ETFs relative to Treasuries (IEF) — falls when spreads widen. "
                      "Set FRED_API_KEY (free) for the real ICE BofA option-adjusted spreads.",
            "hy_ratio_chg_20d_pct": round(d20 * 100, 2) if d20 is not None else None,
            "ig_ratio_chg_20d_pct": round((_rel(r_ig, 20) or 0) * 100, 2), "below_200d": below,
            "series": {"HYG / IEF (rebased 100)": _pts(base(r_hy)), "LQD / IEF (rebased 100)": _pts(base(r_ig))}}


# ── 3. 10-year Treasury ─────────────────────────────────────────────────────────

def _ten_year(prev: dict | None = None) -> dict:
    s = _series("^TNX", "2y")
    now = float(s.iloc[-1])
    c1, c3 = _chg(s, 21), _chg(s, 63)

    def red(k):
        h = _upto(s, k)
        c = _chg(h, 63)
        return bool(c is not None and (c >= TEN_YEAR_SPEED or (float(h.iloc[-1]) > TEN_YEAR_LEVEL and c >= TEN_YEAR_LEVEL_SPEED)))
    amber = bool(now >= _TY["amber_level"] or (c3 is not None and c3 >= _TY["amber_speed_pp"]))
    stay = not (c3 is None or c3 < TEN_YEAR_CLEAR_SPEED)
    status, pending = _decide([red(k) for k in range(PERSIST_DAYS)], amber, (prev or {}).get("status") == "bad", stay)
    trend = "flat" if c1 is None else ("worsening" if c1 > 0.10 else ("improving" if c1 < -0.10 else "flat"))
    return {"status": status, "red_pending": pending, "trend": trend, "yield_pct": round(now, 2), "chg_1m": round(c1, 2) if c1 is not None else None,
            "chg_3m": round(c3, 2) if c3 is not None else None, "high_1y": round(float(s.tail(252).max()), 2),
            "series": {"10-year yield (%)": _pts(s)}}


# ── 4. Market breadth ───────────────────────────────────────────────────────────

SECTOR_ETFS = ["XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU", "XLRE", "XLC"]


def _tv_many(symbols: list[str], bars: int = 300, tries: int = 3) -> dict[str, pd.Series]:
    """TradingView daily closes of INDEX:<symbol>. One connection for all symbols, a few retries — the anonymous
    websocket occasionally times out or is refused, and a second attempt a moment later usually works."""
    from tvDatafeed import TvDatafeed, Interval
    last = None
    for attempt in range(tries):
        try:
            tv = TvDatafeed()
            out = {}
            for sym in symbols:
                d = tv.get_hist(symbol=sym, exchange="INDEX", interval=Interval.in_daily, n_bars=bars)
                if d is None or d.empty:
                    raise RuntimeError(f"TradingView has no data for {sym}")
                x = d["close"].dropna()
                x.index = pd.to_datetime(x.index).normalize()
                out[sym] = x
            return out
        except Exception as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(str(last) or "TradingView did not answer")


def _sectors_above_ma() -> dict:
    """Yahoo-only breadth fallback: how many of the 11 S&P 500 sector ETFs are above their 200- and 50-day averages."""
    a200 = a50 = n = 0
    for t in SECTOR_ETFS:
        try:
            s = _series(t, "2y")
            n += 1
            a200 += bool(s.iloc[-1] > s.rolling(200).mean().iloc[-1])
            a50 += bool(s.iloc[-1] > s.rolling(50).mean().iloc[-1])
        except Exception:
            continue
    return {"sectors_above_200d": a200, "sectors_above_50d": a50, "sectors_total": n} if n else {}


def _previous_breadth() -> dict | None:
    """The last successful TradingView figures, kept in the cached snapshot, for when the feed is down."""
    try:
        d = json.loads(get_app_setting(CACHE_KEY) or "{}").get("breadth") or {}
        return d if d.get("pct_above_200d") is not None and not d.get("stale") else None
    except Exception:
        return None


def _breadth(prev: dict | None = None) -> dict:
    B = RULES["breadth"]
    spx, rsp, spy = _series("^GSPC", "2y"), _series("RSP", "2y"), _series("SPY", "2y")
    ratio = (rsp / spy).dropna()
    rel3 = _rel(ratio, 63)
    dist = float((1 - spx.iloc[-1] / spx.tail(252).max()) * 100)
    out = {"spx_below_high_pct": round(dist, 1), "rsp_vs_spy_3m_pct": round(rel3 * 100, 1) if rel3 is not None else None}
    s200 = s50 = None
    tv_err = ""
    try:
        got = _tv_many(["S5TH", "S5FI"])
        s200, s50 = got["S5TH"], got["S5FI"]
    except Exception as e:
        tv_err = str(e)[:120]
        log.warning("Market Watch: TradingView breadth failed: %s", e)
    series = {"RSP / SPY (rebased 100)": _pts(ratio / ratio.iloc[-260:].iloc[0] * 100)}
    now200 = d20 = None
    if s200 is not None:
        now200, d20 = float(s200.iloc[-1]), _chg(s200, 20)
        out.update({"pct_above_200d": round(now200, 1), "pct_above_50d": round(float(s50.iloc[-1]), 1),
                    "pct_above_200d_chg_20d": round(d20, 1) if d20 is not None else None})
        series = {"S&P 500 stocks above 200-day average (%)": _pts(s200), "…above 50-day average (%)": _pts(s50), **series}
    else:
        prev = _previous_breadth()                 # reuse the last good reading rather than drop the indicator
        if prev:
            now200, d20 = prev["pct_above_200d"], prev.get("pct_above_200d_chg_20d")
            out.update({k: prev[k] for k in ("pct_above_200d", "pct_above_50d", "pct_above_200d_chg_20d") if k in prev})
            out["stale"] = True
            series = {**{k: v for k, v in (prev.get("series") or {}).items() if k.startswith(("S&P", "…"))}, **series}
            out["note"] = f"TradingView could not be reached ({tv_err}); showing the last good reading ({str((prev.get('series') or {}).get('S&P 500 stocks above 200-day average (%)', {}).get('x', ['?'])[-1])})."
        else:
            out["note"] = f"The % of stocks above their moving averages (TradingView) could not be fetched ({tv_err}); using equal- vs cap-weight (RSP/SPY) and the sector ETFs."
        out.update(_sectors_above_ma())
    prev_bad = (prev or {}).get("status") == "bad"
    near = lambda k: float((1 - _upto(spx, k).iloc[-1] / _upto(spx, k).tail(252).max()) * 100) <= B["near_high_pct"]      # "near the high"
    out["spx_near_high"] = bool(near(0))
    if now200 is not None:
        n50 = out.get("pct_above_50d")

        def red(k):
            a = float(_upto(s200, k).iloc[-1]) if s200 is not None else now200
            b = float(_upto(s50, k).iloc[-1]) if s50 is not None else n50
            # either: under 40% above the 200-day, or under 25% above the 50-day while the 200-day is also under 50% (a
            # lone 50-day dip is usually just a short-term oversold point) — both while the index is near its high
            return bool(near(k) and (a < B["red_200d"] or (b is not None and b < B["red_50d"] and a < B["red_50d_needs_200d_under"])))
        amber = bool(now200 < B["amber_200d"] or (rel3 is not None and rel3 <= B["amber_rsp_spy_3m"]) or (n50 is not None and n50 < B["amber_50d"]))
        stay = not (now200 > B["clear_200d"] and (n50 is None or n50 > B["clear_50d"]))       # once red, clears when the 200-day is above 45% and the 50-day above 30%
        status, pending = _decide([red(k) for k in range(PERSIST_DAYS)], amber, prev_bad, stay)
        if now200 >= B["good_200d"] and (rel3 is None or rel3 > B["amber_rsp_spy_3m"]) and status == "warn" and not pending:
            status = "good"
        elif d20 is not None and d20 >= B["good_rising_pts"] and now200 >= B["good_rising_200d"] and status == "warn" and not pending:
            status = "good"
        out["red_pending"] = pending
        trend = "flat" if d20 is None else ("improving" if d20 >= 3 else ("worsening" if d20 <= -3 else "flat"))
    else:                                        # no breadth feed at all: equal- vs cap-weight, and the sectors
        status = "bad" if (rel3 is not None and rel3 < -0.05 and dist <= 5) else ("warn" if rel3 is not None and rel3 < -0.02 else "good")
        n, a = out.get("sectors_total"), out.get("sectors_above_200d")
        if n and status == "good" and a is not None and a / n < 0.5:
            status = "warn"
        trend = "flat" if rel3 is None else ("improving" if rel3 > 0.01 else ("worsening" if rel3 < -0.01 else "flat"))
    out.update({"status": status, "trend": trend, "series": series})
    return out


# ── Context tiles — they do not count toward the banner ────────────────────────

def _france(prev: dict | None = None) -> dict:
    """French minus German 10-year government bond yield (FRED, monthly OECD series). Slow-moving, so no 3-day rule;
    it feeds one playbook step (new IBCI money), not the banner."""
    key = ENV_CONFIG.get("fred_api_key", "")
    if not key:
        return {"status": "na", "note": "Needs a free FRED_API_KEY (see Help → Market Watch)."}
    fr, de = _fred("IRLTLT01FRM156N", key), _fred("IRLTLT01DEM156N", key)
    sp = (fr - de).dropna()
    now = float(sp.iloc[-1])
    d3 = float(sp.iloc[-1] - sp.iloc[-4]) if len(sp) > 3 else None
    F = RULES["france"]
    status = "bad" if now >= F["red_pp"] or (d3 is not None and d3 >= F["red_3m_pp"]) else ("warn" if now >= F["amber_pp"] else "good")
    trend = "flat" if d3 is None else ("worsening" if d3 > 0.1 else ("improving" if d3 < -0.1 else "flat"))
    return {"status": status, "trend": trend, "spread_pp": round(now, 2), "chg_3m": round(d3, 2) if d3 is not None else None,
            "as_of": sp.index[-1].strftime("%Y-%m"), "series": {"France − Germany, 10-year (pp)": _pts(sp)}}


def _erp() -> dict:
    """Equity risk premium: the Shiller earnings yield (1 / CAPE) minus the 10-year real yield (FRED DFII10)."""
    from database.queries import get_shiller_cape_summary
    cape = get_shiller_cape_summary()
    key = ENV_CONFIG.get("fred_api_key", "")
    if not cape:
        return {"status": "na", "note": "No CAPE data yet — download it under Market Data → CAPE Ratios."}
    if not key:
        return {"status": "na", "note": "Needs a free FRED_API_KEY (see Help → Market Watch)."}
    real = _fred("DFII10", key)
    ey = 100.0 / float(cape["cape_ratio"])
    now_real = float(real.iloc[-1])
    erp = ey - now_real
    status = "bad" if erp < RULES["erp"]["red_pp"] else ("warn" if erp < RULES["erp"]["green_pp"] else "good")
    # the premium through time: the monthly earnings yield (1 / CAPE), carried forward, minus the daily real yield
    from database.connection import get_db
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT Cape_Date, Cape_Ratio FROM Shiller_Cape ORDER BY Cape_Date")
        rows = cur.fetchall()
    eys = pd.Series([100.0 / float(r[1]) for r in rows], index=pd.to_datetime([r[0] for r in rows]))
    erp_series = (eys.reindex(eys.index.union(real.index)).ffill().reindex(real.index) - real).dropna()
    return {"status": status, "trend": None, "erp_pp": round(erp, 2), "earnings_yield_pct": round(ey, 2), "real_yield_pct": round(now_real, 2),
            "cape": round(float(cape["cape_ratio"]), 1), "cape_as_of": cape["date"],
            "series": {"Equity risk premium (pp) = 1/CAPE − 10-year real yield": _pts(erp_series, 520)}}


# ── Assemble, cache, serve ──────────────────────────────────────────────────────

def _prev_snapshot() -> dict:
    try:
        return json.loads(get_app_setting(CACHE_KEY) or "{}")
    except Exception:
        return {}


def compute(prev: dict | None = None) -> dict:
    prev = prev if prev is not None else _prev_snapshot()
    jobs = {"fcf": lambda: _fcf(prev.get("fcf")), "spreads": lambda: _spreads(prev.get("spreads")), "ten_year": lambda: _ten_year(prev.get("ten_year")),
            "breadth": lambda: _breadth(prev.get("breadth")), "france": lambda: _france(prev.get("france")), "erp": _erp}
    out: dict = {}

    def run(k):
        try:
            return k, jobs[k]()
        except Exception as e:
            log.warning("Market Watch: %s failed: %s", k, e)
            return k, {"status": "na", "note": f"Could not be fetched: {e}"[:200]}

    with ThreadPoolExecutor(max_workers=4) as pool:
        for k, v in pool.map(run, jobs):
            out[k] = v
    live = [out[k] for k in ("fcf", "spreads", "ten_year", "breadth") if out[k].get("status") != "na"]     # france / erp are context only
    bad = sum(v["status"] == "bad" for v in live)
    warn = sum(v["status"] == "warn" for v in live)
    imp = sum(v.get("trend") == "improving" for v in live)
    wor = sum(v.get("trend") == "worsening" for v in live)
    if not live:
        verdict, level = "No data could be fetched.", "na"
    elif bad >= 2:
        verdict, level = "Two or more indicators are in Warning — the crash rules are on. Follow the playbook.", "bad"
    elif len(live) >= 3 and wor == len(live):
        verdict = ("All the indicators are worsening together — a late-cycle pattern, not a crisis yet: none has crossed its red line."
                   if bad == 0 else "All the indicators are worsening together and one is already in Warning.")
        level = "warn"
    elif bad == 1 or warn >= 2 or wor >= 2:
        verdict, level = "Mixed: some indicators are flashing a warning — worth watching.", "warn"
    elif imp >= 2 and wor == 0:
        verdict, level = "The indicators are healthy and improving — the market has reason to continue.", "good"
    else:
        verdict, level = "Broadly healthy, no indicator is flashing a warning.", "good"
    out["overall"] = {"level": level, "verdict": verdict, "bad": bad, "warn": warn, "improving": imp, "worsening": wor}
    out["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out["rule_version"] = rule_version()
    return out


_LABELS = {"fcf": "Hyperscaler free cash flow", "spreads": "Credit spreads", "ten_year": "10-year Treasury", "breadth": "Market breadth",
           "france": "France–Germany spread", "erp": "Equity risk premium", "overall": "Overall"}
_NAMES = {"good": "healthy", "warn": "on watch", "bad": "in warning", "na": "no data"}


def _record(prev: dict, data: dict) -> None:
    """Today's readings go to Market_Watch_Log (one row per day, for later back-testing); a change of status — green→amber,
    amber→red, red→clear — to Market_Watch_Events, once, so there is a short list of what changed rather than a daily message."""
    from database.connection import get_db
    levels = {k: data[k].get("status") for k in ("fcf", "spreads", "ten_year", "breadth", "france", "erp")}
    levels["overall"] = data["overall"]["level"]
    transition = None
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""INSERT INTO Market_Watch_Log (Log_Date, Overall, Fcf, Spreads, Ten_Year, Breadth, Readings)
                           VALUES (CURRENT_DATE, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (Log_Date) DO UPDATE SET Overall = EXCLUDED.Overall, Fcf = EXCLUDED.Fcf, Spreads = EXCLUDED.Spreads,
                               Ten_Year = EXCLUDED.Ten_Year, Breadth = EXCLUDED.Breadth, Readings = EXCLUDED.Readings, Created_At = NOW()""",
                        (levels["overall"], levels["fcf"], levels["spreads"], levels["ten_year"], levels["breadth"],
                         json.dumps({k: {x: y for x, y in data[k].items() if x not in ("series", "companies", "source", "note")} for k in ("fcf", "spreads", "ten_year", "breadth", "france", "erp")})))
            if prev.get("generated_at"):
                before = {k: prev.get(k, {}).get("status") for k in ("fcf", "spreads", "ten_year", "breadth", "france", "erp")}
                before["overall"] = (prev.get("overall") or {}).get("level")
                if before["overall"] == "bad" and sum(v == "bad" for k, v in before.items() if k in ("fcf", "spreads", "ten_year", "breadth")) < 2:
                    before["overall"] = "warn"          # a snapshot written by older rules said red with no indicator in Warning: it was not really red
                for k, to in levels.items():
                    frm = before.get(k)
                    if frm and to and frm != to and to != "na" and frm != "na":
                        cur.execute("INSERT INTO Market_Watch_Events (Indicator, From_Status, To_Status, Message) VALUES (%s, %s, %s, %s)",
                                    (k, frm, to, f"{_LABELS[k]}: {_NAMES.get(frm, frm)} → {_NAMES.get(to, to)}"))
                        if k == "overall":
                            transition = (frm, to)
    except Exception as e:
        log.warning("Market Watch: could not record the log/events: %s", e)
    if transition and (transition[1] == "bad" or transition[0] == "bad"):          # the two moments that call for action: into red, out of red
        try:
            from database.notify import notify
            reds = [_LABELS[k] for k in ("fcf", "spreads", "ten_year", "breadth") if data[k].get("status") == "bad"]
            plan = get_playbook()["text"]
            if transition[1] == "bad":
                notify(f"🔴 Market Watch is RED — the crash rules are on\nIn warning: {', '.join(reds)}\n\nYour playbook:\n{plan}")
            else:
                notify(f"🟢 Market Watch is out of red\n{data['overall']['verdict']}\n\nYour playbook says when to resume the normal plan:\n{plan}")
        except Exception as e:
            log.warning("Market Watch: Telegram alert failed: %s", e)


def refresh_cache() -> dict:
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
        if d.get("rule_version") != rule_version():          # written by an older version of the rules (e.g. a container not yet restarted)
            return None
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(d["generated_at"])).total_seconds() / 3600
        return d if age <= max_age_h else None
    except Exception:
        return None


DEFAULT_PLAYBOOK = (
    "When the banner turns RED:\n"
    "1. Don't sell anything. Keep SGLD, XEON, IBCI/EUIN and the T-bill ladder running as normal.\n"
    "2. Pause new equity buys (VWCE €300, IS3Q €50, IUHE €100, XDG6 €50). Put that €500/month into XEON instead.\n"
    "3. Resume the normal plan when the banner has been out of red for 10 trading days, or after 2 months at most, "
    "whichever comes first.\n"
    "4. If VWCE falls 20% or more from its high: resume equity buys immediately, add €100–200/month from XEON to VWCE, "
    "and use maturing T-bills to rebuild equities back toward 40% (band 35–45%).\n"
    "5. If the France tile turns red: send new IBCI money to EUIN/XEON, and check XEON's swap counterparties.\n"
    "6. Review the whole plan 3 months after any red episode.")


@router.get("")
def get_market_watch(refresh: bool = False):
    if not refresh:
        d = _cached()
        if d:
            return d
    return refresh_cache()


@router.get("/summary")
def get_market_watch_summary():
    """Just the statuses — what the Dashboard tile needs. Stale data is fine here (the scheduler refreshes it)."""
    from database.connection import get_db
    d = _cached(max_age_h=24 * 7) or refresh_cache()
    recent = 0
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM Market_Watch_Events WHERE Event_At > NOW() - INTERVAL '7 days' AND Indicator <> 'overall' AND Indicator NOT LIKE 'local%'")
            recent = cur.fetchone()[0]
    except Exception:
        pass
    return {"generated_at": d["generated_at"], "overall": d["overall"], "recent_changes": recent,
            "indicators": {k: {"status": d[k].get("status"), "trend": d[k].get("trend")} for k in ("fcf", "spreads", "ten_year", "breadth")}}


@router.get("/events")
def get_market_watch_events(limit: int = 15):
    from database.connection import get_db
    with get_db() as conn:
        cur = conn.cursor()
        # the Local Market Watch's changes ('local_*') live in the same table; they belong to its own tab
        cur.execute("SELECT Event_At, Indicator, From_Status, To_Status, Message FROM Market_Watch_Events WHERE Indicator NOT LIKE 'local%%' ORDER BY Event_At DESC LIMIT %s", (limit,))
        return [{"at": r[0].isoformat(timespec="seconds"), "indicator": r[1], "from": r[2], "to": r[3], "message": r[4]} for r in cur.fetchall()]


@router.get("/playbook")
def get_playbook():
    return {"text": get_app_setting("market_watch_playbook") or DEFAULT_PLAYBOOK, "is_default": not get_app_setting("market_watch_playbook")}


@router.put("/playbook")
def save_playbook(data: dict):
    save_app_setting("market_watch_playbook", (data.get("text") or "").strip())
    return {"ok": True}


def get_market_watch_insights() -> list:
    """Dashboard insights — only at the two moments that call for action: the banner going red (shown for as long as it is
    red) and leaving red (shown for a week), plus the France–Germany spread in Warning, which has its own playbook step.
    Reads the stored snapshot; it never fetches."""
    from database.connection import get_db
    d = _prev_snapshot()
    if not d.get("overall"):
        return []
    out = []
    reds = [_LABELS[k] for k in ("fcf", "spreads", "ten_year", "breadth") if d.get(k, {}).get("status") == "bad"]
    if len(reds) >= 2:                      # judged from the indicators themselves, not the stored banner level
        out.append({"type": "danger", "icon": "market_watch", "title": "Market Watch is RED — the crash rules are on",
                    "message": f"In warning: {', '.join(reds)}. Open Market Data → Market Watch for your playbook."})
    else:
        try:
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute("SELECT Event_At FROM Market_Watch_Events WHERE Indicator = 'overall' AND From_Status = 'bad' "
                            "AND Event_At > NOW() - INTERVAL '7 days' ORDER BY Event_At DESC LIMIT 1")
                row = cur.fetchone()
            if row:
                out.append({"type": "info", "icon": "market_watch", "title": "Market Watch is out of red",
                            "message": f"Left red on {row[0].strftime('%d %b')}. Your playbook says when to resume the normal plan."})
        except Exception:
            pass
    try:                                                                  # a back-test whose result changed materially (last 7 days)
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT Run_At, Material_Change FROM Market_Watch_Backtests WHERE Scope = 'market' AND Material_Change IS NOT NULL "
                        "AND Run_At > NOW() - INTERVAL '7 days' ORDER BY Run_At DESC LIMIT 1")
            row = cur.fetchone()
        if row:
            out.append({"type": "info", "icon": "market_watch", "title": "Market Watch back-test changed",
                        "message": f"{row[1][:300]} — see the Back-test card on Market Data → Market Watch."})
    except Exception:
        pass
    fr = d.get("france") or {}
    if fr.get("status") == "bad":
        out.append({"type": "warning", "icon": "market_watch", "title": "France–Germany 10-year spread is in Warning",
                    "message": f"{fr.get('spread_pp')} pp ({fr.get('as_of')}). See your playbook for what to do with new IBCI money."})
    return out


# ── Back-test: stored runs, compared; run from the UI, on rule changes and on a schedule ─────────────────────────

BACKTEST_KEY = "market_watch_backtest"
LOG_READY_DAYS = 180


def _summary(res: dict) -> dict:
    """What is compared between runs: the share of red days, the number of red spells and when each named crash was first caught."""
    out = {"share_red": {}, "spells": {}, "episodes": {}, "log_days": (res.get("log") or {}).get("days", 0), "data_date": res.get("data_date")}
    for k in ("credit_live", "credit_surrogate", "ten_year"):
        v = res.get(k) or {}
        out["share_red"][k] = v.get("share_red_pct")
        out["spells"][k] = len(v.get("spells") or [])
        out["episodes"][k] = {e["episode"]: e["first_red"] for e in v.get("episodes") or []}
    return out


def _material(prev: dict | None, new: dict, prev_ver: str | None, ver: str) -> str | None:
    """Only a change that matters: a new red spell, a crash caught or missed differently, or the share of red days moving by more than
    the set number of points. Otherwise None — the run is just stored and its date updated, quietly."""
    if not prev:
        return None
    A = RULES["backtest_alert"]
    notes = []
    for k, name in (("credit_live", "credit (ICE BofA)"), ("credit_surrogate", "credit surrogate"), ("ten_year", "10-year")):
        d = (new["share_red"].get(k) or 0) - (prev.get("share_red", {}).get(k) or 0)
        if abs(d) > A["share_red_pts"]:
            notes.append(f"{name}: red on {new['share_red'][k]}% of days ({d:+.1f} points)")
        if new["spells"].get(k, 0) > prev.get("spells", {}).get(k, 0):
            notes.append(f"{name}: a new red spell")
        for ep, first in new["episodes"].get(k, {}).items():
            before = prev.get("episodes", {}).get(k, {}).get(ep)
            if (before is None) != (first is None):
                notes.append(f"{name}: {ep} now {'caught' if first else 'missed'}")
            elif before and first and abs((pd.Timestamp(first) - pd.Timestamp(before)).days) > A["episode_days"]:
                notes.append(f"{name}: {ep} first red moved {before} → {first}")
    if prev_ver and prev_ver != ver and not notes:
        notes.append(f"rule version {prev_ver} → {ver}: no material difference in the back-test")
    elif prev_ver and prev_ver != ver:
        notes.insert(0, f"rule version {prev_ver} → {ver}")
    return "; ".join(notes) or None


def run_and_store(trigger: str = "manual") -> dict:
    """Run the back-test, store it with the rule version and the reason it ran, and note any material change from the last run."""
    from database.connection import get_db
    from data.market_watch_backtest import run_backtest
    res = run_backtest()
    ver = rule_version()
    res["rule_version"], res["trigger"] = ver, trigger
    summ = _summary(res)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT Rule_Version, Summary FROM Market_Watch_Backtests WHERE Scope = 'market' ORDER BY Run_At DESC LIMIT 1")
        last = cur.fetchone()
        if last and last[0] == ver and (last[1] or {}).get("data_date") == summ["data_date"]:
            res["skipped"], res["material_change"] = True, None              # same rule version, same data: nothing new to store
            return res
        change = _material(last[1] if last else None, summ, last[0] if last else None, ver)
        cur.execute("INSERT INTO Market_Watch_Backtests (Trigger, Rule_Version, Summary, Result, Material_Change) VALUES (%s, %s, %s, %s, %s)",
                    (trigger, ver, json.dumps(summ), json.dumps(res), change))
    res["material_change"] = change
    save_app_setting(BACKTEST_KEY, json.dumps(res))
    return res


def backtest_due(now: datetime | None = None) -> str | None:
    """Why a back-test should run now, or None. Event-based first (a rule changed), then the calendar: the first Sunday of the month
    (after 03:00), the mid-month after each earnings season (Feb, May, Aug, Nov), and once the daily log is long enough for breadth and
    free cash flow to be tested."""
    from database.connection import get_db
    now = now or datetime.now()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT Run_At, Rule_Version FROM Market_Watch_Backtests WHERE Scope = 'market' ORDER BY Run_At DESC LIMIT 1")
        last = cur.fetchone()
        if last is None:
            return "first run"
        if last[1] != rule_version():
            return "rule change"
        if now.weekday() == 6 and now.day <= 7 and now.hour >= 3 and last[0].astimezone().replace(tzinfo=None) < now.replace(hour=3, minute=0, second=0, microsecond=0):
            return "monthly"
        if now.month in (2, 5, 8, 11) and now.day >= 15:
            cur.execute("SELECT 1 FROM Market_Watch_Backtests WHERE Scope = 'market' AND Trigger = 'earnings season' AND date_trunc('month', Run_At) = date_trunc('month', NOW()) LIMIT 1")
            if cur.fetchone() is None:
                return "earnings season"
        cur.execute("SELECT COUNT(*) FROM Market_Watch_Log")
        if cur.fetchone()[0] >= LOG_READY_DAYS:
            cur.execute("SELECT 1 FROM Market_Watch_Backtests WHERE Scope = 'market' AND Trigger = 'log 6 months' LIMIT 1")
            if cur.fetchone() is None:
                return "log 6 months"
    return None


@router.get("/backtest")
def get_backtest():
    """The last stored back-test (or null)."""
    raw = get_app_setting(BACKTEST_KEY)
    return json.loads(raw) if raw else None


@router.get("/backtest/history")
def get_backtest_history(limit: int = 20):
    from database.connection import get_db
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT Run_At, Trigger, Rule_Version, Summary, Material_Change FROM Market_Watch_Backtests WHERE Scope = 'market' ORDER BY Run_At DESC LIMIT %s", (limit,))
        return [{"at": r[0].isoformat(timespec="seconds"), "trigger": r[1], "rule_version": r[2], "summary": r[3], "material_change": r[4]} for r in cur.fetchall()]


@router.post("/backtest")
def run_backtest_endpoint():
    """Run the back-test now (the "Run again" button): a few seconds — FRED history + the S&P 500."""
    from fastapi import HTTPException
    try:
        return run_and_store("manual")
    except RuntimeError as e:
        raise HTTPException(400, str(e))
