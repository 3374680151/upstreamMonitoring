"""Admin-site service.

Browser-session management, connection testing, channel-key verification,
and channel/group fetching for admin sites, moved out of
``backend.legacy_runtime``.  The legacy runtime re-exports every name below
for backward compatibility.
"""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from backend.core.config import HTTP_TIMEOUT_SECONDS
from backend.core.normalize import (
    _admin_site_origin,
    _cookie_header_from_response,
    normalize_base_url,
)
from backend.core.state import (
    ADMIN_BROWSER_SESSION_LOCKS,
    ADMIN_BROWSER_SESSION_LOCKS_GUARD,
    ADMIN_SUB2API_SESSION_LOCKS,
    ADMIN_SUB2API_SESSION_LOCKS_GUARD,
)
from backend.core.time import app_now, utc_now_iso
from backend.db.connection import (
    _q,
    db_connection,
    db_execute,
    db_query_all,
    db_query_one,
)
from backend.integrations.http import (
    _admin_browser_refresh_error,
    _upstream_response_message,
    admin_request_json,
    channel_admin_error_message,
    request_json,
    request_json_with_headers,
)
from backend.integrations.newapi import (
    _newapi_channel_list_items,
    _newapi_password_login_bundle,
    ensure_newapi_site_browser_session,
    fetch_all_newapi_channels,
    fetch_newapi_admin_groups,
    fetch_newapi_channel_detail,
    fetch_newapi_channel_key,
    fetch_newapi_channels,
    fetch_newapi_groups_with_access_token,
    find_newapi_user_token_by_key,
    newapi_admin_target,
    newapi_auth_headers,
    parse_groups_payload,
    site_newapi_headers,
    update_newapi_channel,
)
from backend.integrations.sub2api import (
    fetch_sub2api_admin_channel_detail,
    fetch_sub2api_admin_channels_by_token,
    fetch_sub2api_admin_groups,
    fetch_sub2api_admin_site_channels,
    sub2api_admin_groups_payload,
    sub2api_admin_login,
    update_sub2api_admin_channel,
)
from backend.repositories.admin_sites import (
    ADMIN_SITE_CAPABILITIES,
    admin_site_platform,
    get_cached_admin_channel_key,
    is_admin_site_row,
    persist_admin_channel_key,
    validate_admin_site_base_url,
)


# ---------------------------------------------------------------------------
# Admin browser-session helpers
# ---------------------------------------------------------------------------

def _admin_browser_session_lock(site_id: int) -> threading.RLock:
    with ADMIN_BROWSER_SESSION_LOCKS_GUARD:
        return ADMIN_BROWSER_SESSION_LOCKS.setdefault(site_id, threading.RLock())


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
    verified_at = utc_now_iso()
    verification_guard_until = (
        app_now() + timedelta(seconds=60)
    ).isoformat(timespec="seconds")
    db_execute(
        """
        UPDATE admin_sites SET security_proof = ?, security_proof_verified_at = ?,
            key_sync_next_at = CASE WHEN key_sync_enabled = 1 THEN ? ELSE key_sync_next_at END,
            key_sync_last_error = NULL,
            key_sync_backoff_until = CASE WHEN key_sync_enabled = 1 THEN ? ELSE NULL END,
            key_sync_failure_count = 0, updated_at = ? WHERE id = ?
        """,
        (
            str(proof).strip(),
            verified_at,
            verified_at,
            verification_guard_until,
            verified_at,
            admin_site_id,
        ),
    )
    refreshed_site = db_query_one(
        "SELECT * FROM admin_sites WHERE id = ?", (admin_site_id,)
    )
    from backend.services.sync_service import refresh_next_admin_site_channel_key

    refresh_result = refresh_next_admin_site_channel_key(refreshed_site or site)
    if not refresh_result.get("success"):
        db_execute(
            """
            UPDATE admin_sites SET key_sync_last_at = ?, key_sync_last_error = ?,
                updated_at = ? WHERE id = ?
            """,
            (
                utc_now_iso(),
                str(refresh_result.get("message") or "读取渠道 key 失败"),
                utc_now_iso(),
                admin_site_id,
            ),
        )
        return False, (
            "2FA 验证已通过，但读取渠道 key 失败："
            f"{refresh_result.get('message') or '未知错误'}"
        )
    completed_at = utc_now_iso()
    refreshed_admin = refreshed_site or site
    if refreshed_admin.get("key_sync_enabled"):
        interval = max(
            5,
            min(1440, int(refreshed_admin.get("key_sync_interval_minutes") or 5)),
        )
        next_at = (
            completed_at
            if int(refresh_result.get("batch_remaining") or 0) > 0
            else (app_now() + timedelta(minutes=interval)).isoformat(timespec="seconds")
        )
    else:
        next_at = refreshed_admin.get("key_sync_next_at")
    db_execute(
        """
        UPDATE admin_sites SET key_sync_last_at = ?, key_sync_next_at = ?,
            key_sync_last_error = NULL, key_sync_backoff_until = NULL,
            key_sync_failure_count = 0, updated_at = ? WHERE id = ?
        """,
        (completed_at, next_at, completed_at, admin_site_id),
    )
    return True, None


# ---------------------------------------------------------------------------
# Admin-site channel / group dispatchers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Thin OO facade (retained for callers that prefer object-style access)
# ---------------------------------------------------------------------------

class AdminSiteService:
    def list(self) -> list[dict[str, Any]]:
        from backend.repositories.admin_sites import list_admin_sites_payload
        return list_admin_sites_payload()

    def get(self, admin_site_id: int):
        from backend.repositories.admin_sites import get_admin_site_or_404
        return get_admin_site_or_404(admin_site_id)

    def test(self, payload: dict[str, Any]):
        return test_admin_site_connection(payload)
