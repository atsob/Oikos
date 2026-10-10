"""Market Watch — the four indicators worth following (Market Data -> Market Watch, and a Dashboard tile).

  1. Free cash flow of the hyperscalers   does the AI capex pay back?        Yahoo quarterly cash flows
  2. Credit spreads                       what does funding cost?            FRED OAS if FRED_API_KEY is set,
                                                                             else HYG/LQD vs Treasuries (price ratio)
  3. 10-year Treasury yield               the cost of capital                Yahoo ^TNX
  4. Market breadth                       how wide is the rally?             TradingView S5TH / S5FI (% of S&P 500
                                                                             stocks above their 200/50-day average)
                                                                             and RSP vs SPY (equal vs cap weight)

Each gets a status — good / warn / bad — from the usual rules of thumb, a trend (improving / worsening / flat) and the
series behind it. The result is computed from public sources, cached in app_settings for a few hours, and refreshed
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


def _chg(s: pd.Series, n: int) -> float | None:
    return float(s.iloc[-1] - s.iloc[-1 - n]) if len(s) > n else None


def _rel(s: pd.Series, n: int) -> float | None:
    return float(s.iloc[-1] / s.iloc[-1 - n] - 1) if len(s) > n else None


# ── 1. Free cash flow of the hyperscalers ───────────────────────────────────────

def _fcf() -> dict:
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
    two_neg = all(sum(c["q"][i]["fcf"] for c in comp if len(c["q"]) > i) < 0 for i in (0, 1)) if all(len(c["q"]) > 1 for c in comp) else False
    if ttm < 0 or two_neg:
        status = "bad"
    elif yoy is not None and yoy < -0.10:
        status = "warn"
    else:
        status = "good"
    trend = "flat" if yoy is None else ("improving" if yoy > 0.05 else ("worsening" if yoy < -0.05 else "flat"))
    return {"status": status, "trend": trend, "ttm_bn": round(ttm, 1), "capex_ttm_bn": round(sum(c["ttm_capex"] or 0 for c in ttm_cos), 1),
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


def _spreads() -> dict:
    key = ENV_CONFIG.get("fred_api_key", "")
    if key:
        try:
            hy, ig = _fred("BAMLH0A0HYM2", key), _fred("BAMLC0A0CM", key)
            d20 = _chg(hy, 20)
            status = "bad" if d20 is not None and d20 >= 0.75 else ("warn" if d20 is not None and d20 >= 0.35 else "good")
            trend = "flat" if d20 is None else ("worsening" if d20 > 0.15 else ("improving" if d20 < -0.15 else "flat"))
            return {"status": status, "trend": trend, "source": "ICE BofA option-adjusted spreads (FRED)", "kind": "oas",
                    "hy": round(float(hy.iloc[-1]), 2), "ig": round(float(ig.iloc[-1]), 2), "hy_chg_20d": round(d20, 2) if d20 is not None else None,
                    "ig_chg_20d": round(_chg(ig, 20) or 0, 2), "series": {"High yield OAS (%)": _pts(hy), "Investment grade OAS (%)": _pts(ig)}}
        except Exception as e:
            log.warning("Market Watch: FRED spreads failed, using the ETF proxy: %s", e)
    ief, hyg, lqd = _series("IEF", "2y"), _series("HYG", "2y"), _series("LQD", "2y")
    r_hy = (hyg / ief).dropna()
    r_ig = (lqd / ief).dropna()
    d20 = _rel(r_hy, 20)
    sma = r_hy.rolling(200).mean().iloc[-1]
    below = bool(pd.notna(sma) and r_hy.iloc[-1] < sma * 0.98)
    status = "bad" if d20 is not None and d20 <= -0.03 else ("warn" if (d20 is not None and d20 <= -0.015) or below else "good")
    trend = "flat" if d20 is None else ("worsening" if d20 < -0.007 else ("improving" if d20 > 0.007 else "flat"))
    base = lambda s: s / s.iloc[-260:].iloc[0] * 100
    return {"status": status, "trend": trend, "kind": "proxy",
            "source": "Proxy: price of high-yield (HYG) and investment-grade (LQD) bond ETFs relative to Treasuries (IEF) — falls when spreads widen. "
                      "Set FRED_API_KEY (free) for the real ICE BofA option-adjusted spreads.",
            "hy_ratio_chg_20d_pct": round(d20 * 100, 2) if d20 is not None else None,
            "ig_ratio_chg_20d_pct": round((_rel(r_ig, 20) or 0) * 100, 2), "below_200d": below,
            "series": {"HYG / IEF (rebased 100)": _pts(base(r_hy)), "LQD / IEF (rebased 100)": _pts(base(r_ig))}}


# ── 3. 10-year Treasury ─────────────────────────────────────────────────────────

def _ten_year() -> dict:
    s = _series("^TNX", "2y")
    now = float(s.iloc[-1])
    c1, c3 = _chg(s, 21), _chg(s, 63)
    if now > 5.5 and (c3 or 0) > 0:
        status = "bad"
    elif now < 5.0 and (c3 is None or abs(c3) <= 0.25 or c3 < 0):
        status = "good"
    else:
        status = "warn"
    trend = "flat" if c3 is None else ("worsening" if c3 > 0.10 else ("improving" if c3 < -0.10 else "flat"))
    return {"status": status, "trend": trend, "yield_pct": round(now, 2), "chg_1m": round(c1, 2) if c1 is not None else None,
            "chg_3m": round(c3, 2) if c3 is not None else None, "high_1y": round(float(s.tail(252).max()), 2),
            "series": {"10-year yield (%)": _pts(s)}}


# ── 4. Market breadth ───────────────────────────────────────────────────────────

def _tv(symbol: str, bars: int = 300) -> pd.Series:
    from tvDatafeed import TvDatafeed, Interval
    d = TvDatafeed().get_hist(symbol=symbol, exchange="INDEX", interval=Interval.in_daily, n_bars=bars)
    if d is None or d.empty:
        raise RuntimeError(f"TradingView has no data for {symbol}")
    s = d["close"].dropna()
    s.index = pd.to_datetime(s.index).normalize()
    return s


def _breadth() -> dict:
    spx, rsp, spy = _series("^GSPC", "2y"), _series("RSP", "2y"), _series("SPY", "2y")
    ratio = (rsp / spy).dropna()
    rel3 = _rel(ratio, 63)
    dist = float((1 - spx.iloc[-1] / spx.tail(252).max()) * 100)
    out = {"spx_below_high_pct": round(dist, 1), "rsp_vs_spy_3m_pct": round(rel3 * 100, 1) if rel3 is not None else None}
    s200 = s50 = None
    try:
        s200, s50 = _tv("S5TH"), _tv("S5FI")
    except Exception as e:
        log.warning("Market Watch: TradingView breadth failed: %s", e)
    series = {"RSP / SPY (rebased 100)": _pts(ratio / ratio.iloc[-260:].iloc[0] * 100)}
    if s200 is not None:
        now200 = float(s200.iloc[-1])
        d20 = _chg(s200, 20)
        out.update({"pct_above_200d": round(now200, 1), "pct_above_50d": round(float(s50.iloc[-1]), 1),
                    "pct_above_200d_chg_20d": round(d20, 1) if d20 is not None else None})
        series = {"S&P 500 stocks above 200-day average (%)": _pts(s200), "…above 50-day average (%)": _pts(s50), **series}
        if dist <= 3 and now200 < 40:
            status = "bad"
        elif now200 >= 50 and (rel3 is None or rel3 > -0.02):
            status = "good"
        elif d20 is not None and d20 >= 5 and now200 >= 45:
            status = "good"
        else:
            status = "warn"
        trend = "flat" if d20 is None else ("improving" if d20 >= 3 else ("worsening" if d20 <= -3 else "flat"))
    else:                                        # no breadth feed: judge by equal- vs cap-weight alone
        status = "bad" if (rel3 is not None and rel3 < -0.05 and dist <= 3) else ("warn" if rel3 is not None and rel3 < -0.02 else "good")
        trend = "flat" if rel3 is None else ("improving" if rel3 > 0.01 else ("worsening" if rel3 < -0.01 else "flat"))
        out["note"] = "The % of stocks above their moving averages (TradingView) could not be fetched; using equal- vs cap-weight (RSP/SPY)."
    out.update({"status": status, "trend": trend, "series": series})
    return out


# ── Assemble, cache, serve ──────────────────────────────────────────────────────

def compute() -> dict:
    jobs = {"fcf": _fcf, "spreads": _spreads, "ten_year": _ten_year, "breadth": _breadth}
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
    live = [v for v in out.values() if v.get("status") != "na"]
    bad = sum(v["status"] == "bad" for v in live)
    warn = sum(v["status"] == "warn" for v in live)
    imp = sum(v.get("trend") == "improving" for v in live)
    wor = sum(v.get("trend") == "worsening" for v in live)
    if not live:
        verdict, level = "No data could be fetched.", "na"
    elif bad >= 2 or (wor == len(live) and len(live) >= 3):
        verdict, level = "The indicators are deteriorating together — the risk is rising even if the S&P 500 keeps making new highs.", "bad"
    elif bad == 1 or warn >= 2 or wor >= 2:
        verdict, level = "Mixed: some indicators are flashing a warning — worth watching.", "warn"
    elif imp >= 2 and wor == 0:
        verdict, level = "The indicators are healthy and improving — the market has reason to continue.", "good"
    else:
        verdict, level = "Broadly healthy, no indicator is flashing a warning.", "good"
    out["overall"] = {"level": level, "verdict": verdict, "bad": bad, "warn": warn, "improving": imp, "worsening": wor}
    out["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return out


def refresh_cache() -> dict:
    data = compute()
    save_app_setting(CACHE_KEY, json.dumps(data))
    return data


def _cached(max_age_h: float = CACHE_HOURS) -> dict | None:
    raw = get_app_setting(CACHE_KEY)
    if not raw:
        return None
    try:
        d = json.loads(raw)
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(d["generated_at"])).total_seconds() / 3600
        return d if age <= max_age_h else None
    except Exception:
        return None


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
    d = _cached(max_age_h=24 * 7) or refresh_cache()
    return {"generated_at": d["generated_at"], "overall": d["overall"],
            "indicators": {k: {"status": d[k].get("status"), "trend": d[k].get("trend")} for k in ("fcf", "spreads", "ten_year", "breadth")}}
