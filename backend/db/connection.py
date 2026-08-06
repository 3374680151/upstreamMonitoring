"""Database dependency facade backed by the existing PyMySQL pool."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from backend import legacy_runtime as legacy


@contextmanager
def connection() -> Iterator[object]:
    with legacy.db_connection() as conn:
        yield conn


def close() -> None:
    legacy.close_database_pool()
