"""Incremental migration facade."""

from backend import legacy_runtime as legacy


def run() -> None:
    # init_db owns CREATE/ALTER and app_schema_migrations compatibility.
    legacy.init_db()
