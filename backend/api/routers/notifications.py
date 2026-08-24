"""Email, WeCom, and notification history routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from backend.integrations.email import send_email_message
from backend.integrations.wecom import send_wecom_message
from backend.repositories.notifications import (
    notification_settings_payload,
    update_notification_settings,
)
from backend.services.notification_service import NotificationService


router = APIRouter()
service = NotificationService()


async def _read_json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        body = {}
    return body if isinstance(body, dict) else {}


@router.get("/notifications/settings")
def notification_settings():
    return {"data": service.settings_payload()}


@router.get("/notifications/logs")
def notification_logs():
    return {"data": service.logs()}


@router.put("/notifications/settings")
async def update_notification_settings_route(request: Request):
    body = await _read_json_body(request)
    try:
        await run_in_threadpool(update_notification_settings, body)
    except ValueError as exc:
        return JSONResponse(
            {"success": False, "message": str(exc)}, status_code=400
        )
    return JSONResponse(
        {"success": True, "data": notification_settings_payload()}
    )


@router.post("/notifications/test-email")
async def test_email(request: Request):
    body = await _read_json_body(request)
    try:
        if body:
            await run_in_threadpool(update_notification_settings, body)
        ok, error_message = await run_in_threadpool(
            send_email_message,
            "上游倍率监控邮箱测试",
            "这是一封上游分组倍率监控测试邮件。",
        )
    except Exception as exc:
        return JSONResponse(
            {"success": False, "message": str(exc)}, status_code=500
        )
    return JSONResponse(
        {"success": ok, "message": error_message or "测试邮件已发送"}
    )


@router.post("/notifications/test-wecom")
async def test_wecom(request: Request):
    body = await _read_json_body(request)
    try:
        if body:
            await run_in_threadpool(update_notification_settings, body)
        ok, error_message = await run_in_threadpool(
            send_wecom_message,
            "上游倍率监控企业微信测试",
            "这是一条上游分组倍率监控测试消息。",
        )
    except Exception as exc:
        return JSONResponse(
            {"success": False, "message": str(exc)}, status_code=500
        )
    return JSONResponse(
        {"success": ok, "message": error_message or "测试消息已发送"}
    )
