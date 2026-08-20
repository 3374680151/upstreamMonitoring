"""Schema declarations kept separate from bootstrap control flow."""

from __future__ import annotations


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
