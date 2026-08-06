"""Email, WeCom, and notification history routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from backend.api.routers.common import forward_request
from backend.services.notification_service import NotificationService


router = APIRouter()
service = NotificationService()


@router.get("/notifications/settings")
def notification_settings():
    return {"data": service.settings_payload()}


@router.get("/notifications/logs")
def notification_logs():
    return {"data": service.logs()}


@router.api_route(
    "/notifications/settings",
    methods=["PUT"],
)
@router.api_route(
    "/notifications/test-email",
    methods=["POST"],
)
@router.api_route(
    "/notifications/test-wecom",
    methods=["POST"],
)
async def notification_mutation(request: Request):
    return await forward_request(request)
