"""Application settings routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from backend import legacy_runtime as legacy
from backend.api.routers.common import forward_request


router = APIRouter()


@router.get("/settings")
def settings():
    return {
        "success": True,
        "data": {
            legacy.SETTING_RECONCILE_MODE: legacy.get_main_site_reconcile_mode(),
        },
    }


@router.put("/settings")
async def update_settings(request: Request):
    return await forward_request(request)
