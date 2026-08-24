"""Upstream FastAPI entry point.

Compatible shell that delegates to ``backend.main``.  Run with::

    python3 app.py            # equivalent to uvicorn backend.main:app
"""

from __future__ import annotations

from backend.main import app, run


def main() -> None:
    run()


if __name__ == "__main__":
    main()
