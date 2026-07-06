"""Coinbase REST API v2 Connector.

Authentication — two key formats are supported automatically
-------------------------------------------------------------

**CDP / Cloud API keys** (recommended — from cloud.coinbase.com/access/api):
  • API Key Name : ``organizations/{org_id}/apiKeys/{key_id}``
  • API Secret   : PEM EC private key (starts with ``-----BEGIN EC PRIVATE KEY-----``)
  Uses JWT / ES256 signing.  Requires the ``cryptography`` package
  (``pip install cryptography``).

**Legacy API keys** (from coinbase.com/settings/api → New API Key):
  • API Key    : short alphanumeric string
  • API Secret : alphanumeric secret string
  Uses HMAC-SHA256 signing.  No extra packages needed.

Required permissions (both formats):
  • wallet:accounts:read
  • wallet:transactions:read

What gets imported
------------------
Transaction type               → Record type         Action
-----------------------------  ------------------    ----------------------------------------
buy                            Investment            Buy
sell                           Investment            Sell
advanced_trade_fill            Investment            Buy (raw_amt > 0) or Sell (raw_amt < 0)
trade  (crypto-to-crypto swap) Investment            Buy (raw_amt > 0) or Sell (raw_amt < 0)
staking_reward                 Investment            Reinvest  (income as new units, FMV cost basis)
interest                       Investment            Dividend
inflation_reward               Investment            Dividend
retail_simple_price_improvement Investment           Dividend  (price-improvement rebate)
send / receive (crypto wallet) Investment            ShrIn (raw_amt > 0) or ShrOut (raw_amt < 0)
staking_transfer               ──                    SKIP (internal lock/unlock, no qty change)
fiat_deposit                   Transaction           +amount
fiat_withdrawal                Transaction           -amount
exchange_deposit               Transaction           +amount
exchange_withdrawal            Transaction           -amount
(all other types)              Transaction           raw amount

Design note — send / receive
-----------------------------
These directly change the on-chain quantity and must be investment records so
the holdings calculation stays correct. Direction is determined by the sign of
the amount, not the ``type`` string: Coinbase has been observed reporting
``type: "send"`` with a *positive* amount for crypto arriving at the account
(e.g. on-chain transfers via Base and other L2s), so trusting ``send`` to always
mean outflow silently misclassified real incoming transfers as ShrOut. If you
also track the *destination* wallet separately you will see the same transfer
as both a ShrOut (source) and a ShrIn (destination), which is intentional
double-entry bookkeeping.

Dedup prefix: CB|
"""

from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CB_API_BASE  = "https://api.coinbase.com"
_CB_VERSION   = "2016-02-18"      # API-Version header required by v2
_CB_PREFIX    = "CB|"

# Fiat currency codes — wallets with these codes produce Transaction records,
# not Investment records (the fiat side of a buy is skipped; only the crypto
# wallet records the investment).
_FIAT_CODES = {
    # Native fiat currencies
    "EUR", "USD", "GBP", "CHF", "CAD", "AUD", "JPY", "SGD",
    "HKD", "NOK", "SEK", "DKK", "NZD", "MXN", "BRL", "CZK",
    "PLN", "HUF", "RON", "BGN", "HRK", "TRY",
    # EUR-pegged stablecoins.
    # EURC is intentionally NOT listed here so it can be held as an investment
    # security (some users track EURC positions in their portfolio).
    # EURT / EURS remain treated as cash equivalents.
    "EURT",   # Euro Tether
    "EURS",   # STASIS EURO
    # USD-pegged stablecoins (treated as cash for portfolio purposes)
    "USDC", "USDT", "DAI", "TUSD", "USDP", "GUSD", "FRAX",
}

# Well-known crypto names for auto-populating Securities.Securities_Name
_CRYPTO_NAMES: dict[str, str] = {
    "BTC":   "Bitcoin",
    "ETH":   "Ethereum",
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
    "1INCH": "1inch",
    "SUSHI": "SushiSwap",
    "FIL":   "Filecoin",
    "ICP":   "Internet Computer",
    "NEAR":  "NEAR Protocol",
    "FTM":   "Fantom",
    "HBAR":  "Hedera",
    "SAND":  "The Sandbox",
    "MANA":  "Decentraland",
    "AXS":   "Axie Infinity",
    "ENJ":   "Enjin Coin",
    "CHZ":   "Chiliz",
    "FLOW":  "Flow",
    "ROSE":  "Oasis Network",
    "KSM":   "Kusama",
    "BAND":  "Band Protocol",
    "ZRX":   "0x Protocol",
    "BAT":   "Basic Attention Token",
    "ZEC":   "Zcash",
    "XMR":   "Monero",
    "DASH":  "Dash",
    "ETC":   "Ethereum Classic",
    "VET":   "VeChain",
    "EOS":   "EOS",
    "XTZ":   "Tezos",
    "USDC":  "USD Coin",
    "USDT":  "Tether",
    "DAI":   "Dai",
    "TUSD":  "TrueUSD",
    "WBTC":  "Wrapped Bitcoin",
    "STETH": "Staked Ether",
    "CBETH": "Coinbase Wrapped Staked ETH",
    "OSMO":  "Osmosis",
    "INJ":   "Injective",
    "SEI":   "Sei",
    "SUI":   "Sui",
    "APT":   "Aptos",
    "ARB":   "Arbitrum",
    "OP":    "Optimism",
}

# Transaction types that go into the Investments table (crypto wallet side)
_INV_TX_TYPES = frozenset({
    "buy",
    "sell",
    "advanced_trade_fill",
    "staking_reward",
    "interest",
    "inflation_reward",
    "send",       # crypto leaves Coinbase → ShrOut
    "receive",    # crypto arrives at Coinbase → ShrIn
})

# Transaction types that go into the Transactions table (fiat wallet side)
_CASH_TX_TYPES = frozenset({
    "fiat_deposit",
    "fiat_withdrawal",
    "exchange_deposit",
    "exchange_withdrawal",
})


# ---------------------------------------------------------------------------
# Authentication helpers
# ---------------------------------------------------------------------------

def _is_cdp_key(api_key: str, api_secret: str) -> bool:
    """Return True for CDP/Cloud API keys (JWT), False for legacy HMAC keys.

    CDP keys are identified by:
      • API key name containing '/'  (e.g. 'organizations/{org}/apiKeys/{id}')
      • API secret starting with a PEM header (EC or PKCS8 private key)
    """
    return (
        "/" in api_key
        or "BEGIN EC PRIVATE KEY"  in api_secret
        or "BEGIN PRIVATE KEY"     in api_secret
    )


def _make_cb_jwt(key_name: str, private_key_pem: str,
                 path_with_query: str) -> str:
    """Build and sign a JWT for a Coinbase CDP API request (ES256).

    Requires the ``cryptography`` package.

    The JWT ``uri`` claim must be ``"GET api.coinbase.com{path}"`` using only
    the path part (no query string).  The nonce prevents replay attacks.
    """
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
    except ImportError as _e:
        raise ValueError(
            "The 'cryptography' package is required for Coinbase CDP API keys. "
            "Install it with:  pip install cryptography"
        ) from _e

    # Strip query string for the JWT uri claim
    path_only = path_with_query.split("?")[0]
    uri       = f"GET api.coinbase.com{path_only}"

    now   = int(time.time())
    nonce = secrets.token_hex(16)

    def _b64url(data: dict) -> str:
        return base64.urlsafe_b64encode(
            json.dumps(data, separators=(",", ":")).encode("utf-8")
        ).rstrip(b"=").decode("ascii")

    header_b64  = _b64url({"alg": "ES256", "kid": key_name, "nonce": nonce})
    payload_b64 = _b64url({
        "iss": "cdp",
        "nbf": now,
        "exp": now + 120,
        "sub": key_name,
        "uri": uri,
    })

    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")

    # Normalise the PEM key — Streamlit text inputs and env vars sometimes
    # deliver literal "\n" instead of real newlines.
    pem_str = private_key_pem.strip().replace("\\n", "\n")

    private_key = load_pem_private_key(pem_str.encode("utf-8"), password=None)
    sig_der     = private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))

    # Convert DER (r, s ASN.1) → raw 64-byte (r‖s) as JWT ES256 requires
    r, s    = decode_dss_signature(sig_der)
    sig_raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    sig_b64 = base64.urlsafe_b64encode(sig_raw).rstrip(b"=").decode("ascii")

    return f"{header_b64}.{payload_b64}.{sig_b64}"


def _build_headers(api_key: str, api_secret: str,
                   path_with_query: str) -> dict:
    """Return the correct authentication headers for the given key type."""
    if _is_cdp_key(api_key, api_secret):
        jwt_token = _make_cb_jwt(api_key, api_secret, path_with_query)
        return {
            "Authorization": f"Bearer {jwt_token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        }
    else:
        # Legacy HMAC — message = timestamp + "GET" + full_path_with_query
        ts  = str(int(time.time()))
        msg = ts + "GET" + path_with_query
        sig = _hmac.new(
            api_secret.encode("utf-8"),
            msg.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "CB-ACCESS-KEY":       api_key,
            "CB-ACCESS-SIGN":      sig,
            "CB-ACCESS-TIMESTAMP": ts,
            "CB-VERSION":          _CB_VERSION,
            "Accept":              "application/json",
        }


# ---------------------------------------------------------------------------
# API request / pagination
# ---------------------------------------------------------------------------

def _cb_request(api_key: str, api_secret: str,
                path_with_query: str) -> dict:
    """Make one authenticated GET request to api.coinbase.com and return JSON."""
    headers = _build_headers(api_key, api_secret, path_with_query)
    url     = f"{_CB_API_BASE}{path_with_query}"
    req     = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as http_err:
        body = http_err.read().decode("utf-8", errors="replace")
        try:
            err_json = json.loads(body)
            errors   = err_json.get("errors", [])
            if errors:
                msgs = "; ".join(e.get("message", str(e)) for e in errors)
                raise ValueError(f"Coinbase API error: {msgs}") from None
        except (json.JSONDecodeError, TypeError):
            pass
        hint = ""
        if http_err.code == 401:
            hint = (
                "  Hint: if you created your key at cloud.coinbase.com, "
                "paste the full key name (organizations/…/apiKeys/…) and "
                "the PEM private key in the two fields."
            )
        raise ValueError(
            f"Coinbase API HTTP {http_err.code}: {body[:300]}{hint}"
        ) from None
    except urllib.error.URLError as url_err:
        raise ValueError(
            f"Network error contacting Coinbase: {url_err.reason}"
        ) from None

    if "errors" in data and data["errors"]:
        msgs = "; ".join(e.get("message", str(e)) for e in data["errors"])
        raise ValueError(f"Coinbase API error: {msgs}")

    return data


def _cb_paginate(api_key: str, api_secret: str,
                 initial_path: str,
                 params: dict | None = None,
                 max_pages: int = 500,
                 progress_cb=None) -> list[dict]:
    """Collect all pages of a Coinbase v2 paginated endpoint."""
    base_params: dict = {"limit": "100", "order": "asc"}
    if params:
        base_params.update(params)

    all_items: list[dict] = []
    path       = initial_path
    cur_params: dict | None = dict(base_params)
    page       = 0

    while path and page < max_pages:
        pq   = path + ("?" + urllib.parse.urlencode(cur_params) if cur_params else "")
        data = _cb_request(api_key, api_secret, pq)
        items = data.get("data", [])
        all_items.extend(items)

        if progress_cb:
            progress_cb(len(all_items))

        pagination = data.get("pagination", {})
        next_uri   = pagination.get("next_uri") or pagination.get("next_starting_after")
        if not next_uri:
            break

        parsed = urllib.parse.urlparse(next_uri)
        if parsed.path:
            path       = parsed.path
            cur_params = {k: v[0] for k, v in
                          urllib.parse.parse_qs(parsed.query).items()}
        else:
            cur_params = {"starting_after": next_uri, "limit": "100", "order": "asc"}
        page += 1

    return all_items


# ---------------------------------------------------------------------------
# Public fetch functions
# ---------------------------------------------------------------------------

def test_connection(api_key: str, api_secret: str) -> list[dict]:
    """Verify credentials and return list of Coinbase accounts.

    Returns a list of account dicts with at least:
        id, name, currency_code, balance, native_balance, type
    """
    raw_accounts = _cb_paginate(api_key, api_secret, "/v2/accounts")
    return [_normalise_account(a) for a in raw_accounts]


def _normalise_account(a: dict) -> dict:
    """Flatten a Coinbase account object into a plain dict."""
    ccy = a.get("currency", {})
    if isinstance(ccy, dict):
        code = ccy.get("code", "") or ccy.get("name", "?")
        ccy_name = ccy.get("name", code)
    else:
        code = str(ccy)
        ccy_name = code

    bal     = a.get("balance", {})
    nat_bal = a.get("native_balance", {})

    return {
        "id":             a.get("id", ""),
        "name":           a.get("name", code),
        "type":           a.get("type", "wallet"),
        "currency_code":  code,
        "currency_name":  ccy_name,
        "balance":        float(bal.get("amount", 0) or 0),
        "native_balance": float(nat_bal.get("amount", 0) or 0),
        "native_currency":nat_bal.get("currency", "EUR"),
        "is_fiat":        code.upper() in _FIAT_CODES,
    }


def fetch_all_transactions(
    api_key:    str,
    api_secret: str,
    accounts:   list[dict],           # from test_connection()
    start_date: "date | None" = None,
    end_date:   "date | None" = None,
    progress_cb=None,
) -> list[dict]:
    """Fetch every transaction from every account, returning a flat list.

    Each item has all the original Coinbase transaction fields plus the extra
    keys injected by this function:
        _account_id       : Coinbase account UUID
        _account_currency : e.g. "BTC", "EUR"
        _is_fiat_account  : bool

    Date filtering is applied client-side after fetching (the v2 API has no
    server-side date range parameter on the transactions endpoint).
    """
    all_txns: list[dict] = []
    total_accs = len(accounts)

    for idx, acc in enumerate(accounts):
        if progress_cb:
            progress_cb(f"Fetching {acc['currency_code']} account "
                        f"({idx+1}/{total_accs})…")

        raw = _cb_paginate(
            api_key, api_secret,
            f"/v2/accounts/{acc['id']}/transactions",
            params={"expand": "all"},
        )
        for tx in raw:
            tx["_account_id"]       = acc["id"]
            tx["_account_currency"] = acc["currency_code"]
            tx["_is_fiat_account"]  = acc["is_fiat"]
        all_txns.extend(raw)

    # Client-side date filter
    if start_date or end_date:
        filtered = []
        for tx in all_txns:
            tx_date = _parse_cb_date(tx.get("created_at", ""))
            if tx_date is None:
                filtered.append(tx)
                continue
            if start_date and tx_date < start_date:
                continue
            if end_date and tx_date > end_date:
                continue
            filtered.append(tx)
        all_txns = filtered

    return all_txns


# ---------------------------------------------------------------------------
# Record builders
# ---------------------------------------------------------------------------

def _parse_cb_date(val: str) -> "Optional[date]":
    """Parse an ISO 8601 datetime string (with or without timezone) to date."""
    if not val:
        return None
    raw = str(val).strip()[:19]    # "2024-01-15T10:00:00"
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    return None


def _float(obj: dict | None, field: str, default: float = 0.0) -> float:
    if not obj:
        return default
    v = obj.get(field)
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def build_coinbase_records(
    transactions: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Convert a flat list of Coinbase transaction dicts to (inv_records, tx_records).

    Mapping rules:
    ─────────────────────────────────────────────────────────────────────────
    Crypto wallet accounts:
      buy                  → Investment  Buy
      sell                 → Investment  Sell
      advanced_trade_fill  → Investment  Buy (raw_amt > 0) or Sell (raw_amt < 0)
      staking_reward       → Investment  Reinvest  (income as new units, new cost basis at FMV)
      interest             → Investment  Dividend
      inflation_reward     → Investment  Dividend
      send                 → Investment  ShrOut  (crypto left Coinbase — reduces qty)
      receive              → Investment  ShrIn   (crypto arrived at Coinbase — adds qty)

    Fiat wallet accounts:
      fiat_deposit         → Transaction +amount
      fiat_withdrawal      → Transaction -amount
      exchange_deposit     → Transaction +amount
      exchange_withdrawal  → Transaction -amount
      buy  / sell          → SKIPPED  (crypto wallet holds the investment record)
      (other)              → Transaction (catch-all)
    ─────────────────────────────────────────────────────────────────────────
    """
    inv_records: list[dict] = []
    tx_records:  list[dict] = []

    # ── DIAGNOSTIC (remove after confirming fix) ─────────────────────────────
    import collections as _col
    _type_counter   = _col.Counter()
    _action_counter = _col.Counter()
    print("[coinbase_connector] build_coinbase_records VERSION=2026-05-26-FIX-4 called"
          f" with {len(transactions)} raw transactions")
    # ─────────────────────────────────────────────────────────────────────────

    # Dedup key = (account_id, tx_id) so the same Coinbase transaction can be
    # processed once per account.  This is required for multi-wallet trades
    # (e.g. XTZ→ETH shows up in both the XTZ wallet and the ETH wallet with the
    # same tx_id; both sides must be recorded as investment records).
    seen_keys: set[tuple] = set()

    for tx in transactions:
        tx_id   = tx.get("id", "")
        acct_id = tx.get("_account_id", "")
        _dkey   = (acct_id, tx_id)
        if _dkey in seen_keys:
            continue
        seen_keys.add(_dkey)

        tx_type  = str(tx.get("type", "")).lower()
        status   = str(tx.get("status", "")).lower()
        if status not in ("completed", ""):
            continue   # skip pending/failed/reversed

        acct_ccy = str(tx.get("_account_currency", "")).upper()
        is_fiat  = bool(tx.get("_is_fiat_account", acct_ccy in _FIAT_CODES))

        amt_obj  = tx.get("amount",        {}) or {}
        nat_obj  = tx.get("native_amount", {}) or {}

        raw_amt  = _float(amt_obj, "amount")       # crypto or fiat units
        nat_amt  = _float(nat_obj, "amount")       # EUR (negative = cost)
        ccy_code = str(amt_obj.get("currency", acct_ccy)).upper()

        dt = _parse_cb_date(tx.get("created_at", ""))
        if dt is None:
            continue

        # Stable dedup key
        key = f"{_CB_PREFIX}{ccy_code}|{tx_id}"

        # ── Diagnostic counter ────────────────────────────────────────────
        _type_counter[f"{'fiat' if is_fiat else 'crypto'}:{tx_type}"] += 1

        # ── Fiat-account rows ─────────────────────────────────────────────
        if is_fiat:
            # Skip the fiat side of crypto buys/sells — the investment record
            # on the crypto wallet already carries the full EUR cost.
            if tx_type in ("buy", "sell", "advanced_trade_fill"):
                continue

            eur_amt = abs(nat_amt) if nat_amt else abs(raw_amt)

            if tx_type in ("fiat_deposit", "exchange_deposit"):
                # Cash arrived in the Coinbase EUR wallet from an external
                # bank/card transfer.  Record as CashIn in the Investments
                # table (Securities_Id = NULL) — the correct way for
                # investment accounts in this app.  Balance, XIRR, and
                # net-invested queries all read CashIn/CashOut from Investments.
                inv_records.append({
                    "record_type":    "investment",
                    "source":         "Coinbase",
                    "desc":           key,
                    "symbol":         None,   # CashIn has no security
                    "name":           None,
                    "isin":           "",
                    "currency":       "EUR",
                    "asset_category": None,
                    "date":           dt,
                    "action":         "CashIn",
                    "quantity":       0.0,
                    "price":          0.0,
                    "commission":     0.0,
                    "total_eur":      round(eur_amt, 4),
                })
                _action_counter["CashIn"] += 1

            elif tx_type in ("fiat_withdrawal", "exchange_withdrawal"):
                # Cash left the Coinbase EUR wallet to an external bank account.
                inv_records.append({
                    "record_type":    "investment",
                    "source":         "Coinbase",
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
                    "total_eur":      round(eur_amt, 4),
                })
                _action_counter["CashOut"] += 1

            else:
                # Any other fiat-wallet transaction (rare) → cash record
                desc = _tx_description(tx_type, ccy_code, tx)
                tx_records.append({
                    "record_type": "transaction",
                    "source":      "Coinbase",
                    "desc":        key,
                    "date":        dt,
                    "amount":      round(raw_amt, 6),
                    "description": desc,
                    "currency":    ccy_code,
                })
                _action_counter["cash:fiat_other"] += 1

            continue

        # ── Crypto-account rows ───────────────────────────────────────────

        # ── Shared base dict for investment records ────────────────────────
        _base_inv = {
            "record_type":    "investment",
            "source":         "Coinbase",
            "desc":           key,
            "symbol":         ccy_code,
            "name":           _crypto_name(ccy_code),
            "isin":           "",
            "currency":       "EUR",
            "asset_category": "CRYPTO",
            "date":           dt,
        }

        # --- Investment types ---

        # advanced_trade_fill direction is determined by the sign of raw_amt:
        #   positive → crypto was received (Buy), negative → crypto was sent (Sell)
        is_buy  = tx_type == "buy" or (
            tx_type == "advanced_trade_fill" and raw_amt >= 0
        )
        is_sell = tx_type == "sell" or (
            tx_type == "advanced_trade_fill" and raw_amt < 0
        )

        if is_buy:
            qty, price, commission, total_eur = _extract_buy(tx, raw_amt, nat_amt)
            inv_records.append({
                **_base_inv,
                "action":     "Buy",
                "quantity":   round(qty, 8),
                "price":      round(price, 6),
                "commission": round(commission, 6),
                "total_eur":  round(total_eur, 4),
            })
            _action_counter["Buy"] += 1

        elif is_sell:
            qty, price, commission, total_eur = _extract_sell(tx, raw_amt, nat_amt)
            inv_records.append({
                **_base_inv,
                "action":     "Sell",
                "quantity":   round(qty, 8),
                "price":      round(price, 6),
                "commission": round(commission, 6),
                "total_eur":  round(abs(total_eur), 4),
            })
            _action_counter["Sell"] += 1

        elif tx_type == "staking_reward":
            qty     = abs(raw_amt)
            eur_val = abs(nat_amt)
            unit_px = eur_val / qty if qty > 0 else 0.0
            inv_records.append({
                **_base_inv,
                "action":     "Reinvest",
                "quantity":   round(qty, 8),
                "price":      round(unit_px, 6),
                "commission": 0.0,
                "total_eur":  round(eur_val, 4),
            })
            _action_counter["Reinvest"] += 1

        elif tx_type in ("interest", "inflation_reward"):
            qty     = abs(raw_amt)
            eur_val = abs(nat_amt)
            unit_px = eur_val / qty if qty > 0 else 0.0
            inv_records.append({
                **_base_inv,
                "action":     "Dividend",
                "quantity":   round(qty, 8),
                "price":      round(unit_px, 6),
                "commission": 0.0,
                "total_eur":  round(eur_val, 4),
            })
            _action_counter["Dividend"] += 1

        elif tx_type in ("send", "receive"):
            # Direction is determined by the sign of raw_amt, not the type string —
            # Coinbase has been observed reporting type="send" with a POSITIVE amount
            # for crypto ARRIVING at the account (e.g. on-chain transfers via Base and
            # other L2s), contradicting the naive send=outflow/receive=inflow mapping
            # this used to assume unconditionally. A negative amount always means the
            # crypto left; a positive amount always means it arrived — mirroring the
            # same sign-based disambiguation already used for advanced_trade_fill above.
            qty     = abs(raw_amt)
            eur_val = abs(nat_amt)
            unit_px = eur_val / qty if qty > 0 else 0.0
            action  = "ShrIn" if raw_amt > 0 else "ShrOut"
            inv_records.append({
                **_base_inv,
                "action":     action,
                "quantity":   round(qty, 8),
                "price":      round(unit_px, 6),
                "commission": 0.0,
                "total_eur":  round(eur_val, 4),
            })
            _action_counter[action] += 1
            print(f"  [CB-DIAG] {tx_type}(amt={raw_amt})→{action}  {ccy_code}  qty={abs(raw_amt)}  key={key}")

        elif tx_type == "trade":
            # Coinbase Convert / crypto-to-crypto swap.
            # Each side of the trade appears on its own wallet with the same tx_id.
            # raw_amt > 0 → crypto arrived (Buy); raw_amt < 0 → crypto left (Sell).
            if raw_amt >= 0:
                qty, price, commission, total_eur = _extract_buy(tx, raw_amt, nat_amt)
                inv_records.append({
                    **_base_inv,
                    "action":     "Buy",
                    "quantity":   round(qty, 8),
                    "price":      round(price, 6),
                    "commission": round(commission, 6),
                    "total_eur":  round(total_eur, 4),
                })
                _action_counter["Buy"] += 1
            else:
                qty, price, commission, total_eur = _extract_sell(tx, raw_amt, nat_amt)
                inv_records.append({
                    **_base_inv,
                    "action":     "Sell",
                    "quantity":   round(qty, 8),
                    "price":      round(price, 6),
                    "commission": round(commission, 6),
                    "total_eur":  round(abs(total_eur), 4),
                })
                _action_counter["Sell"] += 1
            print(f"  [CB-DIAG] trade→{'Buy' if raw_amt>=0 else 'Sell'}  {ccy_code}"
                  f"  qty={abs(raw_amt)}  key={key}")

        elif tx_type == "staking_transfer":
            # Internal Coinbase movement between spot wallet and staking contract.
            # Does NOT change total holdings — skip entirely.
            # (Staking rewards are captured separately as staking_reward → Reinvest.)
            _action_counter["skip:staking_transfer"] += 1

        elif tx_type == "retail_simple_price_improvement":
            # Coinbase price-improvement rebate paid as crypto — treat as income.
            qty     = abs(raw_amt)
            eur_val = abs(nat_amt)
            unit_px = eur_val / qty if qty > 0 else 0.0
            inv_records.append({
                **_base_inv,
                "action":     "Dividend",
                "quantity":   round(qty, 8),
                "price":      round(unit_px, 6),
                "commission": 0.0,
                "total_eur":  round(eur_val, 4),
            })
            _action_counter["Dividend"] += 1

        else:
            # Unknown crypto-wallet type → cash transaction (catch-all)
            desc = _tx_description(tx_type, ccy_code, tx)
            tx_records.append({
                "record_type": "transaction",
                "source":      "Coinbase",
                "desc":        key,
                "date":        dt,
                "amount":      round(nat_amt, 6),    # EUR value
                "description": desc,
                "currency":    "EUR",
            })
            _action_counter[f"cash:{tx_type}"] += 1

    # ── DIAGNOSTIC SUMMARY ─────────────────────────────────────────────────
    print(f"[coinbase_connector] TX type distribution: {dict(_type_counter)}")
    print(f"[coinbase_connector] Action breakdown: {dict(_action_counter)}")
    print(f"[coinbase_connector] Result: {len(inv_records)} inv, {len(tx_records)} cash")
    # ───────────────────────────────────────────────────────────────────────

    return inv_records, tx_records


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _crypto_name(code: str) -> str:
    return _CRYPTO_NAMES.get(code.upper(), f"{code} (Crypto)")


def _extract_buy(tx: dict, raw_amt: float, nat_amt: float
                 ) -> tuple[float, float, float, float]:
    """Return (quantity, price_per_unit, commission, total_eur) for a buy.

    For standard Coinbase buys the API sub-object contains:
      • unit_price  — spot price per unit (excludes fee)
      • total       — total EUR paid (includes fee)
      • fee         — often absent / zero in the v2 response

    When the explicit fee is zero but we have both a spot price and a total,
    we recover the implied commission as:
        commission = total_eur − (unit_price × quantity)
    This matches what Coinbase charged, e.g. 103.99 total − 100.00 spot = 3.99 fee.
    """
    qty      = abs(raw_amt)
    eur_cost = abs(nat_amt)
    fee      = 0.0
    unit_px  = 0.0

    buy_obj = tx.get("buy") or tx.get("advanced_trade_fill") or {}
    if buy_obj:
        fee_obj = buy_obj.get("fee", {}) or {}
        fee     = abs(_float(fee_obj, "amount"))

        up_obj  = buy_obj.get("unit_price", {}) or {}
        unit_px = _float(up_obj, "amount")

        total_obj = buy_obj.get("total", {}) or {}
        if total_obj:
            eur_cost = abs(_float(total_obj, "amount"))

    if unit_px <= 0 and qty > 0:
        unit_px = (eur_cost - fee) / qty

    # Implied-fee recovery: when the API doesn't supply a fee field but we
    # have a spot price, the difference between total paid and spot cost is
    # the Coinbase commission.  Guard with a 0.001 EUR floor to avoid
    # floating-point noise on transactions with no real fee.
    if fee == 0.0 and unit_px > 0.0 and qty > 0.0:
        implied = eur_cost - unit_px * qty
        if implied > 0.001:
            fee = round(implied, 6)

    return qty, unit_px, fee, eur_cost


def _extract_sell(tx: dict, raw_amt: float, nat_amt: float
                  ) -> tuple[float, float, float, float]:
    """Return (quantity, price_per_unit, commission, total_eur) for a sell.

    For sells the API sub-object contains:
      • unit_price  — gross spot price per unit (before fee deduction)
      • total       — net EUR received (after fee is deducted)
      • fee         — often absent / zero in the v2 response

    When the explicit fee is zero but we have both a spot price and a total,
    we recover the implied commission as:
        commission = (unit_price × quantity) − net_received
    """
    qty      = abs(raw_amt)
    eur_recv = abs(nat_amt)
    fee      = 0.0
    unit_px  = 0.0

    sell_obj = tx.get("sell") or tx.get("advanced_trade_fill") or {}
    if sell_obj:
        fee_obj = sell_obj.get("fee", {}) or {}
        fee     = abs(_float(fee_obj, "amount"))

        up_obj  = sell_obj.get("unit_price", {}) or {}
        unit_px = _float(up_obj, "amount")

        total_obj = sell_obj.get("total", {}) or {}
        if total_obj:
            eur_recv = abs(_float(total_obj, "amount"))

    if unit_px <= 0 and qty > 0:
        unit_px = (eur_recv + fee) / qty

    # Implied-fee recovery: gross proceeds (spot × qty) minus net received
    # equals the Coinbase commission withheld on the sell side.
    if fee == 0.0 and unit_px > 0.0 and qty > 0.0:
        implied = unit_px * qty - eur_recv
        if implied > 0.001:
            fee = round(implied, 6)

    return qty, unit_px, fee, eur_recv


def _tx_description(tx_type: str, ccy: str, tx: dict) -> str:
    """Generate a human-readable description for a cash transaction."""
    details = tx.get("details", {}) or {}
    title   = details.get("title", "").strip()
    subtitle = details.get("subtitle", "").strip()

    labels = {
        "fiat_deposit":       f"Coinbase Deposit ({ccy})",
        "fiat_withdrawal":    f"Coinbase Withdrawal ({ccy})",
        "exchange_deposit":   f"Coinbase Exchange Deposit ({ccy})",
        "exchange_withdrawal":f"Coinbase Exchange Withdrawal ({ccy})",
        "send":               f"Coinbase Send ({ccy})",
        "receive":            f"Coinbase Receive ({ccy})",
    }
    base = labels.get(tx_type, f"Coinbase {tx_type.replace('_', ' ').title()} ({ccy})")
    if title and title.lower() not in ("", "none"):
        return f"{base}: {title}" + (f" — {subtitle}" if subtitle else "")
    return base


# ---------------------------------------------------------------------------
# Reconciliation helpers  (mirror revolut_importer pattern)
# ---------------------------------------------------------------------------

def check_existing_records(
    inv_records:      list[dict],
    tx_records:       list[dict],
    account_id:       int,
    cash_account_id:  int | None = None,
) -> tuple[set[str], set[str]]:
    """Return (existing_inv_descs, existing_tx_descs).

    When *cash_account_id* is set, CashIn/CashOut records (which will be
    inserted as Transactions into the cash account) are checked against
    ``Transactions`` on that account rather than ``Investments`` on the
    brokerage account.
    """
    from database.connection import get_connection as _gc
    conn = _gc()
    cur  = conn.cursor()
    try:
        existing_inv: set[str] = set()
        existing_tx:  set[str] = set()

        # Split inv_records: crypto investments vs. cash-flow (CashIn/CashOut)
        crypto_inv   = [r for r in inv_records if r.get("symbol")]
        cash_flow    = [r for r in inv_records if not r.get("symbol")]

        if crypto_inv:
            descs = [r["desc"] for r in crypto_inv]
            ph    = ",".join(["%s"] * len(descs))
            cur.execute(
                f"SELECT Description FROM Investments "
                f"WHERE Accounts_Id = %s AND Description IN ({ph})",
                [account_id] + descs,
            )
            existing_inv = {row[0] for row in cur.fetchall()}

        # CashIn/CashOut: check against Transactions in cash_account_id (if set)
        # or against Investments in account_id (legacy single-account mode)
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
    """Return (fuzzy_inv_descs, fuzzy_tx_descs) for likely-duplicate detection.

    When *cash_account_id* is set, CashIn/CashOut records (cash-flow, no symbol)
    are matched against Transactions in the cash account by date + abs(amount),
    sign-insensitive so that existing entries recorded from either perspective
    (deposit as +amount or as −amount via a linked transfer) are detected.
    """
    from database.connection import get_connection as _gc
    conn = _gc()
    cur  = conn.cursor()
    fuzzy_inv: set[str] = set()
    fuzzy_tx:  set[str] = set()
    try:
        for rec in inv_records:
            is_cash_flow = rec["action"] in ("CashIn", "CashOut") and not rec.get("symbol")

            if is_cash_flow and cash_account_id:
                # Match against Transactions in the cash account; ignore sign so both
                # old linked-transfer entries (negative) and new direct entries match.
                eur_amt = rec["total_eur"]
                cur.execute(
                    """SELECT 1 FROM Transactions
                       WHERE Accounts_Id           = %s
                         AND Date                  = %s
                         AND ABS(ABS(Total_Amount) - %s) < 0.05
                       LIMIT 1""",
                    (cash_account_id, rec["date"], eur_amt),
                )
                if cur.fetchone():
                    fuzzy_tx.add(rec["desc"])
            else:
                cur.execute(
                    """SELECT 1 FROM Investments
                       WHERE Accounts_Id  = %s
                         AND Date         = %s
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
                   WHERE Accounts_Id               = %s
                     AND Date                      = %s
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
    """Return {symbol → (sec_id | None, match_type)} for each unique crypto.

    Match priority: saved mapping → ticker → name → 'new'
    """
    from database.connection import get_connection as _gc
    from database.queries    import get_security_mappings as _get_map

    mappings = _get_map("Coinbase")
    conn     = _gc()
    cur      = conn.cursor()
    result: dict[str, tuple] = {}
    try:
        seen: set[str] = set()
        for rec in inv_records:
            sym = (rec.get("symbol") or "").strip()
            if not sym or sym in seen:
                continue
            seen.add(sym)

            # 0. Saved mapping
            if sym in mappings:
                sec_id = mappings[sym]
                cur.execute(
                    "SELECT Securities_Name FROM Securities WHERE Securities_Id = %s",
                    (sec_id,),
                )
                row = cur.fetchone()
                result[sym] = (sec_id, f"mapped:{row[0] if row else sym}")
                continue

            # 1. Ticker match
            cur.execute(
                "SELECT Securities_Id FROM Securities WHERE Ticker = %s LIMIT 1",
                (sym,),
            )
            row = cur.fetchone()
            if row:
                result[sym] = (row[0], "ticker")
                continue

            # 2. Name match
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

def run_coinbase_import(
    inv_records:     list[dict],
    tx_records:      list[dict],
    account_id:      int,
    replace_mode:    bool     = False,
    progress_cb=None,
    cash_account_id: int | None = None,
) -> dict:
    """Insert Coinbase records into the database.

    Uses the same _get_or_create_security logic as revolut_importer
    (saved mapping → ticker → name → create new) for crypto assets.

    When *cash_account_id* is provided, CashIn/CashOut records are inserted
    as ``Transactions`` into that account instead of as ``Investments`` into
    the brokerage account.  This supports the two-account setup where one
    account holds crypto investments and another holds the cash/EUR movements.
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
                "DELETE FROM Investments  WHERE Accounts_Id = %s AND Description LIKE %s",
                (account_id, f"{_CB_PREFIX}%"),
            )
            # In replace mode delete cash-flow records from the appropriate table/account
            if cash_account_id:
                cur.execute(
                    "DELETE FROM Transactions WHERE Accounts_Id = %s AND Description LIKE %s",
                    (cash_account_id, f"{_CB_PREFIX}%"),
                )
            else:
                cur.execute(
                    "DELETE FROM Transactions WHERE Accounts_Id = %s AND Description LIKE %s",
                    (account_id, f"{_CB_PREFIX}%"),
                )

        total = len(inv_records) + len(tx_records)
        done  = 0

        for rec in inv_records:
            desc = rec["desc"]

            is_cash_flow = rec["action"] in ("CashIn", "CashOut") and not rec.get("symbol")

            if is_cash_flow and cash_account_id:
                # Route CashIn/CashOut to the dedicated cash account as Transactions
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
                # Legacy: no separate cash account — keep as Investment CashIn/CashOut
                if not replace_mode and _inv_exists(cur, account_id, desc):
                    counts["investments_skip"] += 1
                else:
                    # CashIn/CashOut are already in EUR (account currency); FX = 1.0
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
                sec_id = _get_or_create_security(
                    cur,
                    rec["symbol"], rec["name"],
                    rec.get("currency", "EUR"), rec.get("asset_category", "CRYPTO"),
                    source="Coinbase", isin="",
                )
                if not replace_mode and _inv_exists(cur, account_id, desc):
                    counts["investments_skip"] += 1
                else:
                    # Coinbase native_amount is already in EUR; FX = 1.0
                    cur.execute(
                        """INSERT INTO Investments
                               (Accounts_Id, Securities_Id, Date, Action, Quantity,
                                Price_Per_Share, Commission,
                                Total_Amount_AccCur, Total_Amount_SecCur, FX_Rate,
                                Description)
                           VALUES (%s, %s, %s, %s::investments_action, %s, %s, %s, %s, %s, %s, %s)""",
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

    # When using a dedicated cash account, skip auto-linked-transaction creation
    # (the cash account already holds the direct transactions).
    if not cash_account_id:
        from database.crud import create_linked_cash_transactions_for_unlinked, get_linked_account_id
        _cb_linked = get_linked_account_id(account_id)
        if _cb_linked:
            create_linked_cash_transactions_for_unlinked(account_id, _cb_linked)
            update_accounts_balances(_cb_linked)
            update_investment_balances()

    if cash_account_id:
        update_accounts_balances(cash_account_id)

    return counts
