"""Admin-site repository: data-access for ``admin_sites`` and the channel-key cache.

Functions moved out of ``backend.legacy_runtime``.  The legacy runtime
re-exports every public name below so existing ``legacy.*`` callers keep
working unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from backend.core.normalize import _channel_key_is_masked, normalize_base_url
from backend.core.time import utc_now_iso
from backend.db.connection import db_execute, db_query_all, db_query_one


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
            "security_proof_verified_at": r.get("security_proof_verified_at"),
            "key_sync_enabled": bool(r.get("key_sync_enabled")),
            "key_sync_interval_minutes": int(r.get("key_sync_interval_minutes") or 5),
            "key_sync_last_at": r.get("key_sync_last_at"),
            "key_sync_next_at": r.get("key_sync_next_at"),
            "key_sync_last_error": r.get("key_sync_last_error"),
            "key_sync_backoff_until": r.get("key_sync_backoff_until"),
            "key_sync_failure_count": int(r.get("key_sync_failure_count") or 0),
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
    key_sync_enabled = 1 if body.get("key_sync_enabled") and platform == "newapi" else 0
    try:
        key_sync_interval = max(5, min(1440, int(body.get("key_sync_interval_minutes") or 5)))
    except (TypeError, ValueError):
        return False, None, "key 自动更新间隔无效"
    if not name or not base_url:
        return False, None, "请填写管理站点名称和 Base URL"
    if platform == "newapi" and (not access_token or not access_user_id):
        return False, None, "请填写管理员系统访问令牌和 NewAPI 用户 ID"
    auth: Dict[str, Any] = {}
    if platform == "sub2api":
        if not login_username or not login_password:
            return False, None, "请填写 sub2api 管理员邮箱和密码"
        from backend.integrations.sub2api import sub2api_admin_login, Sub2ApiUpstreamError

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
            key_sync_enabled, key_sync_interval_minutes, key_sync_next_at,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
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
            key_sync_enabled,
            key_sync_interval,
            now if key_sync_enabled else None,
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
    if "key_sync_enabled" in body or "key_sync_interval_minutes" in body:
        enabled = (
            1 if body.get("key_sync_enabled", existing.get("key_sync_enabled")) and platform == "newapi" else 0
        )
        try:
            interval = max(
                5,
                min(
                    1440,
                    int(body.get("key_sync_interval_minutes") or existing.get("key_sync_interval_minutes") or 5),
                ),
            )
        except (TypeError, ValueError):
            return False, "key 自动更新间隔无效"
        fields.extend([
            "key_sync_enabled = ?",
            "key_sync_interval_minutes = ?",
            "key_sync_next_at = ?",
            "key_sync_last_error = NULL",
            "key_sync_backoff_until = NULL",
            "key_sync_failure_count = 0",
        ])
        params.extend([enabled, interval, utc_now_iso() if enabled else None])
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
            from backend.integrations.sub2api import sub2api_admin_login, Sub2ApiUpstreamError

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
            "security_proof_verified_at = NULL",
        ])
    if not fields:
        return False, "没有要更新的字段"
    fields.append("updated_at = ?")
    params.append(utc_now_iso())
    params.append(admin_site_id)
    db_execute(f"UPDATE admin_sites SET {', '.join(fields)} WHERE id = ?", params)
    return True, None


class AdminSiteRepository:
    """Thin OO facade retained for callers that prefer object-style access."""

    def get(self, admin_site_id: int) -> Optional[Dict[str, Any]]:
        row, _error, _status = get_admin_site_or_404(admin_site_id)
        return row

    def list_payload(self) -> List[Dict[str, Any]]:
        return list_admin_sites_payload()
