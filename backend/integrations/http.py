"""HTTP transport primitives for upstream site requests.

Moved out of ``backend.legacy_runtime`` so the FastAPI boundary can import the
curl fallback, JSON request helpers, and upstream error translators without
pulling in the whole legacy runtime.  The legacy runtime re-exports every name
below for backward compatibility.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from backend.core.config import HTTP_TIMEOUT_SECONDS
from backend.core.normalize import _cookie_header_from_response, _url_origin


def open_upstream_url(
    request: urllib.request.Request,
    timeout: float = HTTP_TIMEOUT_SECONDS,
    *handlers: Any,
):
    """Use the OS network route without application-level HTTP proxies."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), *handlers)
    try:
        return opener.open(request, timeout=timeout)
    except urllib.error.URLError as exc:
        if not _is_connection_reset_by_peer(exc):
            raise
        return _open_upstream_url_with_curl(request, timeout)


class _CurlResponse:
    """Small urllib-compatible response for the connection-reset fallback."""

    def __init__(self, body: bytes, headers: EmailMessage, status: int) -> None:
        self._body = io.BytesIO(body)
        self.headers = headers
        self.status = status

    def read(self, amount: int = -1) -> bytes:
        return self._body.read(amount)

    def __enter__(self) -> "_CurlResponse":
        return self

    def __exit__(self, *_args: Any) -> None:
        self._body.close()


def _is_connection_reset_by_peer(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, ConnectionResetError) or "Connection reset by peer" in str(reason)


def _curl_config_value(value: Any) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", "")


def _curl_headers_from_dump(raw: bytes) -> Tuple[int, EmailMessage]:
    blocks = [block for block in re.split(br"\r?\n\r?\n", raw) if block.strip()]
    block = blocks[-1] if blocks else b""
    lines = block.splitlines()
    if not lines:
        return 0, EmailMessage()
    match = re.search(br"\s(\d{3})\s", lines[0])
    status = int(match.group(1)) if match else 0
    headers = EmailMessage()
    for raw_line in lines[1:]:
        if b":" not in raw_line:
            continue
        key, value = raw_line.split(b":", 1)
        headers.add_header(
            key.decode("iso-8859-1", errors="replace").strip(),
            value.decode("iso-8859-1", errors="replace").strip(),
        )
    return status, headers


def _open_upstream_url_with_curl(
    request: urllib.request.Request, timeout: float
) -> _CurlResponse:
    """Retry only TLS-fingerprint-sensitive sites through the system curl client.

    Some Cloudflare-fronted NewAPI deployments reset stdlib TLS clients while
    accepting the same HTTP request from curl. Credentials stay in a 0700 temp
    directory and are never passed as process arguments.
    """
    with tempfile.TemporaryDirectory(prefix="upstream-curl-") as temp_dir:
        directory = Path(temp_dir)
        config_path = directory / "request.conf"
        body_path = directory / "response.bin"
        headers_path = directory / "headers.txt"
        request_body_path = directory / "request.bin"
        lines = [
            "silent",
            "show-error",
            f'max-time = "{max(1, int(timeout))}"',
            f'request = "{_curl_config_value(request.get_method())}"',
            f'url = "{_curl_config_value(request.full_url)}"',
            f'output = "{_curl_config_value(body_path)}"',
            f'dump-header = "{_curl_config_value(headers_path)}"',
        ]
        for key, value in request.header_items():
            lines.append(
                f'header = "{_curl_config_value(f"{key}: {value}")}"'
            )
        if request.data is not None:
            request_body_path.write_bytes(request.data)
            lines.append(f'data-binary = "@{_curl_config_value(request_body_path)}"')
        config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            result = subprocess.run(
                ["curl", "--config", str(config_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=max(2, int(timeout) + 3),
                check=False,
            )
        except FileNotFoundError as exc:
            raise urllib.error.URLError("上游重置连接，且系统未安装 curl 兼容客户端") from exc
        except subprocess.TimeoutExpired as exc:
            raise urllib.error.URLError("curl 兼容请求超时") from exc
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise urllib.error.URLError(f"curl 兼容请求失败：{detail or result.returncode}")
        body = body_path.read_bytes() if body_path.exists() else b""
        status, headers = _curl_headers_from_dump(
            headers_path.read_bytes() if headers_path.exists() else b""
        )
        if status >= 400:
            raise urllib.error.HTTPError(
                request.full_url,
                status,
                f"HTTP {status}",
                headers,
                io.BytesIO(body),
            )
        if not status:
            raise urllib.error.URLError("curl 兼容请求没有返回有效 HTTP 状态")
        return _CurlResponse(body, headers, status)


def json_request(
    url: str,
    payload: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
    method: str = "POST",
) -> Tuple[int, Dict[str, Any], str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Upstream-Ratio-Watch/1.0",
    }
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    with open_upstream_url(req) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        try:
            payload_obj = json.loads(raw) if raw else {}
        except Exception:
            payload_obj = {"raw": raw}
        if not isinstance(payload_obj, dict):
            payload_obj = {"raw": raw}
        return resp.status, payload_obj, raw


def request_json(url: str, headers: Optional[Dict[str, str]] = None, payload: Optional[Dict[str, Any]] = None, method: str = "GET") -> Tuple[bool, Any, Optional[str]]:
    data = None
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "Upstream-Ratio-Watch/1.0",
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with open_upstream_url(req) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(body) if body else {}
            return True, parsed, None
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        return False, {"status": exc.code, "raw": raw}, f"HTTP {exc.code}"
    except Exception as exc:
        return False, {"error": str(exc)}, str(exc)


class SameOriginAdminRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Prevent administrator credentials from following cross-Origin redirects."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            same_origin = _url_origin(req.full_url) == _url_origin(newurl)
        except (TypeError, ValueError):
            same_origin = False
        if not same_origin:
            raise urllib.error.HTTPError(
                newurl, 403, "跨 Origin 跳转已拒绝", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def admin_request_json(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    payload: Optional[Dict[str, Any]] = None,
    method: str = "GET",
) -> Tuple[bool, Any, Optional[str]]:
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "Upstream-Ratio-Watch/1.0",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(
        url, data=data, headers=request_headers, method=method
    )
    try:
        with open_upstream_url(req, HTTP_TIMEOUT_SECONDS, SameOriginAdminRedirectHandler()) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return True, json.loads(body) if body else {}, None
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        return False, {"status": exc.code, "raw": raw}, f"HTTP {exc.code}"
    except Exception as exc:
        return False, {"error": str(exc)}, str(exc)


def request_json_with_headers(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    payload: Optional[Dict[str, Any]] = None,
    method: str = "GET",
) -> Tuple[bool, Any, Optional[str], Dict[str, Any]]:
    """请求 JSON，同时保留响应头，供网页登录态捕获 Set-Cookie 使用。"""
    data = None
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "Upstream-Ratio-Watch/1.0",
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with open_upstream_url(req) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(body) if body else {}
            response_headers: Dict[str, Any] = {
                "set-cookie": resp.headers.get_all("Set-Cookie") or [],
            }
            return True, parsed, None, response_headers
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        response_headers = {"set-cookie": exc.headers.get_all("Set-Cookie") or []}
        return False, {"status": exc.code, "raw": raw}, f"HTTP {exc.code}", response_headers
    except Exception as exc:
        return False, {"error": str(exc)}, str(exc), {"set-cookie": []}


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
