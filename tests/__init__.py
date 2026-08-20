"""Test-suite bootstrap.

Redirects every test run onto a dedicated scratch database (`upstream_test`)
so the test suite can NEVER write to the production `upstream` database.
On 2026-08-11 a test run wrote directly into the production DB and clobbered
the main-site config (admin_sites id=2 → main/main.example/admin).  This module
closes that hole: backend.legacy_runtime computes DB_CONFIG at import time, so
we must set DB_NAME *before* any `import app`/`import backend`.
"""

from __future__ import annotations

import os

os.environ.setdefault("DB_NAME", "upstream_test")

if os.environ["DB_NAME"] == "upstream":
    raise RuntimeError(
        "拒绝在测试环境下连接生产数据库 upstream：请用 DB_NAME=upstream_test 运行测试"
    )

_TEST_DB = os.environ["DB_NAME"]


def _load_env() -> None:
    """Minimal .env reader (setdefault, same semantics as the app's)."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as fh:
        for raw in fh.read().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _provision() -> None:
    import pymysql

    host = os.getenv("DB_HOST", "127.0.0.1")
    port = int(os.getenv("DB_PORT", "3306"))
    user = os.getenv("DB_USER", "root")
    password = os.getenv("DB_PASSWORD", "")

    conn = pymysql.connect(host=host, port=port, user=user, password=password, charset="utf8mb4")
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE DATABASE IF NOT EXISTS `%s` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                % _TEST_DB
            )
        conn.commit()
    finally:
        conn.close()

    # After this import, DB_CONFIG["database"] == upstream_test (DB_NAME already set).
    from backend import legacy_runtime as legacy

    legacy.init_db()


_load_env()
_provision()
