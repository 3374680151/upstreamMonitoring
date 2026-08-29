"""Application settings routes."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from backend.api.schemas.settings import SettingsPatchRequest
from backend.services.monitoring_service import (
    RECONCILE_MODES,
    SETTING_RECONCILE_MODE,
    get_main_site_reconcile_mode,
    set_app_setting,
)


router = APIRouter()


def _settings_payload() -> dict:
    return {
        SETTING_RECONCILE_MODE: get_main_site_reconcile_mode(),
    }


@router.get("/settings")
def settings():
    return {"success": True, "data": _settings_payload()}


@router.put("/settings")
async def update_settings(patch: SettingsPatchRequest):
    updates = patch.model_dump(exclude_none=True, exclude_unset=True)
    if not updates:
        return JSONResponse(
            {"success": False, "message": "缺少要更新的设置项"}, status_code=400
        )

    if SETTING_RECONCILE_MODE in updates:
        mode = str(updates[SETTING_RECONCILE_MODE] or "").strip().lower()
        if mode not in RECONCILE_MODES:
            return JSONResponse(
                {"success": False, "message": "reconcile mode 无效"}, status_code=400
            )
        await run_in_threadpool(set_app_setting, SETTING_RECONCILE_MODE, mode)

    return JSONResponse({"success": True, "data": _settings_payload()})
