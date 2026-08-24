"""Database connection pool and query helpers.

Moved out of ``backend.legacy_runtime`` so the FastAPI boundary can import the
pool directly without pulling in the whole legacy runtime.  The legacy runtime
re-exports every public name below for backward compatibility.

Configuration (``DB_CONFIG`` and the timeout knobs) is sourced from
``backend.core.config`` and ``DatabasePoolTimeoutError`` from
``backend.core.errors`` so this module has no dependency on the legacy runtime,
which in turn re-exports the pool symbols defined here.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from queue import Empty, LifoQueue
from typing import Any, Dict, Iterable, Iterator, List, Optional

import pymysql
from pymysql.cursors import DictCursor

from backend.core.config import (
    DB_CONFIG,
    DB_CONNECT_TIMEOUT_SECONDS,
    DB_POOL_ACQUIRE_TIMEOUT_SECONDS,
    DB_POOL_SIZE,
    DB_READ_TIMEOUT_SECONDS,
    DB_WRITE_TIMEOUT_SECONDS,
)
from backend.core.errors import DatabasePoolTimeoutError
from backend.core.state import DB_LOCK


def connect_db() -> pymysql.connections.Connection:
    """新建一个可由连接池独占租用的 MySQL 连接。"""
    return pymysql.connect(
        host=DB_CONFIG["host"],
        port=DB_CONFIG["port"],
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"],
        database=DB_CONFIG["database"],
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
        connect_timeout=DB_CONNECT_TIMEOUT_SECONDS,
        read_timeout=DB_READ_TIMEOUT_SECONDS,
        write_timeout=DB_WRITE_TIMEOUT_SECONDS,
    )


class DatabaseConnectionPool:
    """有界、惰性创建的进程内 MySQL 连接池。"""

    def __init__(self, connection_factory, size: int, acquire_timeout: float):
        self._connection_factory = connection_factory
        self._acquire_timeout = acquire_timeout
        self._state_lock = threading.Lock()
        self._closed = False
        self._slots = LifoQueue(maxsize=size)
        for _ in range(size):
            self._slots.put(None)

    @contextmanager
    def connection(self):
        try:
            connection = self._slots.get(timeout=self._acquire_timeout)
        except Empty as exc:
            raise DatabasePoolTimeoutError("数据库连接池繁忙，请稍后重试") from exc
        try:
            if connection is not None:
                try:
                    connection.ping(reconnect=False)
                except Exception:
                    try:
                        connection.close()
                    except Exception:
                        pass
                    connection = None
            if connection is None:
                connection = self._connection_factory()
            yield connection
        finally:
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    try:
                        connection.close()
                    except Exception:
                        pass
                    connection = None
            with self._state_lock:
                if self._closed:
                    if connection is not None:
                        try:
                            connection.close()
                        except Exception:
                            pass
                else:
                    self._slots.put(connection)

    def close(self) -> None:
        with self._state_lock:
            self._closed = True
            while True:
                try:
                    connection = self._slots.get_nowait()
                except Empty:
                    break
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass


DB_POOL = DatabaseConnectionPool(
    lambda: connect_db(),
    size=DB_POOL_SIZE,
    acquire_timeout=DB_POOL_ACQUIRE_TIMEOUT_SECONDS,
)


@contextmanager
def db_connection():
    with DB_POOL.connection() as connection:
        yield connection


def close_database_pool() -> None:
    DB_POOL.close()


def _q(sql: str) -> str:
    """把 SQLite 风格的 ? 占位符转成 PyMySQL 的 %s。

    PyMySQL 在带参数时执行 `query % args`，故 SQL 模板里任何字面量 % 都必须先转义成
    %%，否则 LIKE '%x%'、DATE_FORMAT('%Y') 之类会在运行时抛格式化错误（不是清晰的 SQL 错误）。
    必须「先转义 % 再替换 ?」，顺序不能反（反了会把 %s 变成 %%s）。
    注意：本函数无法处理「SQL 字符串字面量里出现的裸 ?」，请勿在 SQL 中写占位符以外的 ?。
    """
    return sql.replace("%", "%%").replace("?", "%s")


def _existing_columns(cur: Any, table: str) -> set:
    cur.execute(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (table,),
    )
    return {row["COLUMN_NAME"] for row in cur.fetchall()}


def dict_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    # DictCursor 已返回 dict，这里保留函数以兼容旧调用点。
    return dict(row)


def db_query_all(
    sql: str,
    params: Iterable[Any] = (),
    connection: Optional[pymysql.connections.Connection] = None,
) -> List[Dict[str, Any]]:
    if connection is None:
        with db_connection() as leased:
            return db_query_all(sql, params, connection=leased)
    with connection.cursor() as cur:
        cur.execute(_q(sql), tuple(params))
        return [dict(row) for row in cur.fetchall()]


def db_query_one(
    sql: str,
    params: Iterable[Any] = (),
    connection: Optional[pymysql.connections.Connection] = None,
) -> Optional[Dict[str, Any]]:
    if connection is None:
        with db_connection() as leased:
            return db_query_one(sql, params, connection=leased)
    with connection.cursor() as cur:
        cur.execute(_q(sql), tuple(params))
        row = cur.fetchone()
        return dict(row) if row else None


def db_execute(sql: str, params: Iterable[Any] = ()) -> int:
    with db_connection() as connection:
        try:
            with connection.cursor() as cur:
                cur.execute(_q(sql), tuple(params))
                lastrowid = cur.lastrowid
            connection.commit()
            return lastrowid
        except Exception:
            try:
                connection.rollback()
            except Exception:
                pass
            raise


def db_execute_rowcount(sql: str, params: Iterable[Any] = ()) -> int:
    """Execute a write and return affected rows for compare-and-swap updates."""
    with db_connection() as connection:
        try:
            with connection.cursor() as cur:
                cur.execute(_q(sql), tuple(params))
                rowcount = int(cur.rowcount or 0)
            connection.commit()
            return rowcount
        except Exception:
            try:
                connection.rollback()
            except Exception:
                pass
            raise


# --- Facade retained for the FastAPI dependency layer ----------------------

@contextmanager
def connection() -> Iterator[object]:
    with db_connection() as conn:
        yield conn


def close() -> None:
    close_database_pool()
