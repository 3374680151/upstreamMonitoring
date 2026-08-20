"""FastAPI exception handlers preserving the project's JSON envelope."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.db.pool import DatabasePoolTimeoutError
from backend.core.errors import DomainError


def _json_error(
    message: str,
    status_code: int,
    code: str | None = None,
    **extra: Any,
) -> JSONResponse:
    payload: dict[str, Any] = {"success": False, "message": message}
    if code:
        payload["code"] = code
    payload.update(extra)
    return JSONResponse(payload, status_code=status_code)


async def domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(
        jsonable_encoder(exc.to_envelope()),
        status_code=exc.status_code,
    )


async def http_exception_handler(
    _request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    if isinstance(exc.detail, dict):
        return JSONResponse(
            exc.detail,
            status_code=exc.status_code,
            headers=exc.headers,
        )
    return _json_error(str(exc.detail), exc.status_code)


async def validation_exception_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    return _json_error(
        "请求参数无效",
        422,
        "validation_error",
        details=jsonable_encoder(exc.errors()),
    )


async def database_busy_handler(
    _request: Request, _exc: DatabasePoolTimeoutError
) -> JSONResponse:
    return _json_error("数据库连接池繁忙，请稍后重试", 503, "database_busy")


async def unhandled_exception_handler(
    _request: Request, _exc: Exception
) -> JSONResponse:
    """Keep unexpected failures JSON-shaped without leaking credentials/SQL."""
    return _json_error("服务器内部错误", 500, "internal_error")
