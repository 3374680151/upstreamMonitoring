"""HTTP transport primitives for upstream site requests.

传输层基于 **httpx**（连接复用 / 超时 / 手动重定向控制），并在连接被上游
重置（常见于 Cloudflare 前置、对 stdlib TLS 指纹敏感的站点）时回退到
**curl_cffi**（libcurl TLS 栈），取代旧版手写的 urllib opener 与
subprocess curl 包装。对外仍暴露 ``request_json`` /
``admin_request_json`` / ``request_json_with_headers`` 等既有契约。
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

import httpx
from curl_cffi import requests as curl_requests

from backend.core.config import HTTP_TIMEOUT_SECONDS
from backend.core.normalize import _cookie_header_from_response, _url_origin  # noqa: F401 (_cookie_header_from_response 为兼容旧导入面保留再导出)


# ---------------------------------------------------------------------------
# 内部响应模型：统一 httpx 与 curl_cffi 两种来源
# ---------------------------------------------------------------------------


class UpstreamHeaders:
    """大小写不敏感的多值响应头。"""

    __slots__ = ("_items",)

    def __init__(self, items: List[Tuple[str, str]]):
        self._items = [(str(k), str(v)) for k, v in items]

    def get(self, name: str, default: str = "") -> str:
        low = name.lower()
        for key, value in self._items:
            if key.lower() == low:
                return value
        return default

    def get_all(self, name: str) -> List[str]:
        low = name.lower()
        return [value for key, value in self._items if key.lower() == low]


class UpstreamResponse:
    __slots__ = ("status", "headers", "content")

    def __init__(self, status: int, headers: UpstreamHeaders, content: bytes):
        self.status = status
        self.headers = headers
        self.content = content

    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class UpstreamHttpStatusError(Exception):
    """等价于旧 ``urllib.error.HTTPError`` 的语义：>=400 状态 + 可 read() 的响应体。"""

    def __init__(
        self,
        url: str,
        status: int,
        message: str,
        headers: UpstreamHeaders,
        body: bytes,
    ):
        self.url = url
        self.code = status
        self.headers = headers
        self._body = body
        super().__init__(message)

    def read(self) -> bytes:
        return self._body


def _response_from_httpx(resp: httpx.Response) -> UpstreamResponse:
    return UpstreamResponse(
        resp.status_code,
        UpstreamHeaders(list(resp.headers.multi_items())),
        resp.content,
    )


# ---------------------------------------------------------------------------
# 共享客户端与发送逻辑
# ---------------------------------------------------------------------------

_client_lock = threading.Lock()
_client: Optional[httpx.Client] = None


def _http_client() -> httpx.Client:
    """进程内共享客户端；trust_env=False 等价旧的「不走应用级代理」。"""
    global _client
    with _client_lock:
        if _client is None or _client.is_closed:
            _client = httpx.Client(
                trust_env=False,
                timeout=HTTP_TIMEOUT_SECONDS,
                follow_redirects=False,
                headers={"User-Agent": "Upstream-Ratio-Watch/1.0"},
            )
        return _client


_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


def _is_connection_reset(exc: BaseException) -> bool:
    seen = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ConnectionResetError):
            return True
        text = str(current).lower()
        if "connection reset by peer" in text or "econnreset" in text:
            return True
        current = current.__cause__ or current.__context__
    return False


def _fetch_with_curl(
    url: str,
    method: str,
    headers: Dict[str, str],
    data: Optional[bytes],
    timeout: float,
) -> UpstreamResponse:
    """连接被重置时的兼容传输：curl_cffi 走 libcurl TLS 栈。"""
    kwargs: Dict[str, Any] = {
        "headers": dict(headers),
        "data": data if data is not None else b"",
        "timeout": max(1.0, float(timeout)),
        "allow_redirects": False,
        "proxy": "",
    }
    try:
        resp = curl_requests.request(method, url, **kwargs)
    except TypeError:
        kwargs.pop("proxy", None)
        resp = curl_requests.request(method, url, **kwargs)
    except Exception as exc:  # noqa: BLE001 - 统一转译为可读错误
        raise RuntimeError(f"curl 兼容请求失败：{exc}") from exc
    return UpstreamResponse(
        resp.status_code,
        UpstreamHeaders(list(resp.headers.multi_items())),
        resp.content or b"",
    )


def send_upstream_request(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    data: Optional[bytes] = None,
    timeout: float = HTTP_TIMEOUT_SECONDS,
    same_origin_only: bool = False,
    max_redirects: int = 10,
) -> UpstreamResponse:
    """发送请求并手动跟随重定向（保持与旧 urllib 行为一致）。

    - 301/302/303 且方法非 GET/HEAD：改写为 GET 并丢弃请求体；
    - 307/308：保留原方法与请求体；
    - ``same_origin_only=True``（管理端）：跨 Origin 跳转按 403 拒绝，
      防止管理员凭据被重定向到外部域；
    - 连接被上游重置时回退 curl_cffi 兼容传输；
    - >=400 抛 ``UpstreamHttpStatusError``（等价 urllib HTTPError 语义）。
    """
    method = method.upper()
    merged: Dict[str, str] = {
        "Accept": "application/json",
        "User-Agent": "Upstream-Ratio-Watch/1.0",
    }
    if headers:
        merged.update(headers)

    current_url = url
    current_data = data
    response: Optional[UpstreamResponse] = None
    for _ in range(max_redirects + 1):
        try:
            resp = _http_client().request(
                method,
                current_url,
                headers=merged,
                content=current_data,
                timeout=timeout,
            )
            response = _response_from_httpx(resp)
        except httpx.TransportError as exc:
            if not _is_connection_reset(exc):
                raise
            response = _fetch_with_curl(
                current_url, method, merged, current_data, timeout
            )

        assert response is not None
        if response.status not in _REDIRECT_STATUSES:
            break
        location = response.headers.get("location")
        if not location:
            break
        next_url = str(httpx.URL(current_url).join(location))
        if same_origin_only:
            try:
                cross_origin = _url_origin(next_url) != _url_origin(current_url)
            except (TypeError, ValueError):
                cross_origin = True
            if cross_origin:
                raise UpstreamHttpStatusError(
                    next_url,
                    403,
                    "跨 Origin 跳转已拒绝",
                    response.headers,
                    response.content,
                )
        if response.status in (301, 302, 303) and method not in ("GET", "HEAD"):
            method = "GET"
            current_data = None
        current_url = next_url
    else:
        raise httpx.TooManyRedirects(f"超过最大重定向次数：{url}")

    if response.status >= 400:
        raise UpstreamHttpStatusError(
            current_url,
            response.status,
            f"HTTP {response.status}",
            response.headers,
            response.content,
        )
    return response


# ---------------------------------------------------------------------------
# 对外契约层（签名与返回结构与迁移前完全一致）
# ---------------------------------------------------------------------------


def request_json(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    payload: Optional[Dict[str, Any]] = None,
    method: str = "GET",
) -> Tuple[bool, Any, Optional[str]]:
    data = None
    request_headers: Dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    if headers:
        request_headers.update(headers)
    try:
        resp = send_upstream_request(
            url, method=method, headers=request_headers, data=data
        )
        body = resp.text()
        parsed = json.loads(body) if body else {}
        return True, parsed, None
    except UpstreamHttpStatusError as exc:
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        return False, {"status": exc.code, "raw": raw}, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - 保持既有宽松契约
        return False, {"error": str(exc)}, str(exc)


def admin_request_json(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    payload: Optional[Dict[str, Any]] = None,
    method: str = "GET",
) -> Tuple[bool, Any, Optional[str]]:
    request_headers: Dict[str, str] = {}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    if headers:
        request_headers.update(headers)
    try:
        resp = send_upstream_request(
            url,
            method=method,
            headers=request_headers,
            data=data,
            same_origin_only=True,
        )
        body = resp.text()
        return True, json.loads(body) if body else {}, None
    except UpstreamHttpStatusError as exc:
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        return False, {"status": exc.code, "raw": raw}, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - 保持既有宽松契约
        return False, {"error": str(exc)}, str(exc)


def request_json_with_headers(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    payload: Optional[Dict[str, Any]] = None,
    method: str = "GET",
) -> Tuple[bool, Any, Optional[str], Dict[str, Any]]:
    """请求 JSON，同时保留响应头，供网页登录态捕获 Set-Cookie 使用。"""
    data = None
    request_headers: Dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    if headers:
        request_headers.update(headers)
    try:
        resp = send_upstream_request(
            url, method=method, headers=request_headers, data=data
        )
        body = resp.text()
        parsed = json.loads(body) if body else {}
        response_headers: Dict[str, Any] = {
            "set-cookie": resp.headers.get_all("Set-Cookie")
        }
        return True, parsed, None, response_headers
    except UpstreamHttpStatusError as exc:
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        return (
            False,
            {"status": exc.code, "raw": raw},
            f"HTTP {exc.code}",
            {"set-cookie": exc.headers.get_all("Set-Cookie")},
        )
    except Exception as exc:  # noqa: BLE001 - 保持既有宽松契约
        return False, {"error": str(exc)}, str(exc), {"set-cookie": []}


# ---------------------------------------------------------------------------
# 错误信息翻译器（纯函数，行为不变）
# ---------------------------------------------------------------------------


def _upstream_response_message(payload: Any, error: Optional[str] = None) -> str:
    """Extract the useful upstream message without exposing credentials."""
    if isinstance(payload, dict):
        message = str(payload.get("message") or payload.get("error") or "").strip()
        if message:
            return message
        raw = payload.get("raw")
        if isinstance(raw, str) and raw:
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                message = str(parsed.get("message") or parsed.get("error") or "").strip()
                if message:
                    return message
    return str(error or "").strip()


def _upstream_response_details(
    payload: Any, error: Optional[str] = None
) -> Tuple[int, str, str]:
    status = 0
    code = ""
    message = _upstream_response_message(payload, error)
    if not isinstance(payload, dict):
        return status, code, message
    try:
        status = int(payload.get("status") or 0)
    except (TypeError, ValueError):
        status = 0
    if not status:
        match = re.search(r"\bHTTP\s+([1-5][0-9]{2})\b", str(error or ""), re.I)
        if match:
            status = int(match.group(1))
    code = str(payload.get("code") or "").strip()
    raw = payload.get("raw")
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            code = code or str(parsed.get("code") or "").strip()
            message = str(parsed.get("message") or message).strip()
    return status, code, message


def _admin_browser_refresh_error(payload: Any, error: Optional[str]) -> str:
    _status, code, message = _upstream_response_details(payload, error)
    if code == "AUTH_ORIGIN_FORBIDDEN":
        return "主站拒绝刷新登录态：Origin 不受信任，请检查主站 URL 和可信 Origin 配置"
    if code == "AUTH_SESSION_MISMATCH":
        return "主站 RT 与 Session 不一致，请重新完成主站网页登录和 2FA"
    if code in {"AUTH_SESSION_REVOKED", "AUTH_UNAUTHORIZED"}:
        return "主站网页登录 Session 已失效，请重新完成主站网页登录和 2FA"
    if code == "AUTH_REFRESH_RACE":
        return "主站登录态正在刷新，请稍后重试"
    return f"主站网页登录态刷新失败：{message or code or '未知错误'}"


def channel_admin_error_message(error: Optional[str], payload: Any = None) -> str:
    """把上游 401/403（系统令牌不是管理员/权限不足）翻译成可操作的明确提示，
    避免用户只看到一个笼统的 502/HTTP 401。"""
    text = str(error or "")
    blob = text
    if isinstance(payload, (dict, list)):
        try:
            blob += " " + json.dumps(payload, ensure_ascii=False)
        except Exception:
            pass
    lower = blob.lower()
    if (
        "401" in blob
        or "403" in blob
        or "unauthorized" in lower
        or "forbidden" in lower
        or "无权" in blob
        or "权限" in blob
    ):
        return (
            "当前系统访问令牌不是管理员或权限不足，无法管理渠道："
            "请在「站点监控」编辑该站点，把系统访问令牌换成管理员用户的令牌"
        )
    return text or "上游渠道接口调用失败"


def newapi_auth_failure_message(payload: Any, error: Optional[str] = None) -> str:
    """Return an actionable auth failure without exposing credentials."""
    raw_message = _upstream_response_message(payload, error)
    text = f"{raw_message} {error or ''}".lower()
    if "invalid access token" in text or "access token invalid" in text:
        return "令牌无效或已失效，请重新生成并录入普通用户系统访问令牌"
    if "invalid username" in text or "invalid password" in text or "password incorrect" in text:
        return "用户名或密码错误"
    if "require_2fa" in text or "2fa" in text or "two-factor" in text:
        return "需要 2FA 验证码"
    if "connection reset by peer" in text:
        return "上游重置了 Python 连接，已尝试兼容传输；如仍失败请改用浏览器登录态"
    return raw_message or str(error or "上游认证失败")
