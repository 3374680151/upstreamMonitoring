"""Console authentication dependencies."""

from __future__ import annotations

import re

from fastapi import HTTPException, Request

from backend import legacy_runtime as legacy


def bearer_token(request: Request) -> str:
    raw = (request.headers.get("Authorization") or "").strip()
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return raw


def is_public_api_path(path: str) -> bool:
    if path in legacy.PUBLIC_API_PATHS:
        return True
    return bool(
        re.fullmatch(
            r"/api/session-sync/requests/[A-Za-z0-9_-]{1,64}/complete",
            str(path or ""),
        )
    )


def console_authenticated(request: Request) -> bool:
    if not legacy.console_auth_enabled():
        return True
    return legacy.console_session_valid(bearer_token(request))


async def require_console_auth(request: Request) -> None:
    """Protect API routes while keeping the legacy public endpoints public."""
    path = request.url.path
    if not path.startswith("/api/") or is_public_api_path(path):
        return
    if console_authenticated(request):
        return
    raise HTTPException(
        status_code=401,
        detail={
            "success": False,
            "message": "未登录或会话已过期",
            "code": "unauthorized",
        },
    )
