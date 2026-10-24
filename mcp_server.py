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
    "alerts": {
        "path": "/api/dashboard/alerts", "params": [],
        "description": "Triggered Dashboard alerts, each with a group (sr = support/resistance, price, signals, action, events, corporate, portfolio), level, message and stable key: support/resistance events on holdings/Watchlist/exposed currencies, price alerts, signal changes, crosses, trailing stops, upcoming bond/dividend/loan events, splits, allocation drift, and Action changes (a held security's verdict moving between Reduce / Sell, Hold and Add).",
    },
    "action_signals": {
        "path": "/api/reports/action-signals", "params": ["held_only", "action (comma-separated, e.g. Reduce,Sell)", "changed_only", "types (comma-separated, e.g. Stock,ETF)", "shortlist", "min_score (default 6)", "per_sector (default 2)"],
        "description": "The Action verdict of every security, compact: name, ticker, held, value_eur, action (Add / Hold / Reduce / Sell for positions, Buy / Watch / Avoid otherwise), action_score (5-day average of the points), action_why (each factor with its points), bucket / previous_bucket / changed_at of the last move between Reduce / Sell, Hold and Add, alert_pending, and the older final_signal / math_signal. Also type and sector. A fair value more than 100% from the price is ignored in the score and the reasons say so. Held positions first, biggest first. held_only=true for open positions; changed_only=true for the unacknowledged Action Change alerts; types keeps only those security types; shortlist=true returns Buy candidates only — not held, verdict Buy, score at least min_score (default 6), best first, at most per_sector from one sector, of the given types (default: everything but indexes, CDs and employee options).",
    },
    "action_history": {
        "path": "/api/reports/action-history", "params": ["securities_id", "from_date", "limit"],
        "description": "The daily log of every security's Action — date, score (smoothed), raw_score, verdict, held and the reasons — one row per security per day since logging began, newest first. This is what the score is back-tested against (python -m data.action_backtest); the first 3-month results arrive when the log is 3 months old.",
    },
    "plan_items": {
        "path": "/api/plan/items", "params": ["plan", "status (open / done / skipped)"],
        "description": "The steps of the user's Plan (Plan page: dated to-dos such as a rebalancing plan, each reminded on Telegram on its day): plan name, due_date, title, details, status (open / done / skipped) and done_at, ordered by due date. Use it to check progress against a plan.",
    },
    "plan_due": {
        "path": "/api/plan/due", "params": ["days (default 7)"],
        "description": "The open Plan steps that are overdue, due today, and due in the next N days.",
    },
    "portfolio_signals": {
        "path": "/api/reports/portfolio-signals", "params": ["held_only"],
        "description": "The full Portfolio Action Signals grid, one row per security (large — use held_only=true or action_signals first): prices and 1D-3Y changes, volatility, Sharpe, quality score, 3Y range, MA50/MA200 with their 5-day direction (ma50_chg_pct / ma200_chg_pct), Golden/Death regime and crosses, trailing stop, analyst target and upside, fair value, F-Score / Z-Score, final_signal, and action / action_score / action_why.",
    },
    "fundamental_scores": {
        "path": "/api/reports/fundamental-scores", "params": [],
        "description": "Piotroski F-Score (0-9, with the criteria passed) and Altman Z-Score (with its Safe / Grey / Distress zone) for stocks with cached financial statements.",
    },
    "watchlist": {
        "path": "/api/market-data/watchlist", "params": [],
        "description": "The Watchlist: securities you follow without holding — latest price (native and EUR), your target price and stop loss, the analyst target, dividend yield and % from target.",
    },
    "price_alerts": {
        "path": "/api/market-data/alerts", "params": [],
        "description": "Your own price alerts (configuration, not whether they have fired — see alerts for the triggered ones): security, above/below threshold, direction, active flag, note, with the current price and its date. Many rows.",
    },
    "portfolio_summary": {
        "path": "/api/reports/portfolio-summary", "params": ["account_ids"],
        "description": "Current positions, one row per account and security: quantity, last price, FX rate, value in EUR, currency. account_ids limits it to some accounts.",
    },
    "pnl_summary": {
        "path": "/api/reports/pnl-summary", "params": ["start_date", "end_date"],
        "description": "Realized gain (FIFO, in EUR — redeemed bills and deposits count only their gain, not their face value) and dividend / interest income per month from start_date (default 2024-01-01). For the per-security P&L see /api/reports/pnl.",
    },
    "dividends": {
        "path": "/api/reports/dividends", "params": ["start_date", "end_date"],
        "description": "Dividends received, one row per payment (date, security, ticker, account, amount, currency, fx_rate, amount_eur) — many rows, so pass start_date / end_date.",
    },
    "dividends_forecast": {
        "path": "/api/reports/dividends-forecast", "params": ["period"],
        "description": "Expected dividends from current holdings: summary, monthly_forecast, by_security and upcoming payments (period = the horizon).",
    },
    "risk_metrics": {
        "path": "/api/reports/risk-metrics", "params": ["lookback_days", "benchmark_sec_id", "account_ids"],
        "description": "Portfolio risk over the lookback: annualised volatility, Sharpe, Sortino, max drawdown, VaR / CVaR (% and EUR), beta and alpha against the benchmark.",
    },
    "twr": {
        "path": "/api/reports/twr", "params": ["lookback_days", "account_ids"],
        "description": "Time-weighted return (window and annualised) and money-weighted return of the investment portfolio over the lookback, with the series for a chart.",
    },
    "holdings_snapshot": {
        "path": "/api/reports/holdings-snapshot", "params": ["as_of", "account_ids"],
        "description": "What was held on a past date (as_of): one row per account and security with type, currency, quantity, price, price date and value in EUR.",
    },
    "xray_asset_allocation": {
        "path": "/api/reports/xray/asset-allocation", "params": ["account_ids", "compare_date"],
        "description": "Portfolio Analysis (look-through) asset allocation: summary and detail across stocks, bonds, cash and funds' contents. Sibling endpoints with the same params: /api/reports/xray/sector-weighting, stock-overlap, expense-ratio, bond-quality, style-box (compare_date adds an as-of column).",
    },
    "fund_composition": {
        "path": "/api/securities/{sec_id}/fund-composition", "params": [],
        "description": "One fund's composition (security id in the path): composition (asset mix, sectors, countries, bond quality) and its top holdings.",
    },
    "local_watch": {
        "path": "/api/local-watch/summary", "params": [],
        "description": "Local Market Watch for your home country: overall banner status, recent changes and the indicators (index trend, breadth, banks, sovereign spread, liquidity).",
    },
    "bond_schedule": {
        "path": "/api/reports/bond-schedule", "params": [],
        "description": "The bonds held: face value, coupon rate and frequency, next coupon, annual coupon (EUR), maturity date and days to maturity.",
    },
    "tax_loss_harvesting": {
        "path": "/api/reports/tax-loss-harvesting", "params": [],
        "description": "Positions below cost: quantity, current price, cost basis, current value, unrealized loss in EUR and %.",
    },
    "budget_vs_actual": {
        "path": "/api/reports/budget-vs-actual", "params": ["year", "ref_years"],
        "description": "Per category for a year: historical average, prior year, budget, actual, variance (EUR and %) and an over-budget flag.",
    },
    "net_worth_by_account": {
        "path": "/api/reports/net-worth-by-account", "params": ["start_date", "end_date", "grouping"],
        "description": "Net worth history by account and period (balance_eur) — very large (thousands of rows); pass start_date / end_date and grouping.",
    },
    "account_breakdown": {
        "path": "/api/dashboard/account-breakdown", "params": ["group (cash|investments|pension|assets, required)", "account_ids", "include_future"],
        "description": "A Dashboard KPI split by account: value now and the change vs the previous month, today and YTD, plus the balance in the account's own currency.",
    },
    "technical_levels": {
        "path": "/api/market-data/technical/levels", "params": ["kind (security|currency|currency_stored, required)", "id (required)", "from_date", "quote", "per_side"],
        "description": "Nearest support/resistance levels of a security's prices or a currency's rates (in the quote currency, or as stored vs EUR) since from_date, with touches and distance from the last price.",
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
        "description": "Every tracked security: name, ticker, type, currency, latest price. Use its id as security_id for price_history below.",
    },
    "price_history": {
        "path": "/api/market-data/price-history", "params": ["security_id (required)", "from_date"],
        "description": "Daily close/high/low/volume history for one security (security_id is required — get it from the securities endpoint), from_date onward (default 2020-01-01, no end-date param — returns through the latest close).",
    },
    "fx_rates": {
        "path": "/api/market-data/fx-rates", "params": ["currency_id", "from_date"],
        "description": "Historical daily FX rates vs EUR, from_date onward (default 2020-01-01); currency_id optionally filters to one currency (get ids from the securities/accounts data), otherwise returns every tracked currency.",
    },
    "currencies": {
        "path": "/api/market-data/currencies", "params": [],
        "description": "Every currency: id, code, name, latest stored rate vs EUR and its date. Use its id for currency_detail/currency_history.",
    },
    "currency_detail": {
        "path": "/api/market-data/currencies/{currency_id}/detail", "params": ["quote"],
        "description": "One currency (put its id in the path): rate in the quote currency (default EUR; pass the reporting currency code) with 1D/1M/YTD/1Y change, your exposure to it (accounts + securities, EUR), its share of total exposure, interest-rate series, and the FX effect on cash balances (1D/YTD).",
    },
    "currency_history": {
        "path": "/api/market-data/currencies/{currency_id}/history", "params": ["quote", "from_date"],
        "description": "Rate history of one currency (id in the path) expressed in the quote currency, converted through the stored EUR rates.",
    },
    "currency_fx_effect": {
        "path": "/api/market-data/currencies/{currency_id}/fx-effect", "params": ["period"],
        "description": "Currency effect on P&L for one currency (id in the path) over period DTD/WTD/MTD/QTD/YTD/1Y/3Y/5Y/All (default YTD): per account/security quoted in it, realized and unrealized P&L each split into market and FX, plus income; and the FX effect on cash balances.",
    },
    "market_watch": {
        "path": "/api/market-watch", "params": [],
        "description": "The four market-health indicators with status/trend: hyperscaler free cash flow, credit spreads, 10-year Treasury yield and market breadth (% of S&P 500 above its 200/50-day average, equal vs cap weight), plus an overall verdict.",
    },
    "cash_flow_forecast": {
        "path": "/api/reports/cash-flow-forecast-full", "params": ["days", "months_back", "account_ids", "min_balance"],
        "description": "Forward-looking scheduled/recurring/dividend/interest cash flow projection, plus account_alerts: which checking/savings/credit-card accounts are projected to fall below min_balance (default 0) or their credit limit, with suggested transfers from other accounts.",
    },
    "income_expense": {
        "path": "/api/reports/income-expense", "params": ["start_date", "end_date"],
        "description": "Income/expense totals by category for a date range.",
    },
    "shiller_cape": {
        "path": "/api/market-data/shiller-cape/summary", "params": [],
        "description": "Current U.S. Shiller CAPE ratio, valuation zone, and percentile since 1881.",
    },
    "interest_rates_summary": {
        "path": "/api/market-data/rates/summary", "params": [],
        "description": "Per tracked rate series: latest value, last policy change, overnight move vs 30-day average; tracked funds' return vs their rate (default XEON vs €STR); active rate alerts.",
    },
    "interest_rates": {
        "path": "/api/market-data/rates", "params": ["series", "years"],
        "description": "Rate history. series = comma-separated codes from interest_rate_series (default ESTR, ECB_DFR, SOFR, EFFR, FED_UPPER, FED_LOWER); years = last N years.",
    },
    "country_exposure": {
        "path": "/api/reports/country-exposure", "params": ["account_ids"],
        "description": "Investment exposure by country -> category (Stocks, Government Bonds, Corporate Bonds, ...) -> instrument, with funds spread over their entered country breakdown.",
    },
    "currency_exposure": {
        "path": "/api/reports/currency-exposure", "params": ["account_ids", "include_liabilities"],
        "description": "Currency exposure looked through funds: currency -> category -> instrument (cash accounts included), with the amount in each currency and the EUR impact of a 5% FX move. include_liabilities=true nets credit cards and loans.",
    },
    "day_extremes": {
        "path": "/api/reports/day-extremes", "params": ["account_id", "year", "n", "min_base", "period"],
        "description": "Best and worst trading days, weeks or months of a calendar year for one investment account (omit account_id for all), by P&L in EUR and in %, with the main movers of each. period = day (default) | week | month; n defaults to 10.",
    },
    "interest_rate_series": {
        "path": "/api/market-data/rates/series", "params": [],
        "description": "Definitions of every tracked rate series: code, name, currency, type, provider, latest date, value count.",
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
