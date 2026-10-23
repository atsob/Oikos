"""Action: one rules-based verdict per security from everything Portfolio Action Signals knows.

Each factor adds or removes points (trend, momentum, valuation, fundamentals — see `factors`); the total is
smoothed and turned into one of five levels. Alerting needs a verdict that doesn't flap, so:

  * the score is the 5-day average of the daily score (the daily score is rebuilt from the price history),
  * a level changes only when the average passes a cut by a full point, and stays past it three days running,
  * a triggered trailing stop or a Death Cross regime never reads as a buy (capped at Hold).

Back-test on the held securities (150 trading days): the raw score changed verdict ~12 times per security
(about 125 times a month across the book); smoothed + band + 3-day confirmation ~2 times (~22 a month).

The result is stored in Action_Signals (level, smoothed score, the reasons). A held security whose level moves
between the three outer groups — Reduce / Sell, Hold, Add — is recorded as an unacknowledged change, which the
Dashboard shows as an "Action Change" alert (Signals group), same mechanism as Signal_Notifications.

It is a transparent screen to review, not advice.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from database.connection import get_connection

log = logging.getLogger(__name__)

FLAT_PCT = 0.05          # a moving average within ±0.05% over 5 days counts as flat — keep in step with MA_FLAT_PCT in Reports.tsx
DIR_DAYS = 5
SMOOTH_DAYS = 5
CONFIRM_DAYS = 3
BAND = 1.0               # points past a cut needed to move to the next level
HISTORY_DAYS = 150       # trading days the state machine is run over
CUTS = [-5.5, -2.5, 2.5, 5.5]

HELD_LABELS = ["Sell", "Reduce", "Hold", "Hold / Add", "Add"]
OPEN_LABELS = ["Avoid", "Avoid", "Watch", "Buy", "Buy"]
BUCKETS = ["Reduce / Sell", "Reduce / Sell", "Hold", "Hold", "Add"]


def label(level: int | None, held: bool) -> str | None:
    if level is None or pd.isna(level):
        return None
    return (HELD_LABELS if held else OPEN_LABELS)[int(level)]


def bucket(level: int | None) -> str | None:
    if level is None or pd.isna(level):
        return None
    return BUCKETS[int(level)]


def _dir(v) -> str | None:
    if v is None or pd.isna(v):
        return None
    return "up" if v > FLAT_PCT else "down" if v < -FLAT_PCT else "flat"


def _num(v):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else v


def _flag(v) -> bool | None:
    return None if v is None or (not isinstance(v, (bool, np.bool_)) and pd.isna(v)) else bool(v)


def factors(r: dict) -> list[tuple[int, str]]:
    """The points behind a verdict, from one day's figures (r: dict of the signal fields)."""
    pts: list[tuple[int, str]] = []
    add = lambda p, w: pts.append((p, w))
    mt = _num(r.get("ma_trend"))
    if mt == "Golden": add(1, "Golden Cross regime")
    elif mt == "Death": add(-1, "Death Cross regime")
    a200 = _flag(r.get("above_ma200"))
    if a200 is True: add(1, "above MA200")
    elif a200 is False: add(-1, "below MA200")
    d50, d200 = _dir(_num(r.get("ma50_chg_pct"))), _dir(_num(r.get("ma200_chg_pct")))
    if d50 == "up": add(1, "MA50 rising")
    elif d50 == "down": add(-1, "MA50 falling")
    if d200 == "up": add(1, "MA200 rising")
    elif d200 == "down": add(-1, "MA200 falling")
    ce = _num(r.get("ma_cross_event"))
    if ce == "Golden Cross": add(1, "fresh Golden Cross")
    elif ce == "Death Cross": add(-2, "fresh Death Cross")
    if _flag(r.get("trailing_stop_triggered")) is True: add(-2, "trailing stop triggered")

    sh, q = _num(r.get("sharpe_ratio")), _num(r.get("quality_score"))
    if sh is not None:
        if sh >= 1: add(1, f"Sharpe {sh:.2f}")
        elif sh < 0: add(-1, f"negative Sharpe {sh:.2f}")
    if q is not None:
        if q > 8: add(1, "strong momentum")
        elif q < -5: add(-1, "weak momentum")

    fv, up = _num(r.get("fair_value_upside_pct")), _num(r.get("upside_pct"))
    if fv is not None:
        if fv > 20: add(1, f"{fv:.0f}% below fair value")
        elif fv < -20: add(-1, f"{abs(fv):.0f}% above fair value")
    if up is not None:
        if up > 15: add(1, f"analyst target +{up:.0f}%")
        elif up < -5: add(-1, f"analyst target {up:.0f}%")
    v = _num(r.get("wall_street_view"))
    if v in ("buy", "strong_buy"): add(1, f"analysts: {str(v).replace('_', ' ')}")
    elif v in ("sell", "underperform"): add(-1, f"analysts: {v}")

    f = _num(r.get("f_score"))
    if f is not None:
        fmax = _num(r.get("f_score_max")) or 9
        if f >= 7: add(1, f"F-Score {int(f)}/{int(fmax)}")
        elif f <= 2: add(-1, f"F-Score {int(f)}/{int(fmax)}")
    if r.get("z_zone") == "Distress": add(-1, "Altman Z in Distress zone")
    return pts


def why_text(pts: list[tuple[int, str]]) -> str:
    return " · ".join(f"{'+' if p > 0 else '−'}{abs(p)} {w}" for p, w in sorted(pts, key=lambda x: -abs(x[0])))


def _level(x: float) -> int:
    return sum(x > c for c in CUTS)


def settle(scores: pd.Series) -> tuple[float, int]:
    """(smoothed score today, level today) from a daily score series: 5-day average, then a band and a 3-day confirmation
    before the level moves. Deterministic over the series it is given."""
    sm = scores.rolling(SMOOTH_DAYS, min_periods=1).mean()
    cur, pend, cnt = _level(sm.iloc[0]), None, 0
    for x in sm.iloc[1:]:
        tgt = _level(x)
        if tgt > cur and x >= CUTS[tgt - 1] + BAND: want = tgt
        elif tgt < cur and x <= CUTS[tgt] - BAND: want = tgt
        else: want = cur
        if want != cur:
            if pend == want: cnt += 1
            else: pend, cnt = want, 1
            if cnt >= CONFIRM_DAYS: cur, pend, cnt = want, None, 0
        else:
            pend, cnt = None, 0
    return float(sm.iloc[-1]), cur


def _daily_frame(g: pd.Series, stop_pct: float, rfr: float) -> pd.DataFrame:
    """The price-driven figures for every day of one security's history (g: closes indexed by date)."""
    ma50, ma200 = g.rolling(50).mean(), g.rolling(200).mean()
    ann = (g / g.shift(252) - 1) * 100
    vol = g.pct_change().rolling(252).std() * np.sqrt(252) * 100
    hi = g.rolling(252).max()
    ok200 = ma200.notna()
    prev50, prev200 = ma50.shift(), ma200.shift()
    cross = pd.Series([None] * len(g), index=g.index, dtype=object)
    cross[(prev50 <= prev200) & (ma50 > ma200) & ok200 & prev200.notna()] = "Golden Cross"
    cross[(prev50 >= prev200) & (ma50 < ma200) & ok200 & prev200.notna()] = "Death Cross"
    return pd.DataFrame({
        "ma_trend": np.where(ok200, np.where(ma50 > ma200, "Golden", "Death"), None),
        "above_ma200": np.where(ok200, g > ma200, None),
        "ma50_chg_pct": (ma50 / ma50.shift(DIR_DAYS) - 1) * 100,
        "ma200_chg_pct": (ma200 / ma200.shift(DIR_DAYS) - 1) * 100,
        "ma_cross_event": cross,
        "trailing_stop_triggered": np.where(hi.notna(), g < hi * (1 - stop_pct / 100), None),
        "sharpe_ratio": (ann - rfr) / vol.replace(0, np.nan),
        "quality_score": 0.5 * (g / g.shift(21) - 1) * 100 + 0.3 * (g / g.shift(63) - 1) * 100 + 0.2 * ann,
    }, index=g.index)


def compute_all() -> pd.DataFrame:
    """Level, smoothed score and reasons for every active security with a price history.
    Columns: securities_id, level, score, why."""
    from database.queries import get_portfolio_signals, get_fundamental_scores, _get_trailing_stop_pct
    sig = get_portfolio_signals(None)
    if sig is None or sig.empty:
        return pd.DataFrame(columns=["securities_id", "level", "score", "why"])
    stop_pct = _get_trailing_stop_pct()
    fs = get_fundamental_scores()
    fsm = fs.set_index("securities_id")[["f_score", "f_score_max", "z_zone"]].to_dict("index") if fs is not None and len(fs) else {}
    # the risk-free rate the Sharpe column used, recovered from any row: sharpe = (annual - rfr) / vol
    implied = (sig["annual_chg_pct"] - sig["sharpe_ratio"] * sig["vol_1y_ann"]).dropna()
    rfr = float(implied.median()) if len(implied) else 2.0

    ids = [int(i) for i in sig["securities_id"]]
    conn = get_connection()
    try:
        px = pd.read_sql("SELECT Securities_Id AS sid, Date AS d, Close AS c FROM Historical_Prices "
                         "WHERE Securities_Id = ANY(%s) AND Date >= CURRENT_DATE - INTERVAL '4 years' ORDER BY Securities_Id, Date",
                         conn, params=(ids,))
    finally:
        conn.close()
    prices = {int(sid): grp.set_index("d")["c"].astype(float) for sid, grp in px.groupby("sid")}

    out = []
    for _, r in sig.iterrows():
        sid = int(r["securities_id"])
        g = prices.get(sid)
        if g is None or len(g) < 60:
            continue
        base = r.to_dict()
        base.update(fsm.get(sid, {}))
        daily = _daily_frame(g, stop_pct, rfr).tail(HISTORY_DAYS + 5)
        scores, last_pts, last = [], [], None
        for i in range(len(daily)):
            rec = {**base, **{k: daily[k].iloc[i] for k in daily.columns}}
            pts = factors(rec)
            scores.append(sum(p for p, _ in pts) if pts else np.nan)
            last_pts, last = pts, rec
        s = pd.Series(scores).dropna()
        if len(s) < 5 or not last_pts:
            continue
        score, level = settle(s.tail(HISTORY_DAYS))
        gate = _flag(last.get("trailing_stop_triggered")) is True or last.get("ma_trend") == "Death"
        if gate and level >= 3:
            level = 2
        out.append({"securities_id": sid, "level": int(level), "score": round(score, 1), "why": why_text(last_pts)})
    return pd.DataFrame(out, columns=["securities_id", "level", "score", "why"])


# ── storage and change tracking ──────────────────────────────────────────────────────────────────

def ensure_table() -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS Action_Signals (
                    Securities_Id    INTEGER PRIMARY KEY REFERENCES Securities(Securities_Id) ON DELETE CASCADE,
                    Level            SMALLINT NOT NULL,
                    Score            NUMERIC(6,1),
                    Why              TEXT,
                    Bucket           TEXT,
                    Previous_Bucket  TEXT,
                    Changed_At       TIMESTAMP DEFAULT NOW(),
                    Acknowledged     BOOLEAN DEFAULT TRUE,
                    Updated_At       TIMESTAMP DEFAULT NOW()
                )""")
        conn.commit()
    finally:
        conn.close()


def refresh_action_signals() -> int:
    """Recompute every security's Action and record bucket changes. Called by the scheduler after the market-data refresh
    (right after refresh_signal_notifications). New securities are seeded silently. Returns how many changed bucket."""
    ensure_table()
    res = compute_all()
    if res.empty:
        return 0
    conn = get_connection()
    changed = 0
    try:
        stored = pd.read_sql("SELECT Securities_Id AS sid, Bucket AS bucket FROM Action_Signals", conn)
        stored_map = dict(zip(stored["sid"], stored["bucket"]))
        with conn.cursor() as cur:
            for _, r in res.iterrows():
                sid, lvl = int(r["securities_id"]), int(r["level"])
                b = bucket(lvl)
                prev = stored_map.get(sid)
                if prev is None:
                    cur.execute("INSERT INTO Action_Signals (Securities_Id, Level, Score, Why, Bucket, Acknowledged) "
                                "VALUES (%s,%s,%s,%s,%s,TRUE) ON CONFLICT (Securities_Id) DO NOTHING", (sid, lvl, float(r["score"]), r["why"], b))
                elif prev != b:
                    changed += 1
                    cur.execute("UPDATE Action_Signals SET Level=%s, Score=%s, Why=%s, Bucket=%s, Previous_Bucket=%s, "
                                "Changed_At=NOW(), Acknowledged=FALSE, Updated_At=NOW() WHERE Securities_Id=%s",
                                (lvl, float(r["score"]), r["why"], b, prev, sid))
                else:
                    cur.execute("UPDATE Action_Signals SET Level=%s, Score=%s, Why=%s, Updated_At=NOW() WHERE Securities_Id=%s",
                                (lvl, float(r["score"]), r["why"], sid))
        conn.commit()
    finally:
        conn.close()
    return changed


def action_map() -> dict[int, dict]:
    """Stored verdicts by security id: level, score, why."""
    try:
        ensure_table()
        conn = get_connection()
        try:
            df = pd.read_sql("SELECT Securities_Id AS sid, Level AS level, Score AS score, Why AS why FROM Action_Signals", conn)
        finally:
            conn.close()
    except Exception as e:  # the grid must still load without it
        log.warning("Action_Signals unreadable: %s", e)
        return {}
    return {int(r.sid): {"level": int(r.level), "score": float(r.score) if r.score is not None else None, "why": r.why} for r in df.itertuples()}


def acknowledge(securities_id: int) -> None:
    ensure_table()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE Action_Signals SET Acknowledged = TRUE WHERE Securities_Id = %s", (securities_id,))
        conn.commit()
    finally:
        conn.close()


def pending_changes() -> pd.DataFrame:
    """Unacknowledged bucket changes of securities held now — the Dashboard's Action Change alerts."""
    ensure_table()
    conn = get_connection()
    try:
        return pd.read_sql("""
            SELECT a.Securities_Id AS securities_id, s.Securities_Name AS securities_name, a.Bucket AS bucket,
                   a.Previous_Bucket AS previous_bucket, a.Score AS score, a.Changed_At AS changed_at
            FROM Action_Signals a
            JOIN Securities s ON s.Securities_Id = a.Securities_Id
            WHERE a.Acknowledged = FALSE
              AND EXISTS (SELECT 1 FROM Holdings h WHERE h.Securities_Id = a.Securities_Id AND h.Quantity > 0)
            ORDER BY a.Changed_At DESC""", conn)
    finally:
        conn.close()


def action_rows() -> list[dict]:
    """Every security's stored Action verdict with what is needed to read it: name, ticker, whether and how much is held, the verdict worded for
    held / not held, the score, the reasons, the last move between the outer groups and the existing Final Signal. Held positions first,
    biggest first. Backs GET /api/reports/action-signals."""
    from database.queries import get_portfolio_signals
    ensure_table()
    conn = get_connection()
    try:
        st = pd.read_sql("""
            SELECT a.Securities_Id AS securities_id, s.Ticker AS ticker, a.Level AS level, a.Score AS action_score, a.Why AS action_why,
                   a.Bucket AS bucket, a.Previous_Bucket AS previous_bucket, a.Changed_At AS changed_at, a.Acknowledged AS acknowledged,
                   a.Updated_At AS updated_at
            FROM Action_Signals a JOIN Securities s ON s.Securities_Id = a.Securities_Id""", conn)
    finally:
        conn.close()
    if st.empty:
        return []
    sig = get_portfolio_signals(None)[["securities_id", "securities_name", "current_qty", "current_value_eur", "final_signal", "recommendation_signal"]]
    df = st.merge(sig, on="securities_id", how="left")
    out = []
    txt = lambda v: None if v is None or (isinstance(v, float) and pd.isna(v)) else v
    for r in df.to_dict("records"):
        qty = float(r["current_qty"]) if r.get("current_qty") is not None and not pd.isna(r["current_qty"]) else 0.0
        held = qty > 0
        val = float(r["current_value_eur"]) if r.get("current_value_eur") is not None and not pd.isna(r["current_value_eur"]) else 0.0
        out.append({
            "securities_id": int(r["securities_id"]), "name": txt(r.get("securities_name")), "ticker": txt(r.get("ticker")),
            "held": held, "quantity": qty if held else 0.0, "value_eur": round(val, 2),
            "action": label(int(r["level"]), held), "action_score": float(r["action_score"]) if r["action_score"] is not None and not pd.isna(r["action_score"]) else None,
            "level": int(r["level"]), "action_why": r["action_why"] or "",
            "bucket": txt(r["bucket"]), "previous_bucket": txt(r["previous_bucket"]),
            "changed_at": None if r["changed_at"] is None or pd.isna(r["changed_at"]) else pd.Timestamp(r["changed_at"]).isoformat(),
            "alert_pending": bool(r["acknowledged"] is False),
            "updated_at": None if r["updated_at"] is None or pd.isna(r["updated_at"]) else pd.Timestamp(r["updated_at"]).isoformat(),
            "final_signal": txt(r.get("final_signal")), "math_signal": txt(r.get("recommendation_signal")),
        })
    out.sort(key=lambda x: (not x["held"], -x["value_eur"], x["name"] or ""))
    return out
