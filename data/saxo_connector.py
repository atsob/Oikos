"""
Saxo Bank OpenAPI connector — Trade importer.

Authentication: OAuth 2.0 Authorization Code Grant
  Authorize:  {auth_base}/authorize
  Token:      {auth_base}/token  (exchange code / refresh token)

Primary endpoints
  Accounts:    GET /port/v1/accounts/me
  Client key:  GET /port/v1/clients/me
  Trades:      GET /cs/v1/reports/trades/{ClientKey}
  Instruments: GET /ref/v1/instruments/details

Field notes
  - SAXO OpenAPI does not distribute ISIN identifiers (licensing restriction).
    Instruments are identified by UIC (Unique Instrument Code) + AssetType.
  - Commission is reported in SpreadCostAccountCurrency when available.
    Otherwise back-calculated from BookedAmountAccountCurrency vs TradedValue
    when both are in the same currency.
  - BookedAmountAccountCurrency is used as Total_Amount (already in account
    currency — EUR, USD, etc.  The field is named total_eur for DB compatibility
    but contains whatever the account's base currency is).
"""

from __future__ import annotations

import logging
import urllib.parse
from datetime import date

import requests

from database.connection import get_db

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SAXO_PREFIX = "SAXO|"

_LIVE_AUTH = "https://live.logonvalidation.net"
_SIM_AUTH  = "https://sim.logonvalidation.net"
_LIVE_API  = "https://gateway.saxobank.com/openapi"
_SIM_API   = "https://gateway.saxobank.com/sim/openapi"

# Default redirect URI for a local Streamlit app on the standard port
DEFAULT_REDIRECT_URI = "http://localhost:8501"


# ---------------------------------------------------------------------------
# Asset-type → DB Securities_Type mapping
# ---------------------------------------------------------------------------

# Maps SAXO AssetType → DB Securities_Type (what the *underlying security* IS).
# CFD instruments map to the type of their underlying so that the security record
# in the Securities table always reflects the actual instrument (ETF, Stock, …),
# not the derivative wrapper.  The wrapper is captured separately in Instrument_Type.
_ASSET_TO_SEC_TYPE: dict[str, str] = {
    # Equities
    "Stock":            "Stock",
    "Etf":              "ETF",
    "Etc":              "ETF",
    # Fixed income
    "Bond":             "Bond",
    # CFDs — underlying security type (NOT "CFD"; that belongs in Instrument_Type)
    "CfdOnStock":       "Stock",
    "CfdOnEtf":         "ETF",
    "CfdOnIndex":       "Other",
    "CfdOnFutures":     "Other",
    "CfdOnFund":        "Mutual Fund",
    # FX
    "FxSpot":           "FX Spot",
    "FxForwards":       "FX Spot",
    # Options
    "StockIndexOption": "Option",
    "StockOption":      "Option",
    "FuturesOption":    "Option",
    # Futures / other
    "ContractFutures":  "Other",
    # Funds
    "Fund":             "Mutual Fund",
    "MutualFund":       "Mutual Fund",
}


def _sec_type(asset_type: str) -> str:
    """Return the DB ``Securities_Type`` for the *underlying* security."""
    return _ASSET_TO_SEC_TYPE.get(asset_type, "Other")


# ---------------------------------------------------------------------------
# Asset-type → Instrument_Type (how the trade was *executed*)
# ---------------------------------------------------------------------------

# Maps SAXO AssetType → Investments.Instrument_Type.
# This captures the derivative wrapper independently of the security master.
_ASSET_TO_INSTRUMENT_TYPE: dict[str, str] = {
    "Stock":            "Stock",
    "Etf":              "ETF",
    "Etc":              "ETF",
    "Bond":             "Bond",
    "CfdOnStock":       "CFDOnStock",
    "CfdOnEtf":         "CFDOnETF",
    "CfdOnIndex":       "CFDOnIndex",
    "CfdOnFutures":     "CFDOnFutures",
    "CfdOnFund":        "CFDOnFund",
    "FxSpot":           "FX Spot",
    "FxForwards":       "FX Spot",
    "StockIndexOption": "Option",
    "StockOption":      "Option",
    "FuturesOption":    "Option",
    "ContractFutures":  "Other",
    "Fund":             "Fund",
    "MutualFund":       "Fund",
}


def _instrument_type(asset_type: str) -> str:
    """Return the initial ``Instrument_Type`` value for a SAXO AssetType."""
    return _ASSET_TO_INSTRUMENT_TYPE.get(asset_type, "Other")


# Refinement tables used *after* the security has been matched/created in the DB.
# When the matched security's Securities_Type differs from what was inferred from
# the SAXO AssetType alone (e.g. SAXO says "CfdOnEtf" but the DB record is "ETF"),
# these tables produce the correct specific CFDOn* instrument type.

_DB_SEC_TO_CFD_INSTR: dict[str, str] = {
    "Stock":       "CFDOnStock",
    "ETF":         "CFDOnETF",
    "Mutual Fund": "CFDOnFund",
    "Other":       "CFDOnIndex",   # index products stored as "Other"
}

_DB_SEC_TO_DIRECT_INSTR: dict[str, str] = {
    "Stock":       "Stock",
    "ETF":         "ETF",
    "Bond":        "Bond",
    "Mutual Fund": "Fund",
    "Option":      "Option",
    "CFD":         "CFD",
}


def _refine_instrument_type(
    saxo_asset_type:    str,
    initial_instr_type: str,
    db_sec_type:        str | None,
) -> str:
    """Refine ``Instrument_Type`` using the matched security's DB ``Securities_Type``.

    For CFD instruments the specific ``CFDOn*`` variant is derived from the
    actual type of the underlying security in the database.  For direct
    instruments the DB type is used when it maps cleanly to a known instrument
    type, overriding any SAXO-side mis-classification.
    """
    if not db_sec_type:
        return initial_instr_type

    if saxo_asset_type.startswith("Cfd"):
        return _DB_SEC_TO_CFD_INSTR.get(db_sec_type, "CFD")

    refined = _DB_SEC_TO_DIRECT_INSTR.get(db_sec_type)
    return refined if refined else initial_instr_type


# ---------------------------------------------------------------------------
# Exchange suffix → currency  (SAXO symbols often carry ":xnas" style suffix)
# ---------------------------------------------------------------------------

_EXCHANGE_CURRENCY: dict[str, str] = {
    # US
    "xnas": "USD",
    "xnys": "USD",
    "arcx": "USD",
    "bats": "USD",
    "xase": "USD",
    # Europe (EUR)
    "xetr": "EUR",
    "xpar": "EUR",
    "xams": "EUR",
    "xmil": "EUR",
    "xmad": "EUR",
    "xlis": "EUR",
    "xhel": "EUR",
    "xvtx": "EUR",   # SIX Swiss can also be CHF per security, but EUR default
    # UK
    "xlon": "GBP",
    # Other
    "xtse": "CAD",
    "xasx": "AUD",
    "xjpx": "JPY",
    "xhkg": "HKD",
    "xshg": "CNY",
    "xshe": "CNY",
    "xbom": "INR",
    "xnse": "INR",
    "xswx": "CHF",
    "xseb": "SEK",
    "xcse": "DKK",
    "xosl": "NOK",
    "xwar": "PLN",
    "xbud": "HUF",
}


def _currency_from_symbol(symbol: str) -> str | None:
    """Attempt to derive the instrument's trading currency from its exchange suffix.

    SAXO symbols often use the form ``TICKER:xnas``. The suffix is looked up in
    the ``_EXCHANGE_CURRENCY`` table. Returns *None* when the suffix is unknown
    or when the symbol carries no exchange suffix at all.
    """
    if symbol and ":" in symbol:
        suffix = symbol.rsplit(":", 1)[-1].lower()
        return _EXCHANGE_CURRENCY.get(suffix)
    return None


# ---------------------------------------------------------------------------
# Direction / TradeEventType → Investments_Action mapping
# ---------------------------------------------------------------------------

_ACTION_MAP: dict[str, str] = {
    # Direction enum (standard buy/sell)
    "Buy":    "Buy",
    "Sell":   "Sell",
    # TradeEventType variants seen in some account types
    "Bought": "Buy",
    "Sold":   "Sell",
}

_DIVIDEND_EVENTS = {"Dividend", "DividendReinvest", "SpecialDividend"}

# ---------------------------------------------------------------------------
# Non-trade account entry types → DB action
# ---------------------------------------------------------------------------
# Maps values found in Direction or TradeEventType for entries that are NOT
# trade executions: overnight CFD financing, dividends from stock holdings,
# account maintenance fees, CFD dividend cash adjustments, etc.
#
# SAXO *may* return these through the /cs/v1/reports/trades/ endpoint with
# empty TradeId fields, depending on the account type and API version.
# If the endpoint returns them, parse_charges() will capture them.
# If not, the PDF reconciliation path (saxo_pdf_parser) is the fallback.
_CHARGE_ACTION_MAP: dict[str, str] = {
    # ── Income ──────────────────────────────────────────────────────────────
    "Dividend":              "Dividend",
    "DividendReinvest":      "Dividend",
    "SpecialDividend":       "Dividend",
    "CashDividend":          "Dividend",
    "Cashdividend":          "Dividend",
    # CFD dividend equivalents (cash credited instead of the actual dividend)
    "CFDCashAdjustment":     "MiscInc",
    "CfdCashAdjustment":     "MiscInc",
    "CfDCashAdjustment":     "MiscInc",
    "CFDcashadjustment":     "MiscInc",
    # ── Expenses ─────────────────────────────────────────────────────────────
    # CFD / FX overnight financing (TOM-Next roll)
    "CFDFinance":            "MiscExp",
    "CfdFinance":            "MiscExp",
    "CfdFinanceCharge":      "MiscExp",
    "CfdInterestCharge":     "MiscExp",
    "Interest":              "MiscExp",
    "InterestCharge":        "MiscExp",
    "FinancingCost":         "MiscExp",
    # Account maintenance
    "CustodyFee":            "MiscExp",
    "AdministrationFee":     "MiscExp",
    "VAT":                   "MiscExp",
    "DepositoryCharges":     "MiscExp",
    "ExchangeFee":           "MiscExp",
    "WithholdingTax":        "MiscExp",
    # Misc
    "OtherEvent":            "MiscExp",
    # Deposits and withdrawals are handled by the cash account bank import
    # and must NOT be imported as investment charges to avoid duplicates.
    # "Deposit":            intentionally excluded
    # "Withdrawal":         intentionally excluded
}


# ===========================================================================
# OAuth 2.0 helpers
# ===========================================================================

def _auth_base(use_sim: bool = False) -> str:
    return _SIM_AUTH if use_sim else _LIVE_AUTH


def _api_base(use_sim: bool = False) -> str:
    return _SIM_API if use_sim else _LIVE_API


def get_auth_url(app_key: str, redirect_uri: str, use_sim: bool = False) -> str:
    """Return the OAuth2 authorization URL to send the user to.

    The user opens this in their browser, logs in to Saxo, and is then
    redirected back to redirect_uri with ?code=…&state=saxo_import appended.
    """
    params = {
        "response_type": "code",
        "client_id":     app_key,
        "redirect_uri":  redirect_uri,
        "state":         "saxo_import",
    }
    return f"{_auth_base(use_sim)}/authorize?{urllib.parse.urlencode(params)}"


def exchange_code(
    app_key: str,
    app_secret: str,
    code: str,
    redirect_uri: str,
    use_sim: bool = False,
) -> dict:
    """Exchange an OAuth2 authorization code for access + refresh tokens.

    Returns the raw token dict:
      {access_token, expires_in, refresh_token, refresh_token_expires_in, …}
    """
    resp = requests.post(
        f"{_auth_base(use_sim)}/token",
        auth=(app_key, app_secret),
        data={
            "grant_type":   "authorization_code",
            "code":         code,
            "redirect_uri": redirect_uri,
        },
        timeout=15,
    )
    if not resp.ok:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        raise RuntimeError(f"Saxo token error {resp.status_code}: {detail}")
    return resp.json()


def refresh_access_token(
    app_key: str,
    app_secret: str,
    refresh_tok: str,
    use_sim: bool = False,
) -> dict:
    """Obtain a new access token (and a new refresh token) using the refresh token."""
    resp = requests.post(
        f"{_auth_base(use_sim)}/token",
        auth=(app_key, app_secret),
        data={
            "grant_type":    "refresh_token",
            "refresh_token": refresh_tok,
        },
        timeout=15,
    )
    if not resp.ok:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        raise RuntimeError(f"Saxo token error {resp.status_code}: {detail}")
    return resp.json()


# ===========================================================================
# Low-level API helper
# ===========================================================================

def _api_get(access_token: str, url: str, params: dict | None = None) -> dict:
    """GET a Saxo OpenAPI endpoint; raise on HTTP errors."""
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        params=params or {},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ===========================================================================
# High-level data fetchers
# ===========================================================================

def fetch_client_key(access_token: str, use_sim: bool = False) -> str:
    """Return the ClientKey for the currently authenticated user."""
    data = _api_get(access_token, f"{_api_base(use_sim)}/port/v1/clients/me")
    return data["ClientKey"]


def fetch_accounts(access_token: str, use_sim: bool = False) -> list[dict]:
    """Return all trading accounts owned by the authenticated user.

    Each entry: {AccountKey, AccountId, Currency, AccountType, DisplayName}
    """
    data = _api_get(access_token, f"{_api_base(use_sim)}/port/v1/accounts/me")
    accounts: list[dict] = []
    for a in data.get("Data", []):
        accounts.append({
            "AccountKey":  a["AccountKey"],
            "AccountId":   a.get("AccountId", a["AccountKey"]),
            "Currency":    a.get("Currency", ""),
            "AccountType": a.get("AccountType", ""),
            "DisplayName": (
                a.get("DisplayName")
                or a.get("AccountId")
                or a["AccountKey"]
            ),
        })
    return accounts


def fetch_trades(
    access_token: str,
    client_key:   str,
    account_key:  str,
    from_date:    date,
    to_date:      date,
    use_sim:      bool = False,
) -> list[dict]:
    """Fetch all executed trade records for one account in [from_date, to_date].

    Automatically paginates using the __next link returned by the API.
    """
    url    = f"{_api_base(use_sim)}/cs/v1/reports/trades/{client_key}"
    params: dict = {
        "AccountKey": account_key,
        "FromDate":   from_date.isoformat(),
        "ToDate":     to_date.isoformat(),
        "$top":       1000,
    }
    records: list[dict] = []

    while True:
        data = _api_get(access_token, url, params)
        records.extend(data.get("Data", []))

        next_url = data.get("__next", "")
        if not next_url:
            break
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(next_url).query)
        skip_tok = qs.get("$skiptoken", [None])[0]
        if skip_tok:
            # __next already encodes all original filters; just forward the token
            params = {"$skiptoken": skip_tok}
        else:
            break

    return records


def fetch_instrument_details(
    access_token:       str,
    uic_assettype_pairs: list[tuple[int, str]],
    use_sim:            bool = False,
) -> dict[tuple[int, str], dict]:
    """Batch-fetch instrument details for a list of (Uic, AssetType) pairs.

    Returns {(Uic, AssetType): {Symbol, Description, Currency, Exchange}}.
    Instruments are grouped by AssetType (required by the API) and fetched
    in chunks of up to 100 per request.
    """
    if not uic_assettype_pairs:
        return {}

    # Group by AssetType
    by_type: dict[str, list[int]] = {}
    for uic, at in uic_assettype_pairs:
        by_type.setdefault(at, []).append(uic)

    result: dict[tuple, dict] = {}
    base = f"{_api_base(use_sim)}/ref/v1/instruments/details"

    for asset_type, uics in by_type.items():
        for i in range(0, len(uics), 100):
            chunk = uics[i : i + 100]
            try:
                data = _api_get(access_token, base, params={
                    "Uics":       ",".join(str(u) for u in chunk),
                    "AssetTypes": asset_type,
                })
                for instr in data.get("Data", []):
                    primary = instr.get("PrimaryListing") or {}
                    key = (instr["Identifier"], instr["AssetType"])
                    result[key] = {
                        "Symbol":      instr.get("Symbol", ""),
                        "Description": instr.get("Description", ""),
                        "Currency":    instr.get("CurrencyCode", ""),
                        "Exchange":    primary.get("ExchangeId", "")
                                       if isinstance(primary, dict) else "",
                    }
            except Exception as exc:
                log.warning(
                    "Instrument detail fetch failed for AssetType=%s (chunk %d): %s",
                    asset_type, i, exc,
                )

    return result


# ===========================================================================
# Trade record parser
# ===========================================================================

_FXSPOT_TYPES = frozenset({"FxSpot", "FxForwards", "FxVanillaOption"})


def parse_trades(
    raw_trades:         list[dict],
    instrument_cache:   dict[tuple[int, str], dict],
) -> list[dict]:
    """Convert raw SAXO trade records into the standardised inv_record format.

    Produced fields (compatible with run_import and the preview helpers):
      record_type, source, desc, symbol, name, isin, currency,
      asset_category, date, action, quantity, price,
      commission, total_eur, exchange, account_id_str

    ``total_eur`` is total in the account's base currency (EUR or USD).
    The field name is kept for compatibility with the shared preview helpers.
    """
    # ── Pre-pass: infer closing commissions from Buy trades ─────────────────
    #
    # SAXO does not populate any cost field (SpreadCostAccountCurrency,
    # CommissionAccountCurrency, TotalCostAccountCurrency) on Sell / close
    # trades for position-based instruments.  We therefore derive the fee from
    # the matching Buy / open trades in the same batch.
    #
    # FX Spot (XAGEUR, EURUSD, …): spread is symmetric and proportional to
    #   quantity.  Per-unit rate = BookedAmountAccountCurrency_buy / qty.
    #   Apply rate × qty_sell to each closing trade.
    #
    # CFD on ETF / Fund / Index / Futures: SAXO charges a flat minimum fee
    #   (e.g. EUR 4 / USD 3.4 per trade up to a certain notional, then %).
    #   Use the mean absolute opening commission from Buy trades of the same
    #   symbol as the best estimate of the closing fee.
    #   Note: this is an approximation — if a sell trade is large enough that
    #   the percentage leg exceeds the minimum, the actual fee will differ.

    _fxspot_rates:    dict[str, list[float]] = {}  # symbol → per-unit rates
    _cfd_flat_fees:   dict[str, list[float]] = {}  # symbol → absolute commissions

    for _t in raw_trades:
        # Normalise direction the same way the main loop does (handles both
        # "Buy" and "Bought" values returned by different SAXO account types).
        _dir    = _t.get("Direction") or ""
        _ev     = _t.get("TradeEventType") or ""
        _action = _ACTION_MAP.get(_dir) or _ACTION_MAP.get(_ev)
        if _action != "Buy":
            continue
        _at = _t.get("AssetType", "")
        _uic    = _t.get("Uic")
        _instr  = instrument_cache.get((_uic, _at), {})
        _sym    = (
            _instr.get("Symbol")
            or _t.get("InstrumentSymbol")
            or (str(_uic) if _uic else "")
        )
        _qty    = abs(float(_t.get("Amount", 0) or 0))
        _booked = abs(float(_t.get("BookedAmountAccountCurrency", 0) or 0))

        if _at in _FXSPOT_TYPES:
            # FX Spot: per-unit rate
            if _sym and _qty > 0 and _booked > 0:
                _fxspot_rates.setdefault(_sym, []).append(_booked / _qty)
        elif _at.startswith("Cfd"):
            # CFD: flat opening commission
            if _sym and _booked > 0:
                _cfd_flat_fees.setdefault(_sym, []).append(_booked)

    # Mean per-unit commission rate for each FX Spot symbol seen in Buy trades
    _fxspot_commission_rate: dict[str, float] = {
        sym: sum(rates) / len(rates)
        for sym, rates in _fxspot_rates.items()
    }

    # Mean flat commission for each CFD symbol seen in Buy trades
    _cfd_commission_fee: dict[str, float] = {
        sym: sum(fees) / len(fees)
        for sym, fees in _cfd_flat_fees.items()
    }

    records: list[dict] = []

    for t in raw_trades:
        # ── Date ─────────────────────────────────────────────────────────────
        raw_date = t.get("TradeDate") or (t.get("TradeExecutionTime") or "")[:10]
        if not raw_date:
            continue
        try:
            trade_date = date.fromisoformat(raw_date)
        except ValueError:
            continue

        # ── Action ───────────────────────────────────────────────────────────
        direction  = t.get("Direction") or ""
        event_type = t.get("TradeEventType") or ""
        action     = _ACTION_MAP.get(direction) or _ACTION_MAP.get(event_type)

        if action is None:
            if event_type in _DIVIDEND_EVENTS:
                action = "Dividend"
            else:
                log.debug(
                    "Skipping SAXO trade with Direction=%r TradeEventType=%r",
                    direction, event_type,
                )
                continue

        # ── Instrument ───────────────────────────────────────────────────────
        uic        = t.get("Uic")
        asset_type = t.get("AssetType", "Stock")
        instr      = instrument_cache.get((uic, asset_type), {})

        symbol   = instr.get("Symbol") or t.get("InstrumentSymbol") or (str(uic) if uic else "")
        name     = instr.get("Description") or t.get("InstrumentDescription") or symbol
        # Currency priority:
        #   1. Instrument detail from /ref/v1/instruments/details (most accurate)
        #   2. Exchange-suffix of the symbol (e.g. "NVDA:xnas" → "USD")
        #   3. Account's base currency as last resort
        currency = (
            instr.get("Currency")
            or _currency_from_symbol(symbol)
            or t.get("AccountCurrency", "EUR")
        )
        exchange = instr.get("Exchange", "")

        # asset_category = underlying security type (for Securities table lookup)
        # instrument_type = how the trade was executed (for Investments entry)
        # saxo_asset_type is kept verbatim so run_import can refine instrument_type
        # once the matched security's DB type is known.
        asset_cat   = _sec_type(asset_type)
        instr_type  = _instrument_type(asset_type)

        # ── Quantity & Price ─────────────────────────────────────────────────
        quantity = abs(float(t.get("Amount", 0) or 0))
        price    = abs(float(t.get("Price",  0) or 0))

        # ── Classify trade mechanics ─────────────────────────────────────────
        # "Position-based" instruments (CFD, FX Spot) do NOT move the full
        # notional through the cash account.  For these:
        #   BookedAmountAccountCurrency = spread cost (open) or P&L (close)
        #                                 in account currency — a small number
        #   TradedValue                 = qty × price in instrument currency
        #                                 — the meaningful position notional
        #
        # Regular instruments (Stock, ETF, Bond) DO move the full notional:
        #   BookedAmountAccountCurrency = full cash out/in already FX-converted
        #                                 to account currency
        is_fxspot   = asset_type in _FXSPOT_TYPES
        is_position = asset_type.startswith("Cfd") or is_fxspot

        booked_amt   = abs(float(t.get("BookedAmountAccountCurrency", 0) or 0))
        traded_val   = abs(float(t.get("TradedValue", 0) or 0))
        spread_cost  = abs(float(t.get("SpreadCostAccountCurrency", 0) or 0))
        # Additional SAXO fields that may carry the full closing cost for
        # Sell (close) trades.  Present on some account types / instrument types.
        total_cost        = abs(float(t.get("TotalCostAccountCurrency", 0) or 0))
        comm_field        = abs(float(t.get("CommissionAccountCurrency", 0) or 0))
        cross_ccy_position = False   # set True only for cross-currency position trades
        notional           = 0.0

        if is_position:
            # Use the notional (TradedValue) as the base position value.
            # Fall back to booked_amt if TradedValue is missing.
            notional = traded_val if traded_val > 0 else booked_amt

            # ── Commission ───────────────────────────────────────────────────
            # For BUY (position open):
            #   BookedAmountAccountCurrency = the TOTAL cash debited to open
            #   the position (half-spread + platform fees + all other charges).
            #   This is the single most reliable source for the real commission.
            #
            #   SpreadCostAccountCurrency captures only the raw bid-ask
            #   half-spread component and should NOT be used here — during
            #   volatile markets it can wildly overshoot the actual fee
            #   (e.g. 6.70 EUR reported vs 0.84 EUR actually charged).
            #
            # For SELL (position close):
            #   BookedAmountAccountCurrency = realised P&L — cannot be used.
            #
            #   For FX Spot (XAGEUR, EURUSD, …) the bid-ask spread is symmetric:
            #   the per-unit rate to close equals the per-unit rate to open.
            #   We infer this rate from the Buy trades in this same batch (pre-pass
            #   above) and multiply by quantity.  This gives a consistent ~0.85 EUR
            #   for XAGEUR 10 oz regardless of intraday spread volatility.
            #
            #   For CFDs the closing fee is typically 0 (SAXO charges only on open).
            #   Fall back to TotalCostAccountCurrency / CommissionAccountCurrency /
            #   SpreadCostAccountCurrency if available, else 0.
            if action == "Buy":
                commission = booked_amt
            else:
                # FX Spot: use inferred per-unit rate from Buy trades
                if is_fxspot:
                    _rate = _fxspot_commission_rate.get(symbol, 0.0)
                    if _rate > 0:
                        commission = round(_rate * quantity, 4)
                    elif total_cost > 0:
                        commission = total_cost
                    elif comm_field > 0:
                        commission = comm_field
                    elif spread_cost > 0:
                        commission = spread_cost
                    else:
                        commission = 0.0
                else:
                    # CFD Sell (close): dedicated cost fields first, then fall
                    # back to the mean opening commission seen in Buy trades for
                    # this symbol.  SAXO typically leaves all three cost fields
                    # at 0 for CFD-on-ETF closes — the flat broker fee is
                    # charged but not echoed in the close trade record.
                    if total_cost > 0:
                        commission = total_cost
                    elif comm_field > 0:
                        commission = comm_field
                    elif spread_cost > 0:
                        commission = spread_cost
                    else:
                        commission = round(
                            _cfd_commission_fee.get(symbol, 0.0), 4
                        )

            # Align with the app convention: Total_Amount = qty×price ± commission
            # (Buy adds commission to cost basis; Sell subtracts from proceeds).
            # Same-currency: build total_acc directly from notional ± commission.
            # Cross-currency: the notional is in security currency (e.g. USD) while
            # total_acc must be in account currency (e.g. EUR).  We cannot convert
            # here (no DB access), so we flag the record: total_acc is left as 0
            # and total_sec_cur carries the notional.  run_import will reverse-FX
            # to derive total_acc from total_sec_cur at import time.
            acc_currency = t.get("AccountCurrency", "")
            cross_ccy_position = bool(
                currency and acc_currency and currency != acc_currency
            )
            if not cross_ccy_position:
                if action == "Buy":
                    total_acc = notional + commission
                else:
                    total_acc = max(0.0, notional - commission)
            else:
                # Cross-currency: total_acc cannot be built here (no FX access).
                # run_import derives it from total_sec_cur (notional) + commission
                # using the historical FX rate:
                #   total_sec = notional ± commission_acc / fx
                #   total_acc = total_sec × fx  =  notional×fx ± commission_acc
                total_acc = 0.0

        else:
            # Regular trade: cash flow in account currency (already FX-converted)
            total_acc  = booked_amt
            commission = spread_cost

            # Back-calculate commission for same-currency trades.
            # SAXO BUY  → BookedAmountAccountCurrency = TradedValue + commission
            #              (commission embedded in total outlay), so the difference
            #              equals the commission.
            # SAXO SELL → BookedAmountAccountCurrency = TradedValue (gross proceeds,
            #              commission NOT embedded).  Back-calc yields ~0 for Sell.
            if commission == 0 and quantity > 0 and price > 0 and total_acc > 0:
                acc_currency = t.get("AccountCurrency", "")
                if currency and acc_currency and currency == acc_currency and traded_val > 0:
                    back_comm = abs(total_acc - traded_val)
                    if back_comm > 0.0001:
                        commission = round(back_comm, 4)

            # Fallback: CommissionAccountCurrency / TotalCostAccountCurrency /
            # BookedCostAccountCurrency carry the flat broker fee for cross-currency
            # Buys and Sell trades.
            if commission == 0 and comm_field > 0:
                commission = comm_field
            if commission == 0 and total_cost > 0:
                commission = total_cost

            acc_currency = t.get("AccountCurrency", "")
            cross_currency = bool(currency and acc_currency and currency != acc_currency)

            # For cross-currency Buy: BookedAmountAccountCurrency is the pure trade
            # value (FX-converted); commission is reported separately but in the same
            # account currency, so it is safe to add directly.
            # For same-currency Buy the commission is already embedded in booked_amt
            # via the back-calc above, so do NOT add it again.
            if action == "Buy" and commission > 0 and cross_currency:
                total_acc = round(booked_amt + commission, 2)

            # For Sell: net proceeds = gross proceeds − commission.
            # Only adjust when instrument and account share the same base currency
            # so booked_amt and commission are in the same unit.
            if action == "Sell" and commission > 0:
                if currency and acc_currency and currency == acc_currency:
                    total_acc = max(0.0, round(booked_amt - commission, 2))

        # ── Dedup key / description ──────────────────────────────────────────
        trade_id = t.get("TradeId") or t.get("OrderId") or ""
        if trade_id:
            desc = f"{_SAXO_PREFIX}TRADE|{trade_id}"
        else:
            # Fallback key for records without a stable ID
            desc = f"{_SAXO_PREFIX}{action}|{symbol}|{raw_date}|{quantity}"

        records.append({
            "record_type":    "investment",
            "source":         "SAXO",
            "desc":           desc,
            "symbol":         symbol,
            "name":           name,
            "isin":           "",   # SAXO OpenAPI does not distribute ISINs
            "currency":       currency,
            # Underlying security type (used for Securities table lookup/creation)
            "asset_category": asset_cat,
            # How the trade was executed (written to Investments.Instrument_Type).
            # May be refined in run_import once the matched security's DB type
            # is known (e.g. "CfdOnEtf" + DB says "ETF" → "CFDOnETF").
            "instrument_type": instr_type,
            # Raw SAXO asset type kept for the refinement step
            "saxo_asset_type": asset_type,
            "date":           trade_date,
            "action":         action,
            "quantity":       round(quantity,   6),
            "price":          round(price,      6),
            "commission":     round(commission, 4),
            "total_eur":      round(total_acc,  2),
            # For cross-currency position instruments (e.g. CfdOnEtf USD in EUR
            # account): total_acc is 0 because the notional is in security currency.
            # total_sec_cur carries the security-currency notional so run_import
            # can reverse-FX it to produce the correct account-currency total.
            # For cross-currency positions, total_sec_cur carries the raw notional
            # (TradedValue in security currency).  run_import adds commission
            # (account currency) converted to security currency via the FX rate,
            # giving: total_sec = notional ± commission_acc/fx
            # and:    total_acc = total_sec × fx = notional×fx ± commission_acc
            "total_sec_cur":  round(notional, 2) if cross_ccy_position else None,
            "exchange":       exchange,
            "account_id_str": t.get("AccountId", ""),
        })

    return records


# ===========================================================================
# Account charges — parsing
# ===========================================================================

def parse_charges(
    raw_trades:       list[dict],
    instrument_cache: dict[tuple[int, str], dict],
) -> list[dict]:
    """Extract non-trade account entries from the raw SAXO API data.

    Processed entry types (see ``_CHARGE_ACTION_MAP``):
      • CFDFinance      — overnight TOM-Next financing on open CFD/FX positions
      • Cashdividend    — cash dividend paid on a directly-held stock
      • CFDCashAdjust…  — dividend equivalent credited to CFD holders
      • CustodyFee      — monthly account custody fee
      • VAT             — value-added tax on certain fees
      • OtherEvent      — miscellaneous account bookings

    SAXO *may or may not* return these through ``/cs/v1/reports/trades/``
    depending on account type and API version.  If the list is empty the UI
    will direct the user to the PDF reconciliation path instead.

    Records are compatible with ``run_charges_import()`` and share the same
    dedup-key format as ``parse_trades()`` so a combined replace-mode wipe
    works consistently.
    """
    records: list[dict] = []

    for t in raw_trades:
        direction  = t.get("Direction",      "") or ""
        event_type = t.get("TradeEventType", "") or ""

        action = (
            _CHARGE_ACTION_MAP.get(event_type)
            or _CHARGE_ACTION_MAP.get(direction)
        )
        if action is None:
            continue  # normal trade record — handled by parse_trades()

        # ── Date ─────────────────────────────────────────────────────────────
        raw_date = t.get("TradeDate") or (t.get("TradeExecutionTime") or "")[:10]
        if not raw_date:
            continue
        try:
            trade_date = date.fromisoformat(raw_date)
        except ValueError:
            continue

        # ── Amount ───────────────────────────────────────────────────────────
        booked_amt = float(t.get("BookedAmountAccountCurrency", 0) or 0)
        if booked_amt == 0:
            continue

        # ── Instrument (may be absent for account-level fees) ─────────────
        uic        = t.get("Uic")
        asset_type = t.get("AssetType", "") or ""
        instr      = instrument_cache.get((uic, asset_type), {}) if uic else {}
        symbol     = instr.get("Symbol") or t.get("InstrumentSymbol") or ""
        name       = instr.get("Description") or t.get("InstrumentDescription") or ""
        currency   = instr.get("Currency") or t.get("AccountCurrency", "EUR")

        if not name:
            name = event_type or direction   # human-readable fallback

        asset_cat  = _sec_type(asset_type)          if asset_type else "Other"
        instr_type = _instrument_type(asset_type)    if asset_type else "Other"

        # ── Dedup key ─────────────────────────────────────────────────────
        charge_key = (event_type or direction).upper().replace(" ", "").replace("-", "")
        if symbol:
            desc = f"{_SAXO_PREFIX}CHARGE|{charge_key}|{symbol}|{raw_date}"
        else:
            desc = f"{_SAXO_PREFIX}CHARGE|{charge_key}||{raw_date}"

        # ── Total amount (always stored positive) ─────────────────────────
        # MiscExp / MiscInc / Dividend — magnitude = the actual EUR cost/income
        total_eur = abs(booked_amt)

        records.append({
            "record_type":    "investment",
            "source":         "SAXO",
            "desc":           desc,
            "symbol":         symbol,
            "name":           name,
            "isin":           "",
            "currency":       currency,
            "asset_category": asset_cat,
            "instrument_type": instr_type,
            "saxo_asset_type": asset_type,
            "date":           trade_date,
            "action":         action,
            "quantity":       0.0,
            "price":          0.0,
            "commission":     0.0,
            "total_eur":      round(total_eur, 2),
            "exchange":       "",
            "account_id_str": t.get("AccountId", ""),
            "charge_type":    event_type or direction,  # kept for display
        })

    return records


# ===========================================================================
# Security matching
# ===========================================================================

def _get_or_create_security(
    cur,
    symbol:         str,
    name:           str,
    currency:       str,
    asset_category: str,
    _cached_mappings: dict | None = None,
) -> tuple[int, str]:
    """Return (securities_id, match_type) for a SAXO instrument.

    Match priority:
      0. User-defined mapping in import_security_mappings (by symbol or name)
      1. Exact ticker/symbol match in Securities table
      2. Exact name match in Securities table
      3. Create new Securities row
    """
    if _cached_mappings is None:
        from database.queries import get_security_mappings as _get_map
        _cached_mappings = _get_map("Saxo Bank")

    # 0. User mapping
    for mk in (symbol, name):
        if mk and mk in _cached_mappings:
            sec_id = _cached_mappings[mk]
            cur.execute(
                "SELECT Securities_Name FROM Securities WHERE Securities_Id = %s",
                (sec_id,),
            )
            row = cur.fetchone()
            return sec_id, f"mapped:{row[0] if row else mk}"

    # SAXO symbols carry an exchange suffix (e.g. "NVDA:xnas").
    # Strip it so we can match against DB tickers that were imported from other
    # brokers (IB, manual entry) using the bare ticker without exchange suffix.
    bare_ticker = symbol.split(":")[0] if symbol and ":" in symbol else symbol

    # 1. Ticker match — try full symbol first, then bare ticker
    for _tk in dict.fromkeys(filter(None, [symbol, bare_ticker])):
        cur.execute(
            "SELECT Securities_Id, Securities_Name FROM Securities "
            "WHERE Ticker = %s LIMIT 1",
            (_tk,),
        )
        row = cur.fetchone()
        if row:
            return row[0], "ticker"

    # 2. Name match
    if name:
        cur.execute(
            "SELECT Securities_Id FROM Securities "
            "WHERE Securities_Name = %s LIMIT 1",
            (name,),
        )
        row = cur.fetchone()
        if row:
            return row[0], "name"

    # 3. Create new — store bare ticker so future cross-broker matches work
    ticker_for_db = bare_ticker or name[:30]
    cur.execute(
        """INSERT INTO Securities
               (Ticker, Securities_Name, Securities_Type, Currencies_Id)
           VALUES (%s, %s, %s,
                  (SELECT Currencies_Id FROM Currencies
                   WHERE  Currencies_ShortName = %s LIMIT 1))
           RETURNING Securities_Id""",
        (ticker_for_db, name, asset_category, currency or "EUR"),
    )
    return cur.fetchone()[0], "new"


# ===========================================================================
# Duplicate detection
# ===========================================================================

def _inv_exists(cur, acc_id: int, desc: str) -> bool:
    cur.execute(
        "SELECT 1 FROM Investments "
        "WHERE Accounts_Id = %s AND Description = %s LIMIT 1",
        (acc_id, desc),
    )
    return cur.fetchone() is not None


def check_existing_records(
    inv_records: list,
    account_map: "dict[str, int]",
) -> set:
    """Return the set of description keys already stored verbatim in the DB."""
    if not inv_records or not account_map:
        return set()

    existing: set[str] = set()
    db_acc_ids = list(set(account_map.values()))

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT Description FROM Investments "
            "WHERE  Accounts_Id = ANY(%s) AND Description LIKE %s",
            (db_acc_ids, f"{_SAXO_PREFIX}%"),
        )
        in_db = {row[0] for row in cur.fetchall()}

    for rec in inv_records:
        if rec["desc"] in in_db:
            existing.add(rec["desc"])

    return existing


def check_fuzzy_duplicates(
    inv_records: list,
    account_map: "dict[str, int]",
) -> set:
    """Return description keys for records likely already imported.

    A 'fuzzy match' is: same DB account + same date + same action + same qty
    (within ±0.0001) even if the description key differs.
    """
    if not inv_records or not account_map:
        return set()

    fuzzy: set[str] = set()

    with get_db() as conn:
        cur = conn.cursor()
        for rec in inv_records:
            db_acc_id = account_map.get(rec.get("account_id_str"))
            if not db_acc_id:
                continue
            cur.execute(
                """SELECT 1 FROM Investments
                   WHERE  Accounts_Id = %s
                     AND  Date        = %s
                     AND  Action      = %s::investments_action
                     AND  ABS(Quantity - %s) < 0.0001
                   LIMIT 1""",
                (db_acc_id, rec["date"], rec["action"], rec["quantity"]),
            )
            if cur.fetchone():
                fuzzy.add(rec["desc"])

    return fuzzy


def preview_security_matches(inv_records: list) -> dict[str, tuple]:
    """Return {symbol: (securities_id | None, match_type)} for each unique symbol.

    Used to annotate the preview table before import (same API as IB connector).
    """
    unique: dict[str, tuple] = {
        r["symbol"]: (r["symbol"], r["name"], r["currency"], r["asset_category"])
        for r in inv_records
        if r.get("symbol")
    }
    result: dict[str, tuple] = {}

    with get_db() as conn:
        cur = conn.cursor()
        mappings: dict = {}
        try:
            from database.queries import get_security_mappings as _get_map
            mappings = _get_map("Saxo Bank")
        except Exception:
            pass

        for symbol, (sym, name, ccy, cat) in unique.items():
            # 0. User mapping
            for mk in (sym, name):
                if mk and mk in mappings:
                    sec_id = mappings[mk]
                    cur.execute(
                        "SELECT Securities_Name FROM Securities WHERE Securities_Id = %s",
                        (sec_id,),
                    )
                    row = cur.fetchone()
                    result[symbol] = (sec_id, f"mapped:{row[0] if row else mk}")
                    break
            if symbol in result:
                continue

            # 1. Ticker — try full symbol (e.g. "NVDA:xnas") then bare ticker ("NVDA")
            bare = sym.split(":")[0] if ":" in sym else sym
            _ticker_found = False
            for _tk in dict.fromkeys(filter(None, [sym, bare])):
                cur.execute(
                    "SELECT Securities_Id FROM Securities WHERE Ticker = %s LIMIT 1",
                    (_tk,),
                )
                row = cur.fetchone()
                if row:
                    result[symbol] = (row[0], "ticker")
                    _ticker_found = True
                    break
            if _ticker_found:
                continue

            # 2. Name
            cur.execute(
                "SELECT Securities_Id FROM Securities "
                "WHERE Securities_Name = %s LIMIT 1",
                (name,),
            )
            row = cur.fetchone()
            if row:
                result[symbol] = (row[0], "name")
                continue

            result[symbol] = (None, "new")

    return result


# ===========================================================================
# Import
# ===========================================================================

def run_import(
    inv_records:   list,
    account_map:   "dict[str, int]",
    replace_mode:  bool = False,
    progress_cb    = None,
) -> dict:
    """Insert parsed SAXO trade records into the Investments table.

    Parameters
    ----------
    inv_records  : list of dicts from parse_trades()
    account_map  : {saxo_account_id_str: db_accounts_id}
                   Records whose account_id_str is not in the map are skipped.
    replace_mode : if True, delete all existing SAXO|* records for every mapped
                   DB account before inserting.
    progress_cb  : optional callable(float 0‥1) for progress updates.

    Returns
    -------
    counts: {investments, investments_skip}
    """
    from database.crud import (
        update_holdings,
        update_accounts_balances,
        update_investment_balances,
        resolve_investment_fx,
    )

    counts = {"investments": 0, "investments_skip": 0}
    total  = max(len(inv_records), 1)
    done   = 0

    with get_db() as conn:
        cur = conn.cursor()
        # Cache: db_acc_id → Currencies_Id (fetched lazily)
        _acc_cur_cache: dict[int, int] = {}

        # ── Optional: wipe existing SAXO records for re-import ────────────────
        if replace_mode:
            for db_acc_id in set(account_map.values()):
                # Collect linked cash transaction IDs before deleting investments
                cur.execute(
                    "SELECT Transactions_Id FROM Investments "
                    "WHERE Accounts_Id = %s AND Description LIKE %s "
                    "  AND Transactions_Id IS NOT NULL",
                    (db_acc_id, f"{_SAXO_PREFIX}%"),
                )
                linked_tx_ids = [r[0] for r in cur.fetchall()]

                cur.execute(
                    "DELETE FROM Investments "
                    "WHERE Accounts_Id = %s AND Description LIKE %s",
                    (db_acc_id, f"{_SAXO_PREFIX}%"),
                )

                # Delete orphaned linked cash transactions (and their splits)
                if linked_tx_ids:
                    cur.execute(
                        "DELETE FROM Splits WHERE transactions_id = ANY(%s)",
                        (linked_tx_ids,)
                    )
                    cur.execute(
                        "DELETE FROM Transactions WHERE Transactions_Id = ANY(%s)",
                        (linked_tx_ids,)
                    )

                log.info(
                    "Replace mode: deleted SAXO investments + %d linked transactions "
                    "for account %d", len(linked_tx_ids), db_acc_id
                )

        # ── Load user-defined security mappings once ──────────────────────────
        from database.queries import get_security_mappings as _get_sec_map
        _saxo_mappings = _get_sec_map("Saxo Bank")

        # ── Insert records ────────────────────────────────────────────────────
        for rec in inv_records:
            db_acc_id = account_map.get(rec.get("account_id_str"))
            if db_acc_id is None:
                log.warning(
                    "No DB account mapping for SAXO AccountId=%r — skipping",
                    rec.get("account_id_str"),
                )
                counts["investments_skip"] += 1
                done += 1
                continue

            if not replace_mode and _inv_exists(cur, int(db_acc_id), rec["desc"]):
                counts["investments_skip"] += 1
            else:
                sec_id, _ = _get_or_create_security(
                    cur,
                    rec["symbol"],
                    rec["name"],
                    rec.get("currency", "EUR"),
                    rec.get("asset_category", "Other"),
                    _cached_mappings=_saxo_mappings,
                )

                # Refine instrument_type using the security's actual DB type.
                # This ensures e.g. a CfdOnEtf trade where the DB security is
                # "ETF" gets Instrument_Type = "CFDOnETF", not the raw initial
                # value that was derived from the SAXO asset type alone.
                cur.execute(
                    "SELECT Securities_Type, Currencies_Id FROM Securities "
                    "WHERE Securities_Id = %s",
                    (sec_id,),
                )
                _sec_row = cur.fetchone()
                _db_sec_type = str(_sec_row[0]) if _sec_row else None
                _sec_cur_id  = int(_sec_row[1]) if _sec_row and _sec_row[1] else None
                _instr_type  = _refine_instrument_type(
                    rec.get("saxo_asset_type", ""),
                    rec.get("instrument_type", "Other"),
                    _db_sec_type,
                )

                # Resolve account currency (cached)
                _db_acc_id_int = int(db_acc_id)
                if _db_acc_id_int not in _acc_cur_cache:
                    cur.execute(
                        "SELECT Currencies_Id FROM Accounts WHERE Accounts_Id = %s",
                        (_db_acc_id_int,),
                    )
                    _ar = cur.fetchone()
                    _acc_cur_cache[_db_acc_id_int] = int(_ar[0]) if _ar else None
                _acc_cur_id = _acc_cur_cache[_db_acc_id_int]

                # Resolve FX and security-currency total (SAXO has no explicit FX).
                # Cross-currency position instruments (e.g. CfdOnEtf): total_eur=0
                # and total_sec_cur holds the notional in security currency.
                # We reverse-FX to get account-currency total and store both.
                _known_sec = rec.get("total_sec_cur")   # notional in sec ccy, or None
                _total_acc = rec["total_eur"] if rec["total_eur"] != 0 else None

                if _known_sec is not None and _sec_cur_id is not None and _acc_cur_id is not None:
                    # Look up FX rate: sec → acc direction (e.g. USD→EUR)
                    cur.execute(
                        """SELECT fx_rate FROM Historical_FX
                           WHERE currencies_id_1 = %s AND currencies_id_2 = %s
                             AND date <= COALESCE(%s::date, CURRENT_DATE)
                           ORDER BY date DESC LIMIT 1""",
                        (_sec_cur_id, _acc_cur_id, rec["date"]),
                    )
                    _fxrow = cur.fetchone()
                    if not _fxrow:
                        # Try inverse direction
                        cur.execute(
                            """SELECT fx_rate FROM Historical_FX
                               WHERE currencies_id_1 = %s AND currencies_id_2 = %s
                                 AND date <= COALESCE(%s::date, CURRENT_DATE)
                               ORDER BY date DESC LIMIT 1""",
                            (_acc_cur_id, _sec_cur_id, rec["date"]),
                        )
                        _fxrow = cur.fetchone()
                        _fx = (1.0 / float(_fxrow[0])) if _fxrow else 1.0
                    else:
                        _fx = float(_fxrow[0])
                    # Commission is in account currency; convert to security currency.
                    # total_sec = notional ± commission_acc / fx
                    # total_acc = total_sec × fx  =  notional×fx ± commission_acc
                    _commission_acc = rec.get("commission", 0.0) or 0.0
                    if rec.get("action") == "Buy":
                        _sec_amt   = round(_known_sec + (_commission_acc / _fx if _fx else 0), 18)
                    else:
                        _sec_amt   = round(max(0.0, _known_sec - (_commission_acc / _fx if _fx else 0)), 18)
                    _total_acc = round(_sec_amt * _fx, 2)
                elif _total_acc is not None:
                    _sec_amt, _fx = resolve_investment_fx(
                        cur, _total_acc, _acc_cur_id, _sec_cur_id, rec["date"],
                    )
                else:
                    _sec_amt, _fx = None, 1.0

                cur.execute(
                    """INSERT INTO Investments
                           (Accounts_Id, Securities_Id, Date, Action, Quantity,
                            Price_Per_Share, Commission,
                            Total_Amount_AccCur, Total_Amount_SecCur, FX_Rate,
                            Description, Instrument_Type)
                       VALUES (%s, %s, %s, %s::investments_action, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        _db_acc_id_int,
                        sec_id,
                        rec["date"],
                        rec["action"],
                        rec["quantity"]   if rec["quantity"]   > 0 else None,
                        rec["price"]      if rec["price"]      > 0 else None,
                        rec["commission"] if rec["commission"] > 0 else None,
                        _total_acc,
                        _sec_amt,
                        _fx,
                        rec["desc"],
                        _instr_type or None,
                    ),
                )
                counts["investments"] += 1

            done += 1
            if progress_cb and done % 10 == 0:
                progress_cb(done / total)

    if progress_cb:
        progress_cb(1.0)

    # Post-commit balance / holdings refresh
    update_holdings()
    update_investment_balances()

    # Auto-create linked cash transactions for every imported account that has
    # a linked cash account configured.
    from database.crud import (
        create_linked_cash_transactions_for_unlinked,
        get_linked_account_id,
    )
    _seen_inv_accs: set[int] = set(int(v) for v in account_map.values())
    for _inv_acc in _seen_inv_accs:
        _linked = get_linked_account_id(_inv_acc)
        if _linked:
            create_linked_cash_transactions_for_unlinked(_inv_acc, _linked)
            update_accounts_balances(_linked)
    if _seen_inv_accs:
        update_investment_balances()

    return counts


# ===========================================================================
# Account charges — import
# ===========================================================================

_SAXO_CHARGE_PREFIX = f"{_SAXO_PREFIX}CHARGE|"
_SAXO_ACCT_FEE_TICKER = "SAXO-ACC-FEES"
_SAXO_ACCT_FEE_NAME   = "Saxo Bank (Account Fees)"


def _squash(s: str) -> str:
    """Normalise a security name for fuzzy comparison.

    Removes all whitespace, hyphens, ampersands, dots, commas and slashes
    then lowercases the result.  This lets ``"iSharesSilverTrust ETF"``
    match ``"iShares Silver Trust ETF"`` and ``"Merck&Co.Inc."`` match
    ``"Merck & Co. Inc."``.
    """
    import re as _re
    return _re.sub(r"[\s\-&.,;/]", "", s).lower()


def _find_security_by_name(
    cur,
    name:     str,
    mappings: dict,
    currency: str = "",
) -> int | None:
    """Look up an existing security by name — **never** creates a new row.

    Used for PDF charge records where the instrument name was extracted from the
    PDF but may not match any security in the database exactly (because
    pdfplumber concatenates adjacent words without spaces, e.g.
    ``"iSharesSilverTrust ETF"`` for ``"iShares Silver Trust ETF"``).

    Returns ``None`` when no match is found so the caller can fall back to the
    account-fee placeholder instead of polluting the Securities table with
    garbage names.

    Match priority:
      0. User-defined mapping (by name)
      1. Exact name match in Securities table (case-sensitive)
      2. Case-insensitive name match
      3. Squash-exact match   (removes spaces/punctuation, lowercases)
      4. Squash-prefix match  (DB name starts-with PDF name, or vice-versa)
         — handles names like "iShares Silver Trust" matching "iSharesSilverTrust ETF"
           and "Thomson Reuters Corp (USD)" matching "ThomsonReuters Corp."
         — when multiple candidates, prefer the one whose name contains *currency*

    *currency* is optional but improves tiebreaking among differently-suffixed
    variants of the same security (e.g. Thomson Reuters Corp (USD) vs (EUR)).
    """
    if not name:
        return None

    # 0. User mapping
    if name in mappings:
        mapped_id = mappings[name]
        cur.execute(
            "SELECT Securities_Id FROM Securities WHERE Securities_Id = %s",
            (mapped_id,),
        )
        row = cur.fetchone()
        if row:
            return row[0]

    # 1. Exact name match
    cur.execute(
        "SELECT Securities_Id FROM Securities WHERE Securities_Name = %s LIMIT 1",
        (name,),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    # 2. Case-insensitive name match
    cur.execute(
        "SELECT Securities_Id FROM Securities WHERE LOWER(Securities_Name) = LOWER(%s) LIMIT 1",
        (name,),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    # 3 + 4: Squash-based matching (fetch all once, check both exact and prefix)
    name_sq = _squash(name)
    if not name_sq or len(name_sq) < 4:
        return None

    cur.execute("SELECT Securities_Id, Securities_Name FROM Securities")
    all_rows = cur.fetchall()

    # 3. Exact squash match (handles camelCase differences)
    for sec_id, sec_name in all_rows:
        if sec_name and _squash(sec_name) == name_sq:
            return sec_id

    # 4. Bidirectional prefix squash match
    # Catches cases where DB name has extra qualifiers, e.g.:
    #   PDF "iSharesSilverTrust ETF" → db_sq "isharessilvertrust" (prefix of pdf_sq)
    #   PDF "iSharesDiversified … UCITSETF" → db_sq "…ucitsetfusd(acc)" (pdf_sq is prefix)
    candidates: list[tuple[int, str]] = []
    for sec_id, sec_name in all_rows:
        if not sec_name:
            continue
        db_sq = _squash(sec_name)
        if len(db_sq) < 4:
            continue
        if db_sq.startswith(name_sq) or name_sq.startswith(db_sq):
            candidates.append((sec_id, sec_name))

    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0][0]

    # Multiple prefix matches: prefer candidate whose name contains the currency
    if currency:
        ccy = currency.upper()
        ccy_matches = [(sid, sn) for sid, sn in candidates if ccy in sn.upper()]
        if ccy_matches:
            return ccy_matches[0][0]

    # Default: smallest Securities_Id (earliest imported)
    return min(candidates, key=lambda x: x[0])[0]


def preview_pdf_charge_security_matches(
    charge_records: list,
) -> dict[str, tuple]:
    """Return {instrument_name: (sec_id | None, match_label)} for PDF charges.

    Only instrument-linked records are checked — account-level entries
    (CustodyFee, VAT, FinancingCost, Deposit, Withdrawal) always use the
    placeholder and are excluded.

    Match labels:
      ``"mapped:<name>"`` — resolved via a saved user mapping
      ``"squash:<name>"`` — resolved by squash / bidirectional-prefix lookup
      ``"placeholder"``   — no match found; will use account-fee placeholder
    """
    _ACCT_LEVEL = {
        "CustodyFee", "VAT", "FinancingCost",
        "AdministrationFee", "Deposit", "Withdrawal",
    }

    # Collect unique (name → currency) for instrument-linked records only
    unique: dict[str, str] = {}
    for rec in charge_records:
        charge_t = rec.get("charge_type", "")
        name     = rec.get("name", "")
        if name and name != charge_t and charge_t not in _ACCT_LEVEL:
            unique.setdefault(name, rec.get("currency", ""))

    result: dict[str, tuple] = {}
    if not unique:
        return result

    with get_db() as conn:
        cur = conn.cursor()
        try:
            from database.queries import get_security_mappings as _get_sec_map
            mappings = _get_sec_map("Saxo Bank")
        except Exception:
            mappings = {}

        for name, currency in unique.items():
            # 0. User mapping takes priority
            if name in mappings:
                mapped_id = mappings[name]
                cur.execute(
                    "SELECT Securities_Id, Securities_Name FROM Securities "
                    "WHERE Securities_Id = %s",
                    (mapped_id,),
                )
                row = cur.fetchone()
                if row:
                    result[name] = (row[0], f"mapped:{row[1]}")
                    continue

            # 1-4. Standard squash / prefix lookup
            found = _find_security_by_name(cur, name, mappings, currency=currency)
            if found is not None:
                cur.execute(
                    "SELECT Securities_Name FROM Securities WHERE Securities_Id = %s",
                    (found,),
                )
                row = cur.fetchone()
                result[name] = (found, f"squash:{row[0] if row else found}")
            else:
                result[name] = (None, "placeholder")

    return result


def _get_or_create_account_fee_security(cur, currency: str = "EUR") -> int:
    """Return (or lazily create) a placeholder security for account-level charges.

    Used for entries with no instrument: CustodyFee, VAT, FinancingCost, etc.
    """
    cur.execute(
        "SELECT Securities_Id FROM Securities WHERE Ticker = %s LIMIT 1",
        (_SAXO_ACCT_FEE_TICKER,),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute(
        """INSERT INTO Securities
               (Ticker, Securities_Name, Securities_Type, Currencies_Id)
           VALUES (%s, %s, %s,
                  (SELECT Currencies_Id FROM Currencies
                   WHERE  Currencies_ShortName = %s LIMIT 1))
           RETURNING Securities_Id""",
        (_SAXO_ACCT_FEE_TICKER, _SAXO_ACCT_FEE_NAME, "Other", currency or "EUR"),
    )
    return cur.fetchone()[0]


def preview_pdf_commissions(pdf_path: "str | Path") -> "tuple[list[dict], list[str]]":
    """Parse PDF commissions and return a preview list without writing to the DB.

    Each entry in the returned list is a dict with keys:
      trade_id, security_name, action, date, current_commission,
      pdf_commission, current_total, new_total, status, selected

    status is one of: 'ready' | 'already_set' | 'not_in_db'
    selected defaults to True for 'ready' rows.
    Returns (preview_rows, warnings).
    """
    from data.saxo_pdf_parser import parse_trade_commissions

    commissions = parse_trade_commissions(pdf_path)
    if not commissions:
        return [], ["No trade commissions found in PDF."]

    rows: list[dict] = []
    warnings: list[str] = []
    prefix = "SAXO|TRADE|"

    with get_db() as conn:
        with conn.cursor() as cur:
            for trade_id, comm_eur in commissions.items():
                desc_pattern = f"%{prefix}{trade_id}%"
                cur.execute(
                    """SELECT i.Investments_Id, i.Action, i.Total_Amount_AccCur,
                              i.Transactions_Id, i.Commission,
                              COALESCE(s.Securities_Name, i.Description) AS sec_name,
                              i.Date
                         FROM Investments i
                         LEFT JOIN Securities s ON s.Securities_Id = i.Securities_Id
                        WHERE i.Description LIKE %s""",
                    (desc_pattern,),
                )
                row = cur.fetchone()
                if not row:
                    warnings.append(
                        f"TradeID {trade_id} ({comm_eur:.2f} EUR): not in DB — "
                        f"trade was not imported (outside API fetch range)."
                    )
                    rows.append({
                        "trade_id": trade_id,
                        "security_name": "",
                        "action": "",
                        "date": None,
                        "current_commission": None,
                        "pdf_commission": comm_eur,
                        "current_total": None,
                        "new_total": None,
                        "status": "not_in_db",
                        "selected": False,
                    })
                    continue

                inv_id, action, total_acc_cur, tx_id, cur_comm, sec_name, trade_date = row
                already_set = cur_comm is not None and float(cur_comm) != 0
                if already_set:
                    warnings.append(
                        f"TradeID {trade_id} ({comm_eur:.2f} EUR): commission already "
                        f"set to {float(cur_comm):.4f} EUR."
                    )

                delta = comm_eur if action == "Buy" else -comm_eur
                new_total = (float(total_acc_cur) + delta) if total_acc_cur is not None else delta

                rows.append({
                    "trade_id": trade_id,
                    "security_name": sec_name or "",
                    "action": action or "",
                    "date": trade_date,
                    "current_commission": float(cur_comm) if cur_comm is not None else None,
                    "pdf_commission": comm_eur,
                    "current_total": float(total_acc_cur) if total_acc_cur is not None else None,
                    "new_total": new_total,
                    "status": "already_set" if already_set else "ready",
                    "selected": not already_set,
                })

    return rows, warnings


def apply_pdf_commissions(
    pdf_path: "str | Path",
    selected_trade_ids: "set[str] | None" = None,
) -> "tuple[int, list[str]]":
    """Read Booked Costs from a SAXO Transaction PDF and update Investment records.

    Matches each trade's commission to the Investment row via its Description
    field (stored as ``SAXO|TRADE|{trade_id}``).

    For each matched trade:
      • Sets Commission on the Investment row.
      • Adjusts Total_Amount_AccCur: Buy → +commission, Sell → −commission.
      • If a linked cash Transaction exists (Investments.Transactions_Id IS NOT NULL),
        adjusts Transactions.Total_Amount by the same signed delta so the cash
        account balance stays correct.

    Pass ``selected_trade_ids`` to restrict updates to a specific subset of trades.
    Returns (rows_updated, warnings).
    """
    from data.saxo_pdf_parser import parse_trade_commissions

    commissions = parse_trade_commissions(pdf_path)
    if not commissions:
        return 0, ["No trade commissions found in PDF."]

    if selected_trade_ids is not None:
        commissions = {k: v for k, v in commissions.items() if k in selected_trade_ids}
    if not commissions:
        return 0, ["No selected trades to apply."]

    updated   = 0
    warnings: list[str] = []
    prefix    = "SAXO|TRADE|"

    with get_db() as conn:
        with conn.cursor() as cur:
            for trade_id, comm_eur in commissions.items():
                desc_pattern = f"%{prefix}{trade_id}%"

                # Fetch the investment row (skip if already has commission)
                cur.execute(
                    """SELECT Investments_Id, Action, Total_Amount_AccCur,
                              Transactions_Id, Commission
                         FROM Investments
                        WHERE Description LIKE %s""",
                    (desc_pattern,),
                )
                row = cur.fetchone()
                if not row:
                    warnings.append(
                        f"TradeID {trade_id} ({comm_eur:.2f} EUR): not in DB — "
                        f"trade was not imported (outside API fetch range)."
                    )
                    continue
                if row[4] is not None and float(row[4]) != 0:
                    warnings.append(
                        f"TradeID {trade_id} ({comm_eur:.2f} EUR): skipped — "
                        f"commission already set to {float(row[4]):.4f} EUR "
                        f"(CFD/FX position trade)."
                    )
                    continue

                inv_id, action, total_acc_cur, tx_id, _ = row

                # Signed delta: Buy costs more (+), Sell yields less (−)
                delta = comm_eur if action == "Buy" else -comm_eur
                new_total = (float(total_acc_cur) + delta) if total_acc_cur is not None else delta

                cur.execute(
                    """UPDATE Investments
                          SET Commission          = %s,
                              Total_Amount_AccCur = %s
                        WHERE Investments_Id = %s""",
                    (comm_eur, new_total, inv_id),
                )
                updated += 1

                # Update linked cash transaction when present
                if tx_id:
                    # Cash Total_Amount is negative for Buy (outflow), positive for Sell (inflow)
                    # For Buy: more cost → more negative → delta is −comm_eur on the cash side
                    # For Sell: less received → delta is −comm_eur on the cash side
                    cur.execute(
                        """UPDATE Transactions
                              SET Total_Amount = Total_Amount - %s
                            WHERE Transactions_Id = %s""",
                        (comm_eur, tx_id),
                    )

        conn.commit()

    # Recalculate balances: investment totals changed, linked cash transactions updated.
    from database.crud import update_investment_balances, update_accounts_balances, get_linked_account_id
    update_investment_balances()
    # Refresh the linked cash account for every investment account that was touched.
    touched_inv_accs: set[int] = set()
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT i.Accounts_Id
                     FROM Investments i
                    WHERE i.Description LIKE %s
                      AND i.Commission > 0""",
                (f"%{prefix}%",),
            )
            touched_inv_accs = {r[0] for r in cur.fetchall()}
    for _inv_acc in touched_inv_accs:
        _linked = get_linked_account_id(_inv_acc)
        if _linked:
            update_accounts_balances(_linked)

    return updated, warnings


def run_charges_import(
    charge_records: list,
    account_map:    "dict[str, int]",
    replace_mode:   bool = False,
    progress_cb     = None,
) -> dict:
    """Insert SAXO charge records into the Investments table.

    Parameters
    ----------
    charge_records  : list of dicts from ``parse_charges()`` or
                      ``saxo_pdf_parser.parse_saxo_transactions_pdf()``
    account_map     : {saxo_account_id_str: db_accounts_id}
                      Records whose account_id_str is not in the map are
                      routed to the fallback account (if only one is mapped)
                      or skipped.
    replace_mode    : if True, delete all existing SAXO CHARGE records for
                      mapped accounts before inserting.
    progress_cb     : optional callable(float 0..1) for progress updates.

    Returns
    -------
    counts: {"imported": int, "skipped": int}
    """
    from database.crud import update_holdings, update_investment_balances, resolve_investment_fx

    counts = {"imported": 0, "skipped": 0}
    total  = max(len(charge_records), 1)
    done   = 0

    # If only one account is mapped use it as fallback for records whose
    # account_id_str is unknown (e.g. records imported from the PDF).
    _fallback_acc_id: int | None = (
        next(iter(account_map.values())) if len(account_map) == 1 else None
    )

    with get_db() as conn:
        cur = conn.cursor()
        # Cache: db_acc_id → Currencies_Id (fetched lazily)
        _acc_cur_cache: dict[int, int] = {}

        if replace_mode:
            for db_acc_id in set(account_map.values()):
                cur.execute(
                    "SELECT Transactions_Id FROM Investments "
                    "WHERE Accounts_Id = %s AND Description LIKE %s "
                    "  AND Transactions_Id IS NOT NULL",
                    (db_acc_id, f"{_SAXO_CHARGE_PREFIX}%"),
                )
                linked_tx_ids = [r[0] for r in cur.fetchall()]

                cur.execute(
                    "DELETE FROM Investments "
                    "WHERE Accounts_Id = %s AND Description LIKE %s",
                    (db_acc_id, f"{_SAXO_CHARGE_PREFIX}%"),
                )

                if linked_tx_ids:
                    cur.execute(
                        "DELETE FROM Splits WHERE transactions_id = ANY(%s)",
                        (linked_tx_ids,)
                    )
                    cur.execute(
                        "DELETE FROM Transactions WHERE Transactions_Id = ANY(%s)",
                        (linked_tx_ids,)
                    )
                log.info(
                    "Replace mode: deleted SAXO charge records for account %d",
                    db_acc_id,
                )

        from database.queries import get_security_mappings as _get_sec_map
        _saxo_mappings = _get_sec_map("Saxo Bank")

        for rec in charge_records:
            # Account routing
            db_acc_id = (
                account_map.get(rec.get("account_id_str"))
                or _fallback_acc_id
            )
            if db_acc_id is None:
                log.warning(
                    "No DB account mapping for SAXO charge AccountId=%r — skipping",
                    rec.get("account_id_str"),
                )
                counts["skipped"] += 1
                done += 1
                continue

            if not replace_mode and _inv_exists(cur, int(db_acc_id), rec["desc"]):
                counts["skipped"] += 1
            else:
                # Security resolution:
                #
                #  API records (source="SAXO") always have a symbol → create-or-match.
                #
                #  PDF records (source="SAXO_PDF") never have a reliable symbol and
                #  their extracted names may be partial/abbreviated (e.g. "NVIDIACorp.").
                #  We do LOOKUP ONLY — if no existing security matches, we fall back to
                #  the account-fee placeholder.  This prevents garbage securities from
                #  being created from unintelligible PDF name fragments.
                #
                #  Account-level fees (CustodyFee, VAT, FinancingCost, Deposit, …)
                #  are stored with Securities_Id = NULL — they have no instrument.
                _charge_t   = rec.get("charge_type", "")
                _instr_name = rec.get("name", "")
                _is_pdf     = rec.get("source", "") == "SAXO_PDF"
                _ACCT_LEVEL = {
                    "CustodyFee", "VAT", "FinancingCost",
                    "AdministrationFee", "Deposit", "Withdrawal",
                }

                if rec.get("symbol"):
                    # API record with a known symbol → create-or-match
                    sec_id, _ = _get_or_create_security(
                        cur,
                        rec["symbol"],
                        _instr_name,
                        rec.get("currency", "EUR"),
                        rec.get("asset_category", "Other"),
                        _cached_mappings=_saxo_mappings,
                    )
                elif (
                    not _is_pdf
                    and _instr_name
                    and _instr_name != _charge_t
                    and _charge_t not in _ACCT_LEVEL
                ):
                    # API instrument-linked charge without a symbol → create-or-match by name
                    sec_id, _ = _get_or_create_security(
                        cur,
                        "",
                        _instr_name,
                        rec.get("currency", "EUR"),
                        rec.get("asset_category", "Other"),
                        _cached_mappings=_saxo_mappings,
                    )
                elif (
                    _is_pdf
                    and _instr_name
                    and _instr_name != _charge_t
                    and _charge_t not in _ACCT_LEVEL
                ):
                    # PDF instrument-linked charge → LOOKUP ONLY, no create
                    _found = _find_security_by_name(
                        cur, _instr_name, _saxo_mappings,
                        currency=rec.get("currency", ""),
                    )
                    sec_id = _found if _found is not None else _get_or_create_account_fee_security(
                        cur, rec.get("currency", "EUR")
                    )
                else:
                    # Account-level fee (CustodyFee, VAT, FinancingCost, Deposit, …)
                    # These have no underlying instrument — store Securities_Id = NULL.
                    # The Investments.Securities_Id column is nullable and
                    # update_holdings() already filters out IS NULL rows.
                    sec_id = None

                # Resolve account currency (cached) and FX for charges
                _db_acc_id_int = int(db_acc_id)
                if _db_acc_id_int not in _acc_cur_cache:
                    cur.execute(
                        "SELECT Currencies_Id FROM Accounts WHERE Accounts_Id = %s",
                        (_db_acc_id_int,),
                    )
                    _ar = cur.fetchone()
                    _acc_cur_cache[_db_acc_id_int] = int(_ar[0]) if _ar else None
                _acc_cur_id = _acc_cur_cache[_db_acc_id_int]

                _sec_cur_id_chg = None
                if sec_id is not None:
                    cur.execute(
                        "SELECT Currencies_Id FROM Securities WHERE Securities_Id = %s",
                        (sec_id,),
                    )
                    _scr = cur.fetchone()
                    _sec_cur_id_chg = int(_scr[0]) if _scr and _scr[0] else None

                _total_acc_chg = rec["total_eur"] if rec["total_eur"] != 0 else None

                # Use amounts parsed directly from the PDF when available
                # (saxo_pdf_parser sets total_sec_cur and fx_rate_db from the
                # "Conversion Rate" column).  Fall back to Historical_FX lookup
                # for API-sourced charge records that have no explicit rate.
                _pdf_sec  = rec.get("total_sec_cur")   # None for API records
                _pdf_fx   = rec.get("fx_rate_db")      # None for API records

                if _total_acc_chg is not None:
                    if _pdf_sec is not None and _pdf_fx is not None:
                        # PDF-sourced: trust the on-page values directly
                        _sec_amt_chg = _pdf_sec
                        _fx_chg      = _pdf_fx
                    else:
                        _sec_amt_chg, _fx_chg = resolve_investment_fx(
                            cur, _total_acc_chg, _acc_cur_id, _sec_cur_id_chg, rec["date"],
                        )
                else:
                    _sec_amt_chg, _fx_chg = None, 1.0

                cur.execute(
                    """INSERT INTO Investments
                           (Accounts_Id, Securities_Id, Date, Action,
                            Quantity, Price_Per_Share, Commission,
                            Total_Amount_AccCur, Total_Amount_SecCur, FX_Rate,
                            Description, Instrument_Type)
                       VALUES (%s, %s, %s, %s::investments_action,
                               NULL, NULL, NULL, %s, %s, %s, %s, NULL)""",
                    (
                        _db_acc_id_int,
                        sec_id,
                        rec["date"],
                        rec["action"],
                        _total_acc_chg,
                        _sec_amt_chg,
                        _fx_chg,
                        rec["desc"],
                    ),
                )
                counts["imported"] += 1

            done += 1
            if progress_cb and done % 5 == 0:
                progress_cb(done / total)

    if progress_cb:
        progress_cb(1.0)

    update_holdings()
    update_investment_balances()

    # Auto-create linked cash transactions for every imported account that has
    # a linked cash account configured.
    from database.crud import (
        create_linked_cash_transactions_for_unlinked,
        get_linked_account_id,
        update_accounts_balances,
    )
    _seen_chg_accs: set[int] = set(int(v) for v in account_map.values())
    for _inv_acc in _seen_chg_accs:
        _linked = get_linked_account_id(_inv_acc)
        if _linked:
            create_linked_cash_transactions_for_unlinked(_inv_acc, _linked)
            update_accounts_balances(_linked)
    if _seen_chg_accs:
        update_investment_balances()

    return counts
