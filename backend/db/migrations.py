"""Incremental migration facade."""

from backend.db.schema import init_db


def run() -> None:
    # init_db owns CREATE/ALTER and app_schema_migrations compatibility.
    init_db()
