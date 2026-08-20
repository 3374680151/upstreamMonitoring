"""Small PyMySQL connection pool used by the repository boundary.

This module deliberately contains infrastructure only.  It has no import
path back to the application runtime, so repositories can acquire MySQL
connections without depending on ``legacy_runtime``.
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path
from queue import Empty, LifoQueue
from threading import Lock
from typing import Any, Callable, Iterator

import pymysql
from pymysql.cursors import DictCursor


APP_DIR = Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    env_path = APP_DIR / ".env"
    if not env_path.exists():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    except OSError:
        pass


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


_load_dotenv()

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "upstream"),
}
DB_POOL_SIZE = _env_int("DB_POOL_SIZE", 8, 1, 32)
DB_POOL_ACQUIRE_TIMEOUT_SECONDS = _env_int("DB_POOL_ACQUIRE_TIMEOUT", 5, 1, 60)
DB_CONNECT_TIMEOUT_SECONDS = _env_int("DB_CONNECT_TIMEOUT", 5, 1, 60)
DB_READ_TIMEOUT_SECONDS = _env_int("DB_READ_TIMEOUT", 15, 1, 300)
DB_WRITE_TIMEOUT_SECONDS = _env_int("DB_WRITE_TIMEOUT", 15, 1, 300)


class DatabasePoolTimeoutError(TimeoutError):
    """The pool had no available connection before the acquire timeout."""


def _running_under_test_runner() -> bool:
    return "unittest" in sys.modules or bool(os.environ.get("PYTEST_CURRENT_TEST"))


def connect_db() -> pymysql.connections.Connection:
    """Create one configured MySQL connection."""
    if _running_under_test_runner() and DB_CONFIG["database"] == "upstream":
        raise RuntimeError(
            "拒绝在测试环境下连接生产数据库 upstream。"
            "测试必须走独立测试库（DB_NAME=upstream_test）。"
        )
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
    """Bounded, lazily-created process-local connection pool."""

    def __init__(
        self, connection_factory: Callable[[], Any], size: int, acquire_timeout: float
    ) -> None:
        self._connection_factory = connection_factory
        self._acquire_timeout = float(acquire_timeout)
        self._state_lock = Lock()
        self._closed = False
        self._slots: LifoQueue[Any] = LifoQueue(maxsize=max(1, int(size)))
        for _ in range(max(1, int(size))):
            self._slots.put(None)

    @contextmanager
    def connection(self) -> Iterator[Any]:
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
    connect_db,
    size=DB_POOL_SIZE,
    acquire_timeout=DB_POOL_ACQUIRE_TIMEOUT_SECONDS,
)


__all__ = [
    "DB_CONFIG",
    "DB_POOL",
    "DatabaseConnectionPool",
    "DatabasePoolTimeoutError",
    "connect_db",
]
