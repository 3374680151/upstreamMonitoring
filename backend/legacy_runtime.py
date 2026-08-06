from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import re
import secrets
import smtplib
import subprocess
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request

import pymysql
from pymysql.cursors import DictCursor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Empty, LifoQueue
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, quote, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


# This module is kept as a compatibility domain runtime while the FastAPI
# boundary is migrated.  Resolve paths from the repository root, not from the
# backend package directory.
APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = APP_DIR / "data"
STATIC_DIR = APP_DIR / "static"
WEB_DIST_DIR = APP_DIR / "apps" / "web" / "dist"
DB_PATH = DATA_DIR / "app.db"  # 旧 SQLite 路径，仅供一次性迁移工具引用


def _load_dotenv() -> None:
    """把本地 .env（已 gitignore）的 KEY=VALUE 读入环境变量。

    目的：数据库密码 / SMTP / 令牌等密钥只留在本地 .env，不进源码、不进 git，
    开源时不会泄露。已存在的环境变量优先，不会被覆盖。
    """
    env_path = APP_DIR / ".env"
    if not env_path.exists():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    except OSError:
        pass


_load_dotenv()


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))

# 数据库连接（MySQL）：全部走环境变量 / .env，密码严禁写死在源码里。
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "upstream"),
}

DEFAULT_INTERVAL_MINUTES = 3
MIN_INTERVAL_MINUTES = 1
DB_POOL_SIZE = _env_int("DB_POOL_SIZE", 8, 1, 32)
DB_POOL_ACQUIRE_TIMEOUT_SECONDS = _env_int(
    "DB_POOL_ACQUIRE_TIMEOUT", 5, 1, 60
)
DB_CONNECT_TIMEOUT_SECONDS = _env_int("DB_CONNECT_TIMEOUT", 5, 1, 60)
DB_READ_TIMEOUT_SECONDS = _env_int("DB_READ_TIMEOUT", 15, 1, 300)
DB_WRITE_TIMEOUT_SECONDS = _env_int("DB_WRITE_TIMEOUT", 15, 1, 300)
HTTP_TIMEOUT_SECONDS = _env_int("UPSTREAM_HTTP_TIMEOUT", 15, 1, 120)
SLOW_REQUEST_THRESHOLD_MS = _env_int(
    "SLOW_REQUEST_THRESHOLD_MS", 500, 0, 60000
)
SCAN_INTERVAL_SECONDS = 10
ADMIN_KEY_SYNC_INTERVAL_SECONDS = _env_int(
    "ADMIN_KEY_SYNC_INTERVAL_SECONDS", 180, 60, 3600
)
SERVER_HOST = os.getenv("HOST", "127.0.0.1")
SERVER_PORT = int(os.getenv("PORT", "8000"))
# 控制台登录密码：留空则不启用鉴权（本地/内网直连场景）。设置后所有 /api/* 需先登录。
CONSOLE_PASSWORD = (os.getenv("CONSOLE_PASSWORD") or "").strip()
try:
    CONSOLE_SESSION_TTL_SECONDS = max(300, int(os.getenv("CONSOLE_SESSION_TTL") or "604800"))
except ValueError:
    CONSOLE_SESSION_TTL_SECONDS = 604800  # 7 天
CONSOLE_SESSIONS: Dict[str, float] = {}
CONSOLE_SESSIONS_LOCK = threading.RLock()
APP_TIMEZONE_NAME = os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Asia/Shanghai"
try:
    APP_TIMEZONE = ZoneInfo(APP_TIMEZONE_NAME)
except ZoneInfoNotFoundError:
    APP_TIMEZONE_NAME = "Asia/Shanghai"
    APP_TIMEZONE = timezone(timedelta(hours=8), APP_TIMEZONE_NAME)

DB_LOCK = threading.RLock()
STOP_EVENT = threading.Event()
MODEL_CACHE_TTL_SECONDS = 90
UPTIME_CACHE_TTL_SECONDS = 300
MODEL_DATA_CACHE: Dict[int, Dict[str, Any]] = {}
MODEL_CACHE_REFRESHING: set[int] = set()
MODEL_CACHE_LOCK = threading.RLock()
NEWAPI_UPTIME_CACHE: Dict[str, Dict[str, Any]] = {}
NEWAPI_UPTIME_REFRESHING: set[str] = set()
# NewAPI 用户侧 API 密钥列表（/api/token/）缓存。渠道页会按多个主站渠道
# 连续匹配同一个上游账号，短期复用列表可避免重复分页请求。
NEWAPI_USER_TOKEN_LIST_CACHE: Dict[str, Dict[str, Any]] = {}
NEWAPI_USER_TOKEN_LIST_LOCK = threading.RLock()
NEWAPI_USER_TOKEN_LIST_CACHE_TTL_SECONDS = 15
# NewAPI 对 POST /api/channel/:id/key 通常有更严格的频控；渠道页不能并发轰炸
# 主站。所有主站 key 读取在进程内按站点串行，并留出最小间隔。
MAIN_CHANNEL_KEY_REQUEST_LOCK = threading.RLock()
MAIN_CHANNEL_KEY_LAST_REQUEST_AT: Dict[str, float] = {}
MAIN_CHANNEL_KEY_RATE_LIMIT_UNTIL: Dict[str, float] = {}
MAIN_CHANNEL_KEY_MIN_INTERVAL_SECONDS = 2.0
MAIN_CHANNEL_KEY_RATE_LIMIT_COOLDOWN_SECONDS = 30.0
# 主站 key 在同一页面刷新周期内不会变化。短期缓存可避免 React 开发模式重复加载、
# 页面重绘或多个调用方重复读取同一个渠道时再次触发主站的保护接口限流。
MAIN_CHANNEL_KEY_CACHE: Dict[str, Dict[str, Any]] = {}
MAIN_CHANNEL_KEY_CACHE_TTL_SECONDS = 60
# Refresh tokens rotate on every successful dashboard refresh. Serialize by
# admin site so concurrent channel reads cannot race the same refresh cookie.
ADMIN_BROWSER_SESSION_LOCKS: Dict[int, threading.RLock] = {}
ADMIN_BROWSER_SESSION_LOCKS_GUARD = threading.RLock()
# 普通监控站点与管理站的 refresh cookie 独立轮换，不能共用锁命名空间。
NEWAPI_SITE_BROWSER_SESSION_LOCKS: Dict[int, threading.RLock] = {}
NEWAPI_SITE_BROWSER_SESSION_LOCKS_GUARD = threading.RLock()
# sub2api 管理端 JWT 与普通监控站点登录态分开保存，并按主站串行轮换。
ADMIN_SUB2API_SESSION_LOCKS: Dict[int, threading.RLock] = {}
ADMIN_SUB2API_SESSION_LOCKS_GUARD = threading.RLock()
ADMIN_SUB2API_EXPIRY_SKEW_SECONDS = 60
# sub2api refresh token 也可能轮换；同一个上游站点的并发请求必须串行刷新，
# 并在短时间内复用同一轮刷新结果，避免第二个请求继续使用已轮换的旧 refresh_token。
SUB2API_REFRESH_LOCKS: Dict[str, threading.RLock] = {}
SUB2API_REFRESH_LOCKS_GUARD = threading.RLock()
SUB2API_REFRESH_CACHE: Dict[str, Dict[str, Any]] = {}
SUB2API_REFRESH_CACHE_TTL_SECONDS = 30.0
# A refresh-token lock is not enough for a monitor request: a browser sync,
# a scheduled check, and a manual check can all update the same site row.  Keep
# the complete credential decision (reload -> request -> conditional write)
# serial for each ordinary sub2api site.
SUB2API_SITE_AUTH_LOCKS: Dict[int, threading.RLock] = {}
SUB2API_SITE_AUTH_LOCKS_GUARD = threading.RLock()
BROWSER_AUTH_MODE = "browser"
SESSION_SYNC_TTL_SECONDS = 60
SESSION_SYNC_TERMINAL_STATUSES = {
    "ready",
    "no_session",
    "expired",
    "permission_required",
    "extension_unavailable",
    "failed",
}
SESSION_SYNC_PAGE_FAILURES = {
    "EXTENSION_UNAVAILABLE": (
        "extension_unavailable",
        "未安装或未连接浏览器同步扩展",
    ),
    "ORIGIN_PERMISSION_REQUIRED": (
        "permission_required",
        "扩展需要该站点的读取权限",
    ),
    "COOKIE_PERMISSION_REQUIRED": (
        "permission_required",
        "扩展需要读取 NewAPI 登录 Cookie 的权限，请允许后重新同步",
    ),
    "SYNC_FAILED": ("failed", "登录态同步失败"),
}
SESSION_SYNC_MAX_BODY_BYTES = 40 * 1024
SESSION_SYNC_MAX_TOKEN_LENGTH = 16 * 1024
SESSION_SYNC_REQUEST_LOCK = threading.RLock()

# Discovery imports are intentionally bounded so a malformed client cannot
# create an unbounded number of monitoring sites in one request.
MAX_DISCOVERY_IMPORT_ITEMS = 100
MAX_DISCOVERY_CHANNEL_IDS_PER_ITEM = 1000
MAX_DISCOVERY_INTERVAL_MINUTES = 1440


def app_now() -> datetime:
    return datetime.now(APP_TIMEZONE)


def utc_now_iso() -> str:
    return app_now().isoformat(timespec="seconds")


def parse_iso_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def normalize_base_url(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    if not value:
        return value
    return value


def _normalize_discovery_base_url(value: Any) -> Tuple[str, Optional[str]]:
    """Normalize and validate a URL supplied by the channel discovery flow.

    Discovery URLs originate from an upstream channel list, but import requests
    are still treated as untrusted input.  Keep the existing normalization
    function as the canonical representation and reject credentials or schemes
    that the browser/session bridge cannot safely handle.
    """
    normalized = normalize_base_url(str(value or ""))
    if not normalized:
        return "", "base_url required"
    try:
        parsed = urlparse(normalized)
        # Accessing .port validates malformed/out-of-range port values.
        parsed.port
    except (TypeError, ValueError):
        return "", "base_url invalid"
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or not parsed.hostname
    ):
        return "", "base_url must use http or https"
    if parsed.username or parsed.password:
        return "", "base_url must not include credentials"
    return normalized, None


def _safe_discovery_display_url(value: Any) -> str:
    """Return a URL suitable for an error row without exposing userinfo."""
    text = normalize_base_url(str(value or ""))
    if not text:
        return ""
    try:
        parsed = urlparse(text)
        if parsed.username or parsed.password:
            scheme = parsed.scheme.lower()
            hostname = parsed.hostname or ""
            if not hostname:
                return ""
            try:
                port = f":{parsed.port}" if parsed.port else ""
            except (TypeError, ValueError):
                port = ""
            return f"{scheme}://{hostname}{port}{parsed.path or ''}".rstrip("/")
    except (TypeError, ValueError):
        return ""
    return text[:512]


def aggregate_newapi_channel_candidates(
    channels: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Group NewAPI channels by normalized, safe upstream Base URL.

    Dict insertion order preserves the order in which the upstream pagination
    returned the first channel for each URL.  Source IDs and names are kept in
    stable order for display and for the discovery-link write.
    """
    grouped: Dict[str, Dict[str, Any]] = {}
    for channel in channels or []:
        if not isinstance(channel, dict):
            continue
        channel_id = _positive_channel_id(channel.get("id"))
        if channel_id is None:
            continue
        base_url, _error = _normalize_discovery_base_url(channel.get("base_url"))
        if not base_url:
            continue
        item = grouped.setdefault(
            base_url,
            {
                "base_url": base_url,
                "name": "",
                "channel_ids": [],
                "channel_names": [],
            },
        )
        channel_name = str(channel.get("name") or "").strip()
        if channel_name and not item["name"]:
            item["name"] = channel_name
        if channel_id not in item["channel_ids"]:
            item["channel_ids"].append(channel_id)
            # Keep this list index-aligned with channel_ids.  The old code
            # omitted empty names, which could associate the next channel's
            # name with the wrong channel during import.
            item["channel_names"].append(channel_name)
        elif channel_name:
            index = item["channel_ids"].index(channel_id)
            if not item["channel_names"][index]:
                item["channel_names"][index] = channel_name

    result: List[Dict[str, Any]] = []
    for item in grouped.values():
        candidate = dict(item)
        candidate["name"] = candidate["name"] or candidate["base_url"]
        candidate["channel_count"] = len(candidate["channel_ids"])
        result.append(candidate)
    return result


def _positive_channel_id(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        channel_id = int(value)
    except (TypeError, ValueError):
        return None
    return channel_id if channel_id > 0 else None


SYNC_SECRET_FIELD_NAMES = {
    "access_token",
    "browser_access_token",
    "browser_cookie",
    "browser_refresh_cookie",
    "channel_key",
    "key",
    "login_password",
    "password",
    "refresh_token",
    "secret",
    "client_secret",
    "security_proof",
    "token",
}


def _sync_safe_value(value: Any, field_name: str = "", depth: int = 0) -> Any:
    """Copy upstream metadata while excluding credential-bearing fields."""
    normalized_name = str(field_name or "").strip().lower()
    if (
        normalized_name in SYNC_SECRET_FIELD_NAMES
        or normalized_name.endswith("_token")
        or normalized_name.endswith("_password")
        or normalized_name.endswith("_cookie")
        or normalized_name.endswith("_secret")
    ):
        return None
    if depth > 8:
        return str(value)
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            normalized_key = key.strip().lower()
            if (
                normalized_key in SYNC_SECRET_FIELD_NAMES
                or normalized_key.endswith("_token")
                or normalized_key.endswith("_password")
                or normalized_key.endswith("_cookie")
                or normalized_key.endswith("_secret")
            ):
                continue
            result[key] = _sync_safe_value(raw_value, key, depth + 1)
        return result
    if isinstance(value, list):
        return [_sync_safe_value(item, "", depth + 1) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def normalize_admin_sync_channels(
    channels: Any,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Normalize a complete admin channel list for identity and snapshots."""
    if not isinstance(channels, list):
        return [], "主站渠道响应不是完整列表"
    by_id: Dict[int, Dict[str, Any]] = {}
    for channel in channels:
        if not isinstance(channel, dict):
            return [], "主站渠道列表包含无效项"
        channel_id = _positive_channel_id(channel.get("id"))
        if channel_id is None:
            return [], "主站渠道列表包含缺少 ID 的渠道"
        safe_channel = _sync_safe_value(channel)
        if not isinstance(safe_channel, dict):
            safe_channel = {}
        safe_channel["id"] = channel_id
        if channel.get("base_url"):
            safe_channel["base_url"] = normalize_base_url(
                str(channel.get("base_url") or "")
            )
        by_id[channel_id] = safe_channel
    return [by_id[channel_id] for channel_id in sorted(by_id)], None


def normalize_admin_sync_groups(
    groups_payload: Any,
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Normalize the exact group map returned by an admin-site endpoint."""
    if not isinstance(groups_payload, dict):
        return {}, "主站分组响应无效"
    data = groups_payload.get("data")
    if not isinstance(data, dict):
        return {}, "主站分组响应不是完整列表"
    safe_groups: Dict[str, Any] = {}
    for raw_name, raw_group in data.items():
        name = str(raw_name)
        safe_groups[name] = _sync_safe_value(raw_group)
    return safe_groups, None


def enrich_channel_candidates_with_sites(
    candidates: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Attach a redacted local-site status to each discovery candidate.

    Only the columns needed for status mapping are selected and only public
    fields are copied to the returned object.  Credentials from either the
    database row or a malformed caller-supplied candidate never reach the UI.
    """
    rows = db_query_all(
        "SELECT id, base_url, platform, status, auth_mode, enabled, "
        "session_sync_status "
        "FROM sites WHERE platform = 'newapi'"
    )
    by_url: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("platform") or "newapi").strip().lower() != "newapi":
            continue
        normalized, _error = _normalize_discovery_base_url(row.get("base_url"))
        if normalized:
            by_url.setdefault(normalized, row)

    safe_keys = (
        "base_url",
        "name",
        "channel_ids",
        "channel_names",
        "channel_count",
    )
    enriched: List[Dict[str, Any]] = []
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        safe_candidate = {
            key: candidate[key] for key in safe_keys if key in candidate
        }
        base_url, _error = _normalize_discovery_base_url(
            safe_candidate.get("base_url")
        )
        if base_url:
            safe_candidate["base_url"] = base_url
        row = by_url.get(base_url) if base_url else None
        if row:
            safe_candidate["existing_site_id"] = row.get("id")
            # NewAPI local monitoring never inherits a browser session.  Keep
            # discovery results token-oriented even for legacy rows that were
            # created by the old browser-sync flow.
            safe_candidate["existing_site_auth_mode"] = "token"
            safe_candidate["existing_site_enabled"] = bool(row.get("enabled", True))
            safe_candidate["existing_site_session_sync_status"] = "not_requested"
            safe_candidate["existing_site_status"] = row.get("status") or "unknown"
        else:
            safe_candidate["existing_site_id"] = None
            safe_candidate["existing_site_status"] = None
            safe_candidate["existing_site_auth_mode"] = None
            safe_candidate["existing_site_enabled"] = None
            safe_candidate["existing_site_session_sync_status"] = None
        safe_candidate["importable"] = True
        enriched.append(safe_candidate)
    return enriched


def normalize_session_expiry(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        epoch_value = float(text)
    except (TypeError, ValueError):
        epoch_value = -1
    if epoch_value >= 0:
        if epoch_value >= 100_000_000_000:
            epoch_value /= 1000
        try:
            return datetime.fromtimestamp(epoch_value, tz=timezone.utc).astimezone(
                APP_TIMEZONE
            ).isoformat(timespec="seconds")
        except (OverflowError, OSError, ValueError):
            return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        return ""
    return parsed.isoformat(timespec="seconds")


def site_origin(base_url: str) -> str:
    try:
        parsed = urlparse(str(base_url or "").strip())
        parsed.port
    except (TypeError, ValueError):
        return ""
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)


def connect_db() -> pymysql.connections.Connection:
    """新建一个可由连接池独占租用的 MySQL 连接。"""
    return pymysql.connect(
        host=DB_CONFIG["host"],
        port=DB_CONFIG["port"],
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"],
        database=DB_CONFIG["database"],
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
        connect_timeout=DB_CONNECT_TIMEOUT_SECONDS,
        read_timeout=DB_READ_TIMEOUT_SECONDS,
        write_timeout=DB_WRITE_TIMEOUT_SECONDS,
    )


class DatabasePoolTimeoutError(TimeoutError):
    """连接池在限定时间内没有可用连接。"""


class DatabaseConnectionPool:
    """有界、惰性创建的进程内 MySQL 连接池。"""

    def __init__(self, connection_factory, size: int, acquire_timeout: float):
        self._connection_factory = connection_factory
        self._acquire_timeout = acquire_timeout
        self._state_lock = threading.Lock()
        self._closed = False
        self._slots = LifoQueue(maxsize=size)
        for _ in range(size):
            self._slots.put(None)

    @contextmanager
    def connection(self):
        try:
            connection = self._slots.get(timeout=self._acquire_timeout)
        except Empty as exc:
            raise DatabasePoolTimeoutError("数据库连接池繁忙，请稍后重试") from exc
        try:
            if connection is not None:
                try:
                    connection.ping(reconnect=False)
                except Exception:
                    try:
                        connection.close()
                    except Exception:
                        pass
                    connection = None
            if connection is None:
                connection = self._connection_factory()
            yield connection
        finally:
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    try:
                        connection.close()
                    except Exception:
                        pass
                    connection = None
            with self._state_lock:
                if self._closed:
                    if connection is not None:
                        try:
                            connection.close()
                        except Exception:
                            pass
                else:
                    self._slots.put(connection)

    def close(self) -> None:
        with self._state_lock:
            self._closed = True
            while True:
                try:
                    connection = self._slots.get_nowait()
                except Empty:
                    break
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass


DB_POOL = DatabaseConnectionPool(
    lambda: connect_db(),
    size=DB_POOL_SIZE,
    acquire_timeout=DB_POOL_ACQUIRE_TIMEOUT_SECONDS,
)


@contextmanager
def db_connection():
    with DB_POOL.connection() as connection:
        yield connection


def close_database_pool() -> None:
    DB_POOL.close()


def _q(sql: str) -> str:
    """把 SQLite 风格的 ? 占位符转成 PyMySQL 的 %s。

    PyMySQL 在带参数时执行 `query % args`，故 SQL 模板里任何字面量 % 都必须先转义成
    %%，否则 LIKE '%x%'、DATE_FORMAT('%Y') 之类会在运行时抛格式化错误（不是清晰的 SQL 错误）。
    必须「先转义 % 再替换 ?」，顺序不能反（反了会把 %s 变成 %%s）。
    注意：本函数无法处理「SQL 字符串字面量里出现的裸 ?」，请勿在 SQL 中写占位符以外的 ?。
    """
    return sql.replace("%", "%%").replace("?", "%s")


def _existing_columns(cur: Any, table: str) -> set:
    cur.execute(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (table,),
    )
    return {row["COLUMN_NAME"] for row in cur.fetchall()}


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


def dict_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    # DictCursor 已返回 dict，这里保留函数以兼容旧调用点。
    return dict(row)


def db_query_all(
    sql: str,
    params: Iterable[Any] = (),
    connection: Optional[pymysql.connections.Connection] = None,
) -> List[Dict[str, Any]]:
    if connection is None:
        with db_connection() as leased:
            return db_query_all(sql, params, connection=leased)
    with connection.cursor() as cur:
        cur.execute(_q(sql), tuple(params))
        return [dict(row) for row in cur.fetchall()]


def db_query_one(
    sql: str,
    params: Iterable[Any] = (),
    connection: Optional[pymysql.connections.Connection] = None,
) -> Optional[Dict[str, Any]]:
    if connection is None:
        with db_connection() as leased:
            return db_query_one(sql, params, connection=leased)
    with connection.cursor() as cur:
        cur.execute(_q(sql), tuple(params))
        row = cur.fetchone()
        return dict(row) if row else None


def db_execute(sql: str, params: Iterable[Any] = ()) -> int:
    with db_connection() as connection:
        try:
            with connection.cursor() as cur:
                cur.execute(_q(sql), tuple(params))
                lastrowid = cur.lastrowid
            connection.commit()
            return lastrowid
        except Exception:
            try:
                connection.rollback()
            except Exception:
                pass
            raise


def db_execute_rowcount(sql: str, params: Iterable[Any] = ()) -> int:
    """Execute a write and return affected rows for compare-and-swap updates."""
    with db_connection() as connection:
        try:
            with connection.cursor() as cur:
                cur.execute(_q(sql), tuple(params))
                rowcount = int(cur.rowcount or 0)
            connection.commit()
            return rowcount
        except Exception:
            try:
                connection.rollback()
            except Exception:
                pass
            raise


def hash_session_sync_secret(secret: str) -> str:
    return hashlib.sha256(str(secret or "").encode("utf-8")).hexdigest()


def _session_sync_request_expired(row: Dict[str, Any]) -> bool:
    expires_at = parse_iso_dt(str(row.get("expires_at") or ""))
    if not expires_at:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=APP_TIMEZONE)
    return expires_at <= app_now()


def session_sync_target_kind(row: Dict[str, Any]) -> str:
    has_site = row.get("site_id") is not None
    has_admin_site = row.get("admin_site_id") is not None
    if has_site == has_admin_site:
        return ""
    return "site" if has_site else "admin_site"


def _site_session_sync_request_error(
    site_id: int,
    request_id: str,
    expected_origin: str,
    platform: str,
) -> Optional[str]:
    """Return a safe error when a claimed site sync is no longer current.

    A browser completion can spend several seconds validating the upstream
    session.  During that time the user may start a replacement sync or edit
    the monitor into a different authentication mode.  Re-read both records
    immediately before a credential write so an older completion cannot win
    that race.
    """
    normalized_platform = str(platform or "").strip().lower()
    normalized_origin = site_origin(expected_origin)
    if not request_id or not normalized_origin or normalized_platform != "sub2api":
        return "同步请求无效，请重新发起同步"

    # Keep this target lookup before the request lookup.  Apart from producing
    # the clearest failure for a deleted/edited site, it also avoids touching a
    # stale request when its target no longer accepts browser credentials.
    target = db_query_one(
        "SELECT id, base_url, platform, auth_mode FROM sites WHERE id = ?",
        (int(site_id),),
    )
    if (
        not target
        or str(target.get("platform") or "").strip().lower()
        != normalized_platform
        or str(target.get("auth_mode") or "").strip().lower()
        != BROWSER_AUTH_MODE
        or site_origin(str(target.get("base_url") or "")) != normalized_origin
    ):
        return "同步目标已变更，请重新发起同步"

    request = db_query_one(
        """
        SELECT id, site_id, admin_site_id, platform, target_origin, status
        FROM browser_session_sync_requests
        WHERE id = ? AND site_id = ? AND admin_site_id IS NULL
        """,
        (str(request_id), int(site_id)),
    )
    if (
        not request
        or str(request.get("status") or "") != "validating"
        or str(request.get("platform") or "").strip().lower()
        != normalized_platform
        or str(request.get("target_origin") or "") != normalized_origin
    ):
        return "同步请求已失效，请重新发起同步"
    return None


def _newapi_session_sync_request_error(
    target_kind: str,
    target_id: int,
    request_id: str,
    expected_origin: str,
) -> Optional[str]:
    """Return a safe error when a claimed NewAPI sync is no longer current.

    The sync request must still be in ``validating`` state so an older
    completion cannot race a newer replacement request.
    """
    normalized_origin = site_origin(expected_origin)
    if (
        not request_id
        or not normalized_origin
        or target_kind != "site"
    ):
        return "同步请求无效，请重新发起同步"

    target = db_query_one(
        "SELECT id, base_url, platform, auth_mode FROM sites WHERE id = ?",
        (int(target_id),),
    )
    if (
        not target
        or str(target.get("platform") or "newapi").strip().lower() != "newapi"
        or str(target.get("auth_mode") or "").strip().lower() != BROWSER_AUTH_MODE
        or site_origin(str(target.get("base_url") or "")) != normalized_origin
    ):
        return "同步目标已变更，请重新发起同步"
    request = db_query_one(
        """
        SELECT id, site_id, admin_site_id, platform, target_origin, status
        FROM browser_session_sync_requests
        WHERE id = ? AND site_id = ? AND admin_site_id IS NULL
        """,
        (str(request_id), int(target_id)),
    )

    if (
        not request
        or str(request.get("status") or "") != "validating"
        or str(request.get("platform") or "").strip().lower() != "newapi"
        or str(request.get("target_origin") or "") != normalized_origin
    ):
        return "同步请求已失效，请重新发起同步"
    return None


def persist_newapi_site_browser_session_cas(
    site_id: int,
    session: Dict[str, Any],
    request_id: str,
    expected_origin: str,
) -> bool:
    """CAS write of a NewAPI regular-site browser session.

    Only updates ``sites`` rows that still match the validating sync request,
    so a newer replacement request or a manual auth-mode change cannot be
    clobbered by an older completion.  Returns True only when the row was
    actually updated.
    """
    origin = site_origin(expected_origin)
    if not origin or not request_id:
        return False
    try:
        expires_at = int(session.get("browser_access_expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0
    access_token = str(session.get("access_token") or "").strip()
    access_user_id = str(session.get("access_user_id") or "").strip()
    browser_cookie = str(session.get("browser_cookie") or "").strip() or None
    refresh_cookie = str(session.get("browser_refresh_cookie") or "").strip() or None
    session_id = str(session.get("browser_session_id") or "").strip() or None
    now = utc_now_iso()
    return db_execute_rowcount(
        """
        UPDATE sites AS s
        SET auth_mode = 'browser', login_enabled = 1,
            login_username = NULL, login_password = NULL,
            access_token = ?, access_user_id = ?,
            browser_cookie = ?, browser_refresh_cookie = ?, browser_session_id = ?,
            browser_access_expires_at = ?,
            session_sync_status = 'ready', session_sync_error = NULL,
            session_synced_at = ?, updated_at = ?
        WHERE s.id = ?
          AND s.platform = 'newapi'
          AND s.auth_mode = 'browser'
          AND EXISTS (
              SELECT 1
              FROM browser_session_sync_requests AS r
              WHERE r.id = ?
                AND r.site_id = s.id
                AND r.admin_site_id IS NULL
                AND r.platform = 'newapi'
                AND r.target_origin = ?
                AND r.status = 'validating'
          )
        """,
        (
            access_token,
            access_user_id,
            browser_cookie,
            refresh_cookie,
            session_id,
            expires_at,
            now,
            now,
            int(site_id),
            str(request_id),
            origin,
        ),
    ) > 0


def mark_newapi_site_browser_session_expired_cas(
    site_id: int,
    message: str,
    request_id: str,
    expected_origin: str,
) -> bool:
    """CAS write that marks a NewAPI regular-site sync as expired."""
    origin = site_origin(expected_origin)
    if not origin or not request_id:
        return False
    now = utc_now_iso()
    return db_execute_rowcount(
        """
        UPDATE sites AS s
        SET session_sync_status = 'expired', session_sync_error = ?, updated_at = ?
        WHERE s.id = ?
          AND s.platform = 'newapi'
          AND s.auth_mode = 'browser'
          AND EXISTS (
              SELECT 1
              FROM browser_session_sync_requests AS r
              WHERE r.id = ?
                AND r.site_id = s.id
                AND r.admin_site_id IS NULL
                AND r.platform = 'newapi'
                AND r.target_origin = ?
                AND r.status = 'validating'
          )
        """,
        (
            str(message or "登录态已过期，请重新登录"),
            now,
            int(site_id),
            str(request_id),
            origin,
        ),
    ) > 0


def _session_sync_public_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "request_id": str(row.get("id") or ""),
        "target_kind": session_sync_target_kind(row),
        "status": str(row.get("status") or "failed"),
        "platform": str(row.get("platform") or ""),
        "target_origin": str(row.get("target_origin") or ""),
        "error_code": str(row.get("error_code") or ""),
        "message": str(row.get("error_message") or ""),
        "expires_at": str(row.get("expires_at") or ""),
        "updated_at": str(row.get("updated_at") or ""),
        "consumed_at": str(row.get("consumed_at") or ""),
    }


def _create_session_sync_request(
    site_id: int, target: Dict[str, Any]
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    platform = str(target.get("platform") or "newapi").strip().lower()
    if platform not in {"sub2api", "newapi"}:
        return False, {}, "当前平台不支持浏览器登录态同步"
    if str(target.get("auth_mode") or "").strip().lower() != BROWSER_AUTH_MODE:
        return False, {}, "请先将渠道认证方式切换为浏览器登录态"
    origin = site_origin(str(target.get("base_url") or ""))
    if not origin:
        return False, {}, "渠道 Base URL 无效"

    request_id = secrets.token_urlsafe(24)
    secret = secrets.token_urlsafe(32)
    now_dt = app_now()
    now = now_dt.isoformat(timespec="seconds")
    expires_at = (now_dt + timedelta(seconds=SESSION_SYNC_TTL_SECONDS)).isoformat(
        timespec="seconds"
    )
    with SESSION_SYNC_REQUEST_LOCK:
        db_execute(
            """
            UPDATE browser_session_sync_requests
            SET status = 'expired', error_code = 'REPLACED',
                error_message = '已创建新的同步请求', updated_at = ?
            WHERE status IN ('pending', 'validating') AND site_id = ?
              AND admin_site_id IS NULL
            """,
            (now, int(site_id)),
        )
        db_execute(
            """
            INSERT INTO browser_session_sync_requests
            (id, site_id, admin_site_id, platform, target_origin, secret_hash,
             status, error_code, error_message, expires_at, created_at, updated_at,
             consumed_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, ?, ?, ?, NULL)
            """,
            (
                request_id,
                int(site_id),
                None,
                platform,
                origin,
                hash_session_sync_secret(secret),
                expires_at,
                now,
                now,
            ),
        )
        db_execute(
            """
            UPDATE sites
            SET session_sync_status = 'pending', session_sync_error = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (now, int(site_id)),
        )
    return True, {
        "request_id": request_id,
        "secret": secret,
        "platform": platform,
        "target_kind": "site",
        "target_origin": origin,
        "expires_in": SESSION_SYNC_TTL_SECONDS,
    }, None


def create_site_session_sync_request(
    site_id: int,
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    site = db_query_one("SELECT * FROM sites WHERE id = ?", (int(site_id),))
    if not site:
        return False, {}, "渠道不存在"
    return _create_session_sync_request(int(site_id), site)


def get_site_session_sync_request(
    site_id: int, request_id: str
) -> Optional[Dict[str, Any]]:
    row = db_query_one(
        """
        SELECT * FROM browser_session_sync_requests
        WHERE id = ? AND site_id = ? AND admin_site_id IS NULL
        """,
        (str(request_id), int(site_id)),
    )
    if not row:
        return None
    if (
        str(row.get("status") or "") in {"pending", "validating"}
        and _session_sync_request_expired(row)
    ):
        finish_session_sync_request(
            str(row.get("id") or ""),
            "expired",
            "SYNC_REQUEST_EXPIRED",
            "同步请求已过期",
        )
        row = {
            **row,
            "status": "expired",
            "error_code": "SYNC_REQUEST_EXPIRED",
            "error_message": "同步请求已过期",
            "updated_at": utc_now_iso(),
        }
    return _session_sync_public_payload(row)


def fail_site_session_sync_request(
    site_id: int, request_id: str, error_code: str
) -> Tuple[bool, Optional[str]]:
    failure = SESSION_SYNC_PAGE_FAILURES.get(str(error_code or ""))
    if not failure:
        return False, "不支持的同步失败代码"
    row = db_query_one(
        """
        SELECT * FROM browser_session_sync_requests
        WHERE id = ? AND site_id = ? AND admin_site_id IS NULL
        """,
        (str(request_id), int(site_id)),
    )
    if not row:
        return False, "同步请求不存在"
    if str(row.get("status") or "") != "pending":
        return False, "同步请求已结束"
    if _session_sync_request_expired(row):
        finish_session_sync_request(
            str(request_id), "expired", "SYNC_REQUEST_EXPIRED", "同步请求已过期"
        )
        return False, "同步请求已过期"
    status, message = failure
    finish_session_sync_request(str(request_id), status, str(error_code), message)
    return True, None


def claim_session_sync_request(
    request_id: str, secret: str
) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    with SESSION_SYNC_REQUEST_LOCK:
        row = db_query_one(
            "SELECT * FROM browser_session_sync_requests WHERE id = ?",
            (str(request_id),),
        )
        if not row:
            return False, None, "SYNC_REQUEST_NOT_FOUND"
        if str(row.get("status") or "") != "pending":
            return False, None, "SYNC_REQUEST_CONSUMED"
        if _session_sync_request_expired(row):
            now = utc_now_iso()
            db_execute(
                """
                UPDATE browser_session_sync_requests
                SET status = 'expired', error_code = 'SYNC_REQUEST_EXPIRED',
                    error_message = '同步请求已过期', updated_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (now, str(request_id)),
            )
            if row.get("site_id") is not None:
                db_execute(
                    """
                    UPDATE sites SET session_sync_status = 'expired',
                        session_sync_error = '同步请求已过期', updated_at = ?
                    WHERE id = ?
                    """,
                    (now, int(row["site_id"])),
                )
            return False, None, "SYNC_REQUEST_EXPIRED"
        expected = str(row.get("secret_hash") or "")
        actual = hash_session_sync_secret(secret)
        if not hmac.compare_digest(expected, actual):
            return False, None, "SYNC_REQUEST_SECRET_INVALID"
        now = utc_now_iso()
        db_execute(
            """
            UPDATE browser_session_sync_requests
            SET status = 'validating', updated_at = ?, consumed_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (now, now, str(request_id)),
        )
        if row.get("site_id") is not None:
            db_execute(
                """
                UPDATE sites SET session_sync_status = 'validating',
                    session_sync_error = NULL, updated_at = ? WHERE id = ?
                """,
                (now, int(row["site_id"])),
            )
        return True, {**row, "status": "validating", "consumed_at": now}, None


def finish_session_sync_request(
    request_id: str, status: str, code: str = "", message: str = ""
) -> bool:
    """Mark a sync request as terminal and update the target site in one lock.

    The terminal write on ``browser_session_sync_requests`` and the matching
    site/admin_site write share a single process lock.  Before touching the
    site state we re-read the request to confirm it is still the active one
    for that target; if a newer request has already been claimed, we end this
    request's terminal state but leave the site alone so the new request
    keeps controlling the visible status.
    """
    if status not in SESSION_SYNC_TERMINAL_STATUSES:
        raise ValueError("invalid session sync status")
    with SESSION_SYNC_REQUEST_LOCK:
        row = db_query_one(
            "SELECT id, site_id, admin_site_id, platform, target_origin, status "
            "FROM browser_session_sync_requests WHERE id = ?",
            (str(request_id),),
        )
        if not row:
            return False
        now = utc_now_iso()
        target_origin = str(row.get("target_origin") or "")
        platform = str(row.get("platform") or "").strip().lower()
        request_id_value = str(request_id)
        changed = db_execute_rowcount(
            """
            UPDATE browser_session_sync_requests
            SET status = ?, error_code = ?, error_message = ?, updated_at = ?
            WHERE id = ? AND status IN ('pending', 'validating')
            """,
            (status, str(code or "") or None, str(message or "") or None, now, request_id_value),
        )
        if changed <= 0:
            return False
        # Atomicity guarantee: a newer request may have been claimed while
        # this one was in flight.  Only touch the site if this request is
        # still the active one for the target.
        if row.get("site_id") is not None and platform == "sub2api":
            active = db_query_one(
                """
                SELECT id FROM browser_session_sync_requests
                WHERE site_id = ? AND admin_site_id IS NULL
                  AND platform = 'sub2api'
                  AND target_origin = ? AND status = 'validating'
                """,
                (int(row["site_id"]), target_origin),
            )
            if active and str(active.get("id") or "") != request_id_value:
                return True
            db_execute_rowcount(
                """
                UPDATE sites AS s
                SET session_sync_status = ?, session_sync_error = ?,
                    session_synced_at = CASE WHEN ? = 'ready' THEN ? ELSE session_synced_at END,
                    updated_at = ?
                WHERE s.id = ?
                  AND s.platform = 'sub2api'
                  AND s.auth_mode = 'browser'
                  AND EXISTS (
                      SELECT 1
                      FROM browser_session_sync_requests AS r
                      WHERE r.id = ?
                        AND r.site_id = s.id
                        AND r.admin_site_id IS NULL
                        AND r.platform = 'sub2api'
                        AND r.target_origin = ?
                        AND r.status IN ('validating', 'pending', 'ready', 'failed', 'expired')
                  )
                """,
                (
                    status,
                    str(message or "") or None,
                    status,
                    now,
                    now,
                    int(row["site_id"]),
                    request_id_value,
                    target_origin,
                ),
            )
    return True


SUB2API_SESSION_SYNC_FIELDS = frozenset(
    {"access_token", "refresh_token", "token_expires_at"}
)
NEWAPI_SESSION_SYNC_FIELDS = frozenset(
    {
        "access_token",
        "access_user_id",
        "browser_cookie",
        "browser_refresh_cookie",
        "browser_session_id",
        "browser_access_expires_at",
    }
)


def _newapi_session_payload_error(session: Dict[str, Any]) -> Optional[str]:
    browser_cookie = str(session.get("browser_cookie") or "").strip()
    refresh_cookie = str(session.get("browser_refresh_cookie") or "").strip()
    session_id = str(session.get("browser_session_id") or "").strip()
    raw_expires_at = session.get("browser_access_expires_at")
    has_expires_at = raw_expires_at not in (None, "", 0, "0")
    if browser_cookie:
        if len(browser_cookie) > SESSION_SYNC_MAX_TOKEN_LENGTH:
            return "SESSION_COOKIE_INVALID"
        cookie_parts = [part.strip() for part in browser_cookie.split(";")]
        if not cookie_parts or any(
            not re.fullmatch(r"[A-Za-z0-9_-]+=[^;\s]+", part)
            for part in cookie_parts
        ):
            return "SESSION_COOKIE_INVALID"
        if refresh_cookie or session_id or has_expires_at:
            return "SESSION_FIELDS_INVALID"
        return None
    if refresh_cookie and not re.fullmatch(r"new_api_refresh=[^\s;,]+", refresh_cookie):
        return "SESSION_COOKIE_INVALID"
    if bool(refresh_cookie) != bool(session_id):
        return "SESSION_FIELDS_INVALID"
    if has_expires_at and (not refresh_cookie or not session_id):
        return "SESSION_FIELDS_INVALID"
    if has_expires_at:
        try:
            if int(raw_expires_at) <= 0:
                return "SESSION_FIELDS_INVALID"
        except (TypeError, ValueError):
            return "SESSION_FIELDS_INVALID"
    return None


def complete_session_sync_request(
    request_id: str, secret: str, body: Any
) -> Tuple[int, Dict[str, Any]]:
    if not str(secret or ""):
        return 401, {
            "success": False,
            "status": "failed",
            "code": "SYNC_REQUEST_SECRET_REQUIRED",
            "message": "缺少同步凭证",
        }
    if not isinstance(body, dict):
        return 400, {
            "success": False,
            "status": "failed",
            "code": "INVALID_SYNC_PAYLOAD",
            "message": "同步数据格式无效",
        }
    status = str(body.get("status") or "")
    if status not in {"session_found", "no_session"}:
        return 400, {
            "success": False,
            "status": "failed",
            "code": "INVALID_SYNC_STATUS",
            "message": "同步状态无效",
        }
    platform = str(body.get("platform") or "").strip().lower()
    observed_origin = site_origin(str(body.get("observed_origin") or ""))
    session = body.get("session")
    if status == "session_found":
        if not isinstance(session, dict):
            return 400, {
                "success": False,
                "status": "failed",
                "code": "SESSION_REQUIRED",
                "message": "未提供浏览器登录态",
            }
        all_session_keys = SUB2API_SESSION_SYNC_FIELDS | NEWAPI_SESSION_SYNC_FIELDS
        if set(session) - all_session_keys:
            return 400, {
                "success": False,
                "status": "failed",
                "code": "SESSION_FIELDS_INVALID",
                "message": "登录态字段无效",
            }
        for key in all_session_keys:
            limit = (
                256
                if key
                in {
                    "token_expires_at",
                    "access_user_id",
                    "browser_access_expires_at",
                }
                else SESSION_SYNC_MAX_TOKEN_LENGTH
            )
            if len(str(session.get(key) or "")) > limit:
                return 400, {
                    "success": False,
                    "status": "failed",
                    "code": "SESSION_FIELD_TOO_LARGE",
                    "message": "登录态字段过长",
                }
        if platform == "newapi":
            payload_error = _newapi_session_payload_error(session)
            if payload_error:
                message = (
                    "NewAPI Refresh Cookie 必须严格使用 new_api_refresh"
                    if payload_error == "SESSION_COOKIE_INVALID"
                    else "NewAPI 浏览器登录态字段不完整"
                )
                return 400, {
                    "success": False,
                    "status": "failed",
                    "code": payload_error,
                    "message": message,
                }
    elif session is not None:
        return 400, {
            "success": False,
            "status": "failed",
            "code": "SESSION_NOT_ALLOWED",
            "message": "无登录态响应不能携带 session",
        }

    claimed, request_row, claim_error = claim_session_sync_request(
        str(request_id), str(secret)
    )
    if not claimed or not request_row:
        status_codes = {
            "SYNC_REQUEST_SECRET_INVALID": 401,
            "SYNC_REQUEST_NOT_FOUND": 404,
            "SYNC_REQUEST_CONSUMED": 409,
            "SYNC_REQUEST_EXPIRED": 410,
        }
        return status_codes.get(str(claim_error), 400), {
            "success": False,
            "status": "failed",
            "code": str(claim_error or "SYNC_REQUEST_REJECTED"),
            "message": "同步请求不可用",
        }

    if platform != str(request_row.get("platform") or "").strip().lower():
        finish_session_sync_request(
            str(request_id), "failed", "PLATFORM_MISMATCH", "同步平台不匹配"
        )
        return 400, {
            "success": False,
            "status": "failed",
            "code": "PLATFORM_MISMATCH",
            "message": "同步平台不匹配",
        }
    allowed_session_keys = (
        SUB2API_SESSION_SYNC_FIELDS
        if platform == "sub2api"
        else NEWAPI_SESSION_SYNC_FIELDS
        if platform == "newapi"
        else frozenset()
    )
    if status == "session_found" and set(session) - allowed_session_keys:
        finish_session_sync_request(
            str(request_id), "failed", "SESSION_FIELDS_INVALID", "登录态字段无效"
        )
        return 400, {
            "success": False,
            "status": "failed",
            "code": "SESSION_FIELDS_INVALID",
            "message": "登录态字段无效",
        }
    if not observed_origin or observed_origin != str(
        request_row.get("target_origin") or ""
    ):
        finish_session_sync_request(
            str(request_id), "failed", "ORIGIN_MISMATCH", "同步站点 Origin 不匹配"
        )
        return 400, {
            "success": False,
            "status": "failed",
            "code": "ORIGIN_MISMATCH",
            "message": "同步站点 Origin 不匹配",
        }
    if status == "no_session":
        finish_session_sync_request(
            str(request_id), "no_session", "NO_SESSION", "没有登录态，请提前登录"
        )
        return 200, {
            "success": False,
            "status": "no_session",
            "code": "NO_SESSION",
            "message": "没有登录态，请提前登录",
        }

    site_id = request_row.get("site_id")
    target_kind = session_sync_target_kind(request_row)
    target_origin = str(request_row.get("target_origin") or "")
    if platform == "sub2api" and target_kind == "site" and site_id is not None:
        applied, apply_error = apply_sub2api_browser_session(
            int(site_id),
            str(request_row.get("target_origin") or ""),
            session,
            request_id=str(request_id),
            expected_origin=str(request_row.get("target_origin") or ""),
        )
    elif platform == "newapi" and target_kind == "site" and site_id is not None:
        target = db_query_one("SELECT * FROM sites WHERE id = ?", (int(site_id),))
        if (
            not target
            or str(target.get("platform") or "newapi").strip().lower() != "newapi"
            or str(target.get("auth_mode") or "").strip().lower() != BROWSER_AUTH_MODE
            or site_origin(str(target.get("base_url") or "")) != target_origin
        ):
            applied, apply_error = False, "同步目标已变更，请重新发起同步"
        else:
            applied, _validation, apply_error = validate_newapi_site_browser_session(
                str(target.get("base_url") or ""), session
            )
            if applied:
                sync_error = _newapi_session_sync_request_error(
                    target_kind, int(site_id), str(request_id), target_origin
                )
                if sync_error:
                    applied, apply_error = False, sync_error
            if applied:
                persist_newapi_site_browser_session(int(site_id), session)
    else:
        finish_session_sync_request(
            str(request_id), "failed", "UNSUPPORTED_TARGET", "当前同步目标暂不支持"
        )
        return 400, {
            "success": False,
            "status": "failed",
            "code": "UNSUPPORTED_TARGET",
            "message": "当前同步目标暂不支持",
        }
    if not applied:
        message = apply_error or "登录态已过期，请重新登录"
        finish_session_sync_request(
            str(request_id), "expired", "SESSION_INVALID", message
        )
        return 401, {
            "success": False,
            "status": "expired",
            "code": "SESSION_INVALID",
            "message": message,
        }
    finish_session_sync_request(str(request_id), "ready")
    detection = detect_site(int(site_id)) if target_kind == "site" else None
    return 200, {
        "success": True,
        "status": "ready",
        "message": "浏览器登录态已同步",
        "detected": bool(detection.get("success"))
        if isinstance(detection, dict)
        else False,
    }


def is_admin_site_row(site: Dict[str, Any]) -> bool:
    """识别 admin_sites 查询结果，避免把上游监控站点的 key 写入主站缓存。"""
    return any(
        field in site
        for field in (
            "security_proof",
            "browser_access_token",
            "browser_session_id",
            "browser_access_expires_at",
        )
    )


ADMIN_SITE_CAPABILITIES: Dict[str, Dict[str, bool]] = {
    "newapi": {
        "list_channels": True,
        "read_channel_detail": True,
        "edit_channel": True,
        "toggle_channel": False,
        "create_channel": False,
        "delete_channel": False,
        "channel_key": True,
        "channel_priority": True,
        "channel_weight": True,
        "group_rates": True,
        "model_pricing": False,
    },
    "sub2api": {
        "list_channels": True,
        "read_channel_detail": True,
        "edit_channel": True,
        "toggle_channel": True,
        "create_channel": False,
        "delete_channel": False,
        "channel_key": False,
        "channel_priority": False,
        "channel_weight": False,
        "group_rates": True,
        "model_pricing": True,
    },
}


def admin_site_platform(site: Dict[str, Any]) -> str:
    value = str(site.get("platform") or "newapi").strip().lower()
    return value if value in ADMIN_SITE_CAPABILITIES else "newapi"


def admin_site_capabilities(site: Dict[str, Any]) -> Dict[str, bool]:
    return dict(ADMIN_SITE_CAPABILITIES[admin_site_platform(site)])


def validate_admin_site_base_url(value: str) -> Tuple[str, Optional[str]]:
    normalized = normalize_base_url(value)
    try:
        parsed = urlparse(normalized)
        parsed.port
    except (TypeError, ValueError):
        return "", "主站 Base URL 无效"
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return "", "主站 Base URL 只允许 http 或 https"
    if parsed.username or parsed.password:
        return "", "主站 Base URL 不能包含用户名或密码"
    return normalized, None


def get_cached_admin_channel_key(admin_site_id: int, channel_id: int) -> str:
    row = db_query_one(
        "SELECT channel_key FROM admin_channel_keys WHERE admin_site_id = ? AND channel_id = ?",
        (int(admin_site_id), int(channel_id)),
    )
    key = str(row.get("channel_key") or "").strip() if row else ""
    return key if not _channel_key_is_masked(key) else ""


def persist_admin_channel_key(admin_site_id: int, channel_id: int, channel_key: str) -> None:
    key = str(channel_key or "").strip()
    if not key or _channel_key_is_masked(key):
        return
    now = utc_now_iso()
    db_execute(
        """
        INSERT INTO admin_channel_keys
        (admin_site_id, channel_id, channel_key, fetched_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON DUPLICATE KEY UPDATE
          channel_key = VALUES(channel_key), fetched_at = VALUES(fetched_at),
          updated_at = VALUES(updated_at)
        """,
        (int(admin_site_id), int(channel_id), key, now, now, now),
    )


def clear_admin_channel_key(admin_site_id: int, channel_id: int) -> None:
    db_execute(
        "DELETE FROM admin_channel_keys WHERE admin_site_id = ? AND channel_id = ?",
        (int(admin_site_id), int(channel_id)),
    )


def sync_admin_channel_key(
    admin_site_id: int, channel_id: int, submitted_key: Any
) -> None:
    """Synchronize the local cache after a successful channel create/update."""
    key = str(submitted_key or "").strip()
    if key and not _channel_key_is_masked(key):
        persist_admin_channel_key(admin_site_id, channel_id, key)
        return
    clear_admin_channel_key(admin_site_id, channel_id)


def open_upstream_url(
    request: urllib.request.Request,
    timeout: float = HTTP_TIMEOUT_SECONDS,
    *handlers: Any,
):
    """Use the OS network route without application-level HTTP proxies."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), *handlers)
    try:
        return opener.open(request, timeout=timeout)
    except urllib.error.URLError as exc:
        if not _is_connection_reset_by_peer(exc):
            raise
        return _open_upstream_url_with_curl(request, timeout)


class _CurlResponse:
    """Small urllib-compatible response for the connection-reset fallback."""

    def __init__(self, body: bytes, headers: EmailMessage, status: int) -> None:
        self._body = io.BytesIO(body)
        self.headers = headers
        self.status = status

    def read(self, amount: int = -1) -> bytes:
        return self._body.read(amount)

    def __enter__(self) -> "_CurlResponse":
        return self

    def __exit__(self, *_args: Any) -> None:
        self._body.close()


def _is_connection_reset_by_peer(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, ConnectionResetError) or "Connection reset by peer" in str(reason)


def _curl_config_value(value: Any) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", "")


def _curl_headers_from_dump(raw: bytes) -> Tuple[int, EmailMessage]:
    blocks = [block for block in re.split(br"\r?\n\r?\n", raw) if block.strip()]
    block = blocks[-1] if blocks else b""
    lines = block.splitlines()
    if not lines:
        return 0, EmailMessage()
    match = re.search(br"\s(\d{3})\s", lines[0])
    status = int(match.group(1)) if match else 0
    headers = EmailMessage()
    for raw_line in lines[1:]:
        if b":" not in raw_line:
            continue
        key, value = raw_line.split(b":", 1)
        headers.add_header(
            key.decode("iso-8859-1", errors="replace").strip(),
            value.decode("iso-8859-1", errors="replace").strip(),
        )
    return status, headers


def _open_upstream_url_with_curl(
    request: urllib.request.Request, timeout: float
) -> _CurlResponse:
    """Retry only TLS-fingerprint-sensitive sites through the system curl client.

    Some Cloudflare-fronted NewAPI deployments reset stdlib TLS clients while
    accepting the same HTTP request from curl. Credentials stay in a 0700 temp
    directory and are never passed as process arguments.
    """
    with tempfile.TemporaryDirectory(prefix="upstream-curl-") as temp_dir:
        directory = Path(temp_dir)
        config_path = directory / "request.conf"
        body_path = directory / "response.bin"
        headers_path = directory / "headers.txt"
        request_body_path = directory / "request.bin"
        lines = [
            "silent",
            "show-error",
            f'max-time = "{max(1, int(timeout))}"',
            f'request = "{_curl_config_value(request.get_method())}"',
            f'url = "{_curl_config_value(request.full_url)}"',
            f'output = "{_curl_config_value(body_path)}"',
            f'dump-header = "{_curl_config_value(headers_path)}"',
        ]
        for key, value in request.header_items():
            lines.append(
                f'header = "{_curl_config_value(f"{key}: {value}")}"'
            )
        if request.data is not None:
            request_body_path.write_bytes(request.data)
            lines.append(f'data-binary = "@{_curl_config_value(request_body_path)}"')
        config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            result = subprocess.run(
                ["curl", "--config", str(config_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=max(2, int(timeout) + 3),
                check=False,
            )
        except FileNotFoundError as exc:
            raise urllib.error.URLError("上游重置连接，且系统未安装 curl 兼容客户端") from exc
        except subprocess.TimeoutExpired as exc:
            raise urllib.error.URLError("curl 兼容请求超时") from exc
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise urllib.error.URLError(f"curl 兼容请求失败：{detail or result.returncode}")
        body = body_path.read_bytes() if body_path.exists() else b""
        status, headers = _curl_headers_from_dump(
            headers_path.read_bytes() if headers_path.exists() else b""
        )
        if status >= 400:
            raise urllib.error.HTTPError(
                request.full_url,
                status,
                f"HTTP {status}",
                headers,
                io.BytesIO(body),
            )
        if not status:
            raise urllib.error.URLError("curl 兼容请求没有返回有效 HTTP 状态")
        return _CurlResponse(body, headers, status)


def json_request(
    url: str,
    payload: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
    method: str = "POST",
) -> Tuple[int, Dict[str, Any], str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Upstream-Ratio-Watch/1.0",
    }
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    with open_upstream_url(req) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        try:
            payload_obj = json.loads(raw) if raw else {}
        except Exception:
            payload_obj = {"raw": raw}
        if not isinstance(payload_obj, dict):
            payload_obj = {"raw": raw}
        return resp.status, payload_obj, raw


def parse_groups_payload(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return {}

    normalized: Dict[str, Dict[str, Any]] = {}
    for name in sorted(data.keys()):
        info = data.get(name) or {}
        if not isinstance(info, dict):
            info = {}

        ratio = info.get("ratio")
        if isinstance(ratio, (int, float)):
            ratio_value: Any = float(ratio)
            ratio_type = "number"
        elif isinstance(ratio, str):
            stripped = ratio.strip()
            try:
                ratio_value = float(stripped)
                ratio_type = "number"
            except ValueError:
                ratio_value = stripped
                ratio_type = "text"
        else:
            ratio_value = ratio
            ratio_type = "text"

        normalized[name] = {
            "ratio": ratio_value,
            "ratio_type": ratio_type,
            "desc": info.get("desc", ""),
        }
    return normalized


def parse_newapi_models_by_group(
    pricing_payload: Any,
    uptime_payload: Any,
    groups: Dict[str, Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    if isinstance(pricing_payload, dict) and "data" in pricing_payload:
        pricing_payload = pricing_payload.get("data")
    if isinstance(uptime_payload, dict) and "data" in uptime_payload:
        uptime_payload = uptime_payload.get("data")
    if not isinstance(pricing_payload, list) or not isinstance(groups, dict):
        return {}

    monitors: List[Dict[str, Any]] = []
    if isinstance(uptime_payload, list):
        for category in uptime_payload:
            if not isinstance(category, dict):
                continue
            category_name = str(category.get("categoryName") or "").strip()
            for monitor in category.get("monitors") or []:
                if not isinstance(monitor, dict):
                    continue
                entry = dict(monitor)
                entry["category"] = category_name
                monitors.append(entry)

    def matching_monitor(model_name: str) -> Optional[Dict[str, Any]]:
        normalized = model_name.casefold()
        exact = [item for item in monitors if str(item.get("name") or "").strip().casefold() == normalized]
        if exact:
            return exact[0]
        fuzzy = [
            item for item in monitors
            if str(item.get("name") or "").strip()
            and (
                normalized in str(item.get("name") or "").strip().casefold()
                or str(item.get("name") or "").strip().casefold() in normalized
            )
        ]
        return fuzzy[0] if len(fuzzy) == 1 else None

    def monitor_status(value: Any) -> str:
        try:
            status = int(value)
        except (TypeError, ValueError):
            return "configured"
        return {0: "error", 1: "operational", 2: "degraded", 3: "maintenance"}.get(status, "configured")

    models_by_group: Dict[str, List[Dict[str, Any]]] = {}
    seen: set[Tuple[str, str]] = set()
    for pricing in pricing_payload:
        if not isinstance(pricing, dict):
            continue
        model_name = str(pricing.get("model_name") or pricing.get("name") or "").strip()
        if not model_name:
            continue
        enabled_groups = pricing.get("enable_groups") or pricing.get("groups") or []
        if isinstance(enabled_groups, str):
            enabled_groups = [enabled_groups]
        if not isinstance(enabled_groups, list):
            enabled_groups = []
        enabled_names = {str(name).strip() for name in enabled_groups if str(name).strip()}
        target_groups = list(groups.keys()) if "all" in enabled_names else [name for name in groups if name in enabled_names]
        if not target_groups:
            continue

        monitor = matching_monitor(model_name)
        uptime_value = monitor.get("uptime") if monitor else None
        try:
            availability = float(uptime_value)
            if 0 <= availability <= 1:
                availability *= 100
        except (TypeError, ValueError):
            availability = None

        ratio_value = pricing.get("model_ratio")
        try:
            ratio_value = float(ratio_value)
            ratio_type = "number"
        except (TypeError, ValueError):
            ratio_type = "text"

        for group_name in target_groups:
            key = (group_name, model_name.casefold())
            if key in seen:
                continue
            seen.add(key)
            group_info = groups.get(group_name) or {}
            models_by_group.setdefault(group_name, []).append({
                "name": model_name,
                "ratio": ratio_value,
                "ratio_type": ratio_type,
                "group_ratio": group_info.get("ratio"),
                "channel": str(monitor.get("category") or "") if monitor else "",
                "platform": pricing.get("owner_by") or "NewAPI",
                "status": monitor_status(monitor.get("status")) if monitor else "configured",
                "latency_ms": None,
                "ping_latency_ms": None,
                "availability_7d": availability,
                "availability_label": "24 小时" if availability is not None else "",
                "timeline": [],
                "monitor": str(monitor.get("name") or "") if monitor else model_name,
                "source": "NewAPI 公开监控" if monitor else "NewAPI 模型配置",
                "completion_ratio": pricing.get("completion_ratio"),
            })

    for model_list in models_by_group.values():
        model_list.sort(key=lambda item: str(item.get("name") or "").casefold())
    return models_by_group


def parse_sub2api_groups(groups_payload: Any, rates_payload: Any = None) -> Dict[str, Dict[str, Any]]:
    if isinstance(groups_payload, dict) and "data" in groups_payload:
        groups_payload = groups_payload.get("data")
    if isinstance(rates_payload, dict) and "data" in rates_payload:
        rates_payload = rates_payload.get("data")
    if not isinstance(groups_payload, list):
        return {}
    rates: Dict[str, Any] = {}
    if isinstance(rates_payload, dict):
        rates = {str(key): value for key, value in rates_payload.items()}

    normalized: Dict[str, Dict[str, Any]] = {}
    for item in groups_payload:
        if not isinstance(item, dict):
            continue
        group_id = item.get("id")
        name = str(item.get("name") or group_id or "").strip()
        if not name:
            continue
        base_ratio = item.get("rate_multiplier")
        effective_ratio = rates.get(str(group_id), base_ratio)
        try:
            ratio_value: Any = float(effective_ratio)
            ratio_type = "number"
        except (TypeError, ValueError):
            ratio_value = effective_ratio
            ratio_type = "text"
        normalized[name] = {
            "ratio": ratio_value,
            "ratio_type": ratio_type,
            "desc": item.get("description") or "",
            "id": group_id,
            "platform": item.get("platform") or "",
            "base_ratio": base_ratio,
            "user_ratio": rates.get(str(group_id)),
            "status": item.get("status") or "",
            "is_exclusive": bool(item.get("is_exclusive")),
            "subscription_type": item.get("subscription_type") or "",
            "rpm_limit": item.get("rpm_limit"),
        }
    return normalized


def parse_sub2api_channel_models(
    channels_payload: Any,
    groups: Dict[str, Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Map the user-facing channel model list back to the monitored groups.

    sub2api's user endpoint groups models by channel and platform. A group in
    the same channel/platform section shares that section's model list, while
    its own group multiplier remains the effective multiplier for each model.
    """
    if isinstance(channels_payload, dict) and "data" in channels_payload:
        channels_payload = channels_payload.get("data")
    if not isinstance(channels_payload, list) or not isinstance(groups, dict):
        return {}

    groups_by_id = {
        str(item.get("id")): (name, item)
        for name, item in groups.items()
        if isinstance(item, dict) and item.get("id") is not None
    }
    models_by_group: Dict[str, List[Dict[str, Any]]] = {}
    seen: set[Tuple[str, str, str]] = set()

    for channel in channels_payload:
        if not isinstance(channel, dict):
            continue
        channel_name = str(channel.get("name") or "").strip()
        sections = channel.get("platforms") or []
        if not isinstance(sections, list):
            continue
        for section in sections:
            if not isinstance(section, dict):
                continue
            models = section.get("supported_models") or section.get("models") or []
            group_refs = section.get("groups") or []
            if not isinstance(models, list) or not isinstance(group_refs, list):
                continue

            matched_groups: List[Tuple[str, Dict[str, Any]]] = []
            for group_ref in group_refs:
                if not isinstance(group_ref, dict):
                    continue
                group_id = group_ref.get("id")
                matched = groups_by_id.get(str(group_id)) if group_id is not None else None
                if not matched:
                    group_name = str(group_ref.get("name") or "").strip()
                    group_info = groups.get(group_name)
                    matched = (group_name, group_info) if isinstance(group_info, dict) else None
                if matched:
                    matched_groups.append(matched)

            for group_name, group_info in matched_groups:
                destination = models_by_group.setdefault(group_name, [])
                for model in models:
                    if not isinstance(model, dict):
                        continue
                    model_name = str(model.get("name") or model.get("id") or model.get("model") or "").strip()
                    if not model_name:
                        continue
                    model_key = (group_name, channel_name, model_name)
                    if model_key in seen:
                        continue
                    seen.add(model_key)

                    raw_status = model.get("status") or model.get("health") or model.get("state") or ""
                    if not raw_status and isinstance(model.get("available"), bool):
                        raw_status = "可用" if model["available"] else "不可用"
                    if not raw_status and isinstance(model.get("enabled"), bool):
                        raw_status = "启用" if model["enabled"] else "停用"

                    destination.append({
                        "name": model_name,
                        "ratio": group_info.get("ratio"),
                        "ratio_type": group_info.get("ratio_type") or "text",
                        "channel": channel_name,
                        "platform": model.get("platform") or section.get("platform") or group_info.get("platform") or "",
                        "status": str(raw_status),
                    })

    for model_list in models_by_group.values():
        model_list.sort(key=lambda item: (str(item.get("name") or "").lower(), str(item.get("channel") or "").lower()))
    return models_by_group


def parse_sub2api_monitor_models(
    monitors_payload: Any,
    groups: Dict[str, Dict[str, Any]],
) -> Tuple[Dict[str, List[Dict[str, Any]]], List[Dict[str, Any]]]:
    """Attach sub2api's read-only channel monitor statuses to known groups.

    The monitor API normally includes ``group_name``. Some deployments leave
    it empty; in that case only a single candidate group can be matched safely.
    Ambiguous monitors stay separate instead of being assigned to a wrong group.
    """
    if isinstance(monitors_payload, dict) and "data" in monitors_payload:
        monitors_payload = monitors_payload.get("data")
    items = monitors_payload.get("items") if isinstance(monitors_payload, dict) else None
    if not isinstance(items, list) or not isinstance(groups, dict):
        return {}, []

    models_by_group: Dict[str, List[Dict[str, Any]]] = {}
    unmatched: List[Dict[str, Any]] = []

    def normalized_label(value: Any) -> str:
        """比较上游分组/监控名称时忽略大小写和展示标点。"""
        return "".join(char.casefold() for char in str(value or "") if char.isalnum())

    def numeric_values(value: Any) -> List[float]:
        values: List[float] = []
        for raw in re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", str(value or "")):
            try:
                values.append(float(raw))
            except ValueError:
                continue
        return values

    def same_ratio(left: Any, right: Any) -> bool:
        try:
            return abs(float(left) - float(right)) < 1e-9
        except (TypeError, ValueError):
            return False

    def resolve_group(monitor: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, Any]]]:
        group_name = str(monitor.get("group_name") or "").strip()
        group_info = groups.get(group_name)
        if isinstance(group_info, dict):
            return group_name, group_info
        provider = str(monitor.get("provider") or "").strip().lower()
        platform_matches = [
            (name, item)
            for name, item in groups.items()
            if isinstance(item, dict) and str(item.get("platform") or "").strip().lower() == provider
        ]

        # Some sub2api deployments omit group_name and encode the group label
        # in monitor.name, e.g. ``GPT-pro(0.1)``. Resolve that label before
        # falling back to the single-platform heuristic below.
        monitor_label = normalized_label(monitor.get("name"))
        label_matches = [
            (len(normalized_label(name)), name, item)
            for name, item in platform_matches
            if normalized_label(name) and normalized_label(name) in monitor_label
        ]
        if label_matches:
            longest = max(item[0] for item in label_matches)
            best = [(name, item) for length, name, item in label_matches if length == longest]
            if len(best) == 1:
                return best[0]

            # If labels are still tied, use a numeric suffix such as 0.03
            # from the monitor name against the configured group multiplier.
            monitor_numbers = numeric_values(monitor.get("name"))
            ratio_matches = [
                (name, item)
                for name, item in best
                if any(same_ratio(number, item.get("ratio")) for number in monitor_numbers)
            ]
            if len(ratio_matches) == 1:
                return ratio_matches[0]

        if len(platform_matches) == 1:
            return platform_matches[0]
        if len(groups) == 1:
            name, item = next(iter(groups.items()))
            return (name, item) if isinstance(item, dict) else None
        return None

    for monitor in items:
        if not isinstance(monitor, dict):
            continue
        monitor_name = str(monitor.get("name") or "").strip()
        target_group = resolve_group(monitor)
        monitored_models: List[Dict[str, Any]] = []
        primary_model = str(monitor.get("primary_model") or "").strip()
        timeline = []
        for point in monitor.get("timeline") or []:
            if not isinstance(point, dict):
                continue
            timeline.append({
                "status": str(point.get("status") or ""),
                "latency_ms": point.get("latency_ms"),
                "ping_latency_ms": point.get("ping_latency_ms"),
                "checked_at": str(point.get("checked_at") or ""),
            })
            if len(timeline) >= 60:
                break
        if primary_model:
            monitored_models.append({
                "name": primary_model,
                "status": str(monitor.get("primary_status") or ""),
                "latency_ms": monitor.get("primary_latency_ms"),
                "ping_latency_ms": monitor.get("primary_ping_latency_ms"),
                "availability_7d": monitor.get("availability_7d"),
                "timeline": timeline,
            })
        for extra in monitor.get("extra_models") or []:
            if not isinstance(extra, dict):
                continue
            model_name = str(extra.get("model") or "").strip()
            if model_name:
                monitored_models.append({
                    "name": model_name,
                    "status": str(extra.get("status") or ""),
                    "latency_ms": extra.get("latency_ms"),
                    "ping_latency_ms": None,
                    "availability_7d": None,
                    "timeline": [],
                })

        if not target_group:
            unmatched.extend({
                "name": item["name"],
                "status": item.get("status") or "",
                "monitor": monitor_name,
                "provider": monitor.get("provider") or "",
            } for item in monitored_models)
            continue

        group_name, group_info = target_group
        destination = models_by_group.setdefault(group_name, [])
        for item in monitored_models:
            destination.append({
                "name": item["name"],
                "ratio": group_info.get("ratio"),
                "ratio_type": group_info.get("ratio_type") or "text",
                "channel": "",
                "platform": monitor.get("provider") or group_info.get("platform") or "",
                "status": item["status"],
                "latency_ms": item["latency_ms"],
                "ping_latency_ms": item["ping_latency_ms"],
                "availability_7d": item["availability_7d"],
                "timeline": item["timeline"],
                "monitor": monitor_name,
                "source": "上游监控",
            })

    return models_by_group, unmatched


def merge_sub2api_group_models(
    configured_models: Dict[str, List[Dict[str, Any]]],
    monitored_models: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Use the monitor status as authoritative while retaining configured-only models."""
    merged = {name: [dict(item) for item in values] for name, values in configured_models.items()}
    for group_name, items in monitored_models.items():
        destination = merged.setdefault(group_name, [])
        indexes = {str(item.get("name") or "").casefold(): index for index, item in enumerate(destination)}
        for item in items:
            key = str(item.get("name") or "").casefold()
            if key in indexes:
                destination[indexes[key]].update({key: value for key, value in item.items() if value not in (None, "")})
            else:
                indexes[key] = len(destination)
                destination.append(dict(item))
    for model_list in merged.values():
        model_list.sort(key=lambda item: str(item.get("name") or "").casefold())
    return merged


def stable_hash(obj: Any) -> str:
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def next_check_iso(interval_minutes: int) -> str:
    return (app_now() + timedelta(minutes=max(MIN_INTERVAL_MINUTES, interval_minutes))).isoformat(timespec="seconds")


def fetch_newapi_groups(base_url: str) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    url = f"{normalize_base_url(base_url)}/api/user/groups"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Upstream-Ratio-Watch/1.0",
        },
        method="GET",
    )
    try:
        with open_upstream_url(req) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            payload = json.loads(body)
            if not isinstance(payload, dict) or not payload.get("success"):
                return False, payload if isinstance(payload, dict) else {"raw": body}, "success=false"
            return True, payload, None
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        return False, {"status": exc.code, "raw": raw}, f"HTTP {exc.code}"
    except Exception as exc:
        return False, {"error": str(exc)}, str(exc)


def request_json(url: str, headers: Optional[Dict[str, str]] = None, payload: Optional[Dict[str, Any]] = None, method: str = "GET") -> Tuple[bool, Any, Optional[str]]:
    data = None
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "Upstream-Ratio-Watch/1.0",
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with open_upstream_url(req) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(body) if body else {}
            return True, parsed, None
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        return False, {"status": exc.code, "raw": raw}, f"HTTP {exc.code}"
    except Exception as exc:
        return False, {"error": str(exc)}, str(exc)


def _url_origin(value: str) -> Tuple[str, Optional[str], Optional[int]]:
    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    port = parsed.port
    if port is None:
        port = 443 if scheme == "https" else 80 if scheme == "http" else None
    return scheme, parsed.hostname, port


class SameOriginAdminRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Prevent administrator credentials from following cross-Origin redirects."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            same_origin = _url_origin(req.full_url) == _url_origin(newurl)
        except (TypeError, ValueError):
            same_origin = False
        if not same_origin:
            raise urllib.error.HTTPError(
                newurl, 403, "跨 Origin 跳转已拒绝", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def admin_request_json(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    payload: Optional[Dict[str, Any]] = None,
    method: str = "GET",
) -> Tuple[bool, Any, Optional[str]]:
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "Upstream-Ratio-Watch/1.0",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(
        url, data=data, headers=request_headers, method=method
    )
    try:
        with open_upstream_url(req, HTTP_TIMEOUT_SECONDS, SameOriginAdminRedirectHandler()) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return True, json.loads(body) if body else {}, None
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        return False, {"status": exc.code, "raw": raw}, f"HTTP {exc.code}"
    except Exception as exc:
        return False, {"error": str(exc)}, str(exc)


def request_json_with_headers(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    payload: Optional[Dict[str, Any]] = None,
    method: str = "GET",
) -> Tuple[bool, Any, Optional[str], Dict[str, Any]]:
    """请求 JSON，同时保留响应头，供网页登录态捕获 Set-Cookie 使用。"""
    data = None
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "Upstream-Ratio-Watch/1.0",
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with open_upstream_url(req) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(body) if body else {}
            response_headers: Dict[str, Any] = {
                "set-cookie": resp.headers.get_all("Set-Cookie") or [],
            }
            return True, parsed, None, response_headers
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        response_headers = {"set-cookie": exc.headers.get_all("Set-Cookie") or []}
        return False, {"status": exc.code, "raw": raw}, f"HTTP {exc.code}", response_headers
    except Exception as exc:
        return False, {"error": str(exc)}, str(exc), {"set-cookie": []}


def refresh_newapi_uptime_cache(base_url: str, headers: Dict[str, str]) -> None:
    normalized_base = normalize_base_url(base_url)
    try:
        uptime_ok, uptime_payload, uptime_error = request_json(
            f"{normalized_base}/api/uptime/status",
            headers=headers,
        )
        if uptime_ok and isinstance(uptime_payload, dict) and uptime_payload.get("success"):
            with MODEL_CACHE_LOCK:
                NEWAPI_UPTIME_CACHE[normalized_base] = {
                    "payload": uptime_payload,
                    "updated_monotonic": time.monotonic(),
                    "error": None,
                }
            matching_sites = db_query_all("SELECT id FROM sites WHERE base_url = ?", (normalized_base,))
            for site in matching_sites:
                invalidate_site_model_cache(int(site["id"]))
                schedule_model_cache_refresh(int(site["id"]))
        elif uptime_error:
            with MODEL_CACHE_LOCK:
                previous = NEWAPI_UPTIME_CACHE.get(normalized_base, {})
                previous["error"] = uptime_error
                NEWAPI_UPTIME_CACHE[normalized_base] = previous
    finally:
        with MODEL_CACHE_LOCK:
            NEWAPI_UPTIME_REFRESHING.discard(normalized_base)


def get_cached_newapi_uptime(base_url: str, headers: Dict[str, str]) -> Tuple[Dict[str, Any], Optional[str]]:
    normalized_base = normalize_base_url(base_url)
    with MODEL_CACHE_LOCK:
        entry = NEWAPI_UPTIME_CACHE.get(normalized_base)
        age = time.monotonic() - float(entry.get("updated_monotonic") or 0) if entry else float("inf")
        if age >= UPTIME_CACHE_TTL_SECONDS and normalized_base not in NEWAPI_UPTIME_REFRESHING:
            NEWAPI_UPTIME_REFRESHING.add(normalized_base)
            threading.Thread(
                target=refresh_newapi_uptime_cache,
                args=(normalized_base, dict(headers)),
                daemon=True,
            ).start()
        if entry and isinstance(entry.get("payload"), dict):
            return entry["payload"], entry.get("error")
        return {"success": True, "data": []}, "公开监控正在后台刷新"


def _newapi_uptime_cache_key(site: Dict[str, Any]) -> str:
    """Secret-free cache key for the per-site NewAPI uptime payload.

    Uses only stable, non-sensitive site identity (base URL, site id, auth
    mode, and a coarse bucket of the browser session expiry) so no
    plaintext token, cookie, or session id ever enters a cache key.
    """
    try:
        expires = int(site.get("browser_access_expires_at") or 0)
    except (TypeError, ValueError):
        expires = 0
    auth_mode = str(site.get("auth_mode") or "token").strip().lower()
    return "|".join(
        (
            normalize_base_url(str(site.get("base_url") or "")),
            str(int(site.get("id") or 0)),
            auth_mode,
            str(expires // 60),
        )
    )


def refresh_newapi_uptime_cache_for_site(site: Dict[str, Any]) -> None:
    """Refresh the uptime cache using the unified browser executor."""
    cache_key = _newapi_uptime_cache_key(site)
    normalized_base = normalize_base_url(str(site.get("base_url") or ""))
    try:
        ok, payload, error = newapi_browser_request(site, "GET", "/api/uptime/status")
        if ok and isinstance(payload, dict) and payload.get("success"):
            with MODEL_CACHE_LOCK:
                NEWAPI_UPTIME_CACHE[cache_key] = {
                    "payload": payload,
                    "updated_monotonic": time.monotonic(),
                    "error": None,
                }
            matching_sites = db_query_all(
                "SELECT id FROM sites WHERE base_url = ?", (normalized_base,)
            )
            for row in matching_sites:
                invalidate_site_model_cache(int(row["id"]))
                schedule_model_cache_refresh(int(row["id"]))
        elif error:
            with MODEL_CACHE_LOCK:
                previous = NEWAPI_UPTIME_CACHE.get(cache_key, {})
                previous["error"] = error
                NEWAPI_UPTIME_CACHE[cache_key] = previous
    finally:
        with MODEL_CACHE_LOCK:
            NEWAPI_UPTIME_REFRESHING.discard(cache_key)


def get_cached_newapi_uptime_for_site(
    site: Dict[str, Any]
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Cache-aware NewAPI uptime fetcher keyed by stable site identity.

    Preserves the previous successful payload when a refresh fails so the UI
    can keep showing the last known uptime while the new request is in
    flight.
    """
    cache_key = _newapi_uptime_cache_key(site)
    with MODEL_CACHE_LOCK:
        entry = NEWAPI_UPTIME_CACHE.get(cache_key)
        age = (
            time.monotonic() - float(entry.get("updated_monotonic") or 0)
            if entry
            else float("inf")
        )
        if age >= UPTIME_CACHE_TTL_SECONDS and cache_key not in NEWAPI_UPTIME_REFRESHING:
            NEWAPI_UPTIME_REFRESHING.add(cache_key)
            threading.Thread(
                target=refresh_newapi_uptime_cache_for_site,
                args=(dict(site),),
                daemon=True,
            ).start()
        if entry and isinstance(entry.get("payload"), dict):
            return entry["payload"], entry.get("error")
    return {"success": True, "data": []}, "公开监控正在后台刷新"


def newapi_auth_headers(access_token: str = "", user_id: str = "") -> Dict[str, str]:
    """Build NewAPI console-style headers (system access token + New-Api-User)."""
    headers: Dict[str, str] = {}
    token = (access_token or "").strip()
    if token:
        headers["Authorization"] = token.removeprefix("Bearer ").removeprefix("bearer ").strip()
    if str(user_id or "").strip():
        headers["New-Api-User"] = str(user_id).strip()
    return headers


def site_newapi_headers(site: Dict[str, Any]) -> Dict[str, str]:
    headers = newapi_auth_headers(
        access_token=str(site.get("access_token") or ""),
        user_id=str(site.get("access_user_id") or ""),
    )
    proof = str(site.get("security_proof") or "").strip()
    if proof:
        headers["X-Security-Proof"] = proof
    return headers


def clamp_perf_hours(raw: Any, default: float = 24) -> float:
    try:
        hours = float(raw)
    except (TypeError, ValueError):
        hours = float(default)
    if hours <= 0:
        hours = float(default)
    # NewAPI caps at 30 days
    return min(hours, 24 * 30)


def fetch_newapi_pricing(
    base_url: str,
    access_token: str = "",
    user_id: str = "",
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    headers = newapi_auth_headers(access_token, user_id)
    ok, payload, error = request_json(
        f"{normalize_base_url(base_url)}/api/pricing",
        headers=headers,
    )
    if ok and (not isinstance(payload, dict) or not payload.get("success")):
        return False, payload if isinstance(payload, dict) else {"raw": payload}, (
            str(payload.get("message") or "pricing success=false")
            if isinstance(payload, dict)
            else "pricing 响应异常"
        )
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "读取 pricing 失败"
    return True, payload if isinstance(payload, dict) else {"success": True, "data": payload}, None


def fetch_newapi_perf_summary(
    base_url: str,
    hours: int = 24,
    access_token: str = "",
    user_id: str = "",
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    hours = clamp_perf_hours(hours, 24)
    headers = newapi_auth_headers(access_token, user_id)
    ok, payload, error = request_json(
        f"{normalize_base_url(base_url)}/api/perf-metrics/summary?hours={hours:g}",
        headers=headers,
    )
    if ok and (not isinstance(payload, dict) or not payload.get("success")):
        return False, payload if isinstance(payload, dict) else {"raw": payload}, (
            str(payload.get("message") or "perf-metrics/summary success=false")
            if isinstance(payload, dict)
            else "perf-metrics/summary 响应异常"
        )
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "读取 perf-metrics/summary 失败"
    return True, payload if isinstance(payload, dict) else {"success": True, "data": payload}, None


def fetch_newapi_perf_detail(
    base_url: str,
    model_name: str,
    hours: int = 24,
    group: str = "",
    access_token: str = "",
    user_id: str = "",
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    hours = clamp_perf_hours(hours, 24)
    model_name = (model_name or "").strip()
    if not model_name:
        return False, {}, "model is required"
    qs = f"model={quote(model_name)}&hours={hours:g}"
    group = (group or "").strip()
    if group:
        qs += f"&group={quote(group)}"
    headers = newapi_auth_headers(access_token, user_id)
    ok, payload, error = request_json(
        f"{normalize_base_url(base_url)}/api/perf-metrics?{qs}",
        headers=headers,
    )
    if ok and (not isinstance(payload, dict) or not payload.get("success")):
        return False, payload if isinstance(payload, dict) else {"raw": payload}, (
            str(payload.get("message") or "perf-metrics success=false")
            if isinstance(payload, dict)
            else "perf-metrics 响应异常"
        )
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "读取 perf-metrics 失败"
    return True, payload if isinstance(payload, dict) else {"success": True, "data": payload}, None


def fetch_newapi_pricing_for_site(site: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """NewAPI ``/api/pricing`` for a full site record, browser-aware."""
    ok, payload, error = newapi_browser_request(site, "GET", "/api/pricing")
    if ok and (not isinstance(payload, dict) or not payload.get("success")):
        return False, payload if isinstance(payload, dict) else {"raw": payload}, (
            str(payload.get("message") or "pricing success=false")
            if isinstance(payload, dict)
            else "pricing 响应异常"
        )
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "读取 pricing 失败"
    return True, payload if isinstance(payload, dict) else {"success": True, "data": payload}, None


def fetch_newapi_perf_summary_for_site(
    site: Dict[str, Any], hours: int = 24
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    hours = clamp_perf_hours(hours, 24)
    ok, payload, error = newapi_browser_request(
        site, "GET", "/api/perf-metrics/summary", query=f"hours={hours:g}"
    )
    if ok and (not isinstance(payload, dict) or not payload.get("success")):
        return False, payload if isinstance(payload, dict) else {"raw": payload}, (
            str(payload.get("message") or "perf-metrics/summary success=false")
            if isinstance(payload, dict)
            else "perf-metrics/summary 响应异常"
        )
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "读取 perf-metrics/summary 失败"
    return True, payload if isinstance(payload, dict) else {"success": True, "data": payload}, None


def fetch_newapi_perf_detail_for_site(
    site: Dict[str, Any], model_name: str, hours: int = 24, group: str = ""
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    hours = clamp_perf_hours(hours, 24)
    model_name = (model_name or "").strip()
    if not model_name:
        return False, {}, "model is required"
    qs = f"model={quote(model_name)}&hours={hours:g}"
    group = (group or "").strip()
    if group:
        qs += f"&group={quote(group)}"
    ok, payload, error = newapi_browser_request(
        site, "GET", "/api/perf-metrics", query=qs
    )
    if ok and (not isinstance(payload, dict) or not payload.get("success")):
        return False, payload if isinstance(payload, dict) else {"raw": payload}, (
            str(payload.get("message") or "perf-metrics success=false")
            if isinstance(payload, dict)
            else "perf-metrics 响应异常"
        )
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "读取 perf-metrics 失败"
    return True, payload if isinstance(payload, dict) else {"success": True, "data": payload}, None


# ---------------------------------------------------------------------------
# NewAPI 渠道管理（管理员接口薄代理）
#
# 凭「管理站点」(admin_sites) 存的管理员系统访问令牌，透传官方 /api/channel/* 接口，
# 与监控站点 (sites) 解耦。不改官方字段语义、不碰上游数据库。密钥默认掩码，仅单渠道详情返回明文。
# ---------------------------------------------------------------------------

CHANNEL_STATUS_LABELS = {
    1: "启用",
    2: "手动停用",
    3: "自动停用",
}


def mask_channel_key(key: Any) -> str:
    text = str(key or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "****"
    return f"{text[:4]}****{text[-4:]}"


def mask_channel_in_place(channel: Dict[str, Any]) -> Dict[str, Any]:
    """Return a shallow copy of a channel row with the key masked for list views."""
    safe = dict(channel)
    if "key" in safe:
        safe["key"] = mask_channel_key(safe.get("key"))
        safe["key_masked"] = True
    return safe


def newapi_admin_target(site: Dict[str, Any]) -> Tuple[str, Dict[str, str]]:
    return normalize_base_url(site["base_url"]), site_newapi_headers(site)


def get_admin_site_or_404(admin_site_id: int) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], int]:
    """Fetch a configured NewAPI or sub2api management site."""
    site = db_query_one("SELECT * FROM admin_sites WHERE id = ?", (admin_site_id,))
    if not site:
        return None, {"success": False, "message": "管理站点不存在"}, 404
    platform = admin_site_platform(site)
    if platform == "newapi" and not (
        site.get("access_token") and site.get("access_user_id")
    ):
        return None, {
            "success": False,
            "message": "该 NewAPI 主站未配置管理员系统访问令牌和用户 ID",
        }, 400
    if platform == "sub2api" and not (
        site.get("login_username") and site.get("login_password")
    ):
        return None, {
            "success": False,
            "message": "该 sub2api 主站未配置管理员邮箱和密码",
        }, 400
    return site, None, 200


def _upstream_response_message(payload: Any, error: Optional[str] = None) -> str:
    """Extract the useful upstream message without exposing credentials."""
    if isinstance(payload, dict):
        message = str(payload.get("message") or payload.get("error") or "").strip()
        if message:
            return message
        raw = payload.get("raw")
        if isinstance(raw, str) and raw:
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                message = str(parsed.get("message") or parsed.get("error") or "").strip()
                if message:
                    return message
    return str(error or "").strip()


def _upstream_response_details(
    payload: Any, error: Optional[str] = None
) -> Tuple[int, str, str]:
    status = 0
    code = ""
    message = _upstream_response_message(payload, error)
    if not isinstance(payload, dict):
        return status, code, message
    try:
        status = int(payload.get("status") or 0)
    except (TypeError, ValueError):
        status = 0
    if not status:
        match = re.search(r"\bHTTP\s+([1-5][0-9]{2})\b", str(error or ""), re.I)
        if match:
            status = int(match.group(1))
    code = str(payload.get("code") or "").strip()
    raw = payload.get("raw")
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            code = code or str(parsed.get("code") or "").strip()
            message = str(parsed.get("message") or message).strip()
    return status, code, message


def _admin_browser_refresh_error(payload: Any, error: Optional[str]) -> str:
    _status, code, message = _upstream_response_details(payload, error)
    if code == "AUTH_ORIGIN_FORBIDDEN":
        return "主站拒绝刷新登录态：Origin 不受信任，请检查主站 URL 和可信 Origin 配置"
    if code == "AUTH_SESSION_MISMATCH":
        return "主站 RT 与 Session 不一致，请重新完成主站网页登录和 2FA"
    if code in {"AUTH_SESSION_REVOKED", "AUTH_UNAUTHORIZED"}:
        return "主站网页登录 Session 已失效，请重新完成主站网页登录和 2FA"
    if code == "AUTH_REFRESH_RACE":
        return "主站登录态正在刷新，请稍后重试"
    return f"主站网页登录态刷新失败：{message or code or '未知错误'}"


def _admin_browser_session_lock(site_id: int) -> threading.RLock:
    with ADMIN_BROWSER_SESSION_LOCKS_GUARD:
        return ADMIN_BROWSER_SESSION_LOCKS.setdefault(site_id, threading.RLock())


def _admin_site_origin(base_url: str) -> str:
    try:
        parsed = urlparse(normalize_base_url(base_url))
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        parsed.port  # Access validates malformed or out-of-range ports.
    except (TypeError, ValueError):
        return ""
    if (
        scheme not in {"http", "https"}
        or not parsed.netloc
        or not hostname
        or parsed.username
        or parsed.password
    ):
        return ""
    return f"{scheme}://{parsed.netloc}"


def _cookie_header_from_response(headers: Dict[str, Any], previous: str = "") -> str:
    """Keep only cookie name/value pairs from Set-Cookie response headers."""
    raw_values = headers.get("set-cookie") if isinstance(headers, dict) else []
    if isinstance(raw_values, str):
        raw_values = [raw_values]
    cookie = SimpleCookie()
    for raw in raw_values or []:
        try:
            cookie.load(str(raw))
        except Exception:
            continue
    values = [f"{key}={morsel.value}" for key, morsel in cookie.items()]
    return "; ".join(values) or str(previous or "").strip()


def _admin_browser_auth_headers(site: Dict[str, Any]) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    access_token = str(site.get("browser_access_token") or "").strip()
    session_id = str(site.get("browser_session_id") or "").strip()
    refresh_cookie = str(site.get("browser_refresh_cookie") or "").strip()
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    if session_id:
        headers["X-Auth-Session"] = session_id
    if refresh_cookie:
        headers["Cookie"] = refresh_cookie
    return headers


def _persist_admin_browser_auth(
    site: Dict[str, Any],
    access_token: str,
    refresh_cookie: str,
    session_id: str,
    access_expires_at: Any,
) -> None:
    try:
        expires = int(access_expires_at or 0)
    except (TypeError, ValueError):
        expires = 0
    now = utc_now_iso()
    db_execute(
        """
        UPDATE admin_sites
        SET browser_access_token = ?, browser_refresh_cookie = ?, browser_session_id = ?,
            browser_access_expires_at = ?, browser_login_last_error = NULL,
            browser_login_last_check_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (access_token, refresh_cookie, session_id, expires, now, now, int(site["id"])),
    )
    site.update(
        {
            "browser_access_token": access_token,
            "browser_refresh_cookie": refresh_cookie,
            "browser_session_id": session_id,
            "browser_access_expires_at": expires,
            "browser_login_last_error": None,
            "browser_login_last_check_at": now,
        }
    )


def _persist_admin_browser_login_error(site: Dict[str, Any], message: str) -> None:
    now = utc_now_iso()
    db_execute(
        "UPDATE admin_sites SET browser_login_last_error = ?, browser_login_last_check_at = ?, updated_at = ? WHERE id = ?",
        (message, now, now, int(site["id"])),
    )
    site["browser_login_last_error"] = message
    site["browser_login_last_check_at"] = now


def _admin_browser_auth_data(
    site: Dict[str, Any], payload: Any, response_headers: Dict[str, Any]
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None, _upstream_response_message(payload, "主站登录没有返回认证数据")
    access_token = str(data.get("access_token") or "").strip()
    session = data.get("session") if isinstance(data.get("session"), dict) else {}
    session_id = str(session.get("sid") or "").strip()
    if not access_token or not session_id:
        return None, "主站登录没有返回有效的网页登录态"
    return {
        "access_token": access_token,
        "refresh_cookie": _cookie_header_from_response(
            response_headers, str(site.get("browser_refresh_cookie") or "")
        ),
        "session_id": session_id,
        "access_expires_at": data.get("access_expires_at") or 0,
    }, None


def refresh_admin_site_browser_session(
    site: Dict[str, Any], force: bool = False
) -> Tuple[bool, Optional[str]]:
    """Rotate the dashboard refresh cookie and persist the returned auth bundle."""
    site_id = int(site.get("id") or 0)
    if site_id <= 0:
        return False, "主站记录无效，无法刷新网页登录态"
    with _admin_browser_session_lock(site_id):
        def fail(message: str) -> Tuple[bool, Optional[str]]:
            _persist_admin_browser_login_error(site, message)
            return False, message

        previous_access_token = str(site.get("browser_access_token") or "").strip()
        latest = db_query_one(
            """
            SELECT browser_access_token, browser_refresh_cookie, browser_session_id,
                   browser_access_expires_at
            FROM admin_sites WHERE id = ?
            """,
            (site_id,),
        )
        if latest:
            site.update(latest)

        current_access_token = str(site.get("browser_access_token") or "").strip()
        try:
            expires_at = int(site.get("browser_access_expires_at") or 0)
        except (TypeError, ValueError):
            expires_at = 0
        now = int(time.time())
        another_caller_refreshed = bool(
            latest
            and previous_access_token
            and current_access_token
            and current_access_token != previous_access_token
        )
        if another_caller_refreshed or (not force and expires_at > now + 60):
            return True, None

        refresh_cookie = str(site.get("browser_refresh_cookie") or "").strip()
        session_id = str(site.get("browser_session_id") or "").strip()
        if not refresh_cookie or not session_id:
            return fail("主站网页登录态缺少 Refresh Cookie 或 Session ID")
        origin = _admin_site_origin(str(site.get("base_url") or ""))
        if not origin:
            return fail("主站 URL 无法生成有效 Origin，请检查主站地址")
        base = normalize_base_url(str(site.get("base_url") or ""))
        ok, payload, error, response_headers = request_json_with_headers(
            f"{base}/api/user/auth/refresh",
            headers={
                "Cookie": refresh_cookie,
                "X-Auth-Session": session_id,
                "Origin": origin,
            },
            method="POST",
        )
        if not ok or not isinstance(payload, dict) or not payload.get("success"):
            return fail(_admin_browser_refresh_error(payload, error))
        auth_data, auth_error = _admin_browser_auth_data(site, payload, response_headers)
        if not auth_data:
            return fail(auth_error or "主站刷新没有返回有效的网页登录态")
        _persist_admin_browser_auth(site, **auth_data)
        return True, None


def ensure_admin_site_browser_session(
    site: Dict[str, Any], verification_code: str = ""
) -> Tuple[bool, Optional[str]]:
    """Ensure the admin site has a dashboard session usable by /api/verify.

    NewAPI deliberately rejects PAT/system-token authentication for secure
    verification. The normal dashboard login produces a session-bound access
    token, which is what protected channel-key reads require.
    """
    now = int(time.time())
    access_token = str(site.get("browser_access_token") or "").strip()
    session_id = str(site.get("browser_session_id") or "").strip()
    try:
        expires_at = int(site.get("browser_access_expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0
    base = normalize_base_url(str(site.get("base_url") or ""))
    refresh_cookie = str(site.get("browser_refresh_cookie") or "").strip()
    has_existing_session = bool(access_token and session_id)

    # 部分 NewAPI 版本不返回 access_expires_at。此时 0 代表“未知”，不是“已过期”；
    # 只要已有 access token + session，就应继续复用，避免第二次读取又走密码登录，
    # 而密码登录在开启 2FA 时必然要求新的动态码。
    if has_existing_session and (expires_at <= 0 or expires_at > now + 60):
        return True, None

    refresh_error: Optional[str] = None
    if has_existing_session and base and refresh_cookie:
        refreshed, refresh_error = refresh_admin_site_browser_session(site)
        if refreshed:
            return True, None
        try:
            expires_at = int(site.get("browser_access_expires_at") or 0)
        except (TypeError, ValueError):
            expires_at = 0
    if has_existing_session and expires_at > now:
        return True, None

    # 已知过期且 refresh 失败时，后台刷新没有验证码可用，不能假装重新登录。
    # 明确要求通过页面重新完成一次网页登录/2FA，避免把“会话过期”误报成密码错误。
    if has_existing_session and expires_at > 0 and expires_at <= now + 60 and not verification_code:
        message = refresh_error or "主站网页登录 Session 已过期，请重新完成主站网页登录和 2FA 安全验证"
        if not refresh_error:
            _persist_admin_browser_login_error(site, message)
        return False, message

    username = str(site.get("login_username") or "").strip()
    password = str(site.get("login_password") or "")
    if not username or not password:
        message = "主站未配置网页登录账号和密码，无法完成 2FA 安全验证"
        _persist_admin_browser_login_error(site, message)
        return False, message

    ok, payload, error, response_headers = request_json_with_headers(
        f"{base}/api/user/login",
        payload={"username": username, "password": password},
        method="POST",
    )
    if not ok:
        message = _upstream_response_message(payload, error) or "主站网页登录失败"
        _persist_admin_browser_login_error(site, message)
        return False, message

    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict) and data.get("require_2fa"):
        flow_token = str(data.get("flow_token") or "").strip()
        if not verification_code:
            message = "主站网页登录需要 2FA 验证码"
            _persist_admin_browser_login_error(site, message)
            return False, message
        if not flow_token:
            message = "主站 2FA 登录流程已失效，请重新登录"
            _persist_admin_browser_login_error(site, message)
            return False, message
        ok, payload, error, response_headers = request_json_with_headers(
            f"{base}/api/user/login/2fa",
            payload={"code": verification_code, "flow_token": flow_token},
            method="POST",
        )
        if not ok:
            message = _upstream_response_message(payload, error) or "主站 2FA 登录失败"
            _persist_admin_browser_login_error(site, message)
            return False, message

    if not isinstance(payload, dict) or not payload.get("success"):
        message = _upstream_response_message(payload, "主站网页登录失败")
        _persist_admin_browser_login_error(site, message)
        return False, message
    auth_data, auth_error = _admin_browser_auth_data(site, payload, response_headers)
    if not auth_data:
        message = auth_error or "主站登录没有返回有效的网页登录态"
        _persist_admin_browser_login_error(site, message)
        return False, message
    _persist_admin_browser_auth(site, **auth_data)
    return True, None


def list_admin_sites_payload() -> List[Dict[str, Any]]:
    """List management sites for the UI. Token is never returned; only a flag."""
    rows = db_query_all("SELECT * FROM admin_sites ORDER BY id DESC")
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "platform": admin_site_platform(r),
            "platform_label": (
                "sub2api" if admin_site_platform(r) == "sub2api" else "NewAPI"
            ),
            "capabilities": admin_site_capabilities(r),
            "base_url": r["base_url"],
            "access_user_id": r.get("access_user_id") or "",
            "has_access_token": bool(r.get("access_token")),
            "login_username": r.get("login_username") or "",
            "has_login_password": bool(r.get("login_password")),
            "has_sub2api_session": bool(
                r.get("sub2api_access_token") and r.get("sub2api_refresh_token")
            ),
            "login_last_error": r.get("browser_login_last_error"),
            "login_last_check_at": r.get("browser_login_last_check_at"),
            "has_security_proof": bool(r.get("security_proof")),
            "created_at": r.get("created_at"),
            "updated_at": r.get("updated_at"),
        }
        for r in rows
    ]


def create_admin_site(body: Dict[str, Any]) -> Tuple[bool, Optional[int], Optional[str]]:
    name = str(body.get("name") or "").strip()
    platform = str(body.get("platform") or "newapi").strip().lower()
    if platform not in ADMIN_SITE_CAPABILITIES:
        return False, None, "主站平台只支持 NewAPI 或 sub2api"
    base_url, base_url_error = validate_admin_site_base_url(
        str(body.get("base_url") or "")
    )
    if base_url_error:
        return False, None, base_url_error
    access_token = str(body.get("access_token") or "").strip()
    access_user_id = str(body.get("access_user_id") or "").strip()
    login_username = str(body.get("login_username") or "").strip()
    login_password = str(body.get("login_password") or "")
    if not name or not base_url:
        return False, None, "请填写管理站点名称和 Base URL"
    if platform == "newapi" and (not access_token or not access_user_id):
        return False, None, "请填写管理员系统访问令牌和 NewAPI 用户 ID"
    auth: Dict[str, Any] = {}
    if platform == "sub2api":
        if not login_username or not login_password:
            return False, None, "请填写 sub2api 管理员邮箱和密码"
        logged_in, auth, login_error = sub2api_admin_login(
            base_url, login_username, login_password
        )
        if not logged_in:
            return (
                False,
                None,
                Sub2ApiUpstreamError(
                    login_error or "sub2api 主站登录失败", auth
                ),
            )
    now = utc_now_iso()
    admin_site_id = db_execute(
        """
        INSERT INTO admin_sites (
            name, platform, base_url, access_token, access_user_id,
            login_username, login_password, sub2api_access_token,
            sub2api_refresh_token, sub2api_access_expires_at,
            browser_login_last_error, browser_login_last_check_at,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
        """,
        (
            name,
            platform,
            base_url,
            access_token if platform == "newapi" else "",
            access_user_id if platform == "newapi" else "",
            login_username,
            login_password,
            str(auth.get("access_token") or ""),
            str(auth.get("refresh_token") or ""),
            int(auth.get("access_expires_at") or 0),
            now if platform == "sub2api" else None,
            now,
            now,
        ),
    )
    return True, admin_site_id, None


def update_admin_site(admin_site_id: int, body: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Patch a management site. Token / user id left blank = keep existing (so the
    edit form never has to re-enter the admin token just to rename the site)."""
    existing = db_query_one("SELECT * FROM admin_sites WHERE id = ?", (admin_site_id,))
    if not existing:
        return False, "管理站点不存在"
    platform = admin_site_platform(existing)
    if "platform" in body:
        requested_platform = str(body.get("platform") or "").strip().lower()
        if requested_platform != platform:
            return False, "主站平台创建后不可修改"

    fields: List[str] = []
    params: List[Any] = []
    if "name" in body:
        name = str(body.get("name") or "").strip()
        if not name:
            return False, "名称不能为空"
        fields.append("name = ?")
        params.append(name)
    next_base_url = str(existing.get("base_url") or "")
    if "base_url" in body:
        base_url, base_url_error = validate_admin_site_base_url(
            str(body.get("base_url") or "")
        )
        if base_url_error:
            return False, base_url_error
        next_base_url = base_url
        fields.append("base_url = ?")
        params.append(base_url)

    if platform == "sub2api":
        next_username = (
            str(body.get("login_username") or "").strip()
            if "login_username" in body
            else str(existing.get("login_username") or "").strip()
        )
        submitted_password = (
            str(body.get("login_password") or "")
            if "login_password" in body
            else ""
        )
        next_password = submitted_password or str(
            existing.get("login_password") or ""
        )
        if not next_username or not next_password:
            return False, "sub2api 主站需要管理员邮箱和密码"
        credentials_changed = (
            next_base_url != str(existing.get("base_url") or "")
            or next_username
            != str(existing.get("login_username") or "").strip()
            or bool(
                submitted_password
                and submitted_password != str(existing.get("login_password") or "")
            )
        )
        auth: Dict[str, Any] = {}
        if credentials_changed:
            logged_in, auth, login_error = sub2api_admin_login(
                next_base_url, next_username, next_password
            )
            if not logged_in:
                return False, Sub2ApiUpstreamError(
                    login_error or "sub2api 主站登录失败", auth
                )
        if "login_username" in body:
            fields.append("login_username = ?")
            params.append(next_username)
        if submitted_password:
            fields.append("login_password = ?")
            params.append(submitted_password)
        if credentials_changed:
            fields.extend(
                [
                    "sub2api_access_token = ?",
                    "sub2api_refresh_token = ?",
                    "sub2api_access_expires_at = ?",
                    "browser_login_last_error = NULL",
                    "browser_login_last_check_at = ?",
                ]
            )
            params.extend(
                [
                    str(auth.get("access_token") or ""),
                    str(auth.get("refresh_token") or ""),
                    int(auth.get("access_expires_at") or 0),
                    utc_now_iso(),
                ]
            )
        if not fields:
            return False, "没有要更新的字段"
        fields.append("updated_at = ?")
        params.append(utc_now_iso())
        params.append(admin_site_id)
        db_execute(f"UPDATE admin_sites SET {', '.join(fields)} WHERE id = ?", params)
        return True, None

    access_user_id = str(body.get("access_user_id") or "").strip()
    if access_user_id:
        fields.append("access_user_id = ?")
        params.append(access_user_id)
    access_token = str(body.get("access_token") or "").strip()
    if access_token:
        fields.append("access_token = ?")
        params.append(access_token)
    login_credentials_changed = False
    if "login_username" in body:
        new_username = str(body.get("login_username") or "").strip()
        fields.append("login_username = ?")
        params.append(new_username)
        login_credentials_changed = new_username != str(existing.get("login_username") or "").strip()
    if "login_password" in body and str(body.get("login_password") or ""):
        new_password = str(body.get("login_password") or "")
        fields.append("login_password = ?")
        params.append(new_password)
        login_credentials_changed = login_credentials_changed or (
            new_password != str(existing.get("login_password") or "")
        )
    if login_credentials_changed:
        fields.extend([
            "browser_access_token = NULL",
            "browser_refresh_cookie = NULL",
            "browser_session_id = NULL",
            "browser_access_expires_at = NULL",
            "browser_login_last_error = NULL",
            "security_proof = NULL",
        ])
    if not fields:
        return False, "没有要更新的字段"
    fields.append("updated_at = ?")
    params.append(utc_now_iso())
    params.append(admin_site_id)
    db_execute(f"UPDATE admin_sites SET {', '.join(fields)} WHERE id = ?", params)
    return True, None


def test_admin_site_connection(
    body: Dict[str, Any]
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    saved: Optional[Dict[str, Any]] = None
    try:
        admin_site_id = int(body.get("admin_site_id") or 0)
    except (TypeError, ValueError):
        return False, {"error_source": "local"}, "管理站点 ID 无效"
    if admin_site_id:
        saved = db_query_one(
            "SELECT * FROM admin_sites WHERE id = ?", (admin_site_id,)
        )
        if not saved:
            return False, {"error_source": "local"}, "管理站点不存在"
    platform = str(
        body.get("platform") or (saved or {}).get("platform") or "newapi"
    ).strip().lower()
    if platform not in ADMIN_SITE_CAPABILITIES:
        return False, {"error_source": "local"}, "主站平台无效"
    if saved and platform != admin_site_platform(saved):
        return False, {"error_source": "local"}, "主站平台与已保存配置不一致"
    base_url, base_url_error = validate_admin_site_base_url(
        str(body.get("base_url") or (saved or {}).get("base_url") or "")
    )
    if base_url_error:
        return False, {"error_source": "local"}, base_url_error

    if platform == "newapi":
        access_token = str(
            body.get("access_token") or (saved or {}).get("access_token") or ""
        ).strip()
        access_user_id = str(
            body.get("access_user_id")
            or (saved or {}).get("access_user_id")
            or ""
        ).strip()
        if not access_token or not access_user_id:
            return (
                False,
                {"error_source": "local"},
                "请填写管理员系统访问令牌和 NewAPI 用户 ID",
            )
        ok, payload, error = fetch_newapi_groups_with_access_token(
            base_url, access_token, access_user_id
        )
        groups = parse_groups_payload(payload) if ok and isinstance(payload, dict) else {}
        return ok, {"platform": "newapi", "groups_count": len(groups)}, error

    login_username = str(
        body.get("login_username") or (saved or {}).get("login_username") or ""
    ).strip()
    login_password = str(
        body.get("login_password") or (saved or {}).get("login_password") or ""
    )
    if not login_username or not login_password:
        return (
            False,
            {"error_source": "local"},
            "sub2api 主站需要管理员邮箱和密码",
        )
    ok, auth, error = sub2api_admin_login(
        base_url, login_username, login_password
    )
    if not ok:
        return False, {"error_source": "upstream", "details": auth}, error
    channels_ok, channels, upstream, channels_error = (
        fetch_sub2api_admin_channels_by_token(
            base_url, str(auth.get("access_token") or "")
        )
    )
    if not channels_ok:
        return (
            False,
            {"error_source": "upstream", "details": upstream},
            channels_error,
        )
    return True, {
        "platform": "sub2api",
        "channels_count": len(channels),
    }, None


def verify_admin_site_channel_key_access(
    admin_site_id: int, code: str
) -> Tuple[bool, Optional[str]]:
    """Issue NewAPI's short-lived proof required by POST /channel/:id/key."""
    site = db_query_one("SELECT * FROM admin_sites WHERE id = ?", (admin_site_id,))
    if not site:
        return False, "管理站点不存在"
    if not site.get("access_token") or not site.get("access_user_id"):
        return False, "主站未配置系统访问令牌和用户 ID"
    code = str(code or "").strip()
    if not code:
        return False, "请输入主站 2FA 验证码"
    browser_ok, browser_error = ensure_admin_site_browser_session(site, code)
    if not browser_ok:
        return False, browser_error or "主站网页登录失败"
    base = normalize_base_url(site["base_url"])
    headers = _admin_browser_auth_headers(site)
    ok, payload, error = request_json(
        f"{base}/api/verify",
        headers=headers,
        payload={"method": "2fa", "code": code, "scope": "channel.key.read"},
        method="POST",
    )
    if not ok:
        return False, error or "主站安全验证失败"
    data = payload.get("data") if isinstance(payload, dict) else None
    proof = data.get("proof_token") if isinstance(data, dict) else ""
    if not isinstance(payload, dict) or not payload.get("success") or not proof:
        message = str(payload.get("message")) if isinstance(payload, dict) else "主站安全验证失败"
        return False, message or "主站安全验证失败"
    db_execute(
        "UPDATE admin_sites SET security_proof = ?, updated_at = ? WHERE id = ?",
        (str(proof).strip(), utc_now_iso(), admin_site_id),
    )
    return True, None


def channel_admin_error_message(error: Optional[str], payload: Any = None) -> str:
    """把上游 401/403（系统令牌不是管理员/权限不足）翻译成可操作的明确提示，
    避免用户只看到一个笼统的 502/HTTP 401。"""
    text = str(error or "")
    blob = text
    if isinstance(payload, (dict, list)):
        try:
            blob += " " + json.dumps(payload, ensure_ascii=False)
        except Exception:
            pass
    lower = blob.lower()
    if (
        "401" in blob
        or "403" in blob
        or "unauthorized" in lower
        or "forbidden" in lower
        or "无权" in blob
        or "权限" in blob
    ):
        return (
            "当前系统访问令牌不是管理员或权限不足，无法管理渠道："
            "请在「站点监控」编辑该站点，把系统访问令牌换成管理员用户的令牌"
        )
    return text or "上游渠道接口调用失败"


def _newapi_channel_list_items(payload: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Normalize both list shapes: data=[...] (one-api) and data={items:[...], total,...}."""
    data = payload.get("data") if isinstance(payload, dict) else None
    meta: Dict[str, Any] = {}
    items: List[Dict[str, Any]] = []
    if isinstance(data, list):
        items = [x for x in data if isinstance(x, dict)]
    elif isinstance(data, dict):
        raw_items = data.get("items")
        if isinstance(raw_items, list):
            items = [x for x in raw_items if isinstance(x, dict)]
        for k in ("total", "page", "page_size"):
            if k in data:
                meta[k] = data[k]
    return items, meta


def fetch_newapi_channels(
    site: Dict[str, Any],
    page: int = 0,
    page_size: int = 20,
    keyword: str = "",
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    base, headers = newapi_admin_target(site)
    keyword = (keyword or "").strip()
    if keyword:
        url = f"{base}/api/channel/search?keyword={quote(keyword)}"
    else:
        url = f"{base}/api/channel/?p={int(page)}&page_size={int(page_size)}"
    ok, payload, error = request_json(url, headers=headers)
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "读取渠道列表失败"
    if not isinstance(payload, dict) or not payload.get("success"):
        msg = str(payload.get("message")) if isinstance(payload, dict) else "渠道列表响应异常"
        return False, payload if isinstance(payload, dict) else {"raw": payload}, msg or "渠道列表 success=false"
    return True, payload, None


def fetch_newapi_channel_detail(
    site: Dict[str, Any], channel_id: int
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    base, headers = newapi_admin_target(site)
    ok, payload, error = request_json(f"{base}/api/channel/{int(channel_id)}", headers=headers)
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "读取渠道详情失败"
    if not isinstance(payload, dict) or not payload.get("success"):
        msg = str(payload.get("message")) if isinstance(payload, dict) else "渠道详情响应异常"
        return False, payload if isinstance(payload, dict) else {"raw": payload}, msg or "渠道详情 success=false"
    return True, payload, None


def site_newapi_channel_key_headers(site: Dict[str, Any]) -> Dict[str, str]:
    """Use the browser session for protected key reads when available."""
    browser_headers = _admin_browser_auth_headers(site)
    headers = browser_headers or site_newapi_headers(site)
    proof = str(site.get("security_proof") or "").strip()
    if proof:
        headers["X-Security-Proof"] = proof
    return headers


def fetch_newapi_channel_key(
    site: Dict[str, Any], channel_id: int, clear_admin_proof: bool = True,
    force_refresh: bool = False,
) -> Tuple[bool, str, Optional[str]]:
    """Read a NewAPI channel key through the dedicated protected endpoint.

    GET /api/channel/:id intentionally clears ``key``. NewAPI exposes the real
    value only through POST /api/channel/:id/key after the main-site account
    has passed its security verification.
    """
    cache_key = "|".join(
        (
            normalize_base_url(str(site.get("base_url") or "")),
            str(site.get("id") or "0"),
            str(int(channel_id)),
        )
    )
    admin_site = is_admin_site_row(site)
    if admin_site and not force_refresh:
        persisted_key = get_cached_admin_channel_key(int(site["id"]), int(channel_id))
        if persisted_key:
            with MAIN_CHANNEL_KEY_REQUEST_LOCK:
                MAIN_CHANNEL_KEY_CACHE[cache_key] = {
                    "key": persisted_key,
                    "updated_monotonic": time.monotonic(),
                }
            return True, persisted_key, None
    if not force_refresh:
        with MAIN_CHANNEL_KEY_REQUEST_LOCK:
            cached = MAIN_CHANNEL_KEY_CACHE.get(cache_key)
            if cached and time.monotonic() - float(cached.get("updated_monotonic") or 0) < MAIN_CHANNEL_KEY_CACHE_TTL_SECONDS:
                return True, str(cached.get("key") or ""), None

    # Admin-site rows carry browser session fields; refresh the short-lived
    # dashboard access token before every protected key read. Monitoring-site
    # rows do not have these fields and continue using their normal PAT.
    if "browser_access_token" in site or "browser_session_id" in site:
        browser_ok, browser_error = ensure_admin_site_browser_session(site)
        if not browser_ok:
            return False, "", browser_error or "主站网页登录态不可用"
    base = normalize_base_url(site["base_url"])
    headers = site_newapi_channel_key_headers(site)
    request_gate_key = f"{int(site.get('id') or 0)}|{base}"
    with MAIN_CHANNEL_KEY_REQUEST_LOCK:
        cooldown_until = MAIN_CHANNEL_KEY_RATE_LIMIT_UNTIL.get(request_gate_key, 0.0)
        if cooldown_until > time.monotonic():
            wait_seconds = max(1, int(cooldown_until - time.monotonic()))
            return False, "", f"主站 key 接口触发限流（HTTP 429），已暂停请求，请等待约 {wait_seconds} 秒后再刷新"
        elapsed = time.monotonic() - MAIN_CHANNEL_KEY_LAST_REQUEST_AT.get(request_gate_key, 0.0)
        wait_seconds = MAIN_CHANNEL_KEY_MIN_INTERVAL_SECONDS - elapsed
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        MAIN_CHANNEL_KEY_LAST_REQUEST_AT[request_gate_key] = time.monotonic()
        ok, payload, error = request_json(
            f"{base}/api/channel/{int(channel_id)}/key",
            headers=headers,
            method="POST",
        )
        status, response_code, _response_message = _upstream_response_details(
            payload, error
        )
        if (
            not ok
            and admin_site
            and status == 401
            and response_code == "AUTH_TOKEN_EXPIRED"
        ):
            refreshed, refresh_error = refresh_admin_site_browser_session(
                site, force=True
            )
            if not refreshed:
                return False, "", refresh_error or "主站网页登录态刷新失败"
            headers = site_newapi_channel_key_headers(site)
            ok, payload, error = request_json(
                f"{base}/api/channel/{int(channel_id)}/key",
                headers=headers,
                method="POST",
            )
    if not ok:
        raw_message = ""
        if isinstance(payload, dict):
            if int(payload.get("status") or 0) == 429:
                with MAIN_CHANNEL_KEY_REQUEST_LOCK:
                    MAIN_CHANNEL_KEY_RATE_LIMIT_UNTIL[request_gate_key] = (
                        time.monotonic() + MAIN_CHANNEL_KEY_RATE_LIMIT_COOLDOWN_SECONDS
                    )
                return False, "", "主站 key 接口触发限流（HTTP 429），已暂停请求，请等待约 30 秒后再刷新"
            raw = payload.get("raw")
            try:
                parsed_raw = json.loads(raw) if isinstance(raw, str) and raw else {}
                raw_message = str(parsed_raw.get("message") or "") if isinstance(parsed_raw, dict) else ""
                raw_code = str(parsed_raw.get("code") or "") if isinstance(parsed_raw, dict) else ""
                if raw_code in {"SECURITY_PROOF_REQUIRED", "SECURITY_PROOF_INVALID", "SECURITY_PROOF_EXPIRED"}:
                    if clear_admin_proof:
                        db_execute(
                            "UPDATE admin_sites SET security_proof = NULL, updated_at = ? WHERE id = ?",
                            (utc_now_iso(), int(site["id"])),
                        )
                    with MAIN_CHANNEL_KEY_REQUEST_LOCK:
                        MAIN_CHANNEL_KEY_CACHE.pop(cache_key, None)
                    proof_messages = {
                        "SECURITY_PROOF_REQUIRED": "主站尚未完成 key 读取安全验证",
                        "SECURITY_PROOF_INVALID": "主站网页登录 Session 或安全验证 proof 已失效",
                        "SECURITY_PROOF_EXPIRED": "主站 key 读取安全验证已过期",
                    }
                    return False, "", proof_messages.get(raw_code, "主站安全验证失败")
            except (TypeError, ValueError):
                pass
        return False, "", raw_message or error or "读取主站渠道 key 失败"
    if not isinstance(payload, dict) or not payload.get("success"):
        message = str(payload.get("message")) if isinstance(payload, dict) else "主站 key 接口响应异常"
        return False, "", message or "读取主站渠道 key 失败"
    data = payload.get("data")
    key = data.get("key") if isinstance(data, dict) else ""
    if _channel_key_is_masked(key):
        return False, "", "主站 key 接口没有返回明文 key"
    key = str(key).strip()
    with MAIN_CHANNEL_KEY_REQUEST_LOCK:
        MAIN_CHANNEL_KEY_CACHE[cache_key] = {
            "key": key,
            "updated_monotonic": time.monotonic(),
        }
    if admin_site:
        persist_admin_channel_key(int(site["id"]), int(channel_id), key)
    return True, key, None


def create_newapi_channel(
    site: Dict[str, Any], body: Dict[str, Any]
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """新增渠道。

    上游要的是 `{"mode": "single", "channel": {...}}` 这个信封，直接把渠道对象平铺发过去
    会被拒：`{"success": false, "message": "channel cannot be empty"}`。信封形状取自上游
    后台自己的前端（`POST /api/channel` 带 {mode, channel}），"mode" 只认 single/batch。
    """
    base, headers = newapi_admin_target(site)
    envelope = body if "channel" in body and "mode" in body else {"mode": "single", "channel": body}
    ok, payload, error = request_json(f"{base}/api/channel/", headers=headers, payload=envelope, method="POST")
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "创建渠道失败"
    if not isinstance(payload, dict) or not payload.get("success"):
        msg = str(payload.get("message")) if isinstance(payload, dict) else "创建渠道响应异常"
        return False, payload if isinstance(payload, dict) else {"raw": payload}, msg or "创建渠道 success=false"
    return True, payload, None


def resolve_created_newapi_channel_id(
    site: Dict[str, Any], body: Dict[str, Any], existing_ids: Iterable[int]
) -> Tuple[Optional[int], Optional[str]]:
    """Resolve a newly created channel when NewAPI omits its ID in the response.

    The candidate must be new relative to the pre-create list and match the
    submitted name/base URL. Never guess between multiple candidates.
    """
    ok, items, error = fetch_all_newapi_channels(site)
    if not ok:
        return None, error or "创建成功，但刷新渠道列表失败，无法确认新渠道 ID"
    known_ids = {int(value) for value in existing_ids}
    wanted_name = str(body.get("name") or "").strip()
    wanted_base = normalize_base_url(str(body.get("base_url") or ""))
    candidates: List[int] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            channel_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if channel_id in known_ids:
            continue
        if str(item.get("name") or "").strip() != wanted_name:
            continue
        if normalize_base_url(str(item.get("base_url") or "")) != wanted_base:
            continue
        candidates.append(channel_id)
    if len(candidates) == 1:
        return candidates[0], None
    if not candidates:
        return None, "创建成功，但未能从渠道列表确认新渠道 ID"
    return None, "创建成功，但匹配到多个同名同地址渠道，未安全写入 key 缓存"


def update_newapi_channel(
    site: Dict[str, Any], channel_id: int, patch: Dict[str, Any]
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """Read-merge-write: NewAPI PUT replaces the whole channel, so we fetch the
    current object, apply only the provided fields, and send the merged result.

    两个上游事实，踩过才知道（在 aiinfinite.online / NewAPI 上逐字段二分确认）：

    1. `PUT /api/channel/` 只要 body 里带 `status`，就无条件返回
       `{"success": false, "message": "Invalid parameters"}`——渠道状态不允许走通用更新
       接口改。因为我们是「读回整个渠道再回传」，而读回来的对象必然含 status，于是
       **所有**更新（权重 / 优先级 / 编辑保存 / 切换状态）全都失败。所以这里把 status
       从 PUT body 里剔除，改走专用的 `POST /api/channel/:id/status`（见
       set_newapi_channel_status）。
    2. `GET /api/channel/:id` 不返回明文密钥（key 为空串）。空 key 原样回传虽然不是
       上面那个报错的原因，但会有把密钥清空的风险，所以空 key 也一并剔除。

    合并的基底刻意是「上游回来的整个对象」而不是一份白名单：NewAPI 的 PUT 是整体替换，
    白名单漏掉任何一个可写字段（如 settings / header_override / remark）都会把它清空。
    只剔除确定不能回传的：status、空 key，以及 balance / used_quota 这类派生只读字段。
    """
    ok, detail, error = fetch_newapi_channel_detail(site, channel_id)
    if not ok:
        return False, detail, error
    current = detail.get("data") if isinstance(detail, dict) else None
    if not isinstance(current, dict):
        return False, detail, "渠道详情缺少 data，无法合并更新"

    # only touch caller-provided, whitelisted fields; keep everything else intact
    allowed = {
        "name", "status", "weight", "priority", "group", "groups", "models",
        "base_url", "key", "type", "model_mapping", "tag", "test_model",
        "auto_ban", "other", "setting", "settings", "param_override",
        "status_code_mapping", "header_override", "remark", "openai_organization",
    }
    merged = dict(current)
    for field, value in patch.items():
        if field in allowed:
            merged[field] = value
    merged["id"] = int(channel_id)

    # 上游派生的只读字段，回传没有意义，也避免上游对它们做校验
    for derived in (
        "balance", "balance_updated_time", "used_quota", "created_time",
        "test_time", "response_time", "other_info", "channel_info",
    ):
        merged.pop(derived, None)
    # status 不能经此接口更新（见 docstring 第 1 条）
    status_requested = merged.pop("status", None) if "status" in patch else None
    merged.pop("status", None)
    # 上游不回明文密钥；空 key 别回传，避免把密钥清空
    if not str(merged.get("key") or "").strip():
        merged.pop("key", None)

    other_fields = {k: v for k, v in patch.items() if k in allowed and k != "status"}

    status_payload: Optional[Dict[str, Any]] = None
    if status_requested is not None:
        ok, status_payload, error = set_newapi_channel_status(site, channel_id, status_requested)
        if not ok:
            return False, status_payload, error
        if not other_fields:
            return True, status_payload, None

    base, headers = newapi_admin_target(site)
    ok, payload, error = request_json(f"{base}/api/channel/", headers=headers, payload=merged, method="PUT")
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "更新渠道失败"
    if not isinstance(payload, dict) or not payload.get("success"):
        msg = str(payload.get("message")) if isinstance(payload, dict) else "更新渠道响应异常"
        return False, payload if isinstance(payload, dict) else {"raw": payload}, msg or "更新渠道 success=false"
    return True, payload, None


def set_newapi_channel_status(
    site: Dict[str, Any], channel_id: int, status: Any
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """启用 / 停用单个渠道：`POST /api/channel/:id/status`，body `{"status": 1|2}`。

    这是上游后台自己前端用的端点（通用 PUT 拒收 status，见 update_newapi_channel）。
    只接受 int 1（启用）/ 2（手动停用）：传字符串 "2"、布尔、0、3 都会被判 Invalid
    parameters（3 = 自动停用是上游自己置的，不允许外部设置）。

    注意：同族的 `POST /api/channel/status/batch` 虽然回 success=true，实测并不落库，
    所以批量启停仍然逐个调用本函数，别改成那个批量端点。
    """
    try:
        wanted = int(status)
    except (TypeError, ValueError):
        return False, {}, f"状态值无效：{status!r}"
    if wanted not in (1, 2):
        return False, {}, f"只支持启用(1)/停用(2)，收到 {wanted}（3=自动停用由上游自行置位）"
    base, headers = newapi_admin_target(site)
    ok, payload, error = request_json(
        f"{base}/api/channel/{int(channel_id)}/status",
        headers=headers,
        payload={"status": wanted},
        method="POST",
    )
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "切换渠道状态失败"
    if not isinstance(payload, dict) or not payload.get("success"):
        msg = str(payload.get("message")) if isinstance(payload, dict) else "切换状态响应异常"
        return False, payload if isinstance(payload, dict) else {"raw": payload}, msg or "切换状态 success=false"
    return True, payload, None


def delete_newapi_channel(
    site: Dict[str, Any], channel_id: int
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    base, headers = newapi_admin_target(site)
    ok, payload, error = request_json(f"{base}/api/channel/{int(channel_id)}", headers=headers, method="DELETE")
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "删除渠道失败"
    if not isinstance(payload, dict) or not payload.get("success"):
        msg = str(payload.get("message")) if isinstance(payload, dict) else "删除渠道响应异常"
        return False, payload if isinstance(payload, dict) else {"raw": payload}, msg or "删除渠道 success=false"
    return True, payload, None


# set_tag 为覆盖标签的规范名；add_tag 保留为向后兼容别名（行为同 set_tag）。
BATCH_CHANNEL_ACTIONS = {"enable", "disable", "delete", "set_group", "set_tag", "add_tag"}


def batch_channel_operation(
    site: Dict[str, Any],
    action: str,
    ids: Any,
    params: Dict[str, Any],
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """对多个渠道执行同一操作。复用单渠道的 read-merge-write / delete 助手，
    逐个执行并汇总每个渠道的成功/失败，任一失败不影响其余。"""
    action = (action or "").strip()
    if action not in BATCH_CHANNEL_ACTIONS:
        return False, {}, f"不支持的批量操作：{action or '(空)'}"
    if not isinstance(ids, list) or not ids:
        return False, {}, "未选择任何渠道"
    group_value = str(params.get("group") or "").strip()
    tag_value = str(params.get("tag") or "").strip()
    if action == "set_group" and not group_value:
        return False, {}, "请提供要设置的分组名"
    if action in ("set_tag", "add_tag") and not tag_value:
        return False, {}, "请提供要设置的标签"

    results: List[Dict[str, Any]] = []
    ok_count = 0
    for raw_id in ids:
        try:
            channel_id = int(raw_id)
        except (TypeError, ValueError):
            results.append({"id": raw_id, "ok": False, "message": "无效渠道 ID"})
            continue
        if action == "delete":
            ok, _payload, error = delete_newapi_channel(site, channel_id)
        else:
            patch: Dict[str, Any] = {}
            if action == "enable":
                patch["status"] = 1
            elif action == "disable":
                patch["status"] = 2
            elif action == "set_group":
                patch["group"] = group_value
            elif action in ("set_tag", "add_tag"):
                patch["tag"] = tag_value
            ok, _payload, error = update_newapi_channel(site, channel_id, patch)
        if ok:
            ok_count += 1
        results.append({"id": channel_id, "ok": ok, "message": None if ok else error})

    return True, {
        "action": action,
        "ok_count": ok_count,
        "fail_count": len(results) - ok_count,
        "total": len(results),
        "results": results,
    }, None


def test_newapi_channel(
    site: Dict[str, Any], channel_id: int
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    base, headers = newapi_admin_target(site)
    ok, payload, error = request_json(f"{base}/api/channel/test/{int(channel_id)}", headers=headers)
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "测试渠道失败"
    return True, payload if isinstance(payload, dict) else {"success": True, "data": payload}, None


def split_channel_groups(value: Any) -> List[str]:
    if isinstance(value, list):
        raw = value
    else:
        raw = str(value or "").replace("\n", ",").split(",")
    return [str(item).strip() for item in raw if str(item).strip()]


def get_channel_upstream_binding(admin_site_id: int, channel_id: int) -> Optional[Dict[str, Any]]:
    return db_query_one(
        "SELECT * FROM channel_upstream_bindings WHERE admin_site_id = ? AND channel_id = ?",
        (admin_site_id, channel_id),
    )


def channel_upstream_binding_payload(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not row:
        return {
            "configured": False,
            "match_status": "unmatched",
            "matched_groups": [],
        }
    matched_groups: Any = []
    try:
        matched_groups = json.loads(row.get("matched_groups_json") or "[]")
    except (TypeError, ValueError):
        matched_groups = []
    if not isinstance(matched_groups, list):
        matched_groups = []
    return {
        "configured": bool(row.get("upstream_base_url")),
        "upstream_base_url": row.get("upstream_base_url") or "",
        "upstream_platform": row.get("upstream_platform") or "newapi",
        "auth_mode": row.get("auth_mode") or "token",
        "has_login_username": bool(row.get("login_username")),
        "has_login_password": bool(row.get("login_password")),
        "has_access_token": bool(row.get("access_token")),
        "has_refresh_token": bool(row.get("refresh_token")),
        "access_user_id": row.get("access_user_id") or "",
        "has_channel_key": bool(row.get("channel_key")),
        "match_status": row.get("match_status") or "unmatched",
        "match_message": row.get("match_message") or "",
        "matched_groups": matched_groups,
        "matched_at": row.get("matched_at"),
    }


def list_channel_upstream_bindings(admin_site_id: int) -> Dict[str, Dict[str, Any]]:
    rows = db_query_all(
        "SELECT * FROM channel_upstream_bindings WHERE admin_site_id = ?",
        (admin_site_id,),
    )
    return {str(row["channel_id"]): channel_upstream_binding_payload(row) for row in rows}


def save_channel_upstream_binding(
    admin_site_id: int, channel_id: int, body: Dict[str, Any]
) -> Tuple[bool, Optional[str]]:
    existing = get_channel_upstream_binding(admin_site_id, channel_id) or {}
    platform = str(body.get("upstream_platform") or existing.get("upstream_platform") or "newapi").strip().lower()
    auth_mode = str(body.get("auth_mode") or existing.get("auth_mode") or "token").strip().lower()
    if platform not in {"newapi", "sub2api"}:
        return False, "上游平台只支持 NewAPI 或 sub2api"
    if auth_mode not in {"password", "token"}:
        return False, "上游认证方式无效"

    existing_platform = str(existing.get("upstream_platform") or "newapi").strip().lower()
    existing_auth_mode = str(existing.get("auth_mode") or "token").strip().lower()
    same_platform = existing_platform == platform
    same_auth_mode = same_platform and existing_auth_mode == auth_mode

    def merged_text(name: str, preserve_existing: bool = False) -> str:
        value = str(body.get(name) or "").strip()
        if value:
            return value
        return str(existing.get(name) or "").strip() if preserve_existing else ""

    base_url = normalize_base_url(str(body.get("upstream_base_url") or existing.get("upstream_base_url") or ""))
    username = merged_text(
        "login_username",
        same_auth_mode and auth_mode == "password",
    )
    password = merged_text(
        "login_password",
        same_auth_mode and auth_mode == "password",
    )
    access_token = merged_text(
        "access_token",
        same_platform and (platform == "newapi" or (auth_mode == "token" and same_auth_mode)),
    )
    access_user_id = merged_text("access_user_id", same_platform and platform == "newapi")
    refresh_token = merged_text(
        "refresh_token",
        same_auth_mode and platform == "sub2api" and auth_mode == "token",
    )
    channel_key = merged_text("channel_key", True)

    # 不把另一套协议或另一种认证模式的凭据继续写回。除避免误用外，也让
    # has_* 状态准确反映当前实际配置。
    if platform == "newapi":
        refresh_token = ""
        if auth_mode == "token":
            username = ""
            password = ""
        else:
            access_token = ""
            access_user_id = ""
    else:
        access_user_id = ""
        if auth_mode == "password":
            access_token = ""
            refresh_token = ""
        else:
            username = ""
            password = ""

    if base_url and platform == "newapi":
        if auth_mode == "token" and (not access_token or not access_user_id):
            return False, "NewAPI 上游匹配需要系统访问令牌和用户 ID"
        if auth_mode == "password" and (not username or not password):
            return False, "NewAPI 上游匹配需要用户名和密码"
    if base_url and platform == "sub2api":
        if auth_mode == "password" and (not username or not password):
            return False, "sub2api 上游匹配需要用户邮箱和密码"
        if auth_mode == "token" and not access_token:
            return False, "sub2api 上游匹配需要 auth_token"

    now = utc_now_iso()
    db_execute(
        """
        INSERT INTO channel_upstream_bindings
        (admin_site_id, channel_id, upstream_base_url, upstream_platform, auth_mode,
         login_username, login_password, access_token, access_user_id, refresh_token,
         channel_key, match_status, match_message, matched_groups_json, matched_at,
         created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unmatched', '待匹配', NULL, NULL, ?, ?)
        ON DUPLICATE KEY UPDATE
          upstream_base_url = VALUES(upstream_base_url),
          upstream_platform = VALUES(upstream_platform),
          auth_mode = VALUES(auth_mode),
          login_username = VALUES(login_username),
          login_password = VALUES(login_password),
          access_token = VALUES(access_token),
          access_user_id = VALUES(access_user_id),
          refresh_token = VALUES(refresh_token),
          channel_key = VALUES(channel_key),
          match_status = 'unmatched',
          match_message = '待匹配',
          matched_groups_json = NULL,
          matched_at = NULL,
          updated_at = VALUES(updated_at)
        """,
        (
            admin_site_id, channel_id, base_url, platform, auth_mode,
            username, password, access_token, access_user_id, refresh_token,
            channel_key, now, now,
        ),
    )
    return True, None


def _channel_key_is_masked(value: Any) -> bool:
    text = str(value or "").strip()
    return not text or "****" in text or text == "-"


def normalize_newapi_user_token_key(value: Any) -> str:
    """NewAPI 数据库存储的用户 token 不带 sk-，前端展示/渠道配置通常会带。"""
    text = str(value or "").strip()
    return text[3:] if text.lower().startswith("sk-") else text


def mask_newapi_user_token_key(value: Any) -> str:
    """与 NewAPI model.MaskTokenKey 保持一致，用于从掩码列表筛选候选 token。"""
    key = normalize_newapi_user_token_key(value)
    if not key:
        return ""
    if len(key) <= 4:
        return "*" * len(key)
    if len(key) <= 8:
        return f"{key[:2]}****{key[-2:]}"
    return f"{key[:4]}**********{key[-4:]}"


def _newapi_user_token_items(payload: Any) -> List[Dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        items = data.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def fetch_all_newapi_user_tokens(
    site: Dict[str, Any], page_size: int = 100, max_pages: int = 50
) -> Tuple[bool, List[Dict[str, Any]], Optional[str]]:
    """读取当前上游用户自己的 API token；这是普通 UserAuth，不需要渠道管理员权限。

    Goes through the unified NewAPI browser executor so browser-mode sites
    also use the full session bundle, and 401/403 failures trigger exactly
    one forced refresh + retry.
    """
    if not site.get("access_token") or not site.get("access_user_id"):
        return False, [], "NewAPI 上游缺少用户认证令牌或用户 ID"
    all_items: List[Dict[str, Any]] = []
    # NewAPI 的 token 页码从 1 开始。p=0 会被服务端兼容成第 1 页，若随后
    # 再请求 p=1 就会把第一页重复计算并提前达到 total，漏掉后续密钥。
    for page in range(1, max_pages + 1):
        query = f"p={page}&page_size={int(page_size)}&size={int(page_size)}"
        ok, payload, error = newapi_browser_request(
            site, "GET", "/api/token/", query=query
        )
        if not ok:
            return False, [], error or "读取 NewAPI 用户 API 密钥列表失败"
        if not isinstance(payload, dict) or not payload.get("success"):
            message = str(payload.get("message") or "") if isinstance(payload, dict) else ""
            return False, [], message or "NewAPI 用户 API 密钥列表响应异常"
        items = _newapi_user_token_items(payload)
        all_items.extend(items)
        data = payload.get("data") if isinstance(payload, dict) else None
        total = data.get("total") if isinstance(data, dict) else None
        if len(items) < page_size or (isinstance(total, int) and len(all_items) >= total):
            break
    return True, all_items, None


def fetch_newapi_user_token_key(
    site: Dict[str, Any], token_id: int
) -> Tuple[bool, str, Optional[str]]:
    ok, payload, error = newapi_browser_request(
        site, "POST", f"/api/token/{int(token_id)}/key"
    )
    if not ok:
        return False, "", error or "读取 NewAPI 用户 API 密钥失败"
    data = payload.get("data") if isinstance(payload, dict) else None
    key = data.get("key") if isinstance(data, dict) else ""
    if not isinstance(payload, dict) or not payload.get("success") or not str(key or "").strip():
        message = str(payload.get("message") or "") if isinstance(payload, dict) else ""
        return False, "", message or "NewAPI 用户 API 密钥响应异常"
    return True, str(key).strip(), None


def _newapi_token_cache_key(site: Dict[str, Any]) -> str:
    """Stable, secret-free cache key for the per-site NewAPI token list.

    Never includes plaintext tokens, cookies or session ids.  The bucket of
    ``browser_access_expires_at`` keeps the cache consistent with the current
    browser session, while the ``auth_mode`` flag invalidates it when the
    user switches back to a system token.
    """
    try:
        expires = int(site.get("browser_access_expires_at") or 0)
    except (TypeError, ValueError):
        expires = 0
    auth_mode = str(site.get("auth_mode") or "token").strip().lower()
    return "|".join(
        (
            normalize_base_url(str(site.get("base_url") or "")),
            str(int(site.get("id") or 0)),
            auth_mode,
            str(expires // 60),
        )
    )


def find_newapi_user_token_by_key(
    site: Dict[str, Any], channel_key: str
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """用主站渠道 key 在上游当前用户的 /api/token/ 列表中精确匹配分组。"""
    target = normalize_newapi_user_token_key(channel_key)
    if not target:
        return None, "当前渠道没有真实 key，无法查询上游分组"
    cache_key = _newapi_token_cache_key(site)
    with NEWAPI_USER_TOKEN_LIST_LOCK:
        cached = NEWAPI_USER_TOKEN_LIST_CACHE.get(cache_key)
        if cached and time.monotonic() - float(cached.get("updated_monotonic") or 0) < NEWAPI_USER_TOKEN_LIST_CACHE_TTL_SECONDS:
            tokens = cached.get("tokens") or []
        else:
            ok, tokens, error = fetch_all_newapi_user_tokens(site)
            if not ok:
                return None, error or "读取 NewAPI 用户 API 密钥列表失败"
            tokens = [dict(item) for item in tokens if isinstance(item, dict)]
            NEWAPI_USER_TOKEN_LIST_CACHE[cache_key] = {
                "tokens": tokens,
                "updated_monotonic": time.monotonic(),
            }

        target_mask = mask_newapi_user_token_key(target)
        candidates = [
            item
            for item in tokens
            if normalize_newapi_user_token_key(item.get("key")) == target
            or normalize_newapi_user_token_key(item.get("key")) == target_mask
        ]
        if not candidates:
            return None, "当前 key 未在上游 NewAPI 用户 API 密钥列表中找到"

        key_errors: List[str] = []
        for item in candidates:
            full_key = str(item.get("_full_key") or "").strip()
            if not full_key:
                try:
                    token_id = int(item.get("id"))
                except (TypeError, ValueError):
                    continue
                key_ok, full_key, key_error = fetch_newapi_user_token_key(site, token_id)
                if not key_ok:
                    if key_error and key_error not in key_errors:
                        key_errors.append(key_error)
                    continue
                item["_full_key"] = full_key
            if normalize_newapi_user_token_key(full_key) == target:
                return item, None
        if key_errors:
            return None, "读取上游 NewAPI 用户 API 密钥失败：" + "；".join(key_errors)
        return None, "当前 key 未在上游 NewAPI 用户 API 密钥列表中找到"


def mark_channel_upstream_match_failure(
    admin_site_id: int, channel_id: int, status: str, message: str
) -> None:
    now = utc_now_iso()
    db_execute(
        """
        UPDATE channel_upstream_bindings
        SET match_status = ?, match_message = ?, matched_groups_json = NULL,
            matched_at = NULL, updated_at = ?
        WHERE admin_site_id = ? AND channel_id = ?
        """,
        (status, message, now, admin_site_id, channel_id),
    )


def find_monitor_site_for_channel(base_url: str) -> Optional[Dict[str, Any]]:
    normalized = normalize_base_url(base_url)
    if not normalized:
        return None
    rows = db_query_all("SELECT * FROM sites WHERE enabled = 1 ORDER BY id DESC")
    for row in rows:
        if normalize_base_url(str(row.get("base_url") or "")) == normalized:
            return row
    return None


def sub2api_key_group_name(
    key_item: Dict[str, Any], groups: Dict[str, Dict[str, Any]]
) -> str:
    """Resolve a key's group reference to the display name from available groups."""
    raw_group = key_item.get("group")
    group_info = raw_group if isinstance(raw_group, dict) else {}
    candidates = [
        group_info.get("name"),
        group_info.get("id"),
        key_item.get("group_name"),
        key_item.get("group_id"),
    ]
    for candidate in candidates:
        value = str(candidate or "").strip()
        if not value:
            continue
        if value in groups:
            return value
        for name, info in groups.items():
            if isinstance(info, dict) and str(info.get("id") or "").strip() == value:
                return name
    # key 列表本身已经明确返回了所属分组时，即使该分组暂时不在
    # /groups/available（例如订阅刚过期），也应展示真实分组而不是误报无分组。
    fallback_name = str(group_info.get("name") or key_item.get("group_name") or "").strip()
    if fallback_name:
        return fallback_name
    fallback_id = group_info.get("id") or key_item.get("group_id")
    return f"分组 #{fallback_id}" if fallback_id not in (None, "") else ""


CHANNEL_MATCH_STALE_STATUSES = frozenset(
    {"error", "refresh_error", "needs_key_verification", "missing_key"}
)


def persist_channel_match(
    admin_site_id: int,
    channel_id: int,
    status: str,
    message: str,
    matched_groups: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    binding = get_channel_upstream_binding(admin_site_id, channel_id)
    if not binding:
        now = utc_now_iso()
        db_execute(
            """
            INSERT INTO channel_upstream_bindings
            (admin_site_id, channel_id, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON DUPLICATE KEY UPDATE channel_id = VALUES(channel_id)
            """,
            (admin_site_id, channel_id, now, now),
        )
        binding = get_channel_upstream_binding(admin_site_id, channel_id) or {
            "matched_groups_json": None,
        }
    try:
        previous_groups = json.loads(binding.get("matched_groups_json") or "[]")
    except (TypeError, ValueError):
        previous_groups = []
    if not isinstance(previous_groups, list):
        previous_groups = []

    now = utc_now_iso()
    if status in CHANNEL_MATCH_STALE_STATUSES and not matched_groups:
        # A transient 2FA/rate-limit/network error must not erase the last
        # successful key-to-group result that is still useful to the operator.
        # Keep matched_at unchanged so the UI can distinguish stale data.
        if previous_groups:
            db_execute(
                """
                UPDATE channel_upstream_bindings
                SET match_status = ?, match_message = ?, updated_at = ?
                WHERE admin_site_id = ? AND channel_id = ?
                """,
                (status, message, now, admin_site_id, channel_id),
            )
            return previous_groups

    effective_groups = [dict(item) for item in matched_groups]
    if status == "matched_partial" and previous_groups:
        previous_by_name = {
            str(item.get("name") or ""): item
            for item in previous_groups
            if isinstance(item, dict) and item.get("name")
        }
        for item in effective_groups:
            previous = previous_by_name.get(str(item.get("name") or ""))
            if not previous or item.get("ratio") not in (None, ""):
                continue
            item["ratio"] = previous.get("ratio")
            if previous.get("ratio_type"):
                item["ratio_type"] = previous.get("ratio_type")

    db_execute(
        """
        UPDATE channel_upstream_bindings
        SET match_status = ?, match_message = ?, matched_groups_json = ?, matched_at = ?, updated_at = ?
        WHERE admin_site_id = ? AND channel_id = ?
        """,
        (
            status, message, json.dumps(effective_groups, ensure_ascii=False), now, now,
            admin_site_id, channel_id,
        ),
    )
    return effective_groups


def match_channel_upstream_binding(
    site: Dict[str, Any], channel_id: int, force_refresh: bool = False
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    admin_site_id = int(site["id"])
    binding = get_channel_upstream_binding(admin_site_id, channel_id)
    detail_ok, detail_payload, _detail_error = fetch_newapi_channel_detail(site, channel_id)
    detail = detail_payload.get("data") if isinstance(detail_payload, dict) else {}
    detail = detail if isinstance(detail, dict) else {}

    # 渠道级配置优先；没有单独配置时，复用「渠道监控」中同 Base URL 的登录态。
    # 这是主站渠道和渠道监控共用同一上游时的正常工作方式。
    monitor_site = find_monitor_site_for_channel(str(detail.get("base_url") or ""))
    auth_source = binding if binding and binding.get("upstream_base_url") else monitor_site
    if not auth_source or not auth_source.get("base_url", auth_source.get("upstream_base_url")):
        return True, {
            "configured": False,
            "match_status": "unmatched",
            "match_message": "未配置对应的上游登录态，请优先配置渠道",
            "matched_groups": [],
        }, None

    if binding and binding.get("upstream_base_url"):
        upstream_base = str(binding.get("upstream_base_url") or "")
        platform = str(binding.get("upstream_platform") or "newapi").strip().lower()
        upstream = {
            "id": admin_site_id,
            "base_url": upstream_base,
            "platform": platform,
            "auth_mode": binding.get("auth_mode") or "token",
            "login_username": binding.get("login_username") or "",
            "login_password": binding.get("login_password") or "",
            "access_token": binding.get("access_token") or "",
            "access_user_id": binding.get("access_user_id") or "",
            "refresh_token": binding.get("refresh_token") or "",
        }
    else:
        upstream = auth_source
        upstream_base = str(upstream.get("base_url") or "")
        platform = str(upstream.get("platform") or "newapi").strip().lower()

    inherited_from_monitor = not bool(binding and binding.get("upstream_base_url"))
    # 只配置了一个公开 NewAPI 监控站点，并不等于已经配置了可读取用户 API
    # 密钥的登录态。此时直接提示优先配置渠道，避免先读取主站受保护 key，
    # 随后才报“缺少令牌”，也避免无意义地触发主站 2FA/限流。
    if (
        platform == "newapi"
        and inherited_from_monitor
        and (not upstream.get("access_token") or not upstream.get("access_user_id"))
    ):
        return True, {
            "configured": False,
            "inherited_from_monitor": True,
            "upstream_base_url": upstream_base,
            "upstream_platform": platform,
            "match_status": "unmatched",
            "match_message": "同 Base URL 的渠道监控未配置 NewAPI 普通用户认证，请优先配置渠道",
            "matched_groups": [],
        }, None

    def verification_required_payload(
        message: str,
        matched_groups: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        return {
            "configured": True,
            "inherited_from_monitor": inherited_from_monitor,
            "upstream_base_url": upstream_base,
            "upstream_platform": platform,
            "auth_mode": upstream.get("auth_mode") or "password",
            "has_login_username": bool(upstream.get("login_username")),
            "has_login_password": bool(upstream.get("login_password")),
            "has_access_token": bool(upstream.get("access_token")),
            "has_refresh_token": bool(upstream.get("refresh_token")),
            "access_user_id": upstream.get("access_user_id") or "",
            "match_status": "needs_key_verification",
            "match_message": message,
            "matched_groups": matched_groups or [],
            "matched_at": binding.get("matched_at") if binding else None,
        }

    def key_verification_guidance(error: Optional[str]) -> Optional[str]:
        text = str(error or "")
        markers = (
            "尚未完成 key 读取安全验证",
            "安全验证 proof 已失效",
            "key 读取安全验证已过期",
            "网页登录 Session 已过期",
            "网页登录 Session 已失效",
            "网页登录 Session 或安全验证",
            "网页登录需要 2FA",
            "需要重新完成 2FA",
        )
        if not any(marker in text for marker in markers):
            return None
        return (
            "渠道运行正常，但本地尚未保存该渠道 key。请点击“编辑主站”，"
            "输入当前 2FA 验证码完成一次 key 读取安全验证。"
            "验证成功后会保存渠道 key，后续查询将优先复用；"
            "缓存缺失或验证状态失效时仍需重新验证。"
        )

    channel_key = ""
    key_error: Optional[str] = None
    if not force_refresh and is_admin_site_row(site):
        channel_key = get_cached_admin_channel_key(admin_site_id, channel_id)
    if (
        not channel_key
        and not force_refresh
        and binding
        and not _channel_key_is_masked(binding.get("channel_key"))
    ):
        channel_key = str(binding.get("channel_key") or "").strip()
        persist_admin_channel_key(admin_site_id, channel_id, channel_key)
    if not channel_key and not _channel_key_is_masked(detail.get("key")):
        channel_key = str(detail.get("key") or "").strip()
        persist_admin_channel_key(admin_site_id, channel_id, channel_key)
    if not channel_key:
        key_ok, main_site_key, key_error = fetch_newapi_channel_key(
            site, channel_id, force_refresh=force_refresh
        )
        if key_ok:
            channel_key = main_site_key
    group_names: List[str] = []
    matched_by = "key 精确匹配"
    refreshed_auth: Optional[Dict[str, Any]] = None
    auth_context: Optional[Dict[str, Any]] = None
    if platform == "newapi":
        if str(upstream.get("auth_mode") or "").strip().lower() == "password":
            login_ok, auth_data, login_error = _newapi_password_login_bundle(
                upstream_base,
                str(upstream.get("login_username") or "").strip(),
                str(upstream.get("login_password") or ""),
            )
            if not login_ok:
                message = login_error or "上游 NewAPI 用户名密码登录失败"
                status = "needs_key_verification" if auth_data.get("requires_2fa") else "error"
                stale_groups = persist_channel_match(
                    admin_site_id, channel_id, status, message, []
                )
                return False, verification_required_payload(message, stale_groups), message
            upstream.update(auth_data)
            # The password was only used to establish this one matching session.
            # Do not let the generic executor refresh an unrelated local site row.
            upstream["auth_mode"] = BROWSER_AUTH_MODE
            upstream["id"] = 0
        # NewAPI 普通用户自己的 API 密钥列表 /api/token/ 已直接包含 group。
        # 用主站当前 key 精确匹配该列表，不访问需要管理员权限的 /api/channel/。
        if not channel_key:
            guidance = key_verification_guidance(key_error)
            if guidance:
                stale_groups = persist_channel_match(
                    admin_site_id,
                    channel_id,
                    "needs_key_verification",
                    guidance,
                    [],
                )
                return True, verification_required_payload(guidance, stale_groups), None
            message = f"主站渠道 key 读取失败：{key_error or '未返回真实 key'}"
            persist_channel_match(admin_site_id, channel_id, "missing_key", message, [])
            return False, {}, message
        if not upstream.get("access_token") or not upstream.get("access_user_id"):
            message = "NewAPI 上游未配置用户认证令牌或用户 ID，无法读取用户 API 密钥列表"
            persist_channel_match(admin_site_id, channel_id, "error", message, [])
            return False, {}, message

        matched, upstream_match_error = find_newapi_user_token_by_key(upstream, channel_key)
        if not matched:
            message = upstream_match_error or "当前 key 未在上游 NewAPI 用户 API 密钥列表中找到"
            status = "error" if "无法读取" in message or "缺少" in message else "key_not_found"
            persist_channel_match(admin_site_id, channel_id, status, message, [])
            return False, {}, message
        group_names = split_channel_groups(matched.get("group"))
        if not group_names:
            message = "上游 NewAPI 已找到当前 key，但该用户 API 密钥没有配置分组"
            persist_channel_match(admin_site_id, channel_id, "no_group", message, [])
            return False, {}, message

        groups_ok, groups_payload, groups_error = fetch_newapi_groups_with_access_token(
            upstream_base,
            str(upstream.get("access_token") or ""),
            str(upstream.get("access_user_id") or ""),
        )
        groups = parse_groups_payload(groups_payload) if groups_ok else {}
        matched_groups = [
            {
                "name": name,
                "ratio": (groups.get(name) or {}).get("ratio"),
                "ratio_type": (groups.get(name) or {}).get("ratio_type") or "text",
                "desc": (groups.get(name) or {}).get("desc") or "",
                "available_to_login": name in groups,
            }
            for name in group_names
        ]
        if groups_ok:
            status = (
                "matched"
                if all(item["available_to_login"] for item in matched_groups)
                else "matched_partial"
            )
            message = f"已按{matched_by}读取上游分组倍率"
        else:
            status = "refresh_error"
            message = f"已读取分组，但倍率请求失败：{groups_error or '未知错误'}"
            matched_groups = []
    elif platform == "sub2api":
        monitor_site_id = (
            int(monitor_site.get("id") or 0)
            if inherited_from_monitor and monitor_site
            else 0
        )
        ok, groups_payload, groups_error = fetch_sub2api_user_groups(
            upstream_base,
            username=upstream.get("login_username") or "",
            password=upstream.get("login_password") or "",
            auth_mode=upstream.get("auth_mode") or "password",
            access_token=upstream.get("access_token") or "",
            refresh_token=upstream.get("refresh_token") or "",
            include_auth_context=True,
            site_id=monitor_site_id,
        )
        if not ok:
            message = f"读取 sub2api 登录态分组失败：{groups_error or '未知错误'}"
            persist_channel_match(admin_site_id, channel_id, "error", message, [])
            return False, {}, message
        refreshed_auth = (
            groups_payload.get("refreshed_auth")
            if isinstance(groups_payload, dict)
            else None
        )
        auth_context = (
            groups_payload.get("_auth_context")
            if isinstance(groups_payload, dict)
            else None
        )
        if refreshed_auth and not inherited_from_monitor:
            persist_channel_binding_refreshed_auth(
                admin_site_id,
                channel_id,
                refreshed_auth,
                expected_access_token=str(upstream.get("access_token") or "").strip(),
                expected_refresh_token=str(upstream.get("refresh_token") or "").strip(),
            )
        groups = parse_sub2api_groups(groups_payload.get("data"), groups_payload.get("user_rates"))
        # sub2api 的账号可能同时拥有多个分组，不能用账号分组或唯一分组猜测当前渠道。
        # 必须拿当前渠道 key 去 /api/v1/keys 精确找到它自己的 group。
        if not channel_key:
            guidance = key_verification_guidance(key_error)
            if guidance:
                stale_groups = persist_channel_match(
                    admin_site_id,
                    channel_id,
                    "needs_key_verification",
                    guidance,
                    [],
                )
                return True, verification_required_payload(guidance, stale_groups), None
            if key_error:
                needs_security_hint = any(
                    marker in key_error
                    for marker in (
                        "主站需要重新完成 2FA",
                        "主站网页登录需要 2FA",
                        "安全验证状态无效",
                    )
                )
                hint = "；请完成主站网页登录和安全验证后刷新" if needs_security_hint else ""
                message = f"主站渠道 key 读取失败：{key_error}{hint}"
            else:
                message = "主站没有返回当前渠道 key，无法查询 sub2api key 所属分组"
            persist_channel_match(admin_site_id, channel_id, "missing_key", message, [])
            return False, {}, message

        key_group: Optional[Dict[str, Any]] = None
        key_token = str(
            (refreshed_auth or {}).get("access_token")
            or (auth_context or {}).get("access_token")
            or upstream.get("access_token")
            or ""
        ).strip()
        if not key_token:
            message = "sub2api 登录成功，但没有拿到可查询 key 的 access_token"
            persist_channel_match(admin_site_id, channel_id, "error", message, [])
            return False, {}, message

        source_auth_mode = str(upstream.get("auth_mode") or "token").strip().lower()
        key_auth_mode = (
            BROWSER_AUTH_MODE if source_auth_mode == "password" else source_auth_mode
        )
        key_refresh_token = str(
            (refreshed_auth or {}).get("refresh_token")
            or upstream.get("refresh_token")
            or ""
        ).strip()
        # Password-mode group reads already performed the login.  Keep the
        # pre-authenticated key request on that token so a normal match does
        # not perform a second password login; a later 401 can still use the
        # browser-style fallback when credentials are available.
        key_site_id = monitor_site_id if source_auth_mode != "password" else 0
        keys_ok, keys_payload, keys_error = fetch_sub2api_keys(
            upstream_base,
            username=upstream.get("login_username") or "",
            password=upstream.get("login_password") or "",
            auth_mode=key_auth_mode,
            access_token=key_token,
            refresh_token=key_refresh_token,
            include_auth_context=True,
            site_id=key_site_id,
        )
        if not keys_ok:
            message = f"读取 sub2api key 列表失败：{keys_error or '未知错误'}"
            persist_channel_match(admin_site_id, channel_id, "error", message, [])
            return False, {}, message

        key_refreshed_auth = (
            keys_payload.get("refreshed_auth")
            if isinstance(keys_payload, dict)
            else None
        )
        if key_refreshed_auth:
            refreshed_auth = key_refreshed_auth
            persist_channel_binding_refreshed_auth(
                admin_site_id,
                channel_id,
                key_refreshed_auth,
                expected_access_token=key_token,
                expected_refresh_token=key_refresh_token,
            )
        key_auth_context = (
            keys_payload.get("_auth_context")
            if isinstance(keys_payload, dict)
            else None
        )
        if isinstance(key_auth_context, dict) and key_auth_context.get("access_token"):
            key_token = str(key_auth_context.get("access_token") or "").strip()

        key_items = (keys_payload.get("data") or {}).get("items") or []
        key_match = next(
            (
                item for item in key_items
                if isinstance(item, dict)
                and str(item.get("key") or item.get("value") or "").strip() == channel_key
            ),
            None,
        )
        if not isinstance(key_match, dict):
            message = "当前渠道 key 未在 sub2api 登录账号的 key 列表中找到"
            persist_channel_match(admin_site_id, channel_id, "key_not_found", message, [])
            return False, {}, message

        raw_group = key_match.get("group")
        key_group = raw_group if isinstance(raw_group, dict) else {}
        key_group_name = sub2api_key_group_name(key_match, groups)
        if not key_group_name:
            message = "sub2api 已找到当前 key，但该 key 没有返回所属分组"
            persist_channel_match(admin_site_id, channel_id, "no_group", message, [])
            return False, {}, message
        group_names = [key_group_name]
        matched_by = "key 精确匹配"
        matched_groups = [
            {
                "name": name,
                "ratio": (
                    (groups.get(name) or {}).get("ratio")
                    if (groups.get(name) or {}).get("ratio") is not None
                    else (key_group or {}).get("rate_multiplier")
                ),
                # /groups/rates 是当前用户实际计费的专属倍率，优先于 key.group
                # 返回的分组基础倍率；没有专属/可用分组数据时再回退基础倍率。
                "ratio_type": (
                    (groups.get(name) or {}).get("ratio_type") or "text"
                    if (groups.get(name) or {}).get("ratio") is not None
                    else "number" if (key_group or {}).get("rate_multiplier") is not None else "text"
                ),
                "desc": (key_group or {}).get("description") or (groups.get(name) or {}).get("desc") or "",
                "available_to_login": name in groups,
            }
            for name in group_names
        ]
        status = "matched" if all(item["available_to_login"] for item in matched_groups) else "matched_partial"
        message = f"已按{matched_by}读取 sub2api 分组倍率"
    else:
        message = f"暂不支持上游平台：{platform}"
        persist_channel_match(admin_site_id, channel_id, "unsupported", message, [])
        return False, {}, message

    persisted_groups = persist_channel_match(
        admin_site_id,
        channel_id,
        status,
        message,
        matched_groups,
    )
    if isinstance(persisted_groups, list):
        matched_groups = persisted_groups
    now = utc_now_iso()
    effective_access_token = str(
        (refreshed_auth or {}).get("access_token")
        or (auth_context or {}).get("access_token")
        or upstream.get("access_token")
        or ""
    ).strip()
    effective_refresh_token = str(
        (refreshed_auth or {}).get("refresh_token")
        or upstream.get("refresh_token")
        or ""
    ).strip()
    return True, {
        "configured": True,
        "inherited_from_monitor": inherited_from_monitor,
        "upstream_base_url": upstream_base,
        "upstream_platform": platform,
        "auth_mode": upstream.get("auth_mode") or "password",
        "has_login_username": bool(upstream.get("login_username")),
        "has_login_password": bool(upstream.get("login_password")),
        "has_access_token": bool(effective_access_token),
        "has_refresh_token": bool(effective_refresh_token),
        "access_user_id": upstream.get("access_user_id") or "",
        "match_status": status,
        "match_message": message,
        "matched_groups": matched_groups,
        "matched_at": now,
    }, None


def fetch_all_newapi_channels(
    site: Dict[str, Any], page_size: int = 100, max_pages: int = 50
) -> Tuple[bool, List[Dict[str, Any]], Optional[str]]:
    """翻页拉取全部渠道（渠道表与左侧分组计数都基于全量）。"""
    all_items: List[Dict[str, Any]] = []
    expected_total: Optional[int] = None
    for page in range(max_pages):
        ok, payload, error = fetch_newapi_channels(site, page, page_size)
        if not ok:
            # A partial list is unsafe for discovery: importing it could make
            # a user believe every upstream URL was considered.
            return False, [], error or "读取 NewAPI 渠道分页失败"
        items, _meta = _newapi_channel_list_items(payload)
        if not isinstance(payload, dict) or "data" not in payload:
            return False, [], "NewAPI 渠道响应缺少 data，拒绝返回截断数据"
        raw_data = payload.get("data")
        if not isinstance(raw_data, list) and not (
            isinstance(raw_data, dict) and isinstance(raw_data.get("items"), list)
        ):
            return False, [], "NewAPI 渠道响应格式无效，拒绝返回截断数据"
        raw_items = (
            raw_data
            if isinstance(raw_data, list)
            else raw_data.get("items") or []
        )
        if any(not isinstance(item, dict) for item in raw_items):
            return False, [], "NewAPI 渠道响应包含无效项，拒绝返回截断数据"
        if isinstance(_meta, dict) and _meta.get("total") is not None:
            try:
                expected_total = max(0, int(_meta.get("total") or 0))
            except (TypeError, ValueError):
                return False, [], "NewAPI 渠道总数无效，拒绝返回截断数据"
        all_items.extend(items)
        if expected_total is not None:
            if len(all_items) >= expected_total:
                return True, all_items, None
            if not items:
                return False, [], "NewAPI 渠道分页提前结束，拒绝返回截断数据"
            continue
        if len(items) < page_size:
            return True, all_items, None
    return False, [], f"NewAPI 渠道超过最大分页页数 {max_pages}，拒绝返回截断数据"



def fetch_newapi_admin_groups(
    site: Dict[str, Any]
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """Group name -> {ratio, ratio_type, desc}, for correlating a channel's key
    with the group multiplier it serves under."""
    ok, payload, error = fetch_newapi_groups_with_access_token(
        site["base_url"],
        site.get("access_token") or "",
        site.get("access_user_id") or "",
    )
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "读取分组失败"
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        return False, payload if isinstance(payload, dict) else {}, "NewAPI 分组响应格式无效"
    return True, {"success": True, "data": parse_groups_payload(payload)}, None


def fetch_admin_site_channels(
    site: Dict[str, Any], keyword: str = ""
) -> Tuple[bool, List[Dict[str, Any]], Dict[str, Any], Optional[str]]:
    if admin_site_platform(site) == "sub2api":
        return fetch_sub2api_admin_site_channels(site, keyword)
    keyword = str(keyword or "").strip()
    if keyword:
        ok, payload, error = fetch_newapi_channels(site, 0, 100, keyword)
        if not ok:
            return False, [], {}, channel_admin_error_message(error, payload)
        items, meta = _newapi_channel_list_items(payload)
        return True, items, meta or {"total": len(items)}, None
    ok, items, error = fetch_all_newapi_channels(site)
    if not ok:
        return False, [], {}, channel_admin_error_message(error)
    return True, items, {"total": len(items)}, None


def fetch_admin_site_groups(
    site: Dict[str, Any]
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    if admin_site_platform(site) == "sub2api":
        ok, groups, upstream, error = fetch_sub2api_admin_groups(site)
        if not ok:
            return False, upstream, error
        return True, sub2api_admin_groups_payload(groups), None
    return fetch_newapi_admin_groups(site)


def fetch_admin_site_channel_detail(
    site: Dict[str, Any], channel_id: int
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    if admin_site_platform(site) == "sub2api":
        return fetch_sub2api_admin_channel_detail(site, channel_id)
    return fetch_newapi_channel_detail(site, channel_id)


def update_admin_site_channel(
    site: Dict[str, Any], channel_id: int, patch: Dict[str, Any]
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    if admin_site_platform(site) == "sub2api":
        return update_sub2api_admin_channel(site, channel_id, patch)
    return update_newapi_channel(site, channel_id, patch)


def fetch_newapi_model_data(
    base_url: str,
    access_token: str = "",
    user_id: str = "",
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """Legacy join path: pricing + uptime (kept for old /models UI)."""
    headers = newapi_auth_headers(access_token, user_id)
    normalized_base = normalize_base_url(base_url)
    pricing_ok, pricing_payload, pricing_error = fetch_newapi_pricing(
        normalized_base, access_token=access_token, user_id=user_id
    )
    if not pricing_ok:
        return False, {"pricing": pricing_payload}, pricing_error or "读取 NewAPI 模型配置失败"

    uptime_payload, uptime_error = get_cached_newapi_uptime(normalized_base, headers)

    return True, {
        "success": True,
        "pricing": pricing_payload,
        "uptime": uptime_payload,
        "uptime_error": uptime_error,
    }, None


def get_site_or_404(site_id: int) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], int]:
    site = db_query_one("SELECT * FROM sites WHERE id = ?", (site_id,))
    if not site:
        return None, {"success": False, "message": "site not found"}, 404
    return site, None, 200


def _discovery_existing_site(base_url: str) -> Optional[Dict[str, Any]]:
    """Find a local site by normalized URL, including legacy trailing slashes."""
    row = db_query_one(
        "SELECT id, base_url, platform FROM sites WHERE base_url = ? LIMIT 1",
        (base_url,),
    )
    if row:
        return row
    # Older rows may predate normalize_base_url and retain a trailing slash.
    # Keep the fallback query separate so normal indexed lookups stay cheap.
    return db_query_one(
        "SELECT id, base_url, platform FROM sites "
        "WHERE TRIM(TRAILING '/' FROM base_url) = ? LIMIT 1",
        (base_url,),
    )


def _discovery_public_item(
    item: Dict[str, Any],
    status: str,
    site_id: Any = None,
    message: str = "",
) -> Dict[str, Any]:
    """Build an import result without copying any credential-bearing fields."""
    result: Dict[str, Any] = {
        "base_url": str(item.get("base_url") or ""),
        "name": str(item.get("name") or "").strip()
        or str(item.get("base_url") or ""),
        "channel_ids": list(item.get("channel_ids") or []),
        "status": status,
    }
    if site_id is not None:
        result["site_id"] = site_id
    if message:
        result["message"] = message
    return result


def _discovery_channel_ids(item: Dict[str, Any]) -> Tuple[List[int], Optional[str]]:
    raw_ids = item.get("channel_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        return [], "channel_ids required"
    if len(raw_ids) > MAX_DISCOVERY_CHANNEL_IDS_PER_ITEM:
        return [], "too many channel_ids"
    channel_ids: List[int] = []
    for raw_id in raw_ids:
        if isinstance(raw_id, bool):
            return [], "channel_id invalid"
        try:
            channel_id = int(raw_id)
        except (TypeError, ValueError):
            return [], "channel_id invalid"
        if channel_id <= 0:
            return [], "channel_id invalid"
        if channel_id not in channel_ids:
            channel_ids.append(channel_id)
    if not channel_ids:
        return [], "channel_ids required"
    return channel_ids, None


def import_discovered_sites(
    admin_site: Dict[str, Any], body: Dict[str, Any]
) -> Any:
    """Idempotently create monitoring sites from NewAPI channel candidates.

    The function intentionally returns per-item results for valid requests and
    a small error object for request-wide validation failures.  It never accepts
    or returns credentials; browser session synchronization remains a separate
    explicit endpoint/flow.
    """
    if not isinstance(admin_site, dict):
        return {
            "error": "platform_invalid",
            "message": "主站渠道发现导入仅支持 NewAPI",
        }
    requested_platform = str(admin_site.get("platform") or "newapi").strip().lower()
    if requested_platform != "newapi":
        return {
            "error": "platform_invalid",
            "message": "主站渠道发现导入仅支持 NewAPI",
        }
    if not isinstance(body, dict):
        return {"error": "invalid_body", "message": "请求体无效"}

    raw_items = body.get("items")
    if not isinstance(raw_items, list):
        return {"error": "items_invalid", "message": "items 必须是数组"}
    if len(raw_items) > MAX_DISCOVERY_IMPORT_ITEMS:
        return {
            "error": "too_many_items",
            "message": f"单次最多导入 {MAX_DISCOVERY_IMPORT_ITEMS} 个渠道",
        }

    raw_interval = body.get("interval_minutes", DEFAULT_INTERVAL_MINUTES)
    if isinstance(raw_interval, bool):
        return {"error": "interval_invalid", "message": "interval_minutes 无效"}
    try:
        interval_minutes = int(raw_interval or DEFAULT_INTERVAL_MINUTES)
    except (TypeError, ValueError):
        return {"error": "interval_invalid", "message": "interval_minutes 无效"}
    interval_minutes = max(
        MIN_INTERVAL_MINUTES,
        min(MAX_DISCOVERY_INTERVAL_MINUTES, interval_minutes),
    )

    try:
        admin_site_id = int(admin_site.get("id"))
    except (TypeError, ValueError):
        return {"error": "admin_site_invalid", "message": "管理站点 ID 无效"}

    prepared: List[Tuple[int, Dict[str, Any]]] = []
    results: List[Optional[Dict[str, Any]]] = [None] * len(raw_items)
    for item_index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, dict):
            results[item_index] = (
                {
                    "base_url": "",
                    "name": "",
                    "channel_ids": [],
                    "status": "invalid",
                    "message": "候选项无效",
                }
            )
            continue
        normalized, url_error = _normalize_discovery_base_url(raw_item.get("base_url"))
        item = {
            "base_url": normalized
            or _safe_discovery_display_url(raw_item.get("base_url")),
            "name": str(raw_item.get("name") or "").strip(),
            "channel_ids": [],
            "channel_names": raw_item.get("channel_names"),
        }
        if url_error:
            item["channel_ids"] = (
                list(raw_item.get("channel_ids") or [])
                if isinstance(raw_item.get("channel_ids"), list)
                else []
            )
            results[item_index] = (
                _discovery_public_item(item, "invalid", message=url_error)
            )
            continue
        channel_ids, ids_error = _discovery_channel_ids(raw_item)
        if ids_error:
            submitted_ids = (
                list(raw_item.get("channel_ids") or [])
                if isinstance(raw_item.get("channel_ids"), list)
                else []
            )
            results[item_index] = (
                _discovery_public_item(
                    {**item, "base_url": normalized, "channel_ids": submitted_ids},
                    "invalid",
                    message=ids_error,
                )
            )
            continue
        item["base_url"] = normalized
        item["channel_ids"] = channel_ids
        item["name"] = item["name"] or normalized
        prepared.append((item_index, item))

    # Do not touch the database when every submitted item failed local
    # validation.  This also makes malformed requests cheap and predictable.
    for item_index, item in prepared:
        with db_connection() as connection:
            try:
                result = _import_discovered_site_item(
                    connection,
                    admin_site_id,
                    item,
                    interval_minutes,
                )
                connection.commit()
            except Exception:
                try:
                    connection.rollback()
                except Exception:
                    pass
                result = _discovery_public_item(
                    item, "conflict", message="创建或关联监控站点失败"
                )
        results[item_index] = result

    return [result for result in results if result is not None]


def _import_discovered_site_item(
    connection: Any,
    admin_site_id: int,
    item: Dict[str, Any],
    interval_minutes: int,
    allow_existing_platform: bool = False,
) -> Dict[str, Any]:
    """Create or reuse a local monitoring site for one discovery candidate.

    All writes (the ``sites`` row and every ``site_discovery_links`` row)
    happen on the supplied connection.  The caller is expected to commit
    on success and roll back on any exception; this function itself never
    commits, so a partial failure cannot leave a created site dangling
    without its provenance links.
    """
    base_url = item["base_url"]
    name = item["name"]
    with connection.cursor() as cursor:
        cursor.execute(
            _q(
                "SELECT * FROM sites "
                "WHERE base_url = ? OR TRIM(TRAILING '/' FROM base_url) = ? "
                "ORDER BY (base_url = ?) DESC LIMIT 1 FOR UPDATE"
            ),
            (base_url, base_url, base_url),
        )
        existing = cursor.fetchone()
        if existing:
            platform = str(existing.get("platform") or "newapi").strip().lower()
            if platform != "newapi" and not allow_existing_platform:
                return _discovery_public_item(
                    item,
                    "conflict",
                    site_id=existing.get("id"),
                    message="同 URL 已存在非 NewAPI 监控站点",
                )
            site_id = int(existing["id"])
            item_status = "existing"
        else:
            now = utc_now_iso()
            try:
                cursor.execute(
                    _q(
                        """
                        INSERT INTO sites
                        (name, base_url, platform, enabled, interval_minutes,
                         login_enabled, auth_mode, status, next_check_at,
                         created_at, updated_at)
                        VALUES (?, ?, 'newapi', 1, ?, 0, 'token', 'unknown', ?, ?, ?)
                        """
                    ),
                    (
                        name[:255],
                        base_url,
                        interval_minutes,
                        next_check_iso(interval_minutes),
                        now,
                        now,
                    ),
                )
                site_id = int(cursor.lastrowid or 0)
                item_status = "created"
            except Exception:
                # A concurrent import can win the unique base_url race; treat
                # that row as existing.  Unrelated write failures bubble up
                # so the caller can roll the whole transaction back.
                cursor.execute(
                    _q(
                        "SELECT id, platform FROM sites "
                        "WHERE base_url = ? OR TRIM(TRAILING '/' FROM base_url) = ? "
                        "LIMIT 1"
                    ),
                    (base_url, base_url),
                )
                refreshed = cursor.fetchone()
                refreshed_platform = str(
                    (refreshed or {}).get("platform") or "newapi"
                ).strip().lower()
                if refreshed and (
                    refreshed_platform == "newapi" or allow_existing_platform
                ):
                    site_id = int(refreshed["id"])
                    item_status = "existing"
                elif refreshed:
                    return _discovery_public_item(
                        item,
                        "conflict",
                        site_id=refreshed.get("id"),
                        message="同 URL 已存在非 NewAPI 监控站点",
                    )
                else:
                    return _discovery_public_item(
                        item, "conflict", message="创建监控站点失败"
                    )

        if not site_id:
            return _discovery_public_item(
                item, "conflict", message="监控站点 ID 无效"
            )

        source_names = item.get("channel_names")
        source_names = source_names if isinstance(source_names, list) else []
        now = utc_now_iso()
        for index, channel_id in enumerate(item["channel_ids"]):
            channel_name = (
                str(source_names[index] or "").strip()
                if index < len(source_names)
                else ""
            ) or name
            cursor.execute(
                _q(
                    """
                    INSERT INTO site_discovery_links
                    (site_id, admin_site_id, channel_id, upstream_base_url,
                     channel_name, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON DUPLICATE KEY UPDATE
                      upstream_base_url = VALUES(upstream_base_url),
                      channel_name = VALUES(channel_name),
                      updated_at = VALUES(updated_at)
                    """
                ),
                (
                    int(site_id),
                    int(admin_site_id),
                    int(channel_id),
                    base_url,
                    channel_name[:255],
                    now,
                    now,
                ),
            )
    return _discovery_public_item(item, item_status, site_id=site_id)


def list_site_discovery_links(site_id: int) -> List[Dict[str, Any]]:
    """Return only the public (non-credential) provenance columns.

    The route is intentionally narrow: it joins against ``admin_sites`` to
    surface the source main-site name, but it never copies any token,
    password or session payload from the link row.  The handler relies on
    ``get_site_or_404`` to 404 unknown sites before this lookup runs.
    """
    rows = db_query_all(
        """
        SELECT l.site_id, l.admin_site_id, a.name AS admin_site_name,
               l.channel_id, l.channel_name, l.upstream_base_url,
               l.created_at, l.updated_at
        FROM site_discovery_links l
        JOIN admin_sites a ON a.id = l.admin_site_id
        WHERE l.site_id = ?
        ORDER BY a.name, l.channel_name, l.channel_id
        """,
        (int(site_id),),
    )
    allowed = (
        "site_id",
        "admin_site_id",
        "admin_site_name",
        "channel_id",
        "channel_name",
        "upstream_base_url",
        "created_at",
        "updated_at",
    )
    return [{key: row.get(key) for key in allowed} for row in rows]


def _live_channel_urls_from_candidates(
    candidates: List[Dict[str, Any]],
) -> Dict[int, str]:
    live_urls: Dict[int, str] = {}
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        base_url = normalize_base_url(str(candidate.get("base_url") or ""))
        for raw_channel_id in candidate.get("channel_ids") or []:
            channel_id = _positive_channel_id(raw_channel_id)
            if channel_id is not None:
                live_urls[channel_id] = base_url
    return live_urls


def _reconcile_site_discovery_links_in_connection(
    connection: Any,
    admin_site_id: int,
    candidates: List[Dict[str, Any]],
    preferred_site_by_channel: Optional[Dict[int, int]] = None,
) -> Tuple[int, set]:
    """Keep one source link per ``admin_site_id + channel_id`` identity."""
    live_urls = _live_channel_urls_from_candidates(candidates)
    preferred_site_by_channel = preferred_site_by_channel or {}
    rows = db_query_all(
        "SELECT id, site_id, channel_id, upstream_base_url "
        "FROM site_discovery_links WHERE admin_site_id = ?",
        (int(admin_site_id),),
        connection=connection,
    )
    stale_rows: List[Dict[str, Any]] = []
    affected_site_ids: set = set()
    live_rows_by_channel: Dict[int, List[Dict[str, Any]]] = {}
    for row in rows:
        channel_id = _positive_channel_id(row.get("channel_id"))
        if channel_id is None or channel_id not in live_urls:
            stale_rows.append(row)
        else:
            live_url = live_urls.get(channel_id) or ""
            current_url = normalize_base_url(
                str(row.get("upstream_base_url") or "")
            )
            # A live channel with no usable URL is preserved rather than
            # treated as a disappearance caused by malformed metadata.
            if live_url and current_url != live_url:
                stale_rows.append(row)
            else:
                live_rows_by_channel.setdefault(channel_id, []).append(row)
        try:
            affected_site_ids.add(int(row.get("site_id")))
        except (TypeError, ValueError):
            continue

    # A legacy import could have linked one upstream channel to multiple local
    # sites.  The current sync result identifies the canonical site; otherwise
    # retain the oldest link deterministically and release the rest.
    for channel_id, channel_rows in live_rows_by_channel.items():
        if len(channel_rows) < 2:
            continue
        preferred_site_id = preferred_site_by_channel.get(channel_id)
        canonical = next(
            (
                row
                for row in channel_rows
                if preferred_site_id is not None
                and int(row.get("site_id") or 0) == int(preferred_site_id)
            ),
            None,
        )
        if canonical is None:
            canonical = min(channel_rows, key=lambda row: int(row.get("id") or 0))
        canonical_id = int(canonical.get("id") or 0)
        stale_rows.extend(
            row for row in channel_rows if int(row.get("id") or 0) != canonical_id
        )

    removed = 0
    if stale_rows:
        with connection.cursor() as cursor:
            for row in stale_rows:
                cursor.execute(
                    _q(
                        "DELETE FROM site_discovery_links "
                        "WHERE id = ? AND admin_site_id = ?"
                    ),
                    (int(row["id"]), int(admin_site_id)),
                )
                removed += int(cursor.rowcount or 0)
    return removed, affected_site_ids


def reconcile_site_discovery_links(
    admin_site_id: int,
    candidates: List[Dict[str, Any]],
) -> int:
    """Drop provenance rows whose source channel no longer exists upstream.

    Only ``site_discovery_links`` rows for this admin site are deleted; the
    local monitoring site, snapshots and change history are never touched.
    The caller must invoke this after a complete and successful upstream
    read — paging failures, upstream errors and truncated lists must skip
    the reconcile step.
    """
    with db_connection() as connection:
        try:
            removed, _affected = _reconcile_site_discovery_links_in_connection(
                connection, int(admin_site_id), candidates
            )
            connection.commit()
            return removed
        except Exception:
            try:
                connection.rollback()
            except Exception:
                pass
            raise


SUB2API_AUTH_ERROR_CODES = {
    "401",
    "403",
    "UNAUTHORIZED",
    "FORBIDDEN",
    "INVALID_AUTH_HEADER",
    "EMPTY_TOKEN",
    "TOKEN_EXPIRED",
    "INVALID_TOKEN",
    "USER_NOT_FOUND",
    "USER_INACTIVE",
    "TOKEN_REVOKED",
}
SUB2API_AUTH_ERROR_MESSAGES = {
    "unauthorized",
    "forbidden",
    "authorization header is required",
    "token cannot be empty",
    "token has expired",
    "invalid token",
    "user not found",
    "user account is not active",
    "token has been revoked (password changed)",
    "未登录",
    "登录已过期",
    "令牌已过期",
    "令牌无效",
}


def is_sub2api_auth_error(payload: Any, error: Optional[str] = None) -> bool:
    if isinstance(payload, dict):
        for key in (
            "groups",
            "rates",
            "channels",
            "monitors",
            "refresh",
            "account",
            "login",
            "response",
            "data",
        ):
            if isinstance(payload.get(key), dict) and is_sub2api_auth_error(payload[key], error):
                return True
        status, code, message = _upstream_response_details(payload, error)
        if status in {401, 403}:
            return True
        normalized_code = re.sub(r"[^A-Z0-9]+", "_", code.upper()).strip("_")
        if normalized_code in SUB2API_AUTH_ERROR_CODES:
            return True
        normalized_message = message.strip().casefold()
        return normalized_message in SUB2API_AUTH_ERROR_MESSAGES
    return bool(error and error.startswith(("HTTP 401", "HTTP 403")))


def classify_sub2api_auth_failure(
    payload: Any, error: Optional[str] = None
) -> str:
    """Classify a failed credential request without exposing its secrets.

    Only an explicit authentication rejection may advance the fallback chain.
    Transport/server failures and malformed responses stay terminal for the
    current request so an outage cannot accidentally trigger a password login.
    """
    status, code, message = _upstream_response_details(payload, error)

    def nested_status(value: Any) -> int:
        if not isinstance(value, dict):
            return 0
        try:
            direct = int(value.get("status") or 0)
        except (TypeError, ValueError):
            direct = 0
        if direct:
            return direct
        for key in (
            "account",
            "groups",
            "rates",
            "channels",
            "monitors",
            "refresh",
            "login",
            "response",
            "data",
        ):
            child_status = nested_status(value.get(key))
            if child_status:
                return child_status
        return 0

    status = status or nested_status(payload)
    try:
        serialized = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        serialized = str(payload or "")
    text = " ".join(
        str(value or "") for value in (code, message, error, serialized)
    ).casefold()
    if not status:
        match = re.search(r"\bhttp\s+([1-5][0-9]{2})\b", text, re.I)
        if match:
            status = int(match.group(1))
    interactive_markers = (
        "turnstile",
        "captcha",
        "human verification",
        "人机验证",
        "requires_2fa",
        "require_2fa",
        "2fa",
        "two-factor",
        "temp_token",
        "temporary token",
        "verification required",
        "需要验证",
    )
    if any(marker in text for marker in interactive_markers):
        return "interactive"
    if is_sub2api_auth_error(payload, error):
        return "auth"
    if status >= 500:
        return "transport"
    transport_markers = (
        "urlopen error",
        "timed out",
        "timeout",
        "name or service not known",
        "temporary failure in name resolution",
        "connection refused",
        "connection reset",
        "certificate verify failed",
        "ssl:",
        "tls",
    )
    if any(marker in text for marker in transport_markers):
        return "transport"
    return "data"


def _sanitize_sub2api_error_text(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(
        r"(?i)\bbearer\s+[a-z0-9._~+/=-]+",
        "Bearer [redacted]",
        text,
    )
    text = re.sub(
        r"(?i)\b(access_token|refresh_token|password|authorization|token)\b"
        r"(\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
        r"\1\2[redacted]",
        text,
    )
    return text[:500]


class Sub2ApiUpstreamError(str):
    def __new__(cls, message: str, payload: Any = None):
        instance = super().__new__(cls, str(message or "sub2api 上游请求失败"))
        instance.payload = payload if isinstance(payload, dict) else {}
        return instance


def sub2api_proxy_error_response(
    payload: Any,
    error: Optional[str] = None,
    fallback_message: str = "sub2api 上游请求失败",
) -> Tuple[int, Dict[str, Any]]:
    upstream_status, upstream_code, message = _upstream_response_details(
        payload, error
    )
    if not upstream_status and upstream_code.isdigit():
        numeric_code = int(upstream_code)
        if 100 <= numeric_code <= 599:
            upstream_status = numeric_code
    message = _sanitize_sub2api_error_text(message or fallback_message)
    upstream_code = re.sub(
        r"[^A-Za-z0-9_.-]+", "_", str(upstream_code or "")
    ).strip("_")[:100]
    lowered = message.casefold()

    if is_sub2api_auth_error(payload, error):
        category = "auth"
        response_status = 502
    elif "无主站管理权限" in message or "not an admin" in lowered:
        category = "not_admin"
        response_status = 502
    elif "2fa" in lowered or "turnstile" in lowered:
        category = "unsupported_verification"
        response_status = 502
    elif upstream_status in {400, 422}:
        category = "validation"
        response_status = upstream_status
    elif upstream_status == 404:
        category = "not_found"
        response_status = 404
    elif upstream_status == 429:
        category = "rate_limited"
        response_status = 429
    elif upstream_status >= 500:
        category = "upstream_server"
        response_status = 502
    elif any(
        marker in lowered
        for marker in (
            "urlopen error",
            "timed out",
            "timeout",
            "name or service not known",
            "temporary failure in name resolution",
            "connection refused",
            "connection reset",
            "certificate verify failed",
            "ssl:",
            "tls",
        )
    ):
        category = "transport"
        response_status = 502
    elif any(
        marker in lowered
        for marker in ("响应不是 json", "expecting value", "jsondecodeerror")
    ):
        category = "invalid_response"
        response_status = 502
    else:
        category = "upstream_error"
        response_status = 502

    response: Dict[str, Any] = {
        "success": False,
        "source": "sub2api_upstream",
        "category": category,
        "message": message,
    }
    if upstream_status:
        response["upstream_status"] = upstream_status
    if upstream_code:
        response["upstream_code"] = upstream_code
    return response_status, response


def unwrap_sub2api_response(payload: Any) -> Tuple[bool, Any, Optional[str]]:
    if not isinstance(payload, dict):
        return False, payload, "响应不是 JSON 对象"
    if "code" in payload and payload.get("code") != 0:
        return False, payload, str(payload.get("message") or "code != 0")
    return True, payload.get("data"), None


def sub2api_admin_login(
    base_url: str, email: str, password: str
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    email = str(email or "").strip()
    password = str(password or "")
    if not email or not password:
        return False, {}, "sub2api 主站需要管理员邮箱和密码"
    ok, payload, error = admin_request_json(
        f"{normalize_base_url(base_url)}/api/v1/auth/login",
        payload={
            "email": email,
            "password": password,
            "turnstile_token": "",
        },
        method="POST",
    )
    if not ok:
        message = _upstream_response_message(payload, error)
        if "turnstile" in message.lower():
            message = "当前 sub2api 主站不支持 Turnstile 登录验证"
        return (
            False,
            payload if isinstance(payload, dict) else {},
            message or "sub2api 主站登录失败",
        )
    success, data, message = unwrap_sub2api_response(payload)
    if not success or not isinstance(data, dict):
        return False, {}, message or "sub2api 主站登录响应异常"
    if data.get("requires_2fa") or data.get("temp_token"):
        return False, {}, "当前 sub2api 主站不支持 2FA 登录验证"
    user = data.get("user") if isinstance(data.get("user"), dict) else {}
    if str(user.get("role") or "").strip().lower() != "admin":
        return False, {}, "账号可登录，但无主站管理权限"
    access_token = str(data.get("access_token") or "").strip()
    refresh_token = str(data.get("refresh_token") or "").strip()
    if not access_token or not refresh_token:
        return False, {}, "sub2api 主站登录没有返回完整 token"
    try:
        expires_in = max(0, int(data.get("expires_in") or 0))
    except (TypeError, ValueError):
        expires_in = 0
    return True, {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "access_expires_at": int(time.time()) + expires_in,
    }, None


def sub2api_admin_refresh_token(
    base_url: str, refresh_token: str
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    refresh_token = str(refresh_token or "").strip()
    if not refresh_token:
        return False, {}, "sub2api 主站 refresh token 为空"
    ok, payload, error = admin_request_json(
        f"{normalize_base_url(base_url)}/api/v1/auth/refresh",
        payload={"refresh_token": refresh_token},
        method="POST",
    )
    if not ok:
        return False, payload if isinstance(payload, dict) else {}, error
    success, data, message = unwrap_sub2api_response(payload)
    if not success or not isinstance(data, dict):
        return False, {}, message or "刷新 sub2api 主站登录态失败"
    return True, data, None


def _admin_sub2api_session_lock(site_id: int) -> threading.RLock:
    with ADMIN_SUB2API_SESSION_LOCKS_GUARD:
        return ADMIN_SUB2API_SESSION_LOCKS.setdefault(
            int(site_id), threading.RLock()
        )


def _persist_sub2api_admin_auth(site_id: int, auth: Dict[str, Any]) -> None:
    now = utc_now_iso()
    db_execute(
        """
        UPDATE admin_sites
        SET sub2api_access_token = ?, sub2api_refresh_token = ?,
            sub2api_access_expires_at = ?, browser_login_last_error = NULL,
            browser_login_last_check_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            str(auth.get("access_token") or ""),
            str(auth.get("refresh_token") or ""),
            int(auth.get("access_expires_at") or 0),
            now,
            now,
            int(site_id),
        ),
    )


def _persist_sub2api_admin_error(site_id: int, message: str) -> None:
    now = utc_now_iso()
    db_execute(
        """
        UPDATE admin_sites
        SET browser_login_last_error = ?, browser_login_last_check_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (str(message), now, now, int(site_id)),
    )


def ensure_sub2api_admin_session(
    site: Dict[str, Any],
    force_refresh: bool = False,
    rejected_access_token: str = "",
) -> Tuple[bool, str, Optional[str]]:
    site_id = int(site.get("id") or 0)
    if site_id <= 0:
        return False, "", "sub2api 主站记录无效"
    with _admin_sub2api_session_lock(site_id):
        current = db_query_one(
            "SELECT * FROM admin_sites WHERE id = ?", (site_id,)
        ) or dict(site)
        access_token = str(current.get("sub2api_access_token") or "").strip()
        refresh_token = str(current.get("sub2api_refresh_token") or "").strip()
        try:
            expires_at = int(current.get("sub2api_access_expires_at") or 0)
        except (TypeError, ValueError):
            expires_at = 0
        rejected_access_token = str(rejected_access_token or "").strip()
        if (
            force_refresh
            and access_token
            and rejected_access_token
            and access_token != rejected_access_token
        ):
            return True, access_token, None
        if (
            access_token
            and not force_refresh
            and expires_at
            > int(time.time()) + ADMIN_SUB2API_EXPIRY_SKEW_SECONDS
        ):
            return True, access_token, None

        refresh_error: Optional[str] = None
        if refresh_token:
            refreshed, data, refresh_error = sub2api_admin_refresh_token(
                str(current.get("base_url") or ""), refresh_token
            )
            if refreshed:
                try:
                    expires_in = max(0, int(data.get("expires_in") or 0))
                except (TypeError, ValueError):
                    expires_in = 0
                auth = {
                    "access_token": str(data.get("access_token") or "").strip(),
                    "refresh_token": str(
                        data.get("refresh_token") or refresh_token
                    ).strip(),
                    "access_expires_at": int(time.time()) + expires_in,
                }
                if auth["access_token"]:
                    _persist_sub2api_admin_auth(site_id, auth)
                    return True, str(auth["access_token"]), None

        logged_in, auth, login_error = sub2api_admin_login(
            str(current.get("base_url") or ""),
            str(current.get("login_username") or ""),
            str(current.get("login_password") or ""),
        )
        if not logged_in:
            message = login_error or refresh_error or "sub2api 主站登录失败"
            _persist_sub2api_admin_error(site_id, message)
            return False, "", message
        _persist_sub2api_admin_auth(site_id, auth)
        return True, str(auth["access_token"]), None


def sub2api_admin_request(
    site: Dict[str, Any],
    path: str,
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    parsed_path = urlparse(str(path or ""))
    request_path = parsed_path.path
    if parsed_path.scheme or parsed_path.netloc or not request_path.startswith("/"):
        return False, {}, "sub2api 管理请求路径无效"
    if request_path.startswith("/api/v1/admin/accounts"):
        return False, {}, "本系统不读取或监控 sub2api 号池"
    channel_path_allowed = request_path == "/api/v1/admin/channels" or bool(
        re.fullmatch(r"/api/v1/admin/channels/[0-9]+", request_path)
    )
    allowed = channel_path_allowed or request_path == "/api/v1/admin/groups/all"
    if not allowed:
        return False, {}, "sub2api 主站仅允许访问渠道和分组配置"

    session_ok, token, session_error = ensure_sub2api_admin_session(site)
    if not session_ok:
        return False, {}, session_error
    url = f"{normalize_base_url(str(site.get('base_url') or ''))}{path}"
    ok, response, error = admin_request_json(
        url,
        headers=sub2api_token_headers(token),
        payload=payload,
        method=method,
    )
    normalized = response if isinstance(response, dict) else {}
    if ok or not is_sub2api_auth_error(normalized, error):
        return ok, normalized, error

    session_ok, token, session_error = ensure_sub2api_admin_session(
        site,
        force_refresh=True,
        rejected_access_token=token,
    )
    if not session_ok:
        return False, {}, session_error
    ok, response, error = admin_request_json(
        url,
        headers=sub2api_token_headers(token),
        payload=payload,
        method=method,
    )
    return ok, response if isinstance(response, dict) else {}, error


def _sub2api_admin_channel_page(
    payload: Any,
) -> Tuple[bool, List[Dict[str, Any]], int, Optional[str]]:
    success, data, message = unwrap_sub2api_response(payload)
    if not success or not isinstance(data, dict):
        return False, [], 0, message or "sub2api 渠道响应异常"
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        return False, [], 0, "sub2api 渠道响应缺少 items"
    if any(not isinstance(value, dict) for value in raw_items):
        return False, [], 0, "sub2api 渠道响应包含无效项"
    items = [value for value in raw_items if isinstance(value, dict)]
    try:
        total = max(0, int(data.get("total") or 0))
    except (TypeError, ValueError):
        total = 0
    return True, items, total, None


def fetch_sub2api_admin_channels_by_token(
    base_url: str,
    access_token: str,
    keyword: str = "",
    page_size: int = 100,
    max_pages: int = 100,
) -> Tuple[bool, List[Dict[str, Any]], Dict[str, Any], Optional[str]]:
    if not str(access_token or "").strip():
        return False, [], {}, "sub2api 主站 access token 为空"
    page_size = max(1, min(500, int(page_size)))
    max_pages = max(1, int(max_pages))
    items: List[Dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        query = f"page={page}&page_size={page_size}"
        if str(keyword or "").strip():
            query += f"&search={quote(str(keyword).strip(), safe='')}"
        ok, payload, error = admin_request_json(
            f"{normalize_base_url(base_url)}/api/v1/admin/channels?{query}",
            headers=sub2api_token_headers(access_token),
        )
        if not ok:
            return (
                False,
                [],
                payload if isinstance(payload, dict) else {},
                error or "读取 sub2api 渠道失败",
            )
        page_ok, page_items, total, page_error = _sub2api_admin_channel_page(
            payload
        )
        if not page_ok:
            return (
                False,
                [],
                payload if isinstance(payload, dict) else {},
                page_error,
            )
        items.extend(page_items)
        if (total and len(items) >= total) or (
            not total and len(page_items) < page_size
        ):
            return True, items, {}, None
        if not page_items:
            return False, [], {}, "sub2api 渠道分页提前结束，拒绝返回截断数据"
    return False, [], {}, "sub2api 渠道超过最大分页页数，拒绝返回截断数据"


def fetch_sub2api_admin_groups(
    site: Dict[str, Any]
) -> Tuple[bool, List[Dict[str, Any]], Dict[str, Any], Optional[str]]:
    ok, payload, error = sub2api_admin_request(
        site, "/api/v1/admin/groups/all"
    )
    if not ok:
        return (
            False,
            [],
            payload if isinstance(payload, dict) else {},
            error or "读取 sub2api 分组失败",
        )
    success, data, message = unwrap_sub2api_response(payload)
    if not success or not isinstance(data, list):
        return (
            False,
            [],
            payload if isinstance(payload, dict) else {},
            message or "sub2api 分组响应异常",
        )
    return True, [value for value in data if isinstance(value, dict)], {}, None


def sub2api_admin_groups_payload(
    groups: List[Dict[str, Any]],
) -> Dict[str, Any]:
    data: Dict[str, Dict[str, Any]] = {}
    for item in groups:
        name = str(item.get("name") or f"#{item.get('id')}")
        data[name] = {
            "id": int(item.get("id") or 0),
            "name": name,
            "ratio": item.get("rate_multiplier"),
            "rate_multiplier": item.get("rate_multiplier"),
            "ratio_type": "number",
            "desc": item.get("description") or "",
            "platform": item.get("platform") or "",
            "status": item.get("status") or "",
        }
    return {"success": True, "data": data}


def _sub2api_group_ids(value: Any) -> List[int]:
    if not isinstance(value, list):
        return []
    result: List[int] = []
    for item in value:
        try:
            group_id = int(item)
        except (TypeError, ValueError):
            continue
        if group_id not in result:
            result.append(group_id)
    return result


def normalize_sub2api_admin_channel(
    channel: Dict[str, Any], groups_by_id: Dict[int, Dict[str, Any]]
) -> Dict[str, Any]:
    normalized = dict(channel)
    status = str(channel.get("status") or "disabled").strip().lower()
    normalized_status = (
        "active" if status == "active" else "disabled" if status == "disabled" else "error"
    )
    group_ids = _sub2api_group_ids(channel.get("group_ids"))
    model_pricing = channel.get("model_pricing")
    model_mapping = channel.get("model_mapping")
    normalized.update(
        {
            "source_platform": "sub2api",
            "normalized_status": normalized_status,
            "group_ids": group_ids,
            "groups": [
                {
                    "id": group_id,
                    "name": (
                        groups_by_id.get(group_id, {}).get("name")
                        or f"#{group_id}"
                    ),
                    "platform": groups_by_id.get(group_id, {}).get("platform")
                    or "",
                    "status": groups_by_id.get(group_id, {}).get("status") or "",
                    "rate_multiplier": groups_by_id.get(group_id, {}).get(
                        "rate_multiplier"
                    ),
                }
                for group_id in group_ids
            ],
            "model_pricing": (
                [value for value in model_pricing if isinstance(value, dict)]
                if isinstance(model_pricing, list)
                else []
            ),
            "model_mapping": dict(model_mapping)
            if isinstance(model_mapping, dict)
            else {},
            "capabilities": {
                "edit": True,
                "toggle": True,
                "create": False,
                "delete": False,
            },
        }
    )
    return normalized


def fetch_sub2api_admin_site_channels(
    site: Dict[str, Any],
    keyword: str = "",
    page_size: int = 100,
    max_pages: int = 100,
) -> Tuple[bool, List[Dict[str, Any]], Dict[str, Any], Optional[str]]:
    groups_ok, groups, groups_upstream, groups_error = fetch_sub2api_admin_groups(site)
    if not groups_ok:
        return False, [], groups_upstream, groups_error
    groups_by_id: Dict[int, Dict[str, Any]] = {}
    for group in groups:
        try:
            groups_by_id[int(group.get("id"))] = group
        except (TypeError, ValueError):
            continue

    page_size = max(1, min(500, int(page_size)))
    max_pages = max(1, int(max_pages))
    items: List[Dict[str, Any]] = []
    total = 0
    for page in range(1, max_pages + 1):
        query = f"page={page}&page_size={page_size}"
        if str(keyword or "").strip():
            query += f"&search={quote(str(keyword).strip(), safe='')}"
        ok, payload, error = sub2api_admin_request(
            site, f"/api/v1/admin/channels?{query}"
        )
        if not ok:
            return (
                False,
                [],
                payload if isinstance(payload, dict) else {},
                error or "读取 sub2api 渠道失败",
            )
        page_ok, page_items, page_total, page_error = _sub2api_admin_channel_page(
            payload
        )
        if not page_ok:
            return (
                False,
                [],
                payload if isinstance(payload, dict) else {},
                page_error,
            )
        items.extend(page_items)
        total = page_total or total
        if (total and len(items) >= total) or (
            not total and len(page_items) < page_size
        ):
            return True, [
                normalize_sub2api_admin_channel(item, groups_by_id)
                for item in items
            ], {
                "total": total or len(items),
                "page": page,
                "page_size": page_size,
            }, None
        if not page_items:
            return False, [], {}, "sub2api 渠道分页提前结束，拒绝返回截断数据"
    return False, [], {}, "sub2api 渠道超过最大分页页数，拒绝返回截断数据"


def fetch_sub2api_admin_channel_detail(
    site: Dict[str, Any], channel_id: int
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    ok, payload, error = sub2api_admin_request(
        site, f"/api/v1/admin/channels/{int(channel_id)}"
    )
    if not ok:
        return (
            False,
            payload if isinstance(payload, dict) else {},
            error or "读取 sub2api 渠道详情失败",
        )
    success, data, message = unwrap_sub2api_response(payload)
    if not success or not isinstance(data, dict):
        return (
            False,
            payload if isinstance(payload, dict) else {},
            message or "sub2api 渠道详情响应异常",
        )
    groups_ok, groups, groups_upstream, groups_error = fetch_sub2api_admin_groups(site)
    if not groups_ok:
        return False, groups_upstream, groups_error
    groups_by_id: Dict[int, Dict[str, Any]] = {}
    for group in groups:
        try:
            groups_by_id[int(group.get("id"))] = group
        except (TypeError, ValueError):
            continue
    return True, {
        "success": True,
        "data": normalize_sub2api_admin_channel(data, groups_by_id),
    }, None


SUB2API_ADMIN_CHANNEL_UPDATE_FIELDS = {
    "name",
    "description",
    "status",
    "group_ids",
    "model_pricing",
    "model_mapping",
    "billing_model_source",
    "restrict_models",
    "features",
    "features_config",
    "apply_pricing_to_account_stats",
    "account_stats_pricing_rules",
}
SUB2API_BILLING_MODEL_SOURCES = {
    "requested",
    "upstream",
    "channel_mapped",
}


def validate_sub2api_admin_channel_patch(patch: Dict[str, Any]) -> Optional[str]:
    unknown = sorted(set(patch) - SUB2API_ADMIN_CHANNEL_UPDATE_FIELDS)
    if unknown:
        return f"sub2api 渠道不允许更新字段：{', '.join(unknown)}"
    if not patch:
        return "没有要更新的 sub2api 渠道字段"
    if "status" in patch and str(patch.get("status") or "").lower() not in {
        "active",
        "disabled",
    }:
        return "sub2api 渠道状态只允许 active 或 disabled"
    if (
        "billing_model_source" in patch
        and str(patch.get("billing_model_source") or "").lower()
        not in SUB2API_BILLING_MODEL_SOURCES
    ):
        return (
            "sub2api 渠道 billing_model_source 只允许 "
            "channel_mapped、requested 或 upstream"
        )
    return None


def update_sub2api_admin_channel(
    site: Dict[str, Any], channel_id: int, patch: Dict[str, Any]
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    validation_error = validate_sub2api_admin_channel_patch(patch)
    if validation_error:
        return False, {}, validation_error
    request_payload = {field: patch[field] for field in patch}
    ok, payload, error = sub2api_admin_request(
        site,
        f"/api/v1/admin/channels/{int(channel_id)}",
        method="PUT",
        payload=request_payload,
    )
    if not ok:
        return False, payload, error or "更新 sub2api 渠道失败"
    success, data, message = unwrap_sub2api_response(payload)
    if not success:
        return (
            False,
            payload if isinstance(payload, dict) else {},
            message or "更新 sub2api 渠道失败",
        )
    return True, {"success": True, "data": data}, None


def sub2api_login(base_url: str, username: str, password: str) -> Tuple[bool, str, Dict[str, Any], Optional[str]]:
    email = (username or "").strip()
    password = password or ""
    if not email or not password:
        return False, "", {}, "sub2api 需要填写普通用户邮箱和密码"
    ok, payload, error = request_json(
        f"{normalize_base_url(base_url)}/api/v1/auth/login",
        payload={"email": email, "password": password},
        method="POST",
    )
    if not ok:
        return False, "", payload if isinstance(payload, dict) else {"raw": payload}, error
    success, data, message = unwrap_sub2api_response(payload)
    if not success or not isinstance(data, dict):
        return False, "", payload if isinstance(payload, dict) else {"raw": payload}, message or "登录失败"
    token = str(data.get("access_token") or "").strip()
    if not token:
        if data.get("requires_2fa") or data.get("temp_token"):
            return (
                False,
                "",
                payload if isinstance(payload, dict) else {"raw": payload},
                "sub2api 账号已开启 2FA，请先在上游网页完成登录，再使用“导入登录态”填写 auth_token/refresh_token",
            )
        return False, "", payload if isinstance(payload, dict) else {"raw": payload}, "登录成功但没有返回 access_token"
    return True, token, payload if isinstance(payload, dict) else {"raw": payload}, None


def sub2api_refresh_token(base_url: str, refresh_token: str) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    token = (refresh_token or "").strip()
    if not token:
        return False, {}, "refresh_token 为空"
    ok, payload, error = request_json(
        f"{normalize_base_url(base_url)}/api/v1/auth/refresh",
        payload={"refresh_token": token},
        method="POST",
    )
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error
    success, data, message = unwrap_sub2api_response(payload)
    if not success or not isinstance(data, dict):
        return False, payload if isinstance(payload, dict) else {"raw": payload}, message or "刷新登录态失败"
    return True, data, None


def _sub2api_refresh_lock(base_url: str) -> threading.RLock:
    key = normalize_base_url(base_url)
    with SUB2API_REFRESH_LOCKS_GUARD:
        return SUB2API_REFRESH_LOCKS.setdefault(key, threading.RLock())


def _sub2api_site_auth_lock(site_id: int) -> threading.RLock:
    with SUB2API_SITE_AUTH_LOCKS_GUARD:
        return SUB2API_SITE_AUTH_LOCKS.setdefault(int(site_id), threading.RLock())


def refresh_sub2api_auth(
    base_url: str, access_token: str, refresh_token: str
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """Refresh once per site/old-refresh-token and share the rotated result."""
    old_refresh = str(refresh_token or "").strip()
    if not old_refresh:
        return False, {}, "refresh_token 为空"
    cache_key = f"{normalize_base_url(base_url)}|{old_refresh}"
    now = time.monotonic()
    with _sub2api_refresh_lock(base_url):
        cached = SUB2API_REFRESH_CACHE.get(cache_key)
        if cached and now - float(cached.get("created_monotonic") or 0) < SUB2API_REFRESH_CACHE_TTL_SECONDS:
            return True, dict(cached["data"]), None
        ok, data, error = sub2api_refresh_token(base_url, old_refresh)
        if not ok:
            return False, data, error
        SUB2API_REFRESH_CACHE[cache_key] = {
            "data": dict(data),
            "created_monotonic": time.monotonic(),
        }
        return True, data, None


def sub2api_token_headers(access_token: str) -> Dict[str, str]:
    token = (access_token or "").strip()
    if token.lower().startswith("bearer "):
        return {"Authorization": token}
    return {"Authorization": f"Bearer {token}"}


def fetch_sub2api_groups_by_token(base_url: str, access_token: str) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    token = (access_token or "").strip()
    if not token:
        return False, {}, "auth_token 为空"
    headers = sub2api_token_headers(token)
    groups_ok, groups_payload, groups_error = request_json(
        f"{normalize_base_url(base_url)}/api/v1/groups/available",
        headers=headers,
    )
    if not groups_ok:
        return False, {"groups": groups_payload}, groups_error or "用户可用分组请求失败"
    groups_success, groups_data, groups_message = unwrap_sub2api_response(groups_payload)
    if not groups_success:
        return False, {"groups": groups_payload}, groups_message or "用户可用分组响应失败"

    rates_ok, rates_payload, rates_error = request_json(
        f"{normalize_base_url(base_url)}/api/v1/groups/rates",
        headers=headers,
    )
    if not rates_ok:
        return (
            False,
            {"groups": groups_payload, "rates": rates_payload},
            rates_error or "用户分组倍率请求失败",
        )
    rates_success, rates_data, rates_message = unwrap_sub2api_response(rates_payload)
    if not rates_success:
        return (
            False,
            {"groups": groups_payload, "rates": rates_payload},
            rates_message or "用户分组倍率响应失败",
        )
    if not isinstance(rates_data, dict):
        return (
            False,
            {"groups": groups_payload, "rates": rates_payload},
            "用户分组倍率响应格式异常",
        )

    return True, {
        "success": True,
        "data": groups_data,
        "user_rates": rates_data,
        "rates_error": None if rates_ok else rates_error,
    }, None


def fetch_sub2api_channel_models_by_token(base_url: str, access_token: str) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    token = (access_token or "").strip()
    if not token:
        return False, {}, "auth_token 为空"
    ok, payload, error = request_json(
        f"{normalize_base_url(base_url)}/api/v1/channels/available",
        headers=sub2api_token_headers(token),
    )
    if not ok:
        return False, {"channels": payload}, error or "用户可用渠道请求失败"
    success, data, message = unwrap_sub2api_response(payload)
    if not success:
        return False, {"channels": payload}, message or "用户可用渠道响应失败"
    if not isinstance(data, list):
        return False, {"channels": payload}, "用户可用渠道响应不是列表"
    return True, {"success": True, "data": data}, None


def fetch_sub2api_keys_by_token(
    base_url: str,
    access_token: str,
    page_size: int = 100,
    max_pages: int = 50,
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """翻页读取当前登录用户的全部 API key 及其实际绑定 group。"""
    token = (access_token or "").strip()
    if not token:
        return False, {}, "auth_token 为空"
    base = normalize_base_url(base_url)
    headers = sub2api_token_headers(token)
    all_items: List[Dict[str, Any]] = []
    last_data: Dict[str, Any] = {}
    completed = False
    for page in range(1, max_pages + 1):
        ok, payload, error = request_json(
            f"{base}/api/v1/keys?page={page}&page_size={int(page_size)}",
            headers=headers,
        )
        if not ok:
            return False, {"keys": payload}, error or "上游 key 列表请求失败"
        success, data, message = unwrap_sub2api_response(payload)
        if not success:
            return False, {"keys": payload}, message or "上游 key 列表响应失败"
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            return False, {"keys": payload}, "上游 key 列表响应格式异常"
        items = [item for item in data.get("items") or [] if isinstance(item, dict)]
        all_items.extend(items)
        last_data = data
        try:
            total = int(data.get("total"))
        except (TypeError, ValueError):
            total = None
        try:
            pages = int(data.get("pages"))
        except (TypeError, ValueError):
            pages = None
        if (
            not items
            or len(items) < page_size
            or (total is not None and len(all_items) >= total)
            or (pages is not None and page >= pages)
        ):
            completed = True
            break

    if not completed:
        return (
            False,
            {"truncated": True, "pages_read": max_pages},
            f"上游 key 列表超过最大分页页数 {max_pages}，结果不完整",
        )

    aggregated = dict(last_data)
    aggregated.update({
        "items": all_items,
        "total": len(all_items) if last_data.get("total") is None else last_data.get("total"),
        "page": 1,
        "page_size": page_size,
    })
    return True, {"success": True, "data": aggregated}, None


def fetch_sub2api_channel_monitors_by_token(base_url: str, access_token: str) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    token = (access_token or "").strip()
    if not token:
        return False, {}, "auth_token 为空"
    ok, payload, error = request_json(
        f"{normalize_base_url(base_url)}/api/v1/channel-monitors",
        headers=sub2api_token_headers(token),
    )
    if not ok:
        return False, {"monitors": payload}, error or "上游模型状态请求失败"
    success, data, message = unwrap_sub2api_response(payload)
    if not success:
        return False, {"monitors": payload}, message or "上游模型状态响应失败"
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return False, {"monitors": payload}, "上游模型状态响应格式异常"
    return True, {"success": True, "data": data}, None


def fetch_sub2api_model_data_by_token(base_url: str, access_token: str) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    channels_ok, channels_payload, channels_error = fetch_sub2api_channel_models_by_token(base_url, access_token)
    monitors_ok, monitors_payload, monitors_error = fetch_sub2api_channel_monitors_by_token(base_url, access_token)
    if not channels_ok and not monitors_ok:
        errors = [message for message in (channels_error, monitors_error) if message]
        return False, {"channels": channels_payload, "monitors": monitors_payload}, "；".join(errors) or "读取上游模型数据失败"
    return True, {
        "success": True,
        "channels": channels_payload.get("data") if channels_ok else [],
        "monitors": monitors_payload.get("data") if monitors_ok else {"items": []},
        "channels_error": None if channels_ok else channels_error,
        "monitors_error": None if monitors_ok else monitors_error,
    }, None


def _sub2api_login_auth(
    access_token: str, login_payload: Dict[str, Any]
) -> Dict[str, Any]:
    data = login_payload.get("data") if isinstance(login_payload, dict) else None
    data = data if isinstance(data, dict) else {}
    return {
        "access_token": str(access_token or "").strip(),
        "refresh_token": str(data.get("refresh_token") or "").strip(),
        "expires_in": data.get("expires_in"),
    }


def _sub2api_browser_session_required() -> Tuple[bool, Dict[str, Any], str]:
    return False, {
        "code": "BROWSER_SESSION_REQUIRED",
        "browser_sync_required": True,
    }, "请先在浏览器登录并同步"


_SUB2API_AUTH_CONTEXT_KEYS = frozenset({
    "refreshed_auth",
    "_auth_context",
    "access_token",
    "refresh_token",
    "authorization",
    "password",
    "browser_refresh_cookie",
    "browser_session_id",
})


def _strip_sub2api_auth_context(value: Any) -> Any:
    """Remove authentication material before a payload leaves the auth layer."""
    if isinstance(value, dict):
        return {
            key: _strip_sub2api_auth_context(item)
            for key, item in value.items()
            if str(key or "").strip().lower() not in _SUB2API_AUTH_CONTEXT_KEYS
        }
    if isinstance(value, list):
        return [_strip_sub2api_auth_context(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_strip_sub2api_auth_context(item) for item in value)
    return value


def _attach_sub2api_auth_context(
    payload: Any,
    auth: Optional[Dict[str, Any]],
    access_token: str,
    include_auth_context: bool,
) -> Dict[str, Any]:
    normalized = dict(payload) if isinstance(payload, dict) else {"raw": payload}
    if auth:
        normalized["refreshed_auth"] = dict(auth)
    if include_auth_context:
        normalized["_auth_context"] = {"access_token": str(access_token or "").strip()}
    return normalized


def _fetch_sub2api_with_auth_fallback(
    fetch_by_token: Any,
    base_url: str,
    username: str = "",
    password: str = "",
    auth_mode: str = "password",
    access_token: str = "",
    refresh_token: str = "",
    site_id: int = 0,
    include_auth_context: bool = False,
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    def site_snapshot() -> Dict[str, str]:
        values = {
            "base_url": str(base_url or ""),
            "username": str(username or ""),
            "password": str(password or ""),
            "auth_mode": str(auth_mode or "password"),
            "access_token": str(access_token or ""),
            "refresh_token": str(refresh_token or ""),
        }
        if int(site_id or 0) <= 0:
            return values
        try:
            row = db_query_one("SELECT * FROM sites WHERE id = ?", (int(site_id),))
        except Exception:
            # A request can still use the immutable snapshot when the database
            # is temporarily unavailable; the next request will retry reload.
            return values
        if not isinstance(row, dict):
            return values
        platform = str(row.get("platform") or "sub2api").strip().lower()
        if platform != "sub2api":
            return values
        for key, source in (
            ("base_url", "base_url"),
            ("username", "login_username"),
            ("password", "login_password"),
            ("auth_mode", "auth_mode"),
            ("access_token", "access_token"),
            ("refresh_token", "refresh_token"),
        ):
            if source in row and row.get(source) is not None:
                values[key] = str(row.get(source) or "")
        return values

    def run(values: Dict[str, str]) -> Tuple[bool, Dict[str, Any], Optional[str]]:
        current_base = values["base_url"]
        current_username = values["username"]
        current_password = values["password"]
        mode = values["auth_mode"].strip().lower()
        current_access = values["access_token"].strip()
        current_refresh = values["refresh_token"].strip()
        browser_mode = mode == BROWSER_AUTH_MODE

        def persist(
            auth: Dict[str, Any],
            expected_access: str,
            expected_refresh: str,
            restore_browser_session: Optional[bool] = None,
        ) -> None:
            if int(site_id or 0) <= 0:
                return
            persist_sub2api_refreshed_auth(
                int(site_id),
                auth,
                expected_access_token=expected_access,
                expected_refresh_token=expected_refresh,
                restore_browser_session=(
                    browser_mode
                    if restore_browser_session is None
                    else restore_browser_session
                ),
            )

        def fetch_with_token(
            token: str, auth: Optional[Dict[str, Any]] = None
        ) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
            ok, payload, error = fetch_by_token(current_base, token)
            normalized = _attach_sub2api_auth_context(
                payload, auth, token, include_auth_context
            )
            return ok, normalized, error, classify_sub2api_auth_failure(payload, error)

        if mode == "password":
            login_ok, login_token, login_payload, login_error = sub2api_login(
                current_base, current_username, current_password
            )
            if not login_ok:
                return False, {"login": login_payload}, login_error or "登录失败"
            ok, payload, error, _category = fetch_with_token(login_token)
            return ok, payload, error

        if mode not in {"token", BROWSER_AUTH_MODE}:
            return False, {}, "auth_mode invalid"

        if current_access:
            ok, payload, error, category = fetch_with_token(current_access)
            if ok:
                return True, payload, None
            if category != "auth":
                return False, payload, error
        elif mode == "token" and not current_refresh:
            return False, {}, "auth_token 为空"

        if current_refresh:
            refresh_ok, refreshed, refresh_error = refresh_sub2api_auth(
                current_base, current_access, current_refresh
            )
            if refresh_ok:
                rotated_access = str(refreshed.get("access_token") or "").strip()
                if not rotated_access:
                    return False, {"refresh": refreshed}, "刷新成功但没有返回 access_token"
                rotated_auth = {
                    "access_token": rotated_access,
                    "refresh_token": str(
                        refreshed.get("refresh_token") or current_refresh
                    ).strip(),
                    "expires_in": refreshed.get("expires_in"),
                }
                ok, payload, error, category = fetch_with_token(
                    rotated_access, rotated_auth
                )
                if ok:
                    persist(
                        rotated_auth,
                        current_access,
                        current_refresh,
                        restore_browser_session=True,
                    )
                    return True, payload, None
                persist(
                    rotated_auth,
                    current_access,
                    current_refresh,
                    restore_browser_session=False,
                )
                if category != "auth":
                    return False, payload, error
                # The rotated token was rejected as well.  Password fallback,
                # when enabled, must use the newest session as its CAS cursor.
                current_access = rotated_auth["access_token"]
                current_refresh = rotated_auth["refresh_token"]
            else:
                refresh_category = classify_sub2api_auth_failure(
                    refreshed, refresh_error
                )
                if mode == "token" or refresh_category != "auth":
                    return (
                        False,
                        {"refresh": refreshed},
                        refresh_error or "登录态刷新失败",
                    )

        if mode == "token":
            return False, {}, "登录态已过期"

        if not current_username.strip() or not current_password:
            return _sub2api_browser_session_required()

        login_ok, login_token, login_payload, login_error = sub2api_login(
            current_base, current_username, current_password
        )
        if not login_ok:
            login_category = classify_sub2api_auth_failure(login_payload, login_error)
            if login_category == "interactive":
                return _sub2api_browser_session_required()
            return False, {"login": login_payload}, login_error or "登录失败"

        login_auth = _sub2api_login_auth(login_token, login_payload)
        ok, payload, error, category = fetch_with_token(login_token, login_auth)
        if ok:
            persist(
                login_auth,
                current_access,
                current_refresh,
                restore_browser_session=True,
            )
            return True, payload, None
        persist(
            login_auth,
            current_access,
            current_refresh,
            restore_browser_session=False,
        )
        if category == "interactive":
            return _sub2api_browser_session_required()
        return False, payload, error

    if int(site_id or 0) > 0:
        with _sub2api_site_auth_lock(int(site_id)):
            return run(site_snapshot())
    return run(site_snapshot())


def fetch_sub2api_model_data(
    base_url: str,
    username: str = "",
    password: str = "",
    auth_mode: str = "password",
    access_token: str = "",
    refresh_token: str = "",
    site_id: int = 0,
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    return _fetch_sub2api_with_auth_fallback(
        fetch_sub2api_model_data_by_token,
        base_url,
        username=username,
        password=password,
        auth_mode=auth_mode,
        access_token=access_token,
        refresh_token=refresh_token,
        site_id=site_id,
    )


def fetch_sub2api_user_groups(
    base_url: str,
    username: str = "",
    password: str = "",
    auth_mode: str = "password",
    access_token: str = "",
    refresh_token: str = "",
    include_auth_context: bool = False,
    site_id: int = 0,
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    return _fetch_sub2api_with_auth_fallback(
        fetch_sub2api_groups_by_token,
        base_url,
        username=username,
        password=password,
        auth_mode=auth_mode,
        access_token=access_token,
        refresh_token=refresh_token,
        site_id=site_id,
        include_auth_context=include_auth_context,
    )


def fetch_sub2api_keys(
    base_url: str,
    username: str = "",
    password: str = "",
    auth_mode: str = "password",
    access_token: str = "",
    refresh_token: str = "",
    include_auth_context: bool = False,
    site_id: int = 0,
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """Read a user's keys through the same AT -> RT -> password executor."""
    return _fetch_sub2api_with_auth_fallback(
        fetch_sub2api_keys_by_token,
        base_url,
        username=username,
        password=password,
        auth_mode=auth_mode,
        access_token=access_token,
        refresh_token=refresh_token,
        site_id=site_id,
        include_auth_context=include_auth_context,
    )


# --- 账户额度（登录后可拿到的用户信息）-----------------------------------------
# NewAPI: GET /api/user/self（系统访问令牌 + New-Api-User）
# sub2api: GET /api/v1/auth/me（Bearer access_token），返回 balance / subscriptions

NEWAPI_QUOTA_PER_UNIT = 500000.0  # NewAPI 默认 QuotaPerUnit：500000 额度 = $1


def fetch_newapi_account(base_url: str, access_token: str, user_id: str = "") -> Tuple[bool, Dict[str, Any], Optional[str]]:
    token = (access_token or "").strip()
    if not token:
        return False, {}, "缺少系统访问令牌，无法读取账户额度"
    ok, payload, error = request_json(
        f"{normalize_base_url(base_url)}/api/user/self",
        headers=newapi_auth_headers(access_token, user_id),
    )
    if not ok:
        return False, payload if isinstance(payload, dict) else {"raw": payload}, error or "读取 /api/user/self 失败"
    if not isinstance(payload, dict) or not payload.get("success"):
        message = str(payload.get("message")) if isinstance(payload, dict) and payload.get("message") else None
        return False, payload if isinstance(payload, dict) else {"raw": payload}, message or "/api/user/self success=false"
    data = payload.get("data")
    return True, data if isinstance(data, dict) else {}, None


def fetch_newapi_account_with_headers(
    base_url: str, headers: Dict[str, str]
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    ok, payload, error = request_json(
        f"{normalize_base_url(base_url)}/api/user/self",
        headers=dict(headers),
    )
    if not ok:
        return (
            False,
            payload if isinstance(payload, dict) else {"raw": payload},
            error or "读取 /api/user/self 失败",
        )
    if not isinstance(payload, dict) or not payload.get("success"):
        message = (
            str(payload.get("message"))
            if isinstance(payload, dict) and payload.get("message")
            else None
        )
        return (
            False,
            payload if isinstance(payload, dict) else {"raw": payload},
            message or "/api/user/self success=false",
        )
    data = payload.get("data")
    return True, data if isinstance(data, dict) else {}, None


def fetch_newapi_groups_with_headers(
    base_url: str, headers: Dict[str, str]
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    errors: List[str] = []
    for path in ("/api/user/self/groups", "/api/user/groups"):
        ok, payload, error = request_json(
            f"{normalize_base_url(base_url)}{path}", headers=dict(headers)
        )
        if ok and isinstance(payload, dict) and payload.get("success"):
            return True, payload, None
        errors.append(f"{path}: {newapi_auth_failure_message(payload, error)}")
    return False, {"errors": errors}, "访问令牌分组采集失败：" + "；".join(errors)


def newapi_site_browser_auth_headers(session: Dict[str, Any]) -> Dict[str, str]:
    token = str(session.get("access_token") or "").strip()
    user_id = str(session.get("access_user_id") or "").strip()
    session_id = str(session.get("browser_session_id") or "").strip()
    refresh_cookie = str(
        session.get("browser_cookie")
        or session.get("browser_refresh_cookie")
        or ""
    ).strip()
    headers: Dict[str, str] = {}
    if token:
        normalized_token = token.removeprefix("Bearer ").removeprefix("bearer ").strip()
        headers["Authorization"] = (
            f"Bearer {normalized_token}" if session_id else normalized_token
        )
    if user_id:
        headers["New-Api-User"] = user_id
    if session_id:
        headers["X-Auth-Session"] = session_id
    if refresh_cookie:
        headers["Cookie"] = refresh_cookie
    return headers


def _newapi_status_from_payload(payload: Any) -> Optional[int]:
    """Return the HTTP-like status code from a request_json failure payload."""
    if isinstance(payload, dict):
        raw = payload.get("status")
        if isinstance(raw, int):
            return raw
        if isinstance(raw, str) and raw.isdigit():
            return int(raw)
    return None


def newapi_browser_request(
    site: Dict[str, Any],
    method: str,
    path: str,
    payload: Optional[Dict[str, Any]] = None,
    query: Optional[str] = None,
    force_refresh_on_401: bool = True,
) -> Tuple[bool, Any, Optional[str]]:
    """Unified NewAPI request executor with auth-mode awareness.

    Behaviour:
    * token mode: one request using the system access token + New-Api-User
      (and X-Security-Proof when present).
    * browser mode: one request using the full browser session bundle.  If
      the upstream returns an explicit 401/403 we ``force=True`` refresh the
      browser session and retry **at most once**.  Network errors, timeouts,
      429 and 5xx never trigger a refresh.
    """
    base = normalize_base_url(str(site.get("base_url") or ""))
    url = f"{base}{path}"
    if query:
        url = f"{url}?{query}" if "?" not in url else f"{url}&{query}"
    auth_mode = str(site.get("auth_mode") or "token").strip().lower()

    if auth_mode in {BROWSER_AUTH_MODE, "password"}:
        # Re-read latest state so a concurrent refresh by another caller is
        # honoured before we pick headers.
        latest = db_query_one(
            """
            SELECT access_token, access_user_id, browser_cookie, browser_refresh_cookie,
                   browser_session_id, browser_access_expires_at, auth_mode
            FROM sites WHERE id = ?
            """,
            (int(site.get("id") or 0),),
        )
        if latest:
            site.update(latest)
        ready, ready_error = ensure_newapi_site_browser_session(site)
        if not ready:
            return False, {}, ready_error or "登录态已过期，请重新登录"
        headers = newapi_site_browser_auth_headers(site)
        ok, raw, error = request_json(url, headers=headers, payload=payload, method=method)
        if ok:
            return True, raw, None
        status = _newapi_status_from_payload(raw)
        if force_refresh_on_401 and status in (401, 403):
            refreshed, refresh_error = refresh_newapi_site_browser_session(
                site, force=True
            )
            if not refreshed:
                return False, raw, "登录态已失效，请重新验证登录"
            ready, ready_error = ensure_newapi_site_browser_session(site)
            if not ready:
                return False, raw, "请重新网页登录/同步后再试"
            headers = newapi_site_browser_auth_headers(site)
            ok, raw, error = request_json(
                url, headers=headers, payload=payload, method=method
            )
            if ok:
                return True, raw, None
            status = _newapi_status_from_payload(raw)
            if status in (401, 403):
                return False, raw, "登录态已失效，请重新验证登录"
            return False, raw, newapi_auth_failure_message(raw, error)
        return False, raw, newapi_auth_failure_message(raw, error)

    # token / system-token path
    headers = newapi_auth_headers(
        str(site.get("access_token") or ""),
        str(site.get("access_user_id") or ""),
    )
    proof = str(site.get("security_proof") or "").strip()
    if proof:
        headers["X-Security-Proof"] = proof
    ok, raw, error = request_json(url, headers=headers, payload=payload, method=method)
    if ok:
        return True, raw, None
    status = _newapi_status_from_payload(raw)
    if force_refresh_on_401 and status in (401, 403):
        return False, raw, "上游令牌已失效，请刷新或重新录入"
    return False, raw, error or "NewAPI 上游调用失败"


def validate_newapi_site_browser_session(
    base_url: str, session: Dict[str, Any]
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    access_token = str(session.get("access_token") or "").strip()
    access_user_id = str(session.get("access_user_id") or "").strip()
    browser_cookie = str(session.get("browser_cookie") or "").strip()
    if not access_token and not browser_cookie:
        return False, {}, "没有登录态，请提前登录"
    if not access_user_id:
        return False, {}, "浏览器登录态缺少 NewAPI 用户 ID"
    headers = newapi_site_browser_auth_headers(session)
    account_ok, account, account_error = fetch_newapi_account_with_headers(
        base_url, headers
    )
    if not account_ok:
        return False, {}, account_error or "登录态已过期，请重新登录"
    account_id = str(account.get("id") or "").strip()
    if account_id and account_id != access_user_id:
        return False, {}, "浏览器登录用户与 NewAPI 用户 ID 不匹配"
    groups_ok, groups, groups_error = fetch_newapi_groups_with_headers(
        base_url, headers
    )
    if not groups_ok:
        return False, {}, groups_error or "当前登录态无法读取分组"
    return True, {"account": account, "groups": groups}, None


def persist_newapi_site_browser_session(
    site_id: int,
    session: Dict[str, Any],
    auth_mode: str = BROWSER_AUTH_MODE,
    preserve_login_credentials: bool = False,
) -> None:
    try:
        expires_at = int(session.get("browser_access_expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0
    access_token = str(session.get("access_token") or "").strip()
    access_user_id = str(session.get("access_user_id") or "").strip()
    browser_cookie = str(session.get("browser_cookie") or "").strip() or None
    refresh_cookie = str(session.get("browser_refresh_cookie") or "").strip() or None
    session_id = str(session.get("browser_session_id") or "").strip() or None
    now = utc_now_iso()
    db_execute(
        """
        UPDATE sites
        SET auth_mode = ?, login_enabled = 1,
            login_username = CASE WHEN ? THEN login_username ELSE NULL END,
            login_password = CASE WHEN ? THEN login_password ELSE NULL END,
            access_token = ?, access_user_id = ?,
            browser_cookie = ?, browser_refresh_cookie = ?, browser_session_id = ?,
            browser_access_expires_at = ?,
            session_sync_status = 'ready', session_sync_error = NULL,
            session_synced_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            auth_mode,
            1 if preserve_login_credentials else 0,
            1 if preserve_login_credentials else 0,
            access_token,
            access_user_id,
            browser_cookie,
            refresh_cookie,
            session_id,
            expires_at,
            now,
            now,
            int(site_id),
        ),
    )


def _newapi_site_browser_session_lock(site_id: int) -> threading.RLock:
    with NEWAPI_SITE_BROWSER_SESSION_LOCKS_GUARD:
        return NEWAPI_SITE_BROWSER_SESSION_LOCKS.setdefault(site_id, threading.RLock())


def _newapi_refresh_cookie_from_response(
    headers: Dict[str, Any], previous: str = ""
) -> str:
    raw_values = headers.get("set-cookie") if isinstance(headers, dict) else []
    if isinstance(raw_values, str):
        raw_values = [raw_values]
    for raw in raw_values or []:
        cookie = SimpleCookie()
        try:
            cookie.load(str(raw))
        except Exception:
            continue
        morsel = cookie.get("new_api_refresh")
        if morsel is not None:
            return f"new_api_refresh={morsel.value}"
    previous_value = str(previous or "").strip()
    return previous_value if previous_value.startswith("new_api_refresh=") else ""


def _newapi_site_browser_auth_data(
    site: Dict[str, Any], payload: Any, response_headers: Dict[str, Any]
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None, "NewAPI 刷新没有返回认证数据"
    access_token = str(data.get("access_token") or "").strip()
    user = data.get("user") if isinstance(data.get("user"), dict) else {}
    access_user_id = str(user.get("id") or site.get("access_user_id") or "").strip()
    browser_session = (
        data.get("session") if isinstance(data.get("session"), dict) else {}
    )
    session_id = str(browser_session.get("sid") or "").strip()
    if not access_token or not access_user_id or not session_id:
        return None, "NewAPI 刷新没有返回有效的网页登录态"
    return {
        "access_token": access_token,
        "access_user_id": access_user_id,
        "browser_refresh_cookie": _newapi_refresh_cookie_from_response(
            response_headers, str(site.get("browser_refresh_cookie") or "")
        ),
        "browser_session_id": session_id,
        "browser_access_expires_at": data.get("access_expires_at") or 0,
    }, None


def _newapi_password_login_bundle(
    base_url: str,
    username: str,
    password: str,
    verification_code: str = "",
    access_user_id: str = "",
    previous_refresh_cookie: str = "",
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """Log a NewAPI ordinary user in and return a refreshable session bundle."""
    normalized_base = normalize_base_url(base_url)
    if not normalized_base or not username or not password:
        return False, {}, "请填写 NewAPI 用户名和密码"
    ok, payload, error, response_headers = request_json_with_headers(
        f"{normalized_base}/api/user/login",
        payload={"username": username, "password": password},
        method="POST",
    )
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict) and data.get("require_2fa"):
        flow_token = str(data.get("flow_token") or "").strip()
        if not verification_code:
            return False, {"requires_2fa": True}, "需要 2FA 验证码"
        if not flow_token:
            return False, {}, "2FA 登录流程已失效，请重新验证用户名和密码"
        ok, payload, error, response_headers = request_json_with_headers(
            f"{normalized_base}/api/user/login/2fa",
            payload={"code": verification_code, "flow_token": flow_token},
            method="POST",
        )
    if not ok or not isinstance(payload, dict) or not payload.get("success"):
        return False, {}, newapi_auth_failure_message(payload, error)
    source = {
        "access_user_id": access_user_id,
        "browser_refresh_cookie": previous_refresh_cookie,
    }
    auth_data, auth_error = _newapi_site_browser_auth_data(
        source, payload, response_headers
    )
    if not auth_data:
        return False, {}, auth_error or "NewAPI 登录没有返回有效登录态"
    return True, auth_data, None


def login_newapi_site_with_password(
    site: Dict[str, Any], verification_code: str = ""
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    if str(site.get("platform") or "newapi").strip().lower() != "newapi":
        return False, {}, "只有 NewAPI 渠道支持用户名密码登录"
    if str(site.get("auth_mode") or "").strip().lower() != "password":
        return False, {}, "请先将认证方式切换为用户名密码"
    site_id = int(site.get("id") or 0)
    if site_id <= 0:
        return False, {}, "渠道记录无效"
    with _newapi_site_browser_session_lock(site_id):
        latest = db_query_one("SELECT * FROM sites WHERE id = ?", (site_id,))
        if latest:
            site.update(latest)
        ok, auth_data, error = _newapi_password_login_bundle(
            str(site.get("base_url") or ""),
            str(site.get("login_username") or "").strip(),
            str(site.get("login_password") or ""),
            verification_code=verification_code,
            access_user_id=str(site.get("access_user_id") or ""),
            previous_refresh_cookie=str(site.get("browser_refresh_cookie") or ""),
        )
        if not ok:
            return False, auth_data, error
        persist_newapi_site_browser_session(
            site_id,
            auth_data,
            auth_mode="password",
            preserve_login_credentials=True,
        )
        site.update(auth_data)
        site["auth_mode"] = "password"
        groups_ok, groups_payload, groups_error = fetch_newapi_groups_for_site(site)
        groups = parse_groups_payload(groups_payload) if groups_ok else {}
        return True, {
            "groups_count": len(groups),
            "warning": None
            if groups_ok
            else newapi_auth_failure_message(groups_payload, groups_error),
        }, None


def probe_newapi_password_login(
    base_url: str, username: str, password: str, verification_code: str = ""
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """Validate credentials without persisting them to a monitoring site."""
    ok, auth_data, error = _newapi_password_login_bundle(
        base_url, username, password, verification_code=verification_code
    )
    if not ok:
        return False, auth_data, error
    groups_ok, groups_payload, groups_error = fetch_newapi_groups_with_headers(
        base_url, newapi_site_browser_auth_headers(auth_data)
    )
    groups = parse_groups_payload(groups_payload) if groups_ok else {}
    return groups_ok, {
        "groups_count": len(groups),
        "warning": None
        if groups_ok
        else newapi_auth_failure_message(groups_payload, groups_error),
    }, None if groups_ok else newapi_auth_failure_message(groups_payload, groups_error)


def refresh_newapi_site_browser_session(
    site: Dict[str, Any], force: bool = False
) -> Tuple[bool, Optional[str]]:
    site_id = int(site.get("id") or 0)
    if site_id <= 0:
        return False, "渠道记录无效，无法刷新网页登录态"
    with _newapi_site_browser_session_lock(site_id):
        latest = db_query_one(
            """
            SELECT access_token, access_user_id, browser_refresh_cookie,
                   browser_session_id, browser_access_expires_at
            FROM sites WHERE id = ?
            """,
            (site_id,),
        )
        if latest:
            site.update(latest)
        try:
            expires_at = int(site.get("browser_access_expires_at") or 0)
        except (TypeError, ValueError):
            expires_at = 0
        if not force and expires_at > int(time.time()) + 60:
            return True, None
        refresh_cookie = str(site.get("browser_refresh_cookie") or "").strip()
        session_id = str(site.get("browser_session_id") or "").strip()
        if not refresh_cookie or not session_id:
            return False, "NewAPI 网页登录态缺少 Refresh Cookie 或 Session ID"
        origin = site_origin(str(site.get("base_url") or ""))
        if not origin:
            return False, "渠道 URL 无法生成有效 Origin，请检查渠道地址"
        ok, payload, error, response_headers = request_json_with_headers(
            f"{normalize_base_url(str(site.get('base_url') or ''))}/api/user/auth/refresh",
            headers={
                "Cookie": refresh_cookie,
                "X-Auth-Session": session_id,
                "Origin": origin,
            },
            method="POST",
        )
        if not ok or not isinstance(payload, dict) or not payload.get("success"):
            return False, _admin_browser_refresh_error(payload, error).replace(
                "主站", "NewAPI 站点"
            )
        auth_data, auth_error = _newapi_site_browser_auth_data(
            site, payload, response_headers
        )
        if not auth_data:
            return False, auth_error or "NewAPI 刷新没有返回有效的网页登录态"
        auth_mode = str(site.get("auth_mode") or BROWSER_AUTH_MODE).strip().lower()
        persist_newapi_site_browser_session(
            site_id,
            auth_data,
            auth_mode=auth_mode if auth_mode in {BROWSER_AUTH_MODE, "password"} else BROWSER_AUTH_MODE,
            preserve_login_credentials=auth_mode == "password",
        )
        site.update(auth_data)
        return True, None


def ensure_newapi_site_browser_session(
    site: Dict[str, Any]
) -> Tuple[bool, Optional[str]]:
    access_token = str(site.get("access_token") or "").strip()
    access_user_id = str(site.get("access_user_id") or "").strip()
    browser_cookie = str(site.get("browser_cookie") or "").strip()
    if (not access_token and not browser_cookie) or not access_user_id:
        return False, "没有登录态，请提前登录"
    if browser_cookie:
        return True, None
    session_id = str(site.get("browser_session_id") or "").strip()
    refresh_cookie = str(site.get("browser_refresh_cookie") or "").strip()
    if not session_id and not refresh_cookie:
        return True, None
    if not session_id or not refresh_cookie:
        return False, "NewAPI 网页登录态不完整，请重新登录"
    try:
        expires_at = int(site.get("browser_access_expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0
    if expires_at <= 0 or expires_at > int(time.time()) + 60:
        return True, None
    return refresh_newapi_site_browser_session(site)


def fetch_newapi_account_for_site(
    site: Dict[str, Any]
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    if str(site.get("auth_mode") or "").strip().lower() not in {
        BROWSER_AUTH_MODE,
        "password",
    }:
        return fetch_newapi_account(
            str(site.get("base_url") or ""),
            str(site.get("access_token") or ""),
            str(site.get("access_user_id") or ""),
        )
    ok, payload, error = newapi_browser_request(site, "GET", "/api/user/self")
    if not ok:
        return False, payload if isinstance(payload, dict) else {}, error or "读取 /api/user/self 失败"
    if not isinstance(payload, dict) or not payload.get("success"):
        message = (
            str(payload.get("message"))
            if isinstance(payload, dict) and payload.get("message")
            else None
        )
        return False, payload if isinstance(payload, dict) else {}, message or "/api/user/self success=false"
    data = payload.get("data")
    return True, data if isinstance(data, dict) else {}, None


def fetch_newapi_groups_for_site(
    site: Dict[str, Any]
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    if str(site.get("auth_mode") or "").strip().lower() not in {
        BROWSER_AUTH_MODE,
        "password",
    }:
        return fetch_newapi_groups_with_access_token(
            str(site.get("base_url") or ""),
            str(site.get("access_token") or ""),
            str(site.get("access_user_id") or ""),
        )
    errors: List[str] = []
    for path in ("/api/user/self/groups", "/api/user/groups"):
        ok, payload, error = newapi_browser_request(site, "GET", path)
        if ok and isinstance(payload, dict) and payload.get("success"):
            return True, payload, None
        message = (
            str(payload.get("message") or "")
            if isinstance(payload, dict)
            else ""
        )
        errors.append(f"{path}: {message or error or 'success=false'}")
    return False, {"errors": errors}, "访问令牌分组采集失败：" + "；".join(errors)


def normalize_newapi_account(data: Dict[str, Any]) -> Dict[str, Any]:
    def to_usd(value: Any) -> Optional[float]:
        try:
            return round(float(value) / NEWAPI_QUOTA_PER_UNIT, 4)
        except (TypeError, ValueError):
            return None

    quota = data.get("quota")
    used_quota = data.get("used_quota")
    return {
        "platform": "newapi",
        "username": str(data.get("username") or ""),
        "group": str(data.get("group") or ""),
        "balance_usd": to_usd(quota),          # NewAPI quota = 剩余额度
        "used_usd": to_usd(used_quota),
        "request_count": data.get("request_count"),
        "raw_quota": quota,
        "raw_used_quota": used_quota,
        "quota_per_unit": NEWAPI_QUOTA_PER_UNIT,
        "subscriptions": [],
    }


def fetch_sub2api_account_by_token(base_url: str, access_token: str) -> Tuple[bool, Any, Optional[str]]:
    token = (access_token or "").strip()
    if not token:
        return False, {}, "auth_token 为空"
    ok, payload, error = request_json(
        f"{normalize_base_url(base_url)}/api/v1/auth/me",
        headers=sub2api_token_headers(token),
    )
    if not ok:
        return False, {"account": payload}, error or "读取 /api/v1/auth/me 失败"
    success, data, message = unwrap_sub2api_response(payload)
    if not success or not isinstance(data, dict):
        return False, {"account": payload}, message or "账户信息响应失败"
    return True, data, None


def validate_sub2api_browser_session(
    base_url: str, access_token: str
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    token = str(access_token or "").strip()
    if not token:
        return False, {}, "没有登录态，请提前登录"
    account_ok, account, account_error = fetch_sub2api_account_by_token(
        base_url, token
    )
    if not account_ok:
        return False, {}, account_error or "登录态已过期，请重新登录"
    groups_ok, groups, groups_error = fetch_sub2api_groups_by_token(base_url, token)
    if not groups_ok:
        return False, {}, groups_error or "当前登录态无法读取分组"
    return True, {"account": account, "groups": groups}, None


def persist_site_browser_session(
    site_id: int,
    access_token: str,
    refresh_token: str,
    expires_at: str,
    request_id: str = "",
    expected_origin: str = "",
) -> bool:
    """Persist a sub2api browser session.

    Ordinary refresh/password fallback writes retain their existing behaviour.
    A completion write carries its one-time request ID and uses a database CAS
    condition, so it cannot overwrite a newer request or a manual auth-mode
    change after validation has already started.
    """
    now = utc_now_iso()
    params: List[Any] = [
        str(access_token or "").strip(),
        str(refresh_token or "").strip(),
        normalize_session_expiry(expires_at) or None,
        now,
        now,
        int(site_id),
    ]
    sql = """
        UPDATE sites AS s
        SET auth_mode = 'browser', login_enabled = 1,
            access_token = ?, refresh_token = ?, token_expires_at = ?,
            session_sync_status = 'ready', session_sync_error = NULL,
            session_synced_at = ?, updated_at = ?
        WHERE s.id = ?
    """
    request_id = str(request_id or "").strip()
    if not request_id:
        db_execute(sql, params)
        return True

    origin = site_origin(expected_origin)
    if not origin:
        return False
    sql += """
          AND s.platform = 'sub2api'
          AND s.auth_mode = 'browser'
          AND EXISTS (
              SELECT 1
              FROM browser_session_sync_requests AS r
              WHERE r.id = ?
                AND r.site_id = s.id
                AND r.admin_site_id IS NULL
                AND r.platform = 'sub2api'
                AND r.target_origin = ?
                AND r.status = 'validating'
          )
    """
    params.extend((request_id, origin))
    return db_execute_rowcount(sql, params) > 0


def mark_site_browser_session_expired(
    site_id: int,
    message: str,
    request_id: str = "",
    expected_origin: str = "",
) -> bool:
    now = utc_now_iso()
    params: List[Any] = [
        str(message or "登录态已过期，请重新登录"),
        now,
        int(site_id),
    ]
    sql = """
        UPDATE sites AS s
        SET session_sync_status = 'expired', session_sync_error = ?, updated_at = ?
        WHERE s.id = ?
    """
    request_id = str(request_id or "").strip()
    if not request_id:
        db_execute(sql, params)
        return True

    origin = site_origin(expected_origin)
    if not origin:
        return False
    sql += """
          AND s.platform = 'sub2api'
          AND s.auth_mode = 'browser'
          AND EXISTS (
              SELECT 1
              FROM browser_session_sync_requests AS r
              WHERE r.id = ?
                AND r.site_id = s.id
                AND r.admin_site_id IS NULL
                AND r.platform = 'sub2api'
                AND r.target_origin = ?
                AND r.status = 'validating'
          )
    """
    params.extend((request_id, origin))
    return db_execute_rowcount(sql, params) > 0


def apply_sub2api_browser_session(
    site_id: int,
    base_url: str,
    session: Dict[str, Any],
    request_id: str = "",
    expected_origin: str = "",
) -> Tuple[bool, Optional[str]]:
    def apply() -> Tuple[bool, Optional[str]]:
        request_id_value = str(request_id or "").strip()
        expected_origin_value = str(expected_origin or "").strip()
        if request_id_value:
            current_error = _site_session_sync_request_error(
                site_id,
                request_id_value,
                expected_origin_value,
                "sub2api",
            )
            if current_error:
                return False, current_error
        access_token = str(session.get("access_token") or "").strip()
        refresh_token = str(session.get("refresh_token") or "").strip()
        ok, _validated, error = validate_sub2api_browser_session(
            base_url, access_token
        )
        if not ok:
            message = error or "登录态已过期，请重新登录"
            if request_id_value:
                # A replacement request may have been created while upstream
                # validation was in flight.  Do not turn its pending state into
                # an expired state because an older request failed.
                with SESSION_SYNC_REQUEST_LOCK:
                    current_error = _site_session_sync_request_error(
                        site_id,
                        request_id_value,
                        expected_origin_value,
                        "sub2api",
                    )
                    if current_error:
                        return False, current_error
                    marked = mark_site_browser_session_expired(
                        site_id,
                        message,
                        request_id=request_id_value,
                        expected_origin=expected_origin_value,
                    )
                if not marked:
                    return False, "同步请求已失效，请重新发起同步"
            else:
                mark_site_browser_session_expired(site_id, message)
            return False, message
        if request_id_value:
            # Creation/replacement uses the same lock.  The SQL condition in
            # persist_site_browser_session remains the cross-process guard.
            with SESSION_SYNC_REQUEST_LOCK:
                current_error = _site_session_sync_request_error(
                    site_id,
                    request_id_value,
                    expected_origin_value,
                    "sub2api",
                )
                if current_error:
                    return False, current_error
                persisted = persist_site_browser_session(
                    site_id,
                    access_token,
                    refresh_token,
                    str(session.get("token_expires_at") or ""),
                    request_id=request_id_value,
                    expected_origin=expected_origin_value,
                )
            if not persisted:
                return False, "同步请求已失效，请重新发起同步"
        else:
            persist_site_browser_session(
                site_id,
                access_token,
                refresh_token,
                str(session.get("token_expires_at") or ""),
            )
        return True, None

    if int(site_id or 0) > 0:
        with _sub2api_site_auth_lock(int(site_id)):
            return apply()
    return apply()


def fetch_sub2api_account(
    base_url: str,
    username: str = "",
    password: str = "",
    auth_mode: str = "password",
    access_token: str = "",
    refresh_token: str = "",
    site_id: int = 0,
) -> Tuple[bool, Any, Optional[str]]:
    return _fetch_sub2api_with_auth_fallback(
        fetch_sub2api_account_by_token,
        base_url,
        username=username,
        password=password,
        auth_mode=auth_mode,
        access_token=access_token,
        refresh_token=refresh_token,
        site_id=site_id,
    )


def normalize_sub2api_account(data: Dict[str, Any]) -> Dict[str, Any]:
    def to_float(value: Any) -> Optional[float]:
        try:
            return round(float(value), 4)
        except (TypeError, ValueError):
            return None

    subscriptions: List[Dict[str, Any]] = []
    for item in data.get("subscriptions") or []:
        if not isinstance(item, dict):
            continue
        group = item.get("group") if isinstance(item.get("group"), dict) else {}
        subscriptions.append({
            "name": str(group.get("name") or item.get("subscription_type") or f"分组 {item.get('group_id')}").strip(),
            "status": str(item.get("status") or ""),
            "expires_at": item.get("expires_at"),
            "daily_usage_usd": to_float(item.get("daily_usage_usd")),
            "weekly_usage_usd": to_float(item.get("weekly_usage_usd")),
            "monthly_usage_usd": to_float(item.get("monthly_usage_usd")),
            "daily_limit_usd": to_float(group.get("daily_limit_usd")),
            "weekly_limit_usd": to_float(group.get("weekly_limit_usd")),
            "monthly_limit_usd": to_float(group.get("monthly_limit_usd")),
        })

    return {
        "platform": "sub2api",
        "username": str(data.get("username") or data.get("email") or ""),
        "email": str(data.get("email") or ""),
        "role": str(data.get("role") or ""),
        "status": str(data.get("status") or ""),
        "balance_usd": to_float(data.get("balance")),
        "frozen_balance_usd": to_float(data.get("frozen_balance")),
        "total_recharged_usd": to_float(data.get("total_recharged")),
        "rpm_limit": data.get("rpm_limit"),
        "subscriptions": subscriptions,
    }


def build_site_account_payload(site: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """统一账户额度出口：按平台分发到 NewAPI /api/user/self 或 sub2api /api/v1/auth/me。"""
    platform = site.get("platform") or "newapi"
    if platform == "newapi":
        if not (site.get("login_enabled") and site.get("access_token") and site.get("access_user_id")):
            return 409, {"success": False, "message": "该 NewAPI 站点未配置系统访问令牌，无法读取账户额度"}
        ok, data, error_message = fetch_newapi_account_for_site(site)
        if not ok:
            return 502, {"success": False, "message": error_message or "读取 NewAPI 账户失败"}
        return 200, {
            "success": True,
            "source": "/api/user/self",
            "fetched_at": utc_now_iso(),
            "account": normalize_newapi_account(data),
        }

    if not (site.get("login_enabled") or site.get("access_token") or site.get("login_username")):
        return 409, {"success": False, "message": "该 sub2api 站点未配置登录信息，无法读取账户额度"}
    ok, data, error_message = fetch_sub2api_account(
        site["base_url"],
        username=site.get("login_username") or "",
        password=site.get("login_password") or "",
        auth_mode=site.get("auth_mode") or "password",
        access_token=site.get("access_token") or "",
        refresh_token=site.get("refresh_token") or "",
        site_id=int(site["id"]),
    )
    data = _strip_sub2api_auth_context(data)
    if not ok:
        return 502, {"success": False, "message": error_message or "读取 sub2api 账户失败"}
    return 200, {
        "success": True,
        "source": "/api/v1/auth/me",
        "fetched_at": utc_now_iso(),
        "account": normalize_sub2api_account(data),
    }


def newapi_auth_failure_message(payload: Any, error: Optional[str] = None) -> str:
    """Return an actionable auth failure without exposing credentials."""
    raw_message = _upstream_response_message(payload, error)
    text = f"{raw_message} {error or ''}".lower()
    if "invalid access token" in text or "access token invalid" in text:
        return "令牌无效或已失效，请重新生成并录入普通用户系统访问令牌"
    if "invalid username" in text or "invalid password" in text or "password incorrect" in text:
        return "用户名或密码错误"
    if "require_2fa" in text or "2fa" in text or "two-factor" in text:
        return "需要 2FA 验证码"
    if "connection reset by peer" in text:
        return "上游重置了 Python 连接，已尝试兼容传输；如仍失败请改用浏览器登录态"
    return raw_message or str(error or "上游认证失败")


def fetch_newapi_groups_with_access_token(base_url: str, access_token: str, user_id: str = "") -> Tuple[bool, Dict[str, Any], Optional[str]]:
    token = (access_token or "").strip()
    if not token:
        return False, {}, "访问令牌为空"

    headers = {
        "Accept": "application/json",
        "User-Agent": "Upstream-Ratio-Watch/1.0",
        "Authorization": token.removeprefix("Bearer ").removeprefix("bearer ").strip(),
    }
    if str(user_id or "").strip():
        headers["New-Api-User"] = str(user_id).strip()
    errors: List[str] = []
    for path in ("/api/user/self/groups", "/api/user/groups"):
        ok, payload, error = request_json(
            f"{normalize_base_url(base_url)}{path}", headers=headers
        )
        if ok and isinstance(payload, dict) and payload.get("success"):
            return True, payload, None
        errors.append(f"{path}: {newapi_auth_failure_message(payload, error)}")

    return False, {"errors": errors}, "访问令牌分组采集失败：" + "；".join(errors)


def probe_newapi_groups(base_url: str) -> Dict[str, Any]:
    ok, payload, error_message = fetch_newapi_groups(base_url)
    if not ok:
        return {
            "success": False,
            "message": error_message or "request failed",
            "groups_count": 0,
            "groups": {},
            "raw": payload,
        }

    groups = parse_groups_payload(payload)
    return {
        "success": True,
        "message": "ok",
        "groups_count": len(groups),
        "groups": groups,
    }


def probe_sub2api_groups(
    base_url: str,
    username: str = "",
    password: str = "",
    auth_mode: str = "password",
    access_token: str = "",
    refresh_token: str = "",
) -> Dict[str, Any]:
    ok, payload, error_message = fetch_sub2api_user_groups(
        base_url,
        username=username,
        password=password,
        auth_mode=auth_mode,
        access_token=access_token,
        refresh_token=refresh_token,
    )
    if not ok:
        safe_payload = _strip_sub2api_auth_context(payload)
        return {
            "success": False,
            "message": error_message or "request failed",
            "groups_count": 0,
            "groups": {},
            "raw": safe_payload,
        }
    groups = parse_sub2api_groups(payload.get("data"), payload.get("user_rates"))
    return {
        "success": True,
        "message": "ok",
        "groups_count": len(groups),
        "groups": groups,
    }


def get_last_success_snapshot(site_id: int) -> Optional[Dict[str, Any]]:
    return db_query_one(
        """
        SELECT * FROM snapshots
        WHERE site_id = ? AND status = 'success'
        ORDER BY id DESC
        LIMIT 1
        """,
        (site_id,),
    )


def diff_groups(old_groups: Dict[str, Dict[str, Any]], new_groups: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    changes: List[Dict[str, Any]] = []
    old_names = set(old_groups.keys())
    new_names = set(new_groups.keys())

    for name in sorted(new_names - old_names):
        new_item = new_groups[name]
        message = f"新增分组 {name}"
        if new_item.get("ratio") is not None:
            message += f" · 倍率 {format_change_value(new_item)}"
        changes.append({
            "change_type": "group_added",
            "group_name": name,
            "old_value": None,
            "new_value": new_item,
            "change_percent": None,
            "message": message,
        })

    for name in sorted(old_names - new_names):
        changes.append({
            "change_type": "group_removed",
            "group_name": name,
            "old_value": old_groups[name],
            "new_value": None,
            "change_percent": None,
            "message": f"删除分组 {name}",
        })

    for name in sorted(old_names & new_names):
        old_item = old_groups[name]
        new_item = new_groups[name]
        if old_item.get("ratio") != new_item.get("ratio"):
            old_ratio = old_item.get("ratio")
            new_ratio = new_item.get("ratio")
            change_percent = None
            if isinstance(old_ratio, (int, float)) and isinstance(new_ratio, (int, float)) and old_ratio != 0:
                change_percent = round((float(new_ratio) - float(old_ratio)) / float(old_ratio) * 100, 2)

            if isinstance(old_ratio, (int, float)) and isinstance(new_ratio, (int, float)):
                message = f"{name} 倍率 {old_ratio} -> {new_ratio}"
            else:
                message = f"{name} 倍率 {old_ratio} -> {new_ratio}"

            changes.append({
                "change_type": "ratio_changed",
                "group_name": name,
                "old_value": old_item,
                "new_value": new_item,
                "change_percent": change_percent,
                "message": message,
            })

        if old_item.get("desc") != new_item.get("desc"):
            changes.append({
                "change_type": "desc_changed",
                "group_name": name,
                "old_value": old_item.get("desc"),
                "new_value": new_item.get("desc"),
                "change_percent": None,
                "message": f"{name} 描述变化",
            })
        for field, label in (
            ("status", "状态"),
            ("is_exclusive", "专属分组"),
            ("subscription_type", "订阅类型"),
            ("rpm_limit", "RPM 限制"),
            ("platform", "平台"),
        ):
            if field in old_item or field in new_item:
                if old_item.get(field) != new_item.get(field):
                    changes.append({
                        "change_type": f"{field}_changed",
                        "group_name": name,
                        "old_value": old_item.get(field),
                        "new_value": new_item.get(field),
                        "change_percent": None,
                        "message": f"{name} {label}变化：{old_item.get(field)} -> {new_item.get(field)}",
                    })

        # 模型上/下架：仅当新旧快照都带有 models 名单时才比较，避免误报整组增删
        old_models = old_item.get("models")
        new_models = new_item.get("models")
        if isinstance(old_models, list) and isinstance(new_models, list):
            old_model_set = {str(m).strip() for m in old_models if str(m).strip()}
            new_model_set = {str(m).strip() for m in new_models if str(m).strip()}
            for model_name in sorted(new_model_set - old_model_set):
                changes.append({
                    "change_type": "model_added_to_group",
                    "group_name": name,
                    "old_value": None,
                    "new_value": model_name,
                    "change_percent": None,
                    "message": f"{name} 上架模型 {model_name}",
                })
            for model_name in sorted(old_model_set - new_model_set):
                changes.append({
                    "change_type": "model_removed_from_group",
                    "group_name": name,
                    "old_value": model_name,
                    "new_value": None,
                    "change_percent": None,
                    "message": f"{name} 下架模型 {model_name}",
                })

    return changes


def get_notification_settings() -> Dict[str, Any]:
    row = db_query_one("SELECT * FROM notification_settings WHERE id = 1")
    if row:
        return row
    now = utc_now_iso()
    db_execute(
        """
        INSERT IGNORE INTO notification_settings
        (id, qq_enabled, qq_app_id, qq_client_secret, qq_group_openid, email_enabled, smtp_host, smtp_port, smtp_username, smtp_password, smtp_use_ssl, smtp_from, smtp_to, created_at, updated_at)
        VALUES (1, 0, '', '', '', 0, '', 465, '', '', 1, '', '', ?, ?)
        """,
        (now, now),
    )
    return db_query_one("SELECT * FROM notification_settings WHERE id = 1") or {}


def notification_settings_payload() -> Dict[str, Any]:
    settings = get_notification_settings()
    return {
        "wecom_enabled": bool(settings.get("wecom_enabled")),
        "wecom_webhook": settings.get("wecom_webhook") or "",
        "wecom_has_webhook": bool(settings.get("wecom_webhook")),
        "wecom_last_error": settings.get("wecom_last_error"),
        "wecom_last_sent_at": settings.get("wecom_last_sent_at"),
        "email_enabled": bool(settings.get("email_enabled")),
        "smtp_host": settings.get("smtp_host") or "",
        "smtp_port": int(settings.get("smtp_port") or 465),
        "smtp_username": settings.get("smtp_username") or "",
        "has_smtp_password": bool(settings.get("smtp_password")),
        "smtp_use_ssl": bool(settings.get("smtp_use_ssl")),
        "smtp_from": settings.get("smtp_from") or "",
        "smtp_to": settings.get("smtp_to") or "",
        "email_last_error": settings.get("email_last_error"),
        "email_last_sent_at": settings.get("email_last_sent_at"),
        "updated_at": settings.get("updated_at"),
    }


def update_notification_settings(body: Dict[str, Any]) -> None:
    settings = get_notification_settings()
    wecom_enabled = bool(body.get("wecom_enabled", False))
    wecom_webhook = str(body.get("wecom_webhook") or "").strip()
    email_enabled = bool(body.get("email_enabled", False))
    smtp_host = str(body.get("smtp_host") or "").strip()
    smtp_port = int(body.get("smtp_port") or 465)
    smtp_username = str(body.get("smtp_username") or "").strip()
    smtp_password = str(body.get("smtp_password") or "")
    smtp_use_ssl = bool(body.get("smtp_use_ssl", True))
    smtp_from = str(body.get("smtp_from") or "").strip()
    smtp_to = str(body.get("smtp_to") or "").strip()

    if email_enabled:
        if not smtp_host or not smtp_port or not smtp_username or not (smtp_password or settings.get("smtp_password")) or not smtp_to:
            raise ValueError("启用邮箱推送时需要填写 SMTP 服务器、端口、账号、密码和收件人")
        if not smtp_from:
            smtp_from = smtp_username
    if wecom_enabled and not (wecom_webhook or settings.get("wecom_webhook")):
        raise ValueError("启用企业微信推送时需要填写 Webhook 地址")

    fields = [
        "qq_enabled = 0",
        "wecom_enabled = ?",
        "email_enabled = ?",
        "wecom_webhook = ?",
        "smtp_host = ?",
        "smtp_port = ?",
        "smtp_username = ?",
        "smtp_use_ssl = ?",
        "smtp_from = ?",
        "smtp_to = ?",
        "updated_at = ?",
    ]
    params: List[Any] = [
        1 if wecom_enabled else 0,
        1 if email_enabled else 0,
        wecom_webhook if wecom_webhook else (settings.get("wecom_webhook") or ""),
        smtp_host,
        smtp_port,
        smtp_username,
        1 if smtp_use_ssl else 0,
        smtp_from,
        smtp_to,
        utc_now_iso(),
    ]
    if smtp_password:
        fields.append("smtp_password = ?")
        params.append(smtp_password)
    params.append(1)
    db_execute(f"UPDATE notification_settings SET {', '.join(fields)} WHERE id = ?", params)


def log_notification(channel: str, status: str, target: str, message: str, error_message: Optional[str] = None) -> None:
    db_execute(
        """
        INSERT INTO notification_logs (channel, status, target, message, error_message, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (channel, status, target, message, error_message, utc_now_iso()),
    )


def send_email_message(subject: str, message: str) -> Tuple[bool, Optional[str]]:
    settings = get_notification_settings()
    if not settings.get("email_enabled"):
        return True, "邮箱推送未启用，未发送测试邮件"

    smtp_host = str(settings.get("smtp_host") or "").strip()
    smtp_port = int(settings.get("smtp_port") or 465)
    smtp_username = str(settings.get("smtp_username") or "").strip()
    smtp_password = str(settings.get("smtp_password") or "")
    smtp_from = str(settings.get("smtp_from") or smtp_username).strip()
    smtp_to = str(settings.get("smtp_to") or "").strip()
    smtp_use_ssl = bool(settings.get("smtp_use_ssl"))
    if not smtp_host or not smtp_port or not smtp_username or not smtp_password or not smtp_to:
        return False, "邮箱 SMTP 配置不完整"

    recipients = [item.strip() for item in smtp_to.replace("，", ",").split(",") if item.strip()]
    email = EmailMessage()
    email["Subject"] = subject
    email["From"] = smtp_from
    email["To"] = ", ".join(recipients)
    email.set_content(message)

    try:
        if smtp_use_ssl:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=HTTP_TIMEOUT_SECONDS) as smtp:
                smtp.login(smtp_username, smtp_password)
                smtp.send_message(email)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=HTTP_TIMEOUT_SECONDS) as smtp:
                smtp.starttls()
                smtp.login(smtp_username, smtp_password)
                smtp.send_message(email)
    except Exception as exc:
        error = f"邮箱推送失败：{exc}"
        db_execute(
            "UPDATE notification_settings SET email_last_error = ?, updated_at = ? WHERE id = 1",
            (error, utc_now_iso()),
        )
        log_notification("email", "failed", smtp_to, message, error)
        return False, error

    sent_at = utc_now_iso()
    db_execute(
        """
        UPDATE notification_settings
        SET email_last_error = NULL, email_last_sent_at = ?, updated_at = ?
        WHERE id = 1
        """,
        (sent_at, sent_at),
    )
    log_notification("email", "success", smtp_to, message, None)
    return True, None


def send_wecom_message(subject: str, message: str) -> Tuple[bool, Optional[str]]:
    settings = get_notification_settings()
    if not settings.get("wecom_enabled"):
        return True, "企业微信推送未启用，未发送消息"

    webhook = str(settings.get("wecom_webhook") or "").strip()
    if not webhook:
        return False, "企业微信 Webhook 未配置"

    content = f"**{subject}**\n\n{message}"
    ok, payload, error = request_json(
        webhook,
        payload={
            "msgtype": "markdown",
            "markdown": {
                "content": content,
            },
        },
        method="POST",
    )
    if not ok:
        error_text = error or "企业微信推送失败"
        db_execute(
            "UPDATE notification_settings SET wecom_last_error = ?, updated_at = ? WHERE id = 1",
            (error_text, utc_now_iso()),
        )
        log_notification("wecom", "failed", webhook, message, error_text)
        return False, error_text

    if isinstance(payload, dict) and payload.get("errcode") not in (None, 0):
        error_text = f"企业微信推送失败：{payload.get('errmsg') or payload.get('errcode')}"
        db_execute(
            "UPDATE notification_settings SET wecom_last_error = ?, updated_at = ? WHERE id = 1",
            (error_text, utc_now_iso()),
        )
        log_notification("wecom", "failed", webhook, message, error_text)
        return False, error_text

    sent_at = utc_now_iso()
    db_execute(
        """
        UPDATE notification_settings
        SET wecom_last_error = NULL, wecom_last_sent_at = ?, updated_at = ?
        WHERE id = 1
        """,
        (sent_at, sent_at),
    )
    log_notification("wecom", "success", webhook, message, None)
    return True, None


def format_change_value(raw: Any) -> str:
    if raw is None:
        return "-"
    if isinstance(raw, dict) and "ratio" in raw:
        ratio = raw.get("ratio")
        try:
            return f"{float(ratio):.2f}x"
        except Exception:
            return str(ratio)
    return str(raw)


def ratio_number(raw: Any) -> Optional[float]:
    if isinstance(raw, dict):
        raw = raw.get("ratio")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def ratio_direction(change: Dict[str, Any]) -> str:
    old_ratio = ratio_number(change.get("old_value"))
    new_ratio = ratio_number(change.get("new_value"))
    if old_ratio is None or new_ratio is None:
        return "changed"
    if new_ratio > old_ratio:
        return "up"
    if new_ratio < old_ratio:
        return "down"
    return "changed"


def percent_text(change: Dict[str, Any]) -> str:
    percent = change.get("change_percent")
    if isinstance(percent, (int, float)):
        return f"{abs(percent):.2f}".rstrip("0").rstrip(".") + "%"
    return ""


def fmt_local_time_for_message(value: str) -> str:
    dt = parse_iso_dt(value)
    if not dt:
        return value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local_dt = dt.astimezone(APP_TIMEZONE)
    tz_name = local_dt.tzname() or ""
    suffix = f" {tz_name}" if tz_name else ""
    return local_dt.strftime("%Y-%m-%d %H:%M:%S") + suffix


def platform_label(site: Dict[str, Any]) -> str:
    return "sub2api" if (site.get("platform") or "newapi") == "sub2api" else "NewAPI"


def format_change_subject(site: Dict[str, Any], changes: List[Dict[str, Any]]) -> str:
    site_name = site["name"]
    platform = platform_label(site)
    ratio_changes = [item for item in changes if item.get("change_type") == "ratio_changed"]
    if len(ratio_changes) == 1:
        change = ratio_changes[0]
        label = "倍率上涨" if ratio_direction(change) == "up" else "倍率下降" if ratio_direction(change) == "down" else "倍率变动"
        return f"【{platform} {label}】{site_name} / {change.get('group_name') or '-'}：{format_change_value(change.get('old_value'))} -> {format_change_value(change.get('new_value'))}"
    if len(ratio_changes) > 1:
        return f"【{platform} 倍率变动】{site_name}：{len(ratio_changes)} 个分组有变化"

    added = [item for item in changes if item.get("change_type") == "group_added"]
    removed = [item for item in changes if item.get("change_type") == "group_removed"]
    if len(added) == 1 and not removed:
        change = added[0]
        return f"【{platform} 新增分组】{site_name} / {change.get('group_name') or '-'}：{format_change_value(change.get('new_value'))}"
    if len(removed) == 1 and not added:
        change = removed[0]
        return f"【{platform} 删除分组】{site_name} / {change.get('group_name') or '-'}"
    return f"【{platform} 分组变化】{site_name}：{len(changes)} 条变化"


def format_change_notification(site: Dict[str, Any], changes: List[Dict[str, Any]], checked_at: str) -> str:
    up_changes = [item for item in changes if item.get("change_type") == "ratio_changed" and ratio_direction(item) == "up"]
    down_changes = [item for item in changes if item.get("change_type") == "ratio_changed" and ratio_direction(item) == "down"]
    changed_ratio = [item for item in changes if item.get("change_type") == "ratio_changed" and ratio_direction(item) == "changed"]
    added = [item for item in changes if item.get("change_type") == "group_added"]
    removed = [item for item in changes if item.get("change_type") == "group_removed"]
    desc_changed = [item for item in changes if item.get("change_type") == "desc_changed"]
    other_changed = [
        item for item in changes
        if item.get("change_type") not in {"ratio_changed", "group_added", "group_removed", "desc_changed"}
    ]

    lines = [
        "上游倍率监控提醒",
        f"站点：{site['name']}",
        f"平台：{platform_label(site)}",
        f"时间：{fmt_local_time_for_message(checked_at)}",
        f"本次共 {len(changes)} 条变化",
    ]

    def append_ratio_block(title: str, items: List[Dict[str, Any]], suffix: str) -> None:
        if not items:
            return
        lines.extend(["", title])
        for change in items[:6]:
            percent = percent_text(change)
            extra = f"，{suffix} {percent}" if percent else f"，{suffix}"
            lines.append(
                f"- {change.get('group_name') or '-'}：{format_change_value(change.get('old_value'))} -> {format_change_value(change.get('new_value'))}{extra}"
            )

    append_ratio_block("涨价了，钱包先别眨眼：", up_changes, "上涨")
    append_ratio_block("降价了，这波可以多看两眼：", down_changes, "下降")

    if changed_ratio:
        lines.extend(["", "倍率变了，但方向不太好判断："])
        for change in changed_ratio[:6]:
            lines.append(f"- {change.get('group_name') or '-'}：{format_change_value(change.get('old_value'))} -> {format_change_value(change.get('new_value'))}")

    if added:
        lines.extend(["", "新分组上线："])
        for change in added[:6]:
            lines.append(f"- {change.get('group_name') or '-'}：{format_change_value(change.get('new_value'))}")

    if removed:
        lines.extend(["", "分组下线了："])
        for change in removed[:6]:
            lines.append(f"- {change.get('group_name') or '-'}：原倍率 {format_change_value(change.get('old_value'))}")

    if desc_changed:
        lines.extend(["", "描述有变化："])
        for change in desc_changed[:6]:
            lines.append(f"- {change.get('group_name') or '-'}")

    if other_changed:
        lines.extend(["", "其他配置变化："])
        for change in other_changed[:8]:
            lines.append(
                f"- {change.get('group_name') or '-'}：{format_change_value(change.get('old_value'))} -> {format_change_value(change.get('new_value'))}"
            )

    if len(changes) > 8:
        lines.append("")
        lines.append(f"其余 {len(changes) - 8} 条变化请在面板查看")
    return "\n".join(lines)


def notify_changes(site: Dict[str, Any], changes: List[Dict[str, Any]], checked_at: str) -> None:
    if not changes:
        return
    subject = format_change_subject(site, changes)
    message = format_change_notification(site, changes, checked_at)
    send_email_message(subject, message)
    send_wecom_message(subject, message)


def collect_site_groups(site: Dict[str, Any]) -> Tuple[bool, Dict[str, Dict[str, Any]], Dict[str, Any], str, Optional[str]]:
    platform = site.get("platform") or "newapi"
    if platform == "sub2api":
        ok, payload, error_message = fetch_sub2api_user_groups(
            site["base_url"],
            username=site.get("login_username") or "",
            password=site.get("login_password") or "",
            auth_mode=site.get("auth_mode") or "password",
            access_token=site.get("access_token") or "",
            refresh_token=site.get("refresh_token") or "",
            site_id=int(site.get("id") or 0),
        )
        if (
            not ok
            and str(site.get("auth_mode") or "").strip().lower()
            == BROWSER_AUTH_MODE
            and (
                bool(
                    isinstance(payload, dict)
                    and payload.get("browser_sync_required")
                )
                or is_sub2api_auth_error(payload, error_message)
            )
        ):
            mark_site_browser_session_expired(
                int(site.get("id") or 0),
                error_message or "登录态已过期，请重新登录",
            )
        groups = parse_sub2api_groups(payload.get("data"), payload.get("user_rates")) if ok else {}
        return ok, groups, payload, "/api/v1/groups/available", error_message

    ok, payload, error_message = fetch_newapi_groups(site["base_url"])
    groups = parse_groups_payload(payload) if ok else {}
    return ok, groups, payload, "/api/user/groups", error_message


def attach_group_model_names(site_id: int, groups: Dict[str, Dict[str, Any]]) -> None:
    """把「每个分组当前有哪些模型」写进 groups，用于模型上/下架 diff。

    只使用已有的模型缓存（用户查看模型详情或启动预热时填充），不在检测热路径里
    新增网络请求，避免 sub2api 密码模式频繁登录被上游限流。缓存缺失的分组不写
    models 字段，从而在 diff 时被安全跳过。
    """
    if not groups:
        return
    try:
        cached, _age = get_site_model_cache(site_id)
    except Exception:
        return
    if not isinstance(cached, dict):
        return
    models_by_group = cached.get("models_by_group")
    if not isinstance(models_by_group, dict):
        return
    for name, info in groups.items():
        if not isinstance(info, dict):
            continue
        entries = models_by_group.get(name)
        if not isinstance(entries, list):
            continue
        info["models"] = sorted({
            str(model.get("name")).strip()
            for model in entries
            if isinstance(model, dict) and str(model.get("name") or "").strip()
        })


def detect_site(site_id: int) -> Dict[str, Any]:
    site = db_query_one("SELECT * FROM sites WHERE id = ?", (site_id,))
    if not site:
        return {"success": False, "message": "site not found"}

    checked_at = utc_now_iso()
    ok, new_groups, payload, source, error_message = collect_site_groups(site)
    payload = _strip_sub2api_auth_context(payload)
    latest_success = get_last_success_snapshot(site_id)

    if not ok:
        db_execute(
            """
            INSERT INTO snapshots (site_id, status, source, raw_json, error_message, checked_at, hash)
            VALUES (?, 'failed', ?, ?, ?, ?, NULL)
            """,
            (site_id, source, json.dumps(payload, ensure_ascii=False), error_message, checked_at),
        )

        consecutive_failures = int(site["consecutive_failures"] or 0) + 1
        status = "failed" if consecutive_failures >= 3 else "warning"
        next_check_at = next_check_iso(int(site["interval_minutes"] or DEFAULT_INTERVAL_MINUTES))
        db_execute(
            """
            UPDATE sites
            SET status = ?, last_error = ?, last_check_at = ?, next_check_at = ?, consecutive_failures = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, error_message, checked_at, next_check_at, consecutive_failures, checked_at, site_id),
        )
        result: Dict[str, Any] = {
            "success": False,
            "message": error_message,
            "status": status,
        }
        if isinstance(payload, dict):
            if payload.get("code"):
                result["code"] = str(payload["code"])
            if payload.get("browser_sync_required"):
                result["browser_sync_required"] = True
        return result

    attach_group_model_names(site_id, new_groups)
    groups_json = json.dumps(new_groups, ensure_ascii=False, sort_keys=True)
    hash_value = stable_hash(new_groups)
    login_groups: Dict[str, Dict[str, Any]] = {}
    login_groups_json: Optional[str] = None
    login_error: Optional[str] = None
    db_execute(
        """
        INSERT INTO snapshots (site_id, status, source, groups_json, raw_json, hash, error_message, checked_at)
        VALUES (?, 'success', ?, ?, ?, ?, NULL, ?)
        """,
        (site_id, source, groups_json, json.dumps(payload, ensure_ascii=False), hash_value, checked_at),
    )

    changes: List[Dict[str, Any]] = []
    if latest_success and latest_success.get("groups_json"):
        try:
            old_groups = json.loads(latest_success["groups_json"])
            if isinstance(old_groups, dict):
                changes = diff_groups(old_groups, new_groups)
        except Exception:
            changes = []

    if (site.get("platform") or "newapi") == "newapi" and site.get("login_enabled") and site.get("access_token") and site.get("access_user_id"):
        login_ok, login_payload, login_error_message = fetch_newapi_groups_for_site(
            site
        )
        if login_ok:
            login_groups = parse_groups_payload(login_payload)
            login_groups_json = json.dumps(login_groups, ensure_ascii=False, sort_keys=True)
            old_login_groups = {}
            if site.get("current_login_groups_json"):
                try:
                    parsed_old_login = json.loads(site["current_login_groups_json"])
                    if isinstance(parsed_old_login, dict):
                        old_login_groups = parsed_old_login
                except Exception:
                    old_login_groups = {}
            login_changes = diff_groups(old_login_groups, login_groups) if old_login_groups else []
            for change in login_changes:
                change["message"] = f"认证增强 {change['message']}"
            changes.extend(login_changes)
        else:
            login_error = login_error_message or "认证增强采集失败"

    for change in changes:
        severity = "info"
        if change["change_type"] in {"group_removed"}:
            severity = "critical"
        elif change["change_type"] == "model_removed_from_group":
            severity = "warning"
        elif change["change_type"] == "ratio_changed":
            percent = change.get("change_percent")
            if isinstance(percent, (int, float)) and percent > 0:
                severity = "warning"

        db_execute(
            """
            INSERT INTO changes
            (site_id, change_type, group_name, old_value, new_value, change_percent, message, created_at, acknowledged)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                site_id,
                change["change_type"],
                change.get("group_name"),
                json.dumps(change.get("old_value"), ensure_ascii=False) if change.get("old_value") is not None else None,
                json.dumps(change.get("new_value"), ensure_ascii=False) if change.get("new_value") is not None else None,
                change.get("change_percent"),
                change["message"],
                checked_at,
            ),
        )
        change["severity"] = severity

    notify_changes(site, changes, checked_at)

    next_check_at = next_check_iso(int(site["interval_minutes"] or DEFAULT_INTERVAL_MINUTES))
    effective_status = "warning" if login_error else "ok"
    db_execute(
        """
        UPDATE sites
        SET status = ?,
            last_error = NULL,
            last_check_at = ?,
            next_check_at = ?,
            consecutive_failures = 0,
            current_groups_json = ?,
            current_login_groups_json = COALESCE(?, current_login_groups_json),
            login_last_error = ?,
            login_last_check_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            effective_status,
            checked_at,
            next_check_at,
            groups_json,
            login_groups_json,
            login_error,
            checked_at if site.get("login_enabled") else None,
            checked_at,
            site_id,
        ),
    )
    schedule_model_cache_refresh(site_id)

    return {
        "success": not bool(login_error),
        "message": login_error or "ok",
        "checked_at": checked_at,
        "groups": new_groups,
        "login_groups": login_groups,
        "changes": changes,
    }


def schedule_worker() -> None:
    last_admin_key_sync = 0.0
    while not STOP_EVENT.is_set():
        try:
            now = app_now()
            due_sites = db_query_all(
                """
                SELECT * FROM sites
                WHERE enabled = 1
                  AND (next_check_at IS NULL OR next_check_at <= ?)
                ORDER BY
                  CASE WHEN next_check_at IS NULL THEN 0 ELSE 1 END,
                  next_check_at ASC,
                  id ASC
                """,
                (now.isoformat(timespec="seconds"),),
            )
            for site in due_sites:
                if STOP_EVENT.is_set():
                    break
                try:
                    detect_site(int(site["id"]))
                except Exception:
                    checked_at = utc_now_iso()
                    err = traceback.format_exc(limit=2)
                    consecutive_failures = int(site["consecutive_failures"] or 0) + 1
                    next_check_at = next_check_iso(int(site["interval_minutes"] or DEFAULT_INTERVAL_MINUTES))
                    db_execute(
                        """
                        UPDATE sites
                        SET status = ?,
                            last_error = ?,
                            last_check_at = ?,
                            next_check_at = ?,
                            consecutive_failures = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            "failed" if consecutive_failures >= 3 else "warning",
                            err,
                            checked_at,
                            next_check_at,
                            consecutive_failures,
                            checked_at,
                            site["id"],
                        ),
                    )
            now_monotonic = time.monotonic()
            if now_monotonic - last_admin_key_sync >= ADMIN_KEY_SYNC_INTERVAL_SECONDS:
                last_admin_key_sync = now_monotonic
                try:
                    auto_sync_admin_site_channels_to_sites()
                except Exception:
                    # Site monitoring must continue even when a protected key
                    # refresh needs renewed 2FA or an upstream is unavailable.
                    pass
        except Exception:
            pass
        STOP_EVENT.wait(SCAN_INTERVAL_SECONDS)


# 无需登录即可访问的 API：状态查询与登录/登出本身
PUBLIC_API_PATHS = {"/api/auth/login", "/api/auth/logout", "/api/auth/status"}


def is_public_api_path(path: str) -> bool:
    if path in PUBLIC_API_PATHS:
        return True
    return bool(
        re.fullmatch(
            r"/api/session-sync/requests/[A-Za-z0-9_-]{1,64}/complete",
            str(path or ""),
        )
    )


def console_auth_enabled() -> bool:
    return bool(CONSOLE_PASSWORD)


def create_console_session() -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with CONSOLE_SESSIONS_LOCK:
        CONSOLE_SESSIONS[token] = now + CONSOLE_SESSION_TTL_SECONDS
        # 顺手清理过期会话，避免内存无限增长
        for stale in [t for t, exp in CONSOLE_SESSIONS.items() if exp < now]:
            CONSOLE_SESSIONS.pop(stale, None)
    return token


def console_session_valid(token: str) -> bool:
    token = (token or "").strip()
    if not token:
        return False
    with CONSOLE_SESSIONS_LOCK:
        exp = CONSOLE_SESSIONS.get(token)
        if exp is None:
            return False
        if exp < time.time():
            CONSOLE_SESSIONS.pop(token, None)
            return False
        return True


def drop_console_session(token: str) -> None:
    with CONSOLE_SESSIONS_LOCK:
        CONSOLE_SESSIONS.pop((token or "").strip(), None)


def request_bearer_token(handler: BaseHTTPRequestHandler) -> str:
    raw = (handler.headers.get("Authorization") or "").strip()
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return raw


def console_authenticated(handler: BaseHTTPRequestHandler) -> bool:
    """无密码时视为始终通过；否则要求携带有效会话 token。"""
    if not console_auth_enabled():
        return True
    return console_session_valid(request_bearer_token(handler))


def _slow_request_log_line(
    method: Any,
    target: Any,
    status: Any,
    elapsed_ms: float,
    threshold_ms: int,
) -> Optional[str]:
    if threshold_ms <= 0 or elapsed_ms < threshold_ms:
        return None
    safe_path = urlparse(str(target or "")).path or "/"
    return (
        f"[慢请求] {method or '-'} {safe_path} "
        f"{int(status or 0)} {elapsed_ms:.1f}ms"
    )


def json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def read_json_body(handler: BaseHTTPRequestHandler) -> Any:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    raw = handler.rfile.read(length).decode("utf-8") if length > 0 else "{}"
    return json.loads(raw or "{}")


def read_bounded_json_body(
    handler: BaseHTTPRequestHandler, max_bytes: int = SESSION_SYNC_MAX_BODY_BYTES
) -> Any:
    try:
        length = int(handler.headers.get("Content-Length", "0") or "0")
    except (TypeError, ValueError) as exc:
        raise ValueError("请求体长度无效") from exc
    if length < 0 or length > int(max_bytes):
        raise ValueError("同步请求体过大")
    raw = handler.rfile.read(length) if length > 0 else b"{}"
    if len(raw) > int(max_bytes):
        raise ValueError("同步请求体过大")
    try:
        return json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("同步请求 JSON 无效") from exc


def site_summary(
    site: Dict[str, Any],
    connection: Optional[pymysql.connections.Connection] = None,
) -> Dict[str, Any]:
    groups = {}
    login_groups = {}
    if site.get("current_groups_json"):
        try:
            groups = json.loads(site["current_groups_json"]) or {}
        except Exception:
            groups = {}
    if site.get("current_login_groups_json"):
        try:
            login_groups = json.loads(site["current_login_groups_json"]) or {}
        except Exception:
            login_groups = {}
    latest_snapshot = db_query_one(
        "SELECT checked_at, status, error_message FROM snapshots WHERE site_id = ? ORDER BY id DESC LIMIT 1",
        (site["id"],),
        connection=connection,
    )
    latest_change = db_query_one(
        "SELECT * FROM changes WHERE site_id = ? ORDER BY id DESC LIMIT 1",
        (site["id"],),
        connection=connection,
    )
    return {
        "id": site["id"],
        "name": site["name"],
        "base_url": site["base_url"],
        "platform": site["platform"],
        "platform_label": "sub2api" if site["platform"] == "sub2api" else "NewAPI",
        "enabled": bool(site["enabled"]),
        "interval_minutes": site["interval_minutes"],
        "login_enabled": bool(site.get("login_enabled")),
        "auth_mode": (
            "token"
            if str(site.get("platform") or "newapi").strip().lower() == "newapi"
            else site.get("auth_mode") or "password"
        ),
        "login_username": site.get("login_username") or "",
        "has_login_password": bool(site.get("login_password")),
        "has_access_token": bool(site.get("access_token")),
        "has_refresh_token": bool(site.get("refresh_token")),
        "has_browser_session": bool(
            str(site.get("platform") or "newapi").strip().lower() == "sub2api"
            and site.get("access_token")
            and site.get("browser_session_id")
        ),
        "token_expires_at": site.get("token_expires_at") or "",
        "access_user_id": site.get("access_user_id") or "",
        "login_last_error": site.get("login_last_error"),
        "login_last_check_at": site.get("login_last_check_at"),
        "session_sync_status": (
            site.get("session_sync_status") or "not_requested"
            if str(site.get("platform") or "newapi").strip().lower() == "sub2api"
            else "not_requested"
        ),
        "session_sync_error": (
            site.get("session_sync_error")
            if str(site.get("platform") or "newapi").strip().lower() == "sub2api"
            else None
        ),
        "session_synced_at": (
            site.get("session_synced_at")
            if str(site.get("platform") or "newapi").strip().lower() == "sub2api"
            else None
        ),
        "status": site["status"],
        "last_error": site["last_error"],
        "last_check_at": site["last_check_at"],
        "next_check_at": site["next_check_at"],
        "consecutive_failures": site["consecutive_failures"],
        "current_groups": groups,
        "current_groups_count": len(groups) if isinstance(groups, dict) else 0,
        "current_login_groups": login_groups,
        "current_login_groups_count": len(login_groups) if isinstance(login_groups, dict) else 0,
        "latest_snapshot": latest_snapshot,
        "latest_change": latest_change,
    }


def overview_payload() -> Dict[str, Any]:
    with db_connection() as connection:
        sites = db_query_all(
            "SELECT * FROM sites ORDER BY id DESC", connection=connection
        )
        changes = db_query_all(
            "SELECT * FROM changes ORDER BY id DESC LIMIT 8",
            connection=connection,
        )
        totals = {
            "sites_total": len(sites),
            "sites_enabled": sum(1 for s in sites if s["enabled"]),
            "sites_ok": sum(1 for s in sites if s["status"] == "ok"),
            "sites_failed": sum(
                1 for s in sites if s["status"] in {"failed", "warning"}
            ),
            "changes_today": db_query_one(
                "SELECT COUNT(*) AS count FROM changes WHERE created_at >= ?",
                (
                    app_now()
                    .replace(hour=0, minute=0, second=0, microsecond=0)
                    .isoformat(timespec="seconds"),
                ),
                connection=connection,
            )
            or {"count": 0},
        }
        return {
            "stats": {
                "sites_total": totals["sites_total"],
                "sites_enabled": totals["sites_enabled"],
                "sites_ok": totals["sites_ok"],
                "sites_failed": totals["sites_failed"],
                "changes_today": totals["changes_today"]["count"],
            },
            "sites": [site_summary(site, connection=connection) for site in sites],
            "changes": changes,
        }


def list_sites_payload(
    with_auto_sync: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    # Main-site synchronization is an explicit action.  Ordinary site polling
    # must remain read-only so a 15-second UI refresh cannot repeat upstream
    # pagination or trigger reconciliation.
    auto_sync_results: List[Dict[str, Any]] = (
        auto_sync_admin_site_channels_to_sites() if with_auto_sync else []
    )
    with db_connection() as connection:
        sites = db_query_all(
            "SELECT * FROM sites ORDER BY id DESC", connection=connection
        )
        summaries = [
            site_summary(site, connection=connection) for site in sites
        ]
    return summaries, auto_sync_results


RECONCILE_MODE_DISABLE = "disable"
RECONCILE_MODE_DELETE = "delete"
RECONCILE_MODES = {RECONCILE_MODE_DISABLE, RECONCILE_MODE_DELETE}
SETTING_RECONCILE_MODE = "main_site_reconcile_mode"


def get_app_setting(name: str, default: str = "") -> str:
    try:
        row = db_query_one("SELECT value FROM app_settings WHERE name = ?", (name,))
    except Exception:
        return default
    if not isinstance(row, dict):
        return default
    value = row.get("value")
    return str(value) if value is not None else default


def set_app_setting(name: str, value: str) -> None:
    db_execute(
        "INSERT INTO app_settings (name, value, updated_at) VALUES (?, ?, ?) "
        "ON DUPLICATE KEY UPDATE value = VALUES(value), updated_at = VALUES(updated_at)",
        (name, value, utc_now_iso()),
    )


def get_main_site_reconcile_mode() -> str:
    """User-selected handling for monitoring sites whose upstream channel vanished.

    ``disable`` (default) flips them to enabled=0 and keeps their history;
    ``delete`` physically removes the site (cascading snapshots + changes).
    """
    mode = (
        get_app_setting(SETTING_RECONCILE_MODE, RECONCILE_MODE_DISABLE)
        .strip()
        .lower()
    )
    return mode if mode in RECONCILE_MODES else RECONCILE_MODE_DISABLE


def record_admin_site_sync_error(admin_site_id: int, message: str) -> None:
    """Keep the last failed attempt without replacing the last good snapshot."""
    now = utc_now_iso()
    safe_message = str(message or "主站同步失败")[:4000]
    try:
        db_execute(
            """
            INSERT INTO admin_site_sync_state
            (admin_site_id, channels_json, groups_json, channels_hash,
             groups_hash, last_success_at, last_error, last_attempt_at,
             updated_at)
            VALUES (?, '[]', '{}', ?, ?, NULL, ?, ?, ?)
            ON DUPLICATE KEY UPDATE
              last_error = VALUES(last_error),
              last_attempt_at = VALUES(last_attempt_at),
              updated_at = VALUES(updated_at)
            """,
            (
                int(admin_site_id),
                stable_hash([]),
                stable_hash({}),
                safe_message,
                now,
                now,
            ),
        )
    except Exception:
        # The original upstream error is more useful than a secondary status
        # table failure, and neither failure is allowed to trigger cleanup.
        pass


def _delete_stale_admin_channel_data_in_connection(
    connection: Any,
    admin_site_id: int,
    live_channel_ids: set,
) -> Tuple[int, int]:
    """Remove cached channel data only after a complete channel read."""
    binding_rows = db_query_all(
        "SELECT channel_id FROM channel_upstream_bindings WHERE admin_site_id = ?",
        (int(admin_site_id),),
        connection=connection,
    )
    key_rows = db_query_all(
        "SELECT channel_id FROM admin_channel_keys WHERE admin_site_id = ?",
        (int(admin_site_id),),
        connection=connection,
    )
    removed_bindings = 0
    removed_keys = 0
    with connection.cursor() as cursor:
        for row in binding_rows:
            channel_id = _positive_channel_id(row.get("channel_id"))
            if channel_id is not None and channel_id in live_channel_ids:
                continue
            cursor.execute(
                _q(
                    "DELETE FROM channel_upstream_bindings "
                    "WHERE admin_site_id = ? AND channel_id = ?"
                ),
                (int(admin_site_id), int(row.get("channel_id") or 0)),
            )
            removed_bindings += int(cursor.rowcount or 0)
        for row in key_rows:
            channel_id = _positive_channel_id(row.get("channel_id"))
            if channel_id is not None and channel_id in live_channel_ids:
                continue
            cursor.execute(
                _q(
                    "DELETE FROM admin_channel_keys "
                    "WHERE admin_site_id = ? AND channel_id = ?"
                ),
                (int(admin_site_id), int(row.get("channel_id") or 0)),
            )
            removed_keys += int(cursor.rowcount or 0)
    return removed_bindings, removed_keys


def _apply_admin_site_channel_reconcile_in_connection(
    connection: Any,
    admin_site_id: int,
    affected_site_ids: set,
    mode: str,
) -> Tuple[int, int, int]:
    """Apply local site state only after stale source links are removed."""
    linked_rows = db_query_all(
        "SELECT DISTINCT site_id FROM site_discovery_links "
        "WHERE admin_site_id = ?",
        (int(admin_site_id),),
        connection=connection,
    )
    for row in linked_rows:
        try:
            affected_site_ids.add(int(row.get("site_id")))
        except (TypeError, ValueError):
            continue

    disabled = 0
    reenabled = 0
    deleted = 0
    now = utc_now_iso()
    for raw_site_id in sorted(affected_site_ids):
        try:
            site_id = int(raw_site_id)
        except (TypeError, ValueError):
            continue
        site = db_query_one(
            "SELECT id, enabled, auto_disabled FROM sites WHERE id = ? FOR UPDATE",
            (site_id,),
            connection=connection,
        )
        if not site:
            continue
        remaining = db_query_one(
            "SELECT COUNT(*) AS count FROM site_discovery_links WHERE site_id = ?",
            (site_id,),
            connection=connection,
        )
        remaining_count = int((remaining or {}).get("count") or 0)
        if remaining_count > 0:
            if int(site.get("auto_disabled") or 0) == 1:
                with connection.cursor() as cursor:
                    cursor.execute(
                        _q(
                            "UPDATE sites SET enabled = 1, auto_disabled = 0, "
                            "updated_at = ? WHERE id = ?"
                        ),
                        (now, site_id),
                    )
                    reenabled += int(cursor.rowcount or 0)
            continue
        if mode == RECONCILE_MODE_DELETE:
            with connection.cursor() as cursor:
                cursor.execute(
                    _q("DELETE FROM sites WHERE id = ?"),
                    (site_id,),
                )
                deleted += int(cursor.rowcount or 0)
        elif int(site.get("enabled") or 0) == 1:
            with connection.cursor() as cursor:
                cursor.execute(
                    _q(
                        "UPDATE sites SET enabled = 0, auto_disabled = 1, "
                        "updated_at = ? WHERE id = ?"
                    ),
                    (now, site_id),
                )
                disabled += int(cursor.rowcount or 0)
    return disabled, reenabled, deleted


def _sync_admin_site_snapshot_in_connection(
    connection: Any,
    admin: Dict[str, Any],
    channels: List[Dict[str, Any]],
    groups: Dict[str, Any],
    mode: str,
) -> Dict[str, Any]:
    """Write one complete admin-site snapshot and reconcile its local links."""
    admin_site_id = int(admin.get("id") or 0)
    platform = admin_site_platform(admin)
    if admin_site_id <= 0:
        raise ValueError("管理站点 ID 无效")
    if platform == "newapi":
        for channel in channels:
            base_url, url_error = _normalize_discovery_base_url(
                channel.get("base_url")
            )
            if url_error or not base_url:
                raise ValueError(
                    f"渠道 {channel.get('id')} 缺少有效 Base URL，拒绝执行同步清理"
                )
            channel["base_url"] = base_url

    channels_json = json.dumps(
        channels, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    groups_json = json.dumps(
        groups, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    channels_hash = stable_hash(channels)
    groups_hash = stable_hash(groups)
    now = utc_now_iso()
    previous = db_query_one(
        "SELECT channels_hash, groups_hash FROM admin_site_sync_state "
        "WHERE admin_site_id = ? FOR UPDATE",
        (admin_site_id,),
        connection=connection,
    )

    candidates: List[Dict[str, Any]] = []
    imported = 0
    conflicts: List[Dict[str, Any]] = []
    removed_links = 0
    affected_site_ids: set = set()
    preferred_site_by_channel: Dict[int, int] = {}
    if platform == "newapi":
        candidates = aggregate_newapi_channel_candidates(channels)
        for candidate in candidates:
            result = _import_discovered_site_item(
                connection,
                admin_site_id,
                candidate,
                DEFAULT_INTERVAL_MINUTES,
                allow_existing_platform=True,
            )
            if not isinstance(result, dict):
                raise RuntimeError(
                    "创建或关联监控站点失败"
                )
            if result.get("status") == "conflict":
                # Keep the complete upstream snapshot even if a candidate has
                # an import limitation unrelated to the main-site read.
                conflicts.append(
                    {
                        "base_url": result.get("base_url") or candidate.get("base_url"),
                        "name": result.get("name") or candidate.get("name"),
                        "channel_ids": list(
                            result.get("channel_ids")
                            or candidate.get("channel_ids")
                            or []
                        ),
                        "site_id": result.get("site_id"),
                        "message": result.get("message") or "监控站点平台冲突",
                    }
                )
                continue
            result_site_id = _positive_channel_id(result.get("site_id"))
            if result_site_id is not None:
                for raw_channel_id in candidate.get("channel_ids") or []:
                    channel_id = _positive_channel_id(raw_channel_id)
                    if channel_id is not None:
                        preferred_site_by_channel[channel_id] = result_site_id
            if result.get("status") == "created":
                imported += 1
        removed_links, affected_site_ids = (
            _reconcile_site_discovery_links_in_connection(
                connection,
                admin_site_id,
                candidates,
                preferred_site_by_channel,
            )
        )

    live_channel_ids = {
        int(channel["id"]) for channel in channels if _positive_channel_id(channel.get("id"))
    }
    removed_bindings, removed_keys = (
        _delete_stale_admin_channel_data_in_connection(
            connection, admin_site_id, live_channel_ids
        )
    )
    disabled = reenabled = deleted = 0
    if platform == "newapi":
        disabled, reenabled, deleted = _apply_admin_site_channel_reconcile_in_connection(
            connection, admin_site_id, affected_site_ids, mode
        )

    with connection.cursor() as cursor:
        cursor.execute(
            _q(
                """
                INSERT INTO admin_site_sync_state
                (admin_site_id, channels_json, groups_json, channels_hash,
                 groups_hash, last_success_at, last_error, last_attempt_at,
                 updated_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
                ON DUPLICATE KEY UPDATE
                  channels_json = VALUES(channels_json),
                  groups_json = VALUES(groups_json),
                  channels_hash = VALUES(channels_hash),
                  groups_hash = VALUES(groups_hash),
                  last_success_at = VALUES(last_success_at),
                  last_error = NULL,
                  last_attempt_at = VALUES(last_attempt_at),
                  updated_at = VALUES(updated_at)
                """
            ),
            (
                admin_site_id,
                channels_json,
                groups_json,
                channels_hash,
                groups_hash,
                now,
                now,
                now,
            ),
        )
    return {
        "admin_site_id": admin_site_id,
        "platform": platform,
        "status": "synced",
        "imported": imported,
        "channels_count": len(channels),
        "groups_count": len(groups),
        "channels_changed": not previous
        or previous.get("channels_hash") != channels_hash,
        "groups_changed": not previous
        or previous.get("groups_hash") != groups_hash,
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
        "removed_links": removed_links,
        "removed_bindings": removed_bindings,
        "removed_keys": removed_keys,
        "disabled": disabled,
        "reenabled": reenabled,
        "deleted": deleted,
    }


def refresh_admin_site_channel_keys(
    admin: Dict[str, Any], channels: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Force-refresh NewAPI channel keys during an explicit/scheduled sync.

    The protected key endpoint is deliberately called outside the snapshot
    transaction. A failed refresh leaves the last known key available, while
    successful changes trigger a fresh upstream match below.
    """
    if admin_site_platform(admin) != "newapi":
        return {"refreshed": 0, "changed": 0, "failed": 0, "errors": []}
    refreshed = changed = failed = 0
    errors: List[str] = []
    for channel in channels:
        try:
            channel_id = int(channel.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if channel_id <= 0:
            continue
        previous = get_cached_admin_channel_key(int(admin["id"]), channel_id)
        ok, key, error = fetch_newapi_channel_key(
            admin, channel_id, force_refresh=True
        )
        if not ok:
            failed += 1
            message = error or "读取渠道 key 失败"
            channel["key_sync_error"] = message
            if message not in errors:
                errors.append(message)
            continue
        refreshed += 1
        if previous and previous != key:
            changed += 1
            channel["key_changed"] = True
            try:
                match_channel_upstream_binding(admin, channel_id, force_refresh=True)
            except Exception as exc:
                channel["key_sync_error"] = f"key 已更新，但重新匹配失败：{exc}"
    return {"refreshed": refreshed, "changed": changed, "failed": failed, "errors": errors[:3]}


def _sync_one_admin_site(
    admin: Dict[str, Any],
    mode: str,
) -> Dict[str, Any]:
    admin_site_id = int(admin.get("id") or 0)
    try:
        ok, raw_channels, _channel_meta, channel_error = fetch_admin_site_channels(
            admin, ""
        )
        if not ok:
            raise RuntimeError(channel_error or "读取主站渠道失败")
        ok, raw_groups, group_error = fetch_admin_site_groups(admin)
        if not ok:
            raise RuntimeError(group_error or "读取主站分组失败")
        channels, channels_error = normalize_admin_sync_channels(raw_channels)
        if channels_error:
            raise RuntimeError(channels_error)
        groups, groups_error = normalize_admin_sync_groups(raw_groups)
        if groups_error:
            raise RuntimeError(groups_error)

        key_refresh = refresh_admin_site_channel_keys(admin, channels)
        with db_connection() as connection:
            try:
                result = _sync_admin_site_snapshot_in_connection(
                    connection, admin, channels, groups, mode
                )
                connection.commit()
                result["keys_changed"] = key_refresh["changed"]
                result["keys_refreshed"] = key_refresh["refreshed"]
                result["keys_failed"] = key_refresh["failed"]
                result["key_errors"] = key_refresh["errors"]
                print(
                    "[主站同步] "
                    f"admin_site_id={admin_site_id} "
                    f"channels={result.get('channels_count', 0)} "
                    f"groups={result.get('groups_count', 0)} "
                    f"imported={result.get('imported', 0)} "
                    f"conflicts={result.get('conflict_count', 0)} "
                    f"disabled={result.get('disabled', 0)} "
                    f"deleted={result.get('deleted', 0)}",
                    flush=True,
                )
                return result
            except Exception:
                try:
                    connection.rollback()
                except Exception:
                    pass
                raise
    except Exception as exc:
        message = str(exc) or "主站同步失败"
        record_admin_site_sync_error(admin_site_id, message)
        print(
            f"[主站同步失败] admin_site_id={admin_site_id} message={message}",
            flush=True,
        )
        return {
            "admin_site_id": admin_site_id,
            "platform": admin_site_platform(admin),
            "status": "sync_failed",
            "message": message,
            "imported": 0,
            "disabled": 0,
            "reenabled": 0,
            "deleted": 0,
            "keys_changed": 0,
            "keys_refreshed": 0,
            "keys_failed": 0,
        }


def _run_admin_site_sync(
    admin_site_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    try:
        if admin_site_id is None:
            admin_sites = db_query_all("SELECT * FROM admin_sites ORDER BY id")
        else:
            admin_sites = db_query_all(
                "SELECT * FROM admin_sites WHERE id = ?", (int(admin_site_id),)
            )
    except Exception as exc:
        return [{"status": "sync_failed", "message": str(exc) or "读取主站列表失败"}]

    if admin_site_id is not None and not admin_sites:
        return [
            {
                "admin_site_id": int(admin_site_id),
                "status": "sync_failed",
                "message": "管理站点不存在",
                "imported": 0,
                "disabled": 0,
                "reenabled": 0,
                "deleted": 0,
            }
        ]
    mode = get_main_site_reconcile_mode()
    results: List[Dict[str, Any]] = []
    for admin in admin_sites:
        if not isinstance(admin, dict):
            continue
        results.append(_sync_one_admin_site(admin, mode))

    results.append(
        {
            "status": "reconcile",
            "mode": mode,
            "channels_changed": any(
                bool(item.get("channels_changed"))
                for item in results
                if isinstance(item, dict)
            ),
            "groups_changed": any(
                bool(item.get("groups_changed"))
                for item in results
                if isinstance(item, dict)
            ),
            "disabled": sum(
                int(item.get("disabled") or 0)
                for item in results
                if isinstance(item, dict)
            ),
            "reenabled": sum(
                int(item.get("reenabled") or 0)
                for item in results
                if isinstance(item, dict)
            ),
            "deleted": sum(
                int(item.get("deleted") or 0)
                for item in results
                if isinstance(item, dict)
            ),
        }
    )
    return results


def auto_sync_admin_site_channels_to_sites(
    admin_site_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Synchronize one selected admin site, or all admin sites for API callers."""
    return _run_admin_site_sync(admin_site_id)

def list_snapshots(site_id: int) -> List[Dict[str, Any]]:
    return db_query_all(
        """
        SELECT * FROM snapshots
        WHERE site_id = ?
        ORDER BY id DESC
        LIMIT 100
        """,
        (site_id,),
    )


def list_changes(limit: int = 100) -> List[Dict[str, Any]]:
    return db_query_all(
        "SELECT * FROM changes ORDER BY id DESC LIMIT ?",
        (limit,),
    )


def list_site_changes(site_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    return db_query_all(
        """
        SELECT * FROM changes
        WHERE site_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (site_id, limit),
    )


def site_groups_from_row(site: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    if not site.get("current_groups_json"):
        return {}
    try:
        groups = json.loads(site["current_groups_json"])
        return groups if isinstance(groups, dict) else {}
    except Exception:
        return {}


def persist_sub2api_refreshed_auth(
    site_id: int,
    refreshed_auth: Any,
    *,
    expected_access_token: Optional[str] = None,
    expected_refresh_token: Optional[str] = None,
    restore_browser_session: bool = False,
) -> None:
    if not isinstance(refreshed_auth, dict):
        return
    expires_at = None
    try:
        expires_in = refreshed_auth.get("expires_in")
        expires_at = (app_now() + timedelta(seconds=int(expires_in))).isoformat(timespec="seconds") if expires_in is not None else None
    except (TypeError, ValueError):
        expires_at = None
    access_token = str(refreshed_auth.get("access_token") or "").strip()
    refresh_token = str(refreshed_auth.get("refresh_token") or "").strip()
    assignments = [
        "access_token = COALESCE(NULLIF(?, ''), access_token)",
        "refresh_token = COALESCE(NULLIF(?, ''), refresh_token)",
        "token_expires_at = COALESCE(?, token_expires_at)",
    ]
    params: List[Any] = [access_token, refresh_token, expires_at]
    if restore_browser_session:
        assignments.extend([
            "session_sync_status = 'ready'",
            "session_sync_error = NULL",
        ])
    now = utc_now_iso()
    assignments.append("updated_at = ?")
    params.append(now)
    where = ["id = ?"]
    where_params: List[Any] = [int(site_id)]
    if restore_browser_session:
        where.append("auth_mode = 'browser'")
    if expected_access_token is not None:
        where.append("COALESCE(access_token, '') = ?")
        where_params.append(str(expected_access_token or "").strip())
    if expected_refresh_token is not None:
        where.append("COALESCE(refresh_token, '') = ?")
        where_params.append(str(expected_refresh_token or "").strip())
    params.extend(where_params)
    db_execute_rowcount(
        f"UPDATE sites SET {', '.join(assignments)} WHERE {' AND '.join(where)}",
        tuple(params),
    )


def persist_channel_binding_refreshed_auth(
    admin_site_id: int,
    channel_id: int,
    refreshed_auth: Any,
    *,
    expected_access_token: Optional[str] = None,
    expected_refresh_token: Optional[str] = None,
) -> None:
    """Keep a channel-level sub2api binding usable after access-token refresh."""
    if not isinstance(refreshed_auth, dict):
        return
    access_token = str(refreshed_auth.get("access_token") or "").strip()
    refresh_token = str(refreshed_auth.get("refresh_token") or "").strip()
    if not access_token and not refresh_token:
        return
    assignments = [
        "access_token = COALESCE(NULLIF(?, ''), access_token)",
        "refresh_token = COALESCE(NULLIF(?, ''), refresh_token)",
        "updated_at = ?",
    ]
    params: List[Any] = [access_token, refresh_token, utc_now_iso()]
    where = ["admin_site_id = ?", "channel_id = ?"]
    where_params: List[Any] = [int(admin_site_id), int(channel_id)]
    if expected_access_token is not None:
        where.append("COALESCE(access_token, '') = ?")
        where_params.append(str(expected_access_token or "").strip())
    if expected_refresh_token is not None:
        where.append("COALESCE(refresh_token, '') = ?")
        where_params.append(str(expected_refresh_token or "").strip())
    params.extend(where_params)
    db_execute_rowcount(
        f"UPDATE channel_upstream_bindings SET {', '.join(assignments)} WHERE {' AND '.join(where)}",
        tuple(params),
    )


def build_site_models_payload(site: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    groups = site_groups_from_row(site)
    if not groups:
        return 409, {"success": False, "message": "请先检测站点，获取分组倍率后再查看模型"}

    platform = site.get("platform") or "newapi"
    if platform == "newapi":
        pricing_ok, pricing_payload, pricing_error = fetch_newapi_pricing_for_site(site)
        if not pricing_ok:
            return 502, {"success": False, "message": pricing_error or "读取 NewAPI 模型失败"}
        uptime_payload, uptime_error = get_cached_newapi_uptime_for_site(site)
        payload = {
            "success": True,
            "pricing": pricing_payload,
            "uptime": uptime_payload,
            "uptime_error": uptime_error,
        }
        if not pricing_ok:
            return 502, {"success": False, "message": pricing_error or "读取 NewAPI 模型失败"}
        models_by_group = parse_newapi_models_by_group(payload.get("pricing"), payload.get("uptime"), groups)
        pricing_data = payload.get("pricing", {}).get("data", []) if isinstance(payload.get("pricing"), dict) else []
        uptime_categories = payload.get("uptime", {}).get("data", []) if isinstance(payload.get("uptime"), dict) else []
        uptime_monitors_count = sum(
            len(category.get("monitors") or [])
            for category in uptime_categories
            if isinstance(category, dict)
        )
        return 200, {
            "success": True,
            "source": ["/api/pricing", "/api/uptime/status"],
            "fetched_at": utc_now_iso(),
            "models_by_group": models_by_group,
            "models_count": len(pricing_data),
            "monitors_count": uptime_monitors_count,
            "uptime_error": payload.get("uptime_error"),
        }

    ok, payload, error_message = fetch_sub2api_model_data(
        site["base_url"],
        username=site.get("login_username") or "",
        password=site.get("login_password") or "",
        auth_mode=site.get("auth_mode") or "password",
        access_token=site.get("access_token") or "",
        refresh_token=site.get("refresh_token") or "",
        site_id=int(site["id"]),
    )
    payload = _strip_sub2api_auth_context(payload)
    if not ok:
        return 502, {"success": False, "message": error_message or "读取上游模型失败"}

    configured_models = parse_sub2api_channel_models(payload.get("channels"), groups)
    monitored_models, unmatched_models = parse_sub2api_monitor_models(payload.get("monitors"), groups)
    models_by_group = merge_sub2api_group_models(configured_models, monitored_models)
    monitor_items = payload.get("monitors", {}).get("items", []) if isinstance(payload.get("monitors"), dict) else []
    return 200, {
        "success": True,
        "source": ["/api/v1/channels/available", "/api/v1/channel-monitors"],
        "fetched_at": utc_now_iso(),
        "models_by_group": models_by_group,
        "channels_count": len(payload.get("channels") or []),
        "monitors_count": len(monitor_items),
        "unmatched_models_count": len(unmatched_models),
        "channels_error": payload.get("channels_error"),
        "monitors_error": payload.get("monitors_error"),
    }


def invalidate_site_model_cache(site_id: int) -> None:
    with MODEL_CACHE_LOCK:
        MODEL_DATA_CACHE.pop(site_id, None)


def cache_site_model_payload(site_id: int, payload: Dict[str, Any]) -> None:
    with MODEL_CACHE_LOCK:
        MODEL_DATA_CACHE[site_id] = {
            "payload": json.loads(json.dumps(payload, ensure_ascii=False)),
            "updated_monotonic": time.monotonic(),
        }


def get_site_model_cache(site_id: int) -> Tuple[Optional[Dict[str, Any]], float]:
    with MODEL_CACHE_LOCK:
        entry = MODEL_DATA_CACHE.get(site_id)
        if not entry or not isinstance(entry.get("payload"), dict):
            return None, float("inf")
        age = time.monotonic() - float(entry.get("updated_monotonic") or 0)
        return json.loads(json.dumps(entry["payload"], ensure_ascii=False)), age


def refresh_site_model_cache(site_id: int) -> Tuple[int, Dict[str, Any]]:
    site = db_query_one("SELECT * FROM sites WHERE id = ?", (site_id,))
    if not site:
        return 404, {"success": False, "message": "site not found"}
    status, payload = build_site_models_payload(site)
    if status == 200 and payload.get("success"):
        cache_site_model_payload(site_id, payload)
    return status, payload


def model_cache_refresh_worker(site_id: int) -> None:
    try:
        refresh_site_model_cache(site_id)
    finally:
        with MODEL_CACHE_LOCK:
            MODEL_CACHE_REFRESHING.discard(site_id)


def schedule_model_cache_refresh(site_id: int) -> None:
    with MODEL_CACHE_LOCK:
        if site_id in MODEL_CACHE_REFRESHING:
            return
        MODEL_CACHE_REFRESHING.add(site_id)
    threading.Thread(target=model_cache_refresh_worker, args=(site_id,), daemon=True).start()


def warm_model_cache() -> None:
    for site in db_query_all("SELECT id FROM sites WHERE enabled = 1 ORDER BY id"):
        schedule_model_cache_refresh(int(site["id"]))


class Handler(BaseHTTPRequestHandler):
    server_version = "NewAPIPriceWatch/0.1"

    def handle_one_request(self) -> None:
        started = time.monotonic()
        self._response_status = 0
        self.command = None
        self.path = ""
        try:
            super().handle_one_request()
        except DatabasePoolTimeoutError:
            request_path = urlparse(str(self.path or "")).path
            if not request_path.startswith("/api/"):
                raise
            json_response(
                self,
                {
                    "success": False,
                    "message": "数据库连接池繁忙，请稍后重试",
                    "code": "database_busy",
                },
                503,
            )
        finally:
            elapsed_ms = (time.monotonic() - started) * 1000
            line = _slow_request_log_line(
                self.command,
                self.path,
                self._response_status,
                elapsed_ms,
                SLOW_REQUEST_THRESHOLD_MS,
            )
            if line:
                print(line, flush=True)

    def send_response(self, code: int, message: Optional[str] = None) -> None:
        self._response_status = code
        super().send_response(code, message)

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _auth_guard(self, path: str) -> bool:
        """控制台鉴权网关：返回 True 表示已拦截（已写出 401），调用方应立即 return。
        仅拦截 /api/*（静态资源与 SPA 放行，以便加载登录页）；公开 API 例外。"""
        if not path.startswith("/api/"):
            return False
        if is_public_api_path(path):
            return False
        if console_authenticated(self):
            return False
        json_response(
            self,
            {"success": False, "message": "未登录或会话已过期", "code": "unauthorized"},
            401,
        )
        return True

    def _serve_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _content_type_for(self, path: Path) -> str:
        suffix = path.suffix.lower()
        return {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".ico": "image/x-icon",
            ".woff": "font/woff",
            ".woff2": "font/woff2",
            ".map": "application/json; charset=utf-8",
        }.get(suffix, "application/octet-stream")

    def _serve_spa(self, request_path: str) -> bool:
        """Serve Vite React build when present; fall back to legacy static/."""
        if WEB_DIST_DIR.exists():
            rel = request_path.lstrip("/") or "index.html"
            candidate = (WEB_DIST_DIR / rel).resolve()
            try:
                candidate.relative_to(WEB_DIST_DIR.resolve())
            except ValueError:
                return False
            if candidate.is_file():
                self._serve_file(candidate, self._content_type_for(candidate))
                return True
            # SPA client routes → index.html
            index = WEB_DIST_DIR / "index.html"
            if index.is_file():
                self._serve_file(index, "text/html; charset=utf-8")
                return True
            return False

        if request_path in {"/", "/index.html"}:
            self._serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return True
        if request_path == "/app.js":
            self._serve_file(STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
            return True
        if request_path == "/styles.css":
            self._serve_file(STATIC_DIR / "styles.css", "text/css; charset=utf-8")
            return True
        return False

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if self._auth_guard(path):
            return

        if path == "/api/auth/status":
            return json_response(self, {
                "success": True,
                "auth_required": console_auth_enabled(),
                "authenticated": console_authenticated(self),
            })
        if path == "/api/overview":
            return json_response(self, overview_payload())
        if path == "/api/sites":
            sites_data, auto_sync_results = list_sites_payload()
            return json_response(
                self,
                {"data": sites_data, "auto_sync": auto_sync_results},
            )
        sync_status_match = re.fullmatch(
            r"/api/sites/([0-9]+)/session-sync/requests/([A-Za-z0-9_-]{1,64})",
            path,
        )
        if sync_status_match:
            site_id = int(sync_status_match.group(1))
            request_id = sync_status_match.group(2)
            payload = get_site_session_sync_request(site_id, request_id)
            if payload is None:
                return json_response(
                    self,
                    {"success": False, "message": "同步请求不存在"},
                    404,
                )
            return json_response(self, {"success": True, "data": payload})
        if path == "/api/changes":
            params = parse_qs(parsed.query)
            limit = int(params.get("limit", ["100"])[0] or 100)
            return json_response(self, {"data": list_changes(limit)})
        if path == "/api/settings":
            return json_response(
                self,
                {
                    "success": True,
                    "data": {SETTING_RECONCILE_MODE: get_main_site_reconcile_mode()},
                },
            )
        if path == "/api/notifications/settings":
            return json_response(self, {"data": notification_settings_payload()})
        if path == "/api/notifications/logs":
            return json_response(self, {"data": db_query_all("SELECT * FROM notification_logs ORDER BY id DESC LIMIT 30")})
        if path.startswith("/api/sites/") and path.endswith("/snapshots"):
            try:
                site_id = int(path.split("/")[3])
            except Exception:
                return self.send_error(HTTPStatus.BAD_REQUEST, "invalid site id")
            return json_response(self, {"data": list_snapshots(site_id)})
        if path.startswith("/api/sites/") and path.endswith("/changes"):
            try:
                site_id = int(path.split("/")[3])
            except Exception:
                return self.send_error(HTTPStatus.BAD_REQUEST, "invalid site id")
            params = parse_qs(parsed.query)
            limit = int(params.get("limit", ["100"])[0] or 100)
            return json_response(self, {"data": list_site_changes(site_id, limit)})
        # NewAPI-style split APIs (pricing catalog + perf metrics), matching upstream frontend:
        #  - GET /api/pricing
        #  - GET /api/perf-metrics/summary?hours=
        #  - GET /api/perf-metrics?model=&hours=&group=
        if path.startswith("/api/sites/") and path.endswith("/pricing"):
            try:
                site_id = int(path.split("/")[3])
            except Exception:
                return json_response(self, {"success": False, "message": "invalid site id"}, 400)
            site, err, code = get_site_or_404(site_id)
            if err:
                return json_response(self, err, code)
            if (site.get("platform") or "newapi") != "newapi":
                return json_response(self, {"success": False, "message": "pricing 仅支持 NewAPI 站点"}, 400)
            # Browser-aware: routes through the unified executor so browser-mode
            # sites use Bearer + X-Auth-Session + Cookie, and 401/403 triggers
            # exactly one forced refresh + retry.  Token mode stays unchanged.
            ok, payload, error_message = fetch_newapi_pricing_for_site(site)
            if not ok:
                return json_response(self, {"success": False, "message": error_message, "upstream": payload}, 502)
            # pass-through NewAPI body; annotate site context
            if isinstance(payload, dict):
                payload = dict(payload)
                payload["site_id"] = site_id
                payload["base_url"] = site["base_url"]
                auth_mode = str(site.get("auth_mode") or "token").strip().lower()
                if auth_mode == "browser":
                    payload["auth_used"] = bool(
                        site.get("browser_access_token") and site.get("browser_session_id")
                    )
                else:
                    payload["auth_used"] = bool(
                        site.get("access_token") and site.get("access_user_id")
                    )
            return json_response(self, payload)

        if path.startswith("/api/sites/") and path.endswith("/perf-metrics/summary"):
            try:
                site_id = int(path.split("/")[3])
            except Exception:
                return json_response(self, {"success": False, "message": "invalid site id"}, 400)
            site, err, code = get_site_or_404(site_id)
            if err:
                return json_response(self, err, code)
            if (site.get("platform") or "newapi") != "newapi":
                return json_response(self, {"success": False, "message": "perf-metrics 仅支持 NewAPI 站点"}, 400)
            params = parse_qs(parsed.query)
            hours = clamp_perf_hours((params.get("hours") or ["24"])[0], 24)
            ok, payload, error_message = fetch_newapi_perf_summary_for_site(
                site, hours=hours
            )
            if not ok:
                return json_response(self, {"success": False, "message": error_message, "upstream": payload}, 502)
            if isinstance(payload, dict):
                payload = dict(payload)
                payload["site_id"] = site_id
                payload["hours"] = hours
                payload["note"] = (
                    "summary 为全站模型级汇总，不随 group 筛选变化；"
                    "分组仅用于 pricing 过滤模型名单（与 NewAPI 前端列表一致）"
                )
            return json_response(self, payload)

        if path.startswith("/api/sites/") and ("/perf-metrics" in path) and not path.endswith("/summary"):
            # /api/sites/:id/perf-metrics
            parts = [p for p in path.split("/") if p]
            # ['api','sites',':id','perf-metrics']
            try:
                site_id = int(parts[2])
            except Exception:
                return json_response(self, {"success": False, "message": "invalid site id"}, 400)
            if len(parts) != 4 or parts[3] != "perf-metrics":
                return json_response(self, {"success": False, "message": "not found"}, 404)
            site, err, code = get_site_or_404(site_id)
            if err:
                return json_response(self, err, code)
            if (site.get("platform") or "newapi") != "newapi":
                return json_response(self, {"success": False, "message": "perf-metrics 仅支持 NewAPI 站点"}, 400)
            params = parse_qs(parsed.query)
            model_name = (params.get("model") or [""])[0]
            group = (params.get("group") or [""])[0]
            hours = clamp_perf_hours((params.get("hours") or ["24"])[0], 24)
            if not model_name.strip():
                return json_response(self, {"success": False, "message": "model is required"}, 400)
            ok, payload, error_message = fetch_newapi_perf_detail_for_site(
                site,
                model_name=model_name,
                hours=hours,
                group=group,
            )
            if not ok:
                return json_response(self, {"success": False, "message": error_message, "upstream": payload}, 502)
            if isinstance(payload, dict):
                payload = dict(payload)
                payload["site_id"] = site_id
                payload["hours"] = hours
                payload["requested_model"] = model_name
                payload["requested_group"] = group or None
            return json_response(self, payload)

        if path.startswith("/api/sites/") and path.endswith("/account"):
            try:
                site_id = int(path.split("/")[3])
            except Exception:
                return json_response(self, {"success": False, "message": "invalid site id"}, 400)
            site, err, code = get_site_or_404(site_id)
            if err:
                return json_response(self, err, code)
            status, payload = build_site_account_payload(site)
            return json_response(self, payload, status)

        if path.startswith("/api/sites/") and path.endswith("/discovery-links"):
            try:
                site_id = int(path.split("/")[3])
            except (TypeError, ValueError, IndexError):
                return json_response(
                    self,
                    {"success": False, "message": "invalid site id"},
                    400,
                )
            site, err, code = get_site_or_404(site_id)
            if err:
                return json_response(self, err, code)
            return json_response(
                self,
                {"success": True, "data": list_site_discovery_links(site_id)},
            )

        if path.startswith("/api/sites/") and path.endswith("/models"):
            try:
                site_id = int(path.split("/")[3])
            except Exception:
                return json_response(self, {"success": False, "message": "invalid site id"}, 400)
            site = db_query_one("SELECT * FROM sites WHERE id = ?", (site_id,))
            if not site:
                return json_response(self, {"success": False, "message": "site not found"}, 404)
            cached_payload, cache_age = get_site_model_cache(site_id)
            if cached_payload is not None:
                cached_payload["cache_hit"] = True
                cached_payload["cache_age_seconds"] = round(cache_age, 1)
                if cache_age >= MODEL_CACHE_TTL_SECONDS:
                    schedule_model_cache_refresh(site_id)
                    cached_payload["refreshing"] = True
                return json_response(self, cached_payload)

            status, payload = refresh_site_model_cache(site_id)
            payload["cache_hit"] = False
            return json_response(self, payload, status)

        # 管理站点（NewAPI 后台）列表：GET /api/admin/sites
        if path == "/api/admin/sites":
            return json_response(self, {"data": list_admin_sites_payload()})

        # NewAPI 渠道管理（管理员薄代理，凭管理站点令牌）：
        #   GET /api/admin/sites/:id/groups            分组名 → 倍率/描述（供比对）
        #   GET /api/admin/sites/:id/channel-mappings  渠道 key 的上游匹配结果
        #   GET /api/admin/sites/:id/channels          渠道列表（密钥掩码）
        #   GET /api/admin/sites/:id/channels/:cid     单渠道详情（明文密钥，供点击显示）
        #   GET /api/admin/sites/:id/channels/:cid/test 测试渠道连通
        if path.startswith("/api/admin/sites/"):
            parts = [p for p in path.split("/") if p]
            try:
                admin_site_id = int(parts[3])
            except Exception:
                return json_response(self, {"success": False, "message": "invalid admin site id"}, 400)
            site, err, code = get_admin_site_or_404(admin_site_id)
            if err:
                return json_response(self, err, code)
            if len(parts) == 5 and parts[4] == "channel-candidates":
                if str(site.get("platform") or "newapi").strip().lower() != "newapi":
                    return json_response(
                        self,
                        {
                            "success": False,
                            "message": "主站渠道发现仅支持 NewAPI",
                        },
                        405,
                    )
                # Always fetch the complete upstream list.  A keyword only
                # filters the already-deduplicated display rows, so it cannot
                # change grouping semantics or hide a duplicate source URL.
                ok, channels, source_meta, error = fetch_admin_site_channels(site, "")
                if not ok:
                    return json_response(
                        self,
                        {"success": False, "message": error or "读取主站渠道失败"},
                        502,
                    )
                source_channels = channels if isinstance(channels, list) else []
                candidates = aggregate_newapi_channel_candidates(source_channels)
                candidates = enrich_channel_candidates_with_sites(candidates)
                keyword = str(
                    (parse_qs(parsed.query).get("keyword") or [""])[0]
                    or ""
                ).strip().casefold()
                if keyword:
                    candidates = [
                        candidate
                        for candidate in candidates
                        if keyword
                        in " ".join(
                            [
                                str(candidate.get("base_url") or ""),
                                str(candidate.get("name") or ""),
                                " ".join(
                                    str(name or "")
                                    for name in candidate.get("channel_names") or []
                                ),
                            ]
                        ).casefold()
                    ]
                try:
                    source_channel_total = int(
                        (source_meta if isinstance(source_meta, dict) else {}).get("total")
                        or len(source_channels)
                    )
                except (TypeError, ValueError):
                    source_channel_total = len(source_channels)
                return json_response(
                    self,
                    {
                        "success": True,
                        "data": candidates,
                        "meta": {
                            "total": len(candidates),
                            "source_channel_total": source_channel_total,
                        },
                    },
                )
            if len(parts) == 5 and parts[4] == "groups":
                ok, payload, error = fetch_admin_site_groups(site)
                if not ok:
                    if admin_site_platform(site) == "sub2api":
                        status, response = sub2api_proxy_error_response(
                            payload, error, "读取 sub2api 分组失败"
                        )
                        return json_response(self, response, status)
                    return json_response(self, {"success": False, "message": error, "upstream": payload}, 502)
                return json_response(self, payload)
            if len(parts) == 5 and parts[4] == "channel-mappings":
                if admin_site_platform(site) == "sub2api":
                    return json_response(
                        self,
                        {
                            "success": False,
                            "message": "sub2api 主站不使用 NewAPI 渠道 key 匹配",
                        },
                        405,
                    )
                return json_response(self, {"success": True, "data": list_channel_upstream_bindings(admin_site_id)})
            if len(parts) == 5 and parts[4] == "channels":
                params = parse_qs(parsed.query)
                keyword = (params.get("keyword") or [""])[0]
                ok, items, meta, error = fetch_admin_site_channels(site, keyword)
                if not ok:
                    if admin_site_platform(site) == "sub2api":
                        status, response = sub2api_proxy_error_response(
                            meta, error, "读取 sub2api 渠道失败"
                        )
                        return json_response(self, response, status)
                    return json_response(
                        self,
                        {"success": False, "message": error},
                        502,
                    )
                data = (
                    [mask_channel_in_place(item) for item in items]
                    if admin_site_platform(site) == "newapi"
                    else items
                )
                return json_response(self, {"success": True, "data": data, "meta": meta})
            if len(parts) >= 6 and parts[4] == "channels":
                try:
                    channel_id = int(parts[5])
                except Exception:
                    return json_response(self, {"success": False, "message": "invalid channel id"}, 400)
                if len(parts) == 7 and parts[6] == "test":
                    if admin_site_platform(site) == "sub2api":
                        return json_response(
                            self,
                            {
                                "success": False,
                                "message": "sub2api 主站不支持 NewAPI 渠道测试接口",
                            },
                            405,
                        )
                    ok, payload, error = test_newapi_channel(site, channel_id)
                    if not ok:
                        return json_response(self, {"success": False, "message": error, "upstream": payload}, 502)
                    return json_response(self, payload)
                if len(parts) == 6:
                    ok, payload, error = fetch_admin_site_channel_detail(site, channel_id)
                    if not ok:
                        if admin_site_platform(site) == "sub2api":
                            status, response = sub2api_proxy_error_response(
                                payload, error, "读取 sub2api 渠道详情失败"
                            )
                            return json_response(self, response, status)
                        return json_response(self, {"success": False, "message": error, "upstream": payload}, 502)
                    detail = payload.get("data") if isinstance(payload, dict) else None
                    if (
                        admin_site_platform(site) == "newapi"
                        and isinstance(detail, dict)
                        and _channel_key_is_masked(detail.get("key"))
                    ):
                        key_ok, channel_key, key_error = fetch_newapi_channel_key(site, channel_id)
                        if key_ok:
                            detail = dict(detail)
                            detail["key"] = channel_key
                            payload = dict(payload)
                            payload["data"] = detail
                        elif key_error:
                            payload = dict(payload)
                            payload["key_error"] = key_error
                    return json_response(self, payload)  # 明文密钥：仅点击显示时按需拉取
            return json_response(self, {"success": False, "message": "not found"}, 404)

        if self._serve_spa(path):
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if self._auth_guard(path):
            return

        try:
            complete_match = re.fullmatch(
                r"/api/session-sync/requests/([A-Za-z0-9_-]{1,64})/complete",
                path,
            )
            if complete_match:
                try:
                    body = read_bounded_json_body(self)
                except ValueError as exc:
                    status = 413 if "过大" in str(exc) else 400
                    return json_response(
                        self,
                        {
                            "success": False,
                            "status": "failed",
                            "code": "SYNC_BODY_INVALID",
                            "message": str(exc),
                        },
                        status,
                    )
                status, payload = complete_session_sync_request(
                    complete_match.group(1),
                    str(self.headers.get("X-Upstream-Sync-Token") or ""),
                    body,
                )
                return json_response(self, payload, status)

            if path == "/api/auth/login":
                body = read_json_body(self)
                body = body if isinstance(body, dict) else {}
                if not console_auth_enabled():
                    return json_response(self, {"success": True, "auth_required": False, "token": ""})
                password = str(body.get("password") or "")
                # 用 UTF-8 字节做恒定时间比较：直接比较字符串时，非 ASCII 密码会触发
                # secrets.compare_digest 的 "comparing strings with non-ASCII characters"
                # TypeError，进而 500 且永远发不出 token——对纯中文场景等于把自己锁在门外。
                if not password or not secrets.compare_digest(
                    password.encode("utf-8"), CONSOLE_PASSWORD.encode("utf-8")
                ):
                    return json_response(self, {"success": False, "message": "密码错误"}, 401)
                token = create_console_session()
                return json_response(self, {"success": True, "token": token})

            if path == "/api/auth/logout":
                drop_console_session(request_bearer_token(self))
                return json_response(self, {"success": True})

            if path == "/api/check-connection":
                body = read_json_body(self)
                base_url = normalize_base_url(str(body.get("base_url") or ""))
                platform = str(body.get("platform") or "newapi").strip().lower()
                if not base_url:
                    return json_response(self, {"success": False, "message": "base_url required"}, 400)
                if platform == "sub2api":
                    result = probe_sub2api_groups(
                        base_url,
                        username=str(body.get("login_username") or "").strip(),
                        password=str(body.get("login_password") or ""),
                        auth_mode=str(body.get("auth_mode") or "password").strip().lower(),
                        access_token=str(body.get("access_token") or "").strip(),
                        refresh_token=str(body.get("refresh_token") or "").strip(),
                    )
                else:
                    result = probe_newapi_groups(base_url)
                return json_response(self, result)

            if path == "/api/check-login":
                body = read_json_body(self)
                base_url = normalize_base_url(str(body.get("base_url") or ""))
                auth_mode = str(body.get("auth_mode") or "token").strip().lower()
                if auth_mode == "password":
                    username = str(body.get("login_username") or "").strip()
                    password = str(body.get("login_password") or "")
                    verification_code = str(body.get("two_factor_code") or "").strip()
                    if not base_url or not username or not password:
                        return json_response(self, {"success": False, "message": "Base URL、用户名和密码都需要填写"}, 400)
                    groups_ok, result, groups_error = probe_newapi_password_login(
                        base_url, username, password, verification_code
                    )
                    return json_response(self, {
                        "success": groups_ok,
                        "requires_2fa": bool(result.get("requires_2fa")),
                        "message": groups_error or result.get("warning") or "用户名密码验证成功",
                        "groups_count": result.get("groups_count", 0),
                    })
                access_token = str(body.get("access_token") or "").strip()
                access_user_id = str(body.get("access_user_id") or "").strip()
                if not base_url or not access_token or not access_user_id:
                    return json_response(self, {"success": False, "message": "Base URL、系统访问令牌、NewAPI 用户 ID 都需要填写"}, 400)
                groups_ok, groups_payload, groups_error = fetch_newapi_groups_with_access_token(base_url, access_token, access_user_id)
                groups = parse_groups_payload(groups_payload) if groups_ok else {}
                return json_response(self, {
                    "success": groups_ok,
                    "message": newapi_auth_failure_message(groups_payload, groups_error) if not groups_ok else "访问令牌验证成功",
                    "groups_count": len(groups),
                    "groups": groups,
                })

            # POST /api/sites/sync
            # Manually trigger a complete channel + group snapshot for one
            # selected admin site.  An omitted ID keeps the old all-sites
            # behavior for command-line/API callers.
            if path == "/api/sites/sync":
                body = read_json_body(self)
                body = body if isinstance(body, dict) else {}
                raw_admin_site_id = body.get("admin_site_id")
                admin_site_id: Optional[int] = None
                if raw_admin_site_id not in (None, ""):
                    try:
                        admin_site_id = int(raw_admin_site_id)
                    except (TypeError, ValueError):
                        return json_response(
                            self,
                            {"success": False, "message": "管理站点 ID 无效"},
                            400,
                        )
                    if admin_site_id <= 0:
                        return json_response(
                            self,
                            {"success": False, "message": "管理站点 ID 无效"},
                            400,
                        )
                results = auto_sync_admin_site_channels_to_sites(admin_site_id)
                imported = sum(
                    int(entry.get("imported") or 0)
                    for entry in results
                    if isinstance(entry, dict)
                )
                conflicts = sum(
                    int(entry.get("conflict_count") or 0)
                    for entry in results
                    if isinstance(entry, dict)
                )
                channels_changed = any(
                    bool(entry.get("channels_changed"))
                    for entry in results
                    if isinstance(entry, dict)
                )
                groups_changed = any(
                    bool(entry.get("groups_changed"))
                    for entry in results
                    if isinstance(entry, dict)
                )
                keys_refreshed = sum(
                    int(entry.get("keys_refreshed") or 0)
                    for entry in results
                    if isinstance(entry, dict)
                )
                keys_changed = sum(
                    int(entry.get("keys_changed") or 0)
                    for entry in results
                    if isinstance(entry, dict)
                )
                keys_failed = sum(
                    int(entry.get("keys_failed") or 0)
                    for entry in results
                    if isinstance(entry, dict)
                )
                key_errors: List[str] = []
                for entry in results:
                    for message in (entry.get("key_errors") or []) if isinstance(entry, dict) else []:
                        if message not in key_errors:
                            key_errors.append(str(message))
                reconcile = next(
                    (
                        entry
                        for entry in results
                        if isinstance(entry, dict)
                        and entry.get("status") == "reconcile"
                    ),
                    {},
                )
                failed = [
                    entry
                    for entry in results
                    if isinstance(entry, dict)
                    and entry.get("status") in {"fetch_failed", "sync_failed", "error"}
                ]
                return json_response(
                    self,
                    {
                        "success": True,
                        "data": results,
                        "mode": reconcile.get("mode") or RECONCILE_MODE_DISABLE,
                        "channels_changed": channels_changed,
                        "groups_changed": groups_changed,
                        "keys_refreshed": keys_refreshed,
                        "keys_changed": keys_changed,
                        "keys_failed": keys_failed,
                        "key_errors": key_errors[:3],
                        "imported": imported,
                        "conflicts": conflicts,
                        "disabled": int(reconcile.get("disabled") or 0),
                        "reenabled": int(reconcile.get("reenabled") or 0),
                        "deleted": int(reconcile.get("deleted") or 0),
                        "failed": len(failed),
                    },
                )

            # POST /api/sites/discovery-import
            # Create/reuse local NewAPI monitoring sites from candidates returned
            # by the authenticated admin-site discovery endpoint.  Browser
            # session synchronization is deliberately a separate user action.
            if path == "/api/sites/discovery-import":
                body = read_json_body(self)
                body = body if isinstance(body, dict) else {}
                try:
                    admin_site_id = int(body.get("admin_site_id") or 0)
                except (TypeError, ValueError):
                    return json_response(
                        self,
                        {"success": False, "message": "管理站点 ID 无效"},
                        400,
                    )
                if admin_site_id <= 0:
                    return json_response(
                        self,
                        {"success": False, "message": "管理站点 ID 无效"},
                        400,
                    )
                site, err, code = get_admin_site_or_404(admin_site_id)
                if err:
                    return json_response(self, err, code)
                if str(site.get("platform") or "newapi").strip().lower() != "newapi":
                    return json_response(
                        self,
                        {
                            "success": False,
                            "message": "主站渠道发现导入仅支持 NewAPI",
                        },
                        405,
                    )
                result = import_discovered_sites(site, body)
                if isinstance(result, dict) and result.get("error"):
                    status = 413 if result.get("error") == "too_many_items" else 400
                    return json_response(
                        self,
                        {"success": False, "message": result.get("message") or "导入请求无效", "error": result.get("error")},
                        status,
                    )
                return json_response(self, {"success": True, "data": result or []})

            if path == "/api/sites":
                body = read_json_body(self)
                name = str(body.get("name") or "").strip()
                base_url = normalize_base_url(str(body.get("base_url") or ""))
                platform = str(body.get("platform") or "newapi").strip().lower()
                enabled = bool(body.get("enabled", True))
                interval = int(body.get("interval_minutes") or DEFAULT_INTERVAL_MINUTES)
                interval = max(MIN_INTERVAL_MINUTES, interval)
                login_enabled = bool(body.get("login_enabled", False))
                login_username = str(body.get("login_username") or "").strip()
                login_password = str(body.get("login_password") or "")
                access_token = str(body.get("access_token") or "").strip()
                access_user_id = str(body.get("access_user_id") or "").strip()
                refresh_token = str(body.get("refresh_token") or "").strip()
                token_expires_at = str(body.get("token_expires_at") or "").strip()
                auth_mode = str(body.get("auth_mode") or "password").strip().lower()
                if platform not in {"newapi", "sub2api"}:
                    return json_response(self, {"success": False, "message": "platform invalid"}, 400)
                if auth_mode not in {"password", "token", BROWSER_AUTH_MODE}:
                    return json_response(self, {"success": False, "message": "auth_mode invalid"}, 400)
                if not name or not base_url:
                    return json_response(self, {"success": False, "message": "name/base_url required"}, 400)
                if (
                    platform == "newapi"
                    and auth_mode == "token"
                    and login_enabled
                    and (not access_token or not access_user_id)
                ):
                    return json_response(self, {"success": False, "message": "使用系统访问令牌时需要填写 NewAPI 用户 ID"}, 400)
                if platform == "newapi" and auth_mode == "password" and (
                    not login_username or not login_password
                ):
                    return json_response(self, {"success": False, "message": "NewAPI 用户名密码模式需要填写用户名和密码"}, 400)
                if platform == "sub2api" and auth_mode == "password" and (not login_username or not login_password):
                    return json_response(self, {"success": False, "message": "sub2api 需要填写普通用户邮箱和密码"}, 400)
                if platform == "sub2api" and auth_mode == "token" and not access_token:
                    return json_response(self, {"success": False, "message": "导入登录态时需要填写 auth_token"}, 400)
                now = utc_now_iso()
                try:
                    site_id = db_execute(
                        """
                        INSERT INTO sites
                        (name, base_url, platform, enabled, interval_minutes, login_enabled, auth_mode, login_username, login_password, access_token, access_user_id, refresh_token, token_expires_at, status, last_error, last_check_at, next_check_at, consecutive_failures, current_groups_json, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unknown', NULL, NULL, ?, 0, NULL, ?, ?)
                        """,
                        (
                            name,
                            base_url,
                            platform,
                            1 if enabled else 0,
                            interval,
                            1
                            if (
                                login_enabled
                                or platform == "sub2api"
                                or auth_mode == BROWSER_AUTH_MODE
                                or (platform == "newapi" and auth_mode == "password")
                            )
                            else 0,
                            auth_mode,
                            login_username
                            if (
                                (platform == "sub2api"
                                and auth_mode in {"password", BROWSER_AUTH_MODE})
                                or (platform == "newapi" and auth_mode == "password")
                            )
                            else "",
                            login_password
                            if (
                                (platform == "sub2api"
                                and auth_mode in {"password", BROWSER_AUTH_MODE})
                                or (platform == "newapi" and auth_mode == "password")
                            )
                            else "",
                            access_token
                            if (
                                (platform == "newapi" and login_enabled and auth_mode == "token")
                                or (
                                    platform == "sub2api"
                                    and auth_mode in {"token", BROWSER_AUTH_MODE}
                                )
                            )
                            else "",
                            access_user_id
                            if platform == "newapi" and login_enabled and auth_mode == "token"
                            else "",
                            refresh_token
                            if platform == "sub2api"
                            and auth_mode in {"token", BROWSER_AUTH_MODE}
                            else "",
                            token_expires_at
                            if platform == "sub2api"
                            and auth_mode in {"token", BROWSER_AUTH_MODE}
                            else "",
                            next_check_iso(interval),
                            now,
                            now,
                        ),
                    )
                    return json_response(self, {"success": True, "id": site_id})
                except Exception as insert_err:
                    # MySQL 1062 (Duplicate entry) on sites.base_url UNIQUE: a row with the
                    # same base_url already exists.  Return that row's id instead of
                    # failing, so a "从主站同步" call that re-posts a known base_url just
                    # becomes a no-op rather than a hard error.
                    err_text = str(insert_err).lower()
                    if "1062" in err_text or "duplicate" in err_text:
                        existing = db_query_one(
                            "SELECT id FROM sites WHERE base_url = ? LIMIT 1",
                            (base_url,),
                        )
                        if existing and "id" in existing:
                            return json_response(
                                self,
                                {
                                    "success": True,
                                    "id": int(existing["id"]),
                                    "existed": True,
                                },
                            )
                    raise

            sync_create_match = re.fullmatch(
                r"/api/sites/([0-9]+)/session-sync/requests", path
            )
            if sync_create_match:
                site_id = int(sync_create_match.group(1))
                ok, payload, error = create_site_session_sync_request(site_id)
                if not ok:
                    status = 404 if error == "渠道不存在" else 400
                    return json_response(
                        self, {"success": False, "message": error}, status
                    )
                return json_response(self, {"success": True, "data": payload}, 201)

            sync_fail_match = re.fullmatch(
                r"/api/sites/([0-9]+)/session-sync/requests/([A-Za-z0-9_-]{1,64})/fail",
                path,
            )
            if sync_fail_match:
                body = read_json_body(self)
                body = body if isinstance(body, dict) else {}
                site_id = int(sync_fail_match.group(1))
                request_id = sync_fail_match.group(2)
                ok, error = fail_site_session_sync_request(
                    site_id, request_id, str(body.get("code") or "")
                )
                if not ok:
                    return json_response(
                        self, {"success": False, "message": error}, 400
                    )
                return json_response(self, {"success": True})

            if path.startswith("/api/sites/") and path.endswith("/check"):
                try:
                    site_id = int(path.split("/")[3])
                except Exception:
                    return json_response(self, {"success": False, "message": "invalid site id"}, 400)
                result = detect_site(site_id)
                return json_response(self, result)

            password_login_match = re.fullmatch(r"/api/sites/([0-9]+)/auth/login", path)
            if password_login_match:
                site_id = int(password_login_match.group(1))
                site = db_query_one("SELECT * FROM sites WHERE id = ?", (site_id,))
                if not site:
                    return json_response(self, {"success": False, "message": "site not found"}, 404)
                body = read_json_body(self)
                body = body if isinstance(body, dict) else {}
                ok, result, error = login_newapi_site_with_password(
                    site, str(body.get("two_factor_code") or "").strip()
                )
                if not ok:
                    return json_response(self, {
                        "success": False,
                        "requires_2fa": bool(result.get("requires_2fa")),
                        "message": error or "NewAPI 登录失败",
                    })
                return json_response(self, {
                    "success": True,
                    "message": "NewAPI 用户登录成功",
                    "groups_count": result.get("groups_count", 0),
                    "warning": result.get("warning"),
                })

            if path == "/api/notifications/test-email":
                body = read_json_body(self)
                if body:
                    update_notification_settings(body)
                message = "这是一封上游分组倍率监控测试邮件。"
                ok, error_message = send_email_message("上游倍率监控邮箱测试", message)
                return json_response(self, {"success": ok, "message": error_message or "测试邮件已发送"})

            if path == "/api/notifications/test-wecom":
                body = read_json_body(self)
                if body:
                    update_notification_settings(body)
                message = "这是一条上游分组倍率监控测试消息。"
                ok, error_message = send_wecom_message("上游倍率监控企业微信测试", message)
                return json_response(self, {"success": ok, "message": error_message or "测试消息已发送"})

            # POST /api/admin/sites/test  测试统一主站配置（不保存）
            if path == "/api/admin/sites/test":
                body = read_json_body(self)
                body = body if isinstance(body, dict) else {}
                ok, result, error = test_admin_site_connection(body)
                if not ok:
                    if result.get("error_source") == "upstream":
                        status, response = sub2api_proxy_error_response(
                            result.get("details"),
                            error,
                            "sub2api 主站连接测试失败",
                        )
                        return json_response(self, response, status)
                    return json_response(
                        self, {"success": False, "message": error}, 400
                    )
                return json_response(self, {"success": True, **result})

            # POST /api/admin/sites  新增管理站点
            if path == "/api/admin/sites":
                body = read_json_body(self)
                body = body if isinstance(body, dict) else {}
                ok, result, error = create_admin_site(body)
                if not ok:
                    if isinstance(error, Sub2ApiUpstreamError):
                        status, response = sub2api_proxy_error_response(
                            error.payload,
                            str(error),
                            "sub2api 主站登录验证失败",
                        )
                        return json_response(self, response, status)
                    return json_response(self, {"success": False, "message": error}, 400)
                return json_response(self, {"success": True, "id": result})

            # POST /api/admin/sites/:id/channels        新增渠道
            # POST /api/admin/sites/:id/channels/batch   批量操作
            if path.startswith("/api/admin/sites/"):
                parts = [p for p in path.split("/") if p]
                try:
                    admin_site_id = int(parts[3])
                except Exception:
                    return json_response(self, {"success": False, "message": "invalid admin site id"}, 400)
                site, err, code = get_admin_site_or_404(admin_site_id)
                if err:
                    return json_response(self, err, code)
                # POST /api/admin/sites/:id/key-verification  为主站渠道 key 读取申请 2FA proof
                if len(parts) == 5 and parts[4] == "key-verification":
                    if admin_site_platform(site) == "sub2api":
                        return json_response(
                            self,
                            {
                                "success": False,
                                "message": "sub2api 主站不使用 NewAPI key 安全验证",
                            },
                            405,
                        )
                    body = read_json_body(self)
                    body = body if isinstance(body, dict) else {}
                    verified, verify_error = verify_admin_site_channel_key_access(
                        admin_site_id, str(body.get("code") or "")
                    )
                    if not verified:
                        return json_response(self, {"success": False, "message": verify_error}, 400)
                    return json_response(self, {"success": True, "message": "主站 key 读取权限已验证"})
                # POST /api/admin/sites/:id/channels/batch  批量启用/停用/删除/设分组/打标签
                if len(parts) == 6 and parts[4] == "channels" and parts[5] == "batch":
                    if admin_site_platform(site) == "sub2api":
                        return json_response(
                            self,
                            {
                                "success": False,
                                "message": "sub2api 主站不支持 NewAPI 渠道批量操作",
                            },
                            405,
                        )
                    body = read_json_body(self)
                    body = body if isinstance(body, dict) else {}
                    ok, payload, error = batch_channel_operation(
                        site, str(body.get("action") or ""), body.get("ids"), body
                    )
                    if not ok:
                        return json_response(self, {"success": False, "message": error}, 400)
                    if str(body.get("action") or "") == "delete":
                        for result in payload.get("results") or []:
                            if result.get("ok"):
                                db_execute(
                                    "DELETE FROM channel_upstream_bindings WHERE admin_site_id = ? AND channel_id = ?",
                                    (admin_site_id, result.get("id")),
                                )
                    return json_response(self, {"success": True, "data": payload})
                if len(parts) == 7 and parts[4] == "channels" and parts[6] == "match":
                    if admin_site_platform(site) == "sub2api":
                        return json_response(
                            self,
                            {
                                "success": False,
                                "message": "sub2api 主站不使用渠道 key 匹配",
                            },
                            405,
                        )
                    try:
                        channel_id = int(parts[5])
                    except Exception:
                        return json_response(self, {"success": False, "message": "invalid channel id"}, 400)
                    query = parse_qs(urlparse(self.path).query)
                    force_refresh = str((query.get("refresh") or [""])[0]).lower() in {
                        "1", "true", "yes"
                    }
                    ok, payload, error = match_channel_upstream_binding(
                        site, channel_id, force_refresh=force_refresh
                    )
                    if not ok:
                        binding_row = get_channel_upstream_binding(admin_site_id, channel_id)
                        binding_payload = channel_upstream_binding_payload(binding_row)
                        # A completed match attempt can fail as a business result
                        # (key missing, no group, upstream unavailable). Return the
                        # persisted status so the UI can distinguish stale cache
                        # from a definitive mismatch without guessing from text.
                        binding_payload["configured"] = True
                        binding_payload["inherited_from_monitor"] = not bool(
                            binding_row and binding_row.get("upstream_base_url")
                        )
                        return json_response(
                            self,
                            {
                                "success": False,
                                "data": binding_payload,
                                "message": error,
                            },
                        )
                    return json_response(self, {"success": True, "data": payload})
                if len(parts) == 5 and parts[4] == "channels":
                    if admin_site_platform(site) == "sub2api":
                        return json_response(
                            self,
                            {
                                "success": False,
                                "message": "sub2api 主站不允许在本系统新建渠道",
                            },
                            405,
                        )
                    body = read_json_body(self)
                    if not isinstance(body, dict) or not body:
                        return json_response(self, {"success": False, "message": "渠道内容为空"}, 400)
                    existing_ids: set[int] = set()
                    existing_ok, existing_items, _existing_error = fetch_all_newapi_channels(site)
                    if existing_ok:
                        for existing_item in existing_items:
                            try:
                                existing_ids.add(int(existing_item.get("id")))
                            except (TypeError, ValueError):
                                continue
                    ok, payload, error = create_newapi_channel(site, body)
                    if not ok:
                        return json_response(self, {"success": False, "message": error, "upstream": payload}, 502)
                    response = dict(payload) if isinstance(payload, dict) else {"success": True}
                    created_data = response.get("data")
                    created_id = response.get("id")
                    if created_id is None and isinstance(created_data, dict):
                        created_id = created_data.get("id")
                    if created_id is None and isinstance(created_data, list) and created_data:
                        first_created = created_data[0]
                        if isinstance(first_created, dict):
                            created_id = first_created.get("id")
                    if created_id is None:
                        created_id, resolve_error = resolve_created_newapi_channel_id(
                            site, body, existing_ids
                        )
                        if resolve_error:
                            response["cache_pending"] = True
                            response["cache_message"] = resolve_error
                    if created_id is not None:
                        created_id = int(created_id)
                        response["id"] = created_id
                        if "key" in body:
                            sync_admin_channel_key(
                                admin_site_id, int(created_id), body.get("key")
                            )
                            response["key_cached"] = bool(
                                str(body.get("key") or "").strip()
                                and not _channel_key_is_masked(body.get("key"))
                            )
                    return json_response(self, response)
                return json_response(self, {"success": False, "message": "not found"}, 404)

            self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            return json_response(self, {"success": False, "message": str(exc)}, 500)

    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        if self._auth_guard(path):
            return
        # PUT /api/admin/sites/:id                     更新管理站点（名称/地址/令牌）
        # PUT /api/admin/sites/:id/channels/:cid       更新渠道（切换状态/权重/优先级/分组等，read-merge-write）
        if path.startswith("/api/admin/sites/"):
            parts = [p for p in path.split("/") if p]
            try:
                admin_site_id = int(parts[3])
            except Exception:
                return json_response(self, {"success": False, "message": "invalid path"}, 400)
            if len(parts) == 4:
                body = read_json_body(self)
                body = body if isinstance(body, dict) else {}
                ok, error = update_admin_site(admin_site_id, body)
                if not ok:
                    if isinstance(error, Sub2ApiUpstreamError):
                        status, response = sub2api_proxy_error_response(
                            error.payload,
                            str(error),
                            "sub2api 主站登录验证失败",
                        )
                        return json_response(self, response, status)
                    status = 409 if error and "平台" in error and "不可修改" in error else 400
                    return json_response(self, {"success": False, "message": error}, status)
                return json_response(self, {"success": True})
            site, err, code = get_admin_site_or_404(admin_site_id)
            if err:
                return json_response(self, err, code)
            if len(parts) == 7 and parts[4] == "channels" and parts[6] == "mapping":
                if admin_site_platform(site) == "sub2api":
                    return json_response(
                        self,
                        {
                            "success": False,
                            "message": "sub2api 主站不使用渠道 key 匹配配置",
                        },
                        405,
                    )
                try:
                    channel_id = int(parts[5])
                except Exception:
                    return json_response(self, {"success": False, "message": "invalid channel id"}, 400)
                body = read_json_body(self)
                if not isinstance(body, dict):
                    return json_response(self, {"success": False, "message": "匹配配置内容无效"}, 400)
                ok, error = save_channel_upstream_binding(admin_site_id, channel_id, body)
                if not ok:
                    return json_response(self, {"success": False, "message": error}, 400)
                return json_response(self, {
                    "success": True,
                    "data": channel_upstream_binding_payload(
                        get_channel_upstream_binding(admin_site_id, channel_id)
                    ),
                })
            if len(parts) == 6 and parts[4] == "channels":
                try:
                    channel_id = int(parts[5])
                except Exception:
                    return json_response(self, {"success": False, "message": "invalid channel id"}, 400)
                body = read_json_body(self)
                if not isinstance(body, dict) or not body:
                    return json_response(self, {"success": False, "message": "无更新字段"}, 400)
                if admin_site_platform(site) == "sub2api":
                    validation_error = validate_sub2api_admin_channel_patch(body)
                    if validation_error:
                        return json_response(
                            self,
                            {"success": False, "message": validation_error},
                            400,
                        )
                ok, payload, error = update_admin_site_channel(site, channel_id, body)
                if not ok:
                    if admin_site_platform(site) == "sub2api":
                        status, response = sub2api_proxy_error_response(
                            payload, error, "更新 sub2api 渠道失败"
                        )
                        return json_response(self, response, status)
                    return json_response(self, {"success": False, "message": error, "upstream": payload}, 502)
                if admin_site_platform(site) == "newapi" and "key" in body:
                    sync_admin_channel_key(admin_site_id, channel_id, body.get("key"))
                return json_response(self, payload)
            return json_response(self, {"success": False, "message": "not found"}, 404)

        if path.startswith("/api/sites/"):
            try:
                site_id = int(path.split("/")[3])
            except Exception:
                return json_response(self, {"success": False, "message": "invalid site id"}, 400)
            body = read_json_body(self)
            site = db_query_one("SELECT * FROM sites WHERE id = ?", (site_id,))
            if not site:
                return json_response(self, {"success": False, "message": "site not found"}, 404)
            fields = []
            params = []

            if "name" in body:
                fields.append("name = ?")
                params.append(str(body["name"]).strip())
            if "base_url" in body:
                fields.append("base_url = ?")
                params.append(normalize_base_url(str(body["base_url"])))
            target_platform = str(body.get("platform") or site.get("platform") or "newapi").strip().lower()
            if target_platform not in {"newapi", "sub2api"}:
                return json_response(self, {"success": False, "message": "platform invalid"}, 400)
            if "platform" in body:
                fields.append("platform = ?")
                params.append(target_platform)
            if "enabled" in body:
                fields.append("enabled = ?")
                params.append(1 if body["enabled"] else 0)
            if "interval_minutes" in body:
                fields.append("interval_minutes = ?")
                params.append(max(MIN_INTERVAL_MINUTES, int(body["interval_minutes"])))
            if "login_enabled" in body:
                login_enabled = bool(body["login_enabled"])
                login_username = str(body.get("login_username") or "").strip()
                login_password = str(body.get("login_password") or "")
                access_token = str(body.get("access_token") or "").strip()
                access_user_id = str(body.get("access_user_id") or "").strip()
                refresh_token = str(body.get("refresh_token") or "").strip()
                token_expires_at = str(body.get("token_expires_at") or "").strip()
                auth_mode = str(body.get("auth_mode") or site.get("auth_mode") or "password").strip().lower()
                existing_access_token = site.get("access_token") or ""
                existing_access_user_id = site.get("access_user_id") or ""
                existing_refresh_token = site.get("refresh_token") or ""
                existing_username = site.get("login_username") or ""
                existing_password = site.get("login_password") or ""
                existing_platform = str(site.get("platform") or "newapi").strip().lower()
                existing_auth_mode = str(site.get("auth_mode") or "password").strip().lower()
                same_platform = existing_platform == target_platform
                same_auth_mode = same_platform and existing_auth_mode == auth_mode
                can_preserve_newapi_auth = (
                    same_auth_mode and target_platform == "newapi"
                )
                can_preserve_sub2api_password = (
                    same_auth_mode and target_platform == "sub2api" and auth_mode == "password"
                )
                can_preserve_sub2api_token = (
                    same_auth_mode and target_platform == "sub2api" and auth_mode == "token"
                )
                can_preserve_newapi_password = (
                    same_auth_mode and target_platform == "newapi" and auth_mode == "password"
                )
                if auth_mode not in {"password", "token", BROWSER_AUTH_MODE}:
                    return json_response(self, {"success": False, "message": "auth_mode invalid"}, 400)
                if target_platform == "newapi" and auth_mode == "token":
                    has_token_after_update = bool(
                        access_token or (existing_access_token if can_preserve_newapi_auth else "")
                    )
                    has_user_id_after_update = bool(
                        access_user_id or (existing_access_user_id if can_preserve_newapi_auth else "")
                    )
                    if login_enabled and (not has_token_after_update or not has_user_id_after_update):
                        return json_response(self, {"success": False, "message": "使用系统访问令牌时需要填写 NewAPI 用户 ID"}, 400)
                if target_platform == "newapi" and auth_mode == "password" and (
                    not (
                        login_username
                        or (existing_username if can_preserve_newapi_password else "")
                    )
                    or not (
                        login_password
                        or (existing_password if can_preserve_newapi_password else "")
                    )
                ):
                    return json_response(self, {"success": False, "message": "NewAPI 用户名密码模式需要填写用户名和密码"}, 400)
                if target_platform == "sub2api" and auth_mode == "password" and (
                    not (login_username or (existing_username if can_preserve_sub2api_password else ""))
                    or not (login_password or (existing_password if can_preserve_sub2api_password else ""))
                ):
                    return json_response(self, {"success": False, "message": "sub2api 需要填写普通用户邮箱和密码"}, 400)
                if target_platform == "sub2api" and auth_mode == "token" and not (
                    access_token or (existing_access_token if can_preserve_sub2api_token else "")
                ):
                    return json_response(self, {"success": False, "message": "导入登录态时需要填写 auth_token"}, 400)
                fields.append("login_enabled = ?")
                params.append(
                    1
                    if (
                        login_enabled
                        or target_platform == "sub2api"
                        or auth_mode == BROWSER_AUTH_MODE
                        or (target_platform == "newapi" and auth_mode == "password")
                    )
                    else 0
                )
                fields.append("auth_mode = ?")
                params.append(auth_mode)
                if target_platform == "sub2api":
                    if auth_mode == "password" and (login_username or not can_preserve_sub2api_password):
                        fields.append("login_username = ?")
                        params.append(login_username)
                    if auth_mode == "password" and (login_password or not can_preserve_sub2api_password):
                        fields.append("login_password = ?")
                        params.append(login_password)
                    if auth_mode == "token":
                        fields.append("login_username = ?")
                        params.append("")
                        fields.append("login_password = ?")
                        params.append("")
                        if access_token or not can_preserve_sub2api_token:
                            fields.append("access_token = ?")
                            params.append(access_token)
                        if refresh_token or not can_preserve_sub2api_token or not existing_refresh_token:
                            fields.append("refresh_token = ?")
                            params.append(refresh_token)
                        fields.append("token_expires_at = ?")
                        params.append(token_expires_at)
                    elif auth_mode == BROWSER_AUTH_MODE:
                        if not same_platform or login_username:
                            fields.append("login_username = ?")
                            params.append(login_username)
                        if not same_platform or login_password:
                            fields.append("login_password = ?")
                            params.append(login_password)
                        if not same_platform or access_token:
                            fields.append("access_token = ?")
                            params.append(access_token)
                        if not same_platform or refresh_token:
                            fields.append("refresh_token = ?")
                            params.append(refresh_token)
                        if not same_platform or token_expires_at:
                            fields.append("token_expires_at = ?")
                            params.append(token_expires_at)
                    else:
                        fields.append("access_token = ?")
                        params.append("")
                        fields.append("refresh_token = ?")
                        params.append("")
                        fields.append("token_expires_at = ?")
                        params.append("")
                    fields.append("access_user_id = ?")
                    params.append("")
                    if existing_platform == "newapi":
                        fields.append("browser_cookie = ?")
                        params.append(None)
                        fields.append("browser_refresh_cookie = ?")
                        params.append(None)
                        fields.append("browser_session_id = ?")
                        params.append(None)
                        fields.append("browser_access_expires_at = ?")
                        params.append(0)
                        fields.append("session_sync_status = ?")
                        params.append("not_requested")
                        fields.append("session_sync_error = ?")
                        params.append(None)
                        fields.append("session_synced_at = ?")
                        params.append(None)
                else:
                    fields.append("refresh_token = ?")
                    params.append("")
                    fields.append("token_expires_at = ?")
                    params.append("")
                    if auth_mode == "password":
                        if login_username or not can_preserve_newapi_password:
                            fields.append("login_username = ?")
                            params.append(login_username)
                        if login_password or not can_preserve_newapi_password:
                            fields.append("login_password = ?")
                            params.append(login_password)
                        if not same_auth_mode or login_username or login_password:
                            fields.append("access_token = ?")
                            params.append("")
                            fields.append("access_user_id = ?")
                            params.append("")
                            fields.append("browser_cookie = ?")
                            params.append(None)
                            fields.append("browser_refresh_cookie = ?")
                            params.append(None)
                            fields.append("browser_session_id = ?")
                            params.append(None)
                            fields.append("browser_access_expires_at = ?")
                            params.append(0)
                    elif auth_mode == BROWSER_AUTH_MODE:
                        fields.append("login_username = ?")
                        params.append("")
                        fields.append("login_password = ?")
                        params.append("")
                        if not same_auth_mode:
                            fields.append("access_token = ?")
                            params.append("")
                            fields.append("access_user_id = ?")
                            params.append("")
                            fields.append("browser_refresh_cookie = ?")
                            params.append(None)
                            fields.append("browser_session_id = ?")
                            params.append(None)
                            fields.append("browser_access_expires_at = ?")
                            params.append(0)
                    else:
                        fields.append("login_username = ?")
                        params.append("")
                        fields.append("login_password = ?")
                        params.append("")
                        if not login_enabled:
                            fields.append("access_token = ?")
                            params.append("")
                            fields.append("access_user_id = ?")
                            params.append("")
                        else:
                            if access_token or not can_preserve_newapi_auth:
                                fields.append("access_token = ?")
                                params.append(access_token)
                            if access_user_id or not can_preserve_newapi_auth:
                                fields.append("access_user_id = ?")
                                params.append(access_user_id)
                        fields.append("browser_cookie = ?")
                        params.append(None)
                        fields.append("browser_refresh_cookie = ?")
                        params.append(None)
                        fields.append("browser_session_id = ?")
                        params.append(None)
                        fields.append("browser_access_expires_at = ?")
                        params.append(0)
                    if not same_auth_mode or (
                        auth_mode == "password" and (login_username or login_password)
                    ):
                        fields.append("session_sync_status = ?")
                        params.append("not_requested")
                        fields.append("session_sync_error = ?")
                        params.append(None)
                        fields.append("session_synced_at = ?")
                        params.append(None)
            if "status" in body:
                fields.append("status = ?")
                params.append(str(body["status"]))

            if not fields:
                return json_response(self, {"success": False, "message": "no fields"}, 400)

            fields.append("updated_at = ?")
            params.append(utc_now_iso())
            params.append(site_id)

            db_execute(f"UPDATE sites SET {', '.join(fields)} WHERE id = ?", params)
            invalidate_site_model_cache(site_id)
            schedule_model_cache_refresh(site_id)
            return json_response(self, {"success": True})

        if path == "/api/settings":
            body = read_json_body(self)
            body = body if isinstance(body, dict) else {}
            mode = str(body.get(SETTING_RECONCILE_MODE) or "").strip().lower()
            if mode not in RECONCILE_MODES:
                return json_response(
                    self,
                    {"success": False, "message": "reconcile mode 无效"},
                    400,
                )
            set_app_setting(SETTING_RECONCILE_MODE, mode)
            return json_response(
                self,
                {
                    "success": True,
                    "data": {SETTING_RECONCILE_MODE: get_main_site_reconcile_mode()},
                },
            )

        if path == "/api/notifications/settings":
            body = read_json_body(self)
            try:
                update_notification_settings(body)
            except ValueError as exc:
                return json_response(self, {"success": False, "message": str(exc)}, 400)
            return json_response(self, {"success": True, "data": notification_settings_payload()})

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if self._auth_guard(path):
            return
        # DELETE /api/admin/sites/:id                 删除管理站点
        # DELETE /api/admin/sites/:id/channels/:cid   删除渠道
        if path.startswith("/api/admin/sites/"):
            parts = [p for p in path.split("/") if p]
            try:
                admin_site_id = int(parts[3])
            except Exception:
                return json_response(self, {"success": False, "message": "invalid path"}, 400)
            if len(parts) == 4:
                db_execute("DELETE FROM channel_upstream_bindings WHERE admin_site_id = ?", (admin_site_id,))
                db_execute("DELETE FROM admin_channel_keys WHERE admin_site_id = ?", (admin_site_id,))
                db_execute("DELETE FROM admin_sites WHERE id = ?", (admin_site_id,))
                return json_response(self, {"success": True})
            site, err, code = get_admin_site_or_404(admin_site_id)
            if err:
                return json_response(self, err, code)
            if len(parts) == 6 and parts[4] == "channels":
                if admin_site_platform(site) == "sub2api":
                    return json_response(
                        self,
                        {
                            "success": False,
                            "message": "sub2api 主站不允许在本系统删除渠道",
                        },
                        405,
                    )
                try:
                    channel_id = int(parts[5])
                except Exception:
                    return json_response(self, {"success": False, "message": "invalid channel id"}, 400)
                ok, payload, error = delete_newapi_channel(site, channel_id)
                if not ok:
                    return json_response(self, {"success": False, "message": error, "upstream": payload}, 502)
                db_execute(
                    "DELETE FROM channel_upstream_bindings WHERE admin_site_id = ? AND channel_id = ?",
                    (admin_site_id, channel_id),
                )
                clear_admin_channel_key(admin_site_id, channel_id)
                return json_response(self, payload)
            return json_response(self, {"success": False, "message": "not found"}, 404)

        if path.startswith("/api/sites/"):
            try:
                site_id = int(path.split("/")[3])
            except Exception:
                return json_response(self, {"success": False, "message": "invalid site id"}, 400)
            db_execute("DELETE FROM sites WHERE id = ?", (site_id,))
            invalidate_site_model_cache(site_id)
            return json_response(self, {"success": True})
        self.send_error(HTTPStatus.NOT_FOUND)


def bootstrap_demo_data() -> None:
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


def main() -> None:
    ensure_dirs()
    wait_for_db()
    init_db()
    # 示例站点只在显式开启时播种，避免生产/开源首次启动就多出一个连不上的 Demo 站点，
    # 触发调度器每 interval 反复失败、写入一堆 failed 快照。设 SEED_DEMO=1 可保留旧行为。
    if (os.getenv("SEED_DEMO") or "").strip().lower() in ("1", "true", "yes"):
        bootstrap_demo_data()

    worker = threading.Thread(target=schedule_worker, daemon=True)
    worker.start()
    warm_model_cache()

    server = ThreadingHTTPServer((SERVER_HOST, SERVER_PORT), Handler)
    ui = "apps/web/dist" if WEB_DIST_DIR.exists() else "static/"
    print(f"Upstream Ratio Watch running at http://{SERVER_HOST}:{SERVER_PORT} (ui={ui})")
    if console_auth_enabled():
        print("控制台鉴权：已启用（CONSOLE_PASSWORD 已设置）")
    else:
        print(
            "控制台鉴权：未启用（未设置 CONSOLE_PASSWORD）。"
            f"当前监听 {SERVER_HOST}；若对外网/公网暴露，请设置 CONSOLE_PASSWORD 或加反代/IP 白名单，"
            "否则任何人可读取上游密钥并增删渠道。"
        )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        STOP_EVENT.set()
        server.server_close()
        close_database_pool()


if __name__ == "__main__":
    main()
