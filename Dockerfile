# ── Stage 1: Build React ──────────────────────────────────────────────────────
FROM node:22-alpine AS frontend-build
WORKDIR /build
# .git is excluded from the build context (see .dockerignore), so the commit hash
# shown in the sidebar footer is passed in from the host instead of using `git`
# inside the image. See docker-compose.yml's build.args for how these are set.
ARG GIT_HASH=unknown
ARG GIT_DATE=
ENV GIT_HASH=$GIT_HASH
ENV GIT_DATE=$GIT_DATE
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build:docker

# ── Stage 2: Python app ───────────────────────────────────────────────────────
FROM python:3.13-slim

# System dependencies + PostgreSQL client
RUN set -eux \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential curl ca-certificates gnupg libpq-dev \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
 && . /etc/os-release \
 && arch="$(dpkg --print-architecture)" \
 && cat > /etc/apt/sources.list.d/pgdg.sources <<EOF
Types: deb
URIs: https://apt.postgresql.org/pub/repos/apt
Suites: ${VERSION_CODENAME}-pgdg
Components: main
Architectures: ${arch}
Signed-By: /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
EOF
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client-18 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application source
COPY . .

# Inject pre-built React (replaces any stale local dist)
COPY --from=frontend-build /build/dist ./frontend/dist

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
