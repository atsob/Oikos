FROM python:3.13-slim

# 1. Install system dependencies + PostgreSQL Client 18
RUN set -eux \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        git \
        ca-certificates \
        gnupg \
 \
 # Add PGDG signing key
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
 \
 # Add correct PGDG repo
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
# Μετά το EOF ξεκινάμε νέα εντολή RUN για να αποφύγουμε συντακτικά μπερδέματα
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        libpq-dev \
        postgresql-client-18 \
 && rm -rf /var/lib/apt/lists/*

# 2. Set working directory
WORKDIR /app

# 3. Clone repository
RUN git clone https://github.com/atsob/Personal_Finance.git /tmp/repo \
 && cp -a /tmp/repo/. /app/ \
 && rm -rf /tmp/repo

# 4. Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Runtime hygiene
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8501

CMD ["streamlit", "run", "app.py"]
