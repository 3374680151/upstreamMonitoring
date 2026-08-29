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
from backend.core.state import (
    BROWSER_AUTH_MODE,
    NEWAPI_MATCH_GROUPS_CACHE,
    NEWAPI_MATCH_GROUPS_LOCK,
)
from backend.core.time import stable_hash, utc_now_iso
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
    newapi_browser_request,
    parse_groups_payload,
)
from backend.integrations.sub2api import (
    classify_sub2api_auth_failure,
    fetch_sub2api_keys,
    fetch_sub2api_usage_by_token,
    fetch_sub2api_user_groups,
    parse_sub2api_groups,
    probe_sub2api_gateway_key,
    sub2api_key_group_name,
)
from backend.repositories.admin_sites import (
    get_cached_admin_channel_key,
    is_admin_site_row,
    persist_admin_channel_key,
)
from backend.repositories.sites import (
    find_monitor_site_for_channel,
    site_auth_ready,
)


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
    # 渠道级上游认证配置已废弃：binding 只承载匹配结果，
    # 登录态一律来自「渠道监控」同 Base URL 的上游站点。
    return {
        "configured": True,
        "upstream_base_url": row.get("upstream_base_url") or "",
        "upstream_platform": row.get("upstream_platform") or "newapi",
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

def _cached_newapi_match_groups(
    upstream: Dict[str, Any],
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """上游用户分组带 30s TTL 缓存。

    分组数据与具体渠道无关，同一上游的多个渠道匹配共享一次请求。
    浏览器/密码站点走统一执行器（自带会话与系统令牌兜底），
    令牌站点保持直连；cache_key 不含明文 token（stable_hash）。
    """
    upstream_base = normalize_base_url(str(upstream.get("base_url") or ""))
    access_user_id = str(upstream.get("access_user_id") or "")
    cache_key = "|".join((upstream_base, stable_hash([access_user_id])))
    with NEWAPI_MATCH_GROUPS_LOCK:
        cached = NEWAPI_MATCH_GROUPS_CACHE.get(cache_key)
    if cached is not None:
        return cached

    auth_mode = str(upstream.get("auth_mode") or "").strip().lower()
    error: Optional[str] = None
    payload: Dict[str, Any] = {}
    ok = False
    if auth_mode in (BROWSER_AUTH_MODE, "password") and int(upstream.get("id") or 0) > 0:
        for path in ("/api/user/self/groups", "/api/user/groups"):
            ok, payload, error = newapi_browser_request(upstream, "GET", path)
            if ok and isinstance(payload, dict) and payload.get("success"):
                break
            ok = False
    else:
        ok, payload, error = fetch_newapi_groups_with_access_token(
            upstream_base,
            str(upstream.get("access_token") or ""),
            access_user_id,
        )
    if not ok:
        return False, {}, error
    result: Tuple[bool, Dict[str, Any], Optional[str]] = (True, payload, None)
    with NEWAPI_MATCH_GROUPS_LOCK:
        NEWAPI_MATCH_GROUPS_CACHE[cache_key] = result
    return result


def resolve_sub2api_key_current_group(
    base_url: str,
    access_token: str,
    key_item: Dict[str, Any],
    groups: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """定位路由型 key 的「当前分组」，返回标准 matched_groups 条目；无法
    定位时返回 None，由调用方退回 key 列表 group 字段的标准解析。

    二开版 sub2api（如 ai98）允许一个 key 配置多个路由分组：key 列表项带
    routing_groups（带 priority / availability）与 group_selection_mode
    （balanced/speed/cost/ordered），group 字段只是主分组，实际计费分组
    随路由动态变化。最近一条用量日志带 api_key_id / group_id /
    rate_multiplier，是当前实际计费分组的唯一可靠来源；没有用量时退回
    priority=1 的主分组。普通 sub2api 站点没有 routing_groups，直接返回
    None，行为不变。
    """
    raw_entries = key_item.get("routing_groups")
    routing_entries = (
        [item for item in raw_entries if isinstance(item, dict)]
        if isinstance(raw_entries, list)
        else []
    )
    if len(routing_entries) <= 1:
        return None

    groups_by_id: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    for name, info in groups.items():
        if isinstance(info, dict) and info.get("id") is not None:
            groups_by_id[str(info.get("id"))] = (name, info)

    current_group_id = ""
    located_by = ""
    billed_ratio: Any = None
    usage_group: Dict[str, Any] = {}
    key_id_text = str(key_item.get("id") or "")
    if key_id_text:
        usage_ok, usage_payload, _usage_error = fetch_sub2api_usage_by_token(
            base_url, access_token, key_id=int(key_id_text), page_size=1
        )
        if usage_ok:
            items = (usage_payload.get("data") or {}).get("items") or []
            latest = next(
                (
                    item
                    for item in items
                    if isinstance(item, dict)
                    and str(item.get("api_key_id") or "") == key_id_text
                ),
                None,
            )
            if isinstance(latest, dict):
                group_obj = latest.get("group")
                usage_group = group_obj if isinstance(group_obj, dict) else {}
                if latest.get("group_id") is not None:
                    current_group_id = str(latest.get("group_id"))
                    located_by = "usage"
                    billed_ratio = latest.get("rate_multiplier")

    routed_group: Dict[str, Any] = {}
    if not current_group_id:
        def _priority(item: Dict[str, Any]) -> int:
            try:
                return int(item.get("priority") or 0)
            except (TypeError, ValueError):
                return 999

        entry = min(routing_entries, key=_priority)
        routed_group = (
            entry.get("group") if isinstance(entry.get("group"), dict) else {}
        )
        if entry.get("group_id") is not None:
            current_group_id = str(entry.get("group_id"))
            located_by = "routing"
    if not current_group_id:
        return None

    name = ""
    group_info: Dict[str, Any] = {}
    matched = groups_by_id.get(current_group_id)
    if matched:
        name, group_info = matched
    if not name:
        name = str((usage_group or routed_group).get("name") or "").strip()
    if not name:
        return None
    group_obj = group_info or usage_group or routed_group

    ratio: Any = None
    ratio_type = "text"
    ratio_source = ""
    if billed_ratio is not None:
        try:
            ratio = float(billed_ratio)
            ratio_type = "number"
            ratio_source = "usage"
        except (TypeError, ValueError):
            ratio = None
    if ratio is None and group_info.get("ratio") is not None:
        ratio = group_info.get("ratio")
        ratio_type = str(group_info.get("ratio_type") or "text")
    if ratio is None and group_obj.get("rate_multiplier") is not None:
        try:
            ratio = float(group_obj.get("rate_multiplier"))
            ratio_type = "number"
        except (TypeError, ValueError):
            pass

    return {
        "name": name,
        "ratio": ratio,
        "ratio_type": ratio_type,
        "desc": str(
            group_info.get("desc") or group_obj.get("description") or ""
        ),
        "available_to_login": name in groups,
        "located_by": located_by,
        "ratio_source": ratio_source,
        "routing_count": len(routing_entries),
        "selection_mode": str(key_item.get("group_selection_mode") or "").strip(),
    }


def match_channel_upstream_binding(
    site: Dict[str, Any], channel_id: int, force_refresh: bool = False
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    admin_site_id = int(site["id"])
    binding = get_channel_upstream_binding(admin_site_id, channel_id)
    detail_ok, detail_payload, _detail_error = fetch_newapi_channel_detail(site, channel_id)
    detail = detail_payload.get("data") if isinstance(detail_payload, dict) else {}
    detail = detail if isinstance(detail, dict) else {}

    # 渠道匹配一律复用「渠道监控」中同 Base URL 站点的登录态（浏览器同步
    # 会话、系统兜底令牌或显式用户令牌）；渠道级上游认证配置已废弃。
    monitor_site = find_monitor_site_for_channel(str(detail.get("base_url") or ""))
    if not monitor_site or not str(monitor_site.get("base_url") or ""):
        return True, {
            "configured": False,
            "match_status": "unmatched",
            "match_message": "未找到同 Base URL 的上游监控站点，请先在站点监控添加该上游",
            "matched_groups": [],
        }, None

    upstream = monitor_site
    upstream_base = str(upstream.get("base_url") or "")
    platform = str(upstream.get("platform") or "newapi").strip().lower()

    # 只配置了一个公开 NewAPI 监控站点，并不等于已经配置了可读取用户 API
    # 密钥的登录态。此时直接提示先完成同步/录入令牌，避免先读取主站受保护
    # key，随后才报"缺少令牌"，也避免无意义地触发主站 2FA/限流。
    if platform == "newapi" and not site_auth_ready(upstream):
        return True, {
            "configured": True,
            "inherited_from_monitor": True,
            "upstream_base_url": upstream_base,
            "upstream_platform": platform,
            "match_status": "unmatched",
            "match_message": "同 Base URL 的渠道监控未配置用户登录态，请先完成浏览器同步或录入访问令牌",
            "matched_groups": [],
        }, None

    def verification_required_payload(
        message: str,
        matched_groups: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        return {
            "configured": True,
            "inherited_from_monitor": True,
            "upstream_base_url": upstream_base,
            "upstream_platform": platform,
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
        if not site_auth_ready(upstream):
            message = "NewAPI 上游未配置用户登录态（浏览器会话或访问令牌），无法读取用户 API 密钥列表"
            persist_channel_match(admin_site_id, channel_id, "error", message, [])
            return False, {}, message

        matched, upstream_match_error = find_newapi_user_token_by_key(upstream, channel_key)
        if not matched:
            message = upstream_match_error or "当前 key 未在上游 NewAPI 用户 API 密钥列表中找到"
            # 只有"明确没找到"才是 key_not_found；网络/登录态/限流等临时故障
            # 一律归为 error，保住上次成功匹配数据，避免把能用的 key 标成失效。
            status = (
                "key_not_found"
                if "未在上游 NewAPI 用户 API 密钥列表中找到" in message
                else "error"
            )
            persist_channel_match(admin_site_id, channel_id, status, message, [])
            return False, {}, message
        group_names = split_channel_groups(matched.get("group"))
        if not group_names:
            message = "上游 NewAPI 已找到当前 key，但该用户 API 密钥没有配置分组"
            persist_channel_match(admin_site_id, channel_id, "no_group", message, [])
            return False, {}, message

        groups_ok, groups_payload, groups_error = _cached_newapi_match_groups(upstream)
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
        monitor_site_id = int(monitor_site.get("id") or 0)
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
            if classify_sub2api_auth_failure(groups_payload, groups_error) == "auth":
                message = "上游登录态已失效，无法读取上游分组，请重新同步登录态"
            else:
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
            if classify_sub2api_auth_failure(keys_payload, keys_error) == "auth":
                message = "上游登录态已失效，无法读取 key 列表，请重新同步登录态"
            else:
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
            # key 列表按登录账号隔离：key 仍被网关接受却不在列表里，说明它
            # 属于另一个账号，提示换绑而不是误导性的「已删除或重置」。
            if probe_sub2api_gateway_key(upstream_base, channel_key):
                message = (
                    "当前渠道 key 与 sub2api 登录账号不匹配：key 在上游仍然有效，"
                    "但属于其他账号；请更换渠道 key，或重新同步该账号的登录态"
                )
            else:
                message = "当前渠道 key 未在 sub2api 登录账号的 key 列表中找到（可能已被删除或重置）"
            persist_channel_match(admin_site_id, channel_id, "key_not_found", message, [])
            return False, {}, message

        raw_group = key_match.get("group")
        key_group = raw_group if isinstance(raw_group, dict) else {}
        # 二开版 sub2api 允许一个 key 配置多个路由分组，列表里的 group 只是
        # 主分组；先按用量日志/路由配置定位「当前分组」，失败再退标准解析，
        # 普通 sub2api 站点行为不变。
        current_group = resolve_sub2api_key_current_group(
            upstream_base, key_token, key_match, groups
        )
        key_group_name = (
            str(current_group.get("name") or "").strip()
            if current_group
            else sub2api_key_group_name(key_match, groups)
        )
        if not key_group_name:
            message = "sub2api 已找到当前 key，但该 key 没有返回所属分组"
            persist_channel_match(admin_site_id, channel_id, "no_group", message, [])
            return False, {}, message
        group_names = [key_group_name]
        matched_by = "key 精确匹配"
        if current_group:
            matched_groups = [
                {
                    "name": key_group_name,
                    "ratio": current_group.get("ratio"),
                    "ratio_type": current_group.get("ratio_type") or "text",
                    "desc": current_group.get("desc") or "",
                    "available_to_login": bool(current_group.get("available_to_login")),
                }
            ]
        else:
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
        if status == "matched":
            message = f"已按{matched_by}读取 sub2api 分组倍率"
        else:
            # key 的分组已不在登录可见分组目录（被上游删除/隐藏或订阅过期）；
            # 倍率是否还有 key.group 基础值兜底，决定前端提示哪种数据来源。
            missing_names = "、".join(
                str(item.get("name") or "")
                for item in matched_groups
                if not item["available_to_login"]
            ).strip("、")
            has_fallback_ratio = any(
                item.get("ratio") is not None
                for item in matched_groups
                if not item["available_to_login"]
            )
            ratio_hint = (
                "倍率为 key 记录的基础值"
                if has_fallback_ratio
                else "且未返回该分组的倍率"
            )
            message = (
                f"已按{matched_by}找到分组「{missing_names}」，"
                f"但该分组已不在上游可见分组目录中，{ratio_hint}"
            )
        if current_group:
            routing_count = int(current_group.get("routing_count") or 0)
            mode_text = (
                str(current_group.get("selection_mode") or "").strip()
                or "未知模式"
            )
            if current_group.get("located_by") == "usage":
                message += (
                    f"；该 key 配置了 {routing_count} 个路由分组（{mode_text}），"
                    "已按最近一次用量定位当前分组，倍率为实际计费倍率"
                )
            else:
                message += (
                    f"；该 key 配置了 {routing_count} 个路由分组（{mode_text}），"
                    "暂无用量记录，按主分组（priority=1）展示"
                )
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
    return True, {
        "configured": True,
        "inherited_from_monitor": True,
        "upstream_base_url": upstream_base,
        "upstream_platform": platform,
        "match_status": status,
        "match_message": message,
        "matched_groups": matched_groups,
        "matched_at": now,
    }, None
