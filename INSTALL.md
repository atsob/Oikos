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
                         │  (host-native, or containerized │      │ daily backups, FX/  │
                         │  via --profile full)            │      │ price refresh, etc. │
                         └─────────────────────────────────┘      └───────────────────┘

            (Ollama, if used, sits alongside the same way — host-native
             or containerized via that same --profile full.)
```

Three containers (`oikos`, `oikos_https`, `oikos_scheduler`) all run with
`network_mode: host`, so Postgres — and Ollama, if you use the AI features —
are expected at `localhost` rather than as their own containers. That's a
deliberate, Linux-only choice matching "everything on one server"; see the
comment at the top of `docker-compose.yml` if you're on Docker Desktop
(Mac/Windows) and need the bridge-network alternative instead.

You've got two ways to get Postgres (required) and Ollama (optional) in
place: install them on the host yourself ([step 1](#1-postgresql-setup) /
[step 1b](#1b-ollama-setup-optional)), or let Compose run both as containers
instead ([step 1 alt](#1-alt-run-postgres--ollama-in-docker-instead)). Pick
one — don't do both, or you'll have two Postgres instances fighting over
port 5432.

## Prerequisites

- A **Linux host** (see the `network_mode: host` note above).
- **Docker** + **Docker Compose plugin** (`docker compose version` should work).
- **PostgreSQL 14+** with the [pgvector](https://github.com/pgvector/pgvector)
  extension installed, reachable at `localhost` (or wherever `DB_HOST` points)
  — either installed on the host yourself, or containerized via the
  `--profile full` alternative below. Oikos doesn't bundle its database into
  its own image; one or the other has to provide it.
- **Ollama**, only if you want the AI-backed features (Dashboard's weekly/monthly
  summaries, the RAG chat). Same two options as Postgres — host-native or
  containerized. Skip it entirely and those features just won't be available;
  nothing else in the app depends on it.

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
(see [step 4](#4-start-the-app) below). Match `DB_USER`/`DB_PASSWORD`/`DB_NAME`
above to what you put in `.env` in the next step.

## 1b. Ollama setup (optional)

Skip this if you don't want the AI-backed features (Dashboard's AI summaries,
RAG chat) — everything else in the app works without it. Install Ollama
directly on the host:

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.2:3b   # or whatever OLLAMA_MODEL you set in .env
```

The install script also sets Ollama up as a systemd service listening on
`localhost:11434` (matching `OLLAMA_IP`/`OLLAMA_PORT`'s defaults), so nothing
further to configure. Verify it's up:

```bash
curl http://localhost:11434/api/tags
```

Ollama itself runs fine on CPU (slower AI summaries) or with an NVIDIA GPU if
one's available — the install script auto-detects and uses one if present.

## 1-alt. Run Postgres & Ollama in Docker instead

If you'd rather not install either on the host, `docker-compose.yml` has both
as opt-in services behind the `full` profile — skip steps 1 and 1b above and
use this instead:

```bash
docker compose --profile full up -d --build
```

This starts `postgres` (image `pgvector/pgvector:pg16`, pgvector already
enabled via `docker/postgres-init/`) and `ollama` (image `ollama/ollama`)
alongside the app, each with its own named volume (`postgres_data`,
`ollama_data`) so data survives container recreation. Because every service
here is `network_mode: host`, they bind straight to `localhost:5432` and
`localhost:11434` — the same defaults `DB_HOST`/`OLLAMA_IP` already use, so
no other `.env` changes are needed versus the host-native path.

Two things the containerized path still needs done manually:

- **Pull a model** — the `ollama/ollama` image doesn't ship one:
  ```bash
  docker exec oikos_ollama ollama pull llama3.2:3b   # or your OLLAMA_MODEL
  ```
- **GPU passthrough** (optional, NVIDIA only) — add this to the `ollama`
  service in `docker-compose.yml` if you want the container to use a GPU
  instead of CPU (requires the [NVIDIA Container
  Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
  installed on the host first):
  ```yaml
      deploy:
        resources:
          reservations:
            devices:
              - driver: nvidia
                count: all
                capabilities: [gpu]
  ```

Every `docker compose` command from here on (`up`, `down`, `logs`, etc.) needs
`--profile full` too, or Compose won't know to include these two services.

**Managing this through a GUI tool instead of the CLI** (OpenMediaVault,
Portainer, etc.)? Those generally have no way to pass `--profile` on whatever
`docker compose up` they run under the hood — so by default they'll deploy
`oikos`/`oikos_https`/`oikos_scheduler` only, exactly as if the `full` profile
didn't exist, leaving `postgres`/`ollama` off. That's the right outcome if
you already run Postgres/Ollama as their own separate containers elsewhere
(as e.g. OMV's Compose plugin encourages) — just update `docker-compose.yml`
and redeploy as usual, nothing changes. If you *do* want the bundled
`postgres`/`ollama` from a GUI tool with no `--profile` option, add this line
to `.env` instead — Compose reads it automatically, no flag needed:
```
COMPOSE_PROFILES=full
```
Don't set that if you already have separate Postgres/Ollama containers
running — both would try to bind `localhost:5432`/`localhost:11434` and
one will fail to start.

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
# or, if you're using the containerized Postgres/Ollama from step 1-alt:
docker compose --profile full up -d --build
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
- **AI summaries/chat error out or time out** — Ollama isn't reachable, or
  the model in `OLLAMA_MODEL` hasn't been pulled yet. `curl http://localhost:11434/api/tags`
  should list it; if it's empty, run the `ollama pull`/`docker exec ... ollama pull`
  command from step 1b/1-alt. Everything else in the app works fine without
  Ollama running at all.
- **Ran `docker compose up` without `--profile full`, but meant to use the
  containerized Postgres/Ollama** — the `postgres`/`ollama` services simply
  won't start (no error), and `oikos` will fail to connect to a Postgres
  that isn't there. Re-run with `--profile full`; every subsequent command
  against this install (`down`, `logs`, `pull`, ...) needs that flag too.
- **Two Postgres instances arguing over port 5432** — you have one installed
  on the host *and* ran `--profile full`, which also tries to bind the
  containerized one to `localhost:5432`. Pick one path (step 1 or step
  1-alt), not both.

## Uninstalling / data locations

```bash
docker compose down
# or, if you used the containerized Postgres/Ollama:
docker compose --profile full down          # keeps postgres_data/ollama_data
docker compose --profile full down -v       # also deletes them (irreversible)
```

This stops and removes the containers. If Postgres/Ollama are host-native,
neither is touched — only `./ssl/`/`./database_backups` (bind-mounted from
the host) survive alongside them. If you used the `full` profile instead,
your data lives in the `postgres_data`/`ollama_data` named volumes, which
`docker compose down` (without `-v`) leaves in place across restarts.

To fully remove an install: drop the Postgres database yourself (or
`docker compose --profile full down -v` if it was containerized), and delete
the repo checkout (which takes `ssl/` and `database_backups/` with it, unless
you pointed `BACKUP_DIR` outside the checkout).
