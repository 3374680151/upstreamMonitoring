"""Small standard-library JSON transport for upstream integrations.

The integration layer must not depend on the compatibility runtime just to
issue an HTTP request.  This module owns the common request behavior used by
the NewAPI and sub2api protocol clients: proxy-free urllib access, JSON body
encoding/decoding, same-origin admin redirects, and bounded error envelopes.

No request or response is logged here.  In particular, authorization headers
and upstream response bodies are never written to a file by this module.
"""

from __future__ import annotations

import json
import io
import os
import subprocess
import urllib.error
import urllib.request
from email.message import EmailMessage
from typing import Any, Optional


USER_AGENT = "Upstream-Ratio-Watch/1.0"


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


HTTP_TIMEOUT_SECONDS = _env_int("UPSTREAM_HTTP_TIMEOUT", 15, 1, 120)


def normalize_base_url(value: str) -> str:
    """Return the canonical base URL used by protocol clients."""
    return str(value or "").strip().rstrip("/")


def _url_origin(value: str) -> tuple[str, Optional[str], Optional[int]]:
    from urllib.parse import urlparse

    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    port = parsed.port
    if port is None:
        port = 443 if scheme == "https" else 80 if scheme == "http" else None
    return scheme, parsed.hostname, port


class SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Do not carry administrator credentials across origins."""

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


class _CurlResponse:
    """Small urllib-compatible response for TLS fingerprint fallbacks."""

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


def _open_upstream_url_with_curl(
    request: urllib.request.Request, timeout: float
) -> _CurlResponse:
    """Retry TLS-fingerprint-sensitive sites through system curl.

    The curl config and request body are sent through stdin and never written to
    disk or passed as command-line arguments, so credentials do not appear in
    process listings, logs, or temporary files.
    """
    marker = "__UPSTREAM_CURL_STATUS_7E4A__"
    lines = [
        "silent",
        "show-error",
        "noproxy = \"*\"",
        f'max-time = "{max(1, int(timeout))}"',
        f'request = "{_curl_config_value(request.get_method())}"',
        f'url = "{_curl_config_value(request.full_url)}"',
        "output = \"-\"",
        f'write-out = "\\n{marker}%{{http_code}}"',
    ]
    for key, value in request.header_items():
        header_value = _curl_config_value(f"{key}: {value}")
        lines.append(f'header = "{header_value}"')
    if request.data is not None:
        try:
            body_text = request.data.decode("utf-8")
        except (AttributeError, UnicodeDecodeError) as exc:
            raise urllib.error.URLError("上游连接重置，curl 兼容请求体不是 UTF-8") from exc
        lines.append(f'data-raw = "{_curl_config_value(body_text)}"')
    config = ("\n".join(lines) + "\n").encode("utf-8")
    try:
        result = subprocess.run(
            ["curl", "--config", "-"],
            input=config,
            stdout=subprocess.PIPE,
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
    raw_output = result.stdout
    marker_bytes = marker.encode("ascii")
    marker_index = raw_output.rfind(marker_bytes)
    if marker_index < 0:
        raise urllib.error.URLError("curl 兼容请求没有返回有效 HTTP 状态")
    body = raw_output[:marker_index].rstrip(b"\r\n")
    status_text = raw_output[marker_index + len(marker_bytes) :].strip()
    try:
        status = int(status_text)
    except (TypeError, ValueError) as exc:
        raise urllib.error.URLError("curl 兼容请求返回无效 HTTP 状态") from exc
    headers = EmailMessage()
    if status >= 400:
        raise urllib.error.HTTPError(
            request.full_url, status, f"HTTP {status}", headers, io.BytesIO(body)
        )
    return _CurlResponse(body, headers, status)


def open_upstream_url(
    request: urllib.request.Request,
    timeout: float = HTTP_TIMEOUT_SECONDS,
    admin: bool = False,
):
    """Open an upstream URL without inheriting application proxy settings."""
    handlers: list[Any] = [urllib.request.ProxyHandler({})]
    if admin:
        handlers.append(SameOriginRedirectHandler())
    opener = urllib.request.build_opener(*handlers)
    try:
        return opener.open(request, timeout=timeout)
    except urllib.error.URLError as exc:
        if not _is_connection_reset_by_peer(exc):
            raise
        return _open_upstream_url_with_curl(request, timeout)


def _parse_body(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace")
    if not text:
        return {}
    return json.loads(text)


def request_json(
    url: str,
    headers: Optional[dict[str, str]] = None,
    payload: Any = None,
    method: str = "GET",
    *,
    admin: bool = False,
) -> tuple[bool, Any, Optional[str]]:
    """Issue a JSON request and preserve the legacy ``(ok, payload, error)`` API."""
    request_headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    if headers:
        request_headers.update({str(key): str(value) for key, value in headers.items()})
    request = urllib.request.Request(
        url, data=data, headers=request_headers, method=str(method or "GET").upper()
    )
    try:
        with open_upstream_url(request, admin=admin) as response:
            try:
                parsed = _parse_body(response.read())
            except (TypeError, ValueError) as exc:
                return False, {"error": str(exc)}, str(exc)
            return True, parsed, None
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
        except Exception:
            raw = b""
        return False, {"status": exc.code, "raw": raw.decode("utf-8", errors="replace")}, f"HTTP {exc.code}"
    except Exception as exc:
        return False, {"error": str(exc)}, str(exc)


def request_json_with_headers(
    url: str,
    headers: Optional[dict[str, str]] = None,
    payload: Any = None,
    method: str = "GET",
    *,
    admin: bool = False,
) -> tuple[bool, Any, Optional[str], dict[str, Any]]:
    """Issue a JSON request and retain response ``Set-Cookie`` headers.

    Browser-session login and refresh are the only callers that need response
    headers.  The normal ``request_json`` path stays unchanged for all other
    integrations.
    """
    request_headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    if headers:
        request_headers.update({str(key): str(value) for key, value in headers.items()})
    request = urllib.request.Request(
        url, data=data, headers=request_headers, method=str(method or "GET").upper()
    )

    def response_headers(response: Any) -> dict[str, Any]:
        header_map = getattr(response, "headers", None)
        if header_map is None:
            return {"set-cookie": []}
        try:
            values = header_map.get_all("Set-Cookie") or []
        except Exception:
            values = []
        return {"set-cookie": values}

    try:
        with open_upstream_url(request, admin=admin) as response:
            try:
                parsed = _parse_body(response.read())
            except (TypeError, ValueError) as exc:
                return False, {"error": str(exc)}, str(exc), response_headers(response)
            return True, parsed, None, response_headers(response)
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
        except Exception:
            raw = b""
        return (
            False,
            {"status": exc.code, "raw": raw.decode("utf-8", errors="replace")},
            f"HTTP {exc.code}",
            response_headers(exc),
        )
    except Exception as exc:
        return False, {"error": str(exc)}, str(exc), {"set-cookie": []}


def newapi_auth_headers(access_token: str = "", user_id: str = "") -> dict[str, str]:
    """Build NewAPI console headers without retaining a Bearer prefix."""
    headers: dict[str, str] = {}
    token = str(access_token or "").strip()
    if token:
        headers["Authorization"] = token.removeprefix("Bearer ").removeprefix("bearer ").strip()
    user = str(user_id or "").strip()
    if user:
        headers["New-Api-User"] = user
    return headers


def sub2api_token_headers(access_token: str) -> dict[str, str]:
    token = str(access_token or "").strip()
    if token.lower().startswith("bearer "):
        return {"Authorization": token}
    return {"Authorization": f"Bearer {token}"}


__all__ = [
    "HTTP_TIMEOUT_SECONDS",
    "USER_AGENT",
    "SameOriginRedirectHandler",
    "newapi_auth_headers",
    "normalize_base_url",
    "open_upstream_url",
    "request_json",
    "request_json_with_headers",
    "sub2api_token_headers",
]
