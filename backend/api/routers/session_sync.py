"""Browser session synchronization routes."""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from backend.core.state import SESSION_SYNC_MAX_BODY_BYTES
from backend.services.session_sync_service import (
    complete_session_sync_request,
    create_site_session_sync_request,
    fail_site_session_sync_request,
    get_site_session_sync_request,
    share_site_browser_session,
)


router = APIRouter()


def _parse_bounded_sync_body(body_bytes: bytes) -> tuple[bool, object, int]:
    """Validate the session-sync completion payload size + JSON shape.

    Mirrors the legacy ``read_bounded_json_body`` contract: oversized bodies
    map to 413, malformed JSON maps to 400. Returns (ok, body, status).
    """
    if len(body_bytes) > SESSION_SYNC_MAX_BODY_BYTES:
        return False, None, 413
    try:
        body = json.loads(body_bytes) if body_bytes else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False, None, 400
    return True, body, 200


def _sync_body_error(status: int) -> JSONResponse:
    if status == 413:
        message = "同步请求体过大"
    else:
        message = "同步请求 JSON 无效"
    return JSONResponse(
        {
            "success": False,
            "status": "failed",
            "code": "SYNC_BODY_INVALID",
            "message": message,
        },
        status_code=status,
    )


@router.post("/session-sync/requests/{request_id}/complete")
async def complete_session_sync(request: Request, request_id: str):
    body_bytes = await request.body()
    ok, body, status = _parse_bounded_sync_body(body_bytes)
    if not ok:
        return _sync_body_error(status)
    secret = str(request.headers.get("X-Upstream-Sync-Token") or "")
    response_status, payload = await run_in_threadpool(
        complete_session_sync_request, request_id, secret, body
    )
    return JSONResponse(payload, status_code=response_status)


@router.post("/sites/{site_id}/session-sync/requests")
async def create_session_sync_request(site_id: int):
    ok, payload, error = await run_in_threadpool(
        create_site_session_sync_request, site_id
    )
    if not ok:
        status_code = 404 if error == "渠道不存在" else 400
        return JSONResponse(
            {"success": False, "message": error}, status_code=status_code
        )
    return JSONResponse(
        {"success": True, "data": payload}, status_code=201
    )


@router.get("/sites/{site_id}/session-sync/requests/{request_id}")
async def get_session_sync_request(site_id: int, request_id: str):
    payload = await run_in_threadpool(
        get_site_session_sync_request, site_id, request_id
    )
    if payload is None:
        return JSONResponse(
            {"success": False, "message": "同步请求不存在"}, status_code=404
        )
    return JSONResponse({"success": True, "data": payload})


@router.post("/sites/{site_id}/session-sync/share")
async def share_session_sync(site_id: int):
    """同注册域兄弟站点登录态复用：命中则直接落库返回 ready。"""
    payload = await run_in_threadpool(share_site_browser_session, site_id)
    return JSONResponse({"success": True, "data": payload})


@router.post("/sites/{site_id}/session-sync/requests/{request_id}/fail")
async def fail_session_sync_request(
    site_id: int, request_id: str, request: Request
):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    ok, error = await run_in_threadpool(
        fail_site_session_sync_request,
        site_id,
        request_id,
        str(body.get("code") or ""),
    )
    if not ok:
        return JSONResponse(
            {"success": False, "message": error}, status_code=400
        )
    return JSONResponse({"success": True})
