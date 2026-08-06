"""Upstream FastAPI entry point.

The old implementation is exposed as ``app`` for compatibility with the
SQLite migration utility and existing internal callers.  Runtime HTTP serving
is handled by ``backend.main``.
"""

from __future__ import annotations

import sys

from backend import legacy_runtime as _legacy
from backend.main import app as fastapi_app


# Keep ``import app`` compatible with existing scripts and test helpers.  The
# legacy module remains the source of the current domain behavior while its
# HTTP Handler is no longer used to serve requests.
_legacy.fastapi_app = fastapi_app
_legacy.app = fastapi_app
if __name__ != "__main__":
    sys.modules[__name__] = _legacy


def main() -> None:
    from backend.main import run

    run()


if __name__ == "__main__":
    main()
