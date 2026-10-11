"""Back-test of the Local Market Watch red rules for the country of usage (Market Data -> Local Market Watch -> Back-test).

What can be tested over a long history, for any country with a profile (database/local_markets.py):
  trend       the benchmark index: 15% or more below its 52-week high and under its 200-day average
  banks       an equal-weight basket of the systemic banks (daily-rebalanced from the banks that traded each day): 20% or more below its
              high and under its 200-day
  sovereign   the country's 10-year yield minus Germany's (FRED monthly) — used with a 2-month lag, as that is when a month's figure is
              really known (the series is published about 6 weeks late), so there is no peeking
  banner      two or more of the three red at once — the banner rule, on the legs that have a history
What cannot: breadth (the large caps' constituents have no free history) — the stored daily log (Local_Watch_Log) will cover it.

The rules are those of api/routers/local_watch.py (RULES_LOCAL): a red condition must hold 3 trading days in a row and clears only when
back past its margin. The back-test never changes a threshold; it only reports.

Caveats shown with the result: banks that no longer exist (merged, delisted) are missing from Yahoo, so the bank basket is survivors-only —
it flatters the banks; and the history of a country's index is what Yahoo has (Athens: from 1997).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from api.routers.local_watch import PERSIST_DAYS, RULES_LOCAL, rule_version
from api.routers.market_watch import _fred
from config.settings import ENV_CONFIG
from data.market_watch_backtest import persistent, red_rows
from database.local_markets import get_profile, home_country

MIN_DEPTH = 25.0        # a "major" fall: this % from a peak to a trough — and the rally from the trough that ends it
LAG_MONTHS = 2          # the monthly sovereign figure is known about 6 weeks after its month ends


def _hist(sym: str) -> pd.Series:
    import yfinance as yf
    s = yf.Ticker(sym).history(period="max")["Close"].dropna()
    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
    return s[s > 0]


def _dd(x: pd.Series, n: int = 252) -> pd.Series:
    return (1 - x / x.rolling(n, min_periods=1).max()) * 100


def trend_red(idx: pd.Series) -> pd.Series:
    R = RULES_LOCAL["trend"]
    sma = idx.rolling(200).mean()
    dd = _dd(idx)
    return persistent((dd >= R["red_drawdown"]) & (idx < sma), (dd < R["clear_drawdown"]) & (idx >= sma))


def banks_red(basket: pd.Series) -> pd.Series:
    R = RULES_LOCAL["banks"]
    sma = basket.rolling(200).mean()
    dd = _dd(basket)
    return persistent((dd >= R["red_drawdown"]) & (basket < sma), (dd < R["clear_drawdown"]) & (basket >= sma))


def sovereign_red(spread: pd.Series, daily_index: pd.DatetimeIndex) -> pd.Series:
    """The monthly spread with its publication lag, spread over the days: red at the red level or +0.5 pp in 3 months."""
    R = RULES_LOCAL["sovereign"]
    red = (spread >= R["red_pp"]) | (spread.diff(3) >= R["red_3m_pp"])
    shifted = pd.Series(red.values, index=red.index + pd.DateOffset(months=LAG_MONTHS)).astype(bool)
    return shifted.reindex(shifted.index.union(daily_index)).ffill().reindex(daily_index).fillna(False).astype(bool)


def drawdown_episodes(idx: pd.Series, min_depth: float = MIN_DEPTH) -> list[dict]:
    """The major falls, by the classic bear-market rule: a bear starts once the index is `min_depth` % below its peak and ends once it has
    rallied `min_depth` % from its low. (Without the rally rule a market that never regains an old high — Athens after 1999 — is one 17-year
    "fall".) Each episode: peak date, trough date, depth."""
    out = []
    peak_d, peak = idx.index[0], float(idx.iloc[0])
    trough_d, trough, down = peak_d, peak, False
    for d, v in idx.items():
        v = float(v)
        if not down:
            if v > peak:
                peak_d, peak = d, v
            elif (1 - v / peak) * 100 >= min_depth:
                down, trough_d, trough = True, d, v
        else:
            if v < trough:
                trough_d, trough = d, v
            elif (v / trough - 1) * 100 >= min_depth:
                out.append({"peak": peak_d, "trough": trough_d, "depth": round((1 - trough / peak) * 100, 1)})
                down, peak_d, peak = False, d, v
    if down:
        out.append({"peak": peak_d, "trough": trough_d, "depth": round((1 - trough / peak) * 100, 1)})
    return out


def episode_rows(flag: pd.Series, idx: pd.Series, eps: list[dict]) -> list[dict]:
    """For each major fall: the red spell that covers it, when it began against the peak, and how far the index had fallen by then and
    still fell. A spell that began during the previous fall and never cleared is 'carried over' — it was not a fresh signal."""
    from data.market_watch_backtest import episodes as spells_of
    spells = spells_of(flag)
    rows = []
    for n, e in enumerate(eps):
        pk, tr = e["peak"], e["trough"]
        prev_tr = eps[n - 1]["trough"] if n else None
        base = {"episode": f"{pk.date()} → {tr.date()}", "depth_pct": -e["depth"]}
        sp = next(((a, b) for a, b in spells if a <= tr and b >= pk), None)
        if sp is None:
            rows.append({**base, "first_red": None, "days_vs_peak": None, "carried": False, "from_peak_pct": None, "further_pct": None})
            continue
        a = sp[0]
        carried = prev_tr is not None and a <= prev_tr + pd.Timedelta(days=5)
        at = max(a, pk)                                           # the point from which the signal had to be acted on
        s_then, s_pk, s_tr = float(idx[:at].iloc[-1]), float(idx[:pk].iloc[-1]), float(idx[:tr].iloc[-1])
        rows.append({**base, "first_red": str(a.date()), "days_vs_peak": (a - pk).days, "carried": bool(carried),
                     "from_peak_pct": round((s_then / s_pk - 1) * 100, 1), "further_pct": round((s_tr / s_then - 1) * 100, 1)})
    return rows


def log_section(cc: str) -> dict:
    """What the stored daily log (Local_Watch_Log) holds so far — the only record of breadth."""
    from database.connection import get_db
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT Log_Date, Overall, Trend, Breadth, Banks, Sovereign FROM Local_Watch_Log WHERE Country = %s ORDER BY Log_Date", (cc,))
        rows = cur.fetchall()
    if not rows:
        return {"days": 0, "ready": False, "ready_days": 180}
    ind = ("trend", "breadth", "banks", "sovereign")
    lv = {x: sum(1 for r in rows if r[1] == x) for x in ("good", "warn", "bad")}
    return {"days": len(rows), "first": str(rows[0][0]), "last": str(rows[-1][0]), "ready": len(rows) >= 180, "ready_days": 180, "banner_days": lv,
            "indicator_red_days": {k: sum(1 for r in rows if r[2 + i] == "bad") for i, k in enumerate(ind)},
            "two_or_more_red_days": sum(1 for r in rows if sum(1 for v in r[2:6] if v == "bad") >= 2)}


def run_backtest() -> dict:
    cc = home_country()
    p = get_profile(cc)
    if not p:
        raise RuntimeError("Choose a country with a profile under Tools → App Settings → Country of Usage first.")
    idx = _hist(p["index"])
    # the banks' basket: the mean of the banks' daily returns (those that traded that day), compounded
    rets = pd.concat({t: _hist(t).pct_change(fill_method=None) for t in p["banks"]}, axis=1).dropna(how="all")
    rets = rets.where(rets.abs() < 0.5)                    # drop a split/reverse-split artefact the adjusted series still shows
    basket = (1 + rets.mean(axis=1).fillna(0)).cumprod() * 100
    basket = basket[basket.index >= rets.dropna(how="all").index[0]]
    flags = {"trend": trend_red(idx), "banks": banks_red(basket).reindex(idx.index).ffill().fillna(False).astype(bool)}
    have_sov = False
    key = ENV_CONFIG.get("fred_api_key", "")
    if p.get("euro", True) and p.get("fred") and key:
        spread = (_fred_all(f"IRLTLT01{p['fred']}M156N") - _fred_all("IRLTLT01DEM156N")).dropna()
        flags["sovereign"] = sovereign_red(spread, idx.index)
        have_sov = True
    flags["trend"] = flags["trend"].astype(bool)
    stack = pd.concat(flags, axis=1).astype(int)
    flags["banner"] = (stack.sum(axis=1) >= 2)
    eps = drawdown_episodes(idx)
    out_rules = {}
    for k, f in flags.items():
        f = f.reindex(idx.index).fillna(False).astype(bool)
        out_rules[k] = {"share_red_pct": round(float(f.mean()) * 100, 1), "share_red_10y_pct": round(float(f[f.index >= f.index[-1] - pd.DateOffset(years=10)].mean()) * 100, 1), "episodes": episode_rows(f, idx, eps), "spells": red_rows(f, idx)}
    res = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "country": cc, "name": p["name"], "index_name": p["index_name"], "rule_version": rule_version(),
        "ranges": {"index_from": str(idx.index[0].date()), "banks_from": str(basket.index[0].date()),
                   "sovereign_from": str(spread.index[0].date()) if have_sov else None, "sovereign_lag_months": LAG_MONTHS},
        "drawdowns": [{"peak": str(e["peak"].date()), "trough": str(e["trough"].date()), "depth_pct": -e["depth"]} for e in eps],
        "min_depth": MIN_DEPTH, "rules": out_rules, "log": log_section(cc),
        "banner_legs": [k for k in ("trend", "banks", "sovereign") if k in flags],
        "notes": ["Banks that no longer exist (merged or delisted) are missing from Yahoo, so the bank basket is survivors-only and flatters the banks.",
                  f"The sovereign spread is used with a {LAG_MONTHS}-month lag — the monthly figure is published about 6 weeks late." if have_sov else
                  "No sovereign-spread leg for this country (not a euro country, the euro benchmark itself, or no FRED key).",
                  "Breadth has no free history; the daily log will test it."],
    }
    return res


def _fred_all(series_id: str) -> pd.Series:
    """The whole FRED series (the live module only needs the last two years)."""
    import requests
    r = requests.get("https://api.stlouisfed.org/fred/series/observations", params={"series_id": series_id, "api_key": ENV_CONFIG["fred_api_key"], "file_type": "json"}, timeout=(8, 40))
    r.raise_for_status()
    obs = [(o["date"], float(o["value"])) for o in r.json()["observations"] if o["value"] not in ("", ".")]
    return pd.Series([v for _, v in obs], index=pd.to_datetime([d for d, _ in obs]))


def main() -> None:
    r = run_backtest()
    nl = chr(10)
    print(f"# Local Market Watch back-test — {r['name']} ({r['index_name']}){nl}")
    print(f"Index from {r['ranges']['index_from']}, banks from {r['ranges']['banks_from']}, sovereign from {r['ranges']['sovereign_from']}.{nl}")
    print(f"Major falls (≥{r['min_depth']:.0f}%):")
    for d in r["drawdowns"]:
        print(f"  {d['peak']} → {d['trough']}  {d['depth_pct']}%")
    for k, v in r["rules"].items():
        print(f"{nl}## {k}: red on {v['share_red_pct']}% of days")
        for e in v["episodes"]:
            print("  ", e)
    print(nl + "Notes: " + " ".join(r["notes"]))


if __name__ == "__main__":
    main()
