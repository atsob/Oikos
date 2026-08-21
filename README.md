# Oikos

A personal finance app — bank & cash accounts, transactions, and multicurrency
investments (stocks, ETFs, bonds, crypto) with cash flow forecasting, tax
reporting, and portfolio analysis. FastAPI + React/TypeScript, backed by
PostgreSQL. (An earlier Streamlit version has since been fully rebuilt on this
stack.)

Self-hosted, single-tenant by design — everything below sets up one instance
for one household.

## Prerequisites

- **PostgreSQL** (with the [pgvector](https://github.com/pgvector/pgvector)
  extension installed and available) — reachable from wherever the app
  containers run. Not bundled in `docker-compose.yml`; point it at an existing
  server (a Postgres instance you already run, e.g. on the same host).
- **Docker** + **Docker Compose**.
- A Linux host — `docker-compose.yml` uses `network_mode: host` throughout, so
  Postgres (and optionally [Ollama](https://ollama.com), if you use the
  Ollama-backed AI features) are assumed reachable at `localhost`. Docker
  Desktop on Mac/Windows doesn't support host networking the same way; see the
  comment at the top of `docker-compose.yml` for the bridge-network
  alternative if that's your setup.

## Quick start

```bash
git clone https://github.com/atsob/Oikos.git
cd Oikos
cp .env.example .env
# edit .env: at minimum set DB_PASSWORD (to your Postgres password) and
# ADMIN_USERNAME/ADMIN_PASSWORD (your first login — see below)
docker compose up -d --build
```

The schema is created automatically on first startup — no manual `psql` step.
Point `docker compose up` at any empty Postgres database and it self-initializes.

Open `https://<host>:8443` and log in with the `ADMIN_USERNAME`/`ADMIN_PASSWORD`
from `.env` (only used once, to create that first account — safe to leave set
afterward). The self-signed cert's CA is downloadable from
`http://<host>:8444/ca.crt` if you want your browser/OS to trust it directly.
Add more accounts, or change your password, from the sidebar's **Account** panel
once logged in.

See `.env.example` for every other optional integration (market data, open
banking imports, local LLM) — all are optional and the app runs fine without
them; unset ones just show up empty or need entering manually in the relevant
import tab.

## Updating

```bash
git pull
docker compose up -d --build
```

Startup migrations (schema changes since your last update) run automatically
and are idempotent — safe to run on every restart.

## What's new

See [CHANGELOG.md](./CHANGELOG.md), also browsable in-app under **Release Notes**.

## License

BSD 2-Clause — see [LICENSE](./LICENSE).
