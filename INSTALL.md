# Installing Oikos

This is the detailed install/config reference. If you just want the fastest
path to a running instance, see the [README](./README.md#quick-start) instead —
come back here for anything it glosses over: PostgreSQL setup, the TLS
certificate `docker-compose.yml` expects, the full `.env` reference, backups,
and troubleshooting.

## Architecture at a glance

```
                         ┌─────────────────────────────┐
   :8444 (HTTP)  ───────▶│  oikos_https (nginx:alpine)  │  serves the CA cert
   :8443 (HTTPS) ───────▶│  terminates TLS, proxies to  │  for browsers/OS to
                         │  the app on 127.0.0.1:8080   │  trust it
                         └───────────────┬───────────────┘
                                         │
                         ┌───────────────▼───────────────┐
                         │  oikos (FastAPI + built React) │  network_mode: host
                         │  :8080, 3 uvicorn workers      │
                         └───────────────┬───────────────┘
                                         │
                         ┌───────────────▼───────────────┐      ┌───────────────────┐
                         │  PostgreSQL + pgvector          │◀────▶│ oikos_scheduler    │
                         │  (on the host, not a container) │      │ daily backups, FX/  │
                         └─────────────────────────────────┘      │ price refresh, etc. │
                                                                   └───────────────────┘
```

Three containers (`oikos`, `oikos_https`, `oikos_scheduler`) all run with
`network_mode: host`, so Postgres — and Ollama, if you use the AI features —
are expected at `localhost` rather than as their own containers. That's a
deliberate, Linux-only choice matching "everything on one server"; see the
comment at the top of `docker-compose.yml` if you're on Docker Desktop
(Mac/Windows) and need the bridge-network alternative instead.

## Prerequisites

- A **Linux host** (see the `network_mode: host` note above).
- **Docker** + **Docker Compose plugin** (`docker compose version` should work).
- **PostgreSQL 14+** with the [pgvector](https://github.com/pgvector/pgvector)
  extension installed, reachable at `localhost` (or wherever `DB_HOST` points).
  Oikos doesn't containerize its own database — point it at one you already run.

## 1. PostgreSQL setup

Install Postgres and pgvector (Debian/Ubuntu shown; adjust for your distro —
pgvector ships as a package on most current ones):

```bash
sudo apt install postgresql postgresql-contrib postgresql-*-pgvector
```

Create the database and a role for the app:

```bash
sudo -u postgres psql -c "CREATE ROLE admin WITH LOGIN PASSWORD 'pick-a-real-password';"
sudo -u postgres psql -c "CREATE DATABASE \"Oikos\" OWNER admin;"
sudo -u postgres psql -d Oikos -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

Nothing else to do here — the app creates every table itself on first startup
(see "Schema & starter data" below). Match `DB_USER`/`DB_PASSWORD`/`DB_NAME`
above to what you put in `.env` in the next step.

## 2. Get the code & configure `.env`

```bash
git clone https://github.com/atsob/Oikos.git
cd Oikos
cp .env.example .env
```

Edit `.env` — at minimum set `DB_PASSWORD` to match what you created above,
and `ADMIN_USERNAME`/`ADMIN_PASSWORD` for your first login. See the
[environment variable reference](#environment-variable-reference) below for
everything else; all of it is optional.

## 3. Generate a TLS certificate

`docker-compose.yml`'s `oikos_https` service expects three files that aren't
generated automatically — `./ssl/cert.pem`, `./ssl/key.pem`, `./ssl/rootCA.pem`
— because a self-hosted instance needs its own certificate authority (there's
no public domain for Let's Encrypt to validate against). Create them once,
before the first `docker compose up`:

```bash
mkdir -p ssl && cd ssl

# 1. A local certificate authority (valid 10 years — this is what browsers/OS
#    end up trusting, not the leaf cert below)
openssl genrsa -out rootCA.key 4096
openssl req -x509 -new -nodes -key rootCA.key -sha256 -days 3650 \
    -out rootCA.pem -subj "/CN=Oikos Local CA"

# 2. A server certificate signed by that CA. Replace the IPs/hostnames in
#    -addext with whatever you'll actually browse to (LAN IP, hostname, both).
openssl genrsa -out key.pem 2048
openssl req -new -key key.pem -out server.csr -subj "/CN=oikos.local"
openssl x509 -req -in server.csr -CA rootCA.pem -CAkey rootCA.key \
    -CAcreateserial -out cert.pem -days 825 -sha256 \
    -extfile <(printf "subjectAltName=DNS:oikos.local,IP:192.168.1.10")

rm server.csr rootCA.key rootCA.srl
cd ..
```

`ssl/` is gitignored — these files stay local to the host, never committed.
If you'd rather use your own existing CA/cert (e.g. from `mkcert` or an
internal PKI), just drop the three files with those same names into `./ssl/`
instead of running the commands above.

## 4. Start the app

```bash
docker compose up -d --build
```

This builds the image locally (or set `IMAGE_NAME` in `.env` to pull a
pre-built one instead — see the reference below). First startup creates every
table via `database/Oikos.sql` and seeds generic starter data (currencies,
well-known stocks/ETFs, major institutions, a standard category list, common
payees, and Greek-resident tax rules) — nothing further to run by hand.

Check it came up clean:

```bash
docker compose logs -f oikos
curl -k https://localhost:8443/api/health
```

## 5. First login & trusting the certificate

Open `https://<host>:8443` and log in with the `ADMIN_USERNAME`/`ADMIN_PASSWORD`
from `.env` — that's the only time those two values are used; the account is
created once and `.env` is safe to leave them set afterward.

Your browser/OS won't trust the certificate yet, since it's signed by a CA
nobody knows about. Download and trust it once:

```
http://<host>:8444/ca.crt
```

- **macOS**: double-click the downloaded file → Keychain Access → set
  "Always Trust" on it.
- **Windows**: double-click it → Install Certificate → Local Machine →
  "Place all certificates in the following store" → Trusted Root Certification
  Authorities.
- **Linux (Debian/Ubuntu)**: `sudo cp ca.crt /usr/local/share/ca-certificates/oikos.crt && sudo update-ca-certificates`
- **Android/iOS**: open the downloaded file directly and follow the prompt to
  install a CA certificate (iOS also needs Settings → General → About →
  Certificate Trust Settings → enable full trust for it afterward).

Once trusted, `https://<host>:8443` loads without a warning from that device
going forward. Add more accounts, or change your own password, from the
sidebar's **Account** panel once logged in.

## Environment variable reference

All variables live in `.env` (copied from `.env.example`). Required ones are
marked; everything else has a working default or is an optional integration.

| Variable | Required | Purpose |
|---|---|---|
| `DB_USER` | | Postgres role (default `admin`) |
| `DB_PASSWORD` | **Yes** | Postgres password — `docker compose up` refuses to start without it |
| `DB_HOST` | | Postgres host (default `localhost`) |
| `DB_PORT` | | Postgres port (default `5432`) |
| `DB_NAME` | | Database name (default `Oikos`) |
| `DB_TIMEZONE` | | Applied to every DB session so timestamps display in local time (default `Europe/Athens`) |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | **Yes**, first run only | Creates the first login account if the `Users` table is empty; harmless to leave set afterward |
| `OLLAMA_IP` / `OLLAMA_PORT` / `OLLAMA_MODEL` | | Local LLM for AI summaries — optional, only if you use Ollama-backed features |
| `EODHD_API_KEY` | | Market data provider key — get one at [eodhd.com](https://eodhd.com); optional, price/dividend downloads just won't work without it |
| `GOCARDLESS_SECRET_ID` / `GOCARDLESS_SECRET_KEY` | | Pre-fills Importers → GoCardless (PSD2 open banking); optional, can be entered manually per session instead |
| `SALTEDGE_APP_ID` / `SALTEDGE_SECRET` | | Pre-fills Importers → Salt Edge; same deal as GoCardless above |
| `BACKUP_DIR` | | Host path for scheduled DB backups, bind-mounted into the container (default `./database_backups`, auto-created) |
| `IMAGE_NAME` | | Image tag `docker compose pull`/`up` uses (default `atsob/oikos:latest`); override if you're pulling your own pre-built image instead of building locally |

## Updating

```bash
git pull
docker compose up -d --build
```

Startup migrations (schema changes since your last update) run automatically
and are idempotent — safe on every restart. They never touch the starter
seed data or your own entries once the database already exists.

## Backups

The scheduler container runs a `pg_dump` backup daily at 06:00 (server time),
keeping 30 days of history, written to `BACKUP_DIR`. You can also trigger a
manual backup, list/download/delete existing ones, or restore from a file —
all from **Tools → Backup & Restore** in the app itself; no need to shell into
a container for routine backup management.

## Troubleshooting

- **`docker compose up` fails immediately with a bind-mount error for `ssl/cert.pem`**
  — you skipped [step 3](#3-generate-a-tls-certificate); those three files
  don't get created automatically.
- **`ModuleNotFoundError: No module named 'bcrypt'` running outside Docker**
  (e.g. `uvicorn api.main:app --reload` directly) — `pip install -r requirements.txt`
  in whatever environment you're running it from; the Docker image already
  has this, this only bites local/dev runs.
- **Browser shows a certificate warning at `https://<host>:8443`** — you
  haven't trusted the CA yet, or trusted it on a different device than the
  one you're browsing from now. Re-download `http://<host>:8444/ca.crt` on
  the device showing the warning and follow the per-OS steps above.
- **`docker compose pull` fails with "pull access denied"** — `IMAGE_NAME`
  points at an image that doesn't exist on any registry you have access to.
  Either build locally (`docker compose up -d --build`, no separate pull
  step needed) or set `IMAGE_NAME` to an image you've actually pushed
  somewhere reachable.
- **First login fails even though `ADMIN_USERNAME`/`ADMIN_PASSWORD` are set**
  — that bootstrap only ever fires once, when the `Users` table is empty. If
  you're re-running against a database that already has a `Users` table
  (e.g. testing against a DB from a previous install), those variables are
  ignored; log in with whatever account already exists there instead, or
  point at a genuinely empty database.

## Uninstalling / data locations

```bash
docker compose down
```

This stops and removes the containers but never touches Postgres (it isn't
containerized) or `./ssl/`/`./database_backups` (bind-mounted from the host).
To fully remove an install: drop the Postgres database yourself, and delete
the repo checkout (which takes `ssl/` and `database_backups/` with it, unless
you pointed `BACKUP_DIR` outside the checkout).
