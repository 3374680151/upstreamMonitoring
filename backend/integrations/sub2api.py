"""sub2api integration.

Protocol-specific sub2api helpers -- group / channel / model parsing, auth &
token refresh, admin-site channel management, account / balance reading and
browser-session application -- live here.  They were moved out of
``backend.legacy_runtime`` verbatim; the legacy runtime re-exports them so
existing callers keep working unchanged.

Generic transport / response helpers (``request_json``, ``admin_request_json``,
``_upstream_response_details`` ...) and the shared browser-session
persistence primitives live in :mod:`backend.integrations.http` and
:mod:`backend.legacy_runtime` respectively.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

from backend.core.normalize import normalize_base_url
from backend.integrations.http import (
    _upstream_response_details,
    _upstream_response_message,
    admin_request_json,
    detect_waf_challenge_payload,
    request_json,
)
from backend.core.state import (
    ADMIN_SUB2API_EXPIRY_SKEW_SECONDS,
    ADMIN_SUB2API_SESSION_LOCKS,
    BROWSER_AUTH_MODE,
    SESSION_SYNC_REQUEST_LOCK,
    SUB2API_REFRESH_CACHE,
    SUB2API_REFRESH_CACHE_TTL_SECONDS,
    SUB2API_REFRESH_LOCKS,
    SUB2API_SITE_AUTH_LOCKS,
)
from backend.core.time import app_now, utc_now_iso
from backend.services.session_sync_service import (
    _site_session_sync_request_error,
    mark_site_browser_session_expired,
    persist_site_browser_session,
)
from backend.db.connection import (
    _q,
    db_connection,
    db_execute,
    db_execute_rowcount,
    db_query_all,
    db_query_one,
)


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
            # WAF/盾的边缘 403/401（HTML 挑战页）不代表凭据失效，不算认证错误
            if detect_waf_challenge_payload(payload, error):
                return False
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
    # WAF/盾的边缘拦截优先于人机验证判定：挑战页 HTML 里常带 captcha/turnstile
    # 字样，但它说明请求没到达上游应用，与凭据无关，不能推进任何降级链。
    if detect_waf_challenge_payload(payload, error):
        return "waf"
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
    return ADMIN_SUB2API_SESSION_LOCKS.lock(int(site_id))


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
    return SUB2API_REFRESH_LOCKS.lock(normalize_base_url(base_url))


def _sub2api_site_auth_lock(site_id: int) -> threading.RLock:
    return SUB2API_SITE_AUTH_LOCKS.lock(int(site_id))


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


def fetch_sub2api_usage_by_token(
    base_url: str,
    access_token: str,
    key_id: Any = None,
    page_size: int = 5,
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """读取最近用量记录（按时间倒序）。记录带 api_key_id / group_id /
    rate_multiplier，是路由型 key「当前实际计费分组」的唯一可靠来源。
    二开版支持 api_key_id 过滤，单 key 定位只需一页。"""
    token = (access_token or "").strip()
    if not token:
        return False, {}, "auth_token 为空"
    page_size = max(1, min(200, int(page_size)))
    query = f"page=1&page_size={int(page_size)}"
    if key_id is not None:
        try:
            query += f"&api_key_id={int(key_id)}"
        except (TypeError, ValueError):
            pass
    ok, payload, error = request_json(
        f"{normalize_base_url(base_url)}/api/v1/usage?{query}",
        headers=sub2api_token_headers(token),
    )
    if not ok:
        return False, {"usage": payload}, error or "上游用量请求失败"
    success, data, message = unwrap_sub2api_response(payload)
    if not success or not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return False, {"usage": payload}, message or "上游用量响应格式异常"
    return True, {"success": True, "data": data}, None


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


SUB2API_WAF_BLOCKED_MESSAGE = "上游防护拦截（WAF），与登录态无关，请稍后重试"


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
            category = classify_sub2api_auth_failure(payload, error)
            if not ok and category == "waf":
                error = SUB2API_WAF_BLOCKED_MESSAGE
            return ok, normalized, error, category

        if mode == "password":
            login_ok, login_token, login_payload, login_error = sub2api_login(
                current_base, current_username, current_password
            )
            if not login_ok:
                if classify_sub2api_auth_failure(login_payload, login_error) == "waf":
                    return False, {"login": login_payload}, SUB2API_WAF_BLOCKED_MESSAGE
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
                if refresh_category == "waf":
                    return (
                        False,
                        {"refresh": refreshed},
                        SUB2API_WAF_BLOCKED_MESSAGE,
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
            if login_category == "waf":
                return False, {"login": login_payload}, SUB2API_WAF_BLOCKED_MESSAGE
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
        if detect_waf_challenge_payload(account, account_error):
            return False, {}, SUB2API_WAF_BLOCKED_MESSAGE
        return False, {}, account_error or "登录态已过期，请重新登录"
    groups_ok, groups, groups_error = fetch_sub2api_groups_by_token(base_url, token)
    if not groups_ok:
        if detect_waf_challenge_payload(groups, groups_error):
            return False, {}, SUB2API_WAF_BLOCKED_MESSAGE
        return False, {}, groups_error or "当前登录态无法读取分组"
    return True, {"account": account, "groups": groups}, None


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
            if message == SUB2API_WAF_BLOCKED_MESSAGE:
                # WAF 边缘拦截不代表凭据失效：不改登录态、不置 expired，
                # 同步请求按失败返回，等下一轮自动重试。
                return False, message
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
class Sub2ApiClient:
    """Facade used by the service layer to fetch sub2api data for a site."""

    def fetch_groups(self, site: "dict[str, Any]"):
        return fetch_sub2api_user_groups(
            site["base_url"],
            username=site.get("login_username") or "",
            password=site.get("login_password") or "",
            auth_mode=site.get("auth_mode") or "password",
            access_token=site.get("access_token") or "",
            refresh_token=site.get("refresh_token") or "",
            site_id=int(site.get("id") or 0),
        )

    def fetch_account(self, site: "dict[str, Any]"):
        return fetch_sub2api_account(
            site["base_url"],
            username=site.get("login_username") or "",
            password=site.get("login_password") or "",
            auth_mode=site.get("auth_mode") or "password",
            access_token=site.get("access_token") or "",
            refresh_token=site.get("refresh_token") or "",
            site_id=int(site.get("id") or 0),
        )

    def fetch_models(self, site: "dict[str, Any]"):
        return fetch_sub2api_model_data(
            site["base_url"],
            username=site.get("login_username") or "",
            password=site.get("login_password") or "",
            auth_mode=site.get("auth_mode") or "password",
            access_token=site.get("access_token") or "",
            refresh_token=site.get("refresh_token") or "",
            site_id=int(site.get("id") or 0),
        )

