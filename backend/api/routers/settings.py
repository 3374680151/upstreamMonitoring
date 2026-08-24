"""Application settings routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from backend.services.monitoring_service import (
    RECONCILE_MODES,
    SETTING_RECONCILE_MODE,
    get_main_site_reconcile_mode,
    set_app_setting,
)


router = APIRouter()


@router.get("/settings")
def settings():
    return {
        "success": True,
        "data": {
            SETTING_RECONCILE_MODE: get_main_site_reconcile_mode(),
        },
    }


@router.put("/settings")
async def update_settings(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    mode = str(body.get(SETTING_RECONCILE_MODE) or "").strip().lower()
    if mode not in RECONCILE_MODES:
        return JSONResponse(
            {"success": False, "message": "reconcile mode 无效"}, status_code=400
        )
    await run_in_threadpool(set_app_setting, SETTING_RECONCILE_MODE, mode)
    return JSONResponse(
        {
            "success": True,
            "data": {SETTING_RECONCILE_MODE: get_main_site_reconcile_mode()},
        }
    )
