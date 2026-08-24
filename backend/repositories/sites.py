"""Site repository: data-access functions moved out of ``legacy_runtime``.

This module owns all reads/writes against the ``sites`` table plus the small
normalization helpers used when syncing admin-site channel/group snapshots.
The legacy runtime re-exports every public name below for backward
compatibility.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from backend.core.normalize import (
    _positive_channel_id,
    _sync_safe_value,
    format_change_value,
    normalize_base_url,
    platform_label,
    ratio_direction,
    ratio_number,
    split_channel_groups,
)
from backend.core.time import app_now, parse_iso_dt, utc_now_iso
from backend.core.config import DEFAULT_INTERVAL_MINUTES, MIN_INTERVAL_MINUTES
from backend.core.state import BROWSER_AUTH_MODE
from backend.db.connection import (
    _q,
    db_connection,
    db_execute,
    db_execute_rowcount,
    db_query_all,
    db_query_one,
    dict_from_row,
)


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


def get_site_or_404(site_id: int) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], int]:
    site = db_query_one("SELECT * FROM sites WHERE id = ?", (site_id,))
    if not site:
        return None, {"success": False, "message": "site not found"}, 404
    return site, None, 200


def site_summary(
    site: Dict[str, Any],
    connection: Optional[Any] = None,
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


def site_groups_from_row(site: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    if not site.get("current_groups_json"):
        return {}
    try:
        groups = json.loads(site["current_groups_json"])
        return groups if isinstance(groups, dict) else {}
    except Exception:
        return {}


def list_sites_payload(
    with_auto_sync: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    # Main-site synchronization is an explicit action.  Ordinary site polling
    # must remain read-only so a 15-second UI refresh cannot repeat upstream
    # pagination or trigger reconciliation.
    auto_sync_results: List[Dict[str, Any]] = []
    if with_auto_sync:
        from backend import legacy_runtime as _legacy

        auto_sync_results = _legacy.auto_sync_admin_site_channels_to_sites()
    with db_connection() as connection:
        sites = db_query_all(
            "SELECT * FROM sites ORDER BY id DESC", connection=connection
        )
        summaries = [
            site_summary(site, connection=connection) for site in sites
        ]
    return summaries, auto_sync_results


def find_monitor_site_for_channel(base_url: str) -> Optional[Dict[str, Any]]:
    normalized = normalize_base_url(base_url)
    if not normalized:
        return None
    rows = db_query_all("SELECT * FROM sites WHERE enabled = 1 ORDER BY id DESC")
    for row in rows:
        if normalize_base_url(str(row.get("base_url") or "")) == normalized:
            return row
    return None


def create_site(body: Dict[str, Any]) -> Tuple[bool, Optional[int], Optional[str], bool]:
    """Create a monitored upstream site.

    Returns ``(ok, site_id, error_message, existed)``.  When a site with the
    same ``base_url`` already exists the duplicate is returned as a no-op with
    ``existed=True`` instead of surfacing the MySQL 1062 error.
    """
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
        return False, None, "platform invalid", False
    if auth_mode not in {"password", "token", BROWSER_AUTH_MODE}:
        return False, None, "auth_mode invalid", False
    if not name or not base_url:
        return False, None, "name/base_url required", False
    if (
        platform == "newapi"
        and auth_mode == "token"
        and login_enabled
        and (not access_token or not access_user_id)
    ):
        return False, None, "使用系统访问令牌时需要填写 NewAPI 用户 ID", False
    if platform == "newapi" and auth_mode == "password" and (
        not login_username or not login_password
    ):
        return False, None, "NewAPI 用户名密码模式需要填写用户名和密码", False
    if platform == "sub2api" and auth_mode == "password" and (not login_username or not login_password):
        return False, None, "sub2api 需要填写普通用户邮箱和密码", False
    if platform == "sub2api" and auth_mode == "token" and not access_token:
        return False, None, "导入登录态时需要填写 auth_token", False
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
                _next_check_iso(interval),
                now,
                now,
            ),
        )
        return True, site_id, None, False
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
                return True, int(existing["id"]), None, True
        raise


def _next_check_iso(interval: int) -> str:
    from backend.core.time import next_check_iso

    return next_check_iso(interval)


def update_site(site_id: int, body: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Patch a monitored site. Blank token/user-id fields preserve existing
    values so the edit form never has to re-enter credentials just to rename."""
    site = db_query_one("SELECT * FROM sites WHERE id = ?", (site_id,))
    if not site:
        return False, "site not found"
    fields: List[str] = []
    params: List[Any] = []

    if "name" in body:
        fields.append("name = ?")
        params.append(str(body["name"]).strip())
    if "base_url" in body:
        fields.append("base_url = ?")
        params.append(normalize_base_url(str(body["base_url"])))
    target_platform = str(body.get("platform") or site.get("platform") or "newapi").strip().lower()
    if target_platform not in {"newapi", "sub2api"}:
        return False, "platform invalid"
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
            return False, "auth_mode invalid"
        if target_platform == "newapi" and auth_mode == "token":
            has_token_after_update = bool(
                access_token or (existing_access_token if can_preserve_newapi_auth else "")
            )
            has_user_id_after_update = bool(
                access_user_id or (existing_access_user_id if can_preserve_newapi_auth else "")
            )
            if login_enabled and (not has_token_after_update or not has_user_id_after_update):
                return False, "使用系统访问令牌时需要填写 NewAPI 用户 ID"
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
            return False, "NewAPI 用户名密码模式需要填写用户名和密码"
        if target_platform == "sub2api" and auth_mode == "password" and (
            not (login_username or (existing_username if can_preserve_sub2api_password else ""))
            or not (login_password or (existing_password if can_preserve_sub2api_password else ""))
        ):
            return False, "sub2api 需要填写普通用户邮箱和密码"
        if target_platform == "sub2api" and auth_mode == "token" and not (
            access_token or (existing_access_token if can_preserve_sub2api_token else "")
        ):
            return False, "导入登录态时需要填写 auth_token"
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
        return False, "no fields"

    fields.append("updated_at = ?")
    params.append(utc_now_iso())
    params.append(site_id)

    db_execute(f"UPDATE sites SET {', '.join(fields)} WHERE id = ?", params)
    return True, None


def delete_site(site_id: int) -> int:
    return db_execute_rowcount("DELETE FROM sites WHERE id = ?", (site_id,))


class SiteRepository:
    """Thin OO facade retained for callers that prefer object-style access."""

    def get(self, site_id: int) -> Optional[Dict[str, Any]]:
        return db_query_one("SELECT * FROM sites WHERE id = ?", (site_id,))

    def list(self) -> List[Dict[str, Any]]:
        return db_query_all("SELECT * FROM sites ORDER BY id DESC")

    def delete(self, site_id: int) -> int:
        return delete_site(site_id)
