"""Incremental migration facade."""

from backend.db.schema import init


def run() -> None:
    init()
