"""Currency effect on investment P&L for one currency, over a chosen period.

For every (account, security) quoted in the currency, the period's P&L is split
into realized (units sold in the period, plus income) and unrealized (units still
held at the end), and each of those into a market part (the price move in the
security's own currency, translated at the rate of the day it is measured) and an
FX part (the rest: what the currency's move against the storage base, EUR, added
or took away).

Method — average cost, per position, in both the security's currency (local) and
EUR, restarted at the period's start:
  * The position held at the start is marked to market there (price × rate on the
    start date), so a period only counts what happened inside it. "All" has no
    such mark: cost is the real purchase cost.
  * Buy / ShrIn (transfer in) add units at their cost; Reinvest is income plus a buy.
  * Sell realizes proceeds − average cost: market part = local gain × the sale
    day's rate, FX part = the EUR gain minus that.
  * ShrOut (transfer out) removes units at average cost — no gain realized.
  * ShrIn / ShrOut created by a split (linked to a corporate action) change the unit
    count only.
  * Dividend / IntInc / MiscInc are income (realized, market), MiscExp a cost,
    RtrnCap lowers the cost basis.
  * Unrealized at the end: value now − remaining cost; market part = local gain ×
    today's rate.
For each position realized + unrealized + income equals its value change in EUR
minus the money put in, so the parts add up to the period's P&L.

Quantities are kept as recorded; stored prices are split-adjusted (as downloaded),
so a price at an earlier date is converted back to that date's unit count with the
splits recorded between it and today — the same adjustment get_pnl() makes.
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import date, timedelta
from typing import Optional

import pandas as pd

PERIODS = ("DTD", "WTD", "MTD", "QTD", "YTD", "1Y", "3Y", "5Y", "All")

INCOME_ACTIONS = ("Dividend", "IntInc", "MiscInc")


def period_start(period: str, today: date) -> Optional[date]:
    """Last day *before* the period (positions are marked at its close), as in get_pnl()."""
    if period == "DTD":
        return today - timedelta(days=1)
    if period == "WTD":
        return today - timedelta(days=today.weekday()) - timedelta(days=1)
    if period == "MTD":
        return today.replace(day=1) - timedelta(days=1)
    if period == "QTD":
        return date(today.year, 3 * ((today.month - 1) // 3) + 1, 1) - timedelta(days=1)
    if period == "YTD":
        return date(today.year, 1, 1) - timedelta(days=1)
    if period in ("1Y", "3Y", "5Y"):
        years = int(period[0])
        try:
            return today.replace(year=today.year - years)
        except ValueError:  # 29 Feb
            return today.replace(year=today.year - years, day=28)
    if period == "All":
        return None
    raise ValueError(f"period must be one of {PERIODS}")


class _Series:
    """Date-sorted values with an on-or-before lookup."""

    def __init__(self, dates: list, values: list):
        self.dates, self.values = dates, values

    def at(self, d: date) -> Optional[float]:
        i = bisect_right(self.dates, d)
        return float(self.values[i - 1]) if i else None

    def last(self) -> Optional[float]:
        return float(self.values[-1]) if self.values else None


def _series(df: pd.DataFrame, key_col: str, date_col: str, val_col: str) -> dict:
    out: dict = {}
    if df.empty:
        return out
    df = df.sort_values([key_col, date_col])
    for k, g in df.groupby(key_col):
        out[k] = _Series([pd.Timestamp(x).date() for x in g[date_col]], [float(v) for v in g[val_col]])
    return out


def _splits(df: pd.DataFrame) -> dict:
    """securities_id → [(effective_date, ratio)], merging same-ratio records under 10
    days apart (downloaded corporate actions sometimes carry one split twice) — the
    same de-duplication get_pnl() applies."""
    out: dict = {}
    for sid, g in df.sort_values(["securities_id", "effective_date"]).groupby("securities_id"):
        kept: list = []
        for d, r in zip(g["effective_date"], g["ratio"]):
            d = pd.Timestamp(d).date()
            if not r or pd.isna(r):
                continue
            if any(abs((d - kd).days) <= 10 and abs(kr - float(r)) < 1e-9 for kd, kr in kept):
                continue
            kept.append((d, float(r)))
        out[sid] = kept
    return out


def compute(conn, cid: int, period: str, today: Optional[date] = None) -> dict:
    today = today or date.today()
    start = period_start(period, today)

    txns = pd.read_sql("""
        SELECT i.Accounts_Id AS accounts_id, a.Accounts_Name AS account, a.Accounts_Type::text AS account_type,
               a.Currencies_Id AS acc_cur, i.Securities_Id AS securities_id, s.Securities_Name AS name,
               s.Ticker AS ticker, i.Date AS date, i.Action::text AS action, i.Quantity::float AS qty,
               i.Price_Per_Share::float AS price, i.Commission::float AS commission,
               i.Total_Amount_AccCur::float AS amt_acc, i.Total_Amount_SecCur::float AS amt_sec,
               i.FX_Rate::float AS booked_fx, i.Corporate_Actions_Id AS ca_id, i.Investments_Id AS iid
        FROM Investments i
        JOIN Securities s ON s.Securities_Id = i.Securities_Id
        JOIN Accounts a ON a.Accounts_Id = i.Accounts_Id
        WHERE s.Currencies_Id = %(cid)s AND i.Date <= %(today)s
        ORDER BY i.Date, i.Investments_Id
    """, conn, params={"cid": cid, "today": today})
    if txns.empty:
        return {"period": period, "start": str(start) if start else None, "positions": []}
    txns["date"] = pd.to_datetime(txns["date"]).dt.date

    sec_ids = sorted(int(x) for x in txns["securities_id"].unique())
    cur_ids = sorted({int(cid)} | {int(x) for x in txns["acc_cur"].unique()})
    prices = _series(pd.read_sql(
        "SELECT Securities_Id AS k, Date AS d, Close::float AS v FROM Historical_Prices "
        "WHERE Securities_Id = ANY(%(ids)s) AND Date <= %(today)s AND Close IS NOT NULL",
        conn, params={"ids": sec_ids, "today": today}), "k", "d", "v")
    fx = _series(pd.read_sql(
        "SELECT Currencies_Id_1 AS k, Date AS d, FX_Rate::float AS v FROM Historical_FX "
        "WHERE Currencies_Id_1 = ANY(%(ids)s) AND Date <= %(today)s",
        conn, params={"ids": cur_ids, "today": today}), "k", "d", "v")
    splits = _splits(pd.read_sql(
        "SELECT Securities_Id AS securities_id, Effective_Date AS effective_date, "
        "Ratio_New::float / NULLIF(Ratio_Old, 0)::float AS ratio FROM Corporate_Actions "
        "WHERE Action_Type IN ('Split', 'Reverse Split') AND Securities_Id = ANY(%(ids)s) "
        "AND Effective_Date <= %(today)s",
        conn, params={"ids": sec_ids, "today": today}))

    def rate(cur_id: int, d: date) -> Optional[float]:
        """Storage-base units per 1 unit of cur_id on d; the storage base itself has no
        stored rates, so a currency with none at all counts as 1."""
        s = fx.get(cur_id)
        return 1.0 if s is None else s.at(d)

    def raw_price(sid: int, d: date) -> Optional[float]:
        """Close on or before d, in the unit count of that date (undoing later splits)."""
        s = prices.get(sid)
        p = s.at(d) if s else None
        if p is None:
            return None
        mult = 1.0
        for ed, r in splits.get(sid, []):
            if d < ed <= today:
                mult *= r
        return p * mult

    def local_amount(r) -> Optional[float]:
        if r.amt_sec:
            return abs(r.amt_sec)
        if r.amt_acc and r.booked_fx:
            return abs(r.amt_acc) / r.booked_fx
        q, px, com = abs(r.qty or 0), r.price or 0, r.commission or 0
        if q and px:
            return q * px + (com if r.action == "Buy" else -com if r.action == "Sell" else 0)
        if q:
            p = raw_price(r.securities_id, r.date)
            return q * p if p is not None else None
        return None

    sec_fx_today = rate(cid, today)
    positions = []
    for (acc_id, sid), g in txns.groupby(["accounts_id", "securities_id"], sort=False):
        first = g.iloc[0]
        qty = cost_l = cost_e = 0.0
        real_tot = real_mkt = income = 0.0
        marked = start is None
        active = False  # anything happened in, or was held through, the period
        unmarked = False  # held at the start but no price/rate there: real cost kept

        def mark():
            nonlocal cost_l, cost_e, marked, active, unmarked
            marked = True
            if abs(qty) < 1e-12:
                return
            active = True
            p, r = raw_price(sid, start), rate(cid, start)
            if p is not None and r is not None:
                cost_l, cost_e = qty * p, qty * p * r
            else:  # no price or rate that early: measure from the real cost instead
                unmarked = True

        for r in g.itertuples(index=False):
            if not marked and r.date > start:
                mark()
            in_period = marked
            q = abs(r.qty or 0)
            a = r.action
            amt_l = local_amount(r)
            fx_d = rate(cid, r.date)
            acc_fx = rate(int(r.acc_cur), r.date)
            if r.amt_acc and acc_fx is not None:
                amt_e = abs(r.amt_acc) * acc_fx
            elif amt_l is not None and fx_d is not None:
                amt_e = amt_l * fx_d
            else:
                amt_e = None
            is_split = r.ca_id is not None and not pd.isna(r.ca_id)

            if a in ("Buy", "Reinvest") or (a == "ShrIn" and not is_split):
                if a == "Reinvest" and in_period and amt_e is not None:
                    income += amt_e
                qty += q
                cost_l += amt_l or 0.0
                cost_e += amt_e or 0.0
            elif a == "ShrIn":
                qty += q
            elif a == "ShrOut" and is_split:
                qty -= q
            elif a in ("Sell", "ShrOut"):
                if qty > 1e-12:
                    share = min(q / qty, 1.0)
                    out_l, out_e = cost_l * share, cost_e * share
                    if a == "Sell" and in_period and amt_l is not None and amt_e is not None and fx_d is not None:
                        real_tot += amt_e - out_e
                        real_mkt += (amt_l - out_l) * fx_d
                    cost_l -= out_l
                    cost_e -= out_e
                qty -= q
            elif a in INCOME_ACTIONS:
                if in_period and amt_e is not None:
                    income += amt_e
            elif a == "MiscExp":
                if in_period and amt_e is not None:
                    income -= amt_e
            elif a == "RtrnCap":
                cost_l -= amt_l or 0.0
                cost_e -= amt_e or 0.0
            if in_period:
                active = True
        if not marked:
            mark()

        held = qty > 1e-9
        unr_tot = unr_mkt = 0.0
        p_now = raw_price(sid, today)
        value_l = qty * p_now if held and p_now is not None else 0.0
        if held and p_now is not None and sec_fx_today is not None:
            unr_tot = value_l * sec_fx_today - cost_e
            unr_mkt = (value_l - cost_l) * sec_fx_today
        if not active:
            continue
        positions.append({
            "accounts_id": int(acc_id), "account": first["account"], "account_type": first["account_type"],
            "securities_id": int(sid), "name": first["name"], "ticker": first["ticker"],
            "status": "open" if held else "closed", "quantity": round(qty, 8) if held else 0.0,
            "value_eur": round(value_l * sec_fx_today, 2) if held and sec_fx_today is not None else 0.0,
            "realized_market": round(real_mkt, 2), "realized_fx": round(real_tot - real_mkt, 2),
            "unrealized_market": round(unr_mkt, 2), "unrealized_fx": round(unr_tot - unr_mkt, 2),
            "income": round(income, 2), "from_cost": unmarked,
        })
    positions.sort(key=lambda p: -abs(p["realized_fx"] + p["unrealized_fx"]))
    return {"period": period, "start": str(start) if start else None, "positions": positions}
