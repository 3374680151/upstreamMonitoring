"""Schema bootstrap, forward-compatible column migrations, and DB readiness.

Moved out of ``backend.legacy_runtime`` alongside ``backend.db.connection`` so
the FastAPI lifespan can initialize the schema without importing the legacy
runtime.  The legacy runtime re-exports every public name below for backward
compatibility.
"""

from __future__ import annotations

import time
from typing import Any

from backend.core.config import APP_DIR, DATA_DIR
from backend.core.state import DB_LOCK
from backend.core.time import next_check_iso, utc_now_iso
from backend.db.connection import (
    _existing_columns,
    _q,
    connect_db,
    db_connection,
    db_execute,
    db_query_one,
)

STATIC_DIR = APP_DIR / "static"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)


DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS sites (
        id INT PRIMARY KEY AUTO_INCREMENT,
        name VARCHAR(255) NOT NULL,
        base_url VARCHAR(512) NOT NULL UNIQUE,
        platform VARCHAR(32) NOT NULL DEFAULT 'newapi',
        enabled TINYINT NOT NULL DEFAULT 1,
        interval_minutes INT NOT NULL DEFAULT 3,
        focus_keywords TEXT,
        login_enabled TINYINT NOT NULL DEFAULT 0,
        auth_mode VARCHAR(32) NOT NULL DEFAULT 'password',
        login_username VARCHAR(255),
        login_password TEXT,
        access_token TEXT,
        access_user_id VARCHAR(255),
        refresh_token TEXT,
        token_expires_at VARCHAR(40),
        status VARCHAR(32) NOT NULL DEFAULT 'unknown',
        last_error TEXT,
        last_check_at VARCHAR(40),
        next_check_at VARCHAR(40),
        consecutive_failures INT NOT NULL DEFAULT 0,
        auto_disabled TINYINT NOT NULL DEFAULT 0,
        current_groups_json LONGTEXT,
        current_login_groups_json LONGTEXT,
        login_last_error TEXT,
        login_last_check_at VARCHAR(40),
        session_sync_status VARCHAR(32) NOT NULL DEFAULT 'not_requested',
        session_sync_error TEXT,
        session_synced_at VARCHAR(40),
        browser_refresh_cookie TEXT,
        browser_cookie TEXT,
        browser_session_id VARCHAR(255),
        browser_access_expires_at BIGINT,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        KEY idx_sites_enabled_next_check (enabled, next_check_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS snapshots (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        site_id INT NOT NULL,
        status VARCHAR(32) NOT NULL,
        source VARCHAR(255) NOT NULL DEFAULT '/api/user/groups',
        groups_json LONGTEXT,
        raw_json LONGTEXT,
        hash VARCHAR(64),
        error_message TEXT,
        checked_at VARCHAR(40) NOT NULL,
        KEY idx_snapshots_site_checked (site_id, checked_at),
        CONSTRAINT fk_snapshots_site FOREIGN KEY (site_id)
            REFERENCES sites(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS changes (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        site_id INT NOT NULL,
        change_type VARCHAR(64) NOT NULL,
        group_name VARCHAR(255),
        old_value TEXT,
        new_value TEXT,
        change_percent DOUBLE,
        message TEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        acknowledged TINYINT NOT NULL DEFAULT 0,
        KEY idx_changes_site_created (site_id, created_at),
        CONSTRAINT fk_changes_site FOREIGN KEY (site_id)
            REFERENCES sites(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS notification_settings (
        id INT PRIMARY KEY,
        qq_enabled TINYINT NOT NULL DEFAULT 0,
        qq_app_id TEXT,
        qq_client_secret TEXT,
        qq_group_openid TEXT,
        qq_access_token TEXT,
        qq_token_expires_at VARCHAR(40),
        qq_last_error TEXT,
        qq_last_sent_at VARCHAR(40),
        wecom_enabled TINYINT NOT NULL DEFAULT 0,
        wecom_webhook TEXT,
        wecom_last_error TEXT,
        wecom_last_sent_at VARCHAR(40),
        email_enabled TINYINT NOT NULL DEFAULT 0,
        smtp_host VARCHAR(255),
        smtp_port INT NOT NULL DEFAULT 465,
        smtp_username VARCHAR(255),
        smtp_password TEXT,
        smtp_use_ssl TINYINT NOT NULL DEFAULT 1,
        smtp_from VARCHAR(255),
        smtp_to TEXT,
        email_last_error TEXT,
        email_last_sent_at VARCHAR(40),
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS notification_logs (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        channel VARCHAR(32) NOT NULL,
        status VARCHAR(32) NOT NULL,
        target TEXT,
        message TEXT,
        error_message TEXT,
        created_at VARCHAR(40) NOT NULL,
        KEY idx_notification_logs_created (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    # 管理站点：独立于监控 sites 的 NewAPI 后台（管理员令牌），用于渠道增删改。
    # 一个管理站点底下挂多个渠道；与监控站点解耦，互不影响。
    """
    CREATE TABLE IF NOT EXISTS admin_sites (
        id INT PRIMARY KEY AUTO_INCREMENT,
        name VARCHAR(255) NOT NULL,
        platform VARCHAR(32) NOT NULL DEFAULT 'newapi',
        base_url VARCHAR(512) NOT NULL,
        access_token TEXT,
        access_user_id VARCHAR(255),
        security_proof TEXT,
        security_proof_verified_at VARCHAR(40),
        login_username VARCHAR(255),
        login_password TEXT,
        browser_access_token TEXT,
        browser_refresh_cookie TEXT,
        browser_session_id VARCHAR(255),
        browser_access_expires_at BIGINT,
        browser_login_last_error TEXT,
        browser_login_last_check_at VARCHAR(40),
        sub2api_access_token TEXT,
        sub2api_refresh_token TEXT,
        sub2api_access_expires_at BIGINT,
        key_sync_enabled TINYINT NOT NULL DEFAULT 0,
        key_sync_interval_minutes INT NOT NULL DEFAULT 5,
        key_sync_last_at VARCHAR(40),
        key_sync_next_at VARCHAR(40),
        key_sync_last_error TEXT,
        key_sync_backoff_until VARCHAR(40),
        key_sync_failure_count INT NOT NULL DEFAULT 0,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS channel_upstream_bindings (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        admin_site_id INT NOT NULL,
        channel_id INT NOT NULL,
        upstream_base_url VARCHAR(512) NOT NULL DEFAULT '',
        upstream_platform VARCHAR(32) NOT NULL DEFAULT 'newapi',
        auth_mode VARCHAR(32) NOT NULL DEFAULT 'token',
        login_username VARCHAR(255),
        login_password TEXT,
        access_token TEXT,
        access_user_id VARCHAR(255),
        refresh_token TEXT,
        channel_key TEXT,
        match_status VARCHAR(32) NOT NULL DEFAULT 'unmatched',
        match_message TEXT,
        matched_groups_json LONGTEXT,
        matched_at VARCHAR(40),
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        UNIQUE KEY uq_channel_upstream_binding (admin_site_id, channel_id),
        KEY idx_channel_upstream_binding_site (admin_site_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    # 主站渠道明文 key 的本地缓存。NewAPI 的 key 读取接口要求短期 2FA proof，
    # 但渠道 key 本身通常不会变化；首次读取成功后，普通倍率查询直接复用这里的值。
    # 仅手动强制刷新才再次请求主站受保护接口。
    """
    CREATE TABLE IF NOT EXISTS admin_channel_keys (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        admin_site_id INT NOT NULL,
        channel_id INT NOT NULL,
        channel_key TEXT NOT NULL,
        fetched_at VARCHAR(40) NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        UNIQUE KEY uq_admin_channel_key (admin_site_id, channel_id),
        KEY idx_admin_channel_key_site (admin_site_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS browser_session_sync_requests (
        id VARCHAR(64) PRIMARY KEY,
        site_id INT,
        admin_site_id INT,
        platform VARCHAR(32) NOT NULL,
        target_origin VARCHAR(512) NOT NULL,
        secret_hash VARCHAR(64) NOT NULL,
        status VARCHAR(32) NOT NULL,
        error_code VARCHAR(64),
        error_message TEXT,
        expires_at VARCHAR(40) NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        consumed_at VARCHAR(40),
        KEY idx_browser_sync_site_created (site_id, created_at),
        KEY idx_browser_sync_admin_site_created (admin_site_id, created_at),
        CONSTRAINT fk_browser_sync_site FOREIGN KEY (site_id)
            REFERENCES sites(id) ON DELETE CASCADE,
        CONSTRAINT fk_browser_sync_admin_site FOREIGN KEY (admin_site_id)
            REFERENCES admin_sites(id) ON DELETE CASCADE,
        CONSTRAINT chk_browser_sync_one_target CHECK (
            (site_id IS NOT NULL) <> (admin_site_id IS NOT NULL)
        )
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    # A monitoring site can be discovered from more than one admin channel.
    # Keep that provenance separate from the existing sites row so importing a
    # candidate never overwrites hand-tuned names, intervals, or credentials.
    """
    CREATE TABLE IF NOT EXISTS site_discovery_links (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        site_id INT NOT NULL,
        admin_site_id INT NOT NULL,
        channel_id INT NOT NULL,
        upstream_base_url VARCHAR(512) NOT NULL,
        channel_name VARCHAR(255),
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        UNIQUE KEY uq_site_discovery_channel (site_id, admin_site_id, channel_id),
        KEY idx_site_discovery_site (site_id),
        KEY idx_site_discovery_admin_site (admin_site_id),
        CONSTRAINT fk_site_discovery_site FOREIGN KEY (site_id)
            REFERENCES sites(id) ON DELETE CASCADE,
        CONSTRAINT fk_site_discovery_admin_site FOREIGN KEY (admin_site_id)
            REFERENCES admin_sites(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    # 完整保存每个管理主站最近一次成功读取到的渠道和分组集合。
    # 这是同步对账的安全边界：只有渠道、分组两次读取都成功，才会替换快照
    # 并清理已消失的本地关联。
    """
    CREATE TABLE IF NOT EXISTS admin_site_sync_state (
        admin_site_id INT PRIMARY KEY,
        channels_json LONGTEXT NOT NULL,
        groups_json LONGTEXT NOT NULL,
        channels_hash VARCHAR(64) NOT NULL,
        groups_hash VARCHAR(64) NOT NULL,
        last_success_at VARCHAR(40),
        last_error TEXT,
        last_attempt_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        CONSTRAINT fk_admin_site_sync_state_admin_site FOREIGN KEY (admin_site_id)
            REFERENCES admin_sites(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS app_settings (
        name VARCHAR(128) PRIMARY KEY,
        value TEXT,
        updated_at VARCHAR(40) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS app_schema_migrations (
        name VARCHAR(128) PRIMARY KEY,
        applied_at VARCHAR(40) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]

# 前向兼容：库已存在但缺列时补齐（新库由上面的 CREATE 直接建全，这里为空转）。
SITES_COLUMN_ADDITIONS = {
    "focus_keywords": "TEXT",
    "login_enabled": "TINYINT NOT NULL DEFAULT 0",
    "auto_disabled": "TINYINT NOT NULL DEFAULT 0",
    "auth_mode": "VARCHAR(32) NOT NULL DEFAULT 'password'",
    "login_username": "VARCHAR(255)",
    "login_password": "TEXT",
    "access_token": "TEXT",
    "access_user_id": "VARCHAR(255)",
    "refresh_token": "TEXT",
    "token_expires_at": "VARCHAR(40)",
    "current_login_groups_json": "LONGTEXT",
    "login_last_error": "TEXT",
    "login_last_check_at": "VARCHAR(40)",
    "session_sync_status": "VARCHAR(32) NOT NULL DEFAULT 'not_requested'",
    "session_sync_error": "TEXT",
    "session_synced_at": "VARCHAR(40)",
    "browser_refresh_cookie": "TEXT",
    "browser_cookie": "TEXT",
    "browser_session_id": "VARCHAR(255)",
    "browser_access_expires_at": "BIGINT",
}
NOTIFICATION_COLUMN_ADDITIONS = {
    "email_enabled": "TINYINT NOT NULL DEFAULT 0",
    "wecom_enabled": "TINYINT NOT NULL DEFAULT 0",
    "wecom_webhook": "TEXT",
    "wecom_last_error": "TEXT",
    "wecom_last_sent_at": "VARCHAR(40)",
    "smtp_host": "VARCHAR(255)",
    "smtp_port": "INT NOT NULL DEFAULT 465",
    "smtp_username": "VARCHAR(255)",
    "smtp_password": "TEXT",
    "smtp_use_ssl": "TINYINT NOT NULL DEFAULT 1",
    "smtp_from": "VARCHAR(255)",
    "smtp_to": "TEXT",
    "email_last_error": "TEXT",
    "email_last_sent_at": "VARCHAR(40)",
}
ADMIN_SITE_COLUMN_ADDITIONS = {
    "platform": "VARCHAR(32) NOT NULL DEFAULT 'newapi'",
    "security_proof": "TEXT",
    "security_proof_verified_at": "VARCHAR(40)",
    "login_username": "VARCHAR(255)",
    "login_password": "TEXT",
    "browser_access_token": "TEXT",
    "browser_refresh_cookie": "TEXT",
    "browser_session_id": "VARCHAR(255)",
    "browser_access_expires_at": "BIGINT",
    "browser_login_last_error": "TEXT",
    "browser_login_last_check_at": "VARCHAR(40)",
    "sub2api_access_token": "TEXT",
    "sub2api_refresh_token": "TEXT",
    "sub2api_access_expires_at": "BIGINT",
    "key_sync_enabled": "TINYINT NOT NULL DEFAULT 0",
    "key_sync_interval_minutes": "INT NOT NULL DEFAULT 5",
    "key_sync_last_at": "VARCHAR(40)",
    "key_sync_next_at": "VARCHAR(40)",
    "key_sync_last_error": "TEXT",
    "key_sync_backoff_until": "VARCHAR(40)",
    "key_sync_failure_count": "INT NOT NULL DEFAULT 0",
}


SUB2API_BROWSER_FIRST_MIGRATION = "2026-08-02-sub2api-browser-first"


def migrate_sub2api_sites_to_browser_first(cursor: Any) -> None:
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


def run_sub2api_browser_first_migration_once(cursor: Any) -> bool:
    """Apply the legacy browser-first conversion once without overriding later edits."""
    cursor.execute(
        "SELECT name FROM app_schema_migrations WHERE name = %s",
        (SUB2API_BROWSER_FIRST_MIGRATION,),
    )
    if cursor.fetchone():
        return False
    migrate_sub2api_sites_to_browser_first(cursor)
    cursor.execute(
        "INSERT INTO app_schema_migrations (name, applied_at) VALUES (%s, %s)",
        (SUB2API_BROWSER_FIRST_MIGRATION, utc_now_iso()),
    )
    return True


def init_db() -> None:
    with DB_LOCK:
        conn = connect_db()
        try:
            with conn.cursor() as cur:
                for statement in DDL_STATEMENTS:
                    cur.execute(statement)

                site_columns = _existing_columns(cur, "sites")
                for column_name, column_type in SITES_COLUMN_ADDITIONS.items():
                    if column_name not in site_columns:
                        cur.execute(f"ALTER TABLE sites ADD COLUMN {column_name} {column_type}")

                setting_columns = _existing_columns(cur, "notification_settings")
                for column_name, column_type in NOTIFICATION_COLUMN_ADDITIONS.items():
                    if column_name not in setting_columns:
                        cur.execute(
                            f"ALTER TABLE notification_settings ADD COLUMN {column_name} {column_type}"
                        )

                admin_site_columns = _existing_columns(cur, "admin_sites")
                for column_name, column_type in ADMIN_SITE_COLUMN_ADDITIONS.items():
                    if column_name not in admin_site_columns:
                        cur.execute(f"ALTER TABLE admin_sites ADD COLUMN {column_name} {column_type}")

                run_sub2api_browser_first_migration_once(cur)

                # Local NewAPI monitoring uses a manually configured system
                # token + user ID.  Normalize legacy rows created by the old
                # site-browser-sync flow without touching tokens, snapshots,
                # groups, or change history.  NewAPI admin-site browser
                # sessions live in admin_sites and are intentionally kept.
                cur.execute(
                    """
                    UPDATE sites
                    SET auth_mode = 'token',
                        session_sync_status = 'not_requested',
                        session_sync_error = NULL,
                        session_synced_at = NULL
                    WHERE platform = 'newapi' AND auth_mode = 'browser'
                    """
                )

                cur.execute("SELECT id FROM notification_settings WHERE id = 1")
                if not cur.fetchone():
                    now = utc_now_iso()
                    cur.execute(
                        """
                        INSERT INTO notification_settings
                        (id, qq_enabled, qq_app_id, qq_client_secret, qq_group_openid, qq_access_token, qq_token_expires_at, qq_last_error, qq_last_sent_at, wecom_enabled, wecom_webhook, wecom_last_error, wecom_last_sent_at, email_enabled, smtp_host, smtp_port, smtp_username, smtp_password, smtp_use_ssl, smtp_from, smtp_to, email_last_error, email_last_sent_at, created_at, updated_at)
                        VALUES (1, 0, '', '', '', NULL, NULL, NULL, NULL, 0, '', NULL, NULL, 0, '', 465, '', '', 1, '', '', NULL, NULL, %s, %s)
                        """,
                        (now, now),
                    )
                cur.execute("UPDATE notification_settings SET qq_enabled = 0, qq_last_error = NULL")
            conn.commit()
        finally:
            conn.close()


def wait_for_db(max_wait: float = 60.0) -> None:
    """启动时等待 MySQL 就绪再建表。

    Docker 有 compose 的 depends_on: service_healthy 兜底；但裸机 / systemd 部署
    没有该保护，MySQL 稍慢就绪就会让进程在启动瞬间崩溃。这里做有上限的指数退避重试。
    """
    deadline = time.monotonic() + max_wait
    delay = 1.0
    while True:
        try:
            conn = connect_db()
            conn.close()
            return
        except Exception as exc:  # noqa: BLE001 - 启动期任何连接异常都重试
            if time.monotonic() >= deadline:
                print(f"[启动] 等待 MySQL 超时（{max_wait:.0f}s），放弃：{exc}")
                raise
            print(f"[启动] MySQL 尚未就绪，{delay:.0f}s 后重试… ({exc})")
            time.sleep(delay)
            delay = min(delay * 2, 5.0)


def init() -> None:
    init_db()


def bootstrap_demo_data() -> None:
    """SEED_DEMO=1 时插入一个 Demo NewAPI 站点。"""
    if db_query_one("SELECT id FROM sites LIMIT 1"):
        return

    now = utc_now_iso()
    db_execute(
        """
        INSERT INTO sites
        (name, base_url, platform, enabled, interval_minutes, status, last_error, last_check_at, next_check_at, consecutive_failures, current_groups_json, created_at, updated_at)
        VALUES (?, ?, 'newapi', 1, 3, 'unknown', NULL, NULL, ?, 0, NULL, ?, ?)
        """,
        (
            "Demo NewAPI",
            "http://127.0.0.1:3000",
            next_check_iso(3),
            now,
            now,
        ),
    )
