"""GoCardless Bank Account Data API client (formerly Nordigen).

Authentication flow
-------------------
1. POST /token/new/          → access_token (24h) + refresh_token (30d)
2. GET  /institutions/       → list banks by country
3. POST /agreements/enduser/ → create access agreement (optional but recommended)
4. POST /requisitions/       → create requisition, get redirect link
5. User opens link → authenticates at their bank → redirected back
6. GET  /requisitions/{id}/  → accounts list (once authenticated)
7. GET  /accounts/{id}/transactions/ → transactions for date range
"""

from __future__ import annotations

import requests
from datetime import date, timedelta
from typing import Any

_BASE = "https://bankaccountdata.gocardless.com/api/v2"


class GoCardlessError(Exception):
    pass


class GoCardlessClient:
    def __init__(self, secret_id: str, secret_key: str):
        self._secret_id  = secret_id
        self._secret_key = secret_key
        self._access_token:  str | None = None
        self._refresh_token: str | None = None

    # ── Authentication ──────────────────────────────────────────────────────

    def authenticate(self) -> None:
        """Obtain a new access + refresh token pair."""
        resp = requests.post(
            f"{_BASE}/token/new/",
            json={"secret_id": self._secret_id, "secret_key": self._secret_key},
            timeout=15,
        )
        self._raise_for_status(resp)
        data = resp.json()
        self._access_token  = data["access"]
        self._refresh_token = data["refresh"]

    def refresh_access(self) -> None:
        """Use refresh token to get a new access token without re-authenticating."""
        if not self._refresh_token:
            self.authenticate()
            return
        resp = requests.post(
            f"{_BASE}/token/refresh/",
            json={"refresh": self._refresh_token},
            timeout=15,
        )
        self._raise_for_status(resp)
        self._access_token = resp.json()["access"]

    def _headers(self) -> dict:
        if not self._access_token:
            self.authenticate()
        return {"Authorization": f"Bearer {self._access_token}", "Accept": "application/json"}

    def _get(self, path: str, **params) -> Any:
        resp = requests.get(f"{_BASE}{path}", headers=self._headers(), params=params, timeout=15)
        if resp.status_code == 401:
            self.refresh_access()
            resp = requests.get(f"{_BASE}{path}", headers=self._headers(), params=params, timeout=15)
        self._raise_for_status(resp)
        return resp.json()

    def _post(self, path: str, payload: dict) -> Any:
        resp = requests.post(f"{_BASE}{path}", headers=self._headers(), json=payload, timeout=15)
        if resp.status_code == 401:
            self.refresh_access()
            resp = requests.post(f"{_BASE}{path}", headers=self._headers(), json=payload, timeout=15)
        self._raise_for_status(resp)
        return resp.json()

    def _delete(self, path: str) -> None:
        resp = requests.delete(f"{_BASE}{path}", headers=self._headers(), timeout=15)
        if resp.status_code == 401:
            self.refresh_access()
            resp = requests.delete(f"{_BASE}{path}", headers=self._headers(), timeout=15)
        # 204 No Content is success for DELETE
        if resp.status_code not in (200, 204):
            self._raise_for_status(resp)

    @staticmethod
    def _raise_for_status(resp: requests.Response) -> None:
        if not resp.ok:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise GoCardlessError(f"GoCardless API error {resp.status_code}: {detail}")

    # ── Institutions ────────────────────────────────────────────────────────

    def list_institutions(self, country: str) -> list[dict]:
        """Return institutions available for a 2-letter ISO country code (e.g. 'GR')."""
        return self._get("/institutions/", country=country)

    # ── Requisitions ────────────────────────────────────────────────────────

    def create_requisition(
        self,
        institution_id: str,
        redirect_url: str,
        reference: str = "personal-finance",
        agreement_id: str | None = None,
    ) -> dict:
        """Create a requisition and return the full response (includes 'link' and 'id')."""
        payload: dict = {
            "redirect":       redirect_url,
            "institution_id": institution_id,
            "reference":      reference,
        }
        if agreement_id:
            payload["agreement"] = agreement_id
        return self._post("/requisitions/", payload)

    def get_requisition(self, requisition_id: str) -> dict:
        return self._get(f"/requisitions/{requisition_id}/")

    def delete_requisition(self, requisition_id: str) -> None:
        self._delete(f"/requisitions/{requisition_id}/")

    # ── Accounts ────────────────────────────────────────────────────────────

    def get_account_details(self, account_id: str) -> dict:
        return self._get(f"/accounts/{account_id}/details/")

    def get_account_balances(self, account_id: str) -> dict:
        return self._get(f"/accounts/{account_id}/balances/")

    def get_transactions(
        self,
        account_id: str,
        date_from: date | None = None,
        date_to:   date | None = None,
    ) -> dict:
        """Return the raw transactions dict with 'booked' and 'pending' lists."""
        params: dict = {}
        if date_from:
            params["date_from"] = date_from.isoformat()
        if date_to:
            params["date_to"] = date_to.isoformat()
        return self._get(f"/accounts/{account_id}/transactions/", **params)


# ── Normalisation ────────────────────────────────────────────────────────────

def normalise_transactions(raw_transactions: dict) -> list[dict]:
    """Convert the GoCardless transactions response to the app's normalised format.

    Returns a list of dicts with keys: date, description, amount, balance.
    Only 'booked' (posted) transactions are included; 'pending' are skipped.
    """
    import re
    from datetime import datetime

    rows = []
    for txn in raw_transactions.get("booked", []):
        # Date — prefer valueDate, fall back to bookingDate
        raw_date = txn.get("valueDate") or txn.get("bookingDate", "")
        try:
            txn_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except Exception:
            continue

        # Amount — transactionAmount.amount is a signed string (negative = debit)
        amt_block = txn.get("transactionAmount", {})
        try:
            amount = float(str(amt_block.get("amount", "0")).replace(",", "."))
        except (ValueError, TypeError):
            continue

        # Description — try several fields in preference order
        desc = (
            txn.get("remittanceInformationUnstructured")
            or txn.get("remittanceInformationStructured")
            or txn.get("additionalInformation")
            or txn.get("creditorName")
            or txn.get("debtorName")
            or txn.get("transactionId", "")
        )
        desc = re.sub(r"\s+", " ", str(desc).strip())
        if not desc:
            continue

        # Running balance (not always present)
        balance = None
        bal_list = txn.get("balanceAfterTransaction", {})
        if bal_list:
            try:
                balance = float(str(bal_list.get("balanceAmount", {}).get("amount", "")).replace(",", "."))
            except Exception:
                pass

        rows.append({"date": txn_date, "description": desc, "amount": amount, "balance": balance})

    rows.sort(key=lambda r: r["date"])
    return rows
