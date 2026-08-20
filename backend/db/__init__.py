"""Shared persistence helpers for FastAPI-native modules."""

# Import the pool first.  ``backend.legacy_runtime`` reuses this singleton
# during the transition, and importing the adapter first would create a
# package-initialisation cycle.
from backend.db.pool import (
    DB_POOL,
    DatabaseConnectionPool,
    DatabasePoolTimeoutError,
    connect_db,
)

from backend.db.adapter import (
    adapt_sql,
    close,
    connection,
    execute,
    execute_rowcount,
    normalize_base_url,
    query_all,
    query_one,
    transaction,
    utc_now_iso,
)

__all__ = (
    "adapt_sql",
    "DB_POOL",
    "DatabaseConnectionPool",
    "DatabasePoolTimeoutError",
    "close",
    "connection",
    "execute",
    "execute_rowcount",
    "normalize_base_url",
    "query_all",
    "query_one",
    "connect_db",
    "transaction",
    "utc_now_iso",
)
