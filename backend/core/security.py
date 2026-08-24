"""Console authentication dependencies."""

from __future__ import annotations

import hashlib
import re
import secrets
import time

from http.server import BaseHTTPRequestHandler

from fastapi import HTTPException, Request

from backend.core.config import CONSOLE_PASSWORD, CONSOLE_SESSION_TTL_SECONDS
from backend.core.state import CONSOLE_SESSIONS, CONSOLE_SESSIONS_LOCK


# 无需登录即可访问的 API：状态查询与登录/登出本身
PUBLIC_API_PATHS = {"/api/auth/login", "/api/auth/logout", "/api/auth/status"}


def hash_session_sync_secret(secret: str) -> str:
    return hashlib.sha256(str(secret or "").encode("utf-8")).hexdigest()


def is_public_api_path(path: str) -> bool:
    if path in PUBLIC_API_PATHS:
        return True
    return bool(
        re.fullmatch(
            r"/api/session-sync-requests/[A-Za-z0-9_-]{1,64}/complete",
            str(path or ""),
        )
    )


def console_auth_enabled() -> bool:
    return bool(CONSOLE_PASSWORD)


def create_console_session() -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with CONSOLE_SESSIONS_LOCK:
        CONSOLE_SESSIONS[token] = now + CONSOLE_SESSION_TTL_SECONDS
        # 顺手清理过期会话，避免内存无限增长
        for stale in [t for t, exp in CONSOLE_SESSIONS.items() if exp < now]:
            CONSOLE_SESSIONS.pop(stale, None)
    return token


def console_session_valid(token: str) -> bool:
    token = (token or "").strip()
    if not token:
        return False
    with CONSOLE_SESSIONS_LOCK:
        exp = CONSOLE_SESSIONS.get(token)
        if exp is None:
            return False
        if exp < time.time():
            CONSOLE_SESSIONS.pop(token, None)
            return False
        return True


def drop_console_session(token: str) -> None:
    with CONSOLE_SESSIONS_LOCK:
        CONSOLE_SESSIONS.pop((token or "").strip(), None)


def bearer_token(request: Request) -> str:
    raw = (request.headers.get("Authorization") or "").strip()
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return raw


def request_bearer_token(handler: BaseHTTPRequestHandler) -> str:
    raw = (handler.headers.get("Authorization") or "").strip()
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return raw


def console_authenticated(request: Request) -> bool:
    """无密码时视为始终通过；否则要求携带有效会话 token。"""
    if not console_auth_enabled():
        return True
    return console_session_valid(bearer_token(request))


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
