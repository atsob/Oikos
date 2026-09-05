import logging
import math
import yfinance as yf
import pdfplumber
import requests
import io
import re
import time
import pandas as pd
from tvDatafeed import TvDatafeed, Interval
from database.connection import get_connection
from ai.llm import get_custom_session
from datetime import datetime, timedelta
from decimal import Decimal
from config.settings import ENV_CONFIG
from psycopg2.extras import execute_batch, Json


# ── Trading-day validation ─────────────────────────────────────────────────────
# Maps TradingView TV_Exchange codes → exchange_calendars calendar IDs.
# Securities whose exchange is NOT listed get a weekday-only (Mon-Fri) check.
# Securities in _TV_ALWAYS_OPEN (crypto, FX 24/7) bypass the day filter entirely.
_TV_TO_XCAL: dict[str, str] = {
    "ATHEX":    "ASEX",    # Athens Stock Exchange
    "AMEX":     "XASE",    # NYSE American
    "BME":      "XMAD",    # Bolsa de Madrid
    "EURONEXT": "XPAR",    # Euronext (Paris as default)
    "FWB":      "XFRA",    # Frankfurt
    "NASDAQ":   "NASDAQ",
    "NYSE":     "NYSE",
    "NYMEX":    "NYMEX",   # Commodities futures
    "TSX":      "TSX",     # Toronto
    "VIE":      "XWBO",    # Vienna
    "XETR":     "XETR",    # Frankfurt XETRA
    "ICE":      "ICE",     # ICE Futures
    "SWB":      "XSTU",    # Stuttgart
    "CBOE":     "CFE",     # CBOE Futures
}

# Exchanges that trade around the clock — never filter by day
_TV_ALWAYS_OPEN: frozenset = frozenset({
    "COINBASE", "KRAKEN", "OKX", "BINANCE", "GEMINI",
    "BITSTAMP", "KUCOIN", "BYBIT", "CRYPTO",
})

# FX / commodity data providers on TradingView.
# These trade Mon–Fri (no fixed exchange calendar) so today's price is valid
# on any weekday regardless of time-of-day.  They are NOT 24/7 (stop on
# weekends) so they don't belong in _TV_ALWAYS_OPEN.
_TV_WEEKDAY_OPEN: frozenset = frozenset({
    "FX_IDC",      # TradingView FX / commodity composite feed (XAGEUR, XAUUSD …)
    "TVC",         # TradingView Composite (indices, metals, economic data)
    "OANDA",       # OANDA FX rates
    "FOREXCOM",    # Forex.com
    "FXCM",        # FXCM
    "CURRENCYCOM", # Currency.com
    "CAPITALCOM",  # Capital.com CFDs
    "PYTH",        # Pyth Network oracle prices
    "ICEUS",       # ICE US (some commodity indices on TV)
})

_xcal_cache: dict[str, object] = {}   # cache calendar objects (one per process)


def _is_tv_trading_day(dt_str: str, tv_exchange: str) -> bool:
    """Return True if *dt_str* is a valid trading session for *tv_exchange*.

    Logic (in order):
      1. Crypto / always-open exchanges → True unconditionally.
      2. Saturday / Sunday → False for all equity/futures/FX exchanges.
      3. FX / commodity weekday-open exchanges → True on weekdays.
      4. Exchange in _TV_TO_XCAL → query the exchange_calendars holiday calendar.
      5. Unknown exchange → weekday check already passed in step 2; allow.
    """
    exch = (tv_exchange or "").upper().strip()

    # 1. Crypto — 24/7, no restriction
    if exch in _TV_ALWAYS_OPEN:
        return True

    from datetime import date as _date
    try:
        dt = _date.fromisoformat(dt_str)
    except ValueError:
        return True     # unparseable date — don't block it

    # 2. Weekends are never trading days for equity/futures/FX markets
    if dt.weekday() >= 5:       # 5 = Saturday, 6 = Sunday
        return False

    # 3. FX / commodity weekday-open — valid on any weekday (no holiday calendar)
    if exch in _TV_WEEKDAY_OPEN:
        return True

    # 4. Full holiday calendar via exchange_calendars
    cal_code = _TV_TO_XCAL.get(exch)
    if cal_code:
        try:
            import exchange_calendars as _xcals
            if cal_code not in _xcal_cache:
                _xcal_cache[cal_code] = _xcals.get_calendar(cal_code)
            return bool(_xcal_cache[cal_code].is_session(dt_str))
        except Exception as _e:
            logging.debug("exchange_calendars check failed for %s %s: %s", cal_code, dt_str, _e)

    # 4. Exchange not in map — weekday check already passed; allow
    return True


def _today_price_state(tv_exchange: str) -> str:
    """Return the current state of today's session for *tv_exchange*.

    Returns one of:
      "pre_market"  — session hasn't started yet → skip (TradingView returns garbage)
      "open"        — session is in progress    → allow (live intraday price)
      "closed"      — session ended + 15 min    → allow (final authoritative close)
      "holiday"     — no session today          → skip (handled by Guard 2 already)

    For crypto (_TV_ALWAYS_OPEN): always "open".
    For FX/commodity weekday-open exchanges (_TV_WEEKDAY_OPEN): "open" on
      weekdays, "holiday" on weekends.
    For exchanges not in any known set: consistent with _is_tv_trading_day() —
      allow on weekdays ("open"), block on weekends ("holiday").
    """
    from datetime import datetime, timezone, timedelta, date as _date

    exch = (tv_exchange or "").upper().strip()

    if exch in _TV_ALWAYS_OPEN:
        return "open"

    # FX / commodity weekday-open exchanges — valid any time on weekdays
    if exch in _TV_WEEKDAY_OPEN:
        return "open" if _date.today().weekday() < 5 else "holiday"

    cal_code = _TV_TO_XCAL.get(exch)
    if not cal_code:
        # Exchange not in any known set.  Consistent with _is_tv_trading_day():
        # allow on weekdays, block on weekends.  This covers FX/commodity pairs
        # whose TV exchange code is not yet in _TV_WEEKDAY_OPEN (e.g. new data
        # providers) rather than silently dropping today's valid price.
        return "open" if _date.today().weekday() < 5 else "holiday"

    try:
        import exchange_calendars as _xcals
        today_str = _date.today().isoformat()

        if cal_code not in _xcal_cache:
            _xcal_cache[cal_code] = _xcals.get_calendar(cal_code)
        cal = _xcal_cache[cal_code]

        if not cal.is_session(today_str):
            return "holiday"

        now_utc   = datetime.now(timezone.utc)
        open_utc  = cal.session_open(today_str).tz_convert("UTC").to_pydatetime()
        close_utc = cal.session_close(today_str).tz_convert("UTC").to_pydatetime()

        if now_utc < open_utc:
            return "pre_market"
        if now_utc < close_utc + timedelta(minutes=15):
            return "open"
        return "closed"

    except Exception as exc:
        # "unknown" makes Guard 1.5 skip today's price for every security on this
        # exchange — a missing/broken exchange_calendars install silently does this
        # for every mapped exchange, every day, so this needs to be loud, not debug.
        logging.warning("_today_price_state(%s) failed, treating as unknown: %s", exch, exc)
        return "unknown"


# Kept for backward-compat (used in tests)
def _market_closed_for_today(tv_exchange: str) -> bool:
    return _today_price_state(tv_exchange) == "closed"


def download_historical_fx(tsperiod=None, currencies_id=None):
    """Download historical FX rates from Yahoo Finance.

    Parameters
    ----------
    tsperiod : str, optional
        Yahoo Finance period string (e.g. "1mo", "1y"). Defaults to "1m".
    currencies_id : int, optional
        When provided, only download rates for this single Currencies_Id.
        When omitted (None), download rates for every non-EUR currency.
    """
    conn = get_connection()
    cur = conn.cursor()
    custom_session = get_custom_session()

    if not tsperiod:
        tsperiod="1m"

    try:
        cur.execute("SELECT Currencies_Id FROM Currencies WHERE Currencies_ShortName = 'EUR'")
        target_id = cur.fetchone()[0]

        if currencies_id is not None:
            cur.execute(
                "SELECT Currencies_Id, Currencies_ShortName FROM Currencies "
                "WHERE Currencies_Id = %s AND Currencies_ShortName != 'EUR'",
                (int(currencies_id),),
            )
        else:
            cur.execute(
                "SELECT Currencies_Id, Currencies_ShortName FROM Currencies "
                "WHERE Currencies_ShortName != 'EUR'"
            )
        currencies = cur.fetchall()
        
        for base_id, symbol in currencies:
            logging.info(f"Downloading historical data for {symbol}...")
            ticker_symbol = f"EUR{symbol}=X"
            ticker = yf.Ticker(ticker_symbol, session=custom_session)
            hist = ticker.history(period=tsperiod)
            
            if hist.empty:
                logging.warning(f"No data found for {ticker_symbol}")
                continue
            
            for date, row in hist.iterrows():
                rate_to_eur = float(1 / row['Close'])
                formatted_date = date.strftime('%Y-%m-%d')
                
                cur.execute("""
                    INSERT INTO Historical_FX (Currencies_Id_1, Currencies_Id_2, Date, FX_Rate)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (Currencies_Id_1, Currencies_Id_2, Date)
                    DO UPDATE SET FX_Rate = EXCLUDED.FX_Rate
                """, (base_id, target_id, formatted_date, rate_to_eur))
            
            conn.commit()
            logging.info(f"Completed import for {symbol}")
            
    except Exception as e:
        logging.error(f"❌ Error: {e}")
        logging.error(f"Error: {e}")
    finally:
        cur.close()
        conn.close()

def _ts_to_date(ts):
    """Convert a Yahoo Finance Unix timestamp (int or None) to a date, or None."""
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts)).date()
    except (OSError, OverflowError, ValueError):
        return None


def _compute_fair_values(cur, eps_updates):
    """Recompute Fair_Value on Securities_Quote for each (sec_id, trailing_eps,
    forward_eps) in eps_updates, using that security's OWN historical median P/E
    (from Historical_Prices joined against Securities_Annual_EPS, just upserted by
    the caller) times its current normalized EPS.

    This is an approximation of GuruFocus's "GF Value" concept — reversion to a
    stock's own historical trading multiple — not a reproduction of their exact,
    undisclosed formula: Yahoo's free tier only exposes ~4 years of annual EPS
    (GuruFocus typically uses a decade-plus), and GuruFocus blends multiple
    multiples (P/E, P/S, P/B) plus its own growth model rather than P/E alone.
    Tested against a real published GF Value (AAPL) during development — this
    approach lands within ~10%, versus ~30% off for a pure growth-rate heuristic.

    Skipped (leaves Fair_Value NULL) for a security with no positive EPS (trailing
    or forward), no annual EPS history at all, or fewer than 6 monthly price points
    to compute a median from — not enough basis for a trustworthy estimate.

    A dedicated pass rather than folded into the scalar quote/dividend updates above
    since it re-reads years of Historical_Prices per security — meaningfully heavier
    than a single-row upsert — and only for securities that actually got EPS data
    this run.
    """
    if not eps_updates:
        return 0

    norm_eps_map = {}
    for sec_id, trailing_eps, forward_eps in eps_updates:
        vals = [v for v in (trailing_eps, forward_eps) if v is not None and v > 0]
        if vals:
            norm_eps_map[sec_id] = sum(vals) / len(vals)
    if not norm_eps_map:
        return 0

    sec_ids = list(norm_eps_map.keys())
    cur.execute("""
        SELECT Securities_Id, Fiscal_Year_End, Diluted_EPS
        FROM Securities_Annual_EPS
        WHERE Securities_Id = ANY(%s) AND Diluted_EPS IS NOT NULL
        ORDER BY Securities_Id, Fiscal_Year_End
    """, (sec_ids,))
    eps_df = pd.DataFrame(cur.fetchall(), columns=['securities_id', 'fiscal_year_end', 'diluted_eps'])
    if eps_df.empty:
        return 0
    eps_df['fiscal_year_end'] = pd.to_datetime(eps_df['fiscal_year_end'])

    fair_value_updates = []
    for sec_id, norm_eps in norm_eps_map.items():
        eps_hist = eps_df[eps_df['securities_id'] == sec_id].sort_values('fiscal_year_end')
        if eps_hist.empty:
            continue

        # Monthly-sampled (not daily) price history — a median needs a
        # representative spread, not every trading day, and this keeps years of
        # history cheap to pull per security across a few hundred securities.
        cur.execute("""
            SELECT DISTINCT ON (date_trunc('month', Date)) Date, Close
            FROM Historical_Prices
            WHERE Securities_Id = %s AND Date >= %s
            ORDER BY date_trunc('month', Date), Date DESC
        """, (sec_id, eps_hist['fiscal_year_end'].iloc[0].date()))
        px_rows = cur.fetchall()
        if len(px_rows) < 6:
            continue

        px_df = pd.DataFrame(px_rows, columns=['date', 'close'])
        px_df['date'] = pd.to_datetime(px_df['date'])
        px_df = px_df.sort_values('date')

        merged = pd.merge_asof(
            px_df, eps_hist[['fiscal_year_end', 'diluted_eps']],
            left_on='date', right_on='fiscal_year_end', direction='backward',
        )
        merged = merged.dropna(subset=['diluted_eps'])
        merged = merged[merged['diluted_eps'] > 0]
        if len(merged) < 6:
            continue

        pe_series = merged['close'].astype(float) / merged['diluted_eps'].astype(float)
        # Clamp to a sane range — a thin/volatile EPS history can otherwise produce
        # an implied multiple in the hundreds (near-zero past earnings) or single
        # digits (a one-off earnings spike), neither of which is a usable "normal"
        # multiple to project forward.
        median_pe = max(3.0, min(float(pe_series.median()), 100.0))

        fair_value_updates.append((
            round(median_pe * norm_eps, 4), round(median_pe, 4),
            round(norm_eps, 4), int(len(eps_hist)), sec_id,
        ))

    if fair_value_updates:
        execute_batch(cur, """
            UPDATE Securities_Quote
            SET Fair_Value = %s, Fair_Value_Pe = %s, Fair_Value_Eps = %s, Fair_Value_Years = %s
            WHERE Securities_Id = %s
        """, fair_value_updates, page_size=200)

    return len(fair_value_updates)


def download_securities_info_from_yahoo(target_sec_id=None):
    """Download securities information from Yahoo Finance.

    Fetches sector, industry, analyst rating, target price, dividend summary
    fields (yield, rate, ex-date, pay-date, payout ratio, 5Y avg yield), and
    trailing/forward EPS + historical annual EPS (income_stmt — Yahoo's free
    tier only exposes ~4 years) for all securities that have a Yahoo_Ticker
    defined. The EPS data feeds a Fair Value estimate (this security's own
    historical median P/E, from Historical_Prices joined against the annual
    EPS points, times current normalized EPS) written to Securities_Quote —
    see _compute_fair_values below for the actual calculation.

    Requests are made in parallel (up to MAX_WORKERS concurrent threads) to
    minimise wall-clock time; DB writes are batched into a single
    executemany + commit.

    Dividend fields come from the same ticker.info call — no extra API
    requests.  Historical dividend records and frequency are downloaded
    separately via download_dividend_history().
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from database.queries import _ensure_fair_value_schema

    MAX_WORKERS = 5   # conservative — avoids Yahoo rate-limiting

    _ensure_fair_value_schema()

    conn = get_connection()
    cur  = conn.cursor()
    custom_session = get_custom_session()

    def _fetch(sec_id, sec_name, symbol):
        """Fetch Yahoo info for one ticker; returns a result tuple."""
        try:
            ticker = yf.Ticker(symbol, session=custom_session)
            info   = ticker.info

            # ── Existing fields ────────────────────────────────────────────
            sector   = info.get('sector')   or None
            industry = info.get('industry') or None
            _raw     = info.get('recommendationKey')
            rating   = (
                None
                if (not _raw or str(_raw).strip().lower() in ('none', 'n/a', ''))
                else str(_raw).strip().lower()
            )
            # ISIN — not in ticker.info; requires the dedicated ticker.isin
            # property which hits a separate Yahoo search endpoint.
            # Validate: must be exactly 12 alphanumeric characters.
            isin = None
            try:
                _isin_raw = ticker.isin
                if (isinstance(_isin_raw, str)
                        and len(_isin_raw.strip()) == 12
                        and _isin_raw.strip().upper() not in ('-', 'N/A', 'NONE')):
                    isin = _isin_raw.strip().upper()
            except Exception:
                pass   # not available for this ticker — leave as None

            # ── Dividend summary (free — already in ticker.info) ───────────
            # Yahoo's dividendYield is already expressed as a percentage value
            # (e.g. 0.96 means 0.96%, 3.55 means 3.55%) — store as-is, no ×100.
            _dy = info.get('dividendYield')
            div_yield = round(float(_dy), 4) if _dy else None

            div_rate = info.get('dividendRate') or None  # annual per share

            # fiveYearAvgDividendYield is already in % (e.g. 2.34).
            _fa = info.get('fiveYearAvgDividendYield')
            five_yr_avg = round(float(_fa), 4) if _fa else None

            # payoutRatio is a decimal (0.45 = 45 %); store as %.
            _pr = info.get('payoutRatio')
            payout = round(float(_pr) * 100, 4) if _pr else None

            ex_div_date  = _ts_to_date(info.get('exDividendDate'))
            div_pay_date = _ts_to_date(
                info.get('dividendDate') or info.get('lastDividendDate')
            )

            # ── Quote summary (free — already in ticker.info) ──────────────
            # Powers Security Detail's Overview tab. Price/change themselves
            # aren't stored here — Price already comes from Historical_Prices
            # (latest_price), Change is derived client-side from that vs.
            # Prev_Close, avoiding a stale duplicate of data we already have.
            def _clamped(raw, max_abs):
                """None if raw isn't a finite number or exceeds the target
                NUMERIC column's precision — e.g. a distressed penny stock's
                near-zero earnings can make Yahoo's trailingPE absurdly large
                (into the millions), which would otherwise raise a Postgres
                'numeric field overflow' on this one row and, since every
                security's quote/sector/dividend/ISIN update shares a single
                transaction, silently discard the entire batch's results."""
                if raw is None:
                    return None
                try:
                    val = float(raw)
                except (TypeError, ValueError):
                    return None
                if not math.isfinite(val) or abs(val) >= max_abs:
                    return None
                return val

            # Some exchanges quote in a minor currency unit Yahoo reports as
            # its own currency code — e.g. London Stock Exchange ordinary
            # shares in "GBp"/"GBX" (pence) rather than "GBP" (pounds), a
            # factor of 100 apart. Every price-like field below (but not
            # dividendRate, dividendYield, trailingPE, or marketCap — Yahoo
            # already reports those in the major unit even for GBp tickers)
            # needs dividing by this scale before storing, or a GBp security's
            # market value ends up ~100x too high everywhere in the app.
            price_scale = 100.0 if str(info.get('currency') or '').strip() in ('GBp', 'GBX') else 1.0

            def _scaled(raw):
                return (raw / price_scale) if raw else None

            target_price = _scaled(info.get('targetMeanPrice'))

            quote = (
                _scaled(info.get('previousClose')),
                _scaled(info.get('open')),
                _scaled(info.get('dayHigh')),
                _scaled(info.get('dayLow')),
                _scaled(info.get('fiftyTwoWeekHigh')),
                _scaled(info.get('fiftyTwoWeekLow')),
                info.get('volume') or None,
                info.get('averageVolume') or None,
                _clamped(info.get('trailingPE'), 10 ** 6),   # NUMERIC(10,4)
                _clamped(info.get('marketCap'), 10 ** 18),   # NUMERIC(20,2)
            )

            # ── EPS (fair-value input) ──────────────────────────────────────
            # trailing/forward EPS are free fields on the same info dict already
            # fetched above. Historical annual EPS needs a separate property —
            # income_stmt — which isn't available for every security type (ETFs,
            # crypto, bonds, some foreign listings raise or return None here),
            # so it's wrapped on its own rather than failing the whole fetch.
            trailing_eps = _scaled(info.get('trailingEps'))
            forward_eps  = _scaled(info.get('forwardEps'))
            annual_eps = []
            try:
                stmt = ticker.income_stmt
                if stmt is not None and 'Diluted EPS' in stmt.index:
                    for dt, val in stmt.loc['Diluted EPS'].items():
                        if val is not None and math.isfinite(float(val)):
                            annual_eps.append((pd.Timestamp(dt).date(), _scaled(float(val))))
            except Exception:
                pass

            return (sec_id, sec_name, symbol,
                    sector, industry, rating, target_price,
                    div_yield, div_rate, five_yr_avg, payout,
                    ex_div_date, div_pay_date,
                    isin,
                    quote,
                    price_scale,
                    (trailing_eps, forward_eps, annual_eps),
                    None)
        except Exception as exc:
            return (sec_id, sec_name, symbol,
                    None, None, None, None,
                    None, None, None, None,
                    None, None,
                    None,
                    None,
                    None,
                    None,
                    str(exc))

    try:
        base_query = """
            SELECT Securities_Id, Securities_Name, Yahoo_Ticker
            FROM   Securities
            WHERE  Yahoo_Ticker IS NOT NULL
              AND  Yahoo_Ticker != ''
              AND  Securities_Name NOT LIKE 'Hellenic T-Bill%'
        """
        if target_sec_id:
            base_query += f" AND Securities_Id = {int(target_sec_id)}"
        base_query += " ORDER BY Securities_Name ASC"

        cur.execute(base_query)
        securities = cur.fetchall()

        if not securities:
            logging.warning("No matching securities found with a valid Yahoo Ticker.")
            return

        total = len(securities)
        print(f"Fetching Yahoo info for {total} securities (up to {MAX_WORKERS} in parallel)…")
        logging.info(f"Fetching Yahoo info for {total} securities…")

        # ── Parallel fetch ────────────────────────────────────────────────────
        results = []
        futures = {}
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            for sec_id, sec_name, symbol in securities:
                f = pool.submit(_fetch, sec_id, sec_name, symbol)
                futures[f] = sec_name

            for f in as_completed(futures):
                results.append(f.result())

        # ── Batch DB update ───────────────────────────────────────────────────
        sec_industry_updates = []    # rows that have sector/industry
        div_updates          = []    # ALL rows that returned without error
        isin_updates         = []    # (isin, sec_id) where Yahoo returned an ISIN
        quote_updates        = []    # ALL rows that returned without error
        price_scale_updates  = []    # (price_scale, sec_id) — only when non-default (100)
        eps_updates          = []    # (sec_id, trailing_eps, forward_eps) — always, even with no annual history
        annual_eps_rows      = []    # (sec_id, fiscal_year_end, diluted_eps)

        for row in results:
            (sec_id, sec_name, symbol,
             sector, industry, rating, target_price,
             div_yield, div_rate, five_yr_avg, payout,
             ex_div_date, div_pay_date,
             isin,
             quote,
             price_scale,
             eps_data,
             err) = row

            if err:
                print(f"  ⚠️ Error fetching {sec_name} ({symbol}): {err}")
                logging.warning(f"Yahoo info error for {sec_name} ({symbol}): {err}")
                continue

            # Sector / industry update (only when both are present)
            if sector and industry:
                print(f"  ✔ {sec_name}: sector={sector}, industry={industry}, "
                      f"rating={rating}, target={target_price}")
                logging.info(f"Yahoo info {sec_name}: sector={sector}, "
                             f"industry={industry}, rating={rating}, target={target_price}")
                sec_industry_updates.append(
                    (sector, industry, rating, target_price, sec_id)
                )
            else:
                print(f"  ⚠️ No sector/industry for {sec_name} ({symbol})")
                logging.warning(f"No sector/industry for {sec_name} ({symbol})")

            # Dividend update (always, even for crypto/ETFs with no sector)
            has_div = any(v is not None for v in
                          (div_yield, div_rate, ex_div_date, div_pay_date))
            if has_div:
                print(f"       div: yield={div_yield}% rate={div_rate} "
                      f"ex={ex_div_date} pay={div_pay_date} "
                      f"payout={payout}% 5yr={five_yr_avg}%")
            div_updates.append(
                (div_yield, div_rate, five_yr_avg, payout,
                 ex_div_date, div_pay_date, sec_id)
            )

            # ISIN update (separate — only when Yahoo returned a valid ISIN)
            if isin:
                print(f"       isin={isin}")
                isin_updates.append((isin, sec_id))

            # Quote update (Security Detail Overview tab)
            quote_updates.append((sec_id, *quote))

            # Price_Scale update — only written when non-default (100), since
            # the column already defaults to 1 and there's no need to touch
            # every security's row on every run just to reassert that default.
            if price_scale and price_scale != 1.0:
                price_scale_updates.append((price_scale, sec_id))

            # EPS (fair-value input) — trailing/forward kept in memory only, for
            # _compute_fair_values below; annual history is the one that's persisted.
            trailing_eps, forward_eps, annual_eps = eps_data
            eps_updates.append((sec_id, trailing_eps, forward_eps))
            for fy_end, eps_val in annual_eps:
                if eps_val is not None:
                    annual_eps_rows.append((sec_id, fy_end, eps_val))

        if sec_industry_updates:
            cur.executemany("""
                UPDATE Securities
                SET    Sector               = %s,
                       Industry             = %s,
                       Analyst_Rating       = COALESCE(%s, Analyst_Rating),
                       Analyst_Target_Price = COALESCE(%s, Analyst_Target_Price)
                WHERE  Securities_Id = %s
            """, sec_industry_updates)

        if div_updates:
            cur.executemany("""
                UPDATE Securities
                SET    Dividend_Yield      = COALESCE(%s, Dividend_Yield),
                       Dividend_Rate       = COALESCE(%s, Dividend_Rate),
                       Five_Year_Avg_Yield = COALESCE(%s, Five_Year_Avg_Yield),
                       Payout_Ratio        = COALESCE(%s, Payout_Ratio),
                       Ex_Dividend_Date    = COALESCE(%s, Ex_Dividend_Date),
                       Dividend_Pay_Date   = COALESCE(%s, Dividend_Pay_Date)
                WHERE  Securities_Id = %s
            """, div_updates)

        # ISIN: dedicated pass — only fills NULL/empty slots, never overwrites
        # a manually entered ISIN with Yahoo's value.
        if isin_updates:
            cur.executemany("""
                UPDATE Securities
                SET    ISIN = %s
                WHERE  Securities_Id = %s
                  AND  (ISIN IS NULL OR ISIN = '')
            """, isin_updates)
            print(f"  ISIN: {len(isin_updates)} securities with Yahoo ISIN "
                  f"(only NULL/empty slots written).")

        if quote_updates:
            execute_batch(cur, """
                INSERT INTO Securities_Quote
                    (Securities_Id, Prev_Close, Day_Open, Day_High, Day_Low,
                     Week52_High, Week52_Low, Volume, Avg_Volume, Trailing_PE, Market_Cap,
                     Quote_Updated_At)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (Securities_Id) DO UPDATE SET
                    Prev_Close = EXCLUDED.Prev_Close, Day_Open = EXCLUDED.Day_Open,
                    Day_High = EXCLUDED.Day_High, Day_Low = EXCLUDED.Day_Low,
                    Week52_High = EXCLUDED.Week52_High, Week52_Low = EXCLUDED.Week52_Low,
                    Volume = EXCLUDED.Volume, Avg_Volume = EXCLUDED.Avg_Volume,
                    Trailing_PE = EXCLUDED.Trailing_PE, Market_Cap = EXCLUDED.Market_Cap,
                    Quote_Updated_At = NOW()
            """, quote_updates, page_size=500)

        if price_scale_updates:
            cur.executemany("""
                UPDATE Securities SET Price_Scale = %s WHERE Securities_Id = %s
            """, price_scale_updates)
            print(f"  Price_Scale: {len(price_scale_updates)} securities quoted in a "
                  f"minor currency unit (e.g. GBp/GBX pence) — scale factor recorded.")

        if annual_eps_rows:
            execute_batch(cur, """
                INSERT INTO Securities_Annual_EPS (Securities_Id, Fiscal_Year_End, Diluted_EPS)
                VALUES (%s, %s, %s)
                ON CONFLICT (Securities_Id, Fiscal_Year_End) DO UPDATE SET
                    Diluted_EPS = EXCLUDED.Diluted_EPS
            """, annual_eps_rows, page_size=500)

        fair_value_count = _compute_fair_values(cur, eps_updates)

        conn.commit()
        print(f"Yahoo info update complete — "
              f"{len(sec_industry_updates)} sector/industry, "
              f"{sum(1 for r in div_updates)} dividend fields, "
              f"{len(isin_updates)} ISIN(s), "
              f"{len(price_scale_updates)} Price_Scale, "
              f"{fair_value_count} Fair Value updated "
              f"(out of {total} securities).")
        logging.info(f"Yahoo info update complete — {len(sec_industry_updates)} "
                     f"sector/industry, dividend fields for {len(div_updates)} securities, "
                     f"{len(isin_updates)} ISIN(s), {fair_value_count} Fair Value written.")

    except Exception as e:
        logging.error(f"❌ Error: {e}")
        logging.error(f"Error: {e}")
    finally:
        cur.close()
        conn.close()


def download_dividend_history(target_sec_id=None):
    """Download full historical dividend records from Yahoo Finance.

    Populates the Securities_Dividends table (one row per ex-date per
    security) and back-fills Dividend_Frequency on the Securities row by
    analysing how many payments occurred in the most recent full calendar
    year.

    This is kept separate from download_securities_info_from_yahoo because
    ticker.dividends is a heavier API call (returns a time series, not just
    a scalar) and is not needed as frequently.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    MAX_WORKERS = 5

    conn = get_connection()
    cur  = conn.cursor()
    custom_session = get_custom_session()

    def _infer_frequency(dividends: "pd.Series") -> "str | None":
        """Return frequency label from a yfinance dividends Series.

        Strategy:
        1. Try the most recent *completed* calendar year (i.e. not the current
           partial year) — most reliable signal.
        2. If no completed year has data, fall back to a trailing-12-month
           window anchored to the latest ex-date.
        3. As a last resort, use the current partial year.

        This avoids mis-classifying a quarterly payer as Semi-Annual simply
        because only 2 of 4 payments have occurred in the current year.
        """
        if dividends is None or dividends.empty:
            return None

        import datetime as _dt

        def _label(count: int) -> str:
            if count >= 10:
                return "Monthly"
            if count >= 4:
                return "Quarterly"
            if count >= 2:
                return "Semi-Annual"
            return "Annual"

        current_year = _dt.date.today().year

        # 1. Most recent completed calendar year with at least one payment
        completed_years = sorted(
            [yr for yr in dividends.index.year.unique() if yr < current_year],
            reverse=True,
        )
        for yr in completed_years:
            count = int((dividends.index.year == yr).sum())
            if count > 0:
                return _label(count)

        # 2. Trailing 12-month window anchored to the latest ex-date
        latest = dividends.index.max()
        cutoff = latest - pd.DateOffset(months=12)
        trailing = dividends[dividends.index > cutoff]
        if not trailing.empty:
            return _label(len(trailing))

        # 3. Current partial year as last resort
        count = int((dividends.index.year == current_year).sum())
        if count > 0:
            return _label(count)

        return None

    def _fetch(sec_id, sec_name, symbol, price_scale):
        try:
            import logging as _logging
            # yfinance logs period errors at ERROR level before raising — suppress them
            _yf_logger = _logging.getLogger("yfinance")
            _prev_level = _yf_logger.level
            _yf_logger.setLevel(_logging.CRITICAL)
            try:
                ticker = yf.Ticker(symbol, session=custom_session)
                divs   = ticker.dividends       # pandas Series, index = ex-date
            finally:
                _yf_logger.setLevel(_prev_level)
            if divs is None or divs.empty:
                return sec_id, sec_name, symbol, [], None, None
            rows = []
            skipped = 0
            price = latest_price_by_sec.get(sec_id)
            for ts, amount in divs.items():
                ex_date = ts.date() if hasattr(ts, 'date') else None
                if ex_date is None or amount <= 0:
                    continue
                # Like Historical_Prices, yfinance's dividends series for a
                # GBp/GBX (pence) security comes back in the minor unit —
                # scale it to the major unit so it lines up with the
                # already-scaled price and with Dividend_Rate (which Yahoo
                # reports in the major unit directly). Otherwise a per-share
                # amount looks ~100x too large next to the security's price.
                scaled_amount = float(amount) / price_scale
                # Yahoo's per-security dividend series isn't retroactively
                # adjusted for reverse splits the way price history is —
                # tickers that underwent a severe consolidation (e.g. Greek
                # banks recapitalized 2013-2015) report genuine pre-split
                # amounts still denominated in old-share terms, which land
                # many orders of magnitude above any plausible modern
                # per-share dividend (seen: >€40,000/share for a stock
                # trading at a few euros). A real per-share dividend can't
                # exceed the share's own trading price by much, so reject
                # anything wildly out of line with the latest known close
                # rather than storing it and re-corrupting the history on
                # every future re-download.
                if price and scaled_amount > price * 5:
                    skipped += 1
                    continue
                rows.append((sec_id, ex_date, scaled_amount))
            if skipped:
                print(f"  ⚠️ {sec_name}: skipped {skipped} implausible dividend amount(s) "
                      f"(> 5x latest price {price})")
            frequency = _infer_frequency(divs)
            return sec_id, sec_name, symbol, rows, frequency, None
        except Exception as exc:
            return sec_id, sec_name, symbol, [], None, str(exc)

    try:
        base_query = """
            SELECT Securities_Id, Securities_Name, Yahoo_Ticker, COALESCE(Price_Scale, 1)
            FROM   Securities
            WHERE  Yahoo_Ticker IS NOT NULL
              AND  Yahoo_Ticker != ''
              AND  Securities_Name NOT LIKE 'Hellenic T-Bill%'
        """
        if target_sec_id:
            base_query += f" AND Securities_Id = {int(target_sec_id)}"
        base_query += " ORDER BY Securities_Name ASC"

        cur.execute(base_query)
        securities = cur.fetchall()

        if not securities:
            logging.warning("No securities with Yahoo Ticker found.")
            return

        cur.execute("""
            SELECT DISTINCT ON (Securities_Id) Securities_Id, Close
            FROM Historical_Prices
            ORDER BY Securities_Id, Date DESC
        """)
        latest_price_by_sec = {row[0]: float(row[1]) for row in cur.fetchall() if row[1] is not None}

        total = len(securities)
        print(f"Downloading dividend history for {total} securities…")

        futures = {}
        results = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            for sec_id, sec_name, symbol, price_scale in securities:
                f = pool.submit(_fetch, sec_id, sec_name, symbol, float(price_scale or 1))
                futures[f] = sec_name
            for f in as_completed(futures):
                results.append(f.result())

        all_rows      = []   # (sec_id, ex_date, amount) for Securities_Dividends
        freq_updates  = []   # (frequency, sec_id) for Securities

        for sec_id, sec_name, symbol, rows, frequency, err in results:
            if err:
                print(f"  ⚠️ {sec_name} ({symbol}): {err}")
                logging.warning(f"Dividend history error {sec_name}: {err}")
                continue
            if not rows:
                print(f"  — {sec_name}: no dividends")
                continue
            print(f"  ✔ {sec_name}: {len(rows)} records, frequency={frequency}")
            all_rows.extend(rows)
            if frequency:
                freq_updates.append((frequency, sec_id))

        # ── Upsert dividend rows ──────────────────────────────────────────────
        if all_rows:
            execute_batch(cur, """
                INSERT INTO Securities_Dividends (Securities_Id, Ex_Date, Amount)
                VALUES (%s, %s, %s)
                ON CONFLICT (Securities_Id, Ex_Date)
                DO UPDATE SET Amount = EXCLUDED.Amount
            """, all_rows, page_size=500)

        # ── Back-fill frequency on Securities ────────────────────────────────
        if freq_updates:
            cur.executemany("""
                UPDATE Securities
                SET    Dividend_Frequency = %s
                WHERE  Securities_Id = %s
            """, freq_updates)

        conn.commit()
        print(f"Dividend history complete — "
              f"{len(all_rows)} records upserted across "
              f"{sum(1 for r in results if r[3])} securities.")
        logging.info(f"Dividend history: {len(all_rows)} rows upserted.")

    except Exception as e:
        logging.error(f"❌ Error: {e}")
        logging.error(f"Error: {e}")
    finally:
        cur.close()
        conn.close()


def download_stock_splits(target_sec_id=None):
    """Download stock split history from Yahoo Finance into corporate_actions.

    Creates plain reference records only (Split / Reverse Split, ratio_new=<Yahoo's
    ratio>, ratio_old=1 — same multiplier convention the Corporate Action Preview/
    Execute flow already uses) — it doesn't touch Investments/Holdings, same as how
    Download Dividend History populates Securities_Dividends without creating actual
    dividend transactions. To apply a downloaded split to shares you actually held
    on that date, use the Corporate Actions tab's own Split entry (Preview → Execute)
    with the same date/ratio, same as recording one manually.

    corporate_actions has no unique constraint on (securities_id, effective_date,
    action_type), so re-running this checks for an existing Split/Reverse Split row
    on the same date before inserting, to stay idempotent.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    MAX_WORKERS = 5

    conn = get_connection()
    cur  = conn.cursor()
    custom_session = get_custom_session()

    def _fetch(sec_id, sec_name, symbol):
        try:
            import logging as _logging
            _yf_logger = _logging.getLogger("yfinance")
            _prev_level = _yf_logger.level
            _yf_logger.setLevel(_logging.CRITICAL)
            try:
                ticker = yf.Ticker(symbol, session=custom_session)
                splits = ticker.splits      # pandas Series, index = effective date, value = ratio
            finally:
                _yf_logger.setLevel(_prev_level)
            if splits is None or splits.empty:
                return sec_id, sec_name, symbol, [], None
            rows = []
            for ts, ratio in splits.items():
                eff_date = ts.date() if hasattr(ts, 'date') else None
                ratio = float(ratio)
                if eff_date is None or ratio <= 0 or ratio == 1:
                    continue
                if ratio >= 1:
                    action_type, desc = 'Split', f"{ratio:g}-for-1 stock split (Yahoo Finance)"
                else:
                    action_type, desc = 'Reverse Split', f"1-for-{1/ratio:g} reverse split (Yahoo Finance)"
                rows.append((sec_id, action_type, eff_date, ratio, 1.0, desc))
            return sec_id, sec_name, symbol, rows, None
        except Exception as exc:
            return sec_id, sec_name, symbol, [], str(exc)

    try:
        base_query = """
            SELECT Securities_Id, Securities_Name, Yahoo_Ticker
            FROM   Securities
            WHERE  Yahoo_Ticker IS NOT NULL
              AND  Yahoo_Ticker != ''
              AND  Securities_Name NOT LIKE 'Hellenic T-Bill%'
        """
        if target_sec_id:
            base_query += f" AND Securities_Id = {int(target_sec_id)}"
        base_query += " ORDER BY Securities_Name ASC"

        cur.execute(base_query)
        securities = cur.fetchall()

        if not securities:
            logging.warning("No securities with Yahoo Ticker found.")
            return

        total = len(securities)
        print(f"Downloading stock split history for {total} securities…")

        results = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(_fetch, sid, sname, sym): sname for sid, sname, sym in securities}
            for f in as_completed(futures):
                results.append(f.result())

        all_rows = []   # (sec_id, action_type, eff_date, ratio_new, ratio_old, description)
        for sec_id, sec_name, symbol, rows, err in results:
            if err:
                print(f"  ⚠️ {sec_name} ({symbol}): {err}")
                logging.warning(f"Stock split history error {sec_name}: {err}")
                continue
            if rows:
                print(f"  ✔ {sec_name}: {len(rows)} split(s)")
                all_rows.extend(rows)

        if not all_rows:
            print("Stock split history complete — no splits found.")
            return

        sec_ids = list({r[0] for r in all_rows})
        cur.execute(
            "SELECT securities_id, effective_date, action_type FROM corporate_actions "
            "WHERE securities_id = ANY(%s) AND action_type IN ('Split','Reverse Split')",
            (sec_ids,),
        )
        existing = {(r[0], r[1], r[2]) for r in cur.fetchall()}
        new_rows = [r for r in all_rows if (r[0], r[2], r[1]) not in existing]

        new_ca_ids = []
        if new_rows:
            for row in new_rows:
                cur.execute("""
                    INSERT INTO corporate_actions
                        (securities_id, action_type, effective_date, ratio_new, ratio_old, description)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING corporate_actions_id
                """, row)
                new_ca_ids.append(cur.fetchone()[0])
            conn.commit()

            from database.queries import record_new_split_notifications
            record_new_split_notifications(new_ca_ids)

        skipped = len(all_rows) - len(new_rows)
        print(f"Stock split history complete — {len(new_rows)} new record(s) inserted"
              f"{f', {skipped} already on record' if skipped else ''}.")
        logging.info(f"Stock splits: {len(new_rows)} rows inserted.")

    except Exception as e:
        logging.error(f"❌ Error: {e}")
    finally:
        cur.close()
        conn.close()


_FUND_COMPOSITION_COLUMNS = [
    "Asset_Cash_Pct", "Asset_Stock_Pct", "Asset_Bond_Pct", "Asset_Preferred_Pct",
    "Asset_Convertible_Pct", "Asset_Other_Pct", "Sector_Weightings", "Category_Name",
    "Fund_Family", "Legal_Type", "Expense_Ratio_Pct", "Category_Avg_Expense_Ratio_Pct",
    "Total_Net_Assets", "Holdings_Turnover_Pct", "Bond_Ratings", "Bond_Duration",
    "Bond_Maturity", "Equity_PE", "Equity_PB", "Equity_PS", "Equity_PCF",
    "Equity_Median_Market_Cap", "Equity_3yr_Earnings_Growth_Pct",
]


def download_fund_composition(target_sec_id=None):
    """Download ETF/Mutual Fund look-through composition from Yahoo Finance.

    Populates Fund_Composition (one row per fund: asset mix, sector weights,
    Morningstar-style category, expense ratio, bond quality/duration, equity
    valuation stats) and Fund_Top_Holdings (up to 10 constituent tickers per
    fund) via yfinance's Ticker.get_funds_data(). Powers the Portfolio X-Ray
    report — this is the fund side of that blend; direct stock/bond holdings
    need no cached data since their own Securities row already has what's needed.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    MAX_WORKERS = 5

    conn = get_connection()
    cur  = conn.cursor()
    custom_session = get_custom_session()

    def _df_val(df, row_label, own_col=True):
        """Best-effort lookup into a get_funds_data() stats DataFrame.

        These DataFrames are indexed by attribute name with two columns: the
        fund's own ticker symbol (varies per call, so accessed positionally)
        and 'Category Average'. Any missing row/column/NA yields None rather
        than raising, since coverage is inherently spotty for many funds.
        """
        try:
            if df is None or row_label not in df.index:
                return None
            col = df.columns[0] if own_col else "Category Average"
            if col not in df.columns:
                return None
            val = df.loc[row_label, col]
            if val is None or pd.isna(val):
                return None
            return float(val)
        except Exception:
            return None

    def _fetch(sec_id, sec_name, symbol):
        try:
            import logging as _logging
            _yf_logger = _logging.getLogger("yfinance")
            _prev_level = _yf_logger.level
            _yf_logger.setLevel(_logging.CRITICAL)
            try:
                fd = yf.Ticker(symbol, session=custom_session).get_funds_data()
            finally:
                _yf_logger.setLevel(_prev_level)
            if fd is None:
                return sec_id, sec_name, symbol, None, [], "get_funds_data() returned no data"

            def _safe(fn):
                try:
                    return fn()
                except Exception:
                    return None

            asset_classes = _safe(lambda: fd.asset_classes) or {}
            sector_w      = _safe(lambda: fd.sector_weightings) or {}
            overview      = _safe(lambda: fd.fund_overview) or {}
            ops_df        = _safe(lambda: fd.fund_operations)
            bond_ratings  = _safe(lambda: fd.bond_ratings) or {}
            bond_h_df     = _safe(lambda: fd.bond_holdings)
            eq_h_df       = _safe(lambda: fd.equity_holdings)
            top_df        = _safe(lambda: fd.top_holdings)

            composition = {
                "Asset_Cash_Pct":        asset_classes.get("cashPosition"),
                "Asset_Stock_Pct":       asset_classes.get("stockPosition"),
                "Asset_Bond_Pct":        asset_classes.get("bondPosition"),
                "Asset_Preferred_Pct":   asset_classes.get("preferredPosition"),
                "Asset_Convertible_Pct": asset_classes.get("convertiblePosition"),
                "Asset_Other_Pct":       asset_classes.get("otherPosition"),
                "Sector_Weightings":     Json(sector_w) if sector_w else None,
                "Category_Name":         overview.get("categoryName"),
                "Fund_Family":           overview.get("family"),
                "Legal_Type":            overview.get("legalType"),
                "Expense_Ratio_Pct":         _df_val(ops_df, "Annual Report Expense Ratio", own_col=True),
                "Category_Avg_Expense_Ratio_Pct": _df_val(ops_df, "Annual Report Expense Ratio", own_col=False),
                "Total_Net_Assets":          _df_val(ops_df, "Total Net Assets", own_col=True),
                "Holdings_Turnover_Pct":     _df_val(ops_df, "Annual Holdings Turnover", own_col=True),
                "Bond_Ratings":          Json(bond_ratings) if bond_ratings else None,
                "Bond_Duration":         _df_val(bond_h_df, "Duration", own_col=True),
                "Bond_Maturity":         _df_val(bond_h_df, "Maturity", own_col=True),
                "Equity_PE":             _df_val(eq_h_df, "Price/Earnings", own_col=True),
                "Equity_PB":             _df_val(eq_h_df, "Price/Book", own_col=True),
                "Equity_PS":             _df_val(eq_h_df, "Price/Sales", own_col=True),
                "Equity_PCF":            _df_val(eq_h_df, "Price/Cashflow", own_col=True),
                "Equity_Median_Market_Cap":       _df_val(eq_h_df, "Median Market Cap", own_col=True),
                "Equity_3yr_Earnings_Growth_Pct": _df_val(eq_h_df, "3 Year Earnings Growth", own_col=True),
            }
            top_rows = []
            if top_df is not None and not top_df.empty:
                for rank, (tkr, row) in enumerate(top_df.iterrows(), start=1):
                    weight = row.get("Holding Percent")
                    if weight is None or pd.isna(weight):
                        continue
                    top_rows.append((sec_id, rank, str(tkr), row.get("Name"), float(weight)))
            return sec_id, sec_name, symbol, composition, top_rows, None
        except Exception as exc:
            return sec_id, sec_name, symbol, None, [], str(exc)

    try:
        base_query = """
            SELECT Securities_Id, Securities_Name, Yahoo_Ticker
            FROM   Securities
            WHERE  Yahoo_Ticker IS NOT NULL AND Yahoo_Ticker != ''
              AND  Securities_Type IN ('ETF', 'Mutual Fund')
        """
        if target_sec_id:
            base_query += f" AND Securities_Id = {int(target_sec_id)}"
        base_query += " ORDER BY Securities_Name ASC"
        cur.execute(base_query)
        funds = cur.fetchall()

        if not funds:
            logging.warning("No ETF/Mutual Fund securities with Yahoo Ticker found.")
            return

        total = len(funds)
        print(f"Downloading fund composition for {total} fund(s)…")

        results = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(_fetch, sec_id, sec_name, symbol): sec_name
                       for sec_id, sec_name, symbol in funds}
            for f in as_completed(futures):
                results.append(f.result())

        comp_rows, holdings_by_sec, error_rows = [], {}, []

        for sec_id, sec_name, symbol, comp, top_rows, err in results:
            if err or comp is None:
                print(f"  ⚠️ {sec_name} ({symbol}): {err}")
                error_rows.append((sec_id, err))
                continue
            # get_funds_data() can return a real (non-None, no-exception) object
            # for a ticker Yahoo simply has no fund data for — e.g. a niche
            # domestic ETF — with every field empty rather than raising. Treated
            # as a normal success, this used to blindly upsert Fund_Composition
            # with all-NULL values and unconditionally DELETE Fund_Top_Holdings
            # (nothing to re-insert, since top_rows is also empty) — silently
            # wiping any previously-fetched *or manually-curated* composition
            # data instead of leaving it alone. Treat "nothing came back" the
            # same as a fetch failure: skip the write, keep whatever's there.
            if all(comp[c] is None for c in _FUND_COMPOSITION_COLUMNS) and not top_rows:
                print(f"  ⚠️ {sec_name} ({symbol}): Yahoo returned no fund data — leaving existing composition untouched")
                error_rows.append((sec_id, "Yahoo returned no fund data"))
                continue
            print(f"  ✔ {sec_name}: composition cached ({len(top_rows)} top holdings)")
            comp_rows.append((sec_id, *[comp[c] for c in _FUND_COMPOSITION_COLUMNS]))
            holdings_by_sec[sec_id] = top_rows

        if comp_rows:
            set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in _FUND_COMPOSITION_COLUMNS)
            execute_batch(cur, f"""
                INSERT INTO Fund_Composition (Securities_Id, {", ".join(_FUND_COMPOSITION_COLUMNS)}, Last_Updated, Fetch_Error)
                VALUES (%s, {", ".join(["%s"] * len(_FUND_COMPOSITION_COLUMNS))}, NOW(), NULL)
                ON CONFLICT (Securities_Id) DO UPDATE SET
                    {set_clause}, Last_Updated = NOW(), Fetch_Error = NULL
            """, comp_rows, page_size=500)

        for sec_id, rows in holdings_by_sec.items():
            # Scoped to Source='yahoo' so a manually added/edited row (always at Rank
            # >= 100 — see api/routers/securities.py's add/edit endpoints) survives this
            # refresh instead of being wiped by it.
            cur.execute("DELETE FROM Fund_Top_Holdings WHERE Securities_Id = %s AND Source = 'yahoo'", (sec_id,))
            if rows:
                execute_batch(cur, """
                    INSERT INTO Fund_Top_Holdings (Securities_Id, Rank, Symbol, Holding_Name, Weight_Pct, Source)
                    VALUES (%s, %s, %s, %s, %s, 'yahoo')
                """, rows, page_size=500)

        if error_rows:
            execute_batch(cur, """
                INSERT INTO Fund_Composition (Securities_Id, Last_Updated, Fetch_Error)
                VALUES (%s, NOW(), %s)
                ON CONFLICT (Securities_Id) DO UPDATE SET Fetch_Error = EXCLUDED.Fetch_Error, Last_Updated = NOW()
            """, error_rows, page_size=500)

        conn.commit()
        print(f"Fund composition complete — {len(comp_rows)} fund(s) cached, {len(error_rows)} error(s).")
        logging.info(f"Fund composition: {len(comp_rows)} funds cached, {len(error_rows)} errors.")

    except Exception as e:
        logging.error(f"❌ Error: {e}")
    finally:
        cur.close()
        conn.close()


def download_historical_prices_from_yahoo(tsperiod=None, target_sec_id=None):
    """Download historical security prices from Yahoo Finance.

    Requests are made in parallel (up to MAX_WORKERS concurrent threads) to
    minimise wall-clock time.  All rows are collected in memory first, then
    written to the DB in a single execute_batch + commit so the connection is
    never held open across slow network calls.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    MAX_WORKERS = 5   # conservative — avoids Yahoo rate-limiting

    conn = get_connection()
    cur  = conn.cursor()
    custom_session = get_custom_session()

    if not tsperiod:
        tsperiod = "1m"

    def _fetch(sec_id, sec_name, symbol, price_scale):
        """Fetch OHLCV history for one ticker; returns (sec_id, sec_name, symbol, rows, error).

        price_scale normalizes securities Yahoo quotes in a minor currency unit
        (e.g. LSE ordinary shares in GBp/GBX pence, 100 to the pound) back to
        the major unit — see Securities.Price_Scale, set by
        download_securities_info_from_yahoo. Volume is a share count, not a
        price, so it's left unscaled.
        """
        try:
            hist = yf.Ticker(symbol, session=custom_session).history(period=tsperiod)
            if hist is None or hist.empty:
                return sec_id, sec_name, symbol, [], None
            scale = float(price_scale) if price_scale else 1.0
            rows = []
            for date, row in hist.iterrows():
                if 'Close' not in row or pd.isna(row['Close']):
                    continue
                rows.append((
                    int(sec_id),
                    date.strftime('%Y-%m-%d'),
                    float(row['Close']) / scale,
                    (float(row['High']) / scale) if 'High' in row and not pd.isna(row['High']) else None,
                    (float(row['Low'])  / scale) if 'Low'  in row and not pd.isna(row['Low'])  else None,
                    float(row['Volume']) if 'Volume' in row and not pd.isna(row['Volume']) else 0,
                ))
            return sec_id, sec_name, symbol, rows, None
        except Exception as exc:
            return sec_id, sec_name, symbol, [], str(exc)

    try:
        base_query = """
            SELECT Securities_Id, Securities_Name, Yahoo_Ticker, COALESCE(Price_Scale, 1)
            FROM   Securities
            WHERE  Yahoo_Ticker IS NOT NULL
              AND  Yahoo_Ticker != ''
              AND  Securities_Name NOT LIKE 'Hellenic T-Bill%'
        """
        if target_sec_id:
            base_query += f" AND Securities_Id = {target_sec_id}"
        base_query += " ORDER BY Securities_Name ASC"

        cur.execute(base_query)
        securities = cur.fetchall()

        if not securities:
            logging.warning("No matching securities found with a valid Yahoo Ticker.")
            return

        total = len(securities)
        logging.info(f"Fetching Yahoo prices for {total} securities (up to {MAX_WORKERS} in parallel)…")
        print(f"Fetching Yahoo prices for {total} securities (up to {MAX_WORKERS} in parallel)…")

        # ── Parallel fetch ────────────────────────────────────────────────────
        all_rows = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(_fetch, sid, sname, sym, scale): sname
                       for sid, sname, sym, scale in securities}
            for f in as_completed(futures):
                sec_id, sec_name, symbol, rows, err = f.result()
                if err:
                    logging.warning(f"Price fetch error for {sec_name} ({symbol}): {err}")
                    print(f"  ⚠️ Error fetching {sec_name} ({symbol}): {err}")
                elif not rows:
                    logging.warning(f"No data for {sec_name} ({symbol})")
                    print(f"  ⚠️ No data for {sec_name} ({symbol})")
                else:
                    all_rows.extend(rows)
                    logging.info(f"  ✔ {sec_name}: {len(rows)} rows")
                    print(f"  ✔ {sec_name}: {len(rows)} rows")

        # ── Single batch upsert ───────────────────────────────────────────────
        if all_rows:
            execute_batch(cur, """
                INSERT INTO Historical_Prices (Securities_Id, Date, Close, High, Low, Volume, Source, Downloaded_At)
                VALUES (%s, %s, %s, %s, %s, %s, 'Yahoo Finance', NOW())
                ON CONFLICT (Securities_Id, Date)
                DO UPDATE SET
                    Close         = EXCLUDED.Close,
                    High          = EXCLUDED.High,
                    Low           = EXCLUDED.Low,
                    Volume        = EXCLUDED.Volume,
                    Source        = EXCLUDED.Source,
                    Downloaded_At = EXCLUDED.Downloaded_At
            """, all_rows, page_size=500)
            conn.commit()

        logging.info(
            f"Yahoo price download complete — {len(all_rows)} rows upserted "
            f"for {total} securities."
        )
        print(
            f"Yahoo price download complete — {len(all_rows)} rows upserted "
            f"for {total} securities."
        )

    except Exception as e:
        conn.rollback()
        logging.error(f"❌ Error: {e}")
        logging.error(f"Error: {e}")
    finally:
        cur.close()
        conn.close()

    _refresh_materialized_views_async()


# ======================================================
# MATERIALIZED VIEW REFRESH
# ======================================================

def refresh_materialized_views():
    """Refresh mv_latest_prices and mv_latest_fx after price/FX downloads."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT refresh_mv_prices_fx();")
        conn.commit()
        logging.info("Materialized views refreshed.")
    except Exception as e:
        logging.warning(f"MV refresh skipped (views may not exist yet): {e}")
    finally:
        conn.close()


def _refresh_materialized_views_async():
    """Fire-and-forget wrapper — runs refresh_materialized_views() in a
    background daemon thread so the calling download function can return
    immediately without blocking the Streamlit UI."""
    import threading
    threading.Thread(target=refresh_materialized_views, daemon=True).start()


# ======================================================
# DATE RANGE HELPER
# ======================================================

def get_smart_date_range(time_input="1mo"):
    """
    Converts Yahoo Finance / EODHD style period strings to a date range.
        15d  = 15 days
        1w   = 1 week
        1mo  = 1 month   (also accepts legacy '1m')
        3mo  = 3 months  (also accepts legacy '3m')
        1y   = 1 year
    Returns:
        from_date, to_date (YYYY-MM-DD)
    """
 
    to_date_obj = datetime.today()
 
    if not time_input:
        time_input = "1mo"
 
    s = str(time_input).lower().strip()
 
    # Normalise legacy short-month format: '1m' → '1mo', '3m' → '3mo'
    # (but don't touch 'min' or other non-month uses of 'm')
    s = re.sub(r'^(\d+)m$', r'mo', s)
 
    # Accept: 15d, 1w, 1mo, 3mo, 1y
    match = re.match(r"^(\d+)(d|w|mo|y)$", s)
 
    if not match:
        logging.warning(f"Invalid period format {time_input!r}, defaulting to 1mo")
        match = re.match(r"^(\d+)(d|w|mo|y)$", "1mo")
 
    value = int(match.group(1))
    unit = match.group(2)
 
    multiplier = {
        "d":  1,
        "w":  7,
        "mo": 30,
        "y":  365
    }
 
    days = value * multiplier[unit]
 
    from_date_obj = to_date_obj - timedelta(days=days)
 
    return (
        from_date_obj.strftime("%Y-%m-%d"),
        to_date_obj.strftime("%Y-%m-%d")
    )
 

# ======================================================
# TRADINGVIEW DOWNLOAD
# ======================================================

def _period_to_n_bars(tsperiod: str) -> int:
    s = str(tsperiod).lower().strip()
    if s in ("max", "all"):
        return 13000  # ~50 years; capped at 1970+ to avoid Windows timestamp overflow
    s = re.sub(r"^(\d+)m$", r"\1mo", s)
    match = re.match(r"^(\d+)(d|w|mo|y)$", s)
    if not match:
        return 40
    value = int(match.group(1))
    unit = match.group(2)
    multiplier = {"d": 1, "w": 5, "mo": 22, "y": 250}
    return value * multiplier[unit] + 10


class _PersistentTvDatafeed(TvDatafeed):
    """TvDatafeed variant that keeps the WebSocket alive across get_hist() calls.

    The standard TvDatafeed.get_hist() calls __create_connection() on every
    invocation, paying a full TCP/TLS handshake per security.  This subclass
    opens the connection once via _connect() and reuses it, generating a fresh
    chart_session per symbol.

    Issues fixed vs the naive persistent approach:
    - Quote session removed: quote_create_session / quote_add_symbols enable
      streaming tick updates that pile up in the socket buffer after each
      request returns, causing the next call to read stale data and the socket
      to appear "already closed".
    - Socket drain: after series_completed we consume any residual frames
      (du, acks, etc.) with a 150 ms window so the next request starts clean.
    - Chart-session filtering: even after the drain, late-arriving du frames
      from a previous session can slip in at the start of the next recv loop.
      We filter raw_data to lines that belong to this chart_session before
      parsing, eliminating data bleed entirely.
    - Reconnect stagger: when a reconnect is needed mid-batch the shared
      connect_lock (passed in at construction time) is acquired so no two
      threads hammer TradingView simultaneously and trigger HTTP 429.
    """

    import re as _re
    # Matches any chart-session token (cs_xxxxxxxxxxxxxxxx) that is NOT ours.
    # Used to filter stale frames out of raw_data before parsing.
    _CS_RE = _re.compile(r'\bcs_[a-zA-Z0-9]+\b')

    # TradingView frames that signal the end of a series request (success or error).
    # Breaking on error frames avoids waiting for the 5 s socket timeout when
    # TradingView has no data for a symbol but keeps the connection alive.
    _TERMINAL_FRAMES = ("series_completed", "series_error", "symbol_error", "critical_error")

    # How long (seconds) to wait for each ws.recv() call.
    # Kept below the library default (5 s) so "no-data" stalls are shorter.
    _WS_TIMEOUT = 2

    def __init__(self, *args, connect_lock=None, **kwargs):
        """Initialise TvDatafeed with an optional shared connect_lock.

        connect_lock : threading.Lock (or RLock), optional
            When provided, both initial _connect() calls (via _get_tv()) and
            reconnect _connect() calls (via get_hist()) acquire this lock so
            that no more than one thread opens a new WebSocket at a time.
            This prevents HTTP 429 errors when multiple workers try to
            (re)connect simultaneously.
        """
        super().__init__(*args, **kwargs)
        self._connect_lock = connect_lock

    def _connect(self):
        """Open the WebSocket and authenticate.  No quote session — not needed for OHLCV.

        Does NOT acquire _connect_lock — the caller is responsible for holding
        the lock before calling this method (see _get_tv() and _reconnect()).
        """
        self._TvDatafeed__create_connection()
        try:
            self.ws.settimeout(self._WS_TIMEOUT)
        except Exception:
            pass
        self._TvDatafeed__send_message("set_auth_token", [self.token])

    def _reconnect(self):
        """Reconnect, acquiring the shared connect_lock if one was provided.

        Called from get_hist() when a mid-batch WS error is detected.
        Serialises reconnects across all worker threads so TradingView does
        not see a burst of simultaneous new connections (HTTP 429).
        """
        if self._connect_lock is not None:
            with self._connect_lock:
                time.sleep(0.5)   # same stagger as initial connection
                self._connect()
        else:
            self._connect()

    def get_hist(
        self,
        symbol: str,
        exchange: str = "NSE",
        interval: Interval = Interval.in_daily,
        n_bars: int = 10,
        fut_contract: int = None,
        extended_session: bool = False,
    ) -> pd.DataFrame:
        symbol_full = self._TvDatafeed__format_symbol(symbol, exchange, fut_contract)
        interval_val = interval.value
        session_str  = '"regular"' if not extended_session else '"extended"'

        for attempt in range(2):
            try:
                return self._request_hist(symbol_full, interval_val, n_bars, session_str)
            except Exception as exc:
                if attempt == 0:
                    logging.warning(
                        f"TV WS error on {symbol_full} (attempt 1): {exc} — reconnecting…"
                    )
                    try:
                        self._reconnect()   # uses connect_lock + stagger
                    except Exception as re_exc:
                        raise RuntimeError(
                            f"TV reconnect failed for {symbol_full}: {re_exc}"
                        ) from re_exc
                else:
                    raise

    def _request_hist(
        self, symbol_full: str, interval_val: str, n_bars: int, session_str: str
    ) -> pd.DataFrame:
        """Send a history request on the already-open WS and return a DataFrame."""
        chart_session = self._TvDatafeed__generate_chart_session()
        send = self._TvDatafeed__send_message   # alias for brevity

        send("chart_create_session", [chart_session, ""])
        send("resolve_symbol", [
            chart_session, "symbol_1",
            f'={{"symbol":"{symbol_full}","adjustment":"splits",'
            f'"session":{session_str}}}',
        ])
        send("create_series",   [chart_session, "s1", "s1", "symbol_1",
                                  interval_val, n_bars])
        send("switch_timezone", [chart_session, "exchange"])

        raw_data = ""
        ws_broke = False
        while True:
            try:
                result = self.ws.recv()
                raw_data += result + "\n"
            except Exception:
                ws_broke = True
                break

            if any(frame in result for frame in self._TERMINAL_FRAMES):
                # Always delete the chart session and drain residual frames,
                # regardless of whether the terminal frame is success or error.
                # Without this, stale error-session frames (symbol_error, etc.)
                # bleed into the NEXT security's recv loop on the same WS,
                # corrupting its raw_data and causing __create_df failures.
                try:
                    send("chart_delete_session", [chart_session])
                except Exception:
                    pass
                try:
                    self.ws.settimeout(0.15)
                    while True:
                        try:
                            self.ws.recv()
                        except Exception:
                            break
                finally:
                    try:
                        self.ws.settimeout(self._WS_TIMEOUT)
                    except Exception:
                        pass
                break

        # If recv() broke mid-stream (timeout / connection drop) before a
        # terminal frame arrived, the chart session is still open on TV's side.
        # Delete it and drain so the next get_hist() on this WS starts clean.
        if ws_broke:
            try:
                send("chart_delete_session", [chart_session])
            except Exception:
                pass
            try:
                self.ws.settimeout(0.15)
                while True:
                    try:
                        self.ws.recv()
                    except Exception:
                        break
            finally:
                try:
                    self.ws.settimeout(self._WS_TIMEOUT)
                except Exception:
                    pass

        # ── Chart-session filtering ───────────────────────────────────────────
        # Despite chart_delete_session + the drain window, a late-arriving du
        # frame from a *previous* session can still slip through and appear at
        # the top of raw_data for this security (its chart_session token is
        # different from ours).  Strip any line that contains a foreign
        # cs_xxxxxxxxxxxxxxxx token to eliminate bleed unconditionally.
        filtered_lines = [
            line for line in raw_data.split('\n')
            if chart_session in line                  # belongs to our session
            or not self._CS_RE.search(line)           # general protocol frame (no cs_ at all)
        ]
        raw_data = '\n'.join(filtered_lines)

        return self._TvDatafeed__create_df(raw_data, symbol_full)


def _tv_recommend_to_rating(value):
    """Convert TradingView Recommend.All (-1 … +1) to a rating label.

    NOTE: Recommend.All is a TECHNICAL indicator composite (MAs, RSI, MACD…),
    NOT broker analyst consensus.  It is NOT written to Analyst_Rating — kept
    here in case a future 'Technical_Rating' column is added.

    Returns None (→ SQL NULL) when the value is absent, non-numeric, or a
    sentinel string ('none', 'n/a', 'nan') so we never write the literal
    string "none" into the database.
    """
    if value is None:
        return None
    # Guard against sentinel strings
    if isinstance(value, str) and value.strip().lower() in ('none', 'n/a', 'nan', ''):
        return None
    try:
        import math
        v = float(value)
        if math.isnan(v):
            return None
        if v >= 0.5:    return 'strong_buy'
        elif v >= 0.1:  return 'buy'
        elif v > -0.1:  return 'hold'
        elif v > -0.5:  return 'underperform'
        else:           return 'sell'
    except (ValueError, TypeError):
        return None


def download_securities_info_from_tradingview(target_sec_id=None, overwrite=False):
    """Fetch Sector, Industry, Analyst Rating and Target Price from TradingView Screener.

    Acts as a fallback for securities that Yahoo Finance does not cover (e.g. ATHEX
    stocks).  By default only fills NULL columns; pass overwrite=True to refresh all.

    Securities must have TV_Symbol and TV_Exchange populated.
    The Recommend.All field (-1 … +1) is mapped to the same rating strings used by
    the Yahoo Finance downloader (strong_buy / buy / hold / underperform / sell).
    """
    try:
        from tradingview_screener import Query, Column
    except ImportError:
        print("tradingview-screener not installed. Run: pip install tradingview-screener")
        return

    conn = get_connection()
    cur  = conn.cursor()

    try:
        sql = """
            SELECT Securities_Id, Securities_Name, TV_Symbol, TV_Exchange
            FROM   Securities
            WHERE  TV_Symbol   IS NOT NULL AND TV_Symbol   != ''
              AND  TV_Exchange IS NOT NULL AND TV_Exchange != ''
        """
        params = []

        if not overwrite:
            # Analyst_Rating is intentionally excluded: TradingView's screener API
            # only exposes technical ratings (Recommend.All), not broker consensus.
            # Analyst_Rating is populated exclusively by Yahoo Finance.
            sql += " AND (Sector IS NULL OR Industry IS NULL OR Analyst_Target_Price IS NULL)"

        if target_sec_id:
            sql += " AND Securities_Id = %s"
            params.append(int(target_sec_id))

        sql += " ORDER BY Securities_Name"
        cur.execute(sql, params)
        rows = cur.fetchall()

        if not rows:
            print("No securities require TradingView info update.")
            logging.info("No securities require TradingView info update.")
            return

        print(f"Fetching TradingView screener data for {len(rows)} securities...")
        logging.info(f"Fetching TradingView screener data for {len(rows)} securities...")

        # Build lookup: "TV_EXCHANGE:TV_SYMBOL" (upper) → (sec_id, sec_name)
        # Using full ticker ensures we match the exact security, not a same-named
        # symbol on a different exchange (e.g. ATHEX:GD ≠ NYSE:GD).
        sec_map = {
            f"{r[3].upper()}:{r[2].upper()}": (r[0], r[1])
            for r in rows
        }
        full_tickers = list(sec_map.keys())

        BATCH = 50
        updated = 0

        for i in range(0, len(full_tickers), BATCH):
            batch = full_tickers[i : i + BATCH]
            try:
                _count, df = (
                    Query()
                    .select('name', 'sector', 'industry', 'price_target_average')
                    .set_tickers(*batch)
                    .get_scanner_data()
                )
            except Exception as e:
                print(f"  Screener query error for batch {i//BATCH + 1}: {e}")
                logging.warning(f"TV screener batch error: {e}")
                continue

            for _, row in df.iterrows():
                # 'ticker' column is always returned as EXCHANGE:SYMBOL
                full_ticker = str(row.get('ticker', '')).upper()
                if full_ticker not in sec_map:
                    continue

                sec_id, sec_name = sec_map[full_ticker]
                sector       = row.get('sector')              or None
                industry     = row.get('industry')            or None
                target_price = row.get('price_target_average') or None

                print(f"  {sec_name}: sector={sector}, industry={industry}, "
                      f"target={target_price}")
                logging.info(f"TV screener {sec_name}: sector={sector}, "
                             f"industry={industry}, target={target_price}")

                if overwrite:
                    cur.execute("""
                        UPDATE Securities
                        SET    Sector               = COALESCE(%s, Sector),
                               Industry             = COALESCE(%s, Industry),
                               Analyst_Target_Price = COALESCE(%s, Analyst_Target_Price)
                        WHERE  Securities_Id = %s
                    """, (sector, industry, target_price, sec_id))
                else:
                    # Only fill genuinely empty columns
                    cur.execute("""
                        UPDATE Securities
                        SET    Sector               = COALESCE(Sector, %s),
                               Industry             = COALESCE(Industry, %s),
                               Analyst_Target_Price = COALESCE(Analyst_Target_Price, %s)
                        WHERE  Securities_Id = %s
                    """, (sector, industry, target_price, sec_id))

                updated += 1

            conn.commit()

        print(f"TradingView screener update complete — {updated} securities updated.")
        logging.info(f"TradingView screener update complete — {updated} updated.")

    except Exception as e:
        print(f"Error in download_securities_info_from_tradingview: {e}")
        logging.error(f"TV screener error: {e}")
    finally:
        cur.close()
        conn.close()


# Maps TradingView exchange codes → EODHD exchange suffix
_TV_TO_EODHD_EXCH: dict[str, str] = {
    "NASDAQ":   "US",
    "NYSE":     "US",
    "AMEX":     "US",
    "ATHEX":    "AT",
    "BME":      "MC",
    "XETR":     "XETRA",
    "FWB":      "F",
    "TSX":      "TO",
    "VIE":      "VI",
    "EURONEXT": "PA",
    "LSE":      "LSE",
    "SWB":      "SG",
}


def download_isin_from_eodhd(target_sec_id=None):
    """Fetch ISIN from EODHD Fundamentals API for securities missing it.

    Uses the already-configured EODHD API key.  For each security it first
    calls the fundamentals endpoint with filter=General::ISIN; if that returns
    nothing (wrong exchange suffix, unlisted security) it falls back to the
    EODHD search endpoint and matches by ticker symbol.

    Only fills NULL/empty ISIN slots — never overwrites an existing value.
    """
    from config.settings import ENV_CONFIG
    api_key = ENV_CONFIG.get('eodhd_api_key', '')
    if not api_key:
        print("EODHD: no API key configured — aborting ISIN lookup.")
        return

    conn = get_connection()
    cur  = conn.cursor()
    try:
        sql = """
            SELECT Securities_Id, Securities_Name,
                   COALESCE(NULLIF(Yahoo_Ticker,''), NULLIF(TV_Symbol,'')) AS ticker,
                   TV_Exchange
            FROM   Securities
            WHERE  COALESCE(NULLIF(Yahoo_Ticker,''), NULLIF(TV_Symbol,'')) IS NOT NULL
              AND  (ISIN IS NULL OR ISIN = '')
        """
        params = []
        if target_sec_id:
            sql += " AND Securities_Id = %s"
            params.append(int(target_sec_id))
        sql += " ORDER BY Securities_Name"
        cur.execute(sql, params)
        rows = cur.fetchall()

        if not rows:
            print("EODHD: no securities need ISIN lookup.")
            logging.info("EODHD: no securities need ISIN lookup.")
            return

        print(f"EODHD: looking up ISIN for {len(rows)} securities…")
        logging.info(f"EODHD: ISIN lookup for {len(rows)} securities")

        isin_updates = []

        for sec_id, sec_name, ticker, tv_exchange in rows:
            eodhd_exch = _TV_TO_EODHD_EXCH.get(tv_exchange or '', 'US')
            isin = None

            # Primary: fundamentals endpoint with exchange suffix
            try:
                resp = requests.get(
                    f"https://eodhd.com/api/fundamentals/{ticker}.{eodhd_exch}",
                    params={"api_token": api_key, "filter": "General::ISIN"},
                    timeout=10,
                )
                if resp.status_code == 200:
                    raw = resp.text.strip().strip('"')
                    if raw and len(raw) == 12 and raw not in ('null', 'None', 'N/A', ''):
                        isin = raw.upper()
            except Exception as e:
                logging.debug(f"EODHD fundamentals error for {ticker}: {e}")

            # Fallback: search endpoint (handles wrong exchange suffix / delisted)
            if not isin:
                try:
                    sresp = requests.get(
                        f"https://eodhd.com/api/search/{ticker}",
                        params={"api_token": api_key, "limit": 10},
                        timeout=10,
                    )
                    if sresp.status_code == 200:
                        for item in sresp.json():
                            if item.get('Code', '').upper() == ticker.upper():
                                raw = (item.get('ISIN') or item.get('isin') or '').strip()
                                if raw and len(raw) == 12:
                                    isin = raw.upper()
                                    break
                except Exception as e:
                    logging.debug(f"EODHD search error for {ticker}: {e}")

            if isin:
                print(f"  {sec_name} ({ticker}): ISIN={isin}")
                logging.info(f"EODHD {sec_name}: ISIN={isin}")
                isin_updates.append((isin, sec_id))
            else:
                print(f"  {sec_name} ({ticker}): not found")

            time.sleep(0.2)  # ~5 req/s — well within EODHD limits

        if isin_updates:
            cur.executemany(
                "UPDATE Securities SET ISIN=%s WHERE Securities_Id=%s AND (ISIN IS NULL OR ISIN='')",
                isin_updates,
            )
            conn.commit()
            print(f"EODHD: {len(isin_updates)} ISIN(s) written.")
            logging.info(f"EODHD: {len(isin_updates)} ISIN(s) written.")
        else:
            print("EODHD: no ISINs found.")
            logging.info("EODHD: no ISINs found.")

    except Exception as e:
        print(f"Error in download_isin_from_eodhd: {e}")
        logging.error(f"EODHD ISIN error: {e}")
    finally:
        cur.close()
        conn.close()


def download_historical_prices_from_tradingview(tsperiod="1m", target_sec_id=None):
    """Download and upsert historical daily prices from TradingView into DB.

    Securities must have TV_Symbol and TV_Exchange populated.

    Each worker thread owns its own TvDatafeed() WebSocket instance so calls
    run concurrently without sharing mutable state.  All rows are collected in
    memory first, then written to the DB in a single execute_batch + commit.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    MAX_WORKERS = 5   # each worker holds one persistent WS connection

    if not tsperiod:
        tsperiod = "1m"
    tsperiod = str(tsperiod).lower().strip()
    n_bars = _period_to_n_bars(tsperiod)

    logging.info(f"Starting TradingView download: period={tsperiod}, n_bars={n_bars}, "
                 f"target_sec_id={target_sec_id}")
    print(f"Starting TradingView download: period={tsperiod}, n_bars={n_bars}, "
          f"target_sec_id={target_sec_id}")

    # Thread-local _PersistentTvDatafeed instances.
    # Each worker thread opens ONE WebSocket on first use (_connect()) and
    # reuses it for every subsequent get_hist() call, eliminating the
    # per-security TCP/TLS handshake that made the sequential version slow.
    #
    # We also track every instance in _tv_instances so we can close their
    # WebSocket connections explicitly *before* the ThreadPoolExecutor exits.
    # Without this, pool.shutdown(wait=True) triggers thread-local GC cleanup
    # which calls WebSocket.__del__() → WebSocket.close() on each thread — a
    # blocking TCP close-handshake that serialises across all 5 workers and
    # adds several seconds after the last security is logged.
    _tv_local     = threading.local()
    _tv_instances: list = []
    _tv_lock      = threading.Lock()
    _connect_lock = threading.Lock()   # serialise initial WS connections

    def _get_tv():
        if not hasattr(_tv_local, 'tv'):
            # Hold the lock while connecting so all MAX_WORKERS threads don't
            # hammer TradingView simultaneously — that triggers HTTP 429.
            with _connect_lock:
                time.sleep(0.5)        # stagger: give TV time between connections
                # Pass connect_lock so that mid-batch reconnects (in get_hist)
                # also serialise through the same lock and avoid HTTP 429.
                tv = _PersistentTvDatafeed(connect_lock=_connect_lock)
                tv._connect()          # lock is already held — no re-entry needed
                _tv_local.tv = tv
                with _tv_lock:
                    _tv_instances.append(tv)
        return _tv_local.tv

    def _fetch(sec_id, sec_name, tv_symbol, tv_exchange, price_scale):
        """Fetch OHLCV history for one security; returns (sec_id, sec_name, symbol, rows, error).

        price_scale normalizes securities TradingView quotes in a minor currency unit
        (e.g. LSE ordinary shares in GBp/GBX pence, 100 to the pound) back to the major
        unit — see Securities.Price_Scale, set by download_securities_info_from_yahoo.
        Without this, the ratio guard below compares an unscaled incoming close against
        an already-scaled stored close and flags every update as suspicious.
        """
        def _try_fetch(bars: int):
            df = _get_tv().get_hist(
                symbol=tv_symbol,
                exchange=tv_exchange,
                interval=Interval.in_daily,
                n_bars=bars,
            )
            if df is None or df.empty:
                return []
            scale = float(price_scale) if price_scale else 1.0
            rows = []
            for date, row in df.iterrows():
                try:
                    date_str = pd.Timestamp(date).strftime("%Y-%m-%d")
                except (OSError, ValueError, OverflowError):
                    continue  # skip pre-1970 timestamps that overflow on Windows
                rows.append((
                    int(sec_id),
                    date_str,
                    float(row["close"]) / scale,
                    None if pd.isna(row["high"])   else float(row["high"]) / scale,
                    None if pd.isna(row["low"])    else float(row["low"])  / scale,
                    0    if pd.isna(row["volume"]) else int(row["volume"]),
                ))
            return rows

        try:
            return sec_id, sec_name, tv_symbol, _try_fetch(n_bars), None
        except (OSError, ValueError, OverflowError) as exc:
            # Windows: datetime.fromtimestamp() fails on pre-1970 timestamps.
            # Retry with ~30 years of data which safely stays post-1970.
            reduced = min(n_bars, 7800)
            logging.warning(f"Timestamp overflow for {tv_symbol} at {n_bars} bars "
                            f"(likely pre-1970 data) — retrying with {reduced} bars")
            try:
                return sec_id, sec_name, tv_symbol, _try_fetch(reduced), None
            except Exception as exc2:
                import traceback
                return sec_id, sec_name, tv_symbol, [], f"{exc2}\n{traceback.format_exc()}"
        except Exception as exc:
            import traceback
            return sec_id, sec_name, tv_symbol, [], f"{exc}\n{traceback.format_exc()}"

    conn = get_connection()
    cur  = conn.cursor()

    try:
        query = """
            SELECT Securities_Id, Securities_Name, TV_Symbol, TV_Exchange, COALESCE(Price_Scale, 1)
            FROM   Securities
            WHERE  TV_Symbol   IS NOT NULL AND TV_Symbol   != ''
              AND  TV_Exchange IS NOT NULL AND TV_Exchange != ''
        """
        params = []
        if target_sec_id:
            query += " AND Securities_Id = %s"
            params.append(int(target_sec_id))
        query += " ORDER BY Securities_Name"

        cur.execute(query, params)
        securities = cur.fetchall()

        if not securities:
            logging.warning("No securities with TV_Symbol/TV_Exchange found.")
            print("No securities with TV_Symbol/TV_Exchange found.")
            return

        total = len(securities)
        logging.info(f"Fetching {n_bars} bars for {total} securities "
                     f"(up to {MAX_WORKERS} in parallel)…")
        print(f"Fetching {n_bars} bars for {total} securities "
              f"(up to {MAX_WORKERS} in parallel)…")

        # ── Parallel fetch ────────────────────────────────────────────────────
        all_rows = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {
                pool.submit(_fetch, sid, sname, sym, exch, scale): sname
                for sid, sname, sym, exch, scale in securities
            }
            for f in as_completed(futures):
                sec_id, sec_name, tv_symbol, rows, err = f.result()
                if err:
                    logging.error(f"Failed {tv_symbol}: {err}")
                    print(f"  ⚠️ Failed {tv_symbol}: {err.splitlines()[0]}")
                elif not rows:
                    logging.warning(f"No data for {tv_symbol}")
                    print(f"  ⚠️ No data for {tv_symbol}")
                else:
                    all_rows.extend(rows)
                    logging.info(f"  ✔ {sec_name} ({tv_symbol}): {len(rows)} rows")
                    print(f"  ✔ {sec_name} ({tv_symbol}): {len(rows)} rows")

            # Close all WebSocket connections explicitly before the executor
            # exits.  If we leave this to thread-local GC (which happens
            # inside pool.shutdown(wait=True)), WebSocket.__del__() blocks on
            # the TCP close-handshake for each of the MAX_WORKERS threads —
            # serially — adding several seconds after the last security logs.
            for _tv_inst in _tv_instances:
                try:
                    if _tv_inst.ws:
                        _tv_inst.ws.close()
                        _tv_inst.ws = None
                except Exception:
                    pass

        # ── Validate before upsert ───────────────────────────────────────────
        # Two guards:
        #
        #  1. Future-date filter — TradingView sometimes returns an incomplete
        #     intraday bar for today when the market is still open. Storing it as
        #     "today's close" would be wrong, and because the scheduler may not
        #     re-run after the market closes the bad price can persist for days.
        #     We skip any row whose date is strictly in the future relative to the
        #     server's wall-clock date.  (Today = allowed; tomorrow+ = skipped.)
        #
        #  2. Ratio check vs existing price — if the incoming close deviates by
        #     more than MAX_OVERWRITE_RATIO in either direction from the already-
        #     stored close for that (security, date) pair, TradingView almost
        #     certainly returned bad data.  We keep the existing price and log
        #     a warning so the anomaly is visible in the scheduler log.

        MAX_OVERWRITE_RATIO = 5.0   # 5× either way = obviously wrong
        today_str           = datetime.today().strftime("%Y-%m-%d")

        # Build per-security lookups for readable log messages and exchange calendars
        sec_name_lkp:   dict[int, str] = {sid: sname for sid, sname, _, _, _ in securities}
        sid_to_exchange: dict[int, str] = {sid: exch  for sid, _, _, exch, _ in securities}

        if all_rows:
            # Fetch existing closes for every (securities_id, date) pair we are
            # about to touch — one query, not N per security.
            uniq_sids  = list({r[0] for r in all_rows})
            uniq_dates = list({r[1] for r in all_rows})
            ph_s = ",".join(["%s"] * len(uniq_sids))
            ph_d = ",".join(["%s"] * len(uniq_dates))
            cur.execute(
                f"SELECT Securities_Id, Date::text, Close "
                f"FROM Historical_Prices "
                f"WHERE Securities_Id IN ({ph_s}) AND Date::text IN ({ph_d})",
                uniq_sids + uniq_dates,
            )
            existing_closes: dict[tuple, float] = {
                (int(r[0]), str(r[1])): float(r[2]) for r in cur.fetchall()
            }

            safe_rows:          list = []
            holiday_deletes:    list = []   # (sid, dt) pairs to DELETE from DB
            skipped_future:     int  = 0
            skipped_holiday:    int  = 0
            deleted_holiday:    int  = 0
            skipped_ratio:      int  = 0

            for row in all_rows:
                sid, dt, close = int(row[0]), str(row[1]), float(row[2])
                sec_name = sec_name_lkp.get(sid, f"id={sid}")
                tv_exch  = sid_to_exchange.get(sid, "")

                # Guard 1 — future date
                if dt > today_str:
                    skipped_future += 1
                    logging.debug("TV: future-dated row skipped  sid=%s  date=%s", sid, dt)
                    continue

                # Guard 1.5 — today's date: only store if market is open OR closed
                # Three states for today:
                #   "pre_market" → TradingView returns pre-open garbage (volume≈0,
                #                  wrong price). SKIP.
                #   "open"       → Live intraday price. ALLOW (user wants P&L).
                #   "closed"     → Final authoritative close (+ 15 min buffer). ALLOW.
                #   "holiday"    → Handled by Guard 2 below.
                #   "unknown"    → Exchange not in calendar. SKIP to be safe.
                if dt == today_str:
                    state = _today_price_state(tv_exch)
                    if state in ("pre_market", "unknown"):
                        skipped_future += 1
                        logging.debug(
                            "TV: skipping today's price for %s (%s) — state=%s",
                            sec_name, tv_exch, state,
                        )
                        continue
                    # "open" or "closed" → fall through and store

                # Guard 2 — non-trading day (weekend or exchange holiday)
                if not _is_tv_trading_day(dt, tv_exch):
                    skipped_holiday += 1
                    msg = (
                        f"TradingView NON-TRADING DAY — {sec_name}: "
                        f"date={dt}  exchange={tv_exch}  close={close:.6f}  → SKIPPED"
                    )
                    logging.warning(msg)
                    print(f"  ⚠️ {msg}")
                    # If a (bad) price was already stored for this non-trading day,
                    # schedule it for deletion so it cannot linger indefinitely.
                    if (sid, dt) in existing_closes:
                        holiday_deletes.append((sid, dt))
                        logging.warning(
                            "TV: scheduling stale holiday price for deletion — "
                            "%s %s stored=%.4f", sec_name, dt, existing_closes[(sid, dt)]
                        )
                    continue

                # Guard 3 — ratio vs existing stored price
                existing = existing_closes.get((sid, dt))
                if existing and existing > 0 and close > 0:
                    ratio = max(close / existing, existing / close)
                    if ratio > MAX_OVERWRITE_RATIO:
                        skipped_ratio += 1
                        msg = (
                            f"TradingView SUSPICIOUS PRICE — {sec_name}: "
                            f"date={dt}  incoming={close:.6f}  "
                            f"stored={existing:.6f}  ratio={ratio:.1f}×  → SKIPPED"
                        )
                        logging.warning(msg)
                        print(f"  ⚠️ {msg}")
                        continue

                safe_rows.append(row)

            # ── Delete stale holiday prices ───────────────────────────────────
            # Prices that were stored on non-trading days (by old code, before
            # the holiday guard was in place) are removed now so they can never
            # corrupt P&L or position reports.
            if holiday_deletes:
                for del_sid, del_dt in holiday_deletes:
                    cur.execute(
                        "DELETE FROM Historical_Prices "
                        "WHERE Securities_Id = %s AND Date = %s",
                        (del_sid, del_dt),
                    )
                    if cur.rowcount:
                        deleted_holiday += 1
                        print(
                            f"  🗑️  Deleted stale holiday price: "
                            f"{sec_name_lkp.get(del_sid, str(del_sid))} {del_dt}"
                        )

            if skipped_future:
                logging.info("TV: skipped %d future-dated or pre-market row(s).", skipped_future)
                print(f"  ℹ️  Skipped {skipped_future} future-dated or pre-market row(s).")
            if skipped_holiday:
                logging.warning("TV: skipped %d non-trading-day row(s) (%d stale deleted).",
                                skipped_holiday, deleted_holiday)
                print(
                    f"  ⚠️  Skipped {skipped_holiday} non-trading-day price(s) "
                    f"(exchange closed — weekend or holiday)"
                    + (f"; deleted {deleted_holiday} stale holiday price(s) from DB."
                       if deleted_holiday else ".")
                )
            if skipped_ratio:
                logging.warning(
                    "TV: skipped %d suspicious row(s) that would have overwritten "
                    "an existing price by more than %.0f×.",
                    skipped_ratio, MAX_OVERWRITE_RATIO,
                )
                print(
                    f"  ⚠️  Skipped {skipped_ratio} suspicious price(s) "
                    f"(>{MAX_OVERWRITE_RATIO:.0f}× deviation). "
                    "Check logs or the Price Quality tool for details."
                )
            all_rows = safe_rows

        # ── Single batch upsert ───────────────────────────────────────────────
        if all_rows:
            execute_batch(cur, """
                INSERT INTO Historical_Prices
                    (Securities_Id, Date, Close, High, Low, Volume, Source, Downloaded_At)
                VALUES (%s, %s, %s, %s, %s, %s, 'TradingView', NOW())
                ON CONFLICT (Securities_Id, Date)
                DO UPDATE SET
                    Close         = EXCLUDED.Close,
                    High          = EXCLUDED.High,
                    Low           = EXCLUDED.Low,
                    Volume        = EXCLUDED.Volume,
                    Source        = EXCLUDED.Source,
                    Downloaded_At = EXCLUDED.Downloaded_At
            """, all_rows, page_size=500)
            conn.commit()

        logging.info(
            f"TradingView price download complete — {len(all_rows)} rows upserted "
            f"for {total} securities."
        )
        print(
            f"TradingView price download complete — {len(all_rows)} rows upserted "
            f"for {total} securities."
        )

    except Exception as e:
        conn.rollback()
        logging.error(f"Global error in TradingView download: {e}")
        print(f"Global error: {e}")
        try:
            logging.error(f"❌ Error: {e}")
        except Exception:
            pass

    finally:
        cur.close()
        conn.close()

    _refresh_materialized_views_async()


def download_bond_prices_from_solidus(target_sec_id=None):
    """Download Greek bond mid-prices from the Solidus PDF and match them to
    Securities by ISIN (stored in Ticker/Yahoo_Ticker for these bonds).

    When target_sec_id is given, only that security's row (if its ISIN appears
    in the PDF) is updated — used for the per-security "Downloads" tab button
    — and the return value reports whether it was actually matched, since a
    single-security caller needs that to show a meaningful status message.
    """
    pdf_url = "https://www.solidus.gr/AppFol/appDetails/RadControls/fol1/Bonds/SOLIDUS_BOND_LIST.pdf"

    response = requests.get(pdf_url)
    if response.status_code != 200:
        print("Failed to receive the file.")
        return {"updated_count": 0, "target_matched": False}

    bond_prices = {}
    pdf_date = None

    with pdfplumber.open(io.BytesIO(response.content)) as pdf:
        # 1. Extract Price Date from the 1st page
        first_page_text = pdf.pages[0].extract_text()
        # Search for patern DD/MM/YYYY (π.χ. 23/4/2026)
        date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{4})', first_page_text)
        if date_match:
            pdf_date = datetime.strptime(date_match.group(1), '%d/%m/%Y').date()
            print(f"PDF Date: {pdf_date}")
        else:
            pdf_date = datetime.now().date()
            print("Date was not found, using today date.")

#    with pdfplumber.open(io.BytesIO(response.content)) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue

            # Regex εξήγηση:
            # (GR[A-Z0-0]{10}) -> Το ISIN (ξεκινά με GR και έχει 10 χαρακτήρες)
            # .*? -> Οτιδήποτε ενδιάμεσα (περιγραφή, ημερομηνίες)
            # (\d+,\d{2,4}) -> Το Bid (νούμερο με κόμμα)
            # \s+ -> Κενό
            # (\d+,\d{2,4}) -> Το Ask (νούμερο με κόμμα)
            pattern = r'(GR[A-Z0-9]{10}).*?(\d+,\d{2,4})\s+(\d+,\d{2,4})'
            
            matches = re.findall(pattern, text)
            
            for match in matches:
                isin = match[0]
                try:
                    # Μετατροπή από "98,50" σε float 98.50
                    bid = float(match[1].replace(',', '.'))
                    ask = float(match[2].replace(',', '.'))
                    
                    mid_price = (bid + ask) / 2
                    bond_prices[isin] = mid_price
                    print(f"Found: {isin} | Bid: {bid} | Ask: {ask} | Mid: {mid_price}")
                except ValueError:
                    continue

    if not bond_prices:
        print("No Data found via Text Extraction.")
        return {"updated_count": 0, "target_matched": False}

    # 3. Update the database
    updated_count = 0
    target_matched = False
    try:
        conn = get_connection()
        cur = conn.cursor()

        for isin, mid_price in bond_prices.items():
            cur.execute("""
                SELECT Securities_Id FROM Securities
                WHERE (Yahoo_Ticker = %s OR Ticker = %s) AND Is_Active = TRUE
            """, (isin, isin))

            res = cur.fetchone()
            if res:
                s_id = res[0]
                if target_sec_id is not None and s_id != target_sec_id:
                    continue
                # Use PDF Date instead of datetime.now()
                cur.execute("""
                    INSERT INTO Historical_Prices (Securities_Id, Date, Close, Source, Downloaded_At)
                    VALUES (%s, %s, %s, 'Solidus', NOW())
                    ON CONFLICT (Securities_Id, Date) DO UPDATE SET
                        Close         = EXCLUDED.Close,
                        Source        = EXCLUDED.Source,
                        Downloaded_At = EXCLUDED.Downloaded_At
                """, (s_id, pdf_date, mid_price))
                updated_count += 1
                if s_id == target_sec_id:
                    target_matched = True

        conn.commit()
        print(f"Successful update of {updated_count} bonds for date {pdf_date}.")
        return {"updated_count": updated_count, "target_matched": target_matched}

    except Exception as e:
        print(f"❌ Error: {e}")
        logging.error(f"❌ Error: {e}")
        return {"updated_count": updated_count, "target_matched": target_matched, "error": str(e)}
    finally:
        cur.close()
        conn.close()

