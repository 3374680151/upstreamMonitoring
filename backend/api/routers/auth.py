"""Console authentication endpoints."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.core.config import CONSOLE_PASSWORD
from backend.core.security import (
    bearer_token,
    console_auth_enabled,
    console_authenticated,
    create_console_session,
    drop_console_session,
)


router = APIRouter()


@router.get("/auth/status")
def auth_status(request: Request) -> dict[str, object]:
    return {
        "success": True,
        "auth_required": console_auth_enabled(),
        "authenticated": console_authenticated(request),
    }


@router.post("/auth/login")
async def login(request: Request):
    # Public path: require_console_auth lets it through via PUBLIC_API_PATHS.
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    if not console_auth_enabled():
        return JSONResponse(
            {"success": True, "auth_required": False, "token": ""}
        )

    password = str(body.get("password") or "")
    # Compare on UTF-8 bytes: secrets.compare_digest raises TypeError on
    # non-ASCII strings, which would lock out pure-Chinese passwords.
    if not password or not secrets.compare_digest(
        password.encode("utf-8"), CONSOLE_PASSWORD.encode("utf-8")
    ):
        return JSONResponse(
            {"success": False, "message": "密码错误"}, status_code=401
        )
    token = create_console_session()
    return JSONResponse({"success": True, "token": token})


@router.post("/auth/logout")
async def logout(request: Request):
    drop_console_session(bearer_token(request))
    return JSONResponse({"success": True})
