"""Pure sub2api administrator HTTP protocol implementation.

The protocol client deliberately knows nothing about local persistence. A
``session_adapter`` may be injected for the existing database-backed token
refresh flow; without one, requests use the token present on the supplied site
row and remain fully usable for one-shot login/probe calls.
"""

from __future__ import annotations

import re
import time
from typing import Any, Optional, Protocol
from urllib.parse import quote, urlparse

from backend.integrations.transport import (
    normalize_base_url,
    request_json,
    sub2api_token_headers,
)


class SessionAdapter(Protocol):
    def ensure_session(
        self,
        site: dict[str, Any],
        force_refresh: bool = False,
        rejected_access_token: str = "",
    ) -> tuple[bool, str, Optional[str]]:
        ...


def _dict_payload(payload: Any) -> dict[str, Any]:
    return payload if isinstance(payload, dict) else {"raw": payload}


def unwrap_response(payload: Any) -> tuple[bool, Any, Optional[str]]:
    if not isinstance(payload, dict):
        return False, payload, "响应不是 JSON 对象"
    if "code" in payload and payload.get("code") != 0:
        return False, payload, str(payload.get("message") or "code != 0")
    return True, payload.get("data"), None


def is_auth_error(payload: Any, error: Optional[str] = None) -> bool:
    auth_codes = {
        "401", "403", "UNAUTHORIZED", "FORBIDDEN", "INVALID_AUTH_HEADER",
        "EMPTY_TOKEN", "TOKEN_EXPIRED", "INVALID_TOKEN", "USER_NOT_FOUND",
        "USER_INACTIVE", "TOKEN_REVOKED",
    }
    auth_messages = {
        "unauthorized", "forbidden", "authorization header is required",
        "token cannot be empty", "token has expired", "invalid token",
        "user not found", "user account is not active", "未登录", "登录已过期",
        "令牌已过期", "令牌无效",
    }
    if isinstance(payload, dict):
        for key in ("groups", "rates", "channels", "monitors", "refresh", "account", "login", "response", "data"):
            if isinstance(payload.get(key), dict) and is_auth_error(payload[key], error):
                return True
        try:
            status = int(payload.get("status") or 0)
        except (TypeError, ValueError):
            status = 0
        if status in {401, 403}:
            return True
        code = re.sub(r"[^A-Z0-9]+", "_", str(payload.get("code") or "").upper()).strip("_")
        if code in auth_codes:
            return True
        message = str(payload.get("message") or payload.get("error") or "").strip().casefold()
        if message in auth_messages:
            return True
    return bool(error and (error.startswith("HTTP 401") or error.startswith("HTTP 403")))


def _sub2api_channel_page(payload: Any) -> tuple[bool, list[dict[str, Any]], int, Optional[str]]:
    success, data, message = unwrap_response(payload)
    if not success or not isinstance(data, dict):
        return False, [], 0, message or "sub2api 渠道响应异常"
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        return False, [], 0, "sub2api 渠道响应缺少 items"
    if any(not isinstance(value, dict) for value in raw_items):
        return False, [], 0, "sub2api 渠道响应包含无效项"
    try:
        total = max(0, int(data.get("total") or 0))
    except (TypeError, ValueError):
        total = 0
    return True, list(raw_items), total, None


def _group_ids(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for item in value:
        try:
            group_id = int(item)
        except (TypeError, ValueError):
            continue
        if group_id not in result:
            result.append(group_id)
    return result


def normalize_channel(
    channel: dict[str, Any], groups_by_id: dict[int, dict[str, Any]]
) -> dict[str, Any]:
    normalized = dict(channel)
    status = str(channel.get("status") or "disabled").strip().lower()
    normalized_status = "active" if status == "active" else "disabled" if status == "disabled" else "error"
    group_ids = _group_ids(channel.get("group_ids"))
    pricing = channel.get("model_pricing")
    mapping = channel.get("model_mapping")
    normalized.update(
        {
            "source_platform": "sub2api",
            "normalized_status": normalized_status,
            "group_ids": group_ids,
            "groups": [
                {
                    "id": group_id,
                    "name": groups_by_id.get(group_id, {}).get("name") or f"#{group_id}",
                    "platform": groups_by_id.get(group_id, {}).get("platform") or "",
                    "status": groups_by_id.get(group_id, {}).get("status") or "",
                    "rate_multiplier": groups_by_id.get(group_id, {}).get("rate_multiplier"),
                }
                for group_id in group_ids
            ],
            "model_pricing": [value for value in pricing if isinstance(value, dict)] if isinstance(pricing, list) else [],
            "model_mapping": dict(mapping) if isinstance(mapping, dict) else {},
            "capabilities": {"edit": True, "toggle": True, "create": False, "delete": False},
        }
    )
    return normalized


def groups_payload(groups: list[dict[str, Any]]) -> dict[str, Any]:
    data: dict[str, dict[str, Any]] = {}
    for item in groups:
        try:
            group_id = int(item.get("id") or 0)
        except (TypeError, ValueError):
            group_id = 0
        name = str(item.get("name") or f"#{group_id}")
        data[name] = {
            "id": group_id,
            "name": name,
            "ratio": item.get("rate_multiplier"),
            "rate_multiplier": item.get("rate_multiplier"),
            "ratio_type": "number",
            "desc": item.get("description") or "",
            "platform": item.get("platform") or "",
            "status": item.get("status") or "",
        }
    return {"success": True, "data": data}


ADMIN_CHANNEL_UPDATE_FIELDS = {
    "name", "description", "status", "group_ids", "model_pricing", "model_mapping",
    "billing_model_source", "restrict_models", "features", "features_config",
    "apply_pricing_to_account_stats", "account_stats_pricing_rules",
}
BILLING_MODEL_SOURCES = {"requested", "upstream", "channel_mapped"}


def validate_channel_patch(patch: dict[str, Any]) -> Optional[str]:
    unknown = sorted(set(patch) - ADMIN_CHANNEL_UPDATE_FIELDS)
    if unknown:
        return f"sub2api 渠道不允许更新字段：{', '.join(unknown)}"
    if not patch:
        return "没有要更新的 sub2api 渠道字段"
    if "status" in patch and str(patch.get("status") or "").lower() not in {"active", "disabled"}:
        return "sub2api 渠道状态只允许 active 或 disabled"
    if "billing_model_source" in patch and str(patch.get("billing_model_source") or "").lower() not in BILLING_MODEL_SOURCES:
        return "sub2api 渠道 billing_model_source 只允许 channel_mapped、requested 或 upstream"
    return None


def proxy_error_response(
    payload: Any,
    error: Optional[str] = None,
    fallback_message: str = "sub2api 上游请求失败",
) -> tuple[int, dict[str, Any]]:
    """Map an upstream failure to the stable public admin-channel envelope."""
    from backend.core.sanitize import safe_value, sanitize_error_text

    def sanitize_metadata(value: Any, field_name: str = "") -> Any:
        """Keep structured diagnostics without returning raw provider bodies."""
        if isinstance(value, dict):
            return {
                str(key): sanitize_metadata(item, str(key))
                for key, item in value.items()
                if str(key).strip().lower() != "raw"
            }
        if isinstance(value, list):
            return [sanitize_metadata(item) for item in value]
        if field_name.strip().lower() in {"message", "error", "detail"}:
            return sanitize_error_text(value)
        return value

    raw = payload if isinstance(payload, dict) else {}
    try:
        status = int(raw.get("status") or 0)
    except (TypeError, ValueError):
        status = 0
    code = str(raw.get("code") or "").strip()
    message = str(raw.get("message") or raw.get("error") or error or fallback_message)
    if not status and str(error or "").startswith("HTTP "):
        try:
            status = int(str(error).split()[1])
        except (IndexError, ValueError):
            status = 0
    safe_message = sanitize_error_text(message or fallback_message)
    lowered = safe_message.casefold()
    if is_auth_error(raw, error):
        category, response_status = "auth", 502
    elif "无主站管理权限" in safe_message or "not an admin" in lowered:
        category, response_status = "not_admin", 502
    elif "2fa" in lowered or "turnstile" in lowered:
        category, response_status = "unsupported_verification", 502
    elif status in {400, 422}:
        category, response_status = "validation", status
    elif status == 404:
        category, response_status = "not_found", 404
    elif status == 429:
        category, response_status = "rate_limited", 429
    elif status >= 500:
        category, response_status = "upstream_server", 502
    else:
        category, response_status = "upstream_error", 502
    envelope: dict[str, Any] = {
        "success": False,
        "message": safe_message,
        "category": category,
    }
    # ``request_json`` keeps HTTP error bodies under ``raw``.  Those bodies,
    # and textual error fields at any nesting level, can echo credentials.
    safe_payload = sanitize_metadata(safe_value(raw))
    if isinstance(safe_payload, dict):
        envelope["upstream"] = safe_payload
    safe_code = re.sub(r"[^A-Za-z0-9_.-]+", "_", code).strip("_")[:100]
    if safe_code:
        envelope["upstream_code"] = safe_code
    return response_status, envelope


def _admin_login_payload(payload: Any) -> tuple[bool, dict[str, Any], Optional[str]]:
    success, data, message = unwrap_response(payload)
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


def admin_login(
    base_url: str, email: str, password: str
) -> tuple[bool, dict[str, Any], Optional[str]]:
    email = str(email or "").strip()
    password = str(password or "")
    if not email or not password:
        return False, {}, "sub2api 主站需要管理员邮箱和密码"
    ok, payload, error = request_json(
        f"{normalize_base_url(base_url)}/api/v1/auth/login",
        payload={"email": email, "password": password, "turnstile_token": ""},
        method="POST",
        admin=True,
    )
    if not ok:
        message = str(_dict_payload(payload).get("message") or error or "sub2api 主站登录失败")
        if "turnstile" in message.lower():
            message = "当前 sub2api 主站不支持 Turnstile 登录验证"
        return False, _dict_payload(payload), message
    ok, auth, error = _admin_login_payload(payload)
    return ok, auth if ok else _dict_payload(payload), error


def admin_refresh_token(
    base_url: str, refresh_token: str
) -> tuple[bool, dict[str, Any], Optional[str]]:
    refresh_token = str(refresh_token or "").strip()
    if not refresh_token:
        return False, {}, "sub2api 主站 refresh token 为空"
    ok, payload, error = request_json(
        f"{normalize_base_url(base_url)}/api/v1/auth/refresh",
        payload={"refresh_token": refresh_token},
        method="POST",
        admin=True,
    )
    if not ok:
        return False, _dict_payload(payload), error
    success, data, message = unwrap_response(payload)
    if not success or not isinstance(data, dict):
        return False, {}, message or "刷新 sub2api 主站登录态失败"
    return True, data, None


class Sub2ApiAdminProtocol:
    def __init__(
        self,
        base_url: str = "",
        access_token: str = "",
        refresh_token: str = "",
        site: Optional[dict[str, Any]] = None,
        session_adapter: Optional[SessionAdapter] = None,
    ) -> None:
        source = dict(site or {})
        self.base_url = normalize_base_url(source.get("base_url") or base_url or "")
        self.access_token = str(
            source.get("sub2api_access_token") or access_token or ""
        ).strip()
        self.refresh_token = str(
            source.get("sub2api_refresh_token") or refresh_token or ""
        ).strip()
        self.site = {**source, "base_url": self.base_url}
        self.site["sub2api_access_token"] = self.access_token
        self.site["sub2api_refresh_token"] = self.refresh_token
        self.session_adapter = session_adapter

    def _token(self, force_refresh: bool = False, rejected_access_token: str = "") -> tuple[bool, str, Optional[str]]:
        if self.session_adapter is not None and int(self.site.get("id") or 0) > 0:
            ok, token, error = self.session_adapter.ensure_session(
                self.site, force_refresh=force_refresh, rejected_access_token=rejected_access_token
            )
            if ok:
                self.access_token = str(token or "").strip()
                self.site["sub2api_access_token"] = self.access_token
            return ok, self.access_token if ok else "", error
        token = self.access_token
        if not token:
            return False, "", "sub2api 主站 access token 为空"
        return True, token, None

    @staticmethod
    def _validate_path(path: str) -> tuple[bool, str, Optional[str]]:
        parsed = urlparse(str(path or ""))
        request_path = parsed.path
        if parsed.scheme or parsed.netloc or not request_path.startswith("/"):
            return False, request_path, "sub2api 管理请求路径无效"
        if request_path.startswith("/api/v1/admin/accounts"):
            return False, request_path, "本系统不读取或监控 sub2api 号池"
        channel_path_allowed = request_path == "/api/v1/admin/channels" or bool(
            re.fullmatch(r"/api/v1/admin/channels/[0-9]+", request_path)
        )
        if not (channel_path_allowed or request_path == "/api/v1/admin/groups/all"):
            return False, request_path, "sub2api 主站仅允许访问渠道和分组配置"
        return True, request_path, None

    def request(
        self,
        path: str,
        method: str = "GET",
        payload: Any = None,
    ) -> tuple[bool, dict[str, Any], Optional[str]]:
        valid, request_path, validation_error = self._validate_path(path)
        if not valid:
            return False, {}, validation_error
        ok, token, error = self._token()
        if not ok:
            return False, {}, error
        url = f"{self.base_url}{path}"
        ok, response, error = request_json(
            url, headers=sub2api_token_headers(token), payload=payload, method=method, admin=True
        )
        normalized = _dict_payload(response)
        # Some sub2api deployments return an auth business code with HTTP 200.
        # Treat it like an HTTP 401/403 so the persisted refresh flow gets the
        # same single retry instead of surfacing a stale-session failure.
        if not is_auth_error(normalized, error):
            return ok, normalized, error
        # Token-only callers have no persistence/session adapter and should
        # preserve the one-request behavior of the legacy token path.
        if self.session_adapter is None or int(self.site.get("id") or 0) <= 0:
            return ok, normalized, error
        ok, token, session_error = self._token(force_refresh=True, rejected_access_token=token)
        if not ok:
            return False, {}, session_error
        ok, response, error = request_json(
            url, headers=sub2api_token_headers(token), payload=payload, method=method, admin=True
        )
        return ok, _dict_payload(response), error

    def list_channels(
        self,
        keyword: str = "",
        page_size: int = 100,
        max_pages: int = 100,
    ) -> tuple[bool, list[dict[str, Any]], dict[str, Any], Optional[str]]:
        page_size = max(1, min(500, int(page_size)))
        max_pages = max(1, int(max_pages))
        items: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            query = f"page={page}&page_size={page_size}"
            if str(keyword or "").strip():
                query += f"&search={quote(str(keyword).strip(), safe='')}"
            ok, payload, error = self.request(f"/api/v1/admin/channels?{query}")
            if not ok:
                return False, [], payload, error or "读取 sub2api 渠道失败"
            page_ok, page_items, total, page_error = _sub2api_channel_page(payload)
            if not page_ok:
                return False, [], payload, page_error
            items.extend(page_items)
            if (total and len(items) >= total) or (not total and len(page_items) < page_size):
                return True, items, {"total": total or len(items), "page": page, "page_size": page_size}, None
            if not page_items:
                return False, [], {}, "sub2api 渠道分页提前结束，拒绝返回截断数据"
        return False, [], {}, "sub2api 渠道超过最大分页页数，拒绝返回截断数据"

    def list_groups_raw(self) -> tuple[bool, list[dict[str, Any]], dict[str, Any], Optional[str]]:
        ok, payload, error = self.request("/api/v1/admin/groups/all")
        if not ok:
            return False, [], payload, error or "读取 sub2api 分组失败"
        success, data, message = unwrap_response(payload)
        if not success or not isinstance(data, list):
            return False, [], payload, message or "sub2api 分组响应异常"
        return True, [item for item in data if isinstance(item, dict)], {}, None

    def list_groups(self) -> tuple[bool, dict[str, Any], Optional[str]]:
        ok, groups, upstream, error = self.list_groups_raw()
        if not ok:
            return False, upstream, error
        return True, groups_payload(groups), None

    def list_site_channels(
        self, keyword: str = "", page_size: int = 100, max_pages: int = 100
    ) -> tuple[bool, list[dict[str, Any]], dict[str, Any], Optional[str]]:
        groups_ok, groups, groups_upstream, groups_error = self.list_groups_raw()
        if not groups_ok:
            return False, [], groups_upstream, groups_error
        groups_by_id: dict[int, dict[str, Any]] = {}
        for group in groups:
            try:
                groups_by_id[int(group.get("id"))] = group
            except (TypeError, ValueError):
                continue
        ok, channels, meta, error = self.list_channels(keyword, page_size, max_pages)
        if not ok:
            return False, [], meta, error
        return True, [normalize_channel(item, groups_by_id) for item in channels], meta, None

    def get_channel(self, channel_id: int) -> tuple[bool, dict[str, Any], Optional[str]]:
        ok, payload, error = self.request(f"/api/v1/admin/channels/{int(channel_id)}")
        if not ok:
            return False, payload, error or "读取 sub2api 渠道详情失败"
        success, data, message = unwrap_response(payload)
        if not success or not isinstance(data, dict):
            return False, payload, message or "sub2api 渠道详情响应异常"
        groups_ok, groups, groups_upstream, groups_error = self.list_groups_raw()
        if not groups_ok:
            return False, groups_upstream, groups_error
        groups_by_id: dict[int, dict[str, Any]] = {}
        for group in groups:
            try:
                groups_by_id[int(group.get("id"))] = group
            except (TypeError, ValueError):
                continue
        return True, {"success": True, "data": normalize_channel(data, groups_by_id)}, None

    def update_channel(
        self, channel_id: int, patch: dict[str, Any]
    ) -> tuple[bool, dict[str, Any], Optional[str]]:
        validation_error = validate_channel_patch(patch)
        if validation_error:
            return False, {}, validation_error
        ok, payload, error = self.request(
            f"/api/v1/admin/channels/{int(channel_id)}", method="PUT", payload=dict(patch)
        )
        if not ok:
            return False, payload, error or "更新 sub2api 渠道失败"
        success, data, message = unwrap_response(payload)
        if not success:
            return False, payload, message or "更新 sub2api 渠道失败"
        return True, {"success": True, "data": data}, None

    def test_connection(self) -> tuple[bool, dict[str, Any], Optional[str]]:
        ok, channels, _meta, error = self.list_channels()
        if not ok:
            return False, {"error_source": "upstream", "details": channels}, error
        return True, {"channels_count": len(channels)}, None


__all__ = [
    "ADMIN_CHANNEL_UPDATE_FIELDS",
    "BILLING_MODEL_SOURCES",
    "SessionAdapter",
    "Sub2ApiAdminProtocol",
    "admin_login",
    "admin_refresh_token",
    "groups_payload",
    "is_auth_error",
    "normalize_channel",
    "proxy_error_response",
    "unwrap_response",
    "validate_channel_patch",
]
