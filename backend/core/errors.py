"""HTTP error handlers preserving the existing JSON envelope."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class DatabasePoolTimeoutError(TimeoutError):
    """连接池在限定时间内没有可用连接。"""


def _json_error(message: str, status_code: int, code: str | None = None, **extra: Any) -> JSONResponse:
    payload: dict[str, Any] = {"success": False, "message": message}
    if code:
        payload["code"] = code
    payload.update(extra)
    return JSONResponse(payload, status_code=status_code)


async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict):
        return JSONResponse(exc.detail, status_code=exc.status_code, headers=exc.headers)
    return _json_error(str(exc.detail), exc.status_code)


async def validation_exception_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    return _json_error("请求参数无效", 422, "validation_error", details=exc.errors())


async def database_busy_handler(_request: Request, _exc: DatabasePoolTimeoutError) -> JSONResponse:
    return _json_error("数据库连接池繁忙，请稍后重试", 503, "database_busy")
