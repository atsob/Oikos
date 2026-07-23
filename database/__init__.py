# Database module
# The connection module is always safe to import.
# API routers import from database.connection/crud/queries directly and do not
# need this __init__ — kept only as a convenience re-export.
from database.connection import get_connection, get_sql_database  # noqa: F401
try:
    from database.crud import update_accounts_balances, update_pension_balances, update_holdings  # noqa: F401
except Exception:
    pass