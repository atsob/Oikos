"""Exposure by country (Reports -> Inv. Portfolio -> Portfolio Analysis -> Country Exposure).

Country -> category -> instrument, in EUR, over the investment holdings of the chosen accounts.

Direct holdings
    Stock                      -> "Stocks"            country: Securities.Country, else the ISIN's prefix
    Bond                       -> "Government Bonds" / "Corporate Bonds"
                                  government when the issuer is flagged (Issuers.Is_Government), else the
                                  security's sector is Government or the issuer/name reads like a state
                                  (Republic, Treasury, T-Bill, …); country: Securities.Country, else the
                                  issuer's, else the ISIN's prefix
    CD                         -> "Cash & Deposits"
    Crypto / Commodity / other -> no country ("Not country-specific")
Cash accounts (Cash / Checking / Savings in the chosen accounts), at today's balance in EUR
                               -> "Cash & Deposits"; country: the IBAN's prefix, else the bank's BIC
                                  (its 5th-6th letters); an account with neither is "Country unknown"
Funds (ETF / Mutual Fund)
    A fund's value is spread over the rows of Fund_Country_Exposure (kind, country, % of the fund) the
    user has entered; whatever the rows don't cover is "Not broken down by country", and a fund with no
    rows at all is shown whole under "No country data" so the total still reconciles.
"""
from __future__ import annotations

import re
from typing import Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from api.routers.reports import _acct_clause, _parse_account_ids, _pit_ctes
from database.connection import get_db
from database.countries import COUNTRIES, country_name, isin_country, parse_country

router = APIRouter()

KINDS = ("Stocks", "Government Bonds", "Corporate Bonds", "Other")
FUND_TYPES = ("ETF", "Mutual Fund")
NO_COUNTRY = "NONE"            # crypto, commodities … — no country applies
UNSPECIFIED = "UNSPEC"         # part of a fund the entered rows don't cover
NO_DATA = "NODATA"             # fund with no country rows at all
UNKNOWN = "UNKNOWN"            # a stock/bond whose country can't be worked out (no ISIN, no country set)
SPECIAL_NAMES = {NO_COUNTRY: "Not country-specific", UNSPECIFIED: "Not broken down by country", NO_DATA: "No country data",
                 UNKNOWN: "Country unknown"}

_GOV_RE = re.compile(r"republic|government|govt|treasury|t-?bill|bund|gilt|sovereign|kingdom of|state of|ministry|\bOAT\b|\bBTP\b|\bBONO", re.I)


def _is_government(sector, issuer, name, issuer_flag) -> bool:
    if issuer_flag is not None and not pd.isna(issuer_flag):
        return bool(issuer_flag)
    if sector and str(sector).strip().lower() == "government":
        return True
    return bool(_GOV_RE.search(f"{issuer or ''} {name or ''}"))


def _direct_class(r):
    """(category, country code or None) for a directly held security."""
    t = r.stype
    if t in ("Stock", "Emp. Stock Opt."):
        return "Stocks", (r.country or isin_country(r.isin))
    if t == "Bond":
        kind = "Government Bonds" if _is_government(r.sector, r.issuer, r.name, r.issuer_gov) else "Corporate Bonds"
        return kind, (r.country or r.issuer_country or isin_country(r.isin))
    if t == "CD":
        return "Cash & Deposits", (r.country or isin_country(r.isin))
    if t == "Crypto":
        return "Crypto", None
    if t == "Commodity":
        return "Commodities", None
    return "Other", (r.country or None)


# ── Countries & a fund's own country rows ───────────────────────────────────────

@router.get("/countries")
def list_countries():
    return [{"code": c, "name": n} for c, n in sorted(COUNTRIES.items(), key=lambda kv: kv[1])]


def _provider_for(url: str) -> str:
    u = (url or "").lower()
    if "ishares.com" in u or "blackrock.com" in u:
        return "ishares"
    if "vaneck.com" in u:
        return "vaneck"
    if "invesco.com" in u:
        return "invesco"
    if "vanguard" in u:
        return "vanguard"
    if not u.startswith("http"):
        raise HTTPException(400, "Enter a web address (a product page, or a direct link to a CSV/XLSX holdings file); leave it empty to use justETF by ISIN")
    return "file"


@router.get("/funds/{sec_id}/source")
def get_fund_source(sec_id: int):
    with get_db() as conn:
        df = pd.read_sql("SELECT Provider AS provider, Url AS url, Kind AS kind FROM Fund_Country_Sources WHERE Securities_Id = %(s)s",
                         conn, params={"s": sec_id})
    return df.iloc[0].where(df.iloc[0].notna(), None).to_dict() if not df.empty else {"provider": None, "url": None, "kind": None}


@router.put("/funds/{sec_id}/source")
def set_fund_source(sec_id: int, data: dict):
    """Set (or clear, with an empty url) the provider page the downloader refreshes this fund from."""
    url = (data.get("url") or "").strip()
    kind = data.get("kind") or None
    if kind is not None and kind not in KINDS:
        raise HTTPException(400, f"Kind must be one of {', '.join(KINDS)}")
    with get_db() as conn:
        cur = conn.cursor()
        if not url:
            cur.execute("DELETE FROM Fund_Country_Sources WHERE Securities_Id = %s", (sec_id,))
            return {"cleared": True}
        provider = _provider_for(url)
        cur.execute("""INSERT INTO Fund_Country_Sources (Securities_Id, Provider, Url, Kind) VALUES (%s, %s, %s, %s)
                       ON CONFLICT (Securities_Id) DO UPDATE SET Provider = EXCLUDED.Provider, Url = EXCLUDED.Url,
                       Kind = EXCLUDED.Kind, Updated_At = NOW()""", (sec_id, provider, url, kind))
    return {"provider": provider}


@router.post("/download")
def download_now(data: dict = {}):
    """Refresh country rows from the providers — one fund (security_id) or every held fund. force=true also
    replaces rows typed in by hand."""
    from data.country_exposure_downloader import download_country_exposure
    try:
        res = download_country_exposure(target_sec_id=data.get("security_id"), force=bool(data.get("force")))
    except Exception as e:
        raise HTTPException(500, str(e))
    summary = f"{res['updated']} updated, {res['skipped']} kept, {res['errors']} failed"
    return {"ok": res["errors"] == 0, "message": summary + (": " + "; ".join(f"{f['name']}: {f['message']}" for f in res['funds'] if f['status'] == 'error')[:300] if res["errors"] else ""), **res}


@router.get("/funds/{sec_id}")
def get_fund_exposure(sec_id: int):
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT Kind AS kind, Country AS country, Weight_Pct::float AS weight_pct, Source AS source, As_Of::text AS as_of, Origin AS origin
            FROM Fund_Country_Exposure WHERE Securities_Id = %(s)s ORDER BY Kind, Weight_Pct DESC
        """, conn, params={"s": sec_id})
        fc = pd.read_sql("""
            SELECT Asset_Stock_Pct::float AS stock, Asset_Bond_Pct::float AS bond, Category_Name AS category
            FROM Fund_Composition WHERE Securities_Id = %(s)s
        """, conn, params={"s": sec_id})
    rows = [{**r, "name": country_name(r["country"])} for r in df.to_dict(orient="records")]
    mix = fc.iloc[0].to_dict() if not fc.empty else {}
    return {
        "rows": [{k: v for k, v in r.items() if k not in ("source", "as_of", "origin")} for r in rows],
        "source": rows[0]["source"] if rows else None, "as_of": rows[0]["as_of"] if rows else None,
        "origin": rows[0]["origin"] if rows else None,
        "total_pct": round(sum(r["weight_pct"] for r in rows), 4),
        # Hints for the editor: which kind a fund's rows probably are.
        "asset_mix": {k: (None if pd.isna(v) else v) for k, v in mix.items()},
    }


@router.put("/funds/{sec_id}")
def save_fund_exposure(sec_id: int, data: dict):
    rows = data.get("rows") or []
    clean, seen = [], set()
    for r in rows:
        kind = r.get("kind")
        code = parse_country(r.get("country"))
        try:
            w = float(r.get("weight_pct"))
        except (TypeError, ValueError):
            raise HTTPException(400, f"Bad weight for {r.get('country')!r}")
        if kind not in KINDS:
            raise HTTPException(400, f"Kind must be one of {', '.join(KINDS)}")
        if code is None:
            raise HTTPException(400, f"Unknown country {r.get('country')!r}")
        if not 0 <= w <= 100:
            raise HTTPException(400, f"Weight for {country_name(code)} must be between 0 and 100")
        if (kind, code) in seen:
            raise HTTPException(400, f"{country_name(code)} appears twice under {kind}")
        seen.add((kind, code))
        clean.append((kind, code, w))
    if sum(w for _, _, w in clean) > 100.5:
        raise HTTPException(400, "The weights add up to more than 100%")
    source = (data.get("source") or "").strip() or None
    as_of = (data.get("as_of") or "").strip() or None
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM Securities WHERE Securities_Id = %s", (sec_id,))
        if cur.fetchone() is None:
            raise HTTPException(404, "Security not found")
        cur.execute("DELETE FROM Fund_Country_Exposure WHERE Securities_Id = %s", (sec_id,))
        for kind, code, w in clean:
            cur.execute("""INSERT INTO Fund_Country_Exposure (Securities_Id, Kind, Country, Weight_Pct, Source, As_Of, Origin)
                           VALUES (%s, %s, %s, %s, %s, %s, 'manual')""", (sec_id, kind, code, w, source, as_of))
    return {"saved": len(clean)}


@router.delete("/funds/{sec_id}")
def clear_fund_exposure(sec_id: int):
    with get_db() as conn:
        conn.cursor().execute("DELETE FROM Fund_Country_Exposure WHERE Securities_Id = %s", (sec_id,))
    return {"cleared": sec_id}


_LINE_RE = re.compile(r"^\s*(.*?)\s*[\t;:|,]\s*(-?\d+(?:[.,]\d+)?)\s*%?\s*$|^\s*(.*?[A-Za-z\.\)])\s+(-?\d+(?:[.,]\d+)?)\s*%?\s*$")


@router.post("/parse")
def parse_pasted(data: dict):
    """Turn text copied from a provider's page ("France  40.8", "Italy;34", "DE\t12,5 %") into rows.
    Lines that don't read as a country and a number are returned in `unrecognised`."""
    rows, bad = [], []
    for line in str(data.get("text") or "").splitlines():
        if not line.strip():
            continue
        m = _LINE_RE.match(line)
        name, num = (m.group(1), m.group(2)) if m and m.group(1) is not None else ((m.group(3), m.group(4)) if m else (None, None))
        code = parse_country(name) if name else None
        if code is None:
            bad.append(line.strip())
            continue
        rows.append({"country": code, "name": country_name(code), "weight_pct": float(num.replace(",", "."))})
    return {"rows": rows, "unrecognised": bad}


# ── The report ──────────────────────────────────────────────────────────────────

# ── Shared by the Country and Currency exposure reports ─────────────────────────

CASH_TYPES = ("Cash", "Checking", "Savings")


def load_held(conn, clause: str) -> pd.DataFrame:
    """Held investments (value in EUR at today's prices) with what both reports need to classify them."""
    fx_cte, prices_cte, h_src_cte = _pit_ctes(None)
    held = pd.read_sql(f"""
        WITH {fx_cte}, {prices_cte}, {h_src_cte}
        SELECT s.Securities_Id AS sid, s.Securities_Name AS name, s.Ticker AS ticker, s.Securities_Type::text AS stype,
               s.ISIN AS isin, s.Country AS country, s.Sector AS sector, cur.Currencies_ShortName AS ccy,
               iss.Issuers_Name AS issuer, iss.Country AS issuer_country, iss.Is_Government AS issuer_gov,
               SUM(h.Quantity * COALESCE(p.Close, 0) * COALESCE(fx.FX_Rate, 1))::float AS value_eur
        FROM h_src h
        JOIN Securities s ON s.Securities_Id = h.Securities_Id
        JOIN Currencies cur ON cur.Currencies_Id = s.Currencies_Id
        LEFT JOIN Issuers iss ON iss.Issuers_Id = s.Issuer_Id
        LEFT JOIN prices p ON p.Securities_Id = h.Securities_Id
        LEFT JOIN fx ON fx.Currencies_Id_1 = s.Currencies_Id
        WHERE h.Quantity > 0 {clause}
        GROUP BY s.Securities_Id, s.Securities_Name, s.Ticker, s.Securities_Type, s.ISIN, s.Country, s.Sector, cur.Currencies_ShortName,
                 iss.Issuers_Name, iss.Country, iss.Is_Government
    """, conn)
    held = held[held["value_eur"].fillna(0) > 0.005]
    return held.astype(object).where(held.notna(), None)          # NaN -> None, so "or" fallbacks work


def load_cash_accounts(conn, acct_ids, types=CASH_TYPES) -> pd.DataFrame:
    """Cash / Checking / Savings accounts in the selection (all of them when none is chosen), with today's balance in EUR
    (the stored balance includes future-dated entries, which are taken back out)."""
    clause = _acct_clause(acct_ids, "a.Accounts_Id")
    df = pd.read_sql(f"""
        SELECT a.Accounts_Id AS aid, a.Accounts_Name AS name, a.Accounts_Type::text AS atype, a.IBAN AS iban,
               inst.BIC_Code AS bic, c.Currencies_ShortName AS ccy,
               ((COALESCE(a.Accounts_Balance, 0) - COALESCE((
                    SELECT SUM(t.Total_Amount) FROM Transactions t
                    WHERE t.Accounts_Id = a.Accounts_Id AND t.Date > CURRENT_DATE AND NOT COALESCE(t.Is_Draft, FALSE)), 0))
                * CASE WHEN c.Currencies_ShortName = 'EUR' THEN 1.0
                       ELSE COALESCE((SELECT h.FX_Rate FROM Historical_FX h WHERE h.Currencies_Id_1 = c.Currencies_Id
                                      ORDER BY h.Date DESC LIMIT 1), 1.0) END)::float AS value_eur
        FROM Accounts a
        JOIN Currencies c ON c.Currencies_Id = a.Currencies_Id
        LEFT JOIN Institutions inst ON inst.Institutions_Id = a.Institutions_Id
        WHERE a.Is_Active AND a.Accounts_Type::text = ANY(%(types)s) {clause}
    """, conn, params={"types": list(types)})
    df = df[df["value_eur"].abs() > 0.005].copy()
    def _country(r):
        iban = r.iban if isinstance(r.iban, str) else ""
        bic = r.bic if isinstance(r.bic, str) else ""
        return (isin_country(iban[:2]) if len(iban) >= 2 else None) or (isin_country(bic[4:6]) if len(bic) >= 6 else None)
    df["country"] = df.apply(_country, axis=1)
    return df


def build_tree(df: pd.DataFrame, total: float, name_of, special: set) -> list:
    """df columns: key, category, sid, aid, name, ticker, value, via -> [{code, name, value_eur, pct, special, categories:[{items}]}]"""
    out = []
    for code, cg in df.groupby("key"):
        cats = []
        for cat, g in cg.groupby("category"):
            inst = g.fillna({"sid": 0, "aid": 0}).groupby(["sid", "aid", "name", "ticker", "via"], as_index=False)["value"].sum().sort_values("value", ascending=False)
            cv = float(g["value"].sum())
            cats.append({
                "category": cat, "value_eur": round(cv, 2), "pct": round(cv / total * 100, 2) if total else 0.0,
                "items": [{"securities_id": int(i.sid) or None, "account_id": int(i.aid) or None, "name": i.name, "ticker": i.ticker, "via": i.via,
                           "value_eur": round(float(i.value), 2), "pct": round(float(i.value) / total * 100, 2) if total else 0.0}
                          for i in inst.itertuples(index=False)],
            })
        cats.sort(key=lambda c: -c["value_eur"])
        cval = float(cg["value"].sum())
        out.append({"code": code, "name": name_of(code), "value_eur": round(cval, 2), "pct": round(cval / total * 100, 2) if total else 0.0,
                    "special": code in special, "categories": cats})
    out.sort(key=lambda c: (c["special"], -c["value_eur"]))
    return out


@router.get("")
def get_country_exposure(account_ids: Optional[str] = Query(None)):
    acct_ids = _parse_account_ids(account_ids)
    clause = _acct_clause(acct_ids, "h.Accounts_Id")
    with get_db() as conn:
        held = load_held(conn, clause)
        cash = load_cash_accounts(conn, acct_ids)
        exposure = pd.read_sql("""
            SELECT Securities_Id AS sid, Kind AS kind, Country AS country, Weight_Pct::float AS w
            FROM Fund_Country_Exposure
        """, conn)
    by_fund: dict[int, list] = {}
    for r in exposure.itertuples(index=False):
        by_fund.setdefault(int(r.sid), []).append((r.kind, r.country, float(r.w)))

    items = []            # (key, category, sid, aid, name, ticker, value, via)
    uncovered = []        # funds with no country rows
    for r in held.itertuples(index=False):
        sid, value = int(r.sid), float(r.value_eur)
        if r.stype in FUND_TYPES:
            rows = by_fund.get(sid)
            if not rows:
                items.append((NO_DATA, "Funds", sid, None, r.name, r.ticker, value, "Fund"))
                uncovered.append({"securities_id": sid, "name": r.name, "ticker": r.ticker, "value_eur": round(value, 2)})
                continue
            covered = 0.0
            for kind, code, w in rows:
                covered += w
                items.append((code, kind, sid, None, r.name, r.ticker, value * w / 100.0, "Fund"))
            if covered < 99.995:
                items.append((UNSPECIFIED, "Not broken down", sid, None, r.name, r.ticker, value * (100.0 - covered) / 100.0, "Fund"))
            continue
        category, code = _direct_class(r)
        items.append((code or (NO_COUNTRY if category in ("Crypto", "Commodities") else UNKNOWN), category, sid, None, r.name, r.ticker, float(r.value_eur), "Direct"))
    for r in cash.itertuples(index=False):
        items.append((r.country or UNKNOWN, "Cash & Deposits", None, int(r.aid), r.name, r.atype, float(r.value_eur), "Account"))

    df = pd.DataFrame(items, columns=["key", "category", "sid", "aid", "name", "ticker", "value", "via"])
    total = float(df["value"].sum()) if not df.empty else 0.0
    countries = build_tree(df, total, lambda c: SPECIAL_NAMES.get(c) or country_name(c), set(SPECIAL_NAMES))
    return {
        "total_eur": round(total, 2), "countries": countries,
        "uncovered_funds": sorted(uncovered, key=lambda u: -u["value_eur"]),
        "notes": [
            "Direct stocks take their country from the security's own Country if set, otherwise from the ISIN's two-letter prefix (the issuer's home market; \"Country unknown\" when there is neither); direct bonds from the security, then the issuer's country, then the ISIN. A bond is a Government bond when its issuer is flagged as one (Static Data → Issuers), or its sector is Government, or the issuer/name reads like a state (Republic, Treasury, T-Bill …).",
            "A fund's value is spread over the country rows entered or downloaded for it (Security Detail → Composition → Country exposure), each row being a share of the fund. What those rows don't cover is shown as \"Not broken down by country\"; a fund with no rows is shown whole under \"No country data\".",
            "Cash, checking and savings accounts in the chosen accounts count at today's balance under \"Cash & Deposits\", placed by the IBAN's country, else the bank's BIC; physical cash and accounts with neither are \"Country unknown\". Credit cards, loans and property are not included.",
            "Exposure is measured at today's prices; funds' own cash/derivative sleeves are not attributed to a country.",
        ],
    }
