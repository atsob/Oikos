"""Interest-rate series, their history, and the review alerts built on them.

Normalised the same way securities and prices are:
  Rate_Series      one row per rate (€STR, SOFR, ECB deposit rate, ...) — definition, currency,
                   type, and where it is downloaded from (provider + key). Add a row to track
                   another rate, in any currency; no code change needed.
  Historical_Rates one row per series per day (Rate_Series_Id, Date, Rate_Pct), like Historical_Prices.
  Rate_Tracking    which fund (a Securities row) to compare against which overnight rate series,
                   with its own window and alert threshold — XEON vs €STR by default.

Providers (see data/downloaders.py::download_interest_rates): ECB (ECB Data Portal SDMX key),
NYFED (NY Fed Markets API path + optional field), FRED (series id; needs FRED_API_KEY), MANUAL
(values typed in on Market Data -> Rates).

The tables and the default series are created at startup (database/connection.py) and for a new
install by database/Oikos.sql, so nothing here creates schema.
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import psycopg2.errors
from psycopg2.extras import execute_values

from database.connection import get_db

RATE_TYPES = ("Overnight", "Policy", "Target Upper", "Target Lower", "Other")
PROVIDERS = ("ECB", "NYFED", "FRED", "MANUAL")

# A policy-rate change stays an active insight for this many days.
CHANGE_ALERT_DAYS = 14
# Overnight rate vs. its trailing average, in percentage points.
MOVE_ALERT_PP = 0.15
MOVE_AVG_DAYS = 30

# What to look at after a rate change, by currency of the rate. Anything else gets the generic line.
_REVIEW_HINTS = {
    "EUR": "cash / money-market vs. short-bond split, bond-fund duration and any T-bill rollover maturities",
    "USD": "USD holdings (US stocks, USD cash/T-bills), EUR/USD exposure and gold",
}


# ── Definitions ───────────────────────────────────────────────────────────────

def get_series_defs(active_only: bool = False) -> pd.DataFrame:
    with get_db() as conn:
        return pd.read_sql(f"""
            SELECT r.Rate_Series_Id AS id, r.Code AS code, r.Name AS name,
                   r.Currencies_Id AS currencies_id, c.Currencies_ShortName AS currency,
                   r.Rate_Type AS rate_type, r.Provider AS provider, r.Provider_Key AS provider_key,
                   r.Provider_Field AS provider_field, r.Is_Active AS is_active,
                   r.Alerts_Enabled AS alerts_enabled, r.Sort_Order AS sort_order, r.Notes AS notes,
                   (SELECT MAX(h.Date) FROM Historical_Rates h WHERE h.Rate_Series_Id = r.Rate_Series_Id) AS last_date,
                   (SELECT COUNT(*) FROM Historical_Rates h WHERE h.Rate_Series_Id = r.Rate_Series_Id) AS row_count
            FROM Rate_Series r LEFT JOIN Currencies c ON c.Currencies_Id = r.Currencies_Id
            {"WHERE r.Is_Active" if active_only else ""}
            ORDER BY r.Sort_Order, r.Code
        """, conn)


def upsert_series(data: dict) -> int:
    """Insert (no id) or update (id given) a series definition. Returns its id."""
    code = (data.get("code") or "").strip().upper()
    name = (data.get("name") or "").strip()
    if not code or not name:
        raise ValueError("Code and name are required")
    rate_type = data.get("rate_type") or "Overnight"
    provider = data.get("provider") or "MANUAL"
    if rate_type not in RATE_TYPES:
        raise ValueError(f"rate_type must be one of {RATE_TYPES}")
    if provider not in PROVIDERS:
        raise ValueError(f"provider must be one of {PROVIDERS}")
    if provider != "MANUAL" and not (data.get("provider_key") or "").strip():
        raise ValueError(f"{provider} series need a provider key")
    params = (
        code, name, data.get("currencies_id") or None, rate_type, provider,
        (data.get("provider_key") or "").strip() or None,
        (data.get("provider_field") or "").strip() or None,
        bool(data.get("is_active", True)), bool(data.get("alerts_enabled", True)),
        int(data.get("sort_order") or 100), (data.get("notes") or "").strip() or None,
    )
    with get_db() as conn:
        cur = conn.cursor()
        if data.get("id"):
            cur.execute("""
                UPDATE Rate_Series SET Code=%s, Name=%s, Currencies_Id=%s, Rate_Type=%s, Provider=%s,
                       Provider_Key=%s, Provider_Field=%s, Is_Active=%s, Alerts_Enabled=%s, Sort_Order=%s, Notes=%s
                WHERE Rate_Series_Id=%s RETURNING Rate_Series_Id
            """, params + (int(data["id"]),))
        else:
            cur.execute("""
                INSERT INTO Rate_Series (Code, Name, Currencies_Id, Rate_Type, Provider, Provider_Key,
                                         Provider_Field, Is_Active, Alerts_Enabled, Sort_Order, Notes)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING Rate_Series_Id
            """, params)
        row = cur.fetchone()
        if not row:
            raise LookupError("Rate series not found")
        return int(row[0])


def delete_series(series_id: int) -> bool:
    """Deletes the definition and (ON DELETE CASCADE) its history and tracking rows."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM Rate_Series WHERE Rate_Series_Id = %s", (series_id,))
        return cur.rowcount > 0


# ── Values ────────────────────────────────────────────────────────────────────

def upsert_rates(rows: list) -> int:
    """Bulk upsert [(rate_series_id, date, rate_pct, source), ...]. Returns rows written."""
    if not rows:
        return 0
    with get_db() as conn:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO Historical_Rates (Rate_Series_Id, Date, Rate_Pct, Source, Downloaded_At)
                VALUES %s
                ON CONFLICT (Rate_Series_Id, Date) DO UPDATE
                    SET Rate_Pct = EXCLUDED.Rate_Pct, Source = EXCLUDED.Source, Downloaded_At = NOW()
            """, rows, template="(%s, %s, %s, %s, NOW())")
    return len(rows)


def delete_rate_value(series_id: int, day: str) -> bool:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM Historical_Rates WHERE Rate_Series_Id = %s AND Date = %s", (series_id, day))
        return cur.rowcount > 0


def get_rate_series(codes: list[str] | None = None, years: int | None = None) -> pd.DataFrame:
    where, params = [], {}
    if codes:
        where.append("r.Code = ANY(%(codes)s)")
        params["codes"] = codes
    if years:
        where.append("h.Date >= CURRENT_DATE - (%(yrs)s || ' years')::INTERVAL")
        params["yrs"] = years
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with get_db() as conn:
        return pd.read_sql(f"""
            SELECT r.Code AS series, h.Date AS date, h.Rate_Pct::float AS rate
            FROM Historical_Rates h JOIN Rate_Series r ON r.Rate_Series_Id = h.Rate_Series_Id
            {clause} ORDER BY r.Code, h.Date
        """, conn, params=params or None)


# ── Tracking (fund vs. overnight rate) ────────────────────────────────────────

# A bond fund this long in duration moves with bond yields, not with an overnight rate, so
# comparing it with one (the point of Rate_Tracking) is meaningless.
NOT_CASH_LIKE_DURATION_YEARS = 1.0


def get_fund_durations() -> dict:
    """{securities_id: duration_years} for bond-heavy funds (>= 50% bonds) whose duration is above
    NOT_CASH_LIKE_DURATION_YEARS — i.e. funds not worth tracking against an overnight rate. A manual
    Bond_Duration override wins over Yahoo's figure, which is often wrong for UCITS ETFs. Equity
    funds are excluded: Yahoo reports a placeholder duration for them too."""
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT Securities_Id AS id,
                   COALESCE((Manual_Overrides->>'Bond_Duration')::float, Bond_Duration::float) AS duration
            FROM Fund_Composition
            WHERE COALESCE(Asset_Bond_Pct, 0) >= 0.5
        """, conn)
    df = df[df["duration"].notna() & (df["duration"] > NOT_CASH_LIKE_DURATION_YEARS)]
    return {int(r.id): float(r.duration) for r in df.itertuples()}


def get_tracking_defs() -> pd.DataFrame:
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT t.Rate_Tracking_Id AS id, t.Securities_Id AS securities_id, s.Ticker AS ticker,
                   s.Securities_Name AS security_name, t.Rate_Series_Id AS rate_series_id,
                   r.Code AS series_code, r.Name AS series_name,
                   t.Window_Days AS window_days, t.Alert_Threshold_PP::float AS threshold_pp, t.Is_Active AS is_active
            FROM Rate_Tracking t
            JOIN Securities s ON s.Securities_Id = t.Securities_Id
            JOIN Rate_Series r ON r.Rate_Series_Id = t.Rate_Series_Id
            ORDER BY s.Ticker, r.Code
        """, conn)
    durations = get_fund_durations()
    df["duration_years"] = df["securities_id"].map(durations)
    return df


def upsert_tracking(data: dict) -> int:
    window = int(data.get("window_days") or 90)
    threshold = float(data.get("threshold_pp") if data.get("threshold_pp") not in (None, "") else 0.25)
    if window < 7:
        raise ValueError("Window must be at least 7 days")
    if not data.get("securities_id") or not data.get("rate_series_id"):
        raise ValueError("A fund and a rate series are both required")
    params = (int(data["securities_id"]), int(data["rate_series_id"]), window, threshold, bool(data.get("is_active", True)))
    with get_db() as conn:
        cur = conn.cursor()
        if data.get("id"):
            # Editing an existing row — including swapping its fund or rate — must replace that row,
            # not add another one beside it (the upsert below keys on the fund/rate pair).
            try:
                cur.execute("""
                    UPDATE Rate_Tracking SET Securities_Id = %s, Rate_Series_Id = %s, Window_Days = %s,
                           Alert_Threshold_PP = %s, Is_Active = %s
                    WHERE Rate_Tracking_Id = %s RETURNING Rate_Tracking_Id
                """, params + (int(data["id"]),))
            except psycopg2.errors.UniqueViolation:
                raise ValueError("That fund is already tracked against this rate — edit or remove that row instead.")
            row = cur.fetchone()
            if not row:
                raise LookupError("Tracking row not found")
            return int(row[0])
        cur.execute("""
            INSERT INTO Rate_Tracking (Securities_Id, Rate_Series_Id, Window_Days, Alert_Threshold_PP, Is_Active)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (Securities_Id, Rate_Series_Id) DO UPDATE
                SET Window_Days = EXCLUDED.Window_Days, Alert_Threshold_PP = EXCLUDED.Alert_Threshold_PP,
                    Is_Active = EXCLUDED.Is_Active
            RETURNING Rate_Tracking_Id
        """, params)
        return int(cur.fetchone()[0])


def delete_tracking(tracking_id: int) -> bool:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM Rate_Tracking WHERE Rate_Tracking_Id = %s", (tracking_id,))
        return cur.rowcount > 0


def _tracking_result(security_id: int, series: pd.Series, window_days: int):
    """Annualised return of the fund over `window_days` vs. the rate compounded over the same
    stretch (each fixing accrues until the next, act/360). None when there isn't enough overlapping
    data. Meant for overnight rates; the act/360 convention is exact for €STR and SOFR and a
    close approximation for others."""
    with get_db() as conn:
        px = pd.read_sql("""
            SELECT Date AS date, Close::float AS close FROM Historical_Prices
            WHERE Securities_Id = %(sid)s AND Close IS NOT NULL ORDER BY Date
        """, conn, params={"sid": security_id})
    series = series.dropna()
    if px.empty or series.empty:
        return None
    px["date"] = pd.to_datetime(px["date"])
    px = px.set_index("date")["close"]
    end = min(px.index[-1], series.index[-1])
    before = px[px.index <= end - pd.Timedelta(days=window_days)]
    if before.empty:
        return None
    start = before.index[-1]
    p0, p1 = float(before.iloc[-1]), float(px[px.index <= end].iloc[-1])
    years = (end - start).days / 365
    if years <= 0 or p0 <= 0:
        return None
    w = series[(series.index > start) & (series.index <= end)]
    if w.empty:
        return None
    idx = list(w.index)
    growth = 1.0
    for i, (d, v) in enumerate(w.items()):
        gap = (idx[i + 1] - d).days if i + 1 < len(idx) else 1
        growth *= 1 + v / 100 * gap / 360
    fund_ann = ((p1 / p0) ** (1 / years) - 1) * 100
    rate_ann = (growth ** (1 / years) - 1) * 100
    return {
        "window_days": (end - start).days, "from": start.date().isoformat(), "to": end.date().isoformat(),
        "security_annualised_pct": round(fund_ann, 3), "series_annualised_pct": round(rate_ann, 3),
        "gap_pp": round(fund_ann - rate_ann, 3),
    }


# ── Summary + alerts ──────────────────────────────────────────────────────────

def _last_change(s: pd.Series):
    """(date, previous, new) of the most recent change in a level series, else None."""
    s = s.dropna()
    if len(s) < 2:
        return None
    changed = s.ne(s.shift())
    changed.iloc[0] = False
    idx = changed[changed].index
    if len(idx) == 0:
        return None
    i = idx[-1]
    pos = s.index.get_loc(i)
    return i, float(s.iloc[pos - 1]), float(s.iloc[pos])


def _recent_vs_average(s: pd.Series):
    """Median of the last 3 observations vs. the mean of the preceding 30 calendar days. The
    median keeps a one-day month-end/quarter-end funding spike (common in SOFR) from reading as a move."""
    s = s.dropna()
    if len(s) < 8:
        return None
    base = s[(s.index <= s.index[-4]) & (s.index > s.index[-4] - pd.Timedelta(days=MOVE_AVG_DAYS))]
    if base.empty:
        return None
    recent, avg = float(s.iloc[-3:].median()), float(base.mean())
    return {"recent": recent, "average": avg, "diff": recent - avg, "as_of": s.index[-1].date().isoformat()}


def get_rates_summary() -> dict | None:
    defs = get_series_defs(active_only=True)
    df = get_rate_series()
    if defs.empty or df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"])
    by = {c: g.set_index("date")["rate"] for c, g in df.groupby("series")}
    today = pd.Timestamp(date.today())

    series_out = []
    for d in defs.itertuples():
        ser = by.get(d.code)
        if ser is None or ser.empty:
            series_out.append({"id": int(d.id), "code": d.code, "name": d.name, "currency": d.currency,
                               "rate_type": d.rate_type, "alerts_enabled": bool(d.alerts_enabled),
                               "latest": None, "change": None, "move": None})
            continue
        prev = ser[ser.index < ser.index[-1]]
        entry = {
            "id": int(d.id), "code": d.code, "name": d.name, "currency": d.currency,
            "rate_type": d.rate_type, "alerts_enabled": bool(d.alerts_enabled),
            "latest": {"date": ser.index[-1].date().isoformat(), "rate": float(ser.iloc[-1]),
                       "previous": float(prev.iloc[-1]) if not prev.empty else None},
            "change": None, "move": None,
        }
        if d.rate_type in ("Policy", "Target Upper"):
            ch = _last_change(ser)
            if ch:
                dt, old, new = ch
                entry["change"] = {"date": dt.date().isoformat(), "from": old, "to": new, "days_ago": int((today - dt).days)}
        elif d.rate_type == "Overnight":
            entry["move"] = _recent_vs_average(ser)
        series_out.append(entry)

    tracking_out = []
    for t in get_tracking_defs().itertuples():
        if not t.is_active or t.series_code not in by:
            continue
        res = _tracking_result(int(t.securities_id), by[t.series_code], int(t.window_days))
        row = {"id": int(t.id), "ticker": t.ticker, "security_name": t.security_name,
               "series_code": t.series_code, "series_name": t.series_name,
               "window_days_setting": int(t.window_days), "threshold_pp": float(t.threshold_pp), "result": res,
               "duration_years": None if pd.isna(t.duration_years) else float(t.duration_years)}
        row["alert"] = bool(res and res["gap_pp"] <= -float(t.threshold_pp))
        tracking_out.append(row)

    summary = {"series": series_out, "tracking": tracking_out}
    summary["alerts"] = _alerts(summary)
    return summary


def _fmt_date(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%d %b %Y")


def _alerts(sm: dict) -> list[dict]:
    """Dashboard-insight-shaped alerts. Titles embed the rate level / date so a dismissed alert
    (dismissal is keyed by title) doesn't hide the next, different one."""
    out: list[dict] = []
    changes = [s for s in sm["series"] if s["change"]]

    for s in changes:
        ch = s["change"]
        if not s["alerts_enabled"] or ch["days_ago"] > CHANGE_ALERT_DAYS:
            continue
        up = ch["to"] > ch["from"]
        hint = _REVIEW_HINTS.get(s["currency"], f"holdings and exposure in {s['currency'] or 'this currency'}")
        out.append({
            "type": "warning", "icon": "rate_change",
            "title": f"{s['name']} {'raised' if up else 'cut'} to {ch['to']:.2f}% ({_fmt_date(ch['date'])})",
            "message": f"Moved from {ch['from']:.2f}% to {ch['to']:.2f}%. Review your {hint}.",
        })

    # A jump right after a policy change is already explained by that change's own alert, so an
    # overnight-rate move only fires when no policy rate in the same currency changed recently.
    for s in sm["series"]:
        m = s["move"]
        if not s["alerts_enabled"] or not m or abs(m["diff"]) < MOVE_ALERT_PP:
            continue
        if any(c["currency"] == s["currency"] and c["change"]["days_ago"] <= MOVE_AVG_DAYS for c in changes):
            continue
        direction = "above" if m["diff"] > 0 else "below"
        out.append({
            "type": "info", "icon": "rate_move",
            "title": f"{s['name']} is {abs(m['diff']):.2f} pp {direction} its {MOVE_AVG_DAYS}-day average ({_fmt_date(m['as_of'])})",
            "message": (f"{m['recent']:.3f}% vs. {m['average']:.3f}% average — markets may be pricing in a "
                        f"central-bank move ahead of the next meeting. Worth a look at your rate-sensitive holdings."),
        })

    for t in sm["tracking"]:
        if not t["alert"]:
            continue
        r = t["result"]
        out.append({
            "type": "warning", "icon": "rate_tracking",
            "title": f"{t['ticker']} is lagging {t['series_name']} by {abs(r['gap_pp']):.2f} pp ({_fmt_date(r['to'])})",
            "message": (f"Over {r['window_days']} days {t['ticker']} returned {r['security_annualised_pct']:.2f}% annualised vs. "
                        f"{r['series_annualised_pct']:.2f}% for the compounded rate (alert at {t['threshold_pp']:.2f} pp). "
                        + (f"This fund's duration is {t['duration_years']:.1f} years, so it moves with bond yields rather than "
                           f"an overnight rate — this comparison isn't meaningful; consider removing it from Tracked funds."
                           if t.get("duration_years") else "Check for a stale price or a tracking problem.")),
        })
    return out


def get_rate_alerts() -> list[dict]:
    sm = get_rates_summary()
    return sm["alerts"] if sm else []
