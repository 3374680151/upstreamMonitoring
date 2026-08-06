"""Admin-site and channel management routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from backend.api.routers.common import forward_request


router = APIRouter()


@router.api_route(
    "/admin/sites",
    methods=["GET", "POST"],
)
@router.api_route(
    "/admin/sites/{admin_site_id}",
    methods=["PUT", "DELETE"],
)
@router.api_route(
    "/admin/sites/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE"],
)
async def admin_site_request(request: Request):
    return await forward_request(request)
