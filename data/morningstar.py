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

Run by the monthly "Morningstar Category & Style Box" job (scheduler.py), by the Download button under Market Data -> Downloads, or
alone:  python -m data.morningstar
"""
from __future__ import annotations

import logging
import time

import requests

from database.connection import get_connection

log = logging.getLogger(__name__)

SCREENER = "https://lt.morningstar.com/api/rest.svc/klr5zyak8x/security/screener"
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


def lookup(isin: str) -> dict | None:
    """-> {"id", "category", "broad", "style_box", "duration", "maturity"} for an ISIN, or None when Morningstar does not know it."""
    for universe in UNIVERSES:
        r = requests.get(SCREENER, headers=HEADERS, timeout=30, params={
            "page": 1, "pageSize": 20, "outputType": "json", "version": 1, "languageId": "en-GB", "currencyId": "EUR",
            "universeIds": universe, "securityDataPoints": DATAPOINTS, "term": isin})
        r.raise_for_status()
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
            dm = f" | duration {res['duration']:.2f}y, maturity {res['maturity']:.2f}y" if res["duration"] is not None and res["maturity"] is not None else ""
            print(f"  ✔ {name}: {res['category']} | {res['style_box'] or 'no style box'}{dm}")
            time.sleep(0.15)
        conn.commit()
        print(f"Morningstar complete — {done} updated, {missing} not found, {errors} error(s).")
        return {"updated": done, "not_found": missing, "errors": errors}
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    download_morningstar()
