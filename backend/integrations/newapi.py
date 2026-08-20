"""NewAPI read integration used by monitoring and connection probes."""

from __future__ import annotations

from http.cookies import SimpleCookie
import threading
import time
from typing import Any
from urllib.parse import quote
from urllib.parse import urlparse

from backend.integrations.newapi_admin import auth_failure_message
from backend.integrations.transport import (
    newapi_auth_headers,
    normalize_base_url,
    request_json,
    request_json_with_headers,
)


def clamp_perf_hours(raw: Any, default: float = 24) -> float:
    try:
        hours = float(raw)
    except (TypeError, ValueError):
        hours = float(default)
    if hours <= 0:
        hours = float(default)
    return min(hours, 24 * 30)


def _status_from_payload(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get("status")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _success_payload(
    ok: bool, payload: Any, error: str | None, fallback: str
) -> tuple[bool, dict[str, Any], str | None]:
    safe_payload = payload if isinstance(payload, dict) else {"raw": payload}
    if ok and isinstance(payload, dict) and payload.get("success"):
        return True, payload, None
    if ok:
        return False, safe_payload, str(payload.get("message") or fallback) if isinstance(payload, dict) else fallback
    return False, safe_payload, error or fallback


def site_origin(base_url: str) -> str:
    """Return a safe HTTP Origin value, or an empty string for invalid URLs."""
    try:
        parsed = urlparse(normalize_base_url(base_url))
        parsed.port
    except (TypeError, ValueError):
        return ""
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def browser_session_headers(session: dict[str, Any]) -> dict[str, str]:
    """Build headers for an already captured NewAPI browser session bundle."""
    token = str(session.get("access_token") or "").strip()
    user_id = str(session.get("access_user_id") or "").strip()
    session_id = str(session.get("browser_session_id") or "").strip()
    cookie = str(
        session.get("browser_cookie")
        or session.get("browser_refresh_cookie")
        or ""
    ).strip()
    headers: dict[str, str] = {}
    if token:
        normalized_token = token.removeprefix("Bearer ").removeprefix(
            "bearer "
        ).strip()
        headers["Authorization"] = (
            f"Bearer {normalized_token}" if session_id else normalized_token
        )
    if user_id:
        headers["New-Api-User"] = user_id
    if session_id:
        headers["X-Auth-Session"] = session_id
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _refresh_cookie_from_headers(headers: dict[str, Any], previous: str = "") -> str:
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


def _browser_auth_data(
    source: dict[str, Any], payload: Any, response_headers: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None, "NewAPI 刷新没有返回认证数据"
    access_token = str(data.get("access_token") or "").strip()
    user = data.get("user") if isinstance(data.get("user"), dict) else {}
    access_user_id = str(
        user.get("id") or source.get("access_user_id") or ""
    ).strip()
    browser_session = (
        data.get("session") if isinstance(data.get("session"), dict) else {}
    )
    session_id = str(browser_session.get("sid") or "").strip()
    if not access_token or not access_user_id or not session_id:
        return None, "NewAPI 刷新没有返回有效的网页登录态"
    return {
        "access_token": access_token,
        "access_user_id": access_user_id,
        "browser_refresh_cookie": _refresh_cookie_from_headers(
            response_headers, str(source.get("browser_refresh_cookie") or "")
        ),
        "browser_session_id": session_id,
        "browser_access_expires_at": data.get("access_expires_at") or 0,
    }, None


def login_password(
    base_url: str,
    username: str,
    password: str,
    verification_code: str = "",
    access_user_id: str = "",
    previous_refresh_cookie: str = "",
) -> tuple[bool, dict[str, Any], str | None]:
    """Log in to NewAPI and return a refreshable browser-session bundle."""
    normalized_base = normalize_base_url(base_url)
    username = str(username or "").strip()
    password = str(password or "")
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
            payload={"code": str(verification_code or "").strip(), "flow_token": flow_token},
            method="POST",
        )
    if not ok or not isinstance(payload, dict) or not payload.get("success"):
        return False, {}, auth_failure_message(payload, error)
    auth, auth_error = _browser_auth_data(
        {
            "access_user_id": access_user_id,
            "browser_refresh_cookie": previous_refresh_cookie,
        },
        payload,
        response_headers,
    )
    if not auth:
        return False, {}, auth_error or "NewAPI 登录没有返回有效登录态"
    return True, auth, None


def refresh_browser_session(
    site: dict[str, Any]
) -> tuple[bool, dict[str, Any], str | None]:
    """Refresh one NewAPI browser session without reading or writing MySQL."""
    refresh_cookie = str(site.get("browser_refresh_cookie") or "").strip()
    session_id = str(site.get("browser_session_id") or "").strip()
    origin = site_origin(str(site.get("base_url") or ""))
    if not refresh_cookie or not session_id:
        return False, {}, "NewAPI 网页登录态缺少 Refresh Cookie 或 Session ID"
    if not origin:
        return False, {}, "渠道 URL 无法生成有效 Origin，请检查渠道地址"
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
        code = ""
        if isinstance(payload, dict):
            code = str(payload.get("code") or "").strip()
            raw = payload.get("raw")
            if not code and isinstance(raw, str):
                try:
                    import json

                    parsed = json.loads(raw)
                except (TypeError, ValueError):
                    parsed = {}
                if isinstance(parsed, dict):
                    code = str(parsed.get("code") or "").strip()
        messages = {
            "AUTH_ORIGIN_FORBIDDEN": "NewAPI 站点拒绝刷新登录态：Origin 不受信任，请检查站点 URL 和可信 Origin 配置",
            "AUTH_SESSION_MISMATCH": "NewAPI 站点 RT 与 Session 不一致，请重新完成网页登录和 2FA",
            "AUTH_SESSION_REVOKED": "NewAPI 站点网页登录 Session 已失效，请重新完成网页登录和 2FA",
            "AUTH_UNAUTHORIZED": "NewAPI 站点网页登录 Session 已失效，请重新完成网页登录和 2FA",
            "AUTH_REFRESH_RACE": "NewAPI 站点登录态正在刷新，请稍后重试",
        }
        return (
            False,
            {},
            messages.get(
                code,
                f"NewAPI 网页登录态刷新失败：{auth_failure_message(payload, error)}",
            ),
        )
    auth, auth_error = _browser_auth_data(site, payload, response_headers)
    if not auth:
        return False, {}, auth_error or "NewAPI 刷新没有返回有效的网页登录态"
    return True, auth, None


def fetch_groups_with_headers(
    base_url: str, headers: dict[str, str]
) -> tuple[bool, dict[str, Any], str | None]:
    errors: list[str] = []
    for path in ("/api/user/self/groups", "/api/user/groups"):
        ok, payload, error = request_json(
            f"{normalize_base_url(base_url)}{path}", headers=headers
        )
        if ok and isinstance(payload, dict) and payload.get("success"):
            return True, payload, None
        errors.append(f"{path}: {auth_failure_message(payload, error or 'success=false')}")
    return False, {"errors": errors}, "访问令牌分组采集失败：" + "；".join(errors)


NEWAPI_USER_TOKEN_LIST_CACHE_TTL_SECONDS = 15.0
_user_token_cache: dict[str, tuple[list[dict[str, Any]], float]] = {}
_user_token_cache_lock = threading.RLock()


def normalize_user_token_key(value: Any) -> str:
    """Normalize NewAPI token keys before comparing a channel key.

    NewAPI stores user tokens without the optional ``sk-`` prefix while
    channels and the UI can include it.
    """
    value = str(value or "").strip()
    return value[3:] if value.lower().startswith("sk-") else value


def mask_user_token_key(value: Any) -> str:
    """Return NewAPI's display mask for a user token key."""
    key = normalize_user_token_key(value)
    if not key:
        return ""
    if len(key) <= 4:
        return "*" * len(key)
    if len(key) <= 8:
        return f"{key[:2]}****{key[-2:]}"
    return f"{key[:4]}**********{key[-4:]}"


def _user_token_items(payload: Any) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return [item for item in data["items"] if isinstance(item, dict)]
    return []


def _user_token_cache_key(site: dict[str, Any]) -> str:
    """Build a secret-free cache key for a user's token list."""
    try:
        expires = int(site.get("browser_access_expires_at") or 0)
    except (TypeError, ValueError):
        expires = 0
    return "|".join(
        (
            normalize_base_url(str(site.get("base_url") or "")),
            str(int(site.get("id") or 0)),
            str(site.get("auth_mode") or "token").strip().lower(),
            # Channel bindings are transient rows (id=0).  The same upstream
            # can be bound to different ordinary users, so their token lists
            # must never share this short-lived cache entry.
            str(site.get("access_user_id") or "").strip(),
            str(expires // 60),
        )
    )


class NewApiClient:
    def __init__(self, site_auth: Any = None) -> None:
        self._site_auth = site_auth

    def fetch_groups(self, base_url: str):
        ok, payload, error = request_json(
            f"{normalize_base_url(base_url)}/api/user/groups"
        )
        if not ok:
            return False, payload if isinstance(payload, dict) else {"raw": payload}, error
        if not isinstance(payload, dict) or not payload.get("success"):
            return False, payload if isinstance(payload, dict) else {"raw": payload}, "success=false"
        return True, payload, None

    def fetch_account(self, base_url: str, access_token: str, user_id: str = ""):
        token = str(access_token or "").strip()
        if not token:
            return False, {}, "缺少系统访问令牌，无法读取账户额度"
        ok, payload, error = request_json(
            f"{normalize_base_url(base_url)}/api/user/self",
            headers=newapi_auth_headers(token, user_id),
        )
        if not ok:
            return False, payload if isinstance(payload, dict) else {"raw": payload}, error
        if not isinstance(payload, dict) or not payload.get("success"):
            return False, payload if isinstance(payload, dict) else {"raw": payload}, "success=false"
        data = payload.get("data")
        return True, data if isinstance(data, dict) else {}, None

    def fetch_account_for_site(
        self, site: dict[str, Any]
    ) -> tuple[bool, dict[str, Any], str | None]:
        """Read account data using the site's configured auth mode."""
        auth_mode = str(site.get("auth_mode") or "token").strip().lower()
        if auth_mode in {"browser", "password"}:
            ok, payload, error = self._site_request(site, "/api/user/self")
            if not ok:
                return False, payload if isinstance(payload, dict) else {}, error
            if not isinstance(payload, dict) or not payload.get("success"):
                return False, payload if isinstance(payload, dict) else {}, "success=false"
            data = payload.get("data")
            return True, data if isinstance(data, dict) else {}, None
        return self.fetch_account(
            str(site.get("base_url") or ""),
            str(site.get("access_token") or ""),
            str(site.get("access_user_id") or ""),
        )

    def fetch_groups_for_site(
        self, site: dict[str, Any]
    ) -> tuple[bool, dict[str, Any], str | None]:
        """Fetch authenticated groups from the two NewAPI-compatible paths."""
        errors: list[str] = []
        for path in ("/api/user/self/groups", "/api/user/groups"):
            ok, payload, error = self._site_request(site, path)
            if ok and isinstance(payload, dict) and payload.get("success"):
                return True, payload, None
            errors.append(
                f"{path}: {auth_failure_message(payload, error or 'success=false')}"
            )
        return (
            False,
            {"errors": errors},
            "访问令牌分组采集失败：" + "；".join(errors),
        )

    def fetch_user_tokens_for_site(
        self,
        site: dict[str, Any],
        page_size: int = 100,
        max_pages: int = 50,
    ) -> tuple[bool, list[dict[str, Any]], str | None]:
        """Read the current user's complete API-token list.

        This is deliberately a user endpoint, not a channel-admin endpoint.
        It therefore works for an upstream binding that only has ordinary user
        credentials.
        """
        if not site.get("access_token") or not site.get("access_user_id"):
            return False, [], "NewAPI 上游缺少用户认证令牌或用户 ID"
        try:
            size = max(1, int(page_size))
            pages = max(1, int(max_pages))
        except (TypeError, ValueError):
            return False, [], "NewAPI 用户 API 密钥分页参数无效"
        all_items: list[dict[str, Any]] = []
        for page in range(1, pages + 1):
            query = f"p={page}&page_size={size}&size={size}"
            ok, payload, error = self._site_request(site, "/api/token/", query=query)
            if not ok:
                return False, [], error or "读取 NewAPI 用户 API 密钥列表失败"
            if not isinstance(payload, dict) or not payload.get("success"):
                message = str(payload.get("message") or "") if isinstance(payload, dict) else ""
                return False, [], message or "NewAPI 用户 API 密钥列表响应异常"
            items = _user_token_items(payload)
            all_items.extend(items)
            data = payload.get("data")
            raw_total = data.get("total") if isinstance(data, dict) else None
            try:
                total = int(raw_total) if raw_total is not None else None
            except (TypeError, ValueError):
                total = None
            if len(items) < size or (total is not None and len(all_items) >= total):
                return True, all_items, None
        return False, [], f"NewAPI 用户 API 密钥超过最大分页页数 {pages}，结果不完整"

    def fetch_user_token_key_for_site(
        self, site: dict[str, Any], token_id: int
    ) -> tuple[bool, str, str | None]:
        """Read one user's plaintext token key for an exact comparison."""
        ok, payload, error = self._site_request(
            site, f"/api/token/{int(token_id)}/key", method="POST"
        )
        if not ok:
            return False, "", error or "读取 NewAPI 用户 API 密钥失败"
        data = payload.get("data") if isinstance(payload, dict) else None
        key = data.get("key") if isinstance(data, dict) else ""
        if not isinstance(payload, dict) or not payload.get("success") or not str(key or "").strip():
            message = str(payload.get("message") or "") if isinstance(payload, dict) else ""
            return False, "", message or "NewAPI 用户 API 密钥响应异常"
        return True, str(key).strip(), None

    def find_user_token_by_key(
        self, site: dict[str, Any], channel_key: str
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Find a user token by its plaintext channel key without admin APIs."""
        target = normalize_user_token_key(channel_key)
        if not target:
            return None, "当前渠道没有真实 key，无法查询上游分组"
        cache_key = _user_token_cache_key(site)
        with _user_token_cache_lock:
            cached = _user_token_cache.get(cache_key)
            if cached and time.monotonic() - cached[1] < NEWAPI_USER_TOKEN_LIST_CACHE_TTL_SECONDS:
                tokens = [dict(item) for item in cached[0]]
            else:
                ok, tokens, error = self.fetch_user_tokens_for_site(site)
                if not ok:
                    return None, error or "读取 NewAPI 用户 API 密钥列表失败"
                tokens = [dict(item) for item in tokens if isinstance(item, dict)]
                _user_token_cache[cache_key] = (tokens, time.monotonic())

        target_mask = mask_user_token_key(target)
        candidates = [
            item
            for item in tokens
            if normalize_user_token_key(item.get("key")) in {target, target_mask}
        ]
        if not candidates:
            return None, "当前 key 未在上游 NewAPI 用户 API 密钥列表中找到"
        key_errors: list[str] = []
        for item in candidates:
            full_key = str(item.get("_full_key") or "").strip()
            if not full_key:
                try:
                    token_id = int(item.get("id"))
                except (TypeError, ValueError):
                    continue
                key_ok, full_key, key_error = self.fetch_user_token_key_for_site(
                    site, token_id
                )
                if not key_ok:
                    if key_error and key_error not in key_errors:
                        key_errors.append(key_error)
                    continue
                item["_full_key"] = full_key
            if normalize_user_token_key(full_key) == target:
                return item, None
        if key_errors:
            return None, "读取上游 NewAPI 用户 API 密钥失败：" + "；".join(key_errors)
        return None, "当前 key 未在上游 NewAPI 用户 API 密钥列表中找到"

    @staticmethod
    def normalize_account(data: dict[str, Any]) -> dict[str, Any]:
        def to_usd(value: Any):
            try:
                return round(float(value) / 500000.0, 4)
            except (TypeError, ValueError):
                return None
        return {
            "platform": "newapi",
            "username": str(data.get("username") or ""),
            "group": str(data.get("group") or ""),
            "balance_usd": to_usd(data.get("quota")),
            "used_usd": to_usd(data.get("used_quota")),
            "request_count": data.get("request_count"),
            "raw_quota": data.get("quota"),
            "raw_used_quota": data.get("used_quota"),
            "quota_per_unit": 500000.0,
            "subscriptions": [],
        }

    def fetch_pricing_for_site(self, site: dict[str, Any]):
        ok, payload, error = self._site_request(site, "/api/pricing")
        return _success_payload(ok, payload, error, "读取 pricing 失败")

    def fetch_uptime_for_site(self, site: dict[str, Any]):
        """Read the public uptime feed with the site's active auth context.

        Some NewAPI deployments place this endpoint behind the same browser
        session as pricing.  Reusing ``_site_request`` retains that behavior
        while token-based sites stay entirely inside the new transport layer.
        """
        ok, payload, error = self._site_request(site, "/api/uptime/status")
        return _success_payload(ok, payload, error, "读取 uptime/status 失败")

    def validate_browser_session(
        self, base_url: str, session: dict[str, Any]
    ) -> tuple[bool, dict[str, Any], str | None]:
        """Validate a captured NewAPI browser session without local writes."""
        access_token = str(session.get("access_token") or "").strip()
        access_user_id = str(session.get("access_user_id") or "").strip()
        browser_cookie = str(session.get("browser_cookie") or "").strip()
        if not access_token and not browser_cookie:
            return False, {}, "没有登录态，请提前登录"
        if not access_user_id:
            return False, {}, "浏览器登录态缺少 NewAPI 用户 ID"

        headers = self.browser_session_headers(session)
        normalized_base = normalize_base_url(base_url)
        ok, payload, error = request_json(
            f"{normalized_base}/api/user/self", headers=headers
        )
        if (
            not ok
            or not isinstance(payload, dict)
            or not payload.get("success")
            or not isinstance(payload.get("data"), dict)
        ):
            return (
                False,
                {},
                auth_failure_message(payload, error or "登录态已过期，请重新登录"),
            )
        account = payload["data"]
        account_id = str(account.get("id") or "").strip()
        if account_id and account_id != access_user_id:
            return False, {}, "浏览器登录用户与 NewAPI 用户 ID 不匹配"

        errors: list[str] = []
        for path in ("/api/user/self/groups", "/api/user/groups"):
            groups_ok, groups_payload, groups_error = request_json(
                f"{normalized_base}{path}", headers=headers
            )
            if (
                groups_ok
                and isinstance(groups_payload, dict)
                and groups_payload.get("success")
            ):
                return True, {"account": account, "groups": groups_payload}, None
            errors.append(
                f"{path}: {auth_failure_message(groups_payload, groups_error or 'success=false')}"
            )
        return False, {}, "当前登录态无法读取分组：" + "；".join(errors)

    @staticmethod
    def browser_session_headers(session: dict[str, Any]) -> dict[str, str]:
        return browser_session_headers(session)

    def fetch_perf_summary_for_site(self, site: dict[str, Any], hours: float = 24):
        hours = clamp_perf_hours(hours, 24)
        ok, payload, error = self._site_request(
            site, "/api/perf-metrics/summary", query=f"hours={hours:g}"
        )
        return _success_payload(ok, payload, error, "读取 perf-metrics/summary 失败")

    def fetch_perf_detail_for_site(self, site: dict[str, Any], model: str, hours: float = 24, group: str = ""):
        model = str(model or "").strip()
        if not model:
            return False, {}, "model is required"
        hours = clamp_perf_hours(hours, 24)
        query = f"model={quote(model)}&hours={hours:g}"
        group = str(group or "").strip()
        if group:
            query += f"&group={quote(group)}"
        ok, payload, error = self._site_request(site, "/api/perf-metrics", query=query)
        return _success_payload(ok, payload, error, "读取 perf-metrics 失败")

    def _site_request(
        self,
        site: dict[str, Any],
        path: str,
        *,
        query: str = "",
        method: str = "GET",
        payload: Any = None,
    ) -> tuple[bool, Any, str | None]:
        """Read a site endpoint while preserving browser-session semantics."""
        auth_mode = str(site.get("auth_mode") or "token").strip().lower()
        if auth_mode in {"browser", "password"}:
            if self._site_auth is None:
                from backend.services.newapi_site_auth_service import (
                    NewApiSiteAuthService,
                )

                self._site_auth = NewApiSiteAuthService()
            return self._site_auth.request(
                site, method, path, payload=payload, query=query
            )

        headers = newapi_auth_headers(
            str(site.get("access_token") or ""),
            str(site.get("access_user_id") or ""),
        )
        proof = str(site.get("security_proof") or "").strip()
        if proof:
            headers["X-Security-Proof"] = proof
        url = f"{normalize_base_url(str(site.get('base_url') or ''))}{path}"
        if query:
            url = f"{url}?{query}"
        ok, response, error = request_json(
            url, headers=headers, payload=payload, method=method
        )
        if ok:
            return True, response, None
        if _status_from_payload(response) in {401, 403}:
            return False, response, "上游令牌已失效，请刷新或重新录入"
        return False, response, error or "NewAPI 上游调用失败"
