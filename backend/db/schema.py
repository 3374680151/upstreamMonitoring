"""Schema bootstrap facade."""

from backend import legacy_runtime as legacy


def init() -> None:
    legacy.init_db()
