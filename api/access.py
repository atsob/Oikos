"""Roles and account access.

Three roles (`Users.Role`):
  admin     everything, on every account — the users that existed before roles
  limited   read and write, but only on the accounts mapped to the user (Tools -> Account -> Users; the accounts linked to a mapped account, such as a
            brokerage's cash account, come along)
  readonly  the same accounts, read only: any request that could change anything is refused

Restricted users (limited, readonly) are held to an allowlist (RULES below). A request that matches no rule is refused with 403 — so a new endpoint is closed to
them until it is deliberately opened here — and a rule says how the account scope is applied to the endpoints it opens: an account in the query string or the body
must be one of the user's, an id in the path or the body is looked up and its account checked (transactions, investments, holdings), and the list endpoints
(`/dashboard/accounts`, `/investments/list`, `/investments/holdings`) narrow themselves to the user's accounts (`scope_ids`). Admins skip all of this.

What restricted users can use today: the Cash Register and the Investments of their accounts (viewing, and for `limited` adding, editing and deleting), the account
list, the categories / payees / securities used to fill the forms, the screen's remembered state, the Release Notes. Reports, the Dashboard, Static Data, Tools,
Importers, Market Data and the rest are admin-only until each is scoped and added below.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from fastapi import HTTPException, Request

from database.connection import get_connection

ACCOUNT_BODY_KEYS = ("accounts_id", "account_id", "accounts_id_target", "cash_account_id", "from_account_id", "to_account_id")
PROTECTED_PREFS = ("app-settings",)          # shared settings; a restricted user may keep their screen state but not change these


@dataclass(frozen=True)
class Rule:
    methods: tuple[str, ...]
    pattern: re.Pattern
    write: bool = False                      # changes data: refused for a read-only user
    query_account: str | None = None         # a query parameter holding an account id, which must be one of the user's when given
    body_accounts: bool = False              # account ids in the JSON body must be the user's
    rows: str | None = None                  # what ids in the path / body point at: "tx_path", "tx_ids", "inv_path", "inv_ids", "holding_path"
    path_account: str | None = None          # a path group holding an account id
    pref_key: bool = False                   # the path ends in a preference key (see PROTECTED_PREFS)


def _r(methods: str, pattern: str, **kw) -> Rule:
    return Rule(tuple(methods.split("|")), re.compile(pattern), **kw)


RULES: list[Rule] = [
    _r("GET", r"^/api/auth/me$"),
    _r("POST", r"^/api/auth/change-password$"),
    _r("GET", r"^/api/changelog$"),
    _r("GET", r"^/api/preferences$"),
    _r("PUT", r"^/api/preferences/(?P<key>[^/]+)$", pref_key=True),
    # reference data used to fill the forms
    _r("GET", r"^/api/static-data/(categories|payees)$"),
    _r("GET", r"^/api/market-data/securities$"),
    # accounts (the endpoint narrows the list to the user's accounts)
    _r("GET", r"^/api/dashboard/accounts$"),
    # Cash Register
    _r("GET", r"^/api/register/transactions(/ids)?$", query_account="account_id"),
    _r("GET", r"^/api/register/transactions/(?P<id>\d+)(/splits)?$", rows="tx_path"),
    _r("POST", r"^/api/register/transactions$", write=True, body_accounts=True),
    _r("PUT", r"^/api/register/transactions/(?P<id>\d+)$", write=True, body_accounts=True, rows="tx_path"),
    _r("PUT", r"^/api/register/transactions/(?P<id>\d+)/splits$", write=True, rows="tx_path"),
    _r("DELETE", r"^/api/register/transactions/(?P<id>\d+)$", write=True, rows="tx_path"),
    _r("POST", r"^/api/register/transactions/batch-delete$", write=True, rows="tx_ids"),
    _r("POST", r"^/api/register/transactions/batch-move$", write=True, body_accounts=True, rows="tx_ids"),
    _r("POST", r"^/api/register/transfers$", write=True, body_accounts=True),
    _r("POST", r"^/api/register/(clear|reconcile)$", write=True, body_accounts=True),
    # Investments
    _r("GET", r"^/api/investments/list$", query_account="account_id"),
    _r("GET", r"^/api/investments/holdings$", query_account="account_id"),
    _r("GET", r"^/api/investments/linked-account/(?P<account_id>\d+)$", path_account="account_id"),
    _r("POST", r"^/api/investments/transactions$", write=True, body_accounts=True),
    _r("PUT", r"^/api/investments/transactions/(?P<id>\d+)$", write=True, body_accounts=True, rows="inv_path"),
    _r("DELETE", r"^/api/investments/transactions/(?P<id>\d+)$", write=True, rows="inv_path"),
    _r("POST", r"^/api/investments/transactions/batch-(update|delete)$", write=True, body_accounts=True, rows="inv_ids"),
    _r("PUT", r"^/api/investments/holdings/(?P<id>\d+)$", write=True, rows="holding_path"),
]


# ── the user, from the session require_auth stored on the request ────────────────────────────────

def user_of(request: Request) -> dict:
    return getattr(request.state, "user", None) or {}


def role_of(request: Request) -> str:
    return user_of(request).get("role", "admin")


def scope_ids(request: Request) -> list[int] | None:
    """The accounts the user may use, or None for every account (an admin)."""
    if role_of(request) == "admin":
        return None
    return list(user_of(request).get("accounts") or [])


def scope_clause(request: Request, column: str) -> str:
    """` AND column IN (…)` narrowing a query to the user's accounts, or an empty string for an admin."""
    ids = scope_ids(request)
    if ids is None:
        return ""
    return f" AND {column} IN ({','.join(str(int(i)) for i in ids) or 'NULL'})"


def forbid(detail: str = "Not permitted for your role"):
    raise HTTPException(403, detail)


def check_accounts(request: Request, ids) -> None:
    allowed = scope_ids(request)
    if allowed is None:
        return
    bad = {int(i) for i in ids if i not in (None, "")} - set(allowed)
    if bad:
        forbid("That account is not one of yours")


# ── looking up what an id points at ──────────────────────────────────────────────────────────────

def _accounts_of_rows(kind: str, ids: list[int]) -> set[int]:
    if not ids:
        return set()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if kind == "tx":
                cur.execute("""SELECT Accounts_Id, Accounts_Id_Target, Transfers_Id FROM Transactions WHERE Transactions_Id = ANY(%s)""", (ids,))
                rows = cur.fetchall()
                out = {a for r in rows for a in (r[0], r[1]) if a is not None}
                tids = [r[2] for r in rows if r[2] is not None]
                if tids:                                                     # the other leg of a transfer lives in another account
                    cur.execute("SELECT Accounts_Id FROM Transactions WHERE Transfers_Id = ANY(%s)", (tids,))
                    out |= {r[0] for r in cur.fetchall()}
                return out
            if kind == "inv":
                cur.execute("""SELECT i.Accounts_Id, t.Accounts_Id, i.Transfers_Id FROM Investments i
                               LEFT JOIN Transactions t ON t.Transactions_Id = i.Transactions_Id WHERE i.Investments_Id = ANY(%s)""", (ids,))
                rows = cur.fetchall()
                out = {a for r in rows for a in (r[0], r[1]) if a is not None}
                tids = [r[2] for r in rows if r[2] is not None]
                if tids:
                    cur.execute("SELECT Accounts_Id FROM Investments WHERE Transfers_Id = ANY(%s)", (tids,))
                    out |= {r[0] for r in cur.fetchall()}
                return out
            if kind == "holding":
                cur.execute("SELECT Accounts_Id FROM Holdings WHERE Holdings_Id = ANY(%s)", (ids,))
                return {r[0] for r in cur.fetchall()}
    finally:
        conn.close()
    return set()


def linked_accounts(account_ids: list[int]) -> set[int]:
    """The accounts linked to the given ones (a brokerage's cash account)."""
    if not account_ids:
        return set()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT Accounts_Id_Linked FROM Accounts WHERE Accounts_Id = ANY(%s) AND Accounts_Id_Linked IS NOT NULL", (account_ids,))
            return {r[0] for r in cur.fetchall()}
    finally:
        conn.close()


# ── the policy ───────────────────────────────────────────────────────────────────────────────────

async def enforce(request: Request, user: dict) -> None:
    """Refuse the request unless a rule opens it for this user's role and the accounts it touches are the user's. Admins never get here."""
    method, path = request.method, request.url.path
    rule, m = None, None
    for r in RULES:
        if method in r.methods and (m := r.pattern.match(path)):
            rule = r
            break
    if rule is None:
        forbid("Not available for your role")
    if rule.write and user["role"] == "readonly":
        forbid("Your access is read-only")
    allowed = set(user.get("accounts") or [])

    def need(ids) -> None:
        bad = {int(i) for i in ids if i not in (None, "")} - allowed
        if bad:
            forbid("That account is not one of yours")

    if rule.pref_key and m.group("key") in PROTECTED_PREFS:
        forbid("Not available for your role")
    if rule.query_account:
        val = request.query_params.get(rule.query_account)
        if val not in (None, ""):
            need([val])
    if rule.path_account:
        need([m.group(rule.path_account)])
    if rule.rows in ("tx_path", "inv_path", "holding_path"):
        kind = {"tx_path": "tx", "inv_path": "inv", "holding_path": "holding"}[rule.rows]
        need(_accounts_of_rows(kind, [int(m.group("id"))]))
    if rule.body_accounts or rule.rows in ("tx_ids", "inv_ids"):
        try:
            body = json.loads((await request.body()) or b"null")
        except ValueError:
            forbid("Unreadable request")
        if isinstance(body, dict):
            if rule.body_accounts:
                need([body.get(k) for k in ACCOUNT_BODY_KEYS])
            if rule.rows in ("tx_ids", "inv_ids"):
                ids = [int(i) for i in (body.get("ids") or [])]
                need(_accounts_of_rows("tx" if rule.rows == "tx_ids" else "inv", ids))
        elif rule.body_accounts or rule.rows:
            forbid("Unreadable request")
