"""Pytest isolation guard.

Never let pytest touch the production `upstream` database.  backend.legacy_runtime
bakes DB_NAME into DB_CONFIG at import time, so this module must run before any
`import backend`.  The app's own connection layer additionally fails closed
(see legacy_runtime.connect_db) if a test process still points at `upstream`.
"""

from __future__ import annotations

import os

os.environ.setdefault("DB_NAME", "upstream_test")

if os.environ["DB_NAME"] == "upstream":
    raise RuntimeError(
        "拒绝在测试环境下连接生产数据库 upstream：请用 DB_NAME=upstream_test 运行测试"
    )

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _isolated_test_database() -> None:
    """Provision the scratch database once per pytest session."""
    import pymysql

    host = os.getenv("DB_HOST", "127.0.0.1")
    port = int(os.getenv("DB_PORT", "3306"))
    user = os.getenv("DB_USER", "root")
    password = os.getenv("DB_PASSWORD", "")
    db_name = os.environ["DB_NAME"]

    conn = pymysql.connect(host=host, port=port, user=user, password=password, charset="utf8mb4")
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE DATABASE IF NOT EXISTS `%s` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                % db_name
            )
        conn.commit()
    finally:
        conn.close()

    from backend import legacy_runtime as legacy

    legacy.init_db()
    yield
    legacy.close_database_pool()
