import os
from dotenv import load_dotenv

# Loads a local .env file into the process environment for local/dev runs (see
# .env.example). No-op — and not required — inside Docker, where docker-compose
# already injects these as real environment variables before the process starts.
load_dotenv()

def get_env_config():
    """Get environment configuration."""
    return {
        'db_user': os.getenv("DB_USER", "admin"),
        # No hardcoded fallback for secrets — must come from a real env var (see
        # .env.example). A previous version of this file defaulted to a real
        # password, which is fine for a gitignored local file but meant anyone
        # copying this file's *structure* (e.g. into docker-compose.yml, which
        # IS tracked) could end up committing a live credential, as happened here.
        'db_password': os.getenv("DB_PASSWORD", ""),
        'db_host': os.getenv("DB_HOST", "localhost"),
        'db_port': os.getenv("DB_PORT", "5432"),
        'db_name': os.getenv("DB_NAME", "Oikos"),
        # Timezone applied to every PostgreSQL session so that TIMESTAMPTZ columns
        # (e.g. Historical_Prices.Downloaded_At) display in local time rather than UTC.
        # The PostgreSQL Docker container typically runs UTC; this client-side setting
        # converts stored UTC values to the configured zone without touching the server.
        'db_timezone': os.getenv("DB_TIMEZONE", "Europe/Athens"),
        'ollama_ip': os.getenv("OLLAMA_IP", "localhost"),
        'ollama_port': os.getenv("OLLAMA_PORT", "11434"),
        'ollama_model': os.getenv("OLLAMA_MODEL", "llama3.2:3b"),
        # Which LLM backend ai/llm.py's init_llm() returns — 'ollama' (default,
        # fully local/private, no API cost) or 'anthropic' (hosted Claude, needs
        # anthropic_api_key below, but far more capable/faster than a small local
        # model). Same LangChain chat-model interface either way, so nothing else
        # (create_ai_agent, weekly/monthly summaries) needs to know which is active.
        'ai_provider': os.getenv("AI_PROVIDER", "ollama").strip().lower(),
        'anthropic_api_key': os.getenv("ANTHROPIC_API_KEY", ""),
        'anthropic_model': os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
        'persist_dir': os.getenv("PERSIST_DIR", "/app/storage_rag"),
        # Same reasoning as db_password above — no hardcoded fallback key.
        'eodhd_api_key': os.getenv("EODHD_API_KEY", ""),
        # First login account, bootstrapped once on startup if the Users table is
        # empty — see api/main.py's lifespan hook and database/queries.py's
        # bootstrap_admin_user(). Empty here means no account gets auto-created;
        # you'd need to insert one directly.
        'admin_username': os.getenv("ADMIN_USERNAME", ""),
        'admin_password': os.getenv("ADMIN_PASSWORD", ""),
        # GoCardless Bank Account Data (PSD2 open banking) — optional
        # Set these to pre-fill credentials in the GoCardless import tab.
        # If unset, the user must enter them manually in the UI each session.
        'gocardless_secret_id':  os.getenv("GOCARDLESS_SECRET_ID",  ""),
        'gocardless_secret_key': os.getenv("GOCARDLESS_SECRET_KEY", ""),
        # Salt Edge Account Information API v5 (PSD2 open banking) — optional
        # Set these to pre-fill credentials in the Salt Edge import tab.
        # Obtain from: https://www.saltedge.com/dashboard → Applications
        'saltedge_app_id': os.getenv("SALTEDGE_APP_ID", ""),
        'saltedge_secret': os.getenv("SALTEDGE_SECRET", ""),
    }

ENV_CONFIG = get_env_config()
DB_URI = f"postgresql+psycopg2://{ENV_CONFIG['db_user']}:{ENV_CONFIG['db_password']}@{ENV_CONFIG['db_host']}:{ENV_CONFIG['db_port']}/{ENV_CONFIG['db_name']}"
OLLAMA_URL = f"http://{ENV_CONFIG['ollama_ip']}:{ENV_CONFIG['ollama_port']}"