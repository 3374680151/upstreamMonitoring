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

from contextlib import contextmanager
from typing import Any, Dict, Iterable, Iterator, List, Optional

import pymysql
from pymysql.cursors import DictCursor
from sqlalchemy import create_engine
from sqlalchemy.exc import TimeoutError as PoolAcquireTimeoutError

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
    """新建一个可由连接池独占租用的 MySQL 连接。

    注意：连接级不设 ``cursorclass=DictCursor``——SQLAlchemy 方言初始化要用普通游标
    探测服务器版本；需要字典行的代码一律显式 ``connection.cursor(DictCursor)``。
    """
    return pymysql.connect(
        host=DB_CONFIG["host"],
        port=DB_CONFIG["port"],
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"],
        database=DB_CONFIG["database"],
        charset="utf8mb4",
        autocommit=False,
        connect_timeout=DB_CONNECT_TIMEOUT_SECONDS,
        read_timeout=DB_READ_TIMEOUT_SECONDS,
        write_timeout=DB_WRITE_TIMEOUT_SECONDS,
    )


class DatabaseConnectionPool:
    """基于 SQLAlchemy ``QueuePool`` 的有界 MySQL 连接池。

    连接生命周期完全复用成熟实现，不再手写：
    - 惰性创建、上限 ``pool_size``（``max_overflow=0`` 不超卖）、获取超时 ``pool_timeout``
    - ``pool_pre_ping=True``：每次租约前探活，坏连接自动重建
    - 归还时自动 rollback（reset_on_return 默认行为），无法复位的连接自动废弃重建
    - ``dispose()`` 关闭全部空闲连接
    """

    def __init__(self, connection_factory, size: int, acquire_timeout: float):
        self._engine = create_engine(
            "mysql+pymysql://",
            creator=connection_factory,
            pool_size=size,
            max_overflow=0,
            pool_timeout=acquire_timeout,
            pool_pre_ping=True,
        )

    @contextmanager
    def connection(self):
        try:
            leased = self._engine.raw_connection()
        except PoolAcquireTimeoutError as exc:
            raise DatabasePoolTimeoutError("数据库连接池繁忙，请稍后重试") from exc
        try:
            yield leased
        finally:
            # close() 只把连接归还到池；归还前的 rollback 由 SQLAlchemy 统一执行。
            leased.close()

    def close(self) -> None:
        self._engine.dispose()


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
    with connection.cursor(DictCursor) as cur:
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
    with connection.cursor(DictCursor) as cur:
        cur.execute(_q(sql), tuple(params))
        row = cur.fetchone()
        return dict(row) if row else None


def db_execute(sql: str, params: Iterable[Any] = ()) -> int:
    with db_connection() as connection:
        try:
            with connection.cursor(DictCursor) as cur:
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
            with connection.cursor(DictCursor) as cur:
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
