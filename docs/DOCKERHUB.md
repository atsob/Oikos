<!--
  The overview (full description) for the Docker Hub repository atsob/oikos — paste everything below the line into
  Docker Hub → Repository → General → Overview. Docker Hub's *short description* (100 characters) is the line right below this comment's
  heading "Short description".
-->

## Short description

Oikos — self-hosted personal finance: accounts, multi-currency investments, forecasts, reports. FastAPI + React.

---

# Oikos

**A self-hosted personal finance manager** — bank and cash accounts, credit cards, loans, real assets, pensions and multi-currency
investments (stocks, ETFs, bonds, crypto) in one place, converted to a single reporting currency, with cash-flow forecasting, tax
reporting and portfolio analysis. FastAPI + React/TypeScript on PostgreSQL. Single-tenant by design: one instance for one household.

*A development of [ALL ABOUT 360 CONSULTING](https://github.com/atsob).* Source, issues and the full install guide:
**<https://github.com/atsob/Oikos>**

## What you get

- **Cash and investments together** — a transaction register with splits, transfers, recurring templates and card statements; an
  investment ledger with holdings, FIFO realized / unrealized P&L, dividends, corporate actions and TWR / MWR.
- **Reports** — net worth, income and expense, cash-flow forecast, P&L by account and security, benchmark comparison (price or total return),
  risk metrics, tax, Portfolio X-Ray (look-through asset allocation, sectors, countries, currencies), stress tests.
- **Signals and alerts** — Action verdicts (Add / Hold / Reduce / Sell), support / resistance, Market Watch and Local Market Watch, with a
  Dashboard you can customize and optional **Telegram** notifications.
- **Plan** — dated to-do steps (a rebalancing plan, a tax-year checklist) with a morning Telegram reminder.
- **Imports** — Interactive Brokers, Saxo, Revolut, bank statements and open banking (GoCardless, Salt Edge); market data from Yahoo
  Finance, TradingView and optional EODHD / FRED keys.
- **AI assistant** (optional) — a local model through Ollama, or Anthropic's API; a read-only connector lets Claude query your data.
- **Phone-friendly** — installs as a home-screen app; tables become cards on a small screen.

## How the image is used

One image, **`atsob/oikos`**, runs two containers:

| Container | Command | Role |
|---|---|---|
| `oikos` | `uvicorn api.main:app --host 0.0.0.0 --port 8080 --workers 3` | the web app and API (serves the built React frontend) |
| `oikos_scheduler` | `python scheduler.py` | background jobs: market data, backups, signals, summaries, reminders |

It also needs **PostgreSQL 16+ with the pgvector extension** (the schema is created and migrated automatically on first start — point it at an
empty database) and, for HTTPS, a small **nginx** container in front. The Compose file in the repository wires all of it, and can run
Postgres and Ollama in containers too (`--profile full`). The image contains the PostgreSQL 18 client tools used for the daily `pg_dump` backup.

## Quick start

```bash
git clone https://github.com/atsob/Oikos.git
cd Oikos
cp .env.example .env        # set DB_PASSWORD and ADMIN_USERNAME / ADMIN_PASSWORD at least
# one-time: create the TLS certificate in ./ssl — see INSTALL.md § 3
docker compose up -d        # uses atsob/oikos:latest; add --build to build the image from source instead
```

Then open `https://<host>:8443` and sign in with the `ADMIN_*` account from `.env` (used once, to create the first user). The CA
certificate can be downloaded from `http://<host>:8444/ca.crt`.

To update: `docker compose pull && docker compose up -d` (schema changes are applied automatically and are safe to repeat).

## Configuration

Everything is an environment variable; only the first two rows are required.

| Variable | Default | Purpose |
|---|---|---|
| `DB_PASSWORD` | — (required) | PostgreSQL password |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | — | first login, created once when there are no users |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` | `127.0.0.1`, `5432`, `Oikos`, `admin` | database connection |
| `DB_TIMEZONE` | `Europe/Athens` | time zone for dates and the scheduler |
| `AI_PROVIDER` | `ollama` | `ollama` or `anthropic` |
| `OLLAMA_IP`, `OLLAMA_PORT`, `OLLAMA_MODEL` | `127.0.0.1`, `11434`, `llama3.2:3b` | local model |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | — | Anthropic API |
| `EODHD_API_KEY`, `FRED_API_KEY` | — | optional market-data sources |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | — | optional Telegram alerts and plan reminders |
| `GOCARDLESS_*`, `SALTEDGE_*` | — | optional open-banking imports |
| `BACKUP_DIR` | `./database_backups` | host folder for the daily database backups |

Every integration is optional: leave a key empty and that feature simply stays empty or manual.

## Ports and volumes

- **8080** — the app (behind nginx it is served on **8443** over HTTPS; **8444** serves the CA certificate). The Compose file uses
  `network_mode: host`, so these are host ports; on Docker Desktop (Mac / Windows) switch to a bridge network with explicit `ports:`.
- **`/app/database_backups`** — mount a host folder here to keep the `pg_dump` backups outside the container.
- Health check: `GET /api/health` on the app container.

## Tags

- `vYYYY-MM-DD.HF` — one tag per release: the date of the release and its hot-fix number, `0` for the first release of a day, then `1`, `2` … (for example `v2026-10-26.0`). The version is shown in the app's sidebar footer, next to the release date, and reported by `/api/health`.
- `latest` — the most recent release.
- Set `IMAGE_NAME` in `.env` to pin a version (for example `atsob/oikos:v2026-10-26.0`) or to use your own build; to roll back, point it at an older tag.

## Data and privacy

Your data stays in your own PostgreSQL database; nothing is sent anywhere except the market-data, bank, Telegram and AI services you
configure. The demo data in the screenshots is fabricated.

## Support and license

Free and open source — if it is useful to you, consider [sponsoring its development](https://github.com/sponsors/atsob).
Licensed under **GNU AGPLv3**; a separate commercial license is available for uses the AGPL does not fit — open an issue on GitHub.
