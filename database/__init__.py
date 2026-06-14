# Database module
# The connection module is always safe to import.
# crud.py and queries.py import streamlit — they are only needed by the Streamlit UI.
# API routers import from database.connection directly and do not need this __init__.
from database.connection import get_connection, get_sql_database  # noqa: F401
try:
    from database.crud import save_changes, save_changes_no_serial, save_changes_mid  # noqa: F401
    from database.crud import update_accounts_balances, update_pension_balances, update_holdings  # noqa: F401
except Exception:
    pass