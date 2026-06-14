"""Market Data API endpoints: currencies, FX rates, securities, price history."""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional
import math
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


# ── Securities ────────────────────────────────────────────────────────────────

@router.get("/securities")
def get_securities(search: Optional[str] = Query(None)):
    clause = "AND (LOWER(s.Securities_Name) LIKE %(s)s OR LOWER(s.Ticker) LIKE %(s)s)" if search else ""
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
                   COALESCE((SELECT COUNT(*) FROM Historical_Prices WHERE Securities_Id = s.Securities_Id), 0) AS price_records,
                   (SELECT Close FROM Historical_Prices WHERE Securities_Id = s.Securities_Id ORDER BY Date DESC LIMIT 1) AS latest_price,
                   (SELECT Date FROM Historical_Prices WHERE Securities_Id = s.Securities_Id ORDER BY Date DESC LIMIT 1) AS price_date,
                   COALESCE((SELECT COUNT(*) FROM Investments WHERE Securities_Id = s.Securities_Id), 0) AS investment_count,
                   COALESCE((SELECT SUM(Quantity) FROM Holdings WHERE Securities_Id = s.Securities_Id), 0) AS held_quantity
            FROM Securities s
            LEFT JOIN Currencies c ON s.Currencies_Id = c.Currencies_Id
            WHERE 1=1 {clause}
            ORDER BY s.Securities_Name ASC
        """, conn, params=params if params else None)
    return _df(df)


@router.get("/price-history")
def get_price_history(
    security_id: int = Query(...),
    from_date: str = Query("2020-01-01"),
):
    """Daily close price history for one security."""
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT Date::text AS date, Close AS close, Source AS source,
                   Downloaded_At::text AS downloaded_at
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
def download_solidus_bonds():
    """Download Greek bond prices from Solidus PDF."""
    try:
        from data.downloaders import download_bond_prices_from_solidus
        download_bond_prices_from_solidus()
        return {"ok": True, "message": "Solidus bond prices downloaded"}
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
