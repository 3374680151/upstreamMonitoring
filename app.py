"""Upstream FastAPI entry point.

The old implementation is exposed as ``app`` for compatibility with the
SQLite migration utility and existing internal callers.  Runtime HTTP serving
is handled by ``backend.main``.
"""

from __future__ import annotations

if __name__ == "__main__":
    # The production process starts FastAPI directly and never needs to load
    # the compatibility runtime.
    from backend.main import run

    run()
else:
    import sys

    from backend import legacy_runtime as _legacy
    from backend.main import app as fastapi_app

    # Keep ``import app`` compatible with existing scripts and test helpers.
    # The old module is not part of the production HTTP call path.
    _legacy.fastapi_app = fastapi_app
    _legacy.app = fastapi_app
    sys.modules[__name__] = _legacy
