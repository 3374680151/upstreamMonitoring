"""Console authentication endpoints."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Request

from backend import legacy_runtime as legacy
from backend.api.routers.common import forward_request
from backend.core.security import bearer_token, console_authenticated


router = APIRouter()


@router.get("/auth/status")
def auth_status(request: Request) -> dict[str, object]:
    return {
        "success": True,
        "auth_required": legacy.console_auth_enabled(),
        "authenticated": console_authenticated(request),
    }


@router.post("/auth/login")
async def login(request: Request):
    # Keep the existing password comparison and response semantics in one place.
    return await forward_request(request)


@router.post("/auth/logout")
async def logout(request: Request):
    return await forward_request(request)
