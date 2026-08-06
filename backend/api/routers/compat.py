"""Fallback for legacy-compatible endpoints not yet specialized above."""

from __future__ import annotations

from fastapi import APIRouter, Request

from backend.api.routers.common import forward_request


router = APIRouter()


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
)
async def legacy_compatible_endpoint(request: Request):
    return await forward_request(request)
