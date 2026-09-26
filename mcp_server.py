"""Read-only MCP server exposing Oikos's own REST API to Claude.

This script itself always runs locally — wherever Claude Desktop/Code is
(e.g. a Windows workstation), since MCP's stdio transport means the client
spawns it as a local child process. It does NOT need to run on the same
machine as Oikos: it just makes HTTPS calls to OIKOS_API_URL, defaulted below
to this deployment's LAN address (the Raspberry Pi's docker-compose stack,
behind its TLS-terminating proxy on :8443). Override OIKOS_API_URL to switch
targets — e.g. the same host's Tailscale IP instead of its LAN one, or a
plain http://localhost:8000 dev backend if you're working on Oikos itself.

This file is fully standalone — only "mcp[cli]" and "requests" as
dependencies, no other Oikos module required — so it's fine to copy just this
one file to a machine that doesn't have the rest of the Oikos repo checked
out (e.g. a Windows workstation, with Oikos itself running on a separate
server/Pi). Set credentials via OIKOS_MCP_USERNAME/OIKOS_MCP_PASSWORD in that
case. If it's instead sitting inside a full Oikos checkout (e.g. Claude Code
working in this repo), it'll pick up ADMIN_USERNAME/ADMIN_PASSWORD from that
checkout's own .env automatically, so you don't have to set anything —
config/settings.py is imported opportunistically, never required.

Logs in once at startup, then reuses that session cookie for every call.

Deliberately read-only: the one HTTP tool this exposes (oikos_get) only ever
issues GET requests — hardcoded in the implementation, not caller-controlled
— so no code path here can create, edit, or delete any Oikos record,
regardless of what a calling agent asks for. Every one of Oikos's own mutating
endpoints is POST/PUT/DELETE (confirmed across every router in api/routers/),
so restricting the HTTP verb is a structural guarantee, not a convention this
file has to keep re-checking.

No direct database access, ever: this file only ever speaks HTTPS to Oikos's
own REST API (never imports `database`/psycopg2/a connection string), so there
is no DB user or credential of any kind for this connector to be scoped
read-only — it's structurally narrower than that, with no SQL access at all.

Sensitive-field redaction: some of Oikos's own GET endpoints return fields
that were never meant to leave the household (IBANs on the accounts-master
endpoint, an institution contact's phone/email, broker-integration API
keys/secrets/refresh tokens on the bank-sync settings endpoints). Every
response this file returns is passed through `_redact()` first, which walks
the JSON recursively and blanks any field whose name matches a denylist
(IBAN, account/card numbers, passwords, API keys/secrets/tokens, email,
phone) regardless of which endpoint it came from — including endpoints not
in the curated list below, since oikos_get() accepts any Oikos GET path.
`/api/bank/*` (broker-sync credential storage — Saxo/Coinbase/Crypto.com app
keys and secrets, refresh tokens) is blocked outright on top of that, since
nothing useful for "reading financial data" lives there anyway.

TLS: the Pi's :8443 endpoint is expected to use a self-signed/home-lab
certificate, so certificate verification is OFF by default (OIKOS_VERIFY_SSL
unset). Set OIKOS_VERIFY_SSL=true once you have a certificate this machine
actually trusts (a real CA-issued one, or Tailscale's own), or set it to a
filesystem path to verify against that specific CA bundle/cert instead of
Python's default trust store.

Register with Claude Code (project-level, from the Oikos repo root):
    claude mcp add oikos -- python mcp_server.py

Or add directly to .mcp.json / claude_desktop_config.json:
    {
      "mcpServers": {
        "oikos": {
          "command": "python",
          "args": ["<absolute-path>/mcp_server.py"],
          "env": { "OIKOS_API_URL": "https://<tailscale-ip>:8443" }
        }
      }
    }
"""
import os
import urllib3

import requests
from mcp.server.mcpserver import MCPServer

try:
    # Only present — and only needed — when this file happens to be sitting
    # inside a full Oikos checkout. Falls back to plain env vars otherwise,
    # which is the expected case when it's been copied to a machine that
    # doesn't have (and doesn't need) the rest of the repo.
    from config.settings import ENV_CONFIG
except ImportError:
    ENV_CONFIG = {}

BASE_URL = os.getenv("OIKOS_API_URL", "https://192.168.4.20:8443")
USERNAME = os.getenv("OIKOS_MCP_USERNAME") or ENV_CONFIG.get("admin_username", "")
PASSWORD = os.getenv("OIKOS_MCP_PASSWORD") or ENV_CONFIG.get("admin_password", "")

# True (verify against the system trust store), False (skip verification —
# the default, since a home-lab :8443 cert is normally self-signed), or a
# filesystem path to a specific CA bundle/certificate to verify against.
_verify_raw = os.getenv("OIKOS_VERIFY_SSL", "false")
VERIFY_SSL: bool | str = (
    True if _verify_raw.strip().lower() in ("true", "1")
    else False if _verify_raw.strip().lower() in ("false", "0", "")
    else _verify_raw  # a path to a CA bundle/cert file
)
if VERIFY_SSL is False:
    # Only this server's own outbound calls are affected — doesn't touch
    # verification anywhere else. Silences the per-request
    # InsecureRequestWarning that would otherwise print on every single call.
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

server = MCPServer(
    name="oikos",
    instructions=(
        "Read-only access to the Oikos personal finance app's own data — net worth, "
        "accounts, cash and investment transactions, holdings, market valuation "
        "(Shiller CAPE), and anything else its own web UI can see. Call "
        "list_endpoints() first for a curated set of ready-to-use paths and their "
        "parameters, then oikos_get(path, params) to fetch data. There is no write "
        "tool — nothing called here can ever modify Oikos's data. Sensitive fields "
        "(IBAN, card/account numbers, passwords, API keys/secrets/tokens, email, "
        "phone) are stripped from every response, and broker-integration settings "
        "(/api/bank/*) are blocked outright."
    ),
)

# Endpoint paths blocked outright — broker-sync credential storage, not
# financial data. Every GET route under here exists to populate a settings
# form with the household's own stored API key/secret/refresh token for Saxo,
# Coinbase, Crypto.com, etc. (confirmed by reading api/routers/bank_router.py
# directly), so there's no "read my finances" use case this connector needs
# it for — simplest to block the whole prefix rather than track which routes
# under it are safe as the integrations list grows.
_BLOCKED_PATH_PREFIXES = ("/api/bank/",)

# Field names (case-insensitive, underscores ignored) whose values get
# blanked out of every response, regardless of which endpoint returned them —
# covers oikos_get() calls to any Oikos GET path, not just the curated list
# above. "email"/"phone" use substring matching since real field names vary
# (e.g. "contact_email"); everything else is an exact match so it doesn't
# also catch unrelated fields that merely contain the word (e.g. "token_valid").
_REDACT_EXACT = {
    "iban", "accountnumber", "cardnumber", "creditcardnumber", "cvv", "cvv2",
    "password", "passwordhash", "apikey", "apisecret", "appkey", "appsecret",
    "refreshtoken", "accesstoken", "clientsecret", "clientid", "secret",
    "token", "ssn", "socialsecuritynumber", "taxid",
}
_REDACT_SUBSTRING = ("email", "phone")


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("_", "").replace("-", "")
    if normalized in _REDACT_EXACT:
        return True
    return any(term in normalized for term in _REDACT_SUBSTRING)


def _redact(value):
    """Recursively blank sensitive fields out of a parsed JSON response."""
    if isinstance(value, dict):
        return {
            k: "[redacted]" if _is_sensitive_key(k) else _redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


_session = requests.Session()
_session_token: str | None = None


def _ensure_login() -> None:
    global _session_token
    if _session_token:
        return
    if not USERNAME or not PASSWORD:
        raise RuntimeError(
            "No Oikos credentials configured — set OIKOS_MCP_USERNAME and "
            "OIKOS_MCP_PASSWORD (or ADMIN_USERNAME/ADMIN_PASSWORD in Oikos's own "
            ".env) before starting this server."
        )
    resp = _session.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        timeout=10,
        verify=VERIFY_SSL,
    )
    resp.raise_for_status()
    # api/routers/auth.py sets this cookie with Secure, which requests' own
    # cookiejar will only ever resend automatically over an HTTPS connection —
    # true for this deployment's real :8443 endpoint, but not if OIKOS_API_URL
    # is pointed at a plain http://localhost dev backend instead. Pulling the
    # token out and passing it explicitly on every request below (rather than
    # relying on the jar's own scheme-aware matching) means this works
    # identically either way, without needing to know in advance which kind
    # of target it's talking to.
    token = resp.cookies.get("oikos_session")
    if not token:
        raise RuntimeError("Login succeeded but no oikos_session cookie was returned.")
    _session_token = token


# Curated starting points — Oikos's REST API has well over a hundred GET routes
# across a dozen routers, far more than useful to hand-list exhaustively. This
# covers the questions someone's actually likely to ask Claude about their
# finances; oikos_get() itself works with any Oikos GET path, not just these.
ENDPOINTS = {
    "net_worth": {
        "path": "/api/dashboard/net-worth", "params": ["include_future"],
        "description": "Current net worth: total plus cash/investments/pension/assets breakdown.",
    },
    "accounts": {
        "path": "/api/dashboard/accounts", "params": ["include_future"],
        "description": "Every account with balance (native currency + EUR-equivalent), currency, institution, type.",
    },
    "insights": {
        "path": "/api/dashboard/insights", "params": [],
        "description": "Auto-generated observations: overspending, negative balances, credit limits nearly reached, upcoming bond/dividend/loan events.",
    },
    "anomalies": {
        "path": "/api/dashboard/anomalies", "params": [],
        "description": "Unusual transactions — statistical outliers versus a payee/category's own history.",
    },
    "upcoming_bills": {
        "path": "/api/dashboard/upcoming-bills", "params": [],
        "description": "Scheduled and recurring-template transactions due soon.",
    },
    "transactions": {
        "path": "/api/register/transactions", "params": ["account_id (required)", "from_date", "to_date", "search", "limit", "offset"],
        "description": "Cash Register transactions for one account (account_id is required — get IDs from the accounts endpoint), filterable by date range/search text.",
    },
    "holdings": {
        "path": "/api/investments/holdings", "params": ["account_id", "include_closed"],
        "description": "Current investment holdings: quantity, cost basis, market value, gain/loss, per security/account.",
    },
    "investment_transactions": {
        "path": "/api/investments/list", "params": ["account_id", "from_date", "to_date", "action", "search", "limit", "offset"],
        "description": "Investment transactions (Buy/Sell/Dividend/etc.), filterable.",
    },
    "securities": {
        "path": "/api/market-data/securities", "params": [],
        "description": "Every tracked security: name, ticker, type, currency, latest price.",
    },
    "cash_flow_forecast": {
        "path": "/api/reports/cash-flow-forecast-full", "params": ["days", "months_back", "account_ids"],
        "description": "Forward-looking scheduled/recurring/dividend/interest cash flow projection.",
    },
    "income_expense": {
        "path": "/api/reports/income-expense", "params": ["start_date", "end_date"],
        "description": "Income/expense totals by category for a date range.",
    },
    "shiller_cape": {
        "path": "/api/market-data/shiller-cape/summary", "params": [],
        "description": "Current U.S. Shiller CAPE ratio, valuation zone, and percentile since 1881.",
    },
}


@server.tool()
def list_endpoints() -> dict:
    """List curated, ready-to-use Oikos read endpoints: name, path, accepted
    query params, and what each one returns. Use these paths with oikos_get().
    oikos_get() also accepts any other Oikos GET path not listed here."""
    return ENDPOINTS


@server.tool()
def oikos_get(path: str, params: dict | None = None) -> dict:
    """Read-only GET request against the Oikos API.

    path must start with "/api/" — see list_endpoints() for ready-to-use
    examples. Always issues a GET; there is no way to write, update, or
    delete Oikos data through this tool. Broker-integration settings
    (/api/bank/*) are off-limits, and IBAN/card/account numbers, passwords,
    API keys/secrets/tokens, email, and phone fields are stripped from
    whatever comes back.
    """
    global _session_token
    if not path.startswith("/api/"):
        raise ValueError('path must start with "/api/" — see list_endpoints() for examples')
    if path.startswith(_BLOCKED_PATH_PREFIXES):
        raise ValueError(f"{path} is off-limits — broker-integration credentials live under /api/bank/, not financial data")
    _ensure_login()
    resp = _session.get(f"{BASE_URL}{path}", params=params or {}, cookies={"oikos_session": _session_token}, timeout=30, verify=VERIFY_SSL)
    if resp.status_code == 401:
        # Session likely expired — log in fresh once and retry, rather than
        # failing a perfectly valid request just because it's been a while
        # since the server started.
        _session_token = None
        _ensure_login()
        resp = _session.get(f"{BASE_URL}{path}", params=params or {}, cookies={"oikos_session": _session_token}, timeout=30, verify=VERIFY_SSL)
    resp.raise_for_status()
    return _redact(resp.json())


if __name__ == "__main__":
    server.run()
