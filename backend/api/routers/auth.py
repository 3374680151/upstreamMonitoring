"""Console authentication endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Request
from backend.core import console_auth
from backend.core.security import console_authenticated
from backend.services.auth_service import AuthService


router = APIRouter()
service = AuthService()


@router.get("/auth/status")
def auth_status(request: Request) -> dict[str, object]:
    return {
        "success": True,
        "auth_required": console_auth.enabled(),
        "authenticated": console_authenticated(request),
    }


@router.post("/auth/login")
def login(payload: dict = None):
    body = payload or {}
    return service.login(str(body.get("password") or ""))


@router.post("/auth/logout")
def logout(request: Request):
    token = (request.headers.get("Authorization") or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return service.logout(token)
