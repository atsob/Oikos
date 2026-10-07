"""Market Data API endpoints: currencies, FX rates, securities, price history."""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional, List
from pydantic import BaseModel
import math
from datetime import date, timedelta
import pandas as pd
from database.connection import get_db

router = APIRouter()


def _df(df: pd.DataFrame) -> list:
    df = df.copy()
    for col in df.select_dtypes(include=["datetime", "datetimetz"]).columns:
        df[col] = df[col].astype(str)
    records = df.where(pd.notnull(df), other=None).to_dict(orient="records")
    return [{k: None if isinstance(v, float) and math.isnan(v) else v for k, v in r.items()} for r in records]


# ── Currencies ────────────────────────────────────────────────────────────────

@router.get("/currencies")
def get_currencies():
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT c.Currencies_Id AS id,
                   c.Currencies_ShortName AS code,
                   c.Currencies_Name AS name,
                   (SELECT FX_Rate FROM Historical_FX
                    WHERE Currencies_Id_1 = c.Currencies_Id
                    ORDER BY Date DESC LIMIT 1) AS latest_rate,
                   (SELECT Date FROM Historical_FX
                    WHERE Currencies_Id_1 = c.Currencies_Id
                    ORDER BY Date DESC LIMIT 1) AS rate_date,
                   (SELECT COUNT(*) FROM Historical_FX WHERE Currencies_Id_1 = c.Currencies_Id) AS price_records
            FROM Currencies c
            ORDER BY c.Currencies_ShortName
        """, conn)
    return _df(df)


@router.get("/fx-rates")
def get_fx_rates(
    currency_id: Optional[int] = Query(None),
    from_date: str = Query("2020-01-01"),
):
    """Historical FX rates vs EUR, optionally filtered to one base currency."""
    clause = "AND hfx.Currencies_Id_1 = %(cid)s" if currency_id else ""
    params: dict = {"from_date": from_date}
    if currency_id:
        params["cid"] = currency_id
    with get_db() as conn:
        df = pd.read_sql(f"""
            SELECT hfx.Date::text AS date,
                   c.Currencies_ShortName AS currency,
                   hfx.FX_Rate AS rate
            FROM Historical_FX hfx
            JOIN Currencies c ON c.Currencies_Id = hfx.Currencies_Id_1
            WHERE hfx.Date >= %(from_date)s {clause}
            ORDER BY hfx.Date ASC, c.Currencies_ShortName ASC
        """, conn, params=params)
    return _df(df)


def _storage_base_id(conn) -> Optional[int]:
    """The currency every stored FX rate is quoted against (Historical_FX.Currencies_Id_2,
    EUR in practice). Read from the data rather than assumed, falling back to the EUR row."""
    df = pd.read_sql("""
        SELECT COALESCE(
            (SELECT Currencies_Id_2 FROM Historical_FX GROUP BY Currencies_Id_2 ORDER BY COUNT(*) DESC LIMIT 1),
            (SELECT Currencies_Id FROM Currencies WHERE Currencies_ShortName = 'EUR')) AS id
    """, conn)
    return None if df.empty or pd.isna(df["id"].iloc[0]) else int(df["id"].iloc[0])


def _stored_rates(conn, cid: int) -> pd.Series:
    """Stored rate history of one currency (storage-base units per 1 unit), date-indexed."""
    df = pd.read_sql("""
        SELECT Date AS date, FX_Rate::float AS rate FROM Historical_FX
        WHERE Currencies_Id_1 = %(cid)s ORDER BY Date
    """, conn, params={"cid": cid})
    return pd.Series(df["rate"].values, index=pd.to_datetime(df["date"]).dt.date, dtype=float)


def _cross_rates(conn, cid: int, quote_id: int, base_id: Optional[int]) -> pd.Series:
    """Units of the quote currency per 1 unit of `cid`, from the two stored histories
    (the storage base itself counts as a constant 1). Each side is carried forward to
    the other's dates, so a day where only one of them has a rate still gets a value."""
    if cid == quote_id:
        return pd.Series(dtype=float)
    x = None if cid == base_id else _stored_rates(conn, cid)
    q = None if quote_id == base_id else _stored_rates(conn, quote_id)
    if x is None and q is None:
        return pd.Series(dtype=float)
    idx = sorted(set(x.index if x is not None else []) | set(q.index if q is not None else []))
    xs = x.reindex(idx).ffill() if x is not None else pd.Series(1.0, index=idx)
    qs = q.reindex(idx).ffill() if q is not None else pd.Series(1.0, index=idx)
    return (xs / qs).replace([float("inf")], float("nan")).dropna()


def _resolve_quote(conn, quote: Optional[str], base_id: Optional[int]) -> tuple:
    """(id, code) of the currency rates are shown in: the reporting currency the frontend
    passes, or the storage base when it's missing or unknown."""
    df = pd.read_sql("""
        SELECT Currencies_Id AS id, TRIM(Currencies_ShortName) AS code FROM Currencies
        WHERE TRIM(Currencies_ShortName) = %(q)s OR Currencies_Id = %(b)s
        ORDER BY (TRIM(Currencies_ShortName) = %(q)s) DESC LIMIT 1
    """, conn, params={"q": (quote or "").strip().upper(), "b": base_id or -1})
    if df.empty:
        return None, (quote or "EUR").strip().upper()
    return int(df["id"].iloc[0]), str(df["code"].iloc[0])


@router.get("/currencies/{cid}/history")
def get_currency_history(cid: int, quote: Optional[str] = Query(None), from_date: str = Query("1900-01-01")):
    """Rate history of one currency expressed in the quote (reporting) currency — what
    Currency Detail's Overview chart plots. Market Data's stored rates are always against
    the storage base; this converts them through it."""
    with get_db() as conn:
        base_id = _storage_base_id(conn)
        quote_id, _ = _resolve_quote(conn, quote, base_id)
        series = _cross_rates(conn, cid, quote_id, base_id) if quote_id is not None else pd.Series(dtype=float)
    cutoff = pd.Timestamp(from_date).date()
    series = series[[d >= cutoff for d in series.index]] if not series.empty else series
    return [{"date": str(d), "rate": round(float(v), 10)} for d, v in series.items()]


@router.get("/currencies/{cid}/fx-effect")
def get_currency_fx_effect(cid: int, period: str = Query("YTD")):
    """Currency effect on P&L over a period (DTD, WTD, MTD, QTD, YTD, 1Y, 3Y, 5Y, All).

    Securities quoted in the currency: per account/security, realized and unrealized
    P&L each split into market and FX parts, plus income — see database/fx_effect.py
    for the method. Cash: today's active cash-side balances in the currency revalued
    at the rate change over the period (an estimate assuming the balance didn't
    change; none for "All", which has no starting rate). All amounts in EUR, the
    storage base the split is measured against."""
    from database import fx_effect
    if period not in fx_effect.PERIODS:
        raise HTTPException(400, f"period must be one of {', '.join(fx_effect.PERIODS)}")
    today = date.today()
    with get_db() as conn:
        result = fx_effect.compute(conn, cid, period, today)
        base_id = _storage_base_id(conn)
        stored = _stored_rates(conn, cid) if cid != base_id else pd.Series(dtype=float)
        cash = pd.read_sql("""
            SELECT COALESCE(SUM(Accounts_Balance), 0)::float AS bal FROM Accounts
            WHERE Is_Active = TRUE AND Accounts_Type NOT IN ('Brokerage','Margin') AND Currencies_Id = %(cid)s
        """, conn, params={"cid": cid})
    balance = float(cash["bal"].iloc[0])
    start = fx_effect.period_start(period, today)
    cash_fx = 0.0 if cid == base_id else None
    if cid != base_id and not stored.empty and start is not None:
        before = stored[[d <= start for d in stored.index]]
        if not before.empty:
            cash_fx = round(balance * (float(stored.iloc[-1]) - float(before.iloc[-1])), 2)
    return {**result, "cash_balance": round(balance, 2), "cash_fx": cash_fx}


@router.get("/currencies/{cid}/detail")
def get_currency_detail(cid: int, quote: Optional[str] = Query(None)):
    """Everything Currency Detail's Overview/Exposure/FX Effect tabs show for one currency.

    Rates are shown in `quote` — the reporting currency (Tools → App Settings), defaulting
    to the storage base: the latest rate and its 1D/1M/YTD/1Y change. Oikos stores every
    FX rate against one storage base (EUR), so only currencies other than that base have
    stored rates to maintain (`is_storage_base`), whatever the reporting currency is.

    Exposure (cash-side accounts + held securities quoted in the currency) uses the same
    definition as Reports → Inv. Portfolio → FX Exposure (active non-Brokerage/Margin
    account balances + holdings at the latest close), so totals and the % share agree with
    that report. Amounts are in the storage base (EUR); the frontend converts them to the
    reporting currency for display, as everywhere else.

    `cash_fx_effect` estimates the currency effect on those cash balances over 1D and YTD:
    today's balance × the change in its rate against the storage base — the same
    measurement the P&L report's Market / FX split uses for securities."""
    with get_db() as conn:
        cur = pd.read_sql("""
            SELECT Currencies_Id AS id, TRIM(Currencies_ShortName) AS code, Currencies_Name AS name
            FROM Currencies WHERE Currencies_Id = %(cid)s
        """, conn, params={"cid": cid})
        if cur.empty:
            raise HTTPException(404, "Currency not found")
        base_id = _storage_base_id(conn)
        base_code = pd.read_sql("SELECT TRIM(Currencies_ShortName) AS code FROM Currencies WHERE Currencies_Id = %(b)s",
                                conn, params={"b": base_id or -1})
        quote_id, quote_code = _resolve_quote(conn, quote, base_id)
        stored = _stored_rates(conn, cid)
        cross = _cross_rates(conn, cid, quote_id, base_id) if quote_id is not None else pd.Series(dtype=float)
        rates = pd.read_sql("""
            SELECT r.Rate_Series_Id AS id, r.Code AS code, r.Name AS name, r.Rate_Type AS rate_type,
                   h.Date::text AS date, h.Rate_Pct::float AS rate_pct
            FROM Rate_Series r
            LEFT JOIN LATERAL (
                SELECT Date, Rate_Pct FROM Historical_Rates
                WHERE Rate_Series_Id = r.Rate_Series_Id ORDER BY Date DESC LIMIT 1
            ) h ON TRUE
            WHERE r.Currencies_Id = %(cid)s AND r.Is_Active
            ORDER BY r.Sort_Order, r.Code
        """, conn, params={"cid": cid})
        accounts = pd.read_sql("""
            WITH fx AS (SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
                        FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC)
            SELECT a.Accounts_Id AS id, a.Accounts_Name AS name, a.Accounts_Type::text AS type,
                   a.Accounts_Balance::float AS balance,
                   (a.Accounts_Balance * COALESCE(fx.FX_Rate, 1))::float AS balance_eur
            FROM Accounts a LEFT JOIN fx ON fx.Currencies_Id_1 = a.Currencies_Id
            WHERE a.Is_Active = TRUE AND a.Accounts_Type NOT IN ('Brokerage','Margin')
              AND a.Currencies_Id = %(cid)s AND a.Accounts_Balance <> 0
            ORDER BY ABS(a.Accounts_Balance) DESC
        """, conn, params={"cid": cid})
        holdings = pd.read_sql("""
            WITH fx AS (SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
                        FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC),
            prices AS (SELECT DISTINCT ON (Securities_Id) Securities_Id, Close
                       FROM Historical_Prices ORDER BY Securities_Id, Date DESC)
            SELECT s.Securities_Id AS securities_id, s.Securities_Name AS name, s.Ticker AS ticker,
                   s.Securities_Type::text AS type, a.Accounts_Id AS accounts_id, a.Accounts_Name AS account,
                   a.Accounts_Type::text AS account_type,
                   h.Quantity::float AS quantity, p.Close::float AS price,
                   (h.Quantity * COALESCE(p.Close, 0))::float AS value,
                   (h.Quantity * COALESCE(p.Close, 0) * COALESCE(fx.FX_Rate, 1))::float AS value_eur
            FROM Holdings h
            JOIN Securities s ON s.Securities_Id = h.Securities_Id
            JOIN Accounts a ON a.Accounts_Id = h.Accounts_Id
            LEFT JOIN prices p ON p.Securities_Id = h.Securities_Id
            LEFT JOIN fx ON fx.Currencies_Id_1 = s.Currencies_Id
            WHERE h.Quantity > 0 AND s.Currencies_Id = %(cid)s
            ORDER BY value_eur DESC NULLS LAST
        """, conn, params={"cid": cid})
        # Denominator for "% of total": the same exposure summed over every currency.
        total = pd.read_sql("""
            WITH fx AS (SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
                        FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC),
            prices AS (SELECT DISTINCT ON (Securities_Id) Securities_Id, Close
                       FROM Historical_Prices ORDER BY Securities_Id, Date DESC)
            SELECT COALESCE((SELECT SUM(a.Accounts_Balance * COALESCE(fx.FX_Rate, 1))
                             FROM Accounts a LEFT JOIN fx ON fx.Currencies_Id_1 = a.Currencies_Id
                             WHERE a.Is_Active = TRUE AND a.Accounts_Type NOT IN ('Brokerage','Margin')), 0)
                 + COALESCE((SELECT SUM(h.Quantity * COALESCE(p.Close, 0) * COALESCE(fx.FX_Rate, 1))
                             FROM Holdings h JOIN Securities s ON s.Securities_Id = h.Securities_Id
                             LEFT JOIN prices p ON p.Securities_Id = h.Securities_Id
                             LEFT JOIN fx ON fx.Currencies_Id_1 = s.Currencies_Id
                             WHERE h.Quantity > 0), 0) AS total_eur
        """, conn)

    def on_or_before(series: pd.Series, d) -> Optional[float]:
        before = series[[x <= d for x in series.index]]
        return float(before.iloc[-1]) if not before.empty else None

    def window_refs(series: pd.Series) -> dict:
        """Reference rates for each window; YTD measures from the previous year's last rate."""
        last = series.index[-1]
        return {
            "1D": float(series.iloc[-2]) if len(series) > 1 else None,
            "1M": on_or_before(series, last - timedelta(days=30)),
            "YTD": on_or_before(series, date(last.year, 1, 1) - timedelta(days=1)),
            "1Y": on_or_before(series, last - timedelta(days=365)),
        }

    latest_rate = latest_date = None
    changes: dict = {}
    if not cross.empty:
        latest_rate = float(cross.iloc[-1])
        latest_date = str(cross.index[-1])
        changes = {k: (round((latest_rate / v - 1) * 100, 4) if v else None) for k, v in window_refs(cross).items()}

    cash_eur = float(accounts["balance_eur"].sum()) if not accounts.empty else 0.0
    cash_native = float(accounts["balance"].sum()) if not accounts.empty else 0.0
    sec_eur = float(holdings["value_eur"].sum()) if not holdings.empty else 0.0
    sec_native = float(holdings["value"].sum()) if not holdings.empty else 0.0
    total_eur = float(total["total_eur"].iloc[0] or 0)
    exposure_eur = cash_eur + sec_eur
    is_storage_base = cid == base_id
    is_quote = cid == quote_id

    # Cash revaluation against the storage base, as the P&L report's FX split measures it.
    cash_fx_effect: dict = {"1D": 0.0, "YTD": 0.0} if is_storage_base else {"1D": None, "YTD": None}
    if not is_storage_base and not stored.empty:
        refs = window_refs(stored)
        now = float(stored.iloc[-1])
        cash_fx_effect = {k: (round(cash_native * (now - refs[k]), 2) if refs[k] is not None else None) for k in ("1D", "YTD")}

    return {
        **_df(cur)[0],
        "storage_base": str(base_code["code"].iloc[0]) if not base_code.empty else "EUR",
        "is_storage_base": is_storage_base,
        "quote": quote_code,
        "is_quote": is_quote,
        "latest_rate": latest_rate,
        "rate_date": latest_date,
        "stored_rate": float(stored.iloc[-1]) if not stored.empty else None,
        "price_records": len(stored),
        "changes": changes,
        "interest_rates": _df(rates),
        "exposure": {
            "cash_native": round(cash_native, 2), "cash_eur": round(cash_eur, 2),
            "securities_native": round(sec_native, 2), "securities_eur": round(sec_eur, 2),
            "total_native": round(cash_native + sec_native, 2), "total_eur": round(exposure_eur, 2),
            "share_pct": round(exposure_eur / total_eur * 100, 2) if total_eur else None,
            # Holding your own reporting currency carries no currency risk in that currency.
            "sensitivity_5pct_eur": 0.0 if is_quote else round(exposure_eur * 0.05, 2),
        },
        "cash_fx_effect": cash_fx_effect,
        "accounts": _df(accounts),
        "holdings": _df(holdings),
    }


# ── Securities ────────────────────────────────────────────────────────────────

@router.get("/securities")
def get_securities(search: Optional[str] = Query(None)):
    # Matches against every text-like column shown/editable on the Securities tab
    # (CONCAT_WS skips NULLs), so a term like "Consumer Defensive" or "ATHEX" finds
    # a security by sector/industry/exchange/etc., not just name or ticker.
    clause = (
        "AND LOWER(CONCAT_WS(' ', s.Securities_Name, s.Ticker, s.ISIN, s.Yahoo_Ticker, s.TV_Symbol, "
        "s.TV_Exchange, s.Securities_Type, s.Sector, s.Industry, s.Tax_Category, c.Currencies_ShortName, "
        "s.Analyst_Rating, s.Dividend_Frequency, s.Coupon_Frequency)) LIKE %(s)s"
    ) if search else ""
    params: dict = {}
    if search:
        params["s"] = f"%{search.lower()}%"
    with get_db() as conn:
        df = pd.read_sql(f"""
            SELECT s.Securities_Id AS id,
                   s.Ticker AS ticker,
                   s.Securities_Name AS name,
                   s.Securities_Type AS type,
                   s.Currencies_Id AS currencies_id,
                   c.Currencies_ShortName AS currency,
                   s.Is_Active AS is_active,
                   s.Is_Tax_Exempt AS is_tax_exempt,
                   s.Tax_Category AS tax_category,
                   s.ISIN AS isin,
                   s.Sector AS sector,
                   s.Industry AS industry,
                   s.Yahoo_Ticker AS yahoo_ticker,
                   s.TV_Symbol AS tv_symbol,
                   s.TV_Exchange AS tv_exchange,
                   s.Maturity_Date AS maturity_date,
                   s.Coupon_Rate AS coupon_rate,
                   s.Coupon_Frequency AS coupon_frequency,
                   s.Face_Value AS face_value,
                   s.Dividend_Yield AS dividend_yield,
                   s.Dividend_Rate AS dividend_rate,
                   s.Dividend_Frequency AS dividend_frequency,
                   s.Ex_Dividend_Date AS ex_dividend_date,
                   s.Dividend_Pay_Date AS dividend_pay_date,
                   s.Payout_Ratio AS payout_ratio,
                   s.Five_Year_Avg_Yield AS five_year_avg_yield,
                   s.Analyst_Rating AS analyst_rating,
                   s.Analyst_Target_Price AS analyst_target_price,
                   s.Issuer_Id AS issuer_id,
                   COALESCE(s.Price_Scale, 1) AS price_scale,
                   COALESCE((SELECT COUNT(*) FROM Historical_Prices WHERE Securities_Id = s.Securities_Id), 0) AS price_records,
                   (SELECT Close FROM Historical_Prices WHERE Securities_Id = s.Securities_Id ORDER BY Date DESC LIMIT 1) AS latest_price,
                   (SELECT Date FROM Historical_Prices WHERE Securities_Id = s.Securities_Id ORDER BY Date DESC LIMIT 1) AS price_date,
                   COALESCE((SELECT COUNT(*) FROM Investments WHERE Securities_Id = s.Securities_Id), 0) AS investment_count,
                   COALESCE((SELECT SUM(Quantity) FROM Holdings WHERE Securities_Id = s.Securities_Id), 0) AS held_quantity,
                   -- Quote fields: prefer Historical_Prices (already downloaded daily for
                   -- every security's price chart, so almost always fresher/more complete)
                   -- and fall back to the Securities_Quote cache from the Yahoo quote
                   -- download only when price history can't supply it. Open, P/E, and
                   -- Market Cap have no Historical_Prices equivalent (no Open column, and
                   -- P/E/Market Cap aren't derivable from price alone), so those three
                   -- always come from Securities_Quote.
                   COALESCE(
                       (SELECT Close FROM Historical_Prices WHERE Securities_Id = s.Securities_Id ORDER BY Date DESC OFFSET 1 LIMIT 1),
                       sq.Prev_Close
                   ) AS prev_close,
                   sq.Day_Open AS day_open,
                   COALESCE(
                       (SELECT High FROM Historical_Prices WHERE Securities_Id = s.Securities_Id ORDER BY Date DESC LIMIT 1),
                       sq.Day_High
                   ) AS day_high,
                   COALESCE(
                       (SELECT Low FROM Historical_Prices WHERE Securities_Id = s.Securities_Id ORDER BY Date DESC LIMIT 1),
                       sq.Day_Low
                   ) AS day_low,
                   COALESCE(
                       (SELECT MAX(High) FROM Historical_Prices WHERE Securities_Id = s.Securities_Id AND Date >= CURRENT_DATE - INTERVAL '365 days'),
                       sq.Week52_High
                   ) AS week52_high,
                   COALESCE(
                       (SELECT MIN(Low) FROM Historical_Prices WHERE Securities_Id = s.Securities_Id AND Date >= CURRENT_DATE - INTERVAL '365 days'),
                       sq.Week52_Low
                   ) AS week52_low,
                   COALESCE(
                       (SELECT Volume FROM Historical_Prices WHERE Securities_Id = s.Securities_Id ORDER BY Date DESC LIMIT 1),
                       sq.Volume
                   ) AS volume,
                   COALESCE(
                       (SELECT ROUND(AVG(Volume)) FROM Historical_Prices WHERE Securities_Id = s.Securities_Id AND Date >= CURRENT_DATE - INTERVAL '30 days'),
                       sq.Avg_Volume
                   ) AS avg_volume,
                   sq.Trailing_PE AS trailing_pe, sq.Market_Cap AS market_cap,
                   sq.Quote_Updated_At AS quote_updated_at
            FROM Securities s
            LEFT JOIN Currencies c ON s.Currencies_Id = c.Currencies_Id
            LEFT JOIN Securities_Quote sq ON sq.Securities_Id = s.Securities_Id
            WHERE 1=1 {clause}
            ORDER BY s.Securities_Name ASC
        """, conn, params=params if params else None)
    return _df(df)


@router.get("/search-ticker")
def search_ticker(q: str = Query(..., min_length=1)):
    """Search Yahoo Finance by ticker symbol or company name; returns up to 10 matches."""
    import requests as _req

    query = q.strip()
    try:
        resp = _req.get(
            "https://query2.finance.yahoo.com/v1/finance/search",
            params={"q": query, "quotesCount": 10, "newsCount": 0, "enableFuzzyQuery": True},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        resp.raise_for_status()
        quotes = resp.json().get("quotes", [])
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Search failed: {exc}")

    QUOTE_TYPE_MAP = {
        "EQUITY": "Stock", "ETF": "ETF", "MUTUALFUND": "Mutual Fund",
        "INDEX": "Market Index", "CRYPTOCURRENCY": "Crypto", "BOND": "Bond",
        "CURRENCY": "FX Spot", "FUTURE": "Commodity", "OPTION": "Option",
    }
    results = []
    for item in quotes:
        sym = item.get("symbol", "").strip()
        if not sym:
            continue
        qt = (item.get("quoteType") or "").upper()
        results.append({
            "symbol": sym,
            "name": item.get("longname") or item.get("shortname") or sym,
            "type": QUOTE_TYPE_MAP.get(qt, qt or "Other"),
            "exchange": item.get("exchDisp") or item.get("exchange") or "",
        })
    return results


# Display names for common ISO 4217 codes beyond seed_data.py's curated currency list —
# used only as a fallback label when auto-creating a Currencies row lookup_ticker below
# needs but doesn't have yet; an unrecognized code just falls back to itself as the name
# (still a valid, if unlabeled, row — correctable afterward in Market Data -> Currencies).
_ISO_CURRENCY_NAMES = {
    "AUD": "Australian Dollar", "NZD": "New Zealand Dollar", "CNY": "Chinese Yuan",
    "INR": "Indian Rupee", "MXN": "Mexican Peso", "ZAR": "South African Rand",
    "THB": "Thai Baht", "IDR": "Indonesian Rupiah", "MYR": "Malaysian Ringgit",
    "PHP": "Philippine Peso", "VND": "Vietnamese Dong", "DKK": "Danish Krone",
    "ISK": "Icelandic Krona", "CLP": "Chilean Peso", "COP": "Colombian Peso",
    "PEN": "Peruvian Sol", "ARS": "Argentine Peso", "SAR": "Saudi Riyal",
    "QAR": "Qatari Riyal", "KWD": "Kuwaiti Dinar", "PKR": "Pakistani Rupee",
    "BDT": "Bangladeshi Taka", "NGN": "Nigerian Naira", "KES": "Kenyan Shilling",
    "MAD": "Moroccan Dirham", "CZK": "Czech Koruna", "HRK": "Croatian Kuna",
    "UAH": "Ukrainian Hryvnia", "VEF": "Venezuelan Bolivar", "XOF": "West African CFA Franc",
}


@router.get("/lookup-ticker")
def lookup_ticker(symbol: str = Query(..., min_length=1)):
    """Fetch metadata for a ticker from Yahoo Finance to pre-fill the security form."""
    import yfinance as yf
    from datetime import datetime as _dt

    sym = symbol.strip().upper()
    try:
        ticker = yf.Ticker(sym)
        info = ticker.info
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Yahoo Finance error: {exc}")

    if not info or not info.get("quoteType"):
        raise HTTPException(status_code=404, detail=f"Ticker '{sym}' not found on Yahoo Finance.")

    QUOTE_TYPE_MAP = {
        "EQUITY": "Stock", "ETF": "ETF", "MUTUALFUND": "Mutual Fund",
        "INDEX": "Market Index", "CRYPTOCURRENCY": "Crypto", "BOND": "Bond",
        "CURRENCY": "FX Spot", "FUTURE": "Commodity", "OPTION": "Option",
    }
    sec_type = QUOTE_TYPE_MAP.get((info.get("quoteType") or "").upper(), "Other")
    name = info.get("longName") or info.get("shortName") or sym

    ccy_code = (info.get("currency") or "").upper()
    currencies_id = None
    if ccy_code:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT Currencies_Id FROM Currencies WHERE UPPER(Currencies_ShortName) = %s LIMIT 1",
                (ccy_code,),
            )
            row = cur.fetchone()
            if row:
                currencies_id = row[0]
            elif len(ccy_code) == 3:
                # seed_data.py ships a curated subset of currencies, not the full ISO
                # 4217 list — Yahoo can report one this install genuinely doesn't have
                # yet (e.g. AUD, for an ASX-listed stock, if nothing already held is
                # AUD-denominated). Failing the whole import over a missing reference
                # row — as a raw "violates not-null constraint" DB error, before this
                # fix — is worse than creating it on the fly; Market Data -> Currencies
                # lets the name be corrected afterward if the fallback below is wrong.
                cur.execute(
                    "INSERT INTO Currencies (Currencies_ShortName, Currencies_Name) VALUES (%s, %s) RETURNING Currencies_Id",
                    (ccy_code, _ISO_CURRENCY_NAMES.get(ccy_code, ccy_code)),
                )
                currencies_id = cur.fetchone()[0]

    isin = None
    try:
        _isin_raw = ticker.isin
        if (
            isinstance(_isin_raw, str)
            and len(_isin_raw.strip()) == 12
            and _isin_raw.strip().upper() not in ("-", "N/A", "NONE")
        ):
            isin = _isin_raw.strip().upper()
    except Exception:
        pass

    def _ts(ts):
        if not ts:
            return None
        try:
            return _dt.fromtimestamp(int(ts)).date().isoformat()
        except Exception:
            return None

    def _flt(v, factor=1.0):
        try:
            import math
            f = float(v) * factor
            return None if math.isnan(f) else round(f, 4)
        except Exception:
            return None

    _raw_rating = info.get("recommendationKey")
    analyst_rating = (
        None
        if (not _raw_rating or str(_raw_rating).strip().lower() in ("none", "n/a", ""))
        else str(_raw_rating).strip().lower()
    )

    return {
        "ticker": sym,
        "name": name,
        "type": sec_type,
        "currencies_id": currencies_id,
        "currency_code": ccy_code,
        "isin": isin,
        "sector": info.get("sector") or None,
        "industry": info.get("industry") or None,
        "yahoo_ticker": sym,
        "dividend_yield": _flt(info.get("dividendYield")),
        "dividend_rate": _flt(info.get("dividendRate")),
        "ex_dividend_date": _ts(info.get("exDividendDate")),
        "dividend_pay_date": _ts(info.get("dividendDate") or info.get("lastDividendDate")),
        "payout_ratio": _flt(info.get("payoutRatio"), 100.0),
        "five_year_avg_yield": _flt(info.get("fiveYearAvgDividendYield")),
        "analyst_rating": analyst_rating,
        "analyst_target_price": _flt(info.get("targetMeanPrice")),
    }


@router.get("/price-history")
def get_price_history(
    security_id: int = Query(...),
    from_date: str = Query("2020-01-01"),
):
    """Daily close price history for one security."""
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT Date::text AS date, Close AS close,
                   High AS high, Low AS low, Volume AS volume,
                   Source AS source, Downloaded_At::text AS downloaded_at
            FROM Historical_Prices
            WHERE Securities_Id = %(sid)s AND Date >= %(from_date)s
            ORDER BY Date ASC
        """, conn, params={"sid": security_id, "from_date": from_date})
    return _df(df)


@router.get("/price-anomalies")
def get_price_anomalies(threshold_pct: float = Query(100.0)):
    """Prices that deviate more than threshold_pct% from their neighbours."""
    ratio = 1.0 + threshold_pct / 100.0
    with get_db() as conn:
        df = pd.read_sql("""
            WITH price_neighbors AS (
                SELECT hp.Securities_Id, s.Securities_Name AS security_name,
                       hp.Date::text AS date, hp.Close,
                       LAG(hp.Close)  OVER (PARTITION BY hp.Securities_Id ORDER BY hp.Date) AS prev_close,
                       LEAD(hp.Close) OVER (PARTITION BY hp.Securities_Id ORDER BY hp.Date) AS next_close
                FROM Historical_Prices hp
                JOIN Securities s ON s.Securities_Id = hp.Securities_Id
                WHERE hp.Close > 0
            )
            SELECT Securities_Id AS security_id, security_name, date,
                   Close AS close, prev_close, next_close,
                   ROUND((Close / NULLIF(prev_close, 0))::numeric, 3) AS ratio_prev,
                   ROUND((Close / NULLIF(next_close, 0))::numeric, 3) AS ratio_next
            FROM price_neighbors
            WHERE (Close / NULLIF(prev_close, 0) >= %(ratio)s OR prev_close / NULLIF(Close, 0) >= %(ratio)s
                OR Close / NULLIF(next_close, 0) >= %(ratio)s OR next_close / NULLIF(Close, 0) >= %(ratio)s)
            ORDER BY security_name, date ASC
        """, conn, params={"ratio": ratio})
    return _df(df)


@router.post("/refresh-prices")
def refresh_prices():
    try:
        from data.downloaders import download_historical_prices_from_yahoo
        download_historical_prices_from_yahoo()
        return {"ok": True, "message": "Yahoo prices refreshed (1m)"}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/refresh-fx")
def refresh_fx(data: dict = {}):
    try:
        from data.downloaders import download_historical_fx
        download_historical_fx(
            tsperiod=data.get("period") or None,
            currencies_id=data.get("currency_id") or None,
        )
        return {"ok": True, "message": "FX rates refreshed"}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/download/yahoo-info")
def download_yahoo_info(data: dict = {}):
    """Update Securities metadata (sector, industry, dividends, ISIN) from Yahoo Finance."""
    try:
        from data.downloaders import download_securities_info_from_yahoo
        download_securities_info_from_yahoo(target_sec_id=data.get("security_id"))
        return {"ok": True, "message": "Yahoo info updated"}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/download/yahoo-dividends")
def download_yahoo_dividends(data: dict = {}):
    """Download full dividend history from Yahoo Finance."""
    try:
        from data.downloaders import download_dividend_history
        download_dividend_history(target_sec_id=data.get("security_id"))
        return {"ok": True, "message": "Dividend history downloaded"}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/download/stock-splits")
def download_stock_splits(data: dict = {}):
    """Download stock split history from Yahoo Finance into Corporate Actions
    (reference records only — doesn't touch holdings; use the Corporate Actions
    tab's Split entry, Preview -> Execute, to apply one to shares actually held)."""
    try:
        from data.downloaders import download_stock_splits as _download_stock_splits
        _download_stock_splits(target_sec_id=data.get("security_id"))
        return {"ok": True, "message": "Stock split history downloaded"}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/download/fund-composition")
def download_fund_composition_endpoint(data: dict = {}):
    """Download ETF/Mutual Fund look-through composition from Yahoo Finance (Portfolio X-Ray)."""
    try:
        from data.downloaders import download_fund_composition
        download_fund_composition(target_sec_id=data.get("security_id"))
        return {"ok": True, "message": "Fund composition downloaded"}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/download/fundamentals")
def download_fundamentals_endpoint(data: dict = {}):
    """Download stock financial statements from Yahoo Finance (Piotroski F-Score /
    Altman Z-Score in Securities Analysis). Stocks only — a non-stock security_id
    is simply excluded by download_securities_fundamentals's own query, not an error."""
    try:
        from data.downloaders import download_securities_fundamentals
        download_securities_fundamentals(target_sec_id=data.get("security_id"))
        return {"ok": True, "message": "Fundamentals downloaded"}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/download/yahoo-prices")
def download_yahoo_prices(data: dict = {}):
    """Download historical prices from Yahoo Finance."""
    try:
        from data.downloaders import download_historical_prices_from_yahoo
        download_historical_prices_from_yahoo(
            tsperiod=data.get("period", "1m"),
            target_sec_id=data.get("security_id"),
        )
        return {"ok": True, "message": "Yahoo prices downloaded"}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/download/tv-info")
def download_tv_info(data: dict = {}):
    """Update Securities metadata from TradingView screener."""
    try:
        from data.downloaders import download_securities_info_from_tradingview
        download_securities_info_from_tradingview(
            target_sec_id=data.get("security_id"),
            overwrite=data.get("overwrite", False),
        )
        return {"ok": True, "message": "TradingView info updated"}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/download/isin")
def download_isin(data: dict = {}):
    """Fetch missing ISINs from EODHD Fundamentals."""
    try:
        from data.downloaders import download_isin_from_eodhd
        download_isin_from_eodhd(target_sec_id=data.get("security_id"))
        return {"ok": True, "message": "ISIN lookup complete"}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/download/tv-prices")
def download_tv_prices(data: dict = {}):
    """Download historical prices from TradingView."""
    try:
        from data.downloaders import download_historical_prices_from_tradingview
        download_historical_prices_from_tradingview(
            tsperiod=data.get("period", "1m"),
            target_sec_id=data.get("security_id"),
        )
        return {"ok": True, "message": "TradingView prices downloaded"}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/download/solidus-bonds")
def download_solidus_bonds(data: dict = {}):
    """Download Greek bond prices from Solidus PDF, optionally scoped to one security."""
    try:
        from data.downloaders import download_bond_prices_from_solidus
        target_sec_id = data.get("security_id")
        result = download_bond_prices_from_solidus(target_sec_id=target_sec_id)
        if target_sec_id is not None:
            message = "Solidus bond price updated" if result.get("target_matched") else "ISIN not found in Solidus PDF"
        else:
            message = f"Solidus bond prices downloaded ({result.get('updated_count', 0)} updated)"
        return {"ok": True, "message": message}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/prices")
def add_price(data: dict):
    """Insert or update a single historical price record."""
    from database.connection import get_connection as _gc
    conn = _gc()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO Historical_Prices (Securities_Id, Date, Close)
            VALUES (%s, %s, %s)
            ON CONFLICT (Securities_Id, Date) DO UPDATE SET Close = EXCLUDED.Close
        """, (data["security_id"], data["date"], data["close"]))
        conn.commit()
        return {"ok": True}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.post("/fx")
def add_fx_rate(data: dict):
    """Insert or update a historical FX rate record."""
    from database.connection import get_connection as _gc
    conn = _gc()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO Historical_FX (Currencies_Id_1, Date, FX_Rate)
            VALUES (%s, %s, %s)
            ON CONFLICT (Currencies_Id_1, Date) DO UPDATE SET FX_Rate = EXCLUDED.FX_Rate
        """, (data["currency_id"], data["date"], data["rate"]))
        conn.commit()
        return {"ok": True}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()



class BulkDeletePricesRequest(BaseModel):
    security_id: int
    dates: List[str]

@router.delete("/prices/bulk")
def delete_prices_bulk(payload: BulkDeletePricesRequest):
    """Delete multiple historical price records in a single query."""
    if not payload.dates:
        return {"deleted": 0}
    from database.connection import get_connection as _gc
    conn = _gc()
    try:
        cur = conn.cursor()
        placeholders = ','.join(['%s'] * len(payload.dates))
        cur.execute(
            f"DELETE FROM Historical_Prices WHERE Securities_Id=%s AND Date IN ({placeholders})",
            [payload.security_id] + payload.dates,
        )
        conn.commit()
        return {"deleted": cur.rowcount}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.delete("/prices")
def delete_price(security_id: int, date: str):
    """Delete a specific historical price record."""
    from database.connection import get_connection as _gc
    conn = _gc()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM Historical_Prices WHERE Securities_Id=%s AND Date=%s", (security_id, date))
        conn.commit()
        return {"deleted": cur.rowcount}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.delete("/fx")
def delete_fx_rate(currency_id: int, date: str):
    """Delete a specific historical FX rate record."""
    from database.connection import get_connection as _gc
    conn = _gc()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM Historical_FX WHERE Currencies_Id_1=%s AND Date=%s", (currency_id, date))
        conn.commit()
        return {"deleted": cur.rowcount}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


# ── Watchlist ─────────────────────────────────────────────────────────────────

@router.get("/watchlist")
def get_watchlist_endpoint():
    from database.queries import get_watchlist
    return _df(get_watchlist())


@router.post("/watchlist")
def upsert_watchlist(data: dict):
    from database.queries import add_watchlist_item
    try:
        add_watchlist_item(
            securities_id=int(data["securities_id"]),
            target_price=data.get("target_price"),
            stop_loss=data.get("stop_loss"),
            note=data.get("note"),
        )
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.delete("/watchlist/{watchlist_id}")
def delete_watchlist(watchlist_id: int):
    from database.queries import remove_watchlist_item
    try:
        remove_watchlist_item(watchlist_id)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Alerts ────────────────────────────────────────────────────────────────────

@router.get("/alerts")
def get_alerts_endpoint():
    from database.queries import get_alerts
    return _df(get_alerts())


@router.post("/alerts")
def save_alert_endpoint(data: dict):
    from database.queries import save_alert
    try:
        save_alert(
            alert_type=data["alert_type"],
            securities_id=data.get("securities_id"),
            asset_type=data.get("asset_type"),
            threshold=data.get("threshold"),
            direction=data.get("direction"),
            note=data.get("note"),
            alert_id=data.get("alert_id"),
        )
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.patch("/alerts/{alert_id}/toggle")
def toggle_alert_endpoint(alert_id: int, data: dict):
    from database.queries import toggle_alert
    try:
        toggle_alert(alert_id, bool(data.get("is_active", True)))
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.delete("/alerts/{alert_id}")
def delete_alert_endpoint(alert_id: int):
    from database.queries import delete_alert
    try:
        delete_alert(alert_id)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Shiller CAPE (U.S. market valuation) ────────────────────────────────────────

@router.get("/shiller-cape")
def get_shiller_cape_endpoint(years: Optional[int] = Query(None)):
    """Full (or last N years of) U.S. Shiller CAPE monthly time series."""
    from database.queries import get_shiller_cape
    return _df(get_shiller_cape(years=years))


@router.get("/shiller-cape/summary")
def get_shiller_cape_summary_endpoint():
    """Latest CAPE reading, its percentile rank since 1881, and the long-run median."""
    from database.queries import get_shiller_cape_summary
    result = get_shiller_cape_summary()
    if result is None:
        raise HTTPException(404, "No Shiller CAPE data yet — use Download below.")
    return result


@router.post("/download/shiller-cape")
def download_shiller_cape_endpoint():
    """Re-download the Shiller CAPE dataset from shillerdata.com."""
    from data.downloaders import download_shiller_cape
    result = download_shiller_cape()
    if result.get("error"):
        raise HTTPException(502, result["error"])
    return {"ok": True, "message": f"{result['rows']} monthly rows updated"}


# ── Interest rates ──────────────────────────────────────────────────────────────
# Series definitions (Rate_Series), their history (Historical_Rates) and fund-vs-rate
# tracking (Rate_Tracking) — see database/rates.py.

@router.get("/rates")
def get_rates_endpoint(series: Optional[str] = Query(None), years: Optional[int] = Query(None)):
    """Rate history, optionally limited to comma-separated series codes (see /rates/series)
    and/or the last N years."""
    from database.rates import get_rate_series
    codes = [s.strip().upper() for s in series.split(",") if s.strip()] if series else None
    return _df(get_rate_series(codes, years))


@router.get("/rates/summary")
def get_rates_summary_endpoint():
    """Per active series: latest value, last policy change, overnight move vs. 30-day average;
    per tracked fund: its return vs. the rate; and the currently active alerts."""
    from database.rates import get_rates_summary
    result = get_rates_summary()
    if result is None:
        raise HTTPException(404, "No interest-rate data yet — use Download Interest Rates on the Downloads tab.")
    return result


@router.get("/rates/series")
def get_rate_series_defs():
    """All rate series definitions (active or not) with their latest date and row count."""
    from database.rates import get_series_defs
    return _df(get_series_defs())


@router.post("/rates/series")
def upsert_rate_series(data: dict):
    """Create (no id) or update (id) a rate series definition."""
    from database.rates import upsert_series
    try:
        return {"id": upsert_series(data)}
    except (ValueError, LookupError) as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        if "unique" in str(e).lower():
            raise HTTPException(409, f"A rate series with code '{data.get('code')}' already exists.")
        raise HTTPException(500, str(e))


@router.delete("/rates/series/{series_id}")
def delete_rate_series(series_id: int):
    """Delete a series, along with its history and any tracking rows that use it."""
    from database.rates import delete_series
    if not delete_series(series_id):
        raise HTTPException(404, "Rate series not found")
    return {"deleted": series_id}


@router.post("/rates/values")
def add_rate_value(data: dict):
    """Add or overwrite one value (percent) for a series — how a MANUAL series is fed."""
    from database.rates import upsert_rates
    try:
        series_id, day, rate = int(data["series_id"]), str(data["date"])[:10], float(data["rate"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "series_id, date and rate are required")
    upsert_rates([(series_id, day, rate, "Manual")])
    return {"ok": True}


@router.delete("/rates/values")
def delete_rate_value_endpoint(series_id: int = Query(...), date: str = Query(...)):
    from database.rates import delete_rate_value
    if not delete_rate_value(series_id, date[:10]):
        raise HTTPException(404, "No such value")
    return {"deleted": True}


@router.get("/rates/tracking")
def get_rate_tracking():
    from database.rates import get_tracking_defs
    return _df(get_tracking_defs())


@router.post("/rates/tracking")
def upsert_rate_tracking(data: dict):
    """Track a fund against an overnight rate series: window (days) and alert threshold (pp)."""
    from database.rates import upsert_tracking
    try:
        return {"id": upsert_tracking(data)}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except LookupError as e:
        raise HTTPException(404, str(e))


@router.get("/rates/fund-durations")
def get_rate_fund_durations():
    """{securities_id: duration in years} for bond funds too long in duration to track against an
    overnight rate — feeds the warning in Rates -> Configure -> Track a fund."""
    from database.rates import get_fund_durations
    return get_fund_durations()


@router.delete("/rates/tracking/{tracking_id}")
def delete_rate_tracking(tracking_id: int):
    from database.rates import delete_tracking
    if not delete_tracking(tracking_id):
        raise HTTPException(404, "Tracking row not found")
    return {"deleted": tracking_id}


@router.post("/download/rates")
def download_rates_endpoint(series: Optional[str] = Query(None)):
    """Refresh every active downloadable rate series (or just the comma-separated codes)."""
    from data.downloaders import download_interest_rates
    codes = [s.strip() for s in series.split(",") if s.strip()] if series else None
    result = download_interest_rates(codes)
    if result["errors"] and not result["rows"]:
        raise HTTPException(502, "; ".join(result["errors"]))
    msg = f"{result['rows']} rate rows updated"
    if result["errors"]:
        msg += f" ({len(result['errors'])} source(s) failed: {'; '.join(result['errors'])})"
    return {"ok": True, "message": msg}


@router.post("/download/country-cape")
def download_country_cape_endpoint():
    """Re-download country-level CAPE snapshots from Siblis Research's free-tier API."""
    from data.downloaders import download_country_cape_ratios
    result = download_country_cape_ratios()
    msg = f"{result['rows']} snapshots updated"
    if result.get("errors"):
        msg += f" ({len(result['errors'])} countries failed)"
    return {"ok": True, "message": msg}


# ── Country CAPE ratios (manual entry) ──────────────────────────────────────────

@router.get("/country-cape")
def get_country_cape_endpoint():
    from database.queries import get_country_cape_ratios
    return _df(get_country_cape_ratios())


class CountryCapePayload(BaseModel):
    id: Optional[int] = None
    country: str
    as_of_date: str
    cape_ratio: float
    source: Optional[str] = None


@router.post("/country-cape")
def upsert_country_cape_endpoint(payload: CountryCapePayload):
    from database.queries import upsert_country_cape_ratio
    try:
        upsert_country_cape_ratio(payload.country, payload.as_of_date, payload.cape_ratio, payload.source, payload.id)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.delete("/country-cape/{cape_id}")
def delete_country_cape_endpoint(cape_id: int):
    from database.queries import delete_country_cape_ratio
    try:
        delete_country_cape_ratio(cape_id)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))
