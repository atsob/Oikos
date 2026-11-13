"""Enable Banking (PSD2 open banking) — read balances and transactions of the user's own bank accounts.

Enable Banking holds the PSD2 licence that a bank's account API requires; an application registered in its
Control Panel in the PRODUCTION environment and "activated by linking accounts" runs in *restricted mode*: it can
read only the accounts its owner linked there, which is exactly a personal app like this one.

Authentication: every request carries a JWT signed RS256 with the application's private key (the .pem file the
Control Panel downloads when the application is registered), header kid = application ID,
iss "enablebanking.com", aud "api.enablebanking.com", at most 24 h long.

Flow: POST /auth (bank + redirect URL) -> the user approves at the bank -> the browser comes back with ?code=&state=
-> POST /sessions {code} -> session_id + the accounts (uid per account, valid while the session is AUTHORIZED) ->
GET /accounts/{uid}/balances and /transactions. The consent lasts up to the bank's maximum (usually 180 days);
after that the bank must be connected again.

Settings (app_settings): eb_app_id, eb_private_key (PEM; never sent back to the browser), eb_connections (JSON list,
one per connected bank), eb_account_map (JSON {identification_hash: Oikos account id} — the hash stays the same
when a bank is connected again, the uid does not), eb_pending_state (the state of the login in progress).
"""
from __future__ import annotations

import json
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

from database.queries import get_app_setting, save_app_setting

BASE = "https://api.enablebanking.com"
_TIMEOUT = 60
_token: dict[str, Any] = {"jwt": "", "exp": 0, "kid": ""}


class EnableBankingError(RuntimeError):
    pass


# ── Credentials ─────────────────────────────────────────────────────────────

def credentials_set() -> bool:
    return bool(get_app_setting("eb_app_id") and get_app_setting("eb_private_key"))


def save_credentials(app_id: str, private_key: str | None) -> None:
    """Store the application ID and, when given, the private key — after checking the key is a usable RSA key."""
    app_id = (app_id or "").strip()
    if not app_id:
        raise EnableBankingError("The application ID is required.")
    if private_key:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        try:
            load_pem_private_key(private_key.encode(), password=None)
        except Exception:
            raise EnableBankingError("That is not a usable private key: upload the .pem file the Control Panel "
                                     "downloaded when the application was registered.")
        save_app_setting("eb_private_key", private_key.strip() + "\n")
    elif not get_app_setting("eb_private_key"):
        raise EnableBankingError("The private key (.pem file) is required.")
    save_app_setting("eb_app_id", app_id)
    _token.update(jwt="", exp=0, kid="")


def _jwt() -> str:
    import jwt as pyjwt
    app_id = get_app_setting("eb_app_id") or ""
    key = get_app_setting("eb_private_key") or ""
    if not (app_id and key):
        raise EnableBankingError("Enable Banking is not set up: enter the application ID and private key first.")
    now = int(time.time())
    if _token["jwt"] and _token["kid"] == app_id and _token["exp"] - now > 300:
        return _token["jwt"]
    exp = now + 3600
    tok = pyjwt.encode({"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": now, "exp": exp},
                       key, algorithm="RS256", headers={"kid": app_id})
    _token.update(jwt=tok, exp=exp, kid=app_id)
    return tok


def _req(method: str, path: str, **kw) -> Any:
    r = requests.request(method, BASE + path, headers={"Authorization": f"Bearer {_jwt()}"}, timeout=_TIMEOUT, **kw)
    if r.status_code >= 400:
        try:
            body = r.json()
            msg = body.get("message") or body.get("error") or r.text
            if body.get("detail"):
                msg = f"{msg}: {body['detail']}"
        except Exception:
            msg = r.text
        raise EnableBankingError(f"Enable Banking {r.status_code}: {str(msg)[:400]}")
    return r.json() if r.content else {}


# ── Application, banks, connecting ──────────────────────────────────────────

def application() -> dict:
    """name, environment, active, redirect_urls … of the registered application (checks the credentials)."""
    a = _req("GET", "/application")
    return {k: a.get(k) for k in ("name", "environment", "active", "redirect_urls", "countries")}


def banks(country: str) -> list[dict]:
    data = _req("GET", "/aspsps", params={"country": country.upper(), "service": "AIS"})
    out = [{"name": b["name"], "country": b["country"], "logo": b.get("logo"), "beta": b.get("beta", False),
            "maximum_consent_validity": b.get("maximum_consent_validity"), "psu_types": b.get("psu_types", [])}
           for b in data.get("aspsps", [])]
    return sorted([b for b in out if "personal" in b["psu_types"]] or out, key=lambda b: b["name"].lower())


def start_auth(bank: str, country: str, redirect_url: str) -> str:
    """URL of the bank's login page; the browser comes back to redirect_url with ?code=&state=."""
    info = next((b for b in banks(country) if b["name"] == bank), None)
    if not info:
        raise EnableBankingError(f"{bank} ({country}) is not offered by Enable Banking.")
    seconds = int(info.get("maximum_consent_validity") or 90 * 86400)
    valid_until = datetime.now(timezone.utc) + timedelta(seconds=max(seconds - 3600, 3600))
    state = "eb_" + secrets.token_urlsafe(16)
    data = _req("POST", "/auth", json={
        "access": {"valid_until": valid_until.isoformat(), "balances": True, "transactions": True},
        "aspsp": {"name": bank, "country": country.upper()},
        "state": state, "redirect_url": redirect_url, "psu_type": "personal",
    })
    save_app_setting("eb_pending_state", state)
    return data["url"]


def _connections() -> list[dict]:
    try:
        return json.loads(get_app_setting("eb_connections") or "[]")
    except Exception:
        return []


def _save_connections(conns: list[dict]) -> None:
    save_app_setting("eb_connections", json.dumps(conns))


def account_map() -> dict[str, int]:
    try:
        return {k: int(v) for k, v in json.loads(get_app_setting("eb_account_map") or "{}").items() if v}
    except Exception:
        return {}


def save_account_map(m: dict) -> None:
    save_app_setting("eb_account_map", json.dumps({k: int(v) for k, v in m.items() if v}))


def _mask(iban: str | None) -> str:
    return f"{iban[:4]} … {iban[-4:]}" if iban and len(iban) > 8 else (iban or "")


def finish_auth(code: str, state: str) -> dict:
    """Turn the code from the bank's redirect into a session; replaces an older connection to the same bank."""
    expected = get_app_setting("eb_pending_state") or ""
    if not expected or not secrets.compare_digest(state or "", expected):
        raise EnableBankingError("This bank login was not started from this Oikos (or was already used). Start it again.")
    save_app_setting("eb_pending_state", "")
    s = _req("POST", "/sessions", json={"code": code})
    accounts = []
    for a in s.get("accounts", []):
        acc_id = a.get("account_id") or {}
        accounts.append({
            "uid": a["uid"], "hash": a.get("identification_hash") or a["uid"],
            "name": a.get("name") or a.get("product") or a.get("details") or "",
            "product": a.get("product") or "", "currency": a.get("currency") or "",
            "iban": _mask(acc_id.get("iban") or (acc_id.get("other") or {}).get("identification")),
        })
    conn = {"session_id": s["session_id"], "bank": s.get("aspsp", {}).get("name", ""),
            "country": s.get("aspsp", {}).get("country", ""),
            "valid_until": (s.get("access") or {}).get("valid_until"), "accounts": accounts,
            "connected_at": datetime.now(timezone.utc).isoformat()}
    old = [c for c in _connections() if (c["bank"], c["country"]) == (conn["bank"], conn["country"])]
    for c in old:                                   # the new consent replaces the old one at the bank too
        try:
            _req("DELETE", f"/sessions/{c['session_id']}")
        except Exception:
            pass
    _save_connections([c for c in _connections() if c not in old] + [conn])
    return conn


def disconnect(session_id: str) -> None:
    try:
        _req("DELETE", f"/sessions/{session_id}")
    except EnableBankingError:
        pass                                          # already expired/revoked at the bank: just forget it
    _save_connections([c for c in _connections() if c["session_id"] != session_id])


def status() -> dict:
    m = account_map()
    now = datetime.now(timezone.utc)
    conns = []
    for c in _connections():
        try:
            expired = bool(c.get("valid_until")) and datetime.fromisoformat(c["valid_until"]) <= now
        except ValueError:
            expired = False
        conns.append({**c, "expired": expired,
                      "accounts": [{**a, "oikos_account_id": m.get(a["hash"])} for a in c["accounts"]]})
    return {"app_id": get_app_setting("eb_app_id") or "", "key_set": bool(get_app_setting("eb_private_key")),
            "connections": conns}


def linked_account(oikos_account_id: int) -> tuple[dict, dict] | None:
    """(connection, bank account) linked to an Oikos account, newest connection first."""
    for c in reversed(_connections()):
        for a in c["accounts"]:
            if account_map().get(a["hash"]) == oikos_account_id:
                return c, a
    return None


# ── Data ────────────────────────────────────────────────────────────────────

def _amount(x: dict | None) -> float | None:
    try:
        return float((x or {}).get("amount"))
    except (TypeError, ValueError):
        return None


def balance(uid: str) -> dict | None:
    """The booked (accounting) balance when the bank gives one, else its first balance: {amount, currency, date, type}."""
    bals = _req("GET", f"/accounts/{uid}/balances").get("balances", [])
    if not bals:
        return None
    order = ["CLBD", "ITBD", "XPCD", "ITAV", "CLAV", "OPBD"]
    b = min(bals, key=lambda b: order.index(b.get("balance_type")) if b.get("balance_type") in order else 99)
    return {"amount": _amount(b.get("balance_amount")), "currency": (b.get("balance_amount") or {}).get("currency"),
            "date": b.get("reference_date") or (b.get("last_change_date_time") or "")[:10] or None,
            "type": b.get("balance_type")}


def _description(t: dict) -> tuple[str, str]:
    """(description, counterparty) of a transaction."""
    party = (t.get("creditor") or {}).get("name") if t.get("credit_debit_indicator") == "DBIT" \
        else (t.get("debtor") or {}).get("name")
    remit = " ".join(s.strip() for s in (t.get("remittance_information") or []) if s and s.strip())
    code = (t.get("bank_transaction_code") or {}).get("description") or ""
    parts = [p for p in (party, remit) if p]
    desc = " — ".join(parts) if parts else (t.get("note") or code or "")
    return " ".join(desc.split()), party or ""


def transactions(uid: str, date_from: date, date_to: date | None = None) -> list[dict]:
    """Booked transactions as statement rows {date, description, amount, balance, payee, ref}, oldest first.
    Pending ones are left out: a bank often changes their text or amount when it books them, so importing them
    would leave duplicates."""
    params: dict[str, Any] = {"date_from": date_from.isoformat()}
    if date_to:
        params["date_to"] = date_to.isoformat()
    rows, key, pages = [], None, 0
    while True:
        p = dict(params, **({"continuation_key": key} if key else {}))
        data = _req("GET", f"/accounts/{uid}/transactions", params=p)
        for t in data.get("transactions", []):
            if t.get("status") not in (None, "BOOK"):
                continue
            amt = _amount(t.get("transaction_amount"))
            if amt is None:
                continue
            if t.get("credit_debit_indicator") == "DBIT":
                amt = -abs(amt)
            elif t.get("credit_debit_indicator") == "CRDT":
                amt = abs(amt)
            d = t.get("booking_date") or t.get("value_date") or t.get("transaction_date")
            if not d:
                continue
            desc, party = _description(t)
            rows.append({"date": d[:10], "description": desc, "amount": round(amt, 2),
                         "balance": _amount(t.get("balance_after_transaction")), "payee": party,
                         "ref": t.get("entry_reference") or t.get("transaction_id")})
        key, pages = data.get("continuation_key"), pages + 1
        if not key or pages >= 100:
            break
    if date_to:
        rows = [r for r in rows if r["date"] <= date_to.isoformat()]
    rows = [r for r in rows if r["date"] >= date_from.isoformat()]
    return sorted(rows, key=lambda r: r["date"])
