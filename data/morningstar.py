"""Morningstar Style Box and Morningstar Category for ETFs and funds — no Morningstar account needed.

Morningstar's own public screener (the one behind its fund pages) is asked for the fund's ISIN; it answers with the
fund's Category ("Global Large-Cap Blend Equity", "EUR Corporate Bond", "Sector Equity Utilities" …) and its Style Box
position, which is worked out from the fund's actual holdings:

  * an equity fund sits in a 3x3 size/style grid ("Large Value" … "Small Growth"), and
  * a bond fund in a 3x3 credit-quality/interest-rate-sensitivity grid ("Medium Quality / Moderate Duration" …).

Cash, money-market and commodity funds have a Category but no Style Box. The values go to Fund_Composition
(Morningstar_Style_Box, Morningstar_Category); a value typed on the Security Detail page (Category_Override for the Style Box,
Morningstar_Category_Override for the category) always wins over these and is never touched here.

The same answer carries a bond fund's Effective Duration and Effective Maturity (years). Yahoo's figures for these are often wrong for
UCITS ETFs (a euro government fund with a "maturity" of 9.9 years, equity funds with a "duration"), so they are no longer taken from Yahoo:
Morningstar's go to Fund_Composition.Bond_Duration / Bond_Maturity, where a manual value (Manual_Overrides) still wins.

Its portfolio data supplements Yahoo's: an Asset Mix or Sector Weightings Yahoo has none of is filled in from Morningstar's portfolio
(security_details), and equity sector weights Yahoo gives a fund that is not an equity fund (a bond, money-market or commodity ETF) are
removed. A mix typed in by hand (Manual_Overrides 'Asset_Mix') is never touched.

Run by the monthly "Morningstar Category & Style Box" job (scheduler.py), by the Download button under Market Data -> Downloads, or
alone:  python -m data.morningstar
"""
from __future__ import annotations

import logging
import time

import requests
from psycopg2.extras import Json

from database.connection import get_connection

log = logging.getLogger(__name__)

# The public access keys of Morningstar's own fund pages, tried in turn: Morningstar withdraws one now and then (klr5zyak8x has
# answered 401 since mid-November 2026), and the others answer the same data.
SCREENER_KEYS = ("dr6pz9spfi", "9vehuxllxs", "klr5zyak8x")
SCREENER = "https://lt.morningstar.com/api/rest.svc/{key}/security/screener"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
DATAPOINTS = "SecId|Name|ISIN|CategoryName|GlobalCategoryName|EquityStyleBox|BondStyleBox|EffectiveDuration|EffectiveMaturity"
# The same screener, asked in turn for ETFs and then for funds; the first universe that knows the ISIN answers.
UNIVERSES = ("ETALL$$ALL", "FOALL$$ALL")

# Morningstar numbers the squares left to right, top to bottom.
EQUITY_BOX = {1: "Large Value", 2: "Large Blend", 3: "Large Growth",
              4: "Mid-Cap Value", 5: "Mid-Cap Blend", 6: "Mid-Cap Growth",
              7: "Small Value", 8: "Small Blend", 9: "Small Growth"}
_QUALITY = ("High", "Medium", "Low")
_DURATION = ("Limited", "Moderate", "Extensive")
BOND_BOX = {i + 1: f"{_QUALITY[i // 3]} Quality / {_DURATION[i % 3]} Duration" for i in range(9)}


DETAILS = "https://lt.morningstar.com/api/rest.svc/klr5zyak8x/security_details/{secid}"
# Morningstar's codes for the asset classes and the 11 equity sectors -> the keys Yahoo (and so the reports) use.
_ASSET_CLASS = {"1": "stock", "3": "bond", "5": "preferred", "6": "convertible", "7": "cash", "8": "other", "99": "other"}
ASSET_COLUMNS = {"cash": "Asset_Cash_Pct", "stock": "Asset_Stock_Pct", "bond": "Asset_Bond_Pct",
                 "preferred": "Asset_Preferred_Pct", "convertible": "Asset_Convertible_Pct", "other": "Asset_Other_Pct"}
_SECTOR = {"101": "basic_materials", "102": "consumer_cyclical", "103": "financial_services", "104": "realestate",
           "205": "consumer_defensive", "206": "healthcare", "207": "utilities", "308": "communication_services",
           "309": "energy", "310": "industrials", "311": "technology"}
STOCK_SECTOR_KEYS = set(_SECTOR.values())


def portfolio(secid: str) -> dict | None:
    """-> {"date", "mix": {stock, bond, cash, …} as fractions adding up to 1, "sectors": {technology, …} as fractions} from the latest portfolio
    Morningstar holds for the fund; None when it has none."""
    r = requests.get(DETAILS.format(secid=secid), headers=HEADERS, timeout=40, params={
        "viewId": "ETFsnapshot", "idtype": "msid", "responseViewFormat": "json", "languageId": "en-GB", "currencyId": "EUR"})
    r.raise_for_status()
    data = r.json()
    ports = (data[0].get("Portfolios") if data else None) or []
    if not ports:
        return None
    p = max(ports, key=lambda x: x.get("Date", ""))
    mix: dict = {}
    for a in p.get("AssetAllocations", []):
        if a.get("Type") == "MorningStarDefault" and a.get("SalePosition") == "N":          # the net position
            for b in a.get("BreakdownValues", []):
                k = _ASSET_CLASS.get(str(b.get("Type")), "other")
                mix[k] = mix.get(k, 0.0) + max(float(b.get("Value") or 0), 0.0)             # a short derivative leg is not a negative share
    total = sum(mix.values())
    mix = {k: v / total for k, v in mix.items()} if total > 0 else {}
    sectors: dict = {}
    for a in p.get("GlobalStockSectorBreakdown", []):
        if a.get("SalePosition") == "N":
            for b in a.get("BreakdownValues", []):
                k = _SECTOR.get(str(b.get("Type")))
                if k and float(b.get("Value") or 0) > 0:
                    sectors[k] = float(b["Value"])
    stotal = sum(sectors.values())
    sectors = {k: v / stotal for k, v in sectors.items()} if stotal >= 50 else {}          # percentages of the equity part; anything thinner is not a breakdown
    return {"date": (p.get("Date") or "")[:10] or None, "mix": mix, "sectors": sectors}


def clean_non_equity_sectors(cur) -> int:
    """Remove the equity sector weights Yahoo gives funds that are not equity funds (a high-yield bond ETF "100% financial services", the
    money-market and commodity-swap ETFs with a technology share …): they only distort the Sector Weighting report. Funds whose Asset Mix
    says under half stocks lose the 11 stock-sector keys; any bond-sector keys stay. Returns the number of funds changed."""
    cur.execute("""SELECT Securities_Id, Sector_Weightings FROM Fund_Composition
                   WHERE Sector_Weightings IS NOT NULL
                     AND COALESCE(Asset_Cash_Pct,0) + COALESCE(Asset_Stock_Pct,0) + COALESCE(Asset_Bond_Pct,0) + COALESCE(Asset_Preferred_Pct,0)
                         + COALESCE(Asset_Convertible_Pct,0) + COALESCE(Asset_Other_Pct,0) > 0
                     AND COALESCE(Asset_Stock_Pct,0) < 0.5""")
    n = 0
    for sec_id, sw in cur.fetchall():
        if not isinstance(sw, dict) or not (STOCK_SECTOR_KEYS & set(sw)):
            continue
        kept = {k: v for k, v in sw.items() if k not in STOCK_SECTOR_KEYS}
        cur.execute("UPDATE Fund_Composition SET Sector_Weightings = %s WHERE Securities_Id = %s", (Json(kept) if kept else None, sec_id))
        n += 1
    return n


_working_key: str | None = None


def _screener(params: dict) -> requests.Response:
    """The screener's answer, from the first access key Morningstar still accepts (remembered for the next call)."""
    global _working_key
    keys = ([_working_key] if _working_key else []) + [k for k in SCREENER_KEYS if k != _working_key]
    for key in keys:
        r = requests.get(SCREENER.format(key=key), headers=HEADERS, timeout=30, params=params)
        if r.status_code in (401, 403):
            log.warning("Morningstar screener key %s refused (HTTP %s)", key, r.status_code)
            continue
        r.raise_for_status()
        _working_key = key
        return r
    raise RuntimeError("Morningstar refused every screener access key (HTTP 401/403)")


def lookup(isin: str) -> dict | None:
    """-> {"id", "category", "broad", "style_box", "duration", "maturity"} for an ISIN, or None when Morningstar does not know it."""
    for universe in UNIVERSES:
        r = _screener({"page": 1, "pageSize": 20, "outputType": "json", "version": 1, "languageId": "en-GB", "currencyId": "EUR",
                       "universeIds": universe, "securityDataPoints": DATAPOINTS, "term": isin})
        rows = [x for x in r.json().get("rows", []) if x.get("ISIN") == isin]      # the term also matches names
        if not rows:
            continue
        best = next((x for x in rows if x.get("CategoryName")), rows[0])           # the listing lines of one fund agree; take one with data
        box = EQUITY_BOX.get(best.get("EquityStyleBox")) or BOND_BOX.get(best.get("BondStyleBox"))
        return {"id": best.get("SecId"), "category": best.get("CategoryName") or None, "style_box": box,
                "duration": best.get("EffectiveDuration"), "maturity": best.get("EffectiveMaturity"),
                "broad": best.get("GlobalCategoryName") or None}
    return None


def download_morningstar(target_sec_id: int | None = None) -> dict:
    """Fill Fund_Composition's Morningstar columns for the active ETFs / funds that have an ISIN. A fund Morningstar does not know,
    or knows without a value, keeps what it had."""
    conn = get_connection()
    cur = conn.cursor()
    done = missing = errors = 0
    try:
        sql = """SELECT Securities_Id, Securities_Name, ISIN FROM Securities
                 WHERE Securities_Type::text IN ('ETF','Mutual Fund') AND Is_Active AND COALESCE(TRIM(ISIN), '') <> ''"""
        params: tuple = ()
        if target_sec_id:
            sql += " AND Securities_Id = %s"
            params = (target_sec_id,)
        cur.execute(sql, params)
        funds = cur.fetchall()
        print(f"Morningstar category / style box for {len(funds)} fund(s)…")
        for sec_id, name, isin in funds:
            try:
                res = lookup(isin.strip())
            except Exception as e:                                                  # a hiccup on one fund must not stop the rest
                errors += 1
                log.warning("Morningstar lookup failed for %s (%s): %s", name, isin, e)
                continue
            if not res or not (res["category"] or res["style_box"]):
                missing += 1
                print(f"  ⚠️ {name}: not found at Morningstar")
                continue
            cur.execute("""
                INSERT INTO Fund_Composition (Securities_Id, Morningstar_Id, Morningstar_Category, Morningstar_Style_Box, Morningstar_Updated,
                                              Bond_Duration, Bond_Maturity, Morningstar_Broad_Category)
                VALUES (%s, %s, %s, %s, NOW(), %s, %s, %s)
                ON CONFLICT (Securities_Id) DO UPDATE SET
                    Morningstar_Id        = COALESCE(EXCLUDED.Morningstar_Id, Fund_Composition.Morningstar_Id),
                    Morningstar_Category  = COALESCE(EXCLUDED.Morningstar_Category, Fund_Composition.Morningstar_Category),
                    Morningstar_Style_Box = COALESCE(EXCLUDED.Morningstar_Style_Box, Fund_Composition.Morningstar_Style_Box),
                    Morningstar_Updated   = NOW(),
                    Morningstar_Broad_Category = COALESCE(EXCLUDED.Morningstar_Broad_Category, Fund_Composition.Morningstar_Broad_Category),
                    -- Morningstar's figure when it has one. Without one, a bond fund keeps what it had; any other fund loses a
                    -- duration/maturity that can only be Yahoo's mistake.
                    Bond_Duration = CASE WHEN EXCLUDED.Bond_Duration IS NOT NULL OR COALESCE(Fund_Composition.Asset_Bond_Pct, 0) >= 0.5
                                         THEN COALESCE(EXCLUDED.Bond_Duration, Fund_Composition.Bond_Duration) END,
                    Bond_Maturity = CASE WHEN EXCLUDED.Bond_Maturity IS NOT NULL OR COALESCE(Fund_Composition.Asset_Bond_Pct, 0) >= 0.5
                                         THEN COALESCE(EXCLUDED.Bond_Maturity, Fund_Composition.Bond_Maturity) END""",
                (sec_id, res["id"], res["category"], res["style_box"], res["duration"], res["maturity"], res["broad"]))
            done += 1
            filled = []
            try:
                pf = portfolio(res["id"]) if res.get("id") else None
            except Exception as e:                                                  # the portfolio is a bonus: the category is already saved
                pf = None
                log.warning("Morningstar portfolio failed for %s: %s", name, e)
            if pf:
                cur.execute("UPDATE Fund_Composition SET Morningstar_Portfolio_Date = %s WHERE Securities_Id = %s", (pf["date"], sec_id))
                if pf["mix"]:
                    cols = list(ASSET_COLUMNS.items())
                    cur.execute(f"""UPDATE Fund_Composition SET {", ".join(f"{c} = %s" for _, c in cols)}
                                    WHERE Securities_Id = %s AND NOT (COALESCE(Manual_Overrides, '{{}}'::jsonb) ? 'Asset_Mix')
                                      AND COALESCE(Asset_Cash_Pct,0) + COALESCE(Asset_Stock_Pct,0) + COALESCE(Asset_Bond_Pct,0)
                                          + COALESCE(Asset_Preferred_Pct,0) + COALESCE(Asset_Convertible_Pct,0) + COALESCE(Asset_Other_Pct,0) = 0""",
                                (*[round(pf["mix"].get(k, 0.0), 4) for k, _ in cols], sec_id))
                    if cur.rowcount:
                        filled.append("asset mix")
                if pf["sectors"]:
                    cur.execute("UPDATE Fund_Composition SET Sector_Weightings = %s WHERE Securities_Id = %s AND Sector_Weightings IS NULL",
                                (Json({k: round(v, 4) for k, v in pf["sectors"].items()}), sec_id))
                    if cur.rowcount:
                        filled.append("sector weights")
            dm = f" | duration {res['duration']:.2f}y, maturity {res['maturity']:.2f}y" if res["duration"] is not None and res["maturity"] is not None else ""
            print(f"  ✔ {name}: {res['category']} | {res['style_box'] or 'no style box'}{dm}{' | filled in: ' + ', '.join(filled) if filled else ''}")
            time.sleep(0.15)
        cleaned = clean_non_equity_sectors(cur)
        if cleaned:
            print(f"  Removed equity sector weights from {cleaned} fund(s) that are not equity funds.")
        labelled = fill_sector_industry(cur, target_sec_id)
        if labelled:
            print(f"  Filled in Sector / Industry for {labelled} fund(s).")
        conn.commit()
        print(f"Morningstar complete — {done} updated, {missing} not found, {errors} error(s).")
        return {"updated": done, "not_found": missing, "errors": errors, "labelled": labelled}
    finally:
        cur.close()
        conn.close()


# ── Sector / Industry for funds ──────────────────────────────────────────────────────────────────
# A fund's Securities.Sector is its asset class (Equity, Fixed Income, Commodities) and its Industry what it holds within it ("Broad market",
# "Government bonds", "Gold", "Utilities" …), the labels used across Oikos (the Stress Test reads them). Both are worked out from the fund's
# Morningstar Category, and from its name where the category says too little ("Other Equity", "Commodities - Precious Metals").
_EQUITY_THEMES = (  # (words in the category or name, Industry) — the first match wins
    (("technology", "semiconductor", "robotics", "artificial intelligence", "cyber", "digital", "cloud", "software"), "Information Technology"),
    (("health", "biotech", "pharma", "medical"), "Health Care"),
    (("utilities", "water"), "Utilities"),
    (("energy", "oil", "uranium", "nuclear", "clean power"), "Energy"),
    (("precious metals", "gold miners", "gold mining", "mining", "natural resources", "materials", "lithium", "battery"), "Materials"),
    (("financial", "bank", "insurance"), "Financials"),
    (("real estate", "property", "reit"), "Real Estate"),
    (("industrial", "infrastructure", "aerospace", "defence", "defense", "space"), "Industrials"),
    (("consumer goods", "consumer staples", "food"), "Consumer Staples"),
    (("consumer discretionary", "luxury"), "Consumer Discretionary"),
    (("communication", "media", "telecom"), "Communication Services"),
)


def classify_fund(category: str | None, name: str | None) -> tuple[str | None, str | None]:
    """(Sector, Industry) for a fund from its Morningstar Category and name; None where neither says enough."""
    c, n = (category or "").lower(), (name or "").lower()
    if not c:
        return None, None
    if c in ("other", "relative value arbitrage") or "alternative" in c and "energy" not in c:  # says nothing: the name may
        if "inflation" in n:
            return "Fixed Income", "Inflation-linked bonds"
        if "money market" in n or "overnight" in n:
            return "Fixed Income", "Money market"
        return None, None
    if "money market" in c:
        return "Fixed Income", "Money market"
    if "commodit" in c:
        if "precious" in c or "focused" in c or "gold" in n or "silver" in n:
            for metal in ("gold", "silver", "platinum", "palladium"):
                if metal in n:
                    return "Commodities", metal.capitalize()
            return "Commodities", "Precious metals"
        return "Commodities", "Diversified commodities"
    if "bond" in c or "fixed income" in c:
        if "inflation" in c:
            return "Fixed Income", "Inflation-linked bonds"
        if "high yield" in c:
            return "Fixed Income", "High yield bonds"
        if "government" in c or "treasury" in c:
            return "Fixed Income", "Government bonds"
        if "corporate" in c or "fixed term" in c:
            return "Fixed Income", "Corporate bonds"
        return "Fixed Income", "Multi-sector bonds"
    if "equity" in c or "stock" in c or "sector" in c or "alternative energy" in c:
        # a sector or theme in the category first ("Sector Equity Utilities", "Thematic Equity Water"), then in the name
        sector_like = c.startswith(("sector", "thematic", "other", "miscellaneous", "alternative energy", "equity precious"))
        for text in ((c, n) if sector_like else (c,)):
            for words, industry in _EQUITY_THEMES:
                if any(w in text for w in words):
                    return "Equity", industry
        return ("Equity", None) if sector_like else ("Equity", "Broad market")
    return None, None


def fill_sector_industry(cur, target_sec_id: int | None = None) -> int:
    """Fill an empty Sector and/or Industry of active ETFs and funds from their Morningstar Category (the override first). A value already
    there, typed by hand or downloaded, is never changed. Returns the number of funds changed."""
    sql = """SELECT s.Securities_Id, s.Securities_Name, COALESCE(fc.Morningstar_Category_Override, fc.Morningstar_Category)
             FROM Securities s JOIN Fund_Composition fc USING (Securities_Id)
             WHERE s.Securities_Type::text IN ('ETF','Mutual Fund','Closed-End Fund') AND s.Is_Active
               AND (NULLIF(TRIM(s.Sector), '') IS NULL OR NULLIF(TRIM(s.Industry), '') IS NULL)"""
    cur.execute(sql + (" AND s.Securities_Id = %s" if target_sec_id else ""), (target_sec_id,) if target_sec_id else ())
    changed = 0
    for sec_id, name, cat in cur.fetchall():
        sector, industry = classify_fund(cat, name)
        if not sector:
            continue
        cur.execute("""UPDATE Securities SET Sector = COALESCE(NULLIF(TRIM(Sector), ''), %s), Industry = COALESCE(NULLIF(TRIM(Industry), ''), %s)
                       WHERE Securities_Id = %s
                         AND (Sector IS DISTINCT FROM COALESCE(NULLIF(TRIM(Sector), ''), %s) OR Industry IS DISTINCT FROM COALESCE(NULLIF(TRIM(Industry), ''), %s))""",
                    (sector, industry, sec_id, sector, industry))
        if cur.rowcount:
            changed += 1
            print(f"  ✔ {name}: Sector {sector}, Industry {industry or '—'} (from Morningstar category {cat})")
    return changed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    download_morningstar()
