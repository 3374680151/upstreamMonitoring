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

from backend.core.time import (
    APP_TIMEZONE,
    APP_TIMEZONE_NAME,
    app_now,
    fmt_local_time_for_message,
    next_check_iso,
    parse_iso_dt,
    stable_hash,
    utc_now_iso,
)

from backend.core.normalize import (
    SYNC_SECRET_FIELD_NAMES,
    _admin_site_origin,
    _channel_key_is_masked,
    _cookie_header_from_response,
    _normalize_discovery_base_url,
    _positive_channel_id,
    _safe_discovery_display_url,
    _sync_safe_value,
    _url_origin,
    clamp_perf_hours,
    format_change_value,
    mask_channel_in_place,
    mask_channel_key,
    mask_newapi_user_token_key,
    normalize_base_url,
    normalize_newapi_user_token_key,
    normalize_session_expiry,
    platform_label,
    ratio_direction,
    ratio_number,
    site_origin,
    split_channel_groups,
)

from backend.core.state import (
    ADMIN_BROWSER_SESSION_LOCKS,
    ADMIN_BROWSER_SESSION_LOCKS_GUARD,
    ADMIN_KEY_SYNC_PROOF_BATCH_SIZE,
    ADMIN_SUB2API_EXPIRY_SKEW_SECONDS,
    ADMIN_SUB2API_SESSION_LOCKS,
    ADMIN_SUB2API_SESSION_LOCKS_GUARD,
    BROWSER_AUTH_MODE,
    CONSOLE_SESSIONS,
    CONSOLE_SESSIONS_LOCK,
    DB_LOCK,
    MAIN_CHANNEL_KEY_CACHE,
    MAIN_CHANNEL_KEY_CACHE_TTL_SECONDS,
    MAIN_CHANNEL_KEY_LAST_REQUEST_AT,
    MAIN_CHANNEL_KEY_MIN_INTERVAL_SECONDS,
    MAIN_CHANNEL_KEY_RATE_LIMIT_COOLDOWN_SECONDS,
    MAIN_CHANNEL_KEY_RATE_LIMIT_UNTIL,
    MAIN_CHANNEL_KEY_REQUEST_LOCK,
    MAX_DISCOVERY_CHANNEL_IDS_PER_ITEM,
    MAX_DISCOVERY_IMPORT_ITEMS,
    MAX_DISCOVERY_INTERVAL_MINUTES,
    MODEL_CACHE_LOCK,
    MODEL_CACHE_REFRESHING,
    MODEL_CACHE_TTL_SECONDS,
    MODEL_DATA_CACHE,
    NEWAPI_SITE_BROWSER_SESSION_LOCKS,
    NEWAPI_SITE_BROWSER_SESSION_LOCKS_GUARD,
    NEWAPI_UPTIME_CACHE,
    NEWAPI_UPTIME_REFRESHING,
    NEWAPI_USER_TOKEN_LIST_CACHE,
    NEWAPI_USER_TOKEN_LIST_CACHE_TTL_SECONDS,
    NEWAPI_USER_TOKEN_LIST_LOCK,
    SESSION_SYNC_MAX_BODY_BYTES,
    SESSION_SYNC_MAX_TOKEN_LENGTH,
    SESSION_SYNC_PAGE_FAILURES,
    SESSION_SYNC_REQUEST_LOCK,
    SESSION_SYNC_TERMINAL_STATUSES,
    SESSION_SYNC_TTL_SECONDS,
    STOP_EVENT,
    SUB2API_REFRESH_CACHE,
    SUB2API_REFRESH_CACHE_TTL_SECONDS,
    SUB2API_REFRESH_LOCKS,
    SUB2API_REFRESH_LOCKS_GUARD,
    SUB2API_SITE_AUTH_LOCKS,
    SUB2API_SITE_AUTH_LOCKS_GUARD,
    UPTIME_CACHE_TTL_SECONDS,
)


# This module is kept as a compatibility domain runtime while the FastAPI
# boundary is migrated.  The configuration / error / security primitives now
# live in backend.core.{config,errors,security}; they are re-exported below so
# existing ``legacy.*`` callers keep working unchanged.
from backend.core.config import (
    APP_DIR,
    CONSOLE_PASSWORD,
    CONSOLE_SESSION_TTL_SECONDS,
    DB_CONFIG,
    DB_CONNECT_TIMEOUT_SECONDS,
    DB_PATH,
    DB_POOL_ACQUIRE_TIMEOUT_SECONDS,
    DB_POOL_SIZE,
    DB_READ_TIMEOUT_SECONDS,
    DB_WRITE_TIMEOUT_SECONDS,
    DATA_DIR,
    DEFAULT_INTERVAL_MINUTES,
    HTTP_TIMEOUT_SECONDS,
    MIN_INTERVAL_MINUTES,
    SCAN_INTERVAL_SECONDS,
    SERVER_HOST,
    SERVER_PORT,
    SLOW_REQUEST_THRESHOLD_MS,
    STATIC_DIR,
    WEB_DIST_DIR,
    _env_int,
    _load_dotenv,
)
from backend.core.errors import DatabasePoolTimeoutError
from backend.core.security import (
    PUBLIC_API_PATHS,
    console_auth_enabled,
    console_authenticated,
    console_session_valid,
    create_console_session,
    drop_console_session,
    hash_session_sync_secret,
    is_public_api_path,
    request_bearer_token,
)

# 配置模块在导入时已读取过 .env；这里幂等地再读取一次，保持旧行为。
_load_dotenv()

# ---------------------------------------------------------------------------
# Repository re-exports.
#
# The data-access functions below have been moved into ``backend.repositories``.
# They are re-exported here so every existing ``legacy.<fn>`` caller (and the
# ``from app import diff_groups`` test shim, since ``app`` is aliased to this
# module) keeps working without modification.
# ---------------------------------------------------------------------------
from backend.repositories.sites import (  # noqa: E402
    create_site,
    delete_site,
    find_monitor_site_for_channel,
    get_site_or_404,
    list_sites_payload,
    normalize_admin_sync_channels,
    normalize_admin_sync_groups,
    site_groups_from_row,
    site_summary,
    update_site,
)
from backend.repositories.admin_sites import (  # noqa: E402
    ADMIN_SITE_CAPABILITIES,
    admin_site_capabilities,
    admin_site_platform,
    clear_admin_channel_key,
    create_admin_site,
    get_admin_site_or_404,
    get_cached_admin_channel_key,
    is_admin_site_row,
    list_admin_sites_payload,
    persist_admin_channel_key,
    sync_admin_channel_key,
    update_admin_site,
    validate_admin_site_base_url,
)
from backend.repositories.changes import (  # noqa: E402
    diff_groups,
    get_last_success_snapshot,
    list_changes,
    list_site_changes,
    list_snapshots,
    persist_snapshot,
)
from backend.repositories.notifications import (  # noqa: E402
    get_notification_settings,
    log_notification,
    notification_settings_payload,
    update_notification_settings,
)


# The connection pool, query helpers, and schema/migration bootstrap now live in
# ``backend.db.connection`` and ``backend.db.schema``.  Re-export them here so the
# existing ``legacy.<name>`` call sites keep working during the FastAPI migration.
from backend.db.connection import (  # noqa: E402
    DB_POOL,
    DatabaseConnectionPool,
    _existing_columns,
    _q,
    close_database_pool,
    connect_db,
    db_connection,
    db_execute,
    db_execute_rowcount,
    db_query_all,
    db_query_one,
    dict_from_row,
)
from backend.integrations.http import (  # noqa: E402
    SameOriginAdminRedirectHandler,
    _CurlResponse,
    _admin_browser_refresh_error,
    _curl_config_value,
    _curl_headers_from_dump,
    _is_connection_reset_by_peer,
    _open_upstream_url_with_curl,
    _upstream_response_details,
    _upstream_response_message,
    admin_request_json,
    channel_admin_error_message,
    json_request,
    newapi_auth_failure_message,
    open_upstream_url,
    request_json,
    request_json_with_headers,
)
from backend.integrations.email import send_email_message  # noqa: E402
from backend.integrations.wecom import send_wecom_message  # noqa: E402
from backend.db.schema import (  # noqa: E402
    ADMIN_SITE_COLUMN_ADDITIONS,
    DDL_STATEMENTS,
    NOTIFICATION_COLUMN_ADDITIONS,
    SUB2API_BROWSER_FIRST_MIGRATION,
    SITES_COLUMN_ADDITIONS,
    ensure_dirs,
    init_db,
    migrate_sub2api_sites_to_browser_first,
    run_sub2api_browser_first_migration_once,
    wait_for_db,
)


# --- Reconcile-mode constants (defined early so service modules can import
# them from legacy_runtime without a circular import) ---
RECONCILE_MODE_DISABLE = "disable"
RECONCILE_MODE_DELETE = "delete"
RECONCILE_MODES = {RECONCILE_MODE_DISABLE, RECONCILE_MODE_DELETE}
SETTING_RECONCILE_MODE = "main_site_reconcile_mode"


# --- session sync moved to backend.services.session_sync_service ----
# The browser-session sync request lifecycle and sub2api persistence
# primitives now live in backend.services.session_sync_service; they are
# re-exported here so existing legacy_runtime callers keep working.
from backend.services.session_sync_service import (  # noqa: F401  (re-export)
    SUB2API_SESSION_SYNC_FIELDS,
    _create_session_sync_request,
    _session_sync_public_payload,
    _session_sync_request_expired,
    _site_session_sync_request_error,
    claim_session_sync_request,
    complete_session_sync_request,
    create_site_session_sync_request,
    fail_site_session_sync_request,
    finish_session_sync_request,
    get_site_session_sync_request,
    mark_site_browser_session_expired,
    persist_site_browser_session,
    session_sync_target_kind,
)


# --- sub2api integration moved to backend.integrations.sub2api -------
# The protocol-specific sub2api helpers now live in backend.integrations.sub2api;
# they are re-exported here so existing legacy_runtime callers keep working.
from backend.integrations.sub2api import (  # noqa: F401  (re-export)
    SUB2API_ADMIN_CHANNEL_UPDATE_FIELDS,
    SUB2API_AUTH_ERROR_CODES,
    SUB2API_AUTH_ERROR_MESSAGES,
    SUB2API_BILLING_MODEL_SOURCES,
    Sub2ApiUpstreamError,
    _SUB2API_AUTH_CONTEXT_KEYS,
    _admin_sub2api_session_lock,
    _attach_sub2api_auth_context,
    _fetch_sub2api_with_auth_fallback,
    _persist_sub2api_admin_auth,
    _persist_sub2api_admin_error,
    _sanitize_sub2api_error_text,
    _strip_sub2api_auth_context,
    _sub2api_admin_channel_page,
    _sub2api_browser_session_required,
    _sub2api_group_ids,
    _sub2api_login_auth,
    _sub2api_refresh_lock,
    _sub2api_site_auth_lock,
    apply_sub2api_browser_session,
    classify_sub2api_auth_failure,
    ensure_sub2api_admin_session,
    fetch_sub2api_account,
    fetch_sub2api_account_by_token,
    fetch_sub2api_admin_channel_detail,
    fetch_sub2api_admin_channels_by_token,
    fetch_sub2api_admin_groups,
    fetch_sub2api_admin_site_channels,
    fetch_sub2api_channel_models_by_token,
    fetch_sub2api_channel_monitors_by_token,
    fetch_sub2api_groups_by_token,
    fetch_sub2api_keys,
    fetch_sub2api_keys_by_token,
    fetch_sub2api_model_data,
    fetch_sub2api_model_data_by_token,
    fetch_sub2api_user_groups,
    is_sub2api_auth_error,
    merge_sub2api_group_models,
    normalize_sub2api_account,
    normalize_sub2api_admin_channel,
    parse_sub2api_channel_models,
    parse_sub2api_groups,
    parse_sub2api_monitor_models,
    persist_sub2api_refreshed_auth,
    probe_sub2api_groups,
    refresh_sub2api_auth,
    sub2api_admin_groups_payload,
    sub2api_admin_login,
    sub2api_admin_refresh_token,
    sub2api_admin_request,
    sub2api_key_group_name,
    sub2api_login,
    sub2api_proxy_error_response,
    sub2api_refresh_token,
    sub2api_token_headers,
    unwrap_sub2api_response,
    update_sub2api_admin_channel,
    validate_sub2api_admin_channel_patch,
    validate_sub2api_browser_session,
)

# Re-export NewAPI integration helpers moved to backend.integrations.newapi
from backend.integrations.newapi import (  # noqa: E402,F401
    aggregate_newapi_channel_candidates,
    enrich_channel_candidates_with_sites,
    _newapi_session_sync_request_error,
    persist_newapi_site_browser_session_cas,
    mark_newapi_site_browser_session_expired_cas,
    _newapi_session_payload_error,
    parse_groups_payload,
    parse_newapi_models_by_group,
    fetch_newapi_groups,
    refresh_newapi_uptime_cache,
    get_cached_newapi_uptime,
    _newapi_uptime_cache_key,
    refresh_newapi_uptime_cache_for_site,
    get_cached_newapi_uptime_for_site,
    newapi_auth_headers,
    site_newapi_headers,
    fetch_newapi_pricing,
    fetch_newapi_perf_summary,
    fetch_newapi_perf_detail,
    fetch_newapi_pricing_for_site,
    fetch_newapi_perf_summary_for_site,
    fetch_newapi_perf_detail_for_site,
    newapi_admin_target,
    _newapi_channel_list_items,
    fetch_newapi_channels,
    fetch_newapi_channel_detail,
    site_newapi_channel_key_headers,
    fetch_newapi_channel_key,
    create_newapi_channel,
    resolve_created_newapi_channel_id,
    update_newapi_channel,
    set_newapi_channel_status,
    delete_newapi_channel,
    batch_channel_operation,
    test_newapi_channel,
    _newapi_user_token_items,
    fetch_all_newapi_user_tokens,
    fetch_newapi_user_token_key,
    _newapi_token_cache_key,
    find_newapi_user_token_by_key,
    fetch_all_newapi_channels,
    fetch_newapi_admin_groups,
    fetch_newapi_model_data,
    fetch_newapi_account,
    fetch_newapi_account_with_headers,
    fetch_newapi_groups_with_headers,
    newapi_site_browser_auth_headers,
    _newapi_status_from_payload,
    newapi_browser_request,
    validate_newapi_site_browser_session,
    persist_newapi_site_browser_session,
    _newapi_site_browser_session_lock,
    _newapi_refresh_cookie_from_response,
    _newapi_site_browser_auth_data,
    _newapi_password_login_bundle,
    login_newapi_site_with_password,
    probe_newapi_password_login,
    refresh_newapi_site_browser_session,
    ensure_newapi_site_browser_session,
    fetch_newapi_account_for_site,
    fetch_newapi_groups_for_site,
    normalize_newapi_account,
    fetch_newapi_groups_with_access_token,
    probe_newapi_groups,
    NEWAPI_SESSION_SYNC_FIELDS,
    BATCH_CHANNEL_ACTIONS,
    NEWAPI_QUOTA_PER_UNIT,
)


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



# ---------------------------------------------------------------------------
# Admin-site service + channel-match service re-exports.
#
# The functions below have been moved into ``backend.services.admin_site_service``
# and ``backend.services.channel_match_service``.  They are re-exported lazily
# through ``_REEXPORT_MAP`` / ``__getattr__`` (see below) so that importing this
# module does not eagerly load the service modules, which would create a circular
# import: service modules import from ``backend.integrations.newapi``, whose
# ``__getattr__`` falls back to this module.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Lazy re-exports for functions moved to service modules.
#
# These names used to be top-level ``from … import`` re-exports, but that
# caused a circular import: the service modules import from
# ``backend.integrations.newapi``, whose ``__getattr__`` falls back to this
# module, which would try to re-import from a still-loading service module.
# Resolving lazily through ``__getattr__`` (PEP 562) breaks the cycle.
# ---------------------------------------------------------------------------
_REEXPORT_MAP = {
    # discovery_service
    "_discovery_channel_ids": "backend.services.discovery_service",
    "_discovery_existing_site": "backend.services.discovery_service",
    "_discovery_public_item": "backend.services.discovery_service",
    "_import_discovered_site_item": "backend.services.discovery_service",
    "_live_channel_urls_from_candidates": "backend.services.discovery_service",
    "_reconcile_site_discovery_links_in_connection": "backend.services.discovery_service",
    "import_discovered_sites": "backend.services.discovery_service",
    "list_site_discovery_links": "backend.services.discovery_service",
    "reconcile_site_discovery_links": "backend.services.discovery_service",
    # monitoring_service
    "attach_group_model_names": "backend.services.monitoring_service",
    "build_site_account_payload": "backend.services.monitoring_service",
    "build_site_models_payload": "backend.services.monitoring_service",
    "cache_site_model_payload": "backend.services.monitoring_service",
    "detect_site": "backend.services.monitoring_service",
    "get_app_setting": "backend.services.monitoring_service",
    "get_main_site_reconcile_mode": "backend.services.monitoring_service",
    "get_site_model_cache": "backend.services.monitoring_service",
    "invalidate_site_model_cache": "backend.services.monitoring_service",
    "model_cache_refresh_worker": "backend.services.monitoring_service",
    "overview_payload": "backend.services.monitoring_service",
    "refresh_site_model_cache": "backend.services.monitoring_service",
    "schedule_model_cache_refresh": "backend.services.monitoring_service",
    "set_app_setting": "backend.services.monitoring_service",
    "warm_model_cache": "backend.services.monitoring_service",
    # notification_service
    "format_change_notification": "backend.services.notification_service",
    "format_change_subject": "backend.services.notification_service",
    "notify_changes": "backend.services.notification_service",
    "percent_text": "backend.services.notification_service",
    # admin_site_service
    "_admin_browser_auth_data": "backend.services.admin_site_service",
    "_admin_browser_auth_headers": "backend.services.admin_site_service",
    "_admin_browser_session_lock": "backend.services.admin_site_service",
    "_persist_admin_browser_auth": "backend.services.admin_site_service",
    "_persist_admin_browser_login_error": "backend.services.admin_site_service",
    "ensure_admin_site_browser_session": "backend.services.admin_site_service",
    "fetch_admin_site_channel_detail": "backend.services.admin_site_service",
    "fetch_admin_site_channels": "backend.services.admin_site_service",
    "fetch_admin_site_groups": "backend.services.admin_site_service",
    "refresh_admin_site_browser_session": "backend.services.admin_site_service",
    "test_admin_site_connection": "backend.services.admin_site_service",
    "update_admin_site_channel": "backend.services.admin_site_service",
    "verify_admin_site_channel_key_access": "backend.services.admin_site_service",
    # channel_match_service
    "CHANNEL_MATCH_STALE_STATUSES": "backend.services.channel_match_service",
    "channel_upstream_binding_payload": "backend.services.channel_match_service",
    "get_channel_upstream_binding": "backend.services.channel_match_service",
    "list_channel_upstream_bindings": "backend.services.channel_match_service",
    "mark_channel_upstream_match_failure": "backend.services.channel_match_service",
    "match_channel_upstream_binding": "backend.services.channel_match_service",
    "persist_channel_match": "backend.services.channel_match_service",
    "save_channel_upstream_binding": "backend.services.channel_match_service",
}


def __getattr__(name):
    module_path = _REEXPORT_MAP.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    mod = importlib.import_module(module_path)
    value = getattr(mod, name)
    globals()[name] = value  # cache for subsequent lookups
    return value



def schedule_worker(*args, **kwargs):
    """Re-exported from backend.workers.scheduler (moved out of legacy_runtime)."""
    from backend.workers.scheduler import schedule_worker as _impl
    return _impl(*args, **kwargs)


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


def record_admin_site_sync_error(*args, **kwargs):
    """Re-exported from backend.services.sync_service (moved out of legacy_runtime)."""
    from backend.services.sync_service import record_admin_site_sync_error as _impl
    return _impl(*args, **kwargs)


def _delete_stale_admin_channel_data_in_connection(*args, **kwargs):
    """Re-exported from backend.services.sync_service (moved out of legacy_runtime)."""
    from backend.services.sync_service import _delete_stale_admin_channel_data_in_connection as _impl
    return _impl(*args, **kwargs)


def _apply_admin_site_channel_reconcile_in_connection(*args, **kwargs):
    """Re-exported from backend.services.sync_service (moved out of legacy_runtime)."""
    from backend.services.sync_service import _apply_admin_site_channel_reconcile_in_connection as _impl
    return _impl(*args, **kwargs)


def _sync_admin_site_snapshot_in_connection(*args, **kwargs):
    """Re-exported from backend.services.sync_service (moved out of legacy_runtime)."""
    from backend.services.sync_service import _sync_admin_site_snapshot_in_connection as _impl
    return _impl(*args, **kwargs)


def refresh_next_admin_site_channel_key(*args, **kwargs):
    """Re-exported from backend.services.sync_service (moved out of legacy_runtime)."""
    from backend.services.sync_service import refresh_next_admin_site_channel_key as _impl
    return _impl(*args, **kwargs)


def run_due_admin_key_syncs(*args, **kwargs):
    """Re-exported from backend.services.sync_service (moved out of legacy_runtime)."""
    from backend.services.sync_service import run_due_admin_key_syncs as _impl
    return _impl(*args, **kwargs)


def _sync_one_admin_site(*args, **kwargs):
    """Re-exported from backend.services.sync_service (moved out of legacy_runtime)."""
    from backend.services.sync_service import _sync_one_admin_site as _impl
    return _impl(*args, **kwargs)


def _run_admin_site_sync(*args, **kwargs):
    """Re-exported from backend.services.sync_service (moved out of legacy_runtime)."""
    from backend.services.sync_service import _run_admin_site_sync as _impl
    return _impl(*args, **kwargs)


def auto_sync_admin_site_channels_to_sites(*args, **kwargs):
    """Re-exported from backend.services.sync_service (moved out of legacy_runtime)."""
    from backend.services.sync_service import auto_sync_admin_site_channels_to_sites as _impl
    return _impl(*args, **kwargs)


def persist_channel_binding_refreshed_auth(*args, **kwargs):
    """Re-exported from backend.services.sync_service (moved out of legacy_runtime)."""
    from backend.services.sync_service import persist_channel_binding_refreshed_auth as _impl
    return _impl(*args, **kwargs)


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
                ok, site_id, error, existed = create_site(body)
                if not ok:
                    return json_response(self, {"success": False, "message": error}, 400)
                response: Dict[str, Any] = {"success": True, "id": site_id}
                if existed:
                    response["existed"] = True
                return json_response(self, response)

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
                if (
                    len(parts) == 8
                    and parts[4] == "channels"
                    and parts[6] == "key"
                    and parts[7] == "refresh"
                ):
                    if admin_site_platform(site) != "newapi":
                        return json_response(
                            self,
                            {"success": False, "message": "仅 NewAPI 主站支持刷新渠道 key"},
                            405,
                        )
                    try:
                        channel_id = int(parts[5])
                    except Exception:
                        return json_response(
                            self, {"success": False, "message": "invalid channel id"}, 400
                        )
                    previous_key = get_cached_admin_channel_key(
                        admin_site_id, channel_id
                    )
                    key_ok, channel_key, key_error = fetch_newapi_channel_key(
                        site, channel_id, force_refresh=True
                    )
                    if not key_ok:
                        message = key_error or "读取渠道真实 key 失败"
                        status = 429 if "429" in message or "限流" in message else 400
                        return json_response(
                            self,
                            {
                                "success": False,
                                "code": (
                                    "rate_limited"
                                    if status == 429
                                    else "security_verification_required"
                                    if any(marker in message for marker in ("安全验证", "2FA", "proof"))
                                    else "key_refresh_failed"
                                ),
                                "message": message,
                            },
                            status,
                        )
                    changed = channel_key != previous_key
                    match_ok, match_payload, match_error = (
                        match_channel_upstream_binding(
                            site, channel_id, force_refresh=False
                        )
                    )
                    binding_row = get_channel_upstream_binding(
                        admin_site_id, channel_id
                    )
                    binding_payload = (
                        match_payload
                        if match_ok and isinstance(match_payload, dict)
                        else channel_upstream_binding_payload(binding_row)
                    )
                    match_status = str(binding_payload.get("match_status") or "")
                    match_success = match_ok and match_status in {
                        "matched",
                        "matched_partial",
                    }
                    match_message = (
                        match_error
                        or binding_payload.get("match_message")
                        or (None if match_success else "未匹配到上游分组倍率")
                    )
                    return json_response(
                        self,
                        {
                            "success": True,
                            "data": {
                                "channel_id": channel_id,
                                "changed": changed,
                                "first_fetch": not bool(previous_key),
                                "fetched_at": utc_now_iso(),
                                "match_success": match_success,
                                "match_message": match_message,
                                "binding": binding_payload,
                            },
                            "message": (
                                "渠道 key 已刷新，倍率已重新匹配"
                                if match_success and changed
                                else "渠道 key 已是最新，倍率已刷新"
                                if match_success
                                else "渠道 key 已保存，但倍率刷新失败"
                            ),
                        },
                    )
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
            ok, error = update_site(site_id, body)
            if not ok:
                return json_response(self, {"success": False, "message": error}, 400)
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
            delete_site(site_id)
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
