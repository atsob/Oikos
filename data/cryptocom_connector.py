"""Crypto.com Exchange API v1 Connector.

Authentication
--------------
Create API keys at: Crypto.com Exchange → Settings → API Management
Required permissions: View (read-only is sufficient)

What gets imported
------------------
Transaction type            → Record type      Action
--------------------------  ----------------   --------------------------------
FILLED BUY order            Investment         Buy
FILLED SELL order           Investment         Sell
Crypto deposit              Investment         ShrIn  (crypto arrived)
Crypto withdrawal           Investment         ShrOut (crypto left)
Fiat deposit                CashIn  (→ cash account or brokerage)
Fiat withdrawal             CashOut (→ cash account or brokerage)

Quote-currency EUR conversion
-----------------------------
EUR  quote pairs (BTC_EUR)  → exact EUR value from cumulative_value
USDT/USDC pairs (BTC_USDT) → approximate 1:1 with EUR (flagged in description)
Other pairs                 → cumulative_value recorded as-is (in quote currency)

Dedup prefix: CDC|
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CDC_API_BASE = "https://api.crypto.com/exchange/v1"
_CDC_PREFIX   = "CDC|"

# Fiat / stablecoin codes — wallets holding these produce Transaction records.
_FIAT_CODES = {
    "EUR", "USD", "GBP", "CHF", "CAD", "AUD", "JPY", "SGD",
    "HKD", "NOK", "SEK", "DKK", "NZD", "MXN", "BRL", "CZK",
    "PLN", "HUF", "RON", "BGN", "TRY",
    # Stablecoins treated as cash
    "USDC", "USDT", "DAI", "TUSD", "USDP", "GUSD", "FRAX",
    "EURT", "EURS",
}

# Stablecoins treated as ≈1:1 EUR for total_eur calculation
_NEAR_EUR_STABLE = {"USDC", "USDT", "DAI", "TUSD", "USDP", "GUSD", "FRAX", "EURT", "EURS"}

# Well-known crypto names
_CRYPTO_NAMES: dict[str, str] = {
    "BTC":   "Bitcoin",
    "ETH":   "Ethereum",
    "CRO":   "Cronos",
    "SOL":   "Solana",
    "ADA":   "Cardano",
    "DOT":   "Polkadot",
    "AVAX":  "Avalanche",
    "MATIC": "Polygon",
    "POL":   "Polygon (POL)",
    "LINK":  "Chainlink",
    "XRP":   "XRP",
    "LTC":   "Litecoin",
    "BCH":   "Bitcoin Cash",
    "ATOM":  "Cosmos",
    "ALGO":  "Algorand",
    "XLM":   "Stellar",
    "DOGE":  "Dogecoin",
    "SHIB":  "Shiba Inu",
    "UNI":   "Uniswap",
    "AAVE":  "Aave",
    "COMP":  "Compound",
    "MKR":   "Maker",
    "SNX":   "Synthetix",
    "GRT":   "The Graph",
    "CRV":   "Curve DAO",
    "FIL":   "Filecoin",
    "ICP":   "Internet Computer",
    "NEAR":  "NEAR Protocol",
    "FTM":   "Fantom",
    "HBAR":  "Hedera",
    "SAND":  "The Sandbox",
    "MANA":  "Decentraland",
    "AXS":   "Axie Infinity",
    "VET":   "VeChain",
    "EOS":   "EOS",
    "XTZ":   "Tezos",
    "WBTC":  "Wrapped Bitcoin",
    "STETH": "Staked Ether",
    "INJ":   "Injective",
    "SUI":   "Sui",
    "APT":   "Aptos",
    "ARB":   "Arbitrum",
    "OP":    "Optimism",
    "ZEC":   "Zcash",
    "XMR":   "Monero",
    "DASH":  "Dash",
    "ETC":   "Ethereum Classic",
    "BAT":   "Basic Attention Token",
}

# Completed status codes for deposits and withdrawals
_DEPOSIT_COMPLETED    = {5}   # 0=Pending,1=Processing,2=Rejected,3=PaymentInProgress,4=Failed,5=Completed,6=Cancelled
_WITHDRAWAL_COMPLETED = {5}   # 1=Pending,2=Processing,3=Rejected,4=PaymentInProgress,5=Completed,6=Cancelled


# ---------------------------------------------------------------------------
# Authentication & request helpers
# ---------------------------------------------------------------------------

def _build_param_str(params: dict) -> str:
    """Concatenate sorted param keys + their string values."""
    return "".join(k + str(v) for k, v in sorted(params.items()))


def _sign_body(api_key: str, api_secret: str, method: str, params: dict) -> dict:
    """Build and sign a Crypto.com Exchange API v1 request body."""
    nonce      = int(time.time() * 1000)
    request_id = nonce
    param_str  = _build_param_str(params)
    sig_input  = method + str(request_id) + api_key + param_str + str(nonce)
    sig        = _hmac.new(
        api_secret.encode("utf-8"),
        sig_input.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "id":      request_id,
        "method":  method,
        "api_key": api_key,
        "params":  params,
        "nonce":   nonce,
        "sig":     sig,
    }


def _cdc_request(api_key: str, api_secret: str, method: str,
                 params: dict | None = None) -> dict:
    """Make one authenticated POST request and return the ``result`` dict."""
    params = params or {}
    body   = _sign_body(api_key, api_secret, method, params)
    url    = f"{_CDC_API_BASE}/{method}"
    data   = json.dumps(body).encode("utf-8")
    req    = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode("utf-8", errors="replace")
        raise ValueError(
            f"Crypto.com API HTTP {e.code}: {body_txt[:400]}"
        ) from None
    except urllib.error.URLError as e:
        raise ValueError(f"Network error contacting Crypto.com: {e.reason}") from None

    code = raw.get("code", -1)
    if code != 0:
        msg = raw.get("message") or raw.get("msg") or str(raw)
        raise ValueError(f"Crypto.com API error (code {code}): {msg}")

    return raw.get("result", {})


def _cdc_paginate(
    api_key:    str,
    api_secret: str,
    method:     str,
    result_key: str,
    base_params: dict | None = None,
    page_size:  int = 200,
    max_pages:  int = 500,
    progress_cb=None,
) -> list[dict]:
    """Collect all pages for a paginated Crypto.com endpoint (page-based)."""
    base_params = dict(base_params or {})
    all_items:   list[dict] = []

    for page in range(max_pages):
        params = {**base_params, "page_size": page_size, "page": page}
        result = _cdc_request(api_key, api_secret, method, params)
        items  = result.get(result_key) or []
        all_items.extend(items)

        if progress_cb:
            progress_cb(len(all_items))

        total_pages = result.get("total_page_num", 1)
        if page + 1 >= int(total_pages):
            break

    return all_items


# ---------------------------------------------------------------------------
# Public fetch functions
# ---------------------------------------------------------------------------

def test_connection(api_key: str, api_secret: str) -> list[dict]:
    """Verify credentials and return list of non-zero balances."""
    result   = _cdc_request(api_key, api_secret, "private/get-account-summary")
    accounts = result.get("accounts") or []
    return [
        {
            "currency":  a.get("currency", "?"),
            "balance":   float(a.get("balance",   0) or 0),
            "available": float(a.get("available", 0) or 0),
            "order":     float(a.get("order",     0) or 0),
            "is_fiat":   str(a.get("currency", "")).upper() in _FIAT_CODES,
        }
        for a in accounts
        if float(a.get("balance", 0) or 0) > 0
    ]


def fetch_all_transactions(
    api_key:    str,
    api_secret: str,
    start_date: "date | None" = None,
    end_date:   "date | None" = None,
    progress_cb=None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Fetch orders, deposits, and withdrawals.

    Returns (orders, deposits, withdrawals) — each a flat list of raw API dicts.
    Date filtering is applied client-side.
    """
    start_ts = int(datetime(start_date.year, start_date.month, start_date.day,
                             tzinfo=timezone.utc).timestamp() * 1000) if start_date else None
    end_ts   = int(datetime(end_date.year,   end_date.month,   end_date.day,
                             23, 59, 59, tzinfo=timezone.utc).timestamp() * 1000) if end_date else None

    base: dict = {}
    if start_ts:
        base["start_ts"] = start_ts
    if end_ts:
        base["end_ts"] = end_ts

    def _status(label: str, n: int):
        if progress_cb:
            progress_cb(f"{label} — {n} fetched so far…")

    # Orders (filled trades)
    if progress_cb:
        progress_cb("Fetching order history…")
    orders = _cdc_paginate(
        api_key, api_secret,
        method     = "private/get-order-history",
        result_key = "order_list",
        base_params = base,
        progress_cb = lambda n: _status("Orders", n),
    )
    orders = [o for o in orders if str(o.get("status", "")).upper() == "FILLED"]

    # Deposits
    if progress_cb:
        progress_cb("Fetching deposit history…")
    deposits = _cdc_paginate(
        api_key, api_secret,
        method     = "private/get-deposit-history",
        result_key = "deposit_list",
        base_params = base,
        progress_cb = lambda n: _status("Deposits", n),
    )
    deposits = [d for d in deposits
                if int(d.get("status", -1)) in _DEPOSIT_COMPLETED]

    # Withdrawals
    if progress_cb:
        progress_cb("Fetching withdrawal history…")
    withdrawals = _cdc_paginate(
        api_key, api_secret,
        method     = "private/get-withdrawal-history",
        result_key = "withdrawal_list",
        base_params = base,
        progress_cb = lambda n: _status("Withdrawals", n),
    )
    withdrawals = [w for w in withdrawals
                   if int(w.get("status", -1)) in _WITHDRAWAL_COMPLETED]

    return orders, deposits, withdrawals


# ---------------------------------------------------------------------------
# Record builders
# ---------------------------------------------------------------------------

def _ts_to_date(ts_ms) -> "Optional[date]":
    """Convert a millisecond timestamp to a date."""
    try:
        return datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc).date()
    except (TypeError, ValueError, OSError):
        return None


def _f(val, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _crypto_name(code: str) -> str:
    return _CRYPTO_NAMES.get(code.upper(), f"{code} (Crypto)")


def _quote_to_eur(value: float, quote: str) -> tuple[float, str]:
    """Convert a value in quote currency to EUR equivalent.

    Returns (eur_value, note) where note is empty string for exact conversion
    or a human-readable approximation note.
    """
    q = quote.upper()
    if q == "EUR":
        return value, ""
    if q in _NEAR_EUR_STABLE:
        return value, f"≈EUR (1:1 from {q})"
    # For any other quote currency (e.g. BTC), we cannot convert without live FX.
    # Return as-is and note it clearly.
    return value, f"in {q} (no EUR rate)"


def build_cryptocom_records(
    orders:      list[dict],
    deposits:    list[dict],
    withdrawals: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Convert raw API lists to (inv_records, tx_records).

    inv_records fields (same schema as Coinbase connector):
        record_type, source, desc, symbol, name, isin, currency,
        asset_category, date, action, quantity, price, commission, total_eur

    tx_records fields:
        record_type, source, desc, date, amount, description, currency
    """
    inv_records: list[dict] = []
    tx_records:  list[dict] = []

    # ── Orders (filled trades) ────────────────────────────────────────────────
    for o in orders:
        instr  = str(o.get("instrument_name", ""))
        if "_" not in instr:
            continue
        base, quote = instr.split("_", 1)
        base  = base.upper()
        quote = quote.upper()

        dt = _ts_to_date(o.get("create_time"))
        if dt is None:
            continue

        side  = str(o.get("side", "")).upper()
        qty   = _f(o.get("cumulative_quantity"))
        value = _f(o.get("cumulative_value"))   # in quote currency
        fee   = _f(o.get("cumulative_fee"))
        fee_ccy = str(o.get("fee_currency", quote)).upper()
        avg_px  = _f(o.get("avg_price"))

        if qty <= 0:
            continue

        eur_val, fx_note = _quote_to_eur(value, quote)
        order_id = str(o.get("order_id") or o.get("client_oid") or "")
        key = f"{_CDC_PREFIX}{base}_{quote}|{order_id}"

        # Price per unit in EUR equivalent
        price_eur = eur_val / qty if qty > 0 else avg_px

        # Commission in EUR equivalent (best effort)
        if fee_ccy == quote:
            commission_eur, _ = _quote_to_eur(fee, quote)
        elif fee_ccy == base:
            # Fee deducted in crypto — convert at avg price
            commission_eur, _ = _quote_to_eur(fee * avg_px if avg_px > 0 else 0.0, quote)
        else:
            commission_eur = 0.0

        desc_note = f" [{fx_note}]" if fx_note else ""
        action = "Buy" if side == "BUY" else "Sell"

        inv_records.append({
            "record_type":    "investment",
            "source":         "Crypto.com",
            "desc":           key,
            "symbol":         base,
            "name":           _crypto_name(base),
            "isin":           "",
            "currency":       "EUR",
            "asset_category": "CRYPTO",
            "date":           dt,
            "action":         action,
            "quantity":       round(qty, 8),
            "price":          round(price_eur, 6),
            "commission":     round(commission_eur, 6),
            "total_eur":      round(eur_val, 4),
            "_fx_note":       fx_note,     # internal — not stored in DB
        })

    # ── Deposits ──────────────────────────────────────────────────────────────
    for d in deposits:
        ccy = str(d.get("currency", "")).upper()
        amt = _f(d.get("amount"))
        fee = _f(d.get("fee"))
        dt  = _ts_to_date(d.get("create_time"))
        if dt is None or amt <= 0:
            continue
        dep_id = str(d.get("id") or d.get("deposit_id") or "")
        key    = f"{_CDC_PREFIX}DEP|{ccy}|{dep_id}"

        if ccy in _FIAT_CODES:
            # Fiat deposit → CashIn (will be routed to cash account)
            eur_val, _ = _quote_to_eur(amt, ccy)
            inv_records.append({
                "record_type":    "investment",
                "source":         "Crypto.com",
                "desc":           key,
                "symbol":         None,
                "name":           None,
                "isin":           "",
                "currency":       "EUR",
                "asset_category": None,
                "date":           dt,
                "action":         "CashIn",
                "quantity":       0.0,
                "price":          0.0,
                "commission":     0.0,
                "total_eur":      round(eur_val, 4),
                "_fx_note":       "",
            })
        else:
            # Crypto deposit → ShrIn
            eur_val, fx_note = _quote_to_eur(amt, "EUR")   # no price info — record qty only
            inv_records.append({
                "record_type":    "investment",
                "source":         "Crypto.com",
                "desc":           key,
                "symbol":         ccy,
                "name":           _crypto_name(ccy),
                "isin":           "",
                "currency":       "EUR",
                "asset_category": "CRYPTO",
                "date":           dt,
                "action":         "ShrIn",
                "quantity":       round(amt, 8),
                "price":          0.0,
                "commission":     round(fee, 6),
                "total_eur":      0.0,    # unknown EUR value at deposit time
                "_fx_note":       "no EUR value at deposit",
            })

    # ── Withdrawals ───────────────────────────────────────────────────────────
    for w in withdrawals:
        ccy = str(w.get("currency", "")).upper()
        amt = _f(w.get("amount"))
        fee = _f(w.get("fee"))
        dt  = _ts_to_date(w.get("create_time"))
        if dt is None or amt <= 0:
            continue
        wdraw_id = str(w.get("id") or w.get("withdrawal_id") or "")
        key      = f"{_CDC_PREFIX}WDR|{ccy}|{wdraw_id}"

        if ccy in _FIAT_CODES:
            # Fiat withdrawal → CashOut (routed to cash account)
            eur_val, _ = _quote_to_eur(amt, ccy)
            inv_records.append({
                "record_type":    "investment",
                "source":         "Crypto.com",
                "desc":           key,
                "symbol":         None,
                "name":           None,
                "isin":           "",
                "currency":       "EUR",
                "asset_category": None,
                "date":           dt,
                "action":         "CashOut",
                "quantity":       0.0,
                "price":          0.0,
                "commission":     0.0,
                "total_eur":      round(eur_val, 4),
                "_fx_note":       "",
            })
        else:
            # Crypto withdrawal → ShrOut
            inv_records.append({
                "record_type":    "investment",
                "source":         "Crypto.com",
                "desc":           key,
                "symbol":         ccy,
                "name":           _crypto_name(ccy),
                "isin":           "",
                "currency":       "EUR",
                "asset_category": "CRYPTO",
                "date":           dt,
                "action":         "ShrOut",
                "quantity":       round(amt, 8),
                "price":          0.0,
                "commission":     round(fee, 6),
                "total_eur":      0.0,    # unknown EUR value at withdrawal time
                "_fx_note":       "no EUR value at withdrawal",
            })

    return inv_records, tx_records


# ---------------------------------------------------------------------------
# Reconciliation helpers
# ---------------------------------------------------------------------------

def check_existing_records(
    inv_records:     list[dict],
    tx_records:      list[dict],
    account_id:      int,
    cash_account_id: int | None = None,
) -> tuple[set[str], set[str]]:
    """Return (existing_inv_descs, existing_tx_descs)."""
    from database.connection import get_connection as _gc
    conn = _gc()
    cur  = conn.cursor()
    try:
        existing_inv: set[str] = set()
        existing_tx:  set[str] = set()

        crypto_inv = [r for r in inv_records if r.get("symbol")]
        cash_flow  = [r for r in inv_records if not r.get("symbol")]

        if crypto_inv:
            descs = [r["desc"] for r in crypto_inv]
            ph    = ",".join(["%s"] * len(descs))
            cur.execute(
                f"SELECT Description FROM Investments "
                f"WHERE Accounts_Id = %s AND Description IN ({ph})",
                [account_id] + descs,
            )
            existing_inv = {row[0] for row in cur.fetchall()}

        if cash_flow:
            descs = [r["desc"] for r in cash_flow]
            ph    = ",".join(["%s"] * len(descs))
            if cash_account_id:
                cur.execute(
                    f"SELECT Description FROM Transactions "
                    f"WHERE Accounts_Id = %s AND Description IN ({ph})",
                    [cash_account_id] + descs,
                )
                existing_tx |= {row[0] for row in cur.fetchall()}
            else:
                cur.execute(
                    f"SELECT Description FROM Investments "
                    f"WHERE Accounts_Id = %s AND Description IN ({ph})",
                    [account_id] + descs,
                )
                existing_inv |= {row[0] for row in cur.fetchall()}

        if tx_records:
            target_id = cash_account_id if cash_account_id else account_id
            descs = [r["desc"] for r in tx_records]
            ph    = ",".join(["%s"] * len(descs))
            cur.execute(
                f"SELECT Description FROM Transactions "
                f"WHERE Accounts_Id = %s AND Description IN ({ph})",
                [target_id] + descs,
            )
            existing_tx |= {row[0] for row in cur.fetchall()}

        return existing_inv, existing_tx
    finally:
        cur.close()
        conn.close()


def check_fuzzy_duplicates(
    inv_records:     list[dict],
    tx_records:      list[dict],
    account_id:      int,
    cash_account_id: int | None = None,
) -> tuple[set[str], set[str]]:
    """Return (fuzzy_inv_descs, fuzzy_tx_descs) for likely-duplicate detection."""
    from database.connection import get_connection as _gc
    conn = _gc()
    cur  = conn.cursor()
    fuzzy_inv: set[str] = set()
    fuzzy_tx:  set[str] = set()
    try:
        for rec in inv_records:
            is_cash_flow = rec["action"] in ("CashIn", "CashOut") and not rec.get("symbol")
            if is_cash_flow and cash_account_id:
                eur_amt = rec["total_eur"]
                cur.execute(
                    """SELECT 1 FROM Transactions
                       WHERE Accounts_Id = %s AND Date = %s
                         AND ABS(ABS(Total_Amount) - %s) < 0.05
                       LIMIT 1""",
                    (cash_account_id, rec["date"], eur_amt),
                )
                if cur.fetchone():
                    fuzzy_tx.add(rec["desc"])
            elif not is_cash_flow and rec.get("quantity", 0) > 0:
                cur.execute(
                    """SELECT 1 FROM Investments
                       WHERE Accounts_Id = %s AND Date = %s
                         AND Action::text ILIKE %s
                         AND ABS(Quantity - %s) < 0.0000001
                       LIMIT 1""",
                    (account_id, rec["date"], rec["action"], rec["quantity"]),
                )
                if cur.fetchone():
                    fuzzy_inv.add(rec["desc"])

        _tx_target = cash_account_id if cash_account_id else account_id
        for rec in tx_records:
            cur.execute(
                """SELECT 1 FROM Transactions
                   WHERE Accounts_Id = %s AND Date = %s
                     AND ABS(ABS(Total_Amount) - ABS(%s)) < 0.05
                   LIMIT 1""",
                (_tx_target, rec["date"], rec["amount"]),
            )
            if cur.fetchone():
                fuzzy_tx.add(rec["desc"])

        return fuzzy_inv, fuzzy_tx
    finally:
        cur.close()
        conn.close()


def preview_security_matches(inv_records: list[dict]) -> dict[str, tuple]:
    """Return {symbol → (sec_id | None, match_type)} for each unique crypto."""
    from database.connection import get_connection as _gc
    from database.queries    import get_security_mappings as _get_map

    mappings = _get_map("Crypto.com")
    conn     = _gc()
    cur      = conn.cursor()
    result:  dict[str, tuple] = {}
    try:
        seen: set[str] = set()
        for rec in inv_records:
            sym = (rec.get("symbol") or "").strip()
            if not sym or sym in seen:
                continue
            seen.add(sym)

            if sym in mappings:
                sec_id = mappings[sym]
                cur.execute(
                    "SELECT Securities_Name FROM Securities WHERE Securities_Id = %s",
                    (sec_id,),
                )
                row = cur.fetchone()
                result[sym] = (sec_id, f"mapped:{row[0] if row else sym}")
                continue

            cur.execute(
                "SELECT Securities_Id FROM Securities WHERE Ticker = %s LIMIT 1", (sym,)
            )
            row = cur.fetchone()
            if row:
                result[sym] = (row[0], "ticker")
                continue

            name = _crypto_name(sym)
            cur.execute(
                "SELECT Securities_Id FROM Securities WHERE Securities_Name = %s LIMIT 1",
                (name,),
            )
            row = cur.fetchone()
            if row:
                result[sym] = (row[0], "name")
                continue

            result[sym] = (None, "new")
    finally:
        cur.close()
        conn.close()
    return result


# ---------------------------------------------------------------------------
# Database insertion
# ---------------------------------------------------------------------------

def run_cryptocom_import(
    inv_records:     list[dict],
    tx_records:      list[dict],
    account_id:      int,
    replace_mode:    bool      = False,
    progress_cb=None,
    cash_account_id: int | None = None,
) -> dict:
    """Insert Crypto.com records into the database.

    CashIn/CashOut records (fiat deposits/withdrawals) are routed to
    *cash_account_id* as Transactions when that account is configured,
    otherwise stored as Investment CashIn/CashOut rows on the brokerage account.
    """
    from database.connection import get_connection as _gc
    from database.crud       import update_holdings, update_accounts_balances, update_investment_balances
    from data.revolut_importer import _get_or_create_security, _inv_exists, _tx_exists

    conn   = _gc()
    cur    = conn.cursor()
    counts = {
        "investments": 0, "investments_skip": 0,
        "transactions": 0, "transactions_skip": 0,
    }

    try:
        if replace_mode:
            cur.execute(
                "DELETE FROM Investments WHERE Accounts_Id = %s AND Description LIKE %s",
                (account_id, f"{_CDC_PREFIX}%"),
            )
            if cash_account_id:
                cur.execute(
                    "DELETE FROM Transactions WHERE Accounts_Id = %s AND Description LIKE %s",
                    (cash_account_id, f"{_CDC_PREFIX}%"),
                )
            else:
                cur.execute(
                    "DELETE FROM Transactions WHERE Accounts_Id = %s AND Description LIKE %s",
                    (account_id, f"{_CDC_PREFIX}%"),
                )

        total = len(inv_records) + len(tx_records)
        done  = 0

        for rec in inv_records:
            desc         = rec["desc"]
            is_cash_flow = rec["action"] in ("CashIn", "CashOut") and not rec.get("symbol")

            if is_cash_flow and cash_account_id:
                if not replace_mode and _tx_exists(cur, cash_account_id, desc):
                    counts["transactions_skip"] += 1
                else:
                    amount = rec["total_eur"] if rec["action"] == "CashIn" else -rec["total_eur"]
                    cur.execute(
                        """INSERT INTO Transactions
                               (Accounts_Id, Date, Total_Amount, Description, Cleared)
                           VALUES (%s, %s, %s, %s, TRUE)""",
                        (cash_account_id, rec["date"], amount, desc),
                    )
                    counts["transactions"] += 1

            elif is_cash_flow:
                # Legacy single-account mode — keep as Investment CashIn/CashOut
                if not replace_mode and _inv_exists(cur, account_id, desc):
                    counts["investments_skip"] += 1
                else:
                    cur.execute(
                        """INSERT INTO Investments
                               (Accounts_Id, Date, Action,
                                Quantity, Price_Per_Share, Commission,
                                Total_Amount_AccCur, Total_Amount_SecCur, FX_Rate,
                                Description)
                           VALUES (%s, %s, %s::investments_action,
                                   %s, %s, %s, %s, %s, %s, %s)""",
                        (account_id, rec["date"], rec["action"],
                         rec["quantity"], rec["price"], rec.get("commission", 0.0),
                         rec["total_eur"], rec["total_eur"], 1.0,
                         desc),
                    )
                    counts["investments"] += 1

            else:
                # Crypto investment record
                sec_id = _get_or_create_security(
                    cur,
                    rec["symbol"], rec["name"],
                    rec.get("currency", "EUR"), rec.get("asset_category", "CRYPTO"),
                    source="Crypto.com", isin="",
                )
                if not replace_mode and _inv_exists(cur, account_id, desc):
                    counts["investments_skip"] += 1
                else:
                    cur.execute(
                        """INSERT INTO Investments
                               (Accounts_Id, Securities_Id, Date, Action, Quantity,
                                Price_Per_Share, Commission,
                                Total_Amount_AccCur, Total_Amount_SecCur, FX_Rate,
                                Description)
                           VALUES (%s, %s, %s, %s::investments_action, %s, %s, %s,
                                   %s, %s, %s, %s)""",
                        (account_id, sec_id, rec["date"], rec["action"],
                         rec["quantity"], rec["price"], rec.get("commission", 0.0),
                         rec["total_eur"], rec["total_eur"], 1.0,
                         desc),
                    )
                    counts["investments"] += 1

            done += 1
            if progress_cb and done % 10 == 0:
                progress_cb(done / max(total, 1))

        _tx_target = cash_account_id if cash_account_id else account_id
        for rec in tx_records:
            desc = rec["desc"]
            if not replace_mode and _tx_exists(cur, _tx_target, desc):
                counts["transactions_skip"] += 1
            else:
                cur.execute(
                    """INSERT INTO Transactions
                           (Accounts_Id, Date, Total_Amount, Description, Cleared)
                       VALUES (%s, %s, %s, %s, TRUE)""",
                    (_tx_target, rec["date"], rec["amount"], desc),
                )
                counts["transactions"] += 1
            done += 1
            if progress_cb and done % 10 == 0:
                progress_cb(done / max(total, 1))

        conn.commit()
        update_holdings()
        update_accounts_balances()
        update_investment_balances()

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    if not cash_account_id:
        from database.crud import create_linked_cash_transactions_for_unlinked, get_linked_account_id
        _linked = get_linked_account_id(account_id)
        if _linked:
            create_linked_cash_transactions_for_unlinked(account_id, _linked)
            update_accounts_balances(_linked)
            update_investment_balances()

    if cash_account_id:
        update_accounts_balances(cash_account_id)

    return counts
