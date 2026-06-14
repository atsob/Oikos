"""Salt Edge Account Information API v5 client.

Authentication
--------------
Every request includes two headers:
  App-id : your application ID
  Secret : your API secret

Flow
----
1. POST /customers                   → create (or reuse) a customer_id
2. POST /connect_sessions/create     → get connect_url (hosted widget)
3. User visits connect_url, authenticates at their bank, is redirected back
4. GET  /connections?customer_id=…   → retrieve connection_id
5. GET  /accounts?connection_id=…    → list bank accounts
6. GET  /transactions?connection_id=…&account_id=… → fetch transactions
"""

from __future__ import annotations

import requests
from datetime import date
from typing import Any

_BASE = "https://www.saltedge.com/api/v5"


class SaltEdgeError(Exception):
    pass


class SaltEdgeClient:
    def __init__(self, app_id: str, secret: str):
        self._app_id  = app_id
        self._secret  = secret

    # ── Internals ────────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {
            "App-id":       self._app_id,
            "Secret":       self._secret,
            "Content-Type": "application/json",
            "Accept":       "application/json",
        }

    def _get(self, path: str, **params) -> Any:
        resp = requests.get(
            f"{_BASE}{path}",
            headers=self._headers(),
            params={k: v for k, v in params.items() if v is not None},
            timeout=15,
        )
        self._raise_for_status(resp)
        return resp.json()

    def _post(self, path: str, payload: dict) -> Any:
        resp = requests.post(
            f"{_BASE}{path}",
            headers=self._headers(),
            json={"data": payload},
            timeout=15,
        )
        self._raise_for_status(resp)
        return resp.json()

    def _delete(self, path: str) -> Any:
        resp = requests.delete(f"{_BASE}{path}", headers=self._headers(), timeout=15)
        self._raise_for_status(resp)
        return resp.json()

    @staticmethod
    def _raise_for_status(resp: requests.Response) -> None:
        if not resp.ok:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise SaltEdgeError(f"Salt Edge API error {resp.status_code}: {detail}")

    # ── Customers ────────────────────────────────────────────────────────────

    def create_customer(self, identifier: str) -> dict:
        """Create a customer. 'identifier' is any unique string you choose."""
        return self._post("/customers", {"identifier": identifier})["data"]

    def list_customers(self) -> list[dict]:
        return self._get("/customers")["data"]

    def get_customer(self, customer_id: str) -> dict:
        return self._get(f"/customers/{customer_id}")["data"]

    def delete_customer(self, customer_id: str) -> dict:
        return self._delete(f"/customers/{customer_id}")["data"]

    # ── Connect sessions ─────────────────────────────────────────────────────

    def create_connect_session(
        self,
        customer_id: str,
        return_to: str,
        from_date: date | None = None,
        provider_code: str | None = None,
    ) -> dict:
        """Return a connect_url the user should visit to authenticate at their bank.

        Parameters
        ----------
        customer_id   : Salt Edge customer ID (from create_customer).
        return_to     : URL the widget redirects to after authentication.
        from_date     : Earliest date for transaction history (defaults to 90 days ago).
        provider_code : Pre-select a specific bank (optional — skips the search step).
        """
        from datetime import timedelta
        if from_date is None:
            from_date = date.today() - timedelta(days=90)

        payload: dict = {
            "customer_id": customer_id,
            "consent": {
                "scopes":     ["account_details", "transactions_details"],
                "from_date":  from_date.isoformat(),
            },
            "attempt": {
                "return_to": return_to,
            },
        }
        if provider_code:
            payload["provider_code"] = provider_code

        return self._post("/connect_sessions/create", payload)["data"]

    # ── Connections ──────────────────────────────────────────────────────────

    def list_connections(self, customer_id: str) -> list[dict]:
        return self._get("/connections", customer_id=customer_id)["data"]

    def get_connection(self, connection_id: str) -> dict:
        return self._get(f"/connections/{connection_id}")["data"]

    def delete_connection(self, connection_id: str) -> dict:
        return self._delete(f"/connections/{connection_id}")["data"]

    # ── Accounts ─────────────────────────────────────────────────────────────

    def list_accounts(self, connection_id: str) -> list[dict]:
        return self._get("/accounts", connection_id=connection_id)["data"]

    # ── Transactions ─────────────────────────────────────────────────────────

    def list_transactions(
        self,
        connection_id: str,
        account_id:    str,
        from_id:       str | None = None,
        date_from:     date | None = None,
        date_to:       date | None = None,
    ) -> list[dict]:
        """Fetch all transactions, following Salt Edge pagination (next_id cursor)."""
        all_txns: list[dict] = []
        next_id: str | None = from_id

        while True:
            resp = self._get(
                "/transactions",
                connection_id = connection_id,
                account_id    = account_id,
                from_id       = next_id,
                date_from     = date_from.isoformat() if date_from else None,
                date_to       = date_to.isoformat()   if date_to   else None,
            )
            page = resp.get("data", [])
            all_txns.extend(page)

            # Salt Edge uses a 'next_id' cursor in meta for pagination
            next_id = resp.get("meta", {}).get("next_id")
            if not next_id or not page:
                break

        return all_txns

    # ── Providers ────────────────────────────────────────────────────────────

    def list_providers(self, country_code: str) -> list[dict]:
        """Return providers for a 2-letter ISO country code (e.g. 'GR')."""
        return self._get("/providers", country_code=country_code)["data"]


# ── Normalisation ─────────────────────────────────────────────────────────────

def normalise_transactions(raw_transactions: list[dict]) -> list[dict]:
    """Convert Salt Edge transaction list to the app's normalised format.

    Returns a list of dicts with keys: date, description, amount, balance.
    Pending transactions are included but flagged in the description.
    """
    import re
    from datetime import datetime

    rows = []
    for txn in raw_transactions:
        # Date
        raw_date = txn.get("made_on", "")
        try:
            txn_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except Exception:
            continue

        # Amount — Salt Edge uses positive for credits, negative for debits (same sign convention)
        try:
            amount = float(txn.get("amount", 0))
        except (ValueError, TypeError):
            continue

        # Description — 'description' field, fall back to 'extra.original_description'
        extra = txn.get("extra", {}) or {}
        desc = (
            txn.get("description")
            or extra.get("original_description")
            or extra.get("payee")
            or extra.get("merchant_name")
            or str(txn.get("id", ""))
        )
        desc = re.sub(r"\s+", " ", str(desc).strip())
        if not desc:
            continue

        # Append pending flag if applicable
        if txn.get("status") == "pending":
            desc = f"[PENDING] {desc}"

        # Running balance (not always present in Salt Edge)
        balance = None

        rows.append({"date": txn_date, "description": desc, "amount": amount, "balance": balance})

    rows.sort(key=lambda r: r["date"])
    return rows
