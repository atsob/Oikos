"""Back-test of the Market Watch red rules that have a long history: credit spreads and the 10-year Treasury.
Free cash flow and breadth have no long free history; the stored daily log (Market_Watch_Log) will cover them from now on.

From the UI: Market Data -> Market Watch -> Back-test -> Run.   From a shell:  python -m data.market_watch_backtest
(both need FRED_API_KEY; the S&P 500 comes from Yahoo).

The rules are those of api/routers/market_watch.py — a red condition must hold 3 trading days in a row, and once red a signal
clears only when back past its margin — applied day by day to the series as they stood that day.

Note: FRED publishes the ICE BofA high-yield / investment-grade spreads (the live credit rule) for the last ~3 years only (ICE's
licence), so that rule can be tested on that window only. Moody's Baa minus the 10-year Treasury (FRED BAA10Y, from 1986) is
used as a long-history surrogate for the *speed* of widening.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import pandas as pd
import requests

from api.routers.market_watch import RULES
from config.settings import ENV_CONFIG

EPISODES = {                      # name: (S&P 500 peak, trough) — the index peak and the low that followed
    "2000-02 dot-com":  ("2000-03-24", "2002-10-09"),
    "2007-08 GFC":      ("2007-10-09", "2009-03-09"),
    "2020 Covid":       ("2020-02-19", "2020-03-23"),
    "2022 rate shock":  ("2022-01-03", "2022-10-12"),
    "2026-03 sell-off": ("2026-02-27", "2026-03-31"),
}


def fred(series_id: str, start: str = "1996-01-01") -> pd.Series:
    r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                     params={"series_id": series_id, "api_key": ENV_CONFIG["fred_api_key"], "file_type": "json", "observation_start": start},
                     timeout=(8, 40))
    r.raise_for_status()
    obs = [(o["date"], float(o["value"])) for o in r.json()["observations"] if o["value"] not in ("", ".")]
    return pd.Series([v for _, v in obs], index=pd.to_datetime([d for d, _ in obs]))


def persistent(red: pd.Series, clear: pd.Series, n: int = RULES["persist_days"]) -> pd.Series:
    """Red after `n` days of the red condition in a row; stays red until `clear` is true."""
    streak = red.astype(int).rolling(n).sum() == n
    out, on = [], False
    for r3, c in zip(streak.fillna(False), clear):
        on = True if r3 else (False if (on and c) else on)
        out.append(on)
    return pd.Series(out, index=red.index)


def credit_red(hy: pd.Series, ig: pd.Series) -> pd.Series:
    R = RULES["credit"]
    red = (hy.diff(20) >= R["red_hy_20d_pp"]) | (hy >= R["red_hy_level"]) | (hy.diff(60) >= R["red_hy_60d_pp"]) | (ig >= R["red_ig_level"])
    return persistent(red, (hy < R["clear_hy_level"]) & (ig < R["clear_ig_level"]))


def surrogate_red(baa: pd.Series) -> pd.Series:
    R = RULES["credit"]
    red = (baa.diff(20) >= R["surrogate_20d_pp"]) | (baa.diff(60) >= R["surrogate_60d_pp"])
    return persistent(red, baa.diff(20) < R["surrogate_clear_20d_pp"])


def ten_year_red(y: pd.Series) -> pd.Series:
    """Speed, not level: up 0.75 pp in 63 trading days, or above 5.5% and up 0.5 pp; clears when the 3-month rise is back under 0.5 pp."""
    R = RULES["ten_year"]
    c = y.diff(63)
    red = (c >= R["speed_pp_63d"]) | ((y > R["level"]) & (c >= R["level_speed_pp"]))
    return persistent(red, c < R["clear_speed_pp"])


def old_ten_year_red(y: pd.Series) -> pd.Series:
    """The first version, for comparison: above 5.5% and rising over 3 months."""
    return persistent((y > 5.5) & (y.diff(63) > 0), y < 5.3)


def episodes(flag: pd.Series) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    out, start = [], None
    for d, v in flag.items():
        if v and start is None:
            start = d
        if not v and start is not None:
            out.append((start, d))
            start = None
    if start is not None:
        out.append((start, flag.index[-1]))
    return out


def episode_rows(flag: pd.Series, spx: pd.Series) -> list[dict]:
    """For each named crash: when did the signal first go red, how far was the S&P 500 from its peak, how much further did it fall."""
    rows = []
    for name, (pk, tr) in EPISODES.items():
        pk, tr = pd.Timestamp(pk), pd.Timestamp(tr)
        w = flag[(flag.index >= pk - pd.Timedelta(days=120)) & (flag.index <= tr)]
        first = w[w].index.min() if w.any() else None
        if first is None:
            rows.append({"episode": name, "peak": str(pk.date()), "first_red": None, "days_vs_peak": None, "from_peak_pct": None, "further_pct": None})
            continue
        s_then, s_pk, s_tr = float(spx[:first].iloc[-1]), float(spx[:pk].iloc[-1]), float(spx[:tr].iloc[-1])
        rows.append({"episode": name, "peak": str(pk.date()), "first_red": str(first.date()), "days_vs_peak": (first - pk).days,
                     "from_peak_pct": round((s_then / s_pk - 1) * 100, 1), "further_pct": round((s_tr / s_then - 1) * 100, 1)})
    return rows


def red_rows(flag: pd.Series, spx: pd.Series) -> list[dict]:
    """Every red spell and what the S&P 500 did next."""
    rows = []
    for a, b in episodes(flag):
        s0 = float(spx[:a].iloc[-1])

        def fwd(days):
            t = a + pd.Timedelta(days=days)
            return round((float(spx[:t].iloc[-1]) / s0 - 1) * 100, 1) if t <= spx.index[-1] else None
        fut = spx[(spx.index >= a) & (spx.index <= a + pd.Timedelta(days=182))]
        rows.append({"from": str(a.date()), "until": str(b.date()), "days": (b - a).days, "spx": round(s0), "m3_pct": fwd(91), "m6_pct": fwd(182),
                     "worst_6m_pct": round((float(fut.min()) / s0 - 1) * 100, 1)})
    return rows


def log_section() -> dict:
    """What the stored daily log (Market_Watch_Log) says so far — the only record of free cash flow and breadth, and of the banner itself.
    Until it holds about 6 months it is a progress report; after that it is the first real test of the "two or more red" rule."""
    from database.connection import get_db
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT Log_Date, Overall, Fcf, Spreads, Ten_Year, Breadth FROM Market_Watch_Log ORDER BY Log_Date")
        rows = cur.fetchall()
    if not rows:
        return {"days": 0, "ready": False, "ready_days": 180}
    ind = ("fcf", "spreads", "ten_year", "breadth")
    red_ind = {k: sum(1 for r in rows if r[2 + i] == "bad") for i, k in enumerate(ind)}
    two = sum(1 for r in rows if sum(1 for v in r[2:6] if v == "bad") >= 2)
    spells, prev = 0, None
    for r in rows:
        spells += 1 if (r[1] == "bad" and prev != "bad") else 0
        prev = r[1]
    lv = {x: sum(1 for r in rows if r[1] == x) for x in ("good", "warn", "bad")}
    return {"days": len(rows), "first": str(rows[0][0]), "last": str(rows[-1][0]), "ready": len(rows) >= 180, "ready_days": 180,
            "banner_days": lv, "banner_red_spells": spells, "indicator_red_days": red_ind, "two_or_more_red_days": two}


def run_backtest() -> dict:
    """Everything as plain data, for the UI (and the markdown printout below)."""
    import yfinance as yf
    if not ENV_CONFIG.get("fred_api_key"):
        raise RuntimeError("The back-test needs a free FRED_API_KEY (see Help → Market Watch).")
    hy, ig, dgs, baa = fred("BAMLH0A0HYM2"), fred("BAMLC0A0CM"), fred("DGS10", "1990-01-01"), fred("BAA10Y", "1990-01-01")
    spx = yf.Ticker("^GSPC").history(period="max")["Close"]
    spx.index = pd.to_datetime(spx.index).tz_localize(None).normalize()
    idx = hy.index.intersection(ig.index)
    cr, sr, ty, ty_old = credit_red(hy.reindex(idx), ig.reindex(idx)), surrogate_red(baa), ten_year_red(dgs), old_ten_year_red(dgs)
    # the days on which the 10-year rule and the credit surrogate were red together — what the banner needs (two or more)
    both = (ty & sr.reindex(ty.index).fillna(False))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_date": str(spx.index[-1].date()),
        "ranges": {"spx_from": str(spx.index[0].date()), "ten_year_from": str(dgs.index[0].date()), "baa_from": str(baa.index[0].date()), "ice_from": str(hy.index[0].date())},
        "log": log_section(),
        "credit_live": {"share_red_pct": round(float(cr.mean()) * 100, 1), "spells": red_rows(cr, spx), "episodes": episode_rows(cr, spx)},
        "credit_surrogate": {"share_red_pct": round(float(sr.mean()) * 100, 1), "spells": red_rows(sr, spx), "episodes": episode_rows(sr, spx)},
        "ten_year": {"share_red_pct": round(float(ty.mean()) * 100, 1), "share_red_old_pct": round(float(ty_old.mean()) * 100, 1),
                     "spells": red_rows(ty, spx), "episodes": episode_rows(ty, spx), "episodes_old": episode_rows(ty_old, spx),
                     "both_with_credit_days": int(both.sum()), "both_with_credit_spells": red_rows(both, spx) if both.any() else []},
    }


def _md(rows: list[dict], cols: list[tuple[str, str]]) -> str:
    nl = chr(10)
    head = "| " + " | ".join(h for h, _ in cols) + " |" + nl + "|" + "---|" * len(cols)
    body = [("| " + " | ".join("—" if r.get(k) is None else str(r[k]) for _, k in cols) + " |") for r in rows]
    return head + nl + nl.join(body)


def main() -> None:
    nl = chr(10)
    r = run_backtest()
    ep = [("Episode", "episode"), ("Index peak", "peak"), ("Red first fired", "first_red"), ("Days vs the peak", "days_vs_peak"),
          ("S&P 500 vs its peak then (%)", "from_peak_pct"), ("Further fall to the trough (%)", "further_pct")]
    sp = [("Red from", "from"), ("Red until", "until"), ("Days", "days"), ("S&P 500", "spx"), ("3 months later (%)", "m3_pct"), ("6 months later (%)", "m6_pct"), ("Worst fall in 6 months (%)", "worst_6m_pct")]
    print("# Market Watch back-test" + nl)
    print(f"S&P 500 from {r['ranges']['spx_from']}; 10-year yield from {r['ranges']['ten_year_from']}; Moody's Baa spread from {r['ranges']['baa_from']}; "
          f"ICE BofA spreads only from {r['ranges']['ice_from']} (FRED licence)." + nl)
    print("## 1. Live credit rule (ICE BofA OAS) — the window FRED allows" + nl + _md(r["credit_live"]["spells"], sp) + nl)
    print("## 2. Credit surrogate (Moody's Baa − 10-year Treasury, speed only) — long history" + nl + _md(r["credit_surrogate"]["episodes"], ep) + nl)
    print(f"Red on {r['credit_surrogate']['share_red_pct']}% of days." + nl + _md(r["credit_surrogate"]["spells"], sp) + nl)
    print("## 3. 10-year yield rule (speed: +0.75 pp in 63 days, or above 5.5% and +0.5 pp)" + nl + _md(r["ten_year"]["episodes"], ep) + nl)
    print(f"Red on {r['ten_year']['share_red_pct']}% of days (the old level rule: {r['ten_year']['share_red_old_pct']}%)." + nl + _md(r["ten_year"]["spells"], sp) + nl)
    print(f"Days with the 10-year rule and the credit surrogate red together: {r['ten_year']['both_with_credit_days']}")


if __name__ == "__main__":
    main()
