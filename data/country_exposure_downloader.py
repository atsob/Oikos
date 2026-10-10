"""Download a fund's breakdown by country from its provider and keep Fund_Country_Exposure current.

Where a fund's data comes from (Fund_Country_Sources, one row per fund; none = justETF by ISIN):
    ishares   a product page on ishares.com / blackrock.com -> its daily holdings file, aggregated by the
              holding's country of risk. Full detail: equities as "Stocks"; bonds as Government /
              Corporate / Other by the issuer's sector.
    vaneck    a product page on vaneck.com (US ETFs) -> the "Country Weightings" and "Currency Exposure" tables of
              its Holdings section (VanEck's page loads them from its own content service).
    invesco   a product page on invesco.com (US ETFs) -> Invesco's own data service behind that page: the
              fund's weighting by country, and its holdings' currencies.
    vanguard  a product page on a Vanguard site -> its "Market allocation" table (the countries the
              fund lists, usually the top 15).
    justetf   justETF's country table for the fund's ISIN (the top few countries and "Other") — the
              fallback for any fund with an ISIN, and the least detailed.

A fund whose rows were typed in by hand (Origin = 'manual') is never overwritten unless asked to, and the
justETF fallback never replaces an iShares/Vanguard result. Every request has a timeout; one fund failing
doesn't stop the others.
"""
from __future__ import annotations

import io
import logging
import re
from datetime import date, datetime

import pandas as pd
import requests

from database.connection import get_connection
from database.countries import country_name, isin_country, parse_country  # noqa: F401  (isin_country re-exported for callers)

log = logging.getLogger(__name__)

UA = {"User-Agent": "Mozilla/5.0 (compatible; Oikos)"}
TIMEOUT = (10, 90)
# A lower-ranked source never replaces a higher one. "index" = a fund tracking a single-country index, set from its definition.
PROVIDER_RANK = {"manual": 3, "ishares": 2, "vanguard": 2, "invesco": 2, "vaneck": 2, "file": 2, "index": 2, "justetf": 1}

_ALIASES = {"european union": "XX", "supranational": "XX", "eurozone": "XX"}
_GOV_SECTORS = {"Treasuries", "Treasury", "Sovereign", "Government Guaranteed", "Government Sponsored", "Local Government",
                "Local Government Guarantee", "Local Government No Guarantee", "Owned No Guarantee", "Supranational"}
_SECURITISED = {"Agency Fixed Rate", "CMBS", "Covered Other", "Hybrid Collateralized", "Mortgage Collateralized",
                "Public Sector Collateralized", "ABS", "Agency CMBS", "Covered"}
_GOV_NAME = re.compile(r"gov|treasur|sovereign|bund|gilt|inflation[- ]linked", re.I)


class NoData(ValueError):
    """The provider simply has no country data for this fund (a commodity ETC, a US-listed ETF …) — not a failure."""


def _code(name) -> str | None:
    n = str(name or "").strip()
    return parse_country(n) or _ALIASES.get(n.lower())


def _get(url: str) -> requests.Response:
    r = requests.get(url, headers=UA, timeout=TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    return r


# ── Providers ───────────────────────────────────────────────────────────────────

def fetch_ishares(url: str, kind_hint: str | None = None):
    """-> (rows[(kind, country, pct)], as_of date | None, note)"""
    m = re.search(r"(https?://[^?#]*?/products/\d+(?:/[^/?#]+)?)", url)
    if not m:
        raise ValueError("Not an iShares/BlackRock product page URL (…/products/<id>/<name>)")
    raw = _get(m.group(1).rstrip("/") + "/latest-holdings.csv").text
    if "Ticker," not in raw:
        raise ValueError("The holdings file wasn't where expected (iShares may have changed its site)")
    as_of = None
    head = raw.split("\n", 1)[0]
    mo = re.search(r'"(\d{2}/\w{3}/\d{4})"', head)
    if mo:
        as_of = datetime.strptime(mo.group(1), "%d/%b/%Y").date()
    df = pd.read_csv(io.StringIO(raw[raw.index("Ticker,"):]), thousands=",").dropna(subset=["Weight (%)"])
    df["c"] = df["Location"].map(_code)

    def kind(r):
        if r["Asset Class"] == "Equity":
            return "Stocks"
        if r["Asset Class"] == "Fixed Income":
            s = r["Sector"]
            return "Government Bonds" if s in _GOV_SECTORS else ("Other" if s in _SECURITISED else "Corporate Bonds")
        return None                                            # cash, money market, futures …
    df["kind"] = df.apply(kind, axis=1)
    g = df.dropna(subset=["c", "kind"]).groupby(["kind", "c"])["Weight (%)"].sum()
    rows = sorted(((k, c, round(float(w), 3)) for (k, c), w in g.items() if w >= 0.005), key=lambda r: -r[2])
    covered = sum(r[2] for r in rows)
    note = f"{len(df):,} holdings lines" + (f", {covered:.0f}% of the fund covered" if covered < 95 else "")
    # The currencies the holdings are denominated in (iShares' "Market Currency").
    inv = df[df["kind"].notna()]
    cg = inv.groupby(inv["Market Currency"].astype(str).str.upper().str.strip())["Weight (%)"].sum()
    ccy = sorted(((c, round(float(w), 3)) for c, w in cg.items() if len(c) == 3 and w >= 0.005), key=lambda r: -r[1])
    return rows, as_of, note, ccy


INVESCO_API = "https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/{id}/{what}?idType={idt}&productType=ETF"


def _invesco_json(ident: str, idt: str, what: str, extra: str = ""):
    # Invesco's service answers a plain request but returns 406 when an Accept header is sent, so none is.
    r = requests.get(INVESCO_API.format(id=ident, what=what, idt=idt) + extra, timeout=TIMEOUT)
    if r.status_code == 404:
        raise NoData("Invesco has no data for that fund")
    r.raise_for_status()
    return r.json()


def fetch_invesco(url: str, kind_hint: str | None = None, ticker: str | None = None):
    """-> (rows, as_of, note, ccy). The product page carries the fund's CUSIP; its country weighting and holdings
    come from the data service the page itself calls (breakdown=country, holdings/fund)."""
    ident, idt = None, "cusip"
    try:                                                   # no Accept header here either — the site answers 406 to one
        html = requests.get(url, timeout=TIMEOUT).text
        mo = re.search(r'<meta\s+name="cusip"\s+content="([0-9A-Za-z]{9})"', html)
        if mo:
            ident = mo.group(1).upper()
    except requests.RequestException:
        pass
    if not ident:
        if not ticker:
            raise ValueError("Couldn't read the fund's CUSIP from that Invesco page, and the security has no ticker")
        ident, idt = ticker, "ticker"
    cw = _invesco_json(ident, idt, "weightedHoldings/fund", "&breakdown=country")
    weights = cw.get("holdingWeights") or []
    kind = kind_hint or "Stocks"
    rows, unknown = [], []
    for w in weights:
        code = _code(w.get("name"))
        if code and w.get("value") is not None:
            rows.append((kind, code, round(float(w["value"]), 4)))
        elif w.get("name"):
            unknown.append(str(w["name"]))
    if not rows:
        raise NoData("Invesco lists no country weights for that fund")
    as_of = None
    try:
        as_of = datetime.strptime(str(cw.get("effectiveDate"))[:10], "%Y-%m-%d").date()
    except ValueError:
        pass
    ccy = None
    try:                                                   # the holdings' own currencies
        hd = _invesco_json(ident, idt, "holdings/fund")
        tot: dict[str, float] = {}
        for h in hd.get("holdings") or []:
            c, pct = str(h.get("currency") or "").upper().strip(), h.get("percentageOfTotalNetAssets")
            if len(c) == 3 and pct is not None:
                tot[c] = tot.get(c, 0.0) + float(pct)
        ccy = sorted(((c, round(w, 3)) for c, w in tot.items() if w >= 0.005), key=lambda r: -r[1]) or None
    except Exception as e:                                  # currencies are a bonus; the country rows still count
        log.info("Invesco holdings currencies unavailable for %s: %s", ident, e)
    note = f"{len(rows)} countries" + (f"; not recognised: {', '.join(unknown)}" if unknown else "")
    return rows, as_of, note, ccy


_CCY_NAMES = {
    "u.s. dollar": "USD", "us dollar": "USD", "united states dollar": "USD", "euro": "EUR", "british pound": "GBP", "pound sterling": "GBP",
    "japanese yen": "JPY", "swiss franc": "CHF", "canadian dollar": "CAD", "australian dollar": "AUD", "new zealand dollar": "NZD",
    "hong kong dollar": "HKD", "singapore dollar": "SGD", "chinese yuan": "CNY", "chinese renminbi": "CNY", "south korean won": "KRW",
    "korean won": "KRW", "taiwan dollar": "TWD", "new taiwan dollar": "TWD", "indian rupee": "INR", "indonesian rupiah": "IDR",
    "swedish krona": "SEK", "norwegian krone": "NOK", "danish krone": "DKK", "polish zloty": "PLN", "czech koruna": "CZK",
    "hungarian forint": "HUF", "south african rand": "ZAR", "brazilian real": "BRL", "mexican peso": "MXN", "israeli shekel": "ILS",
    "thai baht": "THB", "malaysian ringgit": "MYR", "philippine peso": "PHP", "turkish lira": "TRY", "saudi riyal": "SAR",
    "uae dirham": "AED", "kazakhstani tenge": "KZT", "chilean peso": "CLP", "colombian peso": "COP", "peruvian sol": "PEN",
}
VANECK_BLOCKS = "https://www.vaneck.com/Main/{kind}/GetContent/?blockid={b}&pageid={p}&ticker={t}&reactlang=en&reactctr={c}&epieditmode=false&latest=false&contextmode=Default"
VANECK_DATASET = "https://www.vaneck.com/Main/HoldingsBlock/GetDataset/?blockId={b}&pageId={p}&ticker={t}"


def _vaneck_date(v):
    for fmt in ("%m/%d/%Y", "%d %b %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(v)[:11 if fmt == "%d %b %Y" else 10], fmt).date()
        except ValueError:
            continue
    return None


def fetch_vaneck(url: str, kind_hint: str | None = None):
    """-> (rows, as_of, note, ccy). The Holdings page lists its tables as content blocks; each is a small JSON
    document behind the page's own content service: a weightings block per breakdown (WeightingsType CountryOfRisk /
    Currency) and the full holdings list. US pages publish a Currency table; the UCITS (UK/EU) pages don't, so the
    currencies are then summed from the holdings list (each holding's trading currency)."""
    m = re.search(r"(https?://[^/]+/([a-z]{2})/[a-z]{2}/investments/[^/?#]+)", url)
    if not m:
        raise ValueError("Not a VanEck fund page (…/us/en/investments/<fund>/)")
    s = requests.Session()                                  # the site sets a country cookie on first contact
    s.headers.update(UA)
    html = s.get(m.group(1) + "/holdings/", timeout=TIMEOUT).text
    tk = re.search(r"<ve-fundticker[^>]*>\s*([A-Za-z0-9.]+)\s*</ve-fundticker>", html)
    blocks = re.findall(r'<ve-holdingsweightingschartblock[^>]*data-blockid="(\d+)"[^>]*data-pageid="(\d+)"', html)
    if not tk or not blocks:
        raise ValueError("No holdings weightings found on that VanEck page")
    ticker, cc_ = tk.group(1), m.group(2)
    docs = {}
    for b, pid in blocks:
        try:
            r = s.get(VANECK_BLOCKS.format(kind="HoldingsWeightingsChartBlock", b=b, p=pid, t=ticker, c=cc_), timeout=TIMEOUT)
            d = ((r.json() if r.status_code == 200 else None) or {}).get("data") or {}
        except ValueError:                                  # a block that answers with an error page
            continue
        if d.get("Holdings") and d.get("Title") and d.get("WeightingsType") not in docs:
            docs[d["WeightingsType"]] = d                    # first block of a type is the fund's (later ones are the index's)
    cd = docs.get("CountryOfRisk")
    if not cd or not cd.get("Holdings"):
        raise NoData("VanEck lists no country weights for that fund")
    kind = kind_hint or "Stocks"
    rows, unknown = [], []
    for h in cd["Holdings"]:
        code, w = _code(h.get("Label")), _num(h.get("Weight"))
        if code and w is not None:
            rows.append((kind, code, round(w, 4)))
        elif h.get("Label") and not re.search(r"other|cash", str(h["Label"]), re.I):
            unknown.append(str(h["Label"]))
    if not rows:
        raise NoData("VanEck lists no recognisable countries for that fund")
    tot: dict[str, float] = {}
    cc = docs.get("Currency")
    if cc and cc.get("Holdings"):                           # the published currency table
        for h in cc["Holdings"]:
            code, w = _CCY_NAMES.get(str(h.get("Label") or "").strip().lower()), _num(h.get("Weight"))
            if code and w is not None:
                tot[code] = tot.get(code, 0.0) + w
    else:                                                   # UCITS pages: sum the holdings list by trading currency
        hb = re.search(r'<ve-holdingsblock[^>]*data-blockid="(\d+)"[^>]*data-pageid="(\d+)"', html)
        if hb:
            try:
                ds = s.get(VANECK_DATASET.format(b=hb.group(1), p=hb.group(2), t=ticker), timeout=TIMEOUT).json()
                for h in ds.get("Holdings") or []:
                    c, w = str(h.get("CurrencyCode") or "").upper().strip(), _num(h.get("Weight"))
                    if len(c) == 3 and w is not None and str(h.get("AssetClass") or "").lower() != "cash":
                        tot[c] = tot.get(c, 0.0) + w
            except (ValueError, requests.RequestException) as e:
                log.info("VanEck holdings list unavailable for %s: %s", ticker, e)
    ccy = sorted(((c, round(w, 3)) for c, w in tot.items() if w >= 0.005), key=lambda r: -r[1]) or None
    note = f"{len(rows)} countries" + (f"; not recognised: {', '.join(unknown)}" if unknown else "")
    return rows, _vaneck_date(cd.get("AsOfDate")), note, ccy


def fetch_vanguard(url: str, kind_hint: str | None = None):
    html = _get(url).text
    i = html.find("Market allocation")
    if i < 0:
        raise ValueError("No 'Market allocation' table on that Vanguard page")
    block = html[i:i + 60000]
    end = block.find("</table>")
    block = block[: end if end > 0 else len(block)]
    mo = re.search(r"As at\s+(\d{1,2}\s+\w{3}\s+\d{4})", block)
    as_of = datetime.strptime(mo.group(1), "%d %b %Y").date() if mo else None
    kind = kind_hint or "Stocks"
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", block, re.S):
        th = re.search(r'<th[^>]*scope="row"[^>]*>(.*?)</th>', tr, re.S)
        nums = re.findall(r'class="numeric[^"]*"[^>]*>\s*(-?[\d.]+)\s*%', tr)
        code = _code(re.sub(r"<[^>]+>", "", th.group(1)).strip()) if th else None
        if code and nums:
            rows.append((kind, code, float(nums[0])))
    if not rows:
        raise ValueError("The market-allocation table was empty")
    return rows, as_of, "the countries Vanguard lists"


_W_HEADS = ("weight", "% of net", "% of fund", "% of nav", "% net", "percentage", "pct", "allocation", "% of")
_C_HEADS = ("country of risk", "country", "location", "domicile", "incorporation")


def _num(v) -> float | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).strip().replace("%", "").replace(" ", "")
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})*,\d+", t):          # 1.234,56
        t = t.replace(".", "").replace(",", ".")
    else:
        t = t.replace(",", "")
    try:
        return float(t)
    except ValueError:
        return None


def fetch_file(url: str, kind_hint: str | None = None):
    """Any holdings file a provider publishes (CSV or XLSX): finds the header row, the weight column and either a
    country column or an ISIN column (whose prefix is taken as the issuer's country), and totals by country."""
    raw = _get(url).content
    if raw[:2] == b"PK":
        sheets = pd.read_excel(io.BytesIO(raw), header=None, sheet_name=None)
        tables = list(sheets.values())
    else:
        text = raw.decode("utf-8-sig", errors="ignore")
        delim = max([",", ";", "\t", "|"], key=lambda d: text[:5000].count(d))
        rows = list(__import__("csv").reader(io.StringIO(text), delimiter=delim))
        width = max((len(r) for r in rows), default=0)
        tables = [pd.DataFrame([r + [None] * (width - len(r)) for r in rows])]
    for t in tables:
        for i in range(min(len(t), 60)):
            head = [str(x).strip().lower() if x is not None and not (isinstance(x, float) and pd.isna(x)) else "" for x in t.iloc[i]]
            wcol = next((j for j, h in enumerate(head) if any(k in h for k in _W_HEADS) and "market value" not in h), None)
            ccol = next((j for j, h in enumerate(head) if any(k in h for k in _C_HEADS)), None)
            icol = next((j for j, h in enumerate(head) if h in ("isin", "isin code")), None)
            if wcol is None or (ccol is None and icol is None):
                continue
            acol = next((j for j, h in enumerate(head) if h in ("asset class", "asset type", "security type", "type")), None)
            body = t.iloc[i + 1:]
            tot: dict = {}
            for _, r in body.iterrows():
                w = _num(r.iloc[wcol])
                if w is None:
                    continue
                code = _code(r.iloc[ccol]) if ccol is not None else isin_country(str(r.iloc[icol] or ""))
                if not code:
                    continue
                kind = kind_hint or "Stocks"
                if acol is not None:
                    a = str(r.iloc[acol]).lower()
                    if any(x in a for x in ("cash", "money market", "future", "forward", "swap", "currency")):
                        continue
                tot[(kind, code)] = tot.get((kind, code), 0.0) + w
            if not tot:
                continue
            s = sum(tot.values())
            scale = 100.0 if s <= 1.5 else 1.0                 # weights given as fractions
            rows_out = sorted(((k, c, round(w * scale, 3)) for (k, c), w in tot.items() if w * scale >= 0.005), key=lambda r: -r[2])
            return rows_out, None, f"{len(body):,} holdings lines from the file"
    raise ValueError("Couldn't find a weight column together with a country (or ISIN) column in that file")


def fetch_justetf(isin: str, kind_hint: str | None = None):
    try:
        html = _get(f"https://www.justetf.com/en/etf-profile.html?isin={isin}").text
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code in (404, 410):
            raise NoData("justETF doesn't know this ISIN") from e
        raise
    i = html.find("etf-holdings_countries_table")
    if i < 0:
        raise NoData("justETF has no country table for this fund")
    block = html[i:i + 12000]
    block = block[: block.find("</table>")]
    names = re.findall(r'tl_etf-holdings_countries_value_name">([^<]+)<', block)
    pcts = re.findall(r'tl_etf-holdings_countries_value_percentage">\s*([\d.]+)%', block)
    rows = [(kind_hint or "Stocks", c, float(p)) for n, p in zip(names, pcts) if (c := _code(n)) and c != "XX"]
    mo = re.search(r"As of\s+(\d{2}/\d{2}/\d{4})", html)
    as_of = datetime.strptime(mo.group(1), "%d/%m/%Y").date() if mo else None
    if not rows:
        raise NoData("justETF's country table had nothing usable")
    return rows, as_of, "top countries only"


# ── The job ─────────────────────────────────────────────────────────────────────

def _save_currencies(cur, sid: int, name: str, ccy: list, provider: str, as_of, force: bool) -> None:
    """Replace a fund's currency rows with the provider's — except ones typed in by hand. A hedged share class is
    100% its own currency (the hedge removes the exposure of the underlying holdings)."""
    cur.execute("SELECT MAX(Origin) FROM Fund_Currency_Exposure WHERE Securities_Id = %s", (sid,))
    if (cur.fetchone()[0] == "manual") and not force:
        return
    note = {"ishares": "iShares holdings, by market currency", "invesco": "Invesco holdings, by currency", "vaneck": "VanEck currency exposure"}.get(provider, "holdings, by currency")
    if re.search(r"hedged", name, re.I):
        cur.execute("SELECT c.Currencies_ShortName FROM Securities s JOIN Currencies c ON c.Currencies_Id = s.Currencies_Id WHERE s.Securities_Id = %s", (sid,))
        own = cur.fetchone()
        if own:
            ccy, note = [(own[0], 100.0)], f"hedged share class: 100% {own[0]}"
    cur.execute("DELETE FROM Fund_Currency_Exposure WHERE Securities_Id = %s", (sid,))
    for c, w in ccy:
        cur.execute("""INSERT INTO Fund_Currency_Exposure (Securities_Id, Currency, Weight_Pct, Source, As_Of, Origin)
                       VALUES (%s, %s, %s, %s, %s, %s)""", (sid, c, w, note[:100], as_of or date.today(), provider))


def _guess_kind(cur, sid: int, name: str) -> str:
    cur.execute("SELECT Asset_Bond_Pct, Category_Name FROM Fund_Composition WHERE Securities_Id = %s", (sid,))
    r = cur.fetchone()
    if r and (r[0] or 0) >= 0.7:
        return "Government Bonds" if _GOV_NAME.search(f"{r[1] or ''} {name}") else "Corporate Bonds"
    return "Stocks"


def download_country_exposure(target_sec_id=None, force: bool = False, only_held: bool = True) -> dict:
    """Refresh Fund_Country_Exposure. Returns {"funds": [{id, name, status, message}], "updated": n, "skipped": n, "errors": n}."""
    conn = get_connection()
    cur = conn.cursor()
    out = {"funds": [], "updated": 0, "skipped": 0, "errors": 0}
    try:
        if target_sec_id:
            cur.execute("SELECT Securities_Id, Securities_Name, ISIN, Ticker FROM Securities WHERE Securities_Id = %s", (int(target_sec_id),))
        else:
            cur.execute(f"""
                SELECT s.Securities_Id, s.Securities_Name, s.ISIN, s.Ticker FROM Securities s
                WHERE s.Securities_Type::text IN ('ETF', 'Mutual Fund') AND s.Is_Active
                  {"AND EXISTS (SELECT 1 FROM Holdings h WHERE h.Securities_Id = s.Securities_Id AND h.Quantity > 0)" if only_held else ""}
                ORDER BY s.Securities_Name
            """)
        funds = cur.fetchall()
        for sid, name, isin, ticker in funds:
            def done(status, msg):
                out["funds"].append({"id": sid, "name": name, "status": status, "message": msg})
                out["updated" if status == "updated" else ("errors" if status == "error" else "skipped")] += 1

            cur.execute("SELECT Provider, Url, Kind FROM Fund_Country_Sources WHERE Securities_Id = %s", (sid,))
            src = cur.fetchone()
            cur.execute("SELECT MAX(Origin), COUNT(*) FROM Fund_Country_Exposure WHERE Securities_Id = %s", (sid,))
            origin, n_rows = cur.fetchone()
            provider = src[0] if src else ("justetf" if isin else None)
            if provider is None:
                done("skipped", "no source set and no ISIN")
                continue
            if n_rows and not force and PROVIDER_RANK.get(origin or "manual", 3) > PROVIDER_RANK[provider]:
                done("skipped", "entered by hand — kept" if origin == "manual" else ("set from a single-country index — kept" if origin == "index" else f"existing {origin} data is more detailed — kept"))
                continue
            try:
                kind_hint = (src[2] if src and src[2] else None) or _guess_kind(cur, sid, name)
                ccy = None
                if provider == "ishares":
                    rows, as_of, note, ccy = fetch_ishares(src[1], kind_hint)
                elif provider == "vaneck":
                    rows, as_of, note, ccy = fetch_vaneck(src[1], kind_hint)
                elif provider == "invesco":
                    rows, as_of, note, ccy = fetch_invesco(src[1], kind_hint, ticker)
                elif provider == "vanguard":
                    rows, as_of, note = fetch_vanguard(src[1], kind_hint)
                elif provider == "file":
                    rows, as_of, note = fetch_file(src[1], kind_hint)
                else:
                    rows, as_of, note = fetch_justetf(isin, kind_hint)
                # Duplicate (kind, country) rows can't exist, and nothing may exceed 100% in total.
                merged: dict = {}
                for k, c, w in rows:
                    merged[(k, c)] = merged.get((k, c), 0.0) + w
                total = sum(merged.values())
                if total > 100.5:
                    merged = {kc: w * 100.0 / total for kc, w in merged.items()}
                label = {"ishares": "iShares daily holdings", "invesco": "Invesco fund data", "vaneck": "VanEck fund data", "vanguard": "Vanguard market allocation", "justetf": "justETF country table", "file": "holdings file"}[provider]
                cur.execute("DELETE FROM Fund_Country_Exposure WHERE Securities_Id = %s", (sid,))
                for (k, c), w in merged.items():
                    cur.execute("""INSERT INTO Fund_Country_Exposure (Securities_Id, Kind, Country, Weight_Pct, Source, As_Of, Origin)
                                   VALUES (%s, %s, %s, %s, %s, %s, %s)""", (sid, k, c, round(w, 4), f"{label} ({note})"[:100], as_of or date.today(), provider))
                if ccy:
                    _save_currencies(cur, sid, name, ccy, provider, as_of, force)
                conn.commit()
                done("updated", f"{len(merged)} countries from {label}{f', as of {as_of}' if as_of else ''}; {note}")
            except NoData as e:
                conn.rollback()
                done("skipped", str(e))
            except Exception as e:                      # one fund must not stop the rest
                conn.rollback()
                log.warning("Country exposure for %s failed: %s", name, e)
                done("error", str(e)[:200])
        return out
    finally:
        cur.close()
        conn.close()
