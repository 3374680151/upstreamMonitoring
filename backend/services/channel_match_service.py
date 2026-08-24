"""Channel-upstream matching service.

Persistence and matching logic for ``channel_upstream_bindings``, moved out
of ``backend.legacy_runtime``.  The legacy runtime re-exports every name
below for backward compatibility.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from backend.core.normalize import (
    _channel_key_is_masked,
    format_change_value,
    normalize_base_url,
    ratio_direction,
    ratio_number,
    split_channel_groups,
)
from backend.core.state import BROWSER_AUTH_MODE
from backend.core.time import utc_now_iso
from backend.db.connection import (
    _q,
    db_connection,
    db_execute,
    db_query_all,
    db_query_one,
)
from backend.integrations.newapi import (
    _newapi_password_login_bundle,
    fetch_newapi_channel_detail,
    fetch_newapi_channel_key,
    fetch_newapi_groups_with_access_token,
    find_newapi_user_token_by_key,
    parse_groups_payload,
)
from backend.integrations.sub2api import (
    fetch_sub2api_keys,
    fetch_sub2api_user_groups,
    parse_sub2api_groups,
    sub2api_key_group_name,
)
from backend.repositories.admin_sites import (
    get_cached_admin_channel_key,
    is_admin_site_row,
    persist_admin_channel_key,
)
from backend.repositories.sites import find_monitor_site_for_channel


# ---------------------------------------------------------------------------
# Binding CRUD
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Match persistence
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

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
    # 随后才报"缺少令牌"，也避免无意义地触发主站 2FA/限流。
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
            "渠道运行正常，但本地尚未保存该渠道 key。请点击\"编辑主站\"，"
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
            from backend.services.sync_service import persist_channel_binding_refreshed_auth

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
            from backend.services.sync_service import persist_channel_binding_refreshed_auth

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
