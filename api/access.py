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

What restricted users can use: the Cash Register and the Investments of their accounts (viewing, and for `limited` adding, editing and deleting), the Dashboard,
the Reports, Alerts and Recurring over their accounts, their own Plan, Retirement plans and Budgets, News and Market Data, Static Data (read only — changes are
refused), the remembered screen state, Help and the Release Notes. Tools, Importers, the AI Assistant, private funds and the household's own settings stay admin-only.

Three layers keep a restricted user to their accounts:
  1. this allowlist — an endpoint that is not listed is closed, and the listed ones check the accounts and the rows they are given;
  2. the endpoints narrow their own queries (`scope_sql`, `narrow_ids`, `_parse_account_ids`);
  3. on a *read* request every database connection the request opens shows only the user's accounts (temporary views, database/connection.py), so a report that
     forgot to narrow itself still cannot see what is not theirs. A read that is a POST (a report that takes its filters in the body) says so with `reads=True`.
Plans, Retirement plans and Budgets are the user's own: each restricted user has theirs, apart from the household's (`owner_now`).
"""
from __future__ import annotations

import contextvars
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
    query_accounts: tuple[str, ...] = ()     # query parameters holding comma-separated account ids (an Account Preset), each of which must be the user's when given
    body_accounts: bool = False              # account ids in the JSON body must be the user's
    rows: str | None = None                  # what ids in the path / body point at: "tx_path", "tx_ids", "inv_path", "inv_ids", "holding_path", "tpl_path"
    path_account: str | None = None          # a path group holding an account id
    pref_key: bool = False                   # the path ends in a preference key (see PROTECTED_PREFS)
    reads: bool = False                      # a POST that only reads (a report with its filters in the body): its connections get the scoped views like a GET's
    views: bool = True                       # False: no scoped views on this read — for the two reports whose own queries already narrow every table they use, and whose plans the views slow down a lot


def _r(methods: str, pattern: str, **kw) -> Rule:
    return Rule(tuple(methods.split("|")), re.compile(pattern), **kw)


RULES: list[Rule] = [
    _r("GET", r"^/api/auth/me$"),
    _r("POST", r"^/api/auth/change-password$"),
    _r("GET", r"^/api/changelog$"),
    _r("GET", r"^/api/preferences$"),
    _r("PUT", r"^/api/preferences/(?P<key>[^/]+)$", pref_key=True),
    # Static Data: read only (every change is closed). Counts and transaction lists are over the user's accounts.
    _r("GET", r"^/api/static-data/(categories|payees|institutions|credit-ratings|issuers|accounts-master|securities|currencies|tax-category-rules|instrument-type-overrides)$"),
    _r("GET", r"^/api/static-data/(payees|categories)/\d+/(top-categories|transactions)$"),
    _r("GET", r"^/api/static-data/account-interest-rates$", query_account="accounts_id"),
    # Market Data: prices, rates and currencies are everyone's; what is held, and the alerts on it, is the user's own
    _r("GET", r"^/api/market-data/(currencies|fx-rates|securities|search-ticker|lookup-ticker|price-history|prices-freshness|price-anomalies|watchlist|alerts|shiller-cape|shiller-cape/summary"
              r"|rates|rates/summary|rates/series|rates/tracking|rates/fund-durations|country-cape|technical/levels)$"),
    _r("GET", r"^/api/market-data/currencies/\d+/(history|fx-effect|detail)$"),
    _r("GET", r"^/api/securities/\d+/(transactions|holdings|fund-composition|fund-membership|dividends|corporate-actions|price-anomalies)$"),
    _r("GET", r"^/api/news(/search)?$"),
    _r("GET", r"^/api/(market-watch|local-watch)(/(summary|events|playbook|backtest|backtest/history))?$"),
    _r("GET", r"^/api/local-watch/exposure$", query_accounts=("account_ids",)),
    # the account list (the endpoint narrows it to the user's accounts) and the Dashboard
    _r("GET", r"^/api/dashboard/accounts$"),
    _r("GET", r"^/api/dashboard/net-worth$", query_accounts=("account_ids",)),
    _r("GET", r"^/api/dashboard/account-breakdown$", query_accounts=("account_ids",)),
    _r("GET", r"^/api/dashboard/(alerts|upcoming-bills|anomalies|draft-transactions|uncategorized-transactions|insights)$"),
    _r("POST", r"^/api/dashboard/confirm-draft/(?P<id>\d+)$", write=True, rows="tx_path"),
    _r("DELETE", r"^/api/dashboard/delete-draft/(?P<id>\d+)$", write=True, rows="tx_path"),
    _r("POST", r"^/api/dashboard/confirm-all-drafts$", write=True),
    # Plan, Retirement and Budgets: the user's own (database/plan.py, database/retirement.py; the budgets below)
    _r("GET", r"^/api/plan/(items|plans|due)$"),
    _r("POST", r"^/api/plan/(items|import)$", write=True),
    _r("PUT", r"^/api/plan/items/\d+$", write=True),
    _r("POST", r"^/api/plan/items/\d+/status$", write=True),
    _r("DELETE", r"^/api/plan/(items/\d+|plans)$", write=True),
    _r("GET", r"^/api/retirement/(starting-plan|plans)$"),
    _r("GET", r"^/api/retirement/assets$", query_accounts=("account_ids",)),
    _r("POST", r"^/api/retirement/(project|levers|compare)$", reads=True),
    _r("POST", r"^/api/retirement/plans$", write=True),
    _r("DELETE", r"^/api/retirement/plans/\d+$", write=True),
    _r("GET", r"^/api/reports/(budgets|goals)$"),
    _r("POST", r"^/api/reports/goals$", write=True),
    _r("POST", r"^/api/reports/(portfolio-presets|custom-report-presets)$", write=True),
    _r("DELETE", r"^/api/reports/(portfolio-presets|custom-report-presets)/\d+$", write=True),
    _r("DELETE", r"^/api/reports/goals/\d+$", write=True),
    _r("POST", r"^/api/reports/budgets$", write=True),
    _r("DELETE", r"^/api/reports/budgets/\d+$", write=True),
    # Recurring
    _r("GET", r"^/api/recurring/(templates|drafts|recent-transactions)$"),
    _r("GET", r"^/api/recurring/templates/\d+/splits$"),
    _r("GET", r"^/api/recurring/card-payment-preview$", query_account="card_id"),
    _r("POST", r"^/api/recurring/templates$", write=True, body_accounts=True),
    _r("POST", r"^/api/recurring/templates/from-transaction/(?P<id>\d+)$", write=True, rows="tx_path"),
    _r("PUT", r"^/api/recurring/templates/(?P<id>\d+)$", write=True, body_accounts=True, rows="tpl_path"),
    _r("DELETE", r"^/api/recurring/templates/(?P<id>\d+)$", write=True, rows="tpl_path"),
    _r("POST", r"^/api/recurring/templates/(?P<id>\d+)/run$", write=True, rows="tpl_path"),
    _r("POST", r"^/api/recurring/generate-drafts$", write=True),
    _r("PUT", r"^/api/recurring/drafts/(?P<id>\d+)$", write=True, body_accounts=True, rows="tx_path"),
    _r("POST", r"^/api/recurring/drafts/(?P<id>\d+)/confirm$", write=True, rows="tx_path"),
    _r("DELETE", r"^/api/recurring/drafts/(?P<id>\d+)$", write=True, rows="tx_path"),
    # Reports. Each reads through the user's own accounts (the views, and the endpoints' own narrowing); the ones that change something shared are not listed.
    _r("GET", r"^/api/reports/(income-expense|top-categories|costs-by-broker|savings-rate|portfolio-summary|net-worth-report|pnl|pnl-period|income-expense-detail|dividends"
              r"|dividend-recommendations|capital-gains|budget-vs-actual|annual-income|ytd-expense-transactions|cash-flow-forecast|category-breakdown|net-worth-by-account"
              r"|spending-by-payee|spending-trends|savings-rate-detail|benchmark-candidates|tax-loss-harvesting|tax-loss-harvesting/summary|price-changes|savings-accounts"
              r"|savings-forecast|savings-recommendations|correlation|portfolio-signals|action-signals|action-history|fundamental-scores|income-expense-full"
              r"|custom-report-presets|custom-report-filter-data|portfolio-presets|xray/asset-allocation-targets)$"),
    # holdings-snapshot and dividends-forecast narrow Investments / Holdings themselves (_parse_account_ids -> _acct_clause) and join Accounts by id; with the views
    # on top, the planner turns them into nested loops that take twenty seconds instead of one, so they run without.
    _r("GET", r"^/api/reports/(holdings-snapshot|dividends-forecast)$", query_accounts=("account_ids",), views=False),
    _r("GET", r"^/api/reports/(pnl-summary|dividends-tracker|crypto-tax|tax-return|cash-flow-forecast-full|investment-positions-history"
              r"|xray/(sector-weighting|asset-allocation|style-box|bond-quality|stock-overlap|expense-ratio)|twr|risk-metrics|dividend-income-tax|bank-interest-tax|bond-schedule"
              r"|benchmark|monte-carlo)$", query_accounts=("account_ids", "compare_account_ids", "exclude_account_ids"), query_account="account_id"),
    _r("POST", r"^/api/reports/custom-report/(run|drill-down|investment-drill-down)$", reads=True),
    _r("GET", r"^/api/reports/(day-extremes)(/years)?$"),
    _r("GET", r"^/api/reports/country-exposure(/countries|/funds/\d+(/source)?)?$", query_accounts=("account_ids",)),
    _r("GET", r"^/api/reports/currency-exposure(/funds/\d+)?$", query_accounts=("account_ids",)),
    # Cash Register
    _r("GET", r"^/api/register/transactions(/ids)?$", query_account="account_id"),
    _r("GET", r"^/api/register/search$"),                                          # the global search: over the user's accounts, through the scoped views
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


# The signed-in user, for the code the endpoints call (the reports' helpers, the dashboard's forecasts) which has no request to look at.
# require_auth binds it; anything that runs outside a request — the scheduler, a background job — sees no user and so no restriction.
_CURRENT: contextvars.ContextVar[dict | None] = contextvars.ContextVar("oikos_user", default=None)


_VIEWS: contextvars.ContextVar[bool] = contextvars.ContextVar("oikos_scope_views", default=False)


def bind_user(user: dict | None, reading: bool = False) -> None:
    """`reading`: the request only reads, so the database connections it opens get the user's scoped views (database/connection.py)."""
    _CURRENT.set(user)
    _VIEWS.set(bool(reading and user and user.get("role", "admin") != "admin"))


class unscoped:
    """`with unscoped(): conn = get_connection()` — a connection without the scoped views, for the few things that change the schema (a CREATE INDEX on a table
    the views stand in for would fail on the view)."""
    def __enter__(self):
        self._tok = _VIEWS.set(False)

    def __exit__(self, *exc):
        _VIEWS.reset(self._tok)
        return False


def view_scope() -> list[int] | None:
    """The accounts to build scoped views for on a new connection, or None when the connection is to see everything."""
    return scope_now() if _VIEWS.get() else None


def user_id_now() -> int | None:
    """The signed-in user's own id, whatever the role (None outside a request): what a user's own settings — the last report open, the saved presets — are kept under."""
    u = _CURRENT.get()
    return int(u["users_id"]) if u and u.get("users_id") is not None else None


def scope_now() -> list[int] | None:
    """The accounts the current user may use, or None for every account (an admin, or no request at all)."""
    u = _CURRENT.get()
    if not u or u.get("role", "admin") == "admin":
        return None
    return list(u.get("accounts") or [])


def restricted_now() -> bool:
    return scope_now() is not None


def owner_now() -> int | None:
    """Whose personal data (plans, retirement plans, budgets) the current user reads and writes: their own Users_Id for a limited / read-only user,
    None — the household's — for an admin. Each restricted user has their own, kept apart from the household's and from each other's."""
    u = _CURRENT.get()
    if not u or u.get("role", "admin") == "admin":
        return None
    return int(u["users_id"])


def scope_sql(column: str) -> str:
    """` AND column IN (…)` for the current user — an empty string for an admin."""
    ids = scope_now()
    if ids is None:
        return ""
    return f" AND {column} IN ({','.join(str(int(i)) for i in ids) or 'NULL'})"


def in_scope_sql(column: str) -> str:
    """A SQL condition: the account in `column` is one of the current user's (always true for an admin)."""
    ids = scope_now()
    if ids is None:
        return "TRUE"
    return f"{column} IN ({','.join(str(int(i)) for i in ids) or 'NULL'})"


EXTERNAL_ACCOUNT = "External account"      # how an account of someone else's is named on the other side of a transfer


def held_securities() -> set[int] | None:
    """The securities held (or ever traded) in the current user's accounts, or None for an admin — what the news, the watchlist and the alert
    definitions are narrowed to for a restricted user."""
    mine = scope_now()
    if mine is None:
        return None
    if not mine:
        return set()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT Securities_Id FROM Holdings WHERE Accounts_Id = ANY(%s) AND Quantity <> 0 "
                        "UNION SELECT DISTINCT Securities_Id FROM Investments WHERE Accounts_Id = ANY(%s) AND Securities_Id IS NOT NULL", (mine, mine))
            return {r[0] for r in cur.fetchall()}
    finally:
        conn.close()


# Alerts a restricted user never sees: household-wide ones (allocation drift against your targets, private fund calls) and the trend ones, whose messages carry
# the P&L of the whole position (get_portfolio_signals has no account filter).
_HOUSEHOLD_ALERTS = {"allocation_drift", "fund_call", "ma_cross", "trailing_stop"}


def restrict_alerts(alerts: list[dict]) -> list[dict]:
    """The alerts a restricted user may see: those on securities their accounts hold, on currencies and on their loans — the same list for an admin."""
    mine = scope_now()
    if mine is None:
        return alerts
    held = held_securities() or set()
    out = []
    for a in alerts:
        t = a.get("type")
        if t in _HOUSEHOLD_ALERTS:
            continue
        if a.get("securities_id") is not None:
            if int(a["securities_id"]) not in held:
                continue
        elif a.get("accounts_id") is not None:
            if int(a["accounts_id"]) not in mine:
                continue
        elif t not in ("fx_alert",) and not str(t or "").startswith("sr_"):
            continue                      # anything else with no security, account or currency to tie it to stays out
        out.append(a)
    return out


def narrow_ids(ids: list[int] | None) -> list[int] | None:
    """The accounts a query should cover: what was asked for, held to the current user's accounts; for a restricted user who asked for none, all of theirs.
    An admin's choice stands (None = no filter). A restricted user with nothing left gets [-1], an id no account has, rather than an empty list that would read as 'all'."""
    mine = scope_now()
    if mine is None:
        return ids
    left = [i for i in ids if i in set(mine)] if ids else list(mine)
    return left or [-1]


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
            if kind == "tpl":
                cur.execute("SELECT Accounts_Id, Accounts_Id_Target FROM Recurring_Templates WHERE Templates_Id = ANY(%s)", (ids,))
                return {a for r in cur.fetchall() for a in r if a is not None}
    finally:
        conn.close()
    return set()


def linked_accounts(account_ids: list[int]) -> set[int]:
    """The accounts linked to the given ones (a brokerage's cash account) that serve nobody else: a linked account that some other account also links to — one cash
    account behind several people's brokerages — would hand each of them the others' money, so it is left out; an admin maps such an account by hand."""
    if not account_ids:
        return set()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT DISTINCT a.Accounts_Id_Linked FROM Accounts a
                           WHERE a.Accounts_Id = ANY(%s) AND a.Accounts_Id_Linked IS NOT NULL
                             AND NOT EXISTS (SELECT 1 FROM Accounts o WHERE o.Accounts_Id_Linked = a.Accounts_Id_Linked AND o.Accounts_Id <> ALL(%s))""",
                        (account_ids, account_ids))
            out = {r[0] for r in cur.fetchall()}
            try:                                       # a mapped Margin account's financing account (database/margin.py) goes with it
                cur.execute("SELECT Offset_Accounts_Id FROM Margin_Offset_Accounts WHERE Accounts_Id = ANY(%s)", (account_ids,))
                out |= {r[0] for r in cur.fetchall()}
            except Exception:
                conn.rollback()                        # no such table yet: nothing has been converted
            return out
    finally:
        conn.close()


# ── the policy ───────────────────────────────────────────────────────────────────────────────────

async def enforce(request: Request, user: dict) -> Rule:
    """Refuse the request unless a rule opens it for this user's role and the accounts it touches are the user's. Admins never get here. Returns the rule."""
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
    for q in rule.query_accounts:
        val = request.query_params.get(q)
        if val:
            try:
                need([int(x) for x in val.split(",") if x.strip()])
            except ValueError:
                forbid("Unreadable request")
    if rule.path_account:
        need([m.group(rule.path_account)])
    if rule.rows in ("tx_path", "inv_path", "holding_path", "tpl_path"):
        kind = {"tx_path": "tx", "inv_path": "inv", "holding_path": "holding", "tpl_path": "tpl"}[rule.rows]
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
    return rule
