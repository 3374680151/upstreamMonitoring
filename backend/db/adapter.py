"""Database boundary used by FastAPI-native repositories."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Optional

from backend.core.time import utc_now_iso
from backend.db.pool import DB_POOL, DatabasePoolTimeoutError


@contextmanager
def connection() -> Iterator[Any]:
    """Lease one connection from the configured MySQL pool."""
    with DB_POOL.connection() as conn:
        yield conn


_lease_connection = connection


@contextmanager
def transaction() -> Iterator[Any]:
    """Run several statements atomically on one pooled connection."""
    with connection() as conn:
        try:
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise


def close() -> None:
    """Close the shared connection pool during application shutdown."""
    DB_POOL.close()


def adapt_sql(sql: str) -> str:
    """Convert repository ``?`` placeholders to PyMySQL placeholders.

    This intentionally preserves the legacy helper's percent escaping rule:
    literal percent signs must be doubled before PyMySQL interpolates bound
    parameters.
    """
    return sql.replace("%", "%%").replace("?", "%s")


def query_all(
    sql: str,
    params: Iterable[Any] = (),
    *,
    connection: Optional[Any] = None,
) -> list[dict[str, Any]]:
    """Return all rows as dictionaries without committing a transaction."""
    if connection is None:
        with _lease_connection() as conn:
            return query_all(sql, params, connection=conn)
    with connection.cursor() as cur:
        cur.execute(adapt_sql(sql), tuple(params))
        return [dict(row) for row in cur.fetchall()]


def query_one(
    sql: str,
    params: Iterable[Any] = (),
    *,
    connection: Optional[Any] = None,
) -> Optional[dict[str, Any]]:
    """Return one row as a dictionary, or ``None`` when no row matches."""
    if connection is None:
        with _lease_connection() as conn:
            return query_one(sql, params, connection=conn)
    with connection.cursor() as cur:
        cur.execute(adapt_sql(sql), tuple(params))
        row = cur.fetchone()
        return dict(row) if row else None


def execute(
    sql: str,
    params: Iterable[Any] = (),
    *,
    connection: Optional[Any] = None,
) -> int:
    """Execute a write and return its ``lastrowid``.

    Supplying ``connection`` makes this part of the caller's transaction;
    otherwise this helper commits or rolls back exactly once.
    """
    if connection is not None:
        with connection.cursor() as cur:
            cur.execute(adapt_sql(sql), tuple(params))
            return int(cur.lastrowid or 0)

    with _lease_connection() as conn:
        try:
            result = execute(sql, params, connection=conn)
            conn.commit()
            return result
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise


def execute_rowcount(
    sql: str,
    params: Iterable[Any] = (),
    *,
    connection: Optional[Any] = None,
) -> int:
    """Execute a write and return its affected row count."""
    if connection is not None:
        with connection.cursor() as cur:
            cur.execute(adapt_sql(sql), tuple(params))
            return int(cur.rowcount or 0)

    with _lease_connection() as conn:
        try:
            result = execute_rowcount(sql, params, connection=conn)
            conn.commit()
            return result
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise


# Keep the timestamp and URL normalization contract at this boundary so
# repositories do not need to import the compatibility runtime.
def normalize_base_url(value: str) -> str:
    return str(value or "").strip().rstrip("/")
