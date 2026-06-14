"""PayPal Transactions API connector.

Fetches transaction history via the PayPal v1/reporting/transactions endpoint.
Requires a PayPal REST application with the **Transaction Search** permission enabled.

Setup
-----
1. Go to https://developer.paypal.com/developer/applications
2. Create (or select) a Live app.
3. Enable the "Transaction Search" feature under "Live App Settings".
4. Copy the Client ID and Secret into the app UI (or set PAYPAL_CLIENT_ID /
   PAYPAL_CLIENT_SECRET in your .env file for convenience).
"""

from __future__ import annotations

from base64 import b64encode
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# API base URLs
# ---------------------------------------------------------------------------
_LIVE_URL    = "https://api-m.paypal.com"
_SANDBOX_URL = "https://api-m.sandbox.paypal.com"

# PayPal's Reporting API is limited to 31 days per request.
_MAX_CHUNK_DAYS = 31

# Only import completed or pending transactions; skip denied / reversed.
_INCLUDE_STATUSES = {"S", "P"}   # S=Success, P=Pending


def _base(sandbox: bool) -> str:
    return _SANDBOX_URL if sandbox else _LIVE_URL


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def get_access_token(client_id: str, client_secret: str,
                     sandbox: bool = False) -> str:
    """Exchange client credentials for a Bearer access token."""
    creds = b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp  = requests.post(
        f"{_base(sandbox)}/v1/oauth2/token",
        headers={
            "Authorization": f"Basic {creds}",
            "Content-Type":  "application/x-www-form-urlencoded",
            "Accept":        "application/json",
        },
        data="grant_type=client_credentials",
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def _fetch_chunk(token: str, start: datetime, end: datetime,
                 sandbox: bool, currency: Optional[str]) -> list[dict]:
    """Fetch one ≤31-day chunk; handles PayPal pagination automatically."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    results: list[dict] = []
    page = 1

    while True:
        params: dict[str, str] = {
            "start_date": start.strftime("%Y-%m-%dT00:00:00-0000"),
            "end_date":   end.strftime("%Y-%m-%dT23:59:59-0000"),
            "fields":     "all",
            "page_size":  "500",
            "page":       str(page),
        }
        if currency:
            params["transaction_currency"] = currency.upper()

        resp = requests.get(
            f"{_base(sandbox)}/v1/reporting/transactions",
            headers=headers,
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        results.extend(data.get("transaction_details", []))

        if page >= int(data.get("total_pages", 1)):
            break
        page += 1

    return results


def fetch_transactions(
    client_id: str,
    client_secret: str,
    date_from: date,
    date_to: date,
    sandbox: bool = False,
    currency: Optional[str] = None,
) -> list[dict]:
    """Fetch all PayPal transactions for a date range.

    Splits automatically into ≤31-day chunks as required by the API.
    Returns the raw ``transaction_details`` list from the Reporting API.
    """
    token = get_access_token(client_id, client_secret, sandbox)

    results: list[dict] = []
    chunk_start = datetime(date_from.year, date_from.month, date_from.day,
                           tzinfo=timezone.utc)
    limit       = datetime(date_to.year, date_to.month, date_to.day,
                           tzinfo=timezone.utc)

    while chunk_start <= limit:
        chunk_end = min(
            chunk_start + timedelta(days=_MAX_CHUNK_DAYS - 1),
            limit,
        )
        results.extend(_fetch_chunk(token, chunk_start, chunk_end, sandbox, currency))
        chunk_start = chunk_end + timedelta(days=1)

    return results


# ---------------------------------------------------------------------------
# Normalise to the app's standard statement DataFrame format
# ---------------------------------------------------------------------------

def to_dataframe(raw: list[dict]) -> pd.DataFrame:
    """Convert raw PayPal ``transaction_details`` into a normalised DataFrame.

    Output columns (matching the app's standard import format):

    ============  ============================================================
    date          ``date`` — transaction initiation date
    description   ``str``  — payee / sender name + note (if any)
    amount        ``float``— negative = sent/debit, positive = received/credit
    balance       ``float``— PayPal ending balance after this transaction
                             (NaN when the API does not return it)
    ============  ============================================================

    Transactions with status not in {S, P} and zero-amount entries are skipped.
    Non-EUR transactions get a ``[CCY]`` prefix in the description so they are
    easy to spot during the import review.
    """
    rows = []

    for txn in raw:
        info  = txn.get("transaction_info",  {})
        payer = txn.get("payer_info",        {})
        cart  = txn.get("cart_info",         {})

        # ── Status filter ─────────────────────────────────────────────────
        status = info.get("transaction_status", "")
        if status not in _INCLUDE_STATUSES:
            continue

        # ── Amount ────────────────────────────────────────────────────────
        amt_info = info.get("transaction_amount", {})
        try:
            amount = float(amt_info.get("value", 0))
        except (ValueError, TypeError):
            continue
        if amount == 0:
            continue

        currency = amt_info.get("currency_code", "")

        # ── Date ──────────────────────────────────────────────────────────
        raw_date = info.get("transaction_initiation_date", "")
        try:
            # PayPal: "2025-03-15T14:22:01+0000" — normalize offset for fromisoformat
            txn_date = datetime.fromisoformat(
                raw_date.replace("+0000", "+00:00").replace("-0000", "+00:00")
            ).date()
        except Exception:
            try:
                txn_date = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
            except Exception:
                continue   # skip rows with unparseable dates

        # ── Ending balance ────────────────────────────────────────────────
        bal_info = info.get("ending_balance", {})
        try:
            balance = float(bal_info.get("value")) if bal_info else float("nan")
        except (ValueError, TypeError):
            balance = float("nan")

        # ── Description: payee/sender name + optional note ────────────────
        pn = payer.get("payer_name", {})
        payee_name = (
            pn.get("alternate_full_name")
            or f"{pn.get('given_name', '')} {pn.get('surname', '')}".strip()
            or payer.get("email_address", "")
        )

        # Cart item name (common for marketplace payments, e.g. eBay, Etsy)
        items     = cart.get("item_details", [])
        item_name = items[0].get("item_name", "").strip() if items else ""

        note = (
            info.get("transaction_note", "")
            or info.get("invoice_id", "")
            or item_name
        )

        parts = [p for p in [payee_name, note] if p]
        description = " — ".join(parts) if parts else (
            info.get("transaction_event_code", "") or "PayPal"
        )

        # Prefix non-EUR amounts so they stand out in the review table
        if currency and currency.upper() != "EUR":
            description = f"[{currency}] {description}"

        rows.append({
            "date":        txn_date,
            "description": description.strip(),
            "amount":      amount,
            "balance":     balance,
        })

    if not rows:
        return pd.DataFrame(columns=["date", "description", "amount", "balance"])

    df = (
        pd.DataFrame(rows)
        .assign(
            date   = lambda d: pd.to_datetime(d["date"]).dt.date,
            amount = lambda d: d["amount"].astype(float),
        )
        .sort_values("date")
        .reset_index(drop=True)
    )
    return df
