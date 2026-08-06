"""Browser session synchronization routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from backend.api.routers.common import forward_request


router = APIRouter()


@router.api_route(
    "/session-sync/requests/{request_id}/complete",
    methods=["POST"],
)
@router.api_route(
    "/sites/{site_id}/session-sync/requests",
    methods=["POST"],
)
@router.api_route(
    "/sites/{site_id}/session-sync/requests/{request_id}",
    methods=["GET"],
)
@router.api_route(
    "/sites/{site_id}/session-sync/requests/{request_id}/fail",
    methods=["POST"],
)
async def session_sync(request: Request):
    return await forward_request(request)
