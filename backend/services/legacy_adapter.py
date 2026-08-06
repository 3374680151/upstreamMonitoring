"""Run the legacy business dispatcher behind an ASGI request boundary.

The existing domain functions contain a large amount of carefully tuned
NewAPI/sub2api compatibility behavior.  This adapter lets the transport layer
move to FastAPI without duplicating those branches during the migration.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from email.message import Message
from http import HTTPStatus
from typing import Any, Mapping

from backend import legacy_runtime as legacy


@dataclass
class LegacyResponse:
    status_code: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


class _RequestHeaders(Message):
    """Case-insensitive header mapping compatible with BaseHTTPRequestHandler."""


class _ASGIHandler(legacy.Handler):
    def __init__(self, method: str, target: str, headers: Mapping[str, str], body: bytes) -> None:
        self.command = method
        self.path = target
        self.headers = _RequestHeaders()
        for key, value in headers.items():
            self.headers[key] = value
        if self.headers.get("Content-Length") is None:
            self.headers["Content-Length"] = str(len(body))
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self._response = LegacyResponse()

    def send_response(self, code: int, _message: str | None = None) -> None:
        self._response.status_code = int(code)

    def send_header(self, key: str, value: str) -> None:
        self._response.headers[str(key)] = str(value)

    def end_headers(self) -> None:
        return

    def send_error(self, code: int, message: str | None = None, *_args: Any) -> None:
        status = int(code)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        payload = {"success": False, "message": message or HTTPStatus(status).phrase}
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def log_message(self, _fmt: str, *_args: Any) -> None:
        return


def dispatch_legacy_request(
    method: str,
    target: str,
    headers: Mapping[str, str],
    body: bytes = b"",
) -> LegacyResponse:
    handler = _ASGIHandler(method, target, headers, body)
    method_name = f"do_{method.upper()}"
    callback = getattr(handler, method_name, None)
    if callback is None:
        handler.send_error(405, "method not allowed")
    else:
        callback()
    response = handler._response
    response.body = handler.wfile.getvalue()
    if "Content-Length" not in response.headers:
        response.headers["Content-Length"] = str(len(response.body))
    return response
