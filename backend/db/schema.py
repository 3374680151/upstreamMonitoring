"""MySQL schema bootstrap owned by the FastAPI database boundary."""

from __future__ import annotations

import time
from datetime import timedelta
from threading import RLock
from typing import Any

from backend.core.config import APP_DIR, DATA_DIR
from backend.core.time import app_now, utc_now_iso
from backend.db.adapter import execute, query_one
from backend.db.pool import connect_db
from backend.db.schema_ddl import (
    ADMIN_SITE_COLUMN_ADDITIONS,
    DDL_STATEMENTS,
    NOTIFICATION_COLUMN_ADDITIONS,
    SITES_COLUMN_ADDITIONS,
)


STATIC_DIR = APP_DIR / "static"
SUB2API_BROWSER_FIRST_MIGRATION = "2026-08-02-sub2api-browser-first"
_schema_lock = RLock()


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)


def _existing_columns(cursor: Any, table: str) -> set[str]:
    cursor.execute(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (table,),
    )
    return {row["COLUMN_NAME"] for row in cursor.fetchall()}


def _run_sub2api_browser_first_migration_once(cursor: Any) -> bool:
    cursor.execute(
        "SELECT name FROM app_schema_migrations WHERE name = %s",
        (SUB2API_BROWSER_FIRST_MIGRATION,),
    )
    if cursor.fetchone():
        return False
    cursor.execute(
        """
        UPDATE sites
        SET auth_mode = 'browser', login_enabled = 1
        WHERE platform = 'sub2api'
          AND auth_mode IN ('password', 'token')
        """
    )
    cursor.execute(
        """
        UPDATE sites
        SET session_sync_status = 'ready', session_sync_error = NULL
        WHERE platform = 'sub2api'
          AND auth_mode = 'browser'
          AND session_sync_status = 'not_requested'
          AND COALESCE(access_token, '') <> ''
        """
    )
    cursor.execute(
        "INSERT INTO app_schema_migrations (name, applied_at) VALUES (%s, %s)",
        (SUB2API_BROWSER_FIRST_MIGRATION, utc_now_iso()),
    )
    return True


def init() -> None:
    """Create missing tables and apply idempotent compatibility migrations."""
    with _schema_lock:
        conn = connect_db()
        try:
            with conn.cursor() as cursor:
                for statement in DDL_STATEMENTS:
                    cursor.execute(statement)

                site_columns = _existing_columns(cursor, "sites")
                for column_name, column_type in SITES_COLUMN_ADDITIONS.items():
                    if column_name not in site_columns:
                        cursor.execute(
                            f"ALTER TABLE sites ADD COLUMN {column_name} {column_type}"
                        )

                notification_columns = _existing_columns(
                    cursor, "notification_settings"
                )
                for column_name, column_type in NOTIFICATION_COLUMN_ADDITIONS.items():
                    if column_name not in notification_columns:
                        cursor.execute(
                            "ALTER TABLE notification_settings "
                            f"ADD COLUMN {column_name} {column_type}"
                        )

                admin_columns = _existing_columns(cursor, "admin_sites")
                for column_name, column_type in ADMIN_SITE_COLUMN_ADDITIONS.items():
                    if column_name not in admin_columns:
                        cursor.execute(
                            f"ALTER TABLE admin_sites ADD COLUMN {column_name} {column_type}"
                        )

                _run_sub2api_browser_first_migration_once(cursor)
                cursor.execute("SELECT id FROM notification_settings WHERE id = 1")
                if not cursor.fetchone():
                    now = utc_now_iso()
                    cursor.execute(
                        """
                        INSERT INTO notification_settings
                        (id, qq_enabled, qq_app_id, qq_client_secret, qq_group_openid,
                         qq_access_token, qq_token_expires_at, qq_last_error,
                         qq_last_sent_at, wecom_enabled, wecom_webhook,
                         wecom_last_error, wecom_last_sent_at, email_enabled,
                         smtp_host, smtp_port, smtp_username, smtp_password,
                         smtp_use_ssl, smtp_from, smtp_to, email_last_error,
                         email_last_sent_at, created_at, updated_at)
                        VALUES (1, 0, '', '', '', NULL, NULL, NULL, NULL, 0, '',
                                NULL, NULL, 0, '', 465, '', '', 1, '', '', NULL,
                                NULL, %s, %s)
                        """,
                        (now, now),
                    )
                cursor.execute(
                    "UPDATE notification_settings SET qq_enabled = 0, qq_last_error = NULL"
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def wait_for_db(max_wait: float = 60.0) -> None:
    """Wait for MySQL with bounded exponential backoff during startup."""
    deadline = time.monotonic() + max_wait
    delay = 1.0
    while True:
        try:
            conn = connect_db()
            conn.close()
            return
        except Exception as exc:
            if time.monotonic() >= deadline:
                print(f"[启动] 等待 MySQL 超时（{max_wait:.0f}s），放弃：{exc}")
                raise
            print(f"[启动] MySQL 尚未就绪，{delay:.0f}s 后重试… ({exc})")
            time.sleep(delay)
            delay = min(delay * 2, 5.0)


def seed_demo_data() -> None:
    """Create the historical demo site only when the caller opts in."""
    if query_one("SELECT id FROM sites LIMIT 1"):
        return
    now = utc_now_iso()
    next_check_at = (app_now() + timedelta(minutes=3)).isoformat(timespec="seconds")
    execute(
        """
        INSERT INTO sites
        (name, base_url, platform, enabled, interval_minutes, status, last_error,
         last_check_at, next_check_at, consecutive_failures, current_groups_json,
         created_at, updated_at)
        VALUES (?, ?, 'newapi', 1, 3, 'unknown', NULL, NULL, ?, 0, NULL, ?, ?)
        """,
        ("Demo NewAPI", "http://127.0.0.1:3000", next_check_at, now, now),
    )


__all__ = [
    "DDL_STATEMENTS",
    "ensure_dirs",
    "init",
    "seed_demo_data",
    "wait_for_db",
]
