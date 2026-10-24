"""Does the Action score work? Back-test it against what prices did next.

From the UI (Reports → Securities Analysis → Action Backtest) or the command line:

    python -m data.action_backtest                       # the daily log (Action_History): the whole score, valuation / quality / analyst points included
    python -m data.action_backtest --price-only          # rebuild the price-driven points for every month-end since 2016
    python -m data.action_backtest --price-only --benchmark-id 278 --security 70

Two honest limits. The daily log only starts the day Action_History began filling, so the 3-, 6- and 12-month verdicts arrive as that history ages.
The price-only mode has years of data but can only test the points prices alone give (trend, moving-average direction, trailing stop, Sharpe,
momentum) — fair value, analyst view and F-Score have no history. It is also biased upward: it covers the securities you follow today, not the
ones you dropped. Compare scores with each other, not with the benchmark in absolute terms.

Excess = the security's forward return in EUR minus the benchmark's over the same window (the benchmark is any security, set under Tools → App Settings
→ Benchmark, or "the average of what is tested" — pure selection skill); hit = share of cases that beat it.
"""
from __future__ import annotations

import argparse
import threading
import time
import uuid
import warnings

import numpy as np
import pandas as pd

from database import action_signal as A
from database.connection import get_connection

warnings.filterwarnings("ignore")
FALLBACK_BENCH_TICKER = "VWCE.DE"
HORIZONS = {"3m": 63, "6m": 126, "12m": 252}
RFR = 1.5          # % — only used to rebuild Sharpe in the price-only mode
UNIVERSE_TYPES = ("Stock", "ETF")


# ── data ──────────────────────────────────────────────────────────────────────────────────────────

def _q(sql: str, params=None) -> pd.DataFrame:
    conn = get_connection()
    try:
        return pd.read_sql(sql, conn, params=params)
    finally:
        conn.close()


def _load_prices(ids: list[int], since: str):
    px = _q("SELECT Securities_Id AS sid, Date AS d, Close AS c FROM Historical_Prices WHERE Securities_Id = ANY(%s) AND Date >= %s ORDER BY Securities_Id, Date", (ids, since))
    fx = _q("SELECT Currencies_Id_1 AS cid, Date AS d, FX_Rate AS r FROM Historical_FX WHERE Date >= %s ORDER BY Currencies_Id_1, Date", (since,))
    meta = _q("SELECT Securities_Id AS sid, Currencies_Id AS cid FROM Securities WHERE Securities_Id = ANY(%s)", (ids,))
    eur_id = int(_q("SELECT Currencies_Id AS cid FROM Currencies WHERE Currencies_ShortName = 'EUR'").cid.iloc[0])
    fxs = {int(c): g.set_index(pd.to_datetime(g.d))["r"].astype(float) for c, g in fx.groupby("cid")}
    cid_of = {int(r.sid): int(r.cid) for r in meta.itertuples()}
    own, eurs = {}, {}
    for sid, grp in px.groupby("sid"):
        g = grp.set_index(pd.to_datetime(grp.d))["c"].astype(float)
        g = g[~g.index.duplicated()]
        c = cid_of.get(int(sid), eur_id)
        own[int(sid)] = g
        eurs[int(sid)] = g if c == eur_id or c not in fxs else (g * fxs[c].reindex(g.index, method="ffill")).dropna()
    return own, eurs


def default_benchmark_id() -> int | None:
    """The benchmark chosen under Tools → App Settings; VWCE when none is chosen."""
    from database.queries import _get_app_setting_lead_days
    chosen = _get_app_setting_lead_days("defaultBenchmarkId", 0)
    if chosen:
        return int(chosen)
    df = _q("SELECT Securities_Id AS sid FROM Securities WHERE Ticker = %s", (FALLBACK_BENCH_TICKER,))
    return int(df.sid.iloc[0]) if len(df) else None


def _universe(scope: str, account_ids: list[int] | None, securities_id: int | None) -> list[int]:
    if scope == "security":
        if not securities_id:
            raise ValueError("Choose a security.")
        return [int(securities_id)]
    if scope == "holdings":
        return [int(i) for i in _q("SELECT DISTINCT Securities_Id AS sid FROM Holdings WHERE Quantity > 0").sid]
    if scope == "accounts":
        if not account_ids:
            raise ValueError("Choose at least one account.")
        return [int(i) for i in _q("SELECT DISTINCT Securities_Id AS sid FROM Holdings WHERE Quantity > 0 AND Accounts_Id = ANY(%s)", (account_ids,)).sid]
    return [int(i) for i in _q("SELECT Securities_Id AS sid FROM Securities WHERE Is_Active AND Securities_Type::text = ANY(%s)", (list(UNIVERSE_TYPES),)).sid]


def _forward(eur: pd.Series, d: pd.Timestamp, n: int):
    i = eur.index.searchsorted(d)
    if i >= len(eur) or i + n >= len(eur):
        return np.nan
    return (eur.iloc[i + n] / eur.iloc[i] - 1) * 100


# ── samples ───────────────────────────────────────────────────────────────────────────────────────

def _price_samples(ids, own, eurs, start, say) -> pd.DataFrame:
    recs = []
    for k, sid in enumerate(ids):
        g, ge = own.get(sid), eurs.get(sid)
        if g is None or ge is None or len(g) < 420:
            continue
        if k % 25 == 0:
            say(f"Scoring {k + 1} of {len(ids)} securities…")
        daily = A._daily_frame(g, 20.0, RFR)
        idx = g.index
        for d in pd.Series(idx, index=idx).groupby([idx.year, idx.month]).last().values:
            if d < pd.Timestamp(start) or pd.isna(daily.loc[d, "ma200_chg_pct"]):
                continue
            pts = A.factors(daily.loc[d].to_dict())
            if pts:
                recs.append({"sid": sid, "d": d, "score": float(sum(p for p, _ in pts)), "level": np.nan,
                             **{"r" + k_: _forward(ge, d, n) for k_, n in HORIZONS.items()}})
    return pd.DataFrame(recs)


def _log_samples(ids, eurs, say) -> pd.DataFrame:
    h = _q("SELECT Securities_Id AS sid, Log_Date AS d, Level AS level, Score AS score FROM Action_History WHERE Securities_Id = ANY(%s)", (ids,))
    if h.empty:
        return h
    h["d"] = pd.to_datetime(h.d)
    h["score"] = h.score.astype(float)
    say(f"Reading {len(h)} logged verdicts…")
    for k, n in HORIZONS.items():
        h["r" + k] = [_forward(eurs[int(s)], d, n) if int(s) in eurs else np.nan for s, d in zip(h.sid, h.d)]
    return h


# ── summary ───────────────────────────────────────────────────────────────────────────────────────

def _clean(v):
    return None if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))) else round(float(v), 2)


def _stats(sub: pd.DataFrame) -> dict:
    out = {}
    for k in HORIZONS:
        s = sub["x" + k].dropna()
        out[k] = {"n": int(len(s)), "mean": _clean(s.mean()) if len(s) else None, "median": _clean(s.median()) if len(s) else None,
                  "hit": _clean((s > 0).mean() * 100) if len(s) else None}
    return out


def run(params: dict, progress=None) -> dict:
    say = progress or (lambda _m: None)
    mode = params.get("mode") or "price"
    scope = params.get("scope") or "all"
    start = params.get("start") or "2016-01-01"
    bench = params.get("benchmark_id")
    bench = default_benchmark_id() if bench is None else int(bench)       # 0 = the average of what is tested
    ids = _universe(scope, params.get("account_ids"), params.get("securities_id"))
    if not ids:
        raise ValueError("Nothing to test: no open positions in that scope.")
    if bench == 0 and len(ids) < 5:
        raise ValueError("The average of what is tested needs at least 5 securities — choose a benchmark security instead.")
    say("Loading prices…")
    all_ids = sorted(set(ids) | ({bench} if bench else set()))
    own, eurs = _load_prices(all_ids, "2012-01-01" if mode == "price" else str((pd.Timestamp.today() - pd.Timedelta(days=900)).date()))
    df = _price_samples(ids, own, eurs, start, say) if mode == "price" else _log_samples(ids, eurs, say)
    notes = []
    if df.empty:
        return {"mode": mode, "scope": scope, "samples": 0, "securities": 0, "groups": [], "by_score": [], "timeline": [], "benchmark": None,
                "notes": ["Nothing to report yet: " + ("Action_History is empty — it fills with every scheduler refresh." if mode == "log" else "not enough price history.")]}
    say("Comparing with the benchmark…")
    if bench:
        b = eurs.get(bench)
        if b is None:
            raise ValueError("The benchmark has no prices.")
        for k, n in HORIZONS.items():
            bm = {d: _forward(b, d, n) for d in df.d.unique()}
            df["x" + k] = df["r" + k] - df.d.map(bm)
        bname = _q("SELECT Securities_Name AS n FROM Securities WHERE Securities_Id = %s", (bench,)).n.iloc[0]
        benchmark = {"id": bench, "name": bname}
    else:
        for k in HORIZONS:
            df["x" + k] = df["r" + k] - df.groupby("d")["r" + k].transform("mean")
        benchmark = {"id": 0, "name": "the average of the securities tested"}
    if mode == "log" and not any(df["x" + k].notna().any() for k in HORIZONS):
        return {"mode": mode, "scope": scope, "samples": int(len(df)), "securities": int(df.sid.nunique()), "from": str(df.d.min().date()), "to": str(df.d.max().date()),
                "groups": [], "by_score": [], "timeline": [], "benchmark": benchmark,
                "notes": [f"Too early: no logged day has {min(HORIZONS.values())} trading days of prices after it yet. The log is {df.d.nunique()} day(s) old; "
                          "the first 3-month result appears when it is 3 months old. Use the price-driven mode for a ten-year view now."]}
    if mode == "price":
        groups = [("score ≥ 6", df.score >= 6), ("score 5 – 5.9", df.score.between(5, 5.99)), ("score 3 – 4.9", df.score.between(3, 4.99)),
                  ("score 1 – 2.9", df.score.between(1, 2.99)), ("score ≤ 0", df.score <= 0), ("All", df.score > -99)]
        notes += ["Only the price-driven points are tested (trend, moving-average direction, trailing stop, Sharpe, momentum): fair value, analyst view and F-Score have no history.",
                  "The securities are the ones you follow today, not the ones you dropped — results lean upward; compare the groups with each other."]
    else:
        names = {4: "Add / Buy", 3: "Hold-Add / Buy", 2: "Hold / Watch", 1: "Reduce / Avoid", 0: "Sell / Avoid"}
        groups = [(f"{names[l]} (level {l})", df.level == l) for l in (4, 3, 2, 1, 0)] + [("score ≥ 6", df.score >= 6), ("All", df.score > -99)]
        notes.append("The daily log: the whole score as it stood each day, including valuation, quality and analyst points.")
    res_groups = [{"name": n, "n": int(m.sum()), "h": _stats(df[m])} for n, m in groups if int(m.sum())]
    by_score = []
    for s in sorted(df.score.round().clip(-5, 8).unique()):
        sub = df[df.score.round().clip(-5, 8) == s]
        by_score.append({"score": int(s), "n": int(len(sub)), "h": _stats(sub)})
    timeline = []
    if scope == "security":
        for r in df.sort_values("d").itertuples():
            timeline.append({"date": str(r.d.date()), "score": _clean(r.score), **{k: _clean(getattr(r, "x" + k)) for k in HORIZONS}})
        notes.append("Excess is the security's forward return minus the benchmark's, in EUR, from each date.")
    return {"mode": mode, "scope": scope, "samples": int(len(df)), "securities": int(df.sid.nunique()), "from": str(df.d.min().date()), "to": str(df.d.max().date()),
            "benchmark": benchmark, "groups": res_groups, "by_score": by_score, "timeline": timeline, "notes": notes}


# ── background jobs (the UI starts one and polls it) ──────────────────────────────────────────────

_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def start_job(params: dict) -> str:
    jid = uuid.uuid4().hex[:12]
    with _LOCK:
        for old in [k for k, v in _JOBS.items() if time.time() - v["started"] > 3600]:
            _JOBS.pop(old, None)
        _JOBS[jid] = {"status": "running", "stage": "Starting…", "started": time.time()}

    def work():
        try:
            result = run(params, lambda m: _JOBS[jid].update(stage=m))
            _JOBS[jid].update(status="done", result=result, stage="Done")
        except Exception as e:  # shown to the user, not raised
            _JOBS[jid].update(status="error", error=str(e), stage="Failed")
    threading.Thread(target=work, daemon=True).start()
    return jid


def job_status(jid: str) -> dict | None:
    j = _JOBS.get(jid)
    return None if j is None else {k: v for k, v in j.items() if k != "started"} | {"seconds": round(time.time() - j["started"])}


# ── command line ──────────────────────────────────────────────────────────────────────────────────

def _print(res: dict) -> None:
    print(f"{res['mode']} · {res['samples']} samples, {res.get('securities', 0)} securities, {res.get('from', '')} → {res.get('to', '')}; benchmark: {(res.get('benchmark') or {}).get('name')}")
    for n in res["notes"]:
        print("  -", n)
    if not res["groups"]:
        return
    print(f"\n{'group':<22}{'n':>8} | " + " | ".join(f"{k:>3} mean / median   hit%" for k in HORIZONS))
    for g in res["groups"]:
        cells = [f"{g['h'][k]['mean']:6.1f} / {g['h'][k]['median']:6.1f}  {g['h'][k]['hit']:5.1f}" if g["h"][k]["n"] else "      not yet" for k in HORIZONS]
        print(f"{g['name']:<22}{g['n']:>8} | " + " | ".join(f"{c:>26}" for c in cells))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--price-only", action="store_true", help="rebuild the price-driven points for every month-end since 2016 instead of reading the daily log")
    ap.add_argument("--benchmark-id", type=int, default=None, help="security id of the benchmark (0 = the average of what is tested); default: Tools → App Settings, else VWCE")
    ap.add_argument("--security", type=int, default=None, help="test one security")
    args = ap.parse_args()
    _print(run({"mode": "price" if args.price_only else "log", "scope": "security" if args.security else "all", "securities_id": args.security,
                "benchmark_id": args.benchmark_id}, lambda m: print("…", m)))


if __name__ == "__main__":
    main()
