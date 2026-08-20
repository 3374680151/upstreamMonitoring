"""Email, WeCom, and notification history routes."""
from __future__ import annotations

from fastapi import APIRouter
from backend.services.notification_service import NotificationService


router = APIRouter()
service = NotificationService()


@router.get("/notifications/settings")
def notification_settings():
    return {"data": service.settings_payload()}


@router.get("/notifications/logs")
def notification_logs():
    return {"data": service.logs()}


@router.put("/notifications/settings")
def update_notification_settings(payload: dict = None):
    return service.update_settings(payload or {})


@router.post("/notifications/test-email")
def test_email(payload: dict = None):
    return service.test_email(payload or {})


@router.post("/notifications/test-wecom")
def test_wecom(payload: dict = None):
    return service.test_wecom(payload or {})
