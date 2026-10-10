"""Currency exposure, looked through (Reports -> Inv. Portfolio -> Portfolio Analysis -> Currency Exposure).

Currency -> category -> instrument, in EUR. Unlike the plain FX Exposure tab (which counts a fund in the
currency it is quoted in), a fund is spread over the currencies of what it holds:

  stocks / bonds held directly   the currency of the security
  crypto, commodities            US dollars — they are priced in dollars whatever currency the exchange quotes them in
  others                         the currency they are quoted in
  cash accounts                  the account's currency (the chosen accounts' Cash / Checking / Savings)
  a fund with currency rows      its own rows (Fund_Currency_Exposure: downloaded — iShares publishes each
                                 holding's market currency — or typed in); a hedged share class is 100% its own currency
  an unhedged commodity fund     US dollars (gold, silver, oil … are priced in dollars whatever currency the fund is listed in)
  a fund without them            derived: stocks by the home currency of each country in its country breakdown,
                                 bonds in the fund's own currency (a "EUR Corporate Bond" fund holds EUR bonds
                                 whoever the issuer is); marked "derived". With no data at all: the fund's own
                                 currency, under "Funds (no look-through)".
"""
from __future__ import annotations

import re
from typing import Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from api.routers.country_exposure import FUND_TYPES, build_tree, load_cash_accounts, load_held
from api.routers.reports import _acct_clause, _parse_account_ids
from database.connection import get_db
from database.countries import country_currency

router = APIRouter()

LIABILITY_TYPES = ("Credit Card", "Loan", "Liability")
DOLLAR_PRICED = {"Crypto", "Commodity"}      # priced in US dollars worldwide; a EUR quote is just a conversion
UNSPEC = "UNSPEC"
SPECIAL_NAMES = {UNSPEC: "Not broken down by currency"}


def _direct_category(stype: str) -> str:
    return {"Stock": "Stocks", "Emp. Stock Opt.": "Stocks", "Bond": "Bonds", "CD": "Cash & Deposits",
            "Crypto": "Crypto", "Commodity": "Commodities"}.get(stype, "Other")


def _fund_kind(asset_bond_pct) -> str:
    return "Bonds" if (asset_bond_pct or 0) >= 0.7 else "Stocks"


# ── A fund's own currency rows (typed in; downloaded ones arrive the same way) ───

@router.get("/funds/{sec_id}")
def get_fund_currencies(sec_id: int):
    with get_db() as conn:
        df = pd.read_sql("""SELECT Currency AS currency, Weight_Pct::float AS weight_pct, Source AS source, As_Of::text AS as_of, Origin AS origin
                            FROM Fund_Currency_Exposure WHERE Securities_Id = %(s)s ORDER BY Weight_Pct DESC""", conn, params={"s": sec_id})
    rows = df.to_dict(orient="records")
    return {"rows": [{"currency": r["currency"], "weight_pct": r["weight_pct"]} for r in rows],
            "source": rows[0]["source"] if rows else None, "as_of": rows[0]["as_of"] if rows else None,
            "origin": rows[0]["origin"] if rows else None, "total_pct": round(sum(r["weight_pct"] for r in rows), 4)}


@router.put("/funds/{sec_id}")
def save_fund_currencies(sec_id: int, data: dict):
    clean, seen = [], set()
    for r in data.get("rows") or []:
        c = str(r.get("currency") or "").strip().upper()
        try:
            w = float(r.get("weight_pct"))
        except (TypeError, ValueError):
            raise HTTPException(400, f"Bad weight for {c or 'a row'}")
        if not re.fullmatch(r"[A-Z]{3}", c):
            raise HTTPException(400, f"{r.get('currency')!r} isn't a three-letter currency code")
        if not 0 <= w <= 100:
            raise HTTPException(400, f"Weight for {c} must be between 0 and 100")
        if c in seen:
            raise HTTPException(400, f"{c} appears twice")
        seen.add(c)
        clean.append((c, w))
    if sum(w for _, w in clean) > 100.5:
        raise HTTPException(400, "The weights add up to more than 100%")
    source = (data.get("source") or "").strip() or None
    as_of = (data.get("as_of") or "").strip() or None
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM Securities WHERE Securities_Id = %s", (sec_id,))
        if cur.fetchone() is None:
            raise HTTPException(404, "Security not found")
        cur.execute("DELETE FROM Fund_Currency_Exposure WHERE Securities_Id = %s", (sec_id,))
        for c, w in clean:
            cur.execute("""INSERT INTO Fund_Currency_Exposure (Securities_Id, Currency, Weight_Pct, Source, As_Of, Origin)
                           VALUES (%s, %s, %s, %s, %s, 'manual')""", (sec_id, c, w, source, as_of))
    return {"saved": len(clean)}


@router.delete("/funds/{sec_id}")
def clear_fund_currencies(sec_id: int):
    with get_db() as conn:
        conn.cursor().execute("DELETE FROM Fund_Currency_Exposure WHERE Securities_Id = %s", (sec_id,))
    return {"cleared": sec_id}


# ── The report ──────────────────────────────────────────────────────────────────

@router.get("")
def get_currency_exposure(account_ids: Optional[str] = Query(None), include_liabilities: bool = Query(False)):
    acct_ids = _parse_account_ids(account_ids)
    clause = _acct_clause(acct_ids, "h.Accounts_Id")
    with get_db() as conn:
        held = load_held(conn, clause)
        cash = load_cash_accounts(conn, acct_ids)
        debts = load_cash_accounts(conn, acct_ids, LIABILITY_TYPES) if include_liabilities else None
        rate_rows = pd.read_sql("""SELECT DISTINCT ON (c.Currencies_Id) c.Currencies_ShortName AS ccy, h.FX_Rate::float AS rate
                                FROM Historical_FX h JOIN Currencies c ON c.Currencies_Id = h.Currencies_Id_1
                                ORDER BY c.Currencies_Id, h.Date DESC""", conn)
        ccy_rows = pd.read_sql("SELECT Securities_Id AS sid, Currency AS ccy, Weight_Pct::float AS w FROM Fund_Currency_Exposure", conn)
        ctry_rows = pd.read_sql("SELECT Securities_Id AS sid, Kind AS kind, Country AS country, Weight_Pct::float AS w FROM Fund_Country_Exposure", conn)
        mix = pd.read_sql("SELECT Securities_Id AS sid, Asset_Bond_Pct::float AS bond, Asset_Class_Override AS override FROM Fund_Composition", conn)
    own: dict[int, list] = {}
    for r in ccy_rows.itertuples(index=False):
        own.setdefault(int(r.sid), []).append((r.ccy, float(r.w)))
    by_country: dict[int, list] = {}
    for r in ctry_rows.itertuples(index=False):
        by_country.setdefault(int(r.sid), []).append((r.kind, r.country, float(r.w)))
    bond_pct = {int(r.sid): r.bond for r in mix.itertuples(index=False)}
    commodity = {int(r.sid) for r in mix.itertuples(index=False) if (r.override or "") == "Commodities"}

    items = []            # (key, category, sid, aid, name, ticker, value, via)
    derived_funds, plain_funds = [], []
    for r in held.itertuples(index=False):
        sid, value = int(r.sid), float(r.value_eur)
        if r.stype not in FUND_TYPES:
            ccy = "USD" if r.stype in DOLLAR_PRICED else r.ccy
            items.append((ccy, _direct_category(r.stype), sid, None, r.name, r.ticker, value, "Direct"))
            continue
        category = _fund_kind(bond_pct.get(sid))
        hedged = bool(re.search(r"hedged", r.name or "", re.I))
        if sid in own:                                                     # the fund's own currency rows
            covered = 0.0
            for c, w in own[sid]:
                covered += w
                items.append((c, category, sid, None, r.name, r.ticker, value * w / 100.0, "Fund"))
            if covered < 99.995:
                items.append((UNSPEC, category, sid, None, r.name, r.ticker, value * (100.0 - covered) / 100.0, "Fund"))
        elif hedged:                                                       # a hedged share class: its own currency
            items.append((r.ccy, category, sid, None, r.name, r.ticker, value, "Fund"))
            derived_funds.append(r.ticker or r.name)
        elif sid in by_country:                                            # derived from the country breakdown
            covered = 0.0
            for kind, code, w in by_country[sid]:
                covered += w
                ccy = country_currency(code) if kind == "Stocks" else r.ccy        # bonds: denominated in the fund's currency
                items.append((ccy or UNSPEC, category if kind != "Stocks" else "Stocks", sid, None, r.name, r.ticker, value * w / 100.0, "Fund"))
            if covered < 99.995:
                items.append((UNSPEC, category, sid, None, r.name, r.ticker, value * (100.0 - covered) / 100.0, "Fund"))
            derived_funds.append(r.ticker or r.name)
        elif sid in commodity:                                             # gold, silver, oil …: priced in US dollars
            items.append(("USD", "Commodities", sid, None, r.name, r.ticker, value, "Fund"))
            derived_funds.append(r.ticker or r.name)
        else:                                                              # nothing to look through
            items.append((r.ccy, "Funds (no look-through)", sid, None, r.name, r.ticker, value, "Fund"))
            plain_funds.append({"securities_id": sid, "name": r.name, "ticker": r.ticker, "value_eur": round(value, 2)})
    for r in cash.itertuples(index=False):
        items.append((r.ccy, "Cash & Deposits", None, int(r.aid), r.name, r.atype, float(r.value_eur), "Account"))

    if debts is not None:                                                  # credit cards, loans …: negative, netted off
        for r in debts.itertuples(index=False):
            items.append((r.ccy, "Liabilities", None, int(r.aid), r.name, r.atype, float(r.value_eur), "Account"))

    df = pd.DataFrame(items, columns=["key", "category", "sid", "aid", "name", "ticker", "value", "via"])
    total = float(df["value"].sum()) if not df.empty else 0.0
    currencies = build_tree(df, total, lambda c: SPECIAL_NAMES.get(c) or c, set(SPECIAL_NAMES))
    rates = {r.ccy: r.rate for r in rate_rows.itertuples(index=False)} | {"EUR": 1.0}
    for c in currencies:                  # amount in the currency itself, and what a 5% move in it is worth in EUR
        rate = rates.get(c["code"])
        c["rate"] = rate
        c["native_value"] = round(c["value_eur"] / rate, 2) if rate else None
        c["impact_5pct_eur"] = round(c["value_eur"] * 0.05, 2) if rate and c["code"] != "EUR" else None
    return {
        "total_eur": round(total, 2), "countries": currencies, "include_liabilities": include_liabilities,            # same shape as the country report
        "uncovered_funds": sorted(plain_funds, key=lambda u: -u["value_eur"]),
        "derived_funds": sorted(set(derived_funds)),
        "notes": [
            "Direct stocks and bonds count in their own currency; cash accounts in the account's currency; crypto and commodities in US dollars (they are priced in dollars, so a EUR quote on a European exchange is only a conversion). A fund is spread over the currencies of what it holds: its own currency rows where it has them (downloaded for iShares funds from their holdings' market currencies, or typed in under Security Detail → Composition → Currency exposure), and a hedged share class is entirely its own currency.",
            "Where a fund has no currency rows but has a country breakdown, its stocks are placed by each country's home currency and its bonds in the fund's own currency (\"derived\" — an approximation: a US-listed company earning in euros still counts as USD). A fund with neither is shown in its quoting currency under \"Funds (no look-through)\".",
            "An unhedged commodity fund (gold, silver, a commodity swap) is placed in US dollars — they are priced in dollars, whatever currency the fund trades in; a hedged one is its own currency. This is the currency of the holdings, not of the underlying earnings, and it ignores any currency hedging a fund does beyond its hedged share class. Amounts are in EUR at today's rates. \"5% FX move\" is what a 5% change in that currency against the euro would add or subtract (not shown for EUR).",
        ],
    }
