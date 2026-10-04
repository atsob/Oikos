"""Stress Test (Reports -> Inv. Performance -> Stress Test).

1. Rate shock — what a parallel move in interest rates would do to the portfolio, per account
   and per security, over a chosen period.
2. Historical crash replay — what past crashes would do to what is held *today*, including
   holdings that did not exist then (they borrow the behaviour of similar securities).

Neither is a forecast. Both are transparent what-ifs built on stated assumptions, which the API
returns (and accepts as overrides) so the screen can show and edit them. Everything is a simple,
linear approximation — see the notes returned with each result.
"""
from __future__ import annotations

import copy
import math
import re
from datetime import date, datetime
from typing import Optional

import pandas as pd
from fastapi import APIRouter, HTTPException

from database.connection import get_db

router = APIRouter()

# ══════════════════════════════════════════════════════════════════════════════
# Assumptions (all overridable per request)
# ══════════════════════════════════════════════════════════════════════════════

# % change in price for a +1.00 pp rise in interest rates (a fall reverses the sign).
EQUITY_RATE_SENSITIVITY = {
    "Real Estate": -8.0, "Utilities": -6.0, "Technology": -5.0, "Communication Services": -4.0,
    "Consumer Cyclical": -4.0, "Healthcare": -3.0, "Consumer Defensive": -3.0, "Industrials": -3.0,
    "Basic Materials": -2.0, "Energy": -1.0, "Financial Services": 1.0,
    "Other / unclassified stock": -3.5,
}
ASSET_RATE_SENSITIVITY = {
    "Equity fund (broad market)": -3.5,
    "Gold / silver": -3.0,
    "Other commodities": 0.0,
    "Crypto": -5.0,
    "Other": 0.0,
}
BOND_ASSUMPTIONS = {
    "unknown_bond_fund_duration_years": 5.0,   # when a bond fund has no duration on file
    "money_market_duration_years": 0.1,        # overnight / money-market funds
}

# ETF/fund Industry labels (Securities.Industry) -> the stock sector whose sensitivity they get.
INDUSTRY_TO_SECTOR = {
    "Utilities": "Utilities", "Energy": "Energy", "Materials": "Basic Materials", "Health Care": "Healthcare",
    "Consumer Staples": "Consumer Defensive", "Information Technology": "Technology",
}
SECTOR_ALIASES = {"Electronic Technology": "Technology", "Technology Services": "Technology",
                  "Health Technology": "Healthcare", "Finance": "Financial Services"}
YAHOO_SECTOR_KEYS = {
    "realestate": "Real Estate", "technology": "Technology", "healthcare": "Healthcare",
    "financial_services": "Financial Services", "industrials": "Industrials",
    "communication_services": "Communication Services", "consumer_cyclical": "Consumer Cyclical",
    "consumer_defensive": "Consumer Defensive", "energy": "Energy", "basic_materials": "Basic Materials",
    "utilities": "Utilities",
}

SCENARIOS = [
    {"id": "gfc", "name": "Global financial crisis", "start": "2007-10-31", "end": "2009-03-09", "yield_change_pp": -2.0,
     "desc": "Equity peak to trough; euro-area rates fell sharply."},
    {"id": "euro", "name": "Greek / euro-area debt crisis", "start": "2009-10-14", "end": "2012-06-04", "yield_change_pp": 0.0,
     "desc": "Athens market peak to trough; core-euro yields roughly flat overall (periphery up, core down)."},
    {"id": "covid", "name": "Covid crash", "start": "2020-02-19", "end": "2020-03-23", "yield_change_pp": -0.2,
     "desc": "Five-week global sell-off."},
    {"id": "rates22", "name": "2022 rate shock", "start": "2022-01-03", "end": "2022-10-12", "yield_change_pp": 2.5,
     "desc": "Inflation-driven rate rises: bonds and equities fell together."},
]
# Return (%) over the window assumed for a holding that has no history of its own AND no similar
# securities with history. Deliberately rough and round — illustrative, not measured.
FALLBACK_RETURNS = {
    "gfc":     {"Stock": -50, "EquityFund": -48, "Crypto": -80, "Gold": 20, "Commodity": -35, "Other": -30},
    "euro":    {"Stock": -20, "EquityFund": -10, "Crypto": -80, "Gold": 25, "Commodity": 0, "Other": -10},
    "covid":   {"Stock": -33, "EquityFund": -32, "Crypto": -50, "Gold": -3, "Commodity": -30, "Other": -25},
    "rates22": {"Stock": -20, "EquityFund": -18, "Crypto": -65, "Gold": -2, "Commodity": 5, "Other": -10},
    "custom":  {"Stock": -35, "EquityFund": -30, "Crypto": -60, "Gold": 0, "Commodity": -15, "Other": -20},
}
# Equity funds with no history of their own are mapped to the index they track (or the nearest one), when Oikos has
# it (or a blend of two) in Market Data: (regex on the fund name, [(index ticker, weight)], label).
INDEX_PROXIES = [
    (re.compile(r"S&P ?500", re.I), [("SP500", 1.0)], "S&P 500"),
    (re.compile(r"nasdaq", re.I), [("NDX", 1.0)], "Nasdaq 100"),
    (re.compile(r"gold (miners|producers)", re.I), [("XAU", 1.0)], "PHLX Gold & Silver Index"),
    (re.compile(r"athex|greece|greek", re.I), [("FTSE.AT", 1.0)], "FTSE ATHEX Large Cap"),
    # Germany 40 stands in for Europe: the "Euro Stoxx 50 Spot Index" series in Market Data has implausible
    # history (about +25% through the 2008-09 crash), so it is not used.
    (re.compile(r"euro ?stoxx|stoxx europe|msci europe|eurozone|euro dividend|europe", re.I), [("GER40", 1.0)], "Germany 40 (the nearest European index in Market Data)"),
    (re.compile(r"world|acwi|all-world|global", re.I), [("SP500", 0.65), ("GER40", 0.35)], "blend of 65% S&P 500 + 35% Germany 40"),
]
MIN_PEERS = 3
PRICE_LOOKBACK_DAYS = 10     # a price counts for a date if one exists within this many days before it


def default_assumptions() -> dict:
    return {
        "rate_shock": {
            "equity_sensitivity": EQUITY_RATE_SENSITIVITY,
            "asset_sensitivity": ASSET_RATE_SENSITIVITY,
            "bond": BOND_ASSUMPTIONS,
        },
        "replay": {
            "scenarios": SCENARIOS,
            "fallback_returns": FALLBACK_RETURNS,
        },
    }


def _merge(defaults: dict, overrides: Optional[dict]) -> dict:
    """Deep-merge user overrides over defaults, ignoring anything that isn't a number where a
    number belongs (so a half-typed input can't break a run)."""
    out = copy.deepcopy(defaults)
    for k, v in (overrides or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        elif isinstance(out.get(k), (int, float)) and isinstance(v, (int, float)):
            out[k] = v
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Holdings + classification
# ══════════════════════════════════════════════════════════════════════════════

def _load_holdings(account_ids: Optional[list]) -> pd.DataFrame:
    clause = "AND h.Accounts_Id = ANY(%(ids)s)" if account_ids else ""
    with get_db() as conn:
        df = pd.read_sql(f"""
            WITH fx AS (SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate FROM Historical_FX
                        ORDER BY Currencies_Id_1, Date DESC),
                 lp AS (SELECT DISTINCT ON (Securities_Id) Securities_Id, Close FROM Historical_Prices
                        ORDER BY Securities_Id, Date DESC)
            SELECT h.Accounts_Id AS account_id, a.Accounts_Name AS account,
                   s.Securities_Id AS sec_id, s.Ticker AS ticker, s.Securities_Name AS name,
                   s.Securities_Type AS typ, s.Sector AS sector, s.Industry AS industry,
                   s.Currencies_Id AS ccy_id, c.Currencies_ShortName AS ccy,
                   s.Maturity_Date AS maturity, s.Coupon_Rate::float AS coupon,
                   h.Quantity::float AS qty,
                   h.Quantity * lp.Close * CASE WHEN c.Currencies_ShortName = 'EUR' THEN 1
                                                ELSE COALESCE(fx.FX_Rate, 1) END AS value_eur,
                   COALESCE((f.Manual_Overrides->>'Bond_Duration')::float, f.Bond_Duration::float) AS fund_duration,
                   f.Asset_Bond_Pct::float AS bond_pct, f.Asset_Stock_Pct::float AS stock_pct,
                   f.Asset_Class_Override AS asset_class_override,
                   COALESCE(f.Manual_Overrides->'Sector_Weightings', f.Sector_Weightings) AS sector_weights
            FROM Holdings h
            JOIN Accounts a ON a.Accounts_Id = h.Accounts_Id
            JOIN Securities s ON s.Securities_Id = h.Securities_Id
            JOIN Currencies c ON c.Currencies_Id = s.Currencies_Id
            JOIN lp ON lp.Securities_Id = h.Securities_Id
            LEFT JOIN fx ON fx.Currencies_Id_1 = s.Currencies_Id
            LEFT JOIN Fund_Composition f ON f.Securities_Id = s.Securities_Id
            WHERE h.Quantity > 0 {clause}
            ORDER BY a.Accounts_Name, value_eur DESC
        """, conn, params={"ids": list(account_ids)} if account_ids else None)
    df = df[df["value_eur"].notna() & (df["value_eur"] > 0)].reset_index(drop=True)
    df["asset_class"] = df.apply(_classify, axis=1)
    return df


_GOLD_RE = re.compile(r"gold|silver|precious", re.I)
_CASH_RE = re.compile(r"overnight|money market|cash", re.I)


def _classify(r) -> str:
    """Stock | EquityFund | BondFund | CashFund | Bond (direct bonds, T-bills, CDs) | Commodity | Crypto | Other"""
    t = r["typ"]
    if t in ("Bond", "CD"):
        return "Bond"
    if t == "Crypto":
        return "Crypto"
    if t == "Commodity":
        return "Commodity"
    if t == "Stock":
        return "Stock"
    if t in ("ETF", "Mutual Fund", "Closed-End Fund"):
        if r["industry"] == "Money market" or _CASH_RE.search(str(r["name"])):
            return "CashFund"
        if (r["bond_pct"] or 0) >= 0.5:
            return "BondFund"
        if r["asset_class_override"] == "Commodities" or r["sector"] == "Commodities":
            return "Commodity"
        if (r["stock_pct"] or 0) >= 0.5:
            return "EquityFund"
        # no usable Fund_Composition data: fall back to the fund's own Sector label (Equity /
        # Fixed Income / Commodities, as entered on the security)
        return {"Equity": "EquityFund", "Fixed Income": "BondFund", "Commodities": "Commodity"}.get(r["sector"], "Other")
    return "Other"


def _is_precious(r) -> bool:
    return r["industry"] in ("Gold", "Silver") or bool(_GOLD_RE.search(str(r["name"])))


def _years_to(maturity, today: date) -> Optional[float]:
    if maturity is None or pd.isna(maturity):
        return None
    m = maturity.date() if hasattr(maturity, "date") else maturity
    return max((m - today).days, 0) / 365.0


def _bond_duration(r, today: date, bond_cfg: dict):
    """(modified duration in years, basis text) for a holding that behaves like a bond."""
    ac = r["asset_class"]
    if ac == "CashFund":
        d = bond_cfg["money_market_duration_years"]
        return d, f"money-market fund, duration {d:.1f}y (assumed)"
    if ac == "BondFund":
        d = r["fund_duration"]
        if d is not None and not pd.isna(d):
            return float(d), f"fund duration {float(d):.2f}y"
        d = bond_cfg["unknown_bond_fund_duration_years"]
        return d, f"bond fund with no duration on file, {d:.1f}y assumed"
    n = _years_to(r["maturity"], today)
    if n is None:
        d = bond_cfg["unknown_bond_fund_duration_years"]
        return d, f"no maturity on file, {d:.1f}y assumed"
    cpn = r["coupon"]
    if cpn and not pd.isna(cpn) and cpn > 0 and n > 1:
        y = cpn / 100.0
        mac = (1 + y) / y * (1 - (1 + y) ** -n)           # par-bond Macaulay duration
        return mac / (1 + y), f"bond maturing in {n:.1f}y, {cpn:.2f}% coupon"
    return n, f"discount instrument maturing in {n:.2f}y (duration = time to maturity)"


def _equity_sensitivity(r, cfg: dict):
    """(%-per-pp, basis text) for stocks and equity-like funds."""
    eq = cfg["equity_sensitivity"]
    default = eq["Other / unclassified stock"]

    def norm(name):
        name = SECTOR_ALIASES.get(name, name)
        return name if name in eq else None

    if r["asset_class"] == "Stock":
        sec = norm(r["sector"])
        if sec:
            return eq[sec], f"stock, {sec} sector"
        return default, "stock, sector unknown — default"
    # equity-like funds: look-through sector weights first, then the fund's Industry label
    weights = r["sector_weights"]
    if isinstance(weights, dict) and weights:
        tot = used = 0.0
        for key, w in weights.items():
            try:
                w = float(w)
            except (TypeError, ValueError):
                continue
            sec = YAHOO_SECTOR_KEYS.get(key)
            if sec and sec in eq:
                used += w
                tot += w * eq[sec]
        if used >= 0.5:
            return tot / used, "fund look-through sector weights"
    sec = INDUSTRY_TO_SECTOR.get(r["industry"] or "")
    if sec and sec in eq:
        return eq[sec], f"fund focused on {r['industry']}"
    return cfg["asset_sensitivity"]["Equity fund (broad market)"], "equity fund, broad market"


def _blank_row(r, **extra) -> dict:
    return {"account_id": int(r["account_id"]), "account": r["account"], "securities_id": int(r["sec_id"]),
            "ticker": r["ticker"], "security": r["name"], "type": r["typ"], "asset_class": r["asset_class"],
            "value": round(float(r["value_eur"]), 2), **extra}


def _aggregate(rows: list, value_key: str = "value") -> dict:
    """Totals, by-account and by-asset-class rollups for a list of result rows."""
    df = pd.DataFrame(rows)
    if df.empty:
        return {"by_account": [], "by_class": []}
    num = [c for c in ("value", "price_impact", "income_impact", "impact") if c in df.columns]

    def roll(key):
        g = df.groupby(key, sort=False)[num].sum().reset_index()
        g["pct"] = g["impact"] / g["value"].where(g["value"] != 0) * 100
        return [{k: (None if (isinstance(v, float) and math.isnan(v)) else (round(v, 2) if isinstance(v, float) else v))
                 for k, v in rec.items()} for rec in g.to_dict("records")]
    return {"by_account": roll("account"), "by_class": roll("asset_class")}


# ══════════════════════════════════════════════════════════════════════════════
# 1. Rate shock
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/assumptions")
def get_assumptions():
    """The default assumptions both stress tests use, so the screen can show and edit them."""
    return default_assumptions()


@router.post("/rate-shock")
def run_rate_shock(data: dict):
    """Parallel interest-rate move of `shock_pp` percentage points, measured over `period_months`.

    Price impact — bonds, T-bills and bond funds: -duration x shock (linear; convexity ignored).
    Stocks and equity funds: a %-per-pp sensitivity by sector/type (assumptions; editable).
    Income impact over the period — direct bonds/T-bills/CDs that mature inside the period are
    rolled at the new rate for the rest of it; money-market funds reprice immediately.
    """
    account_ids = data.get("account_ids") or None
    try:
        shock = float(data.get("shock_pp", 1.0))
        months = float(data.get("period_months", 12))
    except (TypeError, ValueError):
        raise HTTPException(400, "shock_pp and period_months must be numbers")
    if months <= 0 or months > 120:
        raise HTTPException(400, "period_months must be between 1 and 120")
    cfg = _merge(default_assumptions()["rate_shock"], (data.get("assumptions") or {}).get("rate_shock"))
    today = date.today()
    period_years = months / 12.0
    holdings = _load_holdings(account_ids)
    if holdings.empty:
        return {"params": {"shock_pp": shock, "period_months": months}, "summary": None, "rows": [],
                "by_account": [], "by_class": [], "notes": []}

    rows = []
    for _, r in holdings.iterrows():
        ac, val = r["asset_class"], float(r["value_eur"])
        income = 0.0
        recoverable = False
        if ac in ("Bond", "BondFund", "CashFund"):
            dur, basis = _bond_duration(r, today, cfg["bond"])
            sens = -dur
            if ac == "CashFund":
                sens = 0.0                                   # NAV barely moves; the yield does
                income = val * shock / 100.0 * period_years
                basis += "; yield resets immediately"
            elif ac == "Bond":
                ttm = _years_to(r["maturity"], today)
                if ttm is not None and ttm < period_years:
                    income = val * shock / 100.0 * (period_years - ttm)
                    basis += f"; matures in {ttm:.2f}y and is rolled at the new rate for the rest of the period"
                recoverable = True
        elif ac in ("Stock", "EquityFund"):
            sens, basis = _equity_sensitivity(r, cfg)
        elif ac == "Commodity":
            key = "Gold / silver" if _is_precious(r) else "Other commodities"
            sens, basis = cfg["asset_sensitivity"][key], f"commodity ({key.lower()})"
        elif ac == "Crypto":
            sens, basis = cfg["asset_sensitivity"]["Crypto"], "crypto"
        else:
            sens, basis = cfg["asset_sensitivity"]["Other"], "unclassified — no rate sensitivity assumed"
        price_pct = max(sens * shock, -100.0)
        price = val * price_pct / 100.0
        rows.append(_blank_row(
            r, basis=basis, sensitivity_pct_per_pp=round(sens, 3), price_impact=round(price, 2),
            income_impact=round(income, 2), impact=round(price + income, 2),
            pct=round((price + income) / val * 100, 3), recoverable_at_maturity=recoverable))

    total_value = float(sum(x["value"] for x in rows))
    price_total = float(sum(x["price_impact"] for x in rows))
    income_total = float(sum(x["income_impact"] for x in rows))
    recov = float(sum(x["price_impact"] for x in rows if x["recoverable_at_maturity"]))
    agg = _aggregate(rows)
    return {
        "params": {"shock_pp": shock, "period_months": months},
        "summary": {
            "value": round(total_value, 2), "price_impact": round(price_total, 2), "income_impact": round(income_total, 2),
            "impact": round(price_total + income_total, 2), "pct": round((price_total + income_total) / total_value * 100, 3),
            "price_pct": round(price_total / total_value * 100, 3),
            "recoverable_at_maturity": round(recov, 2),
        },
        "by_account": agg["by_account"], "by_class": agg["by_class"], "rows": rows,
        "assumptions": cfg,
        "notes": [
            "A parallel, instant move in rates of the size entered; the period only matters for income on bonds that mature within it.",
            "Bond-like holdings: price change = -duration x move (linear, convexity ignored). T-bills and discount bonds use time to maturity as duration; bond funds use their stored duration (manual override first).",
            "Direct bonds and T-bills held to maturity recover their price impact — it is a mark-to-market effect, not a realised loss.",
            "Stocks and equity funds react by an assumed %-per-percentage-point sensitivity by sector (editable below) — a rough rule of thumb, not a measured relationship. Funds use their look-through sector weights when known.",
            "Credit spreads, inflation and currency effects, and the knock-on effect of rates on earnings are not modelled. Cash and deposit balances are outside this test.",
        ],
    }


# ══════════════════════════════════════════════════════════════════════════════
# 2. Historical crash replay
# ══════════════════════════════════════════════════════════════════════════════

def _pair_returns(conn, start: str, end: str) -> pd.DataFrame:
    """Local-currency price return start->end for every security that had a price at both dates
    (within PRICE_LOOKBACK_DAYS before each) — the pool both 'own history' and 'similar securities'
    draw from."""
    df = pd.read_sql(f"""
        WITH p0 AS (SELECT DISTINCT ON (Securities_Id) Securities_Id, Close FROM Historical_Prices
                    WHERE Date <= %(a)s::date AND Date >= %(a)s::date - {PRICE_LOOKBACK_DAYS}
                    ORDER BY Securities_Id, Date DESC),
             p1 AS (SELECT DISTINCT ON (Securities_Id) Securities_Id, Close FROM Historical_Prices
                    WHERE Date <= %(z)s::date AND Date >= %(z)s::date - {PRICE_LOOKBACK_DAYS}
                    ORDER BY Securities_Id, Date DESC)
        SELECT s.Securities_Id AS id, s.Ticker AS ticker, s.Securities_Type AS typ, s.Sector AS sector,
               s.Industry AS industry, s.Currencies_Id AS ccy_id,
               p1.Close::float / p0.Close::float - 1 AS ret
        FROM Securities s JOIN p0 ON p0.Securities_Id = s.Securities_Id JOIN p1 ON p1.Securities_Id = s.Securities_Id
        WHERE p0.Close > 0 AND p1.Close > 0
    """, conn, params={"a": start, "z": end})
    return df[(df["ret"] > -0.999) & (df["ret"] < 10)]          # drop obviously broken series


def _fx_changes(conn, ccy_ids: list, start: str, end: str) -> dict:
    if not ccy_ids:
        return {}
    df = pd.read_sql(f"""
        WITH f0 AS (SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1 AS cid, FX_Rate AS r FROM Historical_FX
                    WHERE Date <= %(a)s::date AND Date >= %(a)s::date - {PRICE_LOOKBACK_DAYS}
                      AND Currencies_Id_1 = ANY(%(ids)s) ORDER BY Currencies_Id_1, Date DESC),
             f1 AS (SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1 AS cid, FX_Rate AS r FROM Historical_FX
                    WHERE Date <= %(z)s::date AND Date >= %(z)s::date - {PRICE_LOOKBACK_DAYS}
                      AND Currencies_Id_1 = ANY(%(ids)s) ORDER BY Currencies_Id_1, Date DESC)
        SELECT f0.cid, (f1.r / f0.r)::float AS chg FROM f0 JOIN f1 ON f1.cid = f0.cid WHERE f0.r > 0
    """, conn, params={"a": start, "z": end, "ids": [int(i) for i in ccy_ids]})
    return {int(c): float(v) for c, v in zip(df["cid"], df["chg"])}


def _replay_one(holdings: pd.DataFrame, sc: dict, cfg: dict, pool: pd.DataFrame, today: date) -> dict:
    """`pool` carries, for every security with prices at both dates, its local `ret` and its
    EUR return `eur_ret` (price change x the change in its currency against the euro) — so a
    proxy borrowed from a USD security already includes the currency move a euro investor felt."""
    sid = sc["id"]
    fallback = cfg["fallback_returns"].get(sid, cfg["fallback_returns"]["custom"])
    dy = float(sc.get("yield_change_pp") or 0.0)
    own = pool.set_index("id")
    by_ticker = pool.drop_duplicates("ticker").set_index("ticker")["eur_ret"].to_dict()
    rows = []
    for _, r in holdings.iterrows():
        ac, val = r["asset_class"], float(r["value_eur"])
        sec_id = int(r["sec_id"])
        res = {"eur": None, "local": None, "basis": "", "detail": "", "peers": None}

        def peers_median(mask, label):
            p = pool[mask & (pool["id"] != sec_id)]
            if len(p) >= MIN_PEERS:
                res.update(eur=float(p["eur_ret"].median()), local=float(p["ret"].median()), basis="peers",
                           peers=len(p), detail=f"no history — median of {len(p)} {label} that existed then")
                return True
            return False

        if sec_id in own.index:                                      # 1. its own price history
            res.update(eur=float(own.loc[sec_id, "eur_ret"]), local=float(own.loc[sec_id, "ret"]),
                       basis="own", detail="own price history over the window")
        elif ac in ("Bond", "BondFund"):                             # 2a. bonds: duration rule
            dur, d = _bond_duration(r, today, cfg["_bond"])
            v = -dur * dy / 100.0
            res.update(eur=v, local=v, basis="rule",
                       detail=f"no history — {d}, assumed yield change {dy:+.1f}pp (credit/default risk not modelled)")
        elif ac == "CashFund":
            res.update(eur=0.0, local=0.0, basis="rule", detail="no history — money-market fund assumed flat")
        else:                                                        # 2b. the index it tracks / similar securities
            if ac in ("EquityFund", "Other"):
                for rx, parts, label in INDEX_PROXIES:
                    if rx.search(str(r["name"])) and all(t in by_ticker for t, _ in parts):
                        v = sum(w * by_ticker[t] for t, w in parts)
                        res.update(eur=v, local=v, basis="peers", detail=f"no history — tracks {label}, used as proxy")
                        break
            if res["eur"] is None and ac == "EquityFund" and r["industry"] in INDUSTRY_TO_SECTOR:
                sector = INDUSTRY_TO_SECTOR[r["industry"]]
                peers_median((pool["typ"] == "Stock") & (pool["sector"] == sector), f"{sector} stocks")
            if res["eur"] is None and ac in ("Stock", "Crypto"):
                for label, mask in (
                    ("securities of the same type, sector and industry",
                     (pool["typ"] == r["typ"]) & (pool["sector"] == r["sector"]) & (pool["industry"] == r["industry"])
                     if r["sector"] and r["industry"] else None),
                    ("securities of the same type and sector",
                     (pool["typ"] == r["typ"]) & (pool["sector"] == r["sector"]) if r["sector"] else None),
                    ("securities of the same type", (pool["typ"] == r["typ"])),
                ):
                    if mask is not None and peers_median(mask, label):
                        break
            if res["eur"] is None:                                   # 3. a rough, stated assumption
                key = ("Gold" if _is_precious(r) else "Commodity") if ac == "Commodity" else \
                      {"Stock": "Stock", "EquityFund": "EquityFund", "Crypto": "Crypto"}.get(ac, "Other")
                pct = fallback.get(key, fallback["Other"])
                res.update(eur=pct / 100.0, local=pct / 100.0, basis="assumption",
                           detail=f"no history and no similar securities — assumed {pct:+.0f}% ({key})")

        eur, local = res["eur"], res["local"]
        rows.append(_blank_row(
            r, basis=res["basis"], detail=res["detail"], peers=res["peers"],
            local_return_pct=round(local * 100, 2), fx_pct=round(((1 + eur) / (1 + local) - 1) * 100, 2),
            return_pct=round(eur * 100, 2), impact=round(val * eur, 2), pct=round(eur * 100, 2)))
    total = float(sum(x["value"] for x in rows))
    imp = float(sum(x["impact"] for x in rows))
    cov = {b: round(sum(x["value"] for x in rows if x["basis"] == b) / total * 100, 1) if total else 0.0
           for b in ("own", "peers", "rule", "assumption")}
    agg = _aggregate(rows)
    return {
        "id": sid, "name": sc["name"], "start": sc["start"], "end": sc["end"], "yield_change_pp": dy,
        "desc": sc.get("desc", ""),
        "summary": {"value": round(total, 2), "impact": round(imp, 2), "pct": round(imp / total * 100, 2) if total else 0,
                    "coverage": cov},
        "by_account": agg["by_account"], "by_class": agg["by_class"], "rows": rows,
    }


@router.post("/replay")
def run_replay(data: dict):
    """Apply past crashes to today's holdings. `scenarios` is a list of scenario ids (see
    /assumptions); `custom` = {start, end, yield_change_pp, name} adds a user-defined window."""
    account_ids = data.get("account_ids") or None
    cfg = _merge(default_assumptions()["replay"], (data.get("assumptions") or {}).get("replay"))
    bond_cfg = _merge(BOND_ASSUMPTIONS, ((data.get("assumptions") or {}).get("rate_shock") or {}).get("bond"))
    cfg["_bond"] = bond_cfg

    y_over = (((data.get("assumptions") or {}).get("replay") or {}).get("yield_change_pp")) or {}
    wanted = data.get("scenarios") or [s["id"] for s in SCENARIOS]
    scenarios = []
    for s in SCENARIOS:
        if s["id"] in wanted:
            sc = dict(s)
            ov = y_over.get(s["id"])
            scenarios.append(sc if not isinstance(ov, (int, float)) else {**sc, "yield_change_pp": float(ov)})
    custom = data.get("custom")
    if custom:
        try:
            a, z = str(custom["start"])[:10], str(custom["end"])[:10]
            if datetime.fromisoformat(a) >= datetime.fromisoformat(z):
                raise ValueError
        except (KeyError, ValueError):
            raise HTTPException(400, "Custom scenario needs a start date before its end date (YYYY-MM-DD)")
        scenarios.append({"id": "custom", "name": custom.get("name") or "Custom window", "start": a, "end": z,
                          "yield_change_pp": float(custom.get("yield_change_pp") or 0.0), "desc": "User-defined window."})
    if not scenarios:
        raise HTTPException(400, "No scenario selected")

    holdings = _load_holdings(account_ids)
    today = date.today()
    results = []
    if not holdings.empty:
        with get_db() as conn:
            eur_id = pd.read_sql("SELECT Currencies_Id AS id FROM Currencies WHERE Currencies_ShortName = 'EUR'", conn)
            eur_id = int(eur_id.iloc[0]["id"]) if not eur_id.empty else None
            for sc in scenarios:
                pool = _pair_returns(conn, sc["start"], sc["end"])
                ids = [int(i) for i in pool["ccy_id"].dropna().unique() if int(i) != eur_id]
                fx = _fx_changes(conn, ids, sc["start"], sc["end"])
                pool["eur_ret"] = [(1 + r) * (1.0 if (pd.isna(c) or int(c) == eur_id) else fx.get(int(c), 1.0)) - 1
                                   for r, c in zip(pool["ret"], pool["ccy_id"])]
                results.append(_replay_one(holdings, sc, cfg, pool, today))
    public_cfg = {k: v for k, v in cfg.items() if not k.startswith("_")}
    public_cfg["yield_change_pp"] = {s["id"]: s["yield_change_pp"] for s in scenarios if s["id"] != "custom"}
    return {
        "scenarios": results, "assumptions": public_cfg,
        "notes": [
            "What today's holdings would have done through each past window — not a forecast. Each window runs from a pre-crash level to the low, with hindsight.",
            "Each holding is measured, in order of preference, by: (1) its own price history over the window; (2) for bonds and bond funds without history, duration x an assumed yield change; (3) for equity funds, the index they track when Oikos has it (S&P 500, Nasdaq 100, Germany 40 for Europe, FTSE ATHEX, gold miners, or a stated blend for world funds) or, for sector funds, the median of that sector's stocks; for stocks, the median of similar securities that did exist then (same type, sector and industry, widening to same type; at least 3 needed); (4) failing that, a rough round-number assumption, shown in the table below. Every row says which one it used, and the coverage bar shows how much of the portfolio each covers.",
            "Returns are price changes converted to euros at the exchange rates of the window's two dates (so a borrowed US return includes the dollar move a euro investor felt). Dividends are ignored.",
            "T-bills and direct bonds are assumed to move only with rates; sovereign credit events (e.g. the 2012 Greek debt restructuring) are not modelled. Cash and deposit balances are outside this test.",
        ],
    }
