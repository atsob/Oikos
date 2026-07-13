"""Interactive Brokers Flex Web Service connector.

One-time IB setup (do this once in Account Management)
-------------------------------------------------------
1. Log in to https://www.interactivebrokers.com → Client Portal →
   Reports → Flex Queries.
2. Click "+" → create an "Activity Flex Query".
3. Under **Sections**, enable at minimum:
     • Trades            (buySell, assetCategory, symbol, description,
                          currency, fxRateToBase, quantity, tradePrice,
                          netCash, tradeDate, ibOrderID, isin)
     • Cash Transactions (type, currency, fxRateToBase, amount,
                          description, symbol, dateTime, isin)
4. Date Format: yyyyMMdd   Time Format: HHmmss   Date/Time separator: ;
5. Save — note the numeric **Query ID**.
6. In the same Reports section → "Flex Web Service" → create / copy your
   **Token** (a long alphanumeric string).
7. Paste both into the app import form.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import date, datetime
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SEND_URL = (
    "https://gdcdyn.interactivebrokers.com"
    "/Universal/servlet/FlexStatementService.SendRequest"
)
_GET_URL = (
    "https://gdcdyn.interactivebrokers.com"
    "/Universal/servlet/FlexStatementService.GetStatement"
)
_API_VERSION = "3"
_IB_PREFIX   = "IB|"

_ASSET_TO_SECTYPE: dict[str, str] = {
    "STK":    "Stock",
    "ETF":    "ETF",
    "BOND":   "Bond",
    "OPT":    "Option",
    "WAR":    "Option",
    "IOPT":   "Option",
    "FUT":    "Futures",
    "CASH":   "FX Spot",
    "FX":     "FX Spot",
    "FXSPOT": "FX Spot",
    "CFD":    "CFD",
    "FUND":   "ETF",
    "CRYPTO": "Crypto",
}

# assetCategory values IB uses for the currency-conversion trades it books to fund
# purchases of foreign-currency securities (e.g. selling EUR.GBP to raise GBP for a
# UK stock buy) — these aren't real security positions, just FX housekeeping.
_FX_SPOT_CATEGORIES: set[str] = {"CASH", "FX", "FXSPOT"}


def is_fx_spot_record(rec: dict) -> bool:
    """True for investment records that are IB's own currency-conversion trades."""
    return str(rec.get("asset_category") or "").upper() in _FX_SPOT_CATEGORIES

# CashTransaction.type → Investments.Action. Matched case-insensitively (via
# .lower() at the call site) — IB's Flex API is inconsistent about
# capitalization of this field across reports/accounts (e.g. "Payment in Lieu
# of Dividends" vs "Payment In Lieu Of Dividends"), and an exact-case dict
# lookup used to silently drop the whole record whenever the casing didn't
# match, with no error and no trace in the preview.
_CASH_INV_MAP: dict[str, str] = {
    "dividends":                    "Dividend",
    "payment in lieu of dividends": "Dividend",
    "broker interest received":     "IntInc",
    "broker interest paid":         "MiscExp",
    "withholding tax":              "MiscExp",   # only when it can't be merged into a dividend — see _parse_cash_transactions
}

# Dividend-shaped cash-transaction types eligible to have a same-event
# Withholding Tax entry merged into them (see _parse_cash_transactions).
_DIVIDEND_CASH_TYPES: set[str] = {"dividends", "payment in lieu of dividends"}
_WITHHOLDING_TAX_TYPE = "withholding tax"

# Cash-transaction types that are account-level cash flow, not tied to any real,
# individually-held security — e.g. interest on idle cash, or Stock Yield
# Enhancement Program (SYEP) fee income for lending out shares. IB's own
# "symbol"/"description" fields for these are often non-empty but unstable
# (e.g. "...INTEREST FOR JUN-2026", changing every month) or just the
# settlement currency — neither is a usable, stable security identity. These
# are always booked against one placeholder security per settlement currency
# instead of matching/creating a security from the raw description, so the
# same kind of monthly interest never spawns a new one-off "security" each
# time it recurs. Kept separate per currency — matching this app's existing
# convention of one Securities row per currency variant for the same
# underlying thing (e.g. "Thomson Reuters Corp (USD)" / "(GBP)" / "(CAD)") —
# so a USD interest payment doesn't get mislabeled under a security whose
# currency is actually EUR or GBP.
_ACCOUNT_LEVEL_CASH_TYPES: set[str] = {"broker interest received", "broker interest paid"}


def _ib_cash_symbol(currency: str) -> str:
    return f"IB-CASH-{currency or 'EUR'}"


def _ib_cash_name(currency: str) -> str:
    return f"Interactive Brokers Cash Interest ({currency or 'EUR'})"

# CashTransaction.type → plain Transactions
_CASH_TX_TYPES: set[str] = {
    "Deposits/Withdrawals",
    "Fees Paid",
    "Other Fees",
    "Commission Adjustments",
}


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _parse_ib_date(s: str) -> Optional[date]:
    """Accept '20240115', '20240115;094500', '2024-01-15', etc."""
    if not s:
        return None
    s = str(s).strip().split(";")[0].split(" ")[0]
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# Flex Web Service — network layer
# ---------------------------------------------------------------------------

# IB error messages that are transient — safe to retry automatically.
# Note: IB's Activity Statement Flex Queries only refresh once per day at
# close of business (per IB's own docs, "there is no benefit to generating
# and retrieving these reports more than once per day") — a same-day retry
# after an already-successful fetch reliably surfaces one of these same
# messages (1001/1009), not a real transient server hiccup, so retrying
# won't help there. The fetch endpoint caches each day's successful XML to
# avoid ever hitting this in normal use; this list is for genuine transient
# failures on the *first* fetch of a day.
_IB_TRANSIENT_ERRORS = (
    "Statement could not be generated at this time",   # codes 1001, 1009
    "Statement generation in progress",                # code 1019 — still queued
    "Statement could not be retrieved",                # code 1021
    "Please try again",
)

# IB error messages that indicate a permanent configuration problem
_IB_PERM_ERROR_HINTS: dict[str, str] = {
    "Token has expired":          "Your Flex Token has expired — create a new one in IB → Reports → Flex Web Service.",
    "Token exceeded maximum":     "Your Flex Token has exceeded its request limit — create a new token.",
    "IB Key could not be found":  "Token not recognised by IB.  Double-check the token value and try again.",
    "Account details could not":  "IB could not retrieve account details.  Check that the Query ID belongs to this account.",
    "Access Denied":              "Access denied — in IB portal edit the Flex Query → tick 'Allow Web Service Access' at the bottom → Save.",
}


def _request_statement(token: str, query_id: str,
                       max_attempts: int = 8, delay: float = 10.0,
                       progress_cb=None) -> str:
    """Step 1: ask IB to queue the report.  Returns the reference code.

    IB occasionally returns error 1019 ("Statement could not be generated
    at this time") even for valid queries — this is a transient server-side
    condition.  We retry up to *max_attempts* times with *delay* seconds
    between each attempt before giving up.
    """
    last_err: str = ""
    for attempt in range(max_attempts):
        if attempt > 0:
            if progress_cb:
                progress_cb(
                    f"IB server busy, retrying ({attempt}/{max_attempts - 1})… "
                    f"({last_err[:80]})"
                )
            time.sleep(delay)

        resp = requests.get(
            _SEND_URL,
            params={"t": token, "q": query_id, "v": _API_VERSION},
            timeout=30,
        )
        resp.raise_for_status()
        root   = ET.fromstring(resp.text)
        status = (root.findtext("Status") or "").strip()

        if status == "Success":
            ref = (root.findtext("ReferenceCode") or "").strip()
            if not ref:
                raise ValueError("IB Flex: no ReferenceCode in response.")
            return ref

        msg = (root.findtext("ErrorMessage") or resp.text or "Unknown error").strip()

        # Check for permanent errors — no point retrying these
        for pattern, hint in _IB_PERM_ERROR_HINTS.items():
            if pattern.lower() in msg.lower():
                raise ValueError(f"IB Flex error: {msg}\n\n💡 {hint}")

        # Transient error — will retry on next loop iteration
        is_transient = any(t.lower() in msg.lower() for t in _IB_TRANSIENT_ERRORS)
        if is_transient and attempt < max_attempts - 1:
            last_err = msg
            continue

        # Non-transient, or last attempt — give up
        raise ValueError(f"IB Flex request failed: {msg}")

    raise ValueError(
        f"IB Flex request failed after {max_attempts} attempts: {last_err}\n\n"
        "💡 If you already fetched this statement successfully earlier today, this is "
        "expected — IB only regenerates Activity Statements once per day, so a repeat "
        "request has nothing new to generate. Try again tomorrow, or reuse the XML you "
        "already fetched via 'Paste XML' mode.\n\n"
        "If this is your first attempt today: in IB portal → Reports → Flex Queries → "
        "Edit your query → scroll to the bottom → tick 'Allow Web Service Access' → Save. "
        "If that's already set, IB's server may just be temporarily busy — wait 30–60 "
        "seconds and try again."
    )


def _fetch_statement(token: str, ref_code: str,
                     max_attempts: int = 15, delay: float = 5.0) -> str:
    """Step 2: poll until the report is ready.  Returns raw XML."""
    for attempt in range(max_attempts):
        resp = requests.get(
            _GET_URL,
            params={"q": ref_code, "t": token, "v": _API_VERSION},
            timeout=60,
        )
        resp.raise_for_status()
        text = resp.text.strip()

        # IB returns a short error XML while still generating
        if "Statement generation in progress" in text:
            if attempt < max_attempts - 1:
                time.sleep(delay)
                continue
            raise TimeoutError("IB Flex statement generation timed out.")

        # Check for error in response
        try:
            root = ET.fromstring(text)
            err  = root.findtext("ErrorMessage")
            if err:
                raise ValueError(f"IB Flex error: {err}")
        except ET.ParseError:
            pass   # not an error XML — it's the real report

        return text

    raise TimeoutError("IB Flex: maximum polling attempts reached.")


def fetch_flex_xml(token: str, query_id: str, progress_cb=None) -> str:
    """Fetch a Flex statement from IB.  Returns raw XML string.

    Automatically retries the SendRequest step up to 5 times if IB returns
    the transient error 1019 ("Statement could not be generated at this time").
    """
    if progress_cb:
        progress_cb("Requesting statement from Interactive Brokers…")
    ref_code = _request_statement(token, query_id, progress_cb=progress_cb)
    if progress_cb:
        progress_cb(f"Queued (ref {ref_code}). Waiting for IB to generate report…")
    xml_text = _fetch_statement(token, ref_code)
    if progress_cb:
        progress_cb("Statement received ✓")
    return xml_text


# ---------------------------------------------------------------------------
# XML parsing helpers
# ---------------------------------------------------------------------------

def _s(el: ET.Element, attr: str) -> str:
    return (el.get(attr) or "").strip()


def _f(el: ET.Element, attr: str, default: float = 0.0) -> float:
    try:
        return float(el.get(attr) or default)
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Trades parser
# ---------------------------------------------------------------------------

def _parse_trades(statement: ET.Element) -> tuple[list, int]:
    """Return (records, raw_element_count).

    Uses findall('.//Trade') so that trades nested inside <Order> wrappers
    (a common IB Flex variant) are captured as well as top-level children.

    Handles all known buySell attribute variants across IB API versions:
      Modern : "BUY", "SELL", "BUY (Ca.)", "SELL (Ca.)"
      Legacy : "BOT" (bought), "SLD" (sold), "B", "S"
    """
    records = []
    trades_el = statement.find("Trades")
    if trades_el is None:
        return records, 0

    # .//Trade finds both direct children and any depth (e.g. under <Order>)
    all_trade_els = trades_el.findall(".//Trade")

    for t in all_trade_els:
        asset_cat  = _s(t, "assetCategory")
        buy_sell   = _s(t, "buySell").upper()
        symbol     = _s(t, "symbol")
        name       = _s(t, "description")
        currency   = _s(t, "currency")
        fx_rate    = _f(t, "fxRateToBase", 1.0)
        quantity   = abs(_f(t, "quantity"))
        price      = abs(_f(t, "tradePrice"))
        net_cash   = _f(t, "netCash")               # signed, in trade ccy
        commission = abs(_f(t, "ibCommission"))
        trade_date = _parse_ib_date(_s(t, "tradeDate") or _s(t, "dateTime"))
        order_id   = _s(t, "ibOrderID") or _s(t, "tradeID") or _s(t, "ibExecID")
        isin       = _s(t, "isin")
        exchange   = _s(t, "listingExchange") or _s(t, "exchange")

        if not trade_date or quantity == 0:
            continue

        # Normalise all known buySell variants to Buy / Sell
        if buy_sell in ("BUY", "BOT", "B") or buy_sell.startswith("BUY"):
            action = "Buy"
        elif buy_sell in ("SELL", "SLD", "S") or buy_sell.startswith("SELL"):
            action = "Sell"
        else:
            # Unknown action (e.g. exercise, assignment) — skip
            continue

        # Back-calculate commission from netCash when ibCommission is not reported.
        # Both fields are in trade currency, so:
        #   Buy : abs(netCash) = qty × price + commission  →  comm = abs(netCash) − qty×price
        #   Sell: abs(netCash) = qty × price − commission  →  comm = qty×price − abs(netCash)
        # In both cases comm = abs(abs(netCash) − qty × price).
        # Only apply when ibCommission was truly absent/zero AND the arithmetic gives a
        # meaningful non-zero result (> 0.0001) to avoid storing rounding noise.
        if commission == 0 and quantity > 0 and price > 0:
            _back_comm = abs(abs(net_cash) - quantity * price)
            if _back_comm > 0.0001:
                commission = round(_back_comm, 4)

        total_eur = abs(net_cash) * fx_rate

        records.append({
            "record_type":    "investment",
            "source":         "IB",
            "desc":           f"{_IB_PREFIX}TRADE|{order_id}",
            "symbol":         symbol,
            "name":           name or symbol,
            "isin":           isin,
            "currency":       currency,
            "fx_rate":        fx_rate,
            "asset_category": asset_cat,
            "date":           trade_date,
            "action":         action,
            "quantity":       round(quantity, 6),
            "price":          round(price, 6),
            "commission":     round(commission, 4),
            "total_eur":      round(total_eur, 2),
            "exchange":       exchange,
        })

    return records, len(all_trade_els)


# ---------------------------------------------------------------------------
# Cash-transaction parser
# ---------------------------------------------------------------------------

def _parse_cash_transactions(statement: ET.Element) -> tuple[list, list, int]:
    """Return (inv_records, tx_records, raw_element_count).

    A Withholding Tax entry is merged into its matching Dividend / Payment in
    Lieu of Dividends entry (same symbol, isin, and dateTime — IB always posts
    a dividend and its withholding tax under that identical triple) into a
    single Investments row with Tax_Amount populated, rather than two
    disconnected rows with no link between them. Matching happens in a
    dedicated pass before any record is emitted, so it doesn't matter whether
    IB lists the tax line before or after the dividend line in the XML. A
    Withholding Tax entry with no matching dividend in this statement (e.g.
    tax on something else) still imports on its own as a MiscExp row.
    """
    inv_records: list[dict] = []
    tx_records:  list[dict] = []

    cash_el = statement.find("CashTransactions")
    if cash_el is None:
        return inv_records, tx_records, 0

    all_cash_els = cash_el.findall(".//CashTransaction")   # catches nested variants

    parsed: list[dict] = []
    for ct in all_cash_els:
        tx_type = _s(ct, "type")
        tx_date = _parse_ib_date(_s(ct, "dateTime"))
        amount  = _f(ct, "amount")
        if not tx_date or amount == 0:
            continue
        parsed.append({
            "tx_type":     tx_type,
            "tx_type_key": tx_type.lower(),
            "currency":    _s(ct, "currency"),
            "fx_rate":     _f(ct, "fxRateToBase", 1.0),
            "amount":      amount,
            "symbol":      _s(ct, "symbol") or _s(ct, "conid"),
            "name":        _s(ct, "description"),
            "isin":        _s(ct, "isin"),
            "date":        tx_date,
        })

    # Resolve all dividend/tax merges up front (see docstring for why this
    # can't be interleaved with emission below).
    tax_by_key: dict[tuple, list[int]] = {}
    for i, p in enumerate(parsed):
        if p["tx_type_key"] == _WITHHOLDING_TAX_TYPE:
            tax_by_key.setdefault((p["symbol"], p["isin"], p["date"]), []).append(i)

    tax_amount_for_dividend: dict[int, float] = {}   # dividend index -> merged tax (EUR/acc-cur)
    consumed_tax_idx: set[int] = set()
    for i, p in enumerate(parsed):
        if p["tx_type_key"] not in _DIVIDEND_CASH_TYPES:
            continue
        candidates = [j for j in tax_by_key.get((p["symbol"], p["isin"], p["date"]), [])
                      if j not in consumed_tax_idx]
        if candidates:
            tax_amount_for_dividend[i] = round(
                sum(parsed[j]["amount"] * parsed[j]["fx_rate"] for j in candidates), 2
            )
            consumed_tax_idx.update(candidates)

    for i, p in enumerate(parsed):
        tx_type, tx_type_key = p["tx_type"], p["tx_type_key"]
        currency, fx_rate, amount = p["currency"], p["fx_rate"], p["amount"]
        symbol, name, isin, tx_date = p["symbol"], p["name"], p["isin"], p["date"]

        if tx_type_key == _WITHHOLDING_TAX_TYPE and i in consumed_tax_idx:
            continue   # merged into its dividend above — don't emit separately

        amount_eur = amount * fx_rate
        key = f"{_IB_PREFIX}CASH|{tx_date}|{tx_type}|{symbol}|{amount}"

        if tx_type_key in _CASH_INV_MAP:
            action = _CASH_INV_MAP[tx_type_key]
            is_account_level = tx_type_key in _ACCOUNT_LEVEL_CASH_TYPES
            inv_records.append({
                "record_type":    "investment",
                "source":         "IB",
                "desc":           key,
                "symbol":         _ib_cash_symbol(currency) if is_account_level else (symbol or "IB-CASH"),
                "name":           _ib_cash_name(currency) if is_account_level else name,
                "isin":           "" if is_account_level else isin,
                "currency":       currency,
                "fx_rate":        fx_rate,
                "asset_category": "STK",
                "date":           tx_date,
                "action":         action,
                "quantity":       1.0,
                "price":          round(abs(amount_eur), 4),
                "commission":     0.0,
                "total_eur":      round(abs(amount_eur), 2),
                "tax_amount_eur": tax_amount_for_dividend.get(i),
                "exchange":       "",
            })

        elif tx_type_key == "deposits/withdrawals":
            tx_records.append({
                "record_type": "transaction",
                "source":      "IB",
                "desc":        key,
                "date":        tx_date,
                "amount":      round(amount_eur, 2),
                "description": f"IB {'Deposit' if amount > 0 else 'Withdrawal'}: {name or tx_type}",
                "currency":    currency,
            })

        elif tx_type_key in ("fees paid", "other fees", "commission adjustments"):
            tx_records.append({
                "record_type": "transaction",
                "source":      "IB",
                "desc":        key,
                "date":        tx_date,
                "amount":      round(amount_eur, 2),
                "description": f"IB Fee: {name or tx_type}",
                "currency":    currency,
            })

    return inv_records, tx_records, len(all_cash_els)


# ---------------------------------------------------------------------------
# Top-level parse entry point
# ---------------------------------------------------------------------------

def parse_flex_xml(xml_text: str) -> tuple[list, list, dict]:
    """Parse IB Flex XML into (inv_records, tx_records, meta).

    meta keys:
        account_id, from_date, to_date, base_currency
        diag_sections     — list of top-level section names in the XML
        diag_trade_els    — raw <Trade> element count (before filtering)
        diag_cash_els     — raw <CashTransaction> element count (before filtering)
    """
    root = ET.fromstring(xml_text)
    stmts = root.find("FlexStatements")
    if stmts is None:
        raise ValueError("No <FlexStatements> element found in the Flex XML.")

    all_inv: list[dict] = []
    all_tx:  list[dict] = []
    meta:    dict       = {}

    for stmt in stmts.findall("FlexStatement"):
        # Collect names of all direct-child sections for diagnostics
        section_names = [child.tag for child in stmt]

        trade_records, trade_el_count = _parse_trades(stmt)
        inv_cash, tx_cash, cash_el_count = _parse_cash_transactions(stmt)

        all_inv.extend(trade_records)
        all_inv.extend(inv_cash)
        all_tx.extend(tx_cash)

        meta = {
            "account_id":      _s(stmt, "accountId"),
            "from_date":       _parse_ib_date(_s(stmt, "fromDate")),
            "to_date":         _parse_ib_date(_s(stmt, "toDate")),
            "base_currency":   _s(stmt, "currency") or "EUR",
            # Diagnostics — shown in UI when no importable records are found
            "diag_sections":   section_names,
            "diag_trade_els":  trade_el_count,
            "diag_cash_els":   cash_el_count,
        }

    return all_inv, all_tx, meta


# ---------------------------------------------------------------------------
# Preview helpers (return DataFrames for the UI review table)
# ---------------------------------------------------------------------------

def investments_preview_df(records: list) -> "pd.DataFrame":       # noqa: F821
    import pandas as pd
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    cols = ["date", "action", "symbol", "name", "quantity",
            "price", "total_eur", "currency", "commission",
            "asset_category", "desc"]
    return df[[c for c in cols if c in df.columns]].copy()


def transactions_preview_df(records: list) -> "pd.DataFrame":      # noqa: F821
    import pandas as pd
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    return df[["date", "description", "amount", "currency"]].copy()


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _sec_type(asset_category: str) -> str:
    return _ASSET_TO_SECTYPE.get(asset_category.upper(), "Stock")


def _get_or_create_security(cur, symbol: str, name: str,
                             currency: str, asset_category: str,
                             isin: str = "",
                             _cached_mappings: dict | None = None) -> tuple[int, str]:
    """Return (securities_id, match_type).

    Match priority:
      0. Saved mapping in import_security_mappings (user-defined override,
         keyed by ISIN when present, otherwise by security name)
      1. ISIN  — most reliable; IB always includes it for equities/ETFs
      2. Exact name match
      3. Create new security

    Pass *_cached_mappings* (from ``get_security_mappings("Interactive Brokers")``)
    to avoid a DB round-trip per record when importing many records in a loop.
    """
    # 0. Saved mapping
    if _cached_mappings is None:
        from database.queries import get_security_mappings as _get_map
        _cached_mappings = _get_map("Interactive Brokers")
    _map_key = isin if isin else name
    if _map_key and _map_key in _cached_mappings:
        sec_id = _cached_mappings[_map_key]
        cur.execute(
            "SELECT Securities_Name FROM Securities WHERE Securities_Id = %s",
            (sec_id,),
        )
        row = cur.fetchone()
        return sec_id, f"mapped:{row[0] if row else _map_key}"

    # 1. ISIN match
    if isin:
        cur.execute(
            "SELECT Securities_Id, Securities_Name FROM Securities "
            "WHERE ISIN = %s LIMIT 1",
            (isin,),
        )
        row = cur.fetchone()
        if row:
            return row[0], f"isin:{row[1]}"

    # 2. Exact name match
    cur.execute(
        "SELECT Securities_Id FROM Securities WHERE Securities_Name = %s LIMIT 1",
        (name,),
    )
    row = cur.fetchone()
    if row:
        return row[0], "name"

    # 3. Create
    sec_type = _sec_type(asset_category)
    ticker   = symbol or name[:30]
    cur.execute(
        """INSERT INTO Securities
               (Ticker, Securities_Name, Securities_Type, Currencies_Id, ISIN)
           VALUES (%s, %s, %s,
                  (SELECT Currencies_Id FROM Currencies
                   WHERE Currencies_ShortName = %s LIMIT 1),
                  %s)
           RETURNING Securities_Id""",
        (ticker, name, sec_type, currency or "EUR", isin or None),
    )
    return cur.fetchone()[0], "new"


def _inv_exists(cur, acc_id: int, desc: str) -> bool:
    cur.execute(
        "SELECT 1 FROM Investments WHERE Accounts_Id = %s AND Description = %s LIMIT 1",
        (acc_id, desc),
    )
    return cur.fetchone() is not None


def _tx_exists(cur, acc_id: int, desc: str) -> bool:
    cur.execute(
        "SELECT 1 FROM Transactions WHERE Accounts_Id = %s AND Description = %s LIMIT 1",
        (acc_id, desc),
    )
    return cur.fetchone() is not None


# ---------------------------------------------------------------------------
# Pre-import reconciliation helpers (read-only — used by the UI preview)
# ---------------------------------------------------------------------------

def check_existing_records(
    inv_records: list, tx_records: list, account_id: int,
    cash_account_id: int | None = None,
) -> tuple[set, set]:
    """Return (existing_inv_descs, existing_tx_descs) — description keys that
    already exist in the DB.

    Investment dedup is intentionally checked across ALL accounts because IB
    order IDs / cash-tx keys are globally unique — the same record can't be
    imported twice regardless of which account is selected in the UI.

    Transaction dedup is scoped to the target cash account.
    """
    from database.connection import get_connection as _get_conn
    conn = _get_conn()
    cur  = conn.cursor()
    try:
        existing_inv: set[str] = set()
        existing_tx:  set[str] = set()

        inv_descs = [r["desc"] for r in inv_records]
        if inv_descs:
            placeholders = ",".join(["%s"] * len(inv_descs))
            # No Accounts_Id filter — IB order IDs are globally unique
            cur.execute(
                f"SELECT Description FROM Investments WHERE Description IN ({placeholders})",
                inv_descs,
            )
            existing_inv = {row[0] for row in cur.fetchall()}

        tx_check_id = cash_account_id if cash_account_id else account_id
        tx_descs = [r["desc"] for r in tx_records]
        if tx_descs:
            placeholders = ",".join(["%s"] * len(tx_descs))
            cur.execute(
                f"SELECT Description FROM Transactions "
                f"WHERE Accounts_Id = %s AND Description IN ({placeholders})",
                [tx_check_id] + tx_descs,
            )
            existing_tx = {row[0] for row in cur.fetchall()}

        return existing_inv, existing_tx
    finally:
        cur.close()
        conn.close()


def check_fuzzy_duplicates(
    inv_records: list, tx_records: list, account_id: int,
    cash_account_id: int | None = None,
) -> tuple[set, set]:
    """Secondary duplicate detection using date + action + quantity/amount.

    Catches records that already exist with a *different* description key —
    e.g. manually entered or imported from a bank statement with description
    "Transfer Money" instead of the IB dedup key format.

    Returns (fuzzy_inv_descs, fuzzy_tx_descs) — desc keys of records whose
    content appears to already be in the DB.
    """
    from database.connection import get_connection as _get_conn
    conn = _get_conn()
    cur  = conn.cursor()
    fuzzy_inv: set[str] = set()
    fuzzy_tx:  set[str] = set()
    try:
        for rec in inv_records:
            cur.execute(
                """SELECT 1 FROM Investments
                   WHERE Date            = %s
                     AND Action::text    ILIKE %s
                     AND ABS(Quantity - %s) < 0.001
                   LIMIT 1""",
                (rec["date"], rec["action"], rec["quantity"]),
            )
            if cur.fetchone():
                fuzzy_inv.add(rec["desc"])

        tx_check_id = cash_account_id if cash_account_id else account_id
        for rec in tx_records:
            cur.execute(
                """SELECT 1 FROM Transactions
                   WHERE Accounts_Id          = %s
                     AND Date                 = %s
                     AND ABS(Total_Amount - %s) < 0.01
                   LIMIT 1""",
                (tx_check_id, rec["date"], rec["amount"]),
            )
            if cur.fetchone():
                fuzzy_tx.add(rec["desc"])

        return fuzzy_inv, fuzzy_tx
    finally:
        cur.close()
        conn.close()


def preview_security_matches(inv_records: list) -> dict[str, tuple[int | None, str]]:
    """Return {isin_or_name → (securities_id | None, match_type)} for each
    unique security in *inv_records*.

    match_type: 'mapped:<name>' | 'isin:<name>' | 'name' | 'new'
    Used by the UI to show which securities will be reused vs created.
    """
    from database.connection import get_connection as _get_conn
    from database.queries import get_security_mappings as _get_map
    conn = _get_conn()
    cur  = conn.cursor()
    result: dict[str, tuple[int | None, str]] = {}

    # Load user-defined mappings once
    saved_mappings = _get_map("Interactive Brokers")   # {isin_or_name → sec_id}

    try:
        seen: set[str] = set()
        for rec in inv_records:
            isin   = rec.get("isin", "") or ""
            name   = rec.get("name", "") or ""
            key    = isin if isin else name
            if key in seen:
                continue
            seen.add(key)

            # 0. Saved mapping
            if key in saved_mappings:
                sec_id = saved_mappings[key]
                cur.execute(
                    "SELECT Securities_Name FROM Securities WHERE Securities_Id = %s",
                    (sec_id,),
                )
                row = cur.fetchone()
                result[key] = (sec_id, f"mapped:{row[0] if row else key}")
                continue

            # 1. Try ISIN
            if isin:
                cur.execute(
                    "SELECT Securities_Id, Securities_Name FROM Securities "
                    "WHERE ISIN = %s LIMIT 1", (isin,),
                )
                row = cur.fetchone()
                if row:
                    result[key] = (row[0], f"isin:{row[1]}")
                    continue

            # 2. Try name
            if name:
                cur.execute(
                    "SELECT Securities_Id FROM Securities "
                    "WHERE Securities_Name = %s LIMIT 1", (name,),
                )
                row = cur.fetchone()
                if row:
                    result[key] = (row[0], "name")
                    continue

            result[key] = (None, "new")
    finally:
        cur.close()
        conn.close()
    return result


def _quantity_held_as_of(cur, account_id: int, securities_id: int, as_of_date) -> float:
    """Net signed quantity held in (account_id, securities_id) as of as_of_date
    (inclusive). Used to give a Dividend row its real per-share basis instead
    of a placeholder quantity of 1 — sees this same transaction's own
    already-executed (not yet committed) INSERTs too, which is why this must
    be called after Trades have been inserted (see run_import: trade_records
    always precede cash-derived records in inv_records, so by the time a
    Dividend row is processed, every trade dated on/before it — from this
    import or a previous one — is already visible to this query)."""
    cur.execute("""
        SELECT COALESCE(SUM(
            CASE WHEN Action IN ('Buy', 'Reinvest', 'ShrIn') THEN Quantity
                 WHEN Action IN ('Sell', 'ShrOut') THEN -Quantity
                 ELSE 0 END
        ), 0)
        FROM Investments
        WHERE Accounts_Id = %s AND Securities_Id = %s AND Date <= %s
    """, (account_id, securities_id, as_of_date))
    return float(cur.fetchone()[0] or 0)


def run_import(
    inv_records: list,
    tx_records:  list,
    account_id:  int,
    cash_account_id: int | None = None,
    replace_mode: bool = False,
    progress_cb=None,
) -> dict:
    """Insert parsed IB records into the database."""
    from database.connection import get_connection
    from database.crud import update_holdings, update_accounts_balances, update_investment_balances

    conn = get_connection()
    cur  = conn.cursor()
    counts = {
        "investments": 0, "investments_skip": 0,
        "transactions": 0, "transactions_skip": 0,
    }

    try:
        tx_dest_id = cash_account_id if cash_account_id else account_id
        if replace_mode:
            cur.execute("DELETE FROM Investments WHERE Accounts_Id = %s AND Description LIKE %s",
                        (account_id, f"{_IB_PREFIX}%"))
            # Always clean the margin account (handles prior imports without a cash account)
            cur.execute("DELETE FROM Transactions WHERE Accounts_Id = %s AND Description LIKE %s",
                        (account_id, f"IB %"))
            # Also clean the cash account when configured separately
            if cash_account_id and cash_account_id != account_id:
                cur.execute("DELETE FROM Transactions WHERE Accounts_Id = %s AND Description LIKE %s",
                            (cash_account_id, f"IB %"))

        total = len(inv_records) + len(tx_records)
        done  = 0

        # Load user-defined security mappings once to avoid a DB call per record
        from database.queries import get_security_mappings as _get_sec_map
        _ib_mappings = _get_sec_map("Interactive Brokers")

        for rec in inv_records:
            sec_id, _match = _get_or_create_security(
                cur, rec["symbol"], rec["name"],
                rec.get("currency", "EUR"), rec.get("asset_category", "STK"),
                isin=rec.get("isin", ""),
                _cached_mappings=_ib_mappings,
            )
            if not replace_mode and _inv_exists(cur, account_id, rec["desc"]):
                counts["investments_skip"] += 1
            else:
                # IB supplies fxRateToBase (sec_cur → acc_cur) directly in the record.
                _ib_fx       = float(rec.get("fx_rate") or 1.0)
                _ib_sec_amt  = (round(rec["total_eur"] / _ib_fx, 18)
                                if _ib_fx else rec["total_eur"])

                _quantity, _price = rec["quantity"], rec["price"]
                if rec["action"] == "Dividend":
                    # The cash-transaction parser has no notion of how many
                    # shares were actually held — replace its quantity=1
                    # placeholder with the real position size as of the
                    # dividend date, and re-derive a true per-share amount
                    # accordingly. Total_Amount_AccCur (below) is what
                    # actually drives every downstream P&L/cash-flow
                    # calculation for this row — see api/routers/reports.py,
                    # which only falls back to Quantity * Price_Per_Share when
                    # Total_Amount_AccCur is NULL/zero — so this is purely
                    # cosmetic/informational and can't skew any report.
                    _held = _quantity_held_as_of(cur, account_id, sec_id, rec["date"])
                    if _held > 0:
                        _quantity = _held
                        _price = round(rec["total_eur"] / _held, 6)

                cur.execute(
                    """INSERT INTO Investments
                           (Accounts_Id, Securities_Id, Date, Action, Quantity,
                            Price_Per_Share, Commission,
                            Total_Amount_AccCur, Total_Amount_SecCur, FX_Rate,
                            Description, Tax_Amount)
                       VALUES (%s, %s, %s, %s::investments_action, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (account_id, sec_id, rec["date"], rec["action"],
                     _quantity, _price,
                     rec.get("commission") or None,
                     rec["total_eur"], _ib_sec_amt, _ib_fx,
                     rec["desc"], rec.get("tax_amount_eur")),
                )
                counts["investments"] += 1
            done += 1
            if progress_cb and done % 25 == 0:
                progress_cb(done / total)

        for rec in tx_records:
            if not replace_mode and _tx_exists(cur, tx_dest_id, rec["desc"]):
                counts["transactions_skip"] += 1
            else:
                cur.execute(
                    """INSERT INTO Transactions
                           (Accounts_Id, Date, Total_Amount, Description, Cleared)
                       VALUES (%s, %s, %s, %s, TRUE)""",
                    (tx_dest_id, rec["date"], rec["amount"], rec["desc"]),
                )
                counts["transactions"] += 1
            done += 1
            if progress_cb and done % 25 == 0:
                progress_cb(done / total)

        conn.commit()
        update_holdings()
        update_accounts_balances()
        update_investment_balances()

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    # Auto-create linked cash transactions when the investment account has a
    # configured linked cash account (look up from DB to be authoritative).
    from database.crud import create_linked_cash_transactions_for_unlinked, get_linked_account_id
    _ib_linked = get_linked_account_id(account_id)
    if _ib_linked:
        create_linked_cash_transactions_for_unlinked(account_id, _ib_linked)
        update_accounts_balances(_ib_linked)
        update_investment_balances()

    return counts
