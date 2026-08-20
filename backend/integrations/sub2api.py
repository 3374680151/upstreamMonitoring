"""sub2api user-facing integration.

This module handles read-only user endpoints used by monitoring.  Admin
channel operations live in ``sub2api_admin.py`` so account reads cannot reach
the management API by accident.
"""

from __future__ import annotations

import json
import re
from typing import Any

from backend.integrations.sub2api_admin import is_auth_error, unwrap_response
from backend.integrations.transport import normalize_base_url, request_json, sub2api_token_headers


def _login(base_url: str, username: str, password: str) -> tuple[bool, dict[str, Any], str | None]:
    ok, payload, error = request_json(
        f"{normalize_base_url(base_url)}/api/v1/auth/login",
        payload={"email": str(username or "").strip(), "password": str(password or "")},
        method="POST",
    )
    if not ok:
        return False, payload if isinstance(payload, dict) else {}, error or "sub2api 登录失败"
    success, data, message = unwrap_response(payload)
    if not success or not isinstance(data, dict):
        return False, payload if isinstance(payload, dict) else {}, message or "sub2api 登录失败"
    token = str(data.get("access_token") or "").strip()
    if not token:
        return False, payload if isinstance(payload, dict) else {}, "sub2api 登录没有返回 access_token"
    return True, data, None


def _user_request(base_url: str, access_token: str, path: str):
    return request_json(
        f"{normalize_base_url(base_url)}{path}",
        headers=sub2api_token_headers(access_token),
    )


def parse_sub2api_groups(
    groups_payload: Any, rates_payload: Any = None
) -> dict[str, dict[str, Any]]:
    """Normalize the user-facing group list and per-user rate overrides."""
    if isinstance(groups_payload, dict) and "data" in groups_payload:
        groups_payload = groups_payload.get("data")
    if isinstance(rates_payload, dict) and "data" in rates_payload:
        rates_payload = rates_payload.get("data")
    if not isinstance(groups_payload, list):
        return {}
    rates = (
        {str(key): value for key, value in rates_payload.items()}
        if isinstance(rates_payload, dict)
        else {}
    )
    normalized: dict[str, dict[str, Any]] = {}
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


def _groups_by_token(
    base_url: str, access_token: str
) -> tuple[bool, dict[str, Any], str | None]:
    token = str(access_token or "").strip()
    if not token:
        return False, {}, "auth_token 为空"
    groups_ok, groups_payload, groups_error = _user_request(
        base_url, token, "/api/v1/groups/available"
    )
    if not groups_ok:
        return (
            False,
            {"groups": groups_payload},
            groups_error or "用户可用分组请求失败",
        )
    groups_success, groups_data, groups_message = unwrap_response(groups_payload)
    if not groups_success:
        return (
            False,
            {"groups": groups_payload},
            groups_message or "用户可用分组响应失败",
        )
    rates_ok, rates_payload, rates_error = _user_request(
        base_url, token, "/api/v1/groups/rates"
    )
    if not rates_ok:
        return (
            False,
            {"groups": groups_payload, "rates": rates_payload},
            rates_error or "用户分组倍率请求失败",
        )
    rates_success, rates_data, rates_message = unwrap_response(rates_payload)
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
    return (
        True,
        {
            "success": True,
            "data": groups_data,
            "user_rates": rates_data,
            "rates_error": None,
        },
        None,
    )


def _keys_by_token(
    base_url: str,
    access_token: str,
    page_size: int = 100,
    max_pages: int = 50,
) -> tuple[bool, dict[str, Any], str | None]:
    """Read every API key owned by the current sub2api user.

    The channel matcher needs the key's own group, so account-level groups are
    not sufficient.  Keep pagination complete: a truncated list must never be
    used to report a key as missing.
    """
    token = str(access_token or "").strip()
    if not token:
        return False, {}, "auth_token 为空"
    try:
        size = max(1, int(page_size))
        pages_limit = max(1, int(max_pages))
    except (TypeError, ValueError):
        return False, {}, "sub2api key 分页参数无效"
    all_items: list[dict[str, Any]] = []
    last_data: dict[str, Any] = {}
    for page in range(1, pages_limit + 1):
        ok, payload, error = _user_request(
            base_url, token, f"/api/v1/keys?page={page}&page_size={size}"
        )
        if not ok:
            return False, {"keys": payload}, error or "上游 key 列表请求失败"
        success, data, message = unwrap_response(payload)
        if not success:
            return False, {"keys": payload}, message or "上游 key 列表响应失败"
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            return False, {"keys": payload}, "上游 key 列表响应格式异常"
        items = [item for item in data["items"] if isinstance(item, dict)]
        if len(items) != len(data["items"]):
            return False, {"keys": payload}, "上游 key 列表包含无效项"
        all_items.extend(items)
        last_data = data
        try:
            total = int(data.get("total")) if data.get("total") is not None else None
        except (TypeError, ValueError):
            total = None
        try:
            page_count = int(data.get("pages")) if data.get("pages") is not None else None
        except (TypeError, ValueError):
            page_count = None
        if (
            not items
            or len(items) < size
            or (total is not None and len(all_items) >= total)
            or (page_count is not None and page >= page_count)
        ):
            aggregated = dict(last_data)
            aggregated.update(
                {
                    "items": all_items,
                    "total": total if total is not None else len(all_items),
                    "page": 1,
                    "page_size": size,
                }
            )
            return True, {"success": True, "data": aggregated}, None
    return (
        False,
        {"truncated": True, "pages_read": pages_limit},
        f"上游 key 列表超过最大分页页数 {pages_limit}，结果不完整",
    )


def _model_channels(
    base_url: str, access_token: str
) -> tuple[bool, dict[str, Any], str | None]:
    ok, payload, error = _user_request(
        base_url, access_token, "/api/v1/channels/available"
    )
    if not ok:
        return False, {"channels": payload}, error or "用户可用渠道请求失败"
    success, data, message = unwrap_response(payload)
    if not success:
        return False, {"channels": payload}, message or "用户可用渠道响应失败"
    if not isinstance(data, list):
        return False, {"channels": payload}, "用户可用渠道响应不是列表"
    return True, {"success": True, "data": data}, None


def _model_monitors(
    base_url: str, access_token: str
) -> tuple[bool, dict[str, Any], str | None]:
    ok, payload, error = _user_request(
        base_url, access_token, "/api/v1/channel-monitors"
    )
    if not ok:
        return False, {"monitors": payload}, error or "上游模型状态请求失败"
    success, data, message = unwrap_response(payload)
    if not success:
        return False, {"monitors": payload}, message or "上游模型状态响应失败"
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return False, {"monitors": payload}, "上游模型状态响应格式异常"
    return True, {"success": True, "data": data}, None


def _model_data_by_token(
    base_url: str, access_token: str
) -> tuple[bool, dict[str, Any], str | None]:
    channels_ok, channels_payload, channels_error = _model_channels(
        base_url, access_token
    )
    monitors_ok, monitors_payload, monitors_error = _model_monitors(
        base_url, access_token
    )
    if not channels_ok and not monitors_ok:
        errors = [message for message in (channels_error, monitors_error) if message]
        return (
            False,
            {"channels": channels_payload, "monitors": monitors_payload},
            "；".join(errors) or "读取上游模型数据失败",
        )
    return (
        True,
        {
            "success": True,
            "channels": channels_payload.get("data") if channels_ok else [],
            "monitors": monitors_payload.get("data")
            if monitors_ok
            else {"items": []},
            "channels_error": None if channels_ok else channels_error,
            "monitors_error": None if monitors_ok else monitors_error,
        },
        None,
    )


def _nested_status(value: Any) -> int:
    if not isinstance(value, dict):
        return 0
    try:
        status = int(value.get("status") or 0)
    except (TypeError, ValueError):
        status = 0
    if status:
        return status
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
        status = _nested_status(value.get(key))
        if status:
            return status
    return 0


def classify_auth_failure(payload: Any, error: str | None = None) -> str:
    """Classify a failed user-session request without exposing credentials.

    Refresh and password fallback are only valid after a clear authentication
    rejection.  Transport and malformed-response failures must stay terminal
    so an upstream outage does not cause an unnecessary login attempt.
    """
    status = _nested_status(payload)
    try:
        serialized = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        serialized = str(payload or "")
    text = " ".join((str(error or ""), serialized)).casefold()
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
    if status in {401, 403}:
        return "auth"
    if is_auth_error(payload, error):
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


def browser_session_required() -> tuple[bool, dict[str, Any], str]:
    return False, {
        "code": "BROWSER_SESSION_REQUIRED",
        "browser_sync_required": True,
    }, "请先在浏览器登录并同步"


class Sub2ApiClient:
    """Read-only user protocol client.

    It deliberately has no access to local persistence.  Token rotation,
    browser recovery and credential compare-and-swap are owned by
    ``Sub2ApiSiteAuthService``.
    """

    def login(
        self, base_url: str, username: str, password: str
    ) -> tuple[bool, dict[str, Any], str | None]:
        return _login(base_url, username, password)

    def refresh(
        self, base_url: str, refresh_token: str
    ) -> tuple[bool, dict[str, Any], str | None]:
        token = str(refresh_token or "").strip()
        if not token:
            return False, {}, "refresh_token 为空"
        ok, payload, error = request_json(
            f"{normalize_base_url(base_url)}/api/v1/auth/refresh",
            payload={"refresh_token": token},
            method="POST",
        )
        if not ok:
            return (
                False,
                payload if isinstance(payload, dict) else {},
                error or "刷新登录态失败",
            )
        success, data, message = unwrap_response(payload)
        if not success or not isinstance(data, dict):
            return (
                False,
                payload if isinstance(payload, dict) else {},
                message or "刷新登录态失败",
            )
        return True, data, None

    def fetch_groups_by_token(
        self, base_url: str, access_token: str
    ) -> tuple[bool, dict[str, Any], str | None]:
        return _groups_by_token(base_url, access_token)

    def fetch_keys_by_token(
        self,
        base_url: str,
        access_token: str,
        page_size: int = 100,
        max_pages: int = 50,
    ) -> tuple[bool, dict[str, Any], str | None]:
        return _keys_by_token(base_url, access_token, page_size, max_pages)

    def fetch_models_by_token(
        self, base_url: str, access_token: str
    ) -> tuple[bool, dict[str, Any], str | None]:
        return _model_data_by_token(base_url, access_token)

    def fetch_account_by_token(
        self, base_url: str, access_token: str
    ) -> tuple[bool, dict[str, Any], str | None]:
        token = str(access_token or "").strip()
        if not token:
            return False, {}, "auth_token 为空"
        ok, payload, error = _user_request(base_url, token, "/api/v1/auth/me")
        if not ok:
            return False, payload if isinstance(payload, dict) else {}, error
        success, data, message = unwrap_response(payload)
        if not success or not isinstance(data, dict):
            return (
                False,
                payload if isinstance(payload, dict) else {},
                message or "读取账户信息失败",
            )
        return True, data, None

    def fetch_groups(self, site: dict[str, Any]):
        """One-shot compatibility read without local credential mutation."""
        base_url = str(site.get("base_url") or "")
        auth_mode = str(site.get("auth_mode") or "password").strip().lower()
        if auth_mode == "password":
            ok, auth, error = self.login(
                base_url,
                str(site.get("login_username") or ""),
                str(site.get("login_password") or ""),
            )
            if not ok:
                return False, auth, error
            return self.fetch_groups_by_token(
                base_url, str(auth.get("access_token") or "")
            )
        token = str(site.get("access_token") or "").strip()
        if not token and auth_mode == "browser":
            return browser_session_required()
        return self.fetch_groups_by_token(base_url, token)

    def fetch_account(self, site: dict[str, Any]):
        base_url = str(site.get("base_url") or "")
        token = str(site.get("access_token") or "").strip()
        if not token and str(site.get("auth_mode") or "password").strip().lower() == "password":
            ok, auth, error = self.login(
                base_url,
                str(site.get("login_username") or ""),
                str(site.get("login_password") or ""),
            )
            if not ok:
                return False, auth, error
            token = str(auth.get("access_token") or "")
        if not token and str(site.get("auth_mode") or "").strip().lower() == "browser":
            return browser_session_required()
        return self.fetch_account_by_token(base_url, token)

    def validate_browser_session(
        self, base_url: str, access_token: str
    ) -> tuple[bool, dict[str, Any], str | None]:
        """Verify the captured browser token can read account and groups."""
        token = str(access_token or "").strip()
        if not token:
            return False, {}, "没有登录态，请提前登录"
        ok, payload, error = _user_request(base_url, token, "/api/v1/auth/me")
        if not ok:
            return False, {}, error or "登录态已过期，请重新登录"
        success, account, message = unwrap_response(payload)
        if not success or not isinstance(account, dict):
            return False, {}, message or "登录态已过期，请重新登录"
        ok, payload, error = _user_request(
            base_url, token, "/api/v1/groups/available"
        )
        if not ok:
            return False, {}, error or "当前登录态无法读取分组"
        success, groups, message = unwrap_response(payload)
        if not success:
            return False, {}, message or "当前登录态无法读取分组"
        ok, payload, error = _user_request(
            base_url, token, "/api/v1/groups/rates"
        )
        if not ok:
            return False, {}, error or "当前登录态无法读取分组倍率"
        success, rates, message = unwrap_response(payload)
        if not success or not isinstance(rates, dict):
            return False, {}, message or "当前登录态无法读取分组倍率"
        return True, {
            "account": account,
            "groups": {"data": groups, "user_rates": rates},
        }, None

    def fetch_models(self, site: dict[str, Any]):
        """One-shot compatibility read without local credential mutation."""
        base_url = str(site.get("base_url") or "")
        token = str(site.get("access_token") or "").strip()
        auth_mode = str(site.get("auth_mode") or "password").strip().lower()
        if not token and auth_mode == "password":
            ok, auth, error = self.login(
                base_url,
                str(site.get("login_username") or ""),
                str(site.get("login_password") or ""),
            )
            if not ok:
                return False, auth, error
            token = str(auth.get("access_token") or "").strip()
        if not token and auth_mode == "browser":
            return browser_session_required()
        return self.fetch_models_by_token(base_url, token)


def normalize_account(data: dict[str, Any]) -> dict[str, Any]:
    def to_float(value: Any):
        try:
            return round(float(value), 4)
        except (TypeError, ValueError):
            return None

    subscriptions = []
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
