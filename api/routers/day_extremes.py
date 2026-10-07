"""Best and worst days (Reports -> Inv. Performance -> Best / Worst Days).

For one investment account (or all of them) and one calendar year: the N best and N worst
trading days, ranked both by profit/loss in EUR and by profit/loss in %, with the positions that
moved the account most on each day.

How a day is measured — price P&L only, rebuilt from the Investments ledger and Historical_Prices:
    P&L(day)  = value at the day's close  -  value at the previous close  -  net money put in that day
    base(day) = previous close's value  +  purchases made that day        (what was exposed to the move)
    %         = P&L / base
A purchase or sale is taken at what was actually paid or received, so a fill away from the day's
close counts toward that day. Dividends and interest do not change market value and are left out.
Transfers of shares between accounts are valued at the day's close, so they are neither a gain nor
a loss; splits are neutralised by restating prices in the unit count of each date.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from database.connection import get_db
from database.fx_effect import _splits

router = APIRouter()

INVESTMENT_TYPES = ("Brokerage", "Margin", "Other Investment")
BUY_SIDE = ("Buy", "Reinvest", "ShrIn")
SELL_SIDE = ("Sell", "ShrOut")
WARMUP_DAYS = 45          # price/FX history needed before the year starts to find the previous close
# A trade at a price more than this far (as a ratio) from that day's stored close means the price
# history and the ledger disagree about the unit — typically the history was adjusted for a later
# corporate action (an exchange/merger) that isn't recorded as a split here. The day is left out.
MISMATCH_RATIO = 2.0


def _num(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else v


@router.get("/years")
def get_years(account_id: Optional[int] = Query(None)):
    """Calendar years with trading activity for the account (or all investment accounts)."""
    acct = "AND i.Accounts_Id = %(a)s" if account_id else "AND a.Accounts_Type::text = ANY(%(t)s)"
    with get_db() as conn:
        df = pd.read_sql(f"""
            SELECT DISTINCT EXTRACT(YEAR FROM i.Date)::int AS yr
            FROM Investments i JOIN Accounts a ON a.Accounts_Id = i.Accounts_Id
            WHERE i.Securities_Id IS NOT NULL {acct} ORDER BY yr DESC
        """, conn, params={"a": account_id, "t": list(INVESTMENT_TYPES)})
    return [int(y) for y in df["yr"]]


@router.get("")
def get_day_extremes(
    account_id: Optional[int] = Query(None),
    year: Optional[int] = Query(None),
    n: int = Query(10),
    min_base: float = Query(100.0),
):
    """The `n` best and worst days of `year` for one account (or all investment accounts when
    account_id is omitted). Days whose base (see module docstring) is under `min_base` EUR are
    ignored — a near-empty account would otherwise produce absurd percentages."""
    today = date.today()
    year = year or today.year
    if not 1 <= n <= 100:
        raise HTTPException(400, "n must be between 1 and 100")
    start, end = date(year, 1, 1), min(date(year, 12, 31), today)
    if end < start:
        raise HTTPException(400, "year is in the future")
    acct_clause = "AND i.Accounts_Id = %(a)s" if account_id else "AND a.Accounts_Type::text = ANY(%(t)s)"
    params = {"a": account_id, "t": list(INVESTMENT_TYPES), "end": end}

    with get_db() as conn:
        name = "All investment accounts"
        if account_id:
            nm = pd.read_sql("SELECT Accounts_Name FROM Accounts WHERE Accounts_Id = %(a)s", conn, params={"a": account_id})
            if nm.empty:
                raise HTTPException(404, "Account not found")
            name = str(nm.iloc[0, 0])
        tx = pd.read_sql(f"""
            SELECT i.Securities_Id AS sid, i.Date AS date, i.Action::text AS action, i.Quantity::float AS qty,
                   i.Price_Per_Share::float AS price, i.Commission::float AS commission,
                   i.Total_Amount_AccCur::float AS amt, i.Corporate_Actions_Id AS ca_id, a.Currencies_Id AS acc_cur,
                   i.Investments_Id AS iid
            FROM Investments i JOIN Accounts a ON a.Accounts_Id = i.Accounts_Id
            WHERE i.Securities_Id IS NOT NULL AND i.Action::text = ANY(%(acts)s) AND i.Date <= %(end)s {acct_clause}
            ORDER BY i.Date, i.Investments_Id
        """, conn, params={**params, "acts": list(BUY_SIDE + SELL_SIDE)})
        if tx.empty:
            return _empty(name, year, n)
        tx["date"] = pd.to_datetime(tx["date"])
        sec_ids = sorted(int(x) for x in tx["sid"].unique())
        sec = pd.read_sql("""
            SELECT s.Securities_Id AS sid, s.Ticker AS ticker, s.Securities_Name AS name, s.Currencies_Id AS ccy
            FROM Securities s WHERE s.Securities_Id = ANY(%(ids)s)
        """, conn, params={"ids": sec_ids}).set_index("sid")
        lo = pd.Timestamp(start) - pd.Timedelta(days=WARMUP_DAYS)
        px = pd.read_sql("""
            SELECT Securities_Id AS sid, Date AS date, Close::float AS close FROM Historical_Prices
            WHERE Securities_Id = ANY(%(ids)s) AND Date >= %(lo)s AND Date <= %(end)s AND Close IS NOT NULL
        """, conn, params={"ids": sec_ids, "lo": lo.date(), "end": end})
        px_seed = pd.read_sql("""
            SELECT DISTINCT ON (Securities_Id) Securities_Id AS sid, Date AS date, Close::float AS close FROM Historical_Prices
            WHERE Securities_Id = ANY(%(ids)s) AND Date < %(lo)s AND Close IS NOT NULL ORDER BY Securities_Id, Date DESC
        """, conn, params={"ids": sec_ids, "lo": lo.date()})
        ccy_ids = sorted({int(c) for c in sec["ccy"]} | {int(c) for c in tx["acc_cur"].unique()})
        fx = pd.read_sql("""
            SELECT Currencies_Id_1 AS cid, Date AS date, FX_Rate::float AS rate FROM Historical_FX
            WHERE Currencies_Id_1 = ANY(%(ids)s) AND Date >= %(lo)s AND Date <= %(end)s
        """, conn, params={"ids": ccy_ids, "lo": lo.date(), "end": end})
        fx_before = pd.read_sql("""
            SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1 AS cid, Date AS date, FX_Rate::float AS rate
            FROM Historical_FX WHERE Currencies_Id_1 = ANY(%(ids)s) AND Date < %(lo)s ORDER BY Currencies_Id_1, Date DESC
        """, conn, params={"ids": ccy_ids, "lo": lo.date()})
        splits = _splits(pd.read_sql("""
            SELECT Securities_Id AS securities_id, Effective_Date AS effective_date,
                   Ratio_New::float / NULLIF(Ratio_Old, 0)::float AS ratio
            FROM Corporate_Actions WHERE Action_Type IN ('Split', 'Reverse Split')
              AND Securities_Id = ANY(%(ids)s) AND Effective_Date <= %(end)s
        """, conn, params={"ids": sec_ids, "end": end}))
        eur = pd.read_sql("SELECT Currencies_Id AS id FROM Currencies WHERE Currencies_ShortName = 'EUR'", conn)
    eur_id = int(eur.iloc[0, 0]) if not eur.empty else None

    if px.empty:
        return _empty(name, year, n)
    px["date"] = pd.to_datetime(px["date"])
    days = pd.DatetimeIndex(sorted(px["date"].unique()))
    if not px_seed.empty:
        px_seed["date"] = pd.to_datetime(px_seed["date"])
        px = pd.concat([px_seed, px], ignore_index=True)
    prev_days = days[days < pd.Timestamp(start)]
    if len(prev_days) == 0:
        return _empty(name, year, n)                      # no previous close to measure the first day against
    days = days[days >= prev_days[-1]]                    # previous close + the whole year

    # Carry each price forward from its own last quote — including one from before the window and
    # one that fell on a non-trading day for the others — before cutting down to the trading days.
    wide = px.pivot_table(index="date", columns="sid", values="close", aggfunc="last").sort_index()
    price = wide.reindex(wide.index.union(days)).ffill().reindex(days)
    # A position bought before it has any quote: use the latest price it actually traded at, so it
    # isn't worth zero until its first quote appears (which would read as a huge one-day gain).
    traded = tx[tx["price"].notna() & (tx["price"] > 0) & tx["action"].isin(("Buy", "Sell"))]
    if not traded.empty:
        tp = traded.pivot_table(index="date", columns="sid", values="price", aggfunc="last").sort_index()
        tp = tp.reindex(tp.index.union(days)).ffill().reindex(days)
        price = price.reindex(columns=price.columns.union(tp.columns))
        price = price.fillna(tp.reindex(columns=price.columns))
    # Restate prices in each date's unit count: undo splits that happen after the date, since
    # Historical_Prices is split-adjusted but quantities are as-transacted.
    for sid, lst in splits.items():
        if sid in price.columns:
            mult = pd.Series(1.0, index=price.index)
            for eff, ratio in lst:
                mult[price.index < pd.Timestamp(eff)] *= ratio
            price[sid] = price[sid] * mult

    def fx_series(cid: int) -> pd.Series:
        if eur_id is not None and cid == eur_id:
            return pd.Series(1.0, index=days)
        part = fx[fx["cid"] == cid]
        s = pd.Series(part["rate"].values, index=pd.to_datetime(part["date"])) if not part.empty else pd.Series(dtype=float)
        s = s[~s.index.duplicated(keep="last")].sort_index()
        seed = fx_before[fx_before["cid"] == cid]
        if not seed.empty:
            s = pd.concat([pd.Series([float(seed["rate"].iloc[0])], index=[pd.Timestamp(seed["date"].iloc[0])]), s]).sort_index()
        return s.reindex(s.index.union(days)).ffill().reindex(days).fillna(1.0)

    fx_by_ccy = {c: fx_series(c) for c in ccy_ids}
    fx_sec = pd.DataFrame({sid: fx_by_ccy[int(sec.loc[sid, "ccy"])] for sid in price.columns})

    # quantity held at each day's close
    sign = tx["action"].map(lambda a: 1.0 if a in BUY_SIDE else -1.0)
    delta = (tx["qty"].abs() * sign).groupby([tx["date"], tx["sid"]]).sum().unstack(fill_value=0.0).sort_index()
    qty = delta.cumsum()
    qty = qty.reindex(qty.index.union(days)).ffill().fillna(0.0).reindex(days)
    qty = qty.reindex(columns=price.columns).fillna(0.0)

    value = (qty * price * fx_sec).fillna(0.0)

    # money put in (+) / taken out (-) per security per day, at what was actually paid/received
    flow = pd.DataFrame(0.0, index=days, columns=price.columns)
    buys = pd.Series(0.0, index=days)
    suspect: dict = {}
    for r in tx[tx["date"] >= pd.Timestamp(start)].itertuples(index=False):
        d, sid, q = r.date, int(r.sid), abs(r.qty or 0.0)
        if d not in flow.index or sid not in flow.columns:
            continue
        is_split = r.ca_id is not None and not pd.isna(r.ca_id)
        if is_split:
            continue
        close_px = price.at[d, sid]
        if r.action in ("Buy", "Sell") and r.price and not pd.isna(r.price) and not pd.isna(close_px) and close_px > 0:
            ratio = r.price / close_px
            if ratio > MISMATCH_RATIO or ratio < 1 / MISMATCH_RATIO:
                suspect.setdefault(d, []).append(str(sec.loc[sid, "ticker"]))
        close_val = q * (close_px if not pd.isna(close_px) else 0.0) * fx_sec.at[d, sid]
        if r.action in ("ShrIn", "ShrOut"):
            amt = close_val                                # a transfer: valued at the close, so no gain or loss
        else:
            local = r.amt if (r.amt and not pd.isna(r.amt)) else None
            if local is not None:
                amt = abs(local) * fx_by_ccy[int(r.acc_cur)].at[d]
            else:
                px_ = r.price if (r.price and not pd.isna(r.price)) else None
                com = r.commission if (r.commission and not pd.isna(r.commission)) else 0.0
                amt = ((q * px_ + (com if r.action in BUY_SIDE else -com)) * fx_sec.at[d, sid]) if px_ is not None else close_val
        if r.action in BUY_SIDE:
            flow.at[d, sid] += amt
            buys.at[d] += amt
        else:
            flow.at[d, sid] -= amt

    pnl = value.diff() - flow                              # per security per day
    pnl = pnl.iloc[1:]
    total = pnl.sum(axis=1)
    base = value.shift(1).sum(axis=1).iloc[1:] + buys.iloc[1:]
    pct = (total / base.where(base >= min_base)) * 100.0
    frame = pd.DataFrame({"pnl": total, "pct": pct, "base": base}).dropna(subset=["pct"])
    frame = frame[frame.index >= pd.Timestamp(start)]
    excluded = [{"date": d.date().isoformat(), "securities": sorted(set(t))}
                for d, t in sorted(suspect.items()) if d in frame.index]
    frame = frame.drop(index=[pd.Timestamp(e["date"]) for e in excluded])
    if frame.empty:
        return _empty(name, year, n)

    def contributors(d, worst: bool) -> list:
        row = pnl.loc[d]
        row = row[row.abs() > 0.005].sort_values(ascending=worst)
        return [{"ticker": sec.loc[int(s), "ticker"], "name": sec.loc[int(s), "name"], "pnl": round(float(v), 2)}
                for s, v in row.head(3).items() if (v < 0) == worst]

    def pick(col: str, worst: bool) -> list:
        sel = frame.sort_values(col, ascending=worst).head(n)
        return [{
            "date": d.date().isoformat(), "weekday": d.strftime("%a"),
            "pnl": round(float(r.pnl), 2), "pct": round(float(r.pct), 2), "base": round(float(r.base), 2),
            "contributors": contributors(d, worst),
        } for d, r in sel.iterrows()]

    return {
        "account": name, "account_id": account_id, "year": year, "n": n, "min_base": min_base,
        "trading_days": int(len(frame)), "up_days": int((frame["pnl"] > 0).sum()), "down_days": int((frame["pnl"] < 0).sum()),
        "total_pnl": round(float(frame["pnl"].sum()), 2), "excluded_days": excluded,
        "worst_eur": pick("pnl", True), "worst_pct": pick("pct", True),
        "best_eur": pick("pnl", False), "best_pct": pick("pct", False),
        "notes": [
            "Price P&L only: each day's change in the account's market value (at closing prices), after removing the money put in or taken out that day. Dividends and interest are not part of it.",
            "% = that day's P&L ÷ (the previous close's value + purchases made that day).",
            "Holdings are rebuilt from your transactions and priced from Market Data's price history, converted to EUR at that day's exchange rate; a trade counts at the price actually paid or received, share transfers between accounts at the day's close, and splits are neutralised.",
            f"Days where that base was under €{min_base:,.0f} are ignored, so a nearly empty account can't produce absurd percentages.",
            "A day is also left out (and listed below the tables) when a trade that day was at a price more than twice, or less than half, the stored close — a sign the price history was adjusted for a corporate action that isn't recorded as a split, which would make the day's P&L meaningless.",
        ],
    }


def _empty(name: str, year: int, n: int) -> dict:
    return {"account": name, "year": year, "n": n, "trading_days": 0, "up_days": 0, "down_days": 0, "total_pnl": 0.0,
            "worst_eur": [], "worst_pct": [], "best_eur": [], "best_pct": [], "notes": [], "excluded_days": []}
