"""Admin-site management routes (CRUD).

Channel-level management under ``/admin/sites/{id}/...`` (channels, groups,
channel-mappings, key refresh, batch ops, ...) is intentionally not wired
here: those sub-paths have no clean service boundary yet and continue to be
served by the legacy dispatcher via the ``compat`` catch-all router, which
preserves their behavior unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from backend.db.connection import db_execute
from backend.integrations.sub2api import (
    Sub2ApiUpstreamError,
    sub2api_proxy_error_response,
)
from backend.repositories.admin_sites import (
    create_admin_site,
    list_admin_sites_payload,
    update_admin_site,
)


router = APIRouter()


async def _read_json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        body = {}
    return body if isinstance(body, dict) else {}


def _delete_admin_site(admin_site_id: int) -> None:
    # Mirrors the legacy DELETE /api/admin/sites/:id cascade: clear the
    # channel-key cache + upstream bindings before removing the site row.
    db_execute(
        "DELETE FROM channel_upstream_bindings WHERE admin_site_id = ?",
        (admin_site_id,),
    )
    db_execute(
        "DELETE FROM admin_channel_keys WHERE admin_site_id = ?",
        (admin_site_id,),
    )
    db_execute("DELETE FROM admin_sites WHERE id = ?", (admin_site_id,))


@router.get("/admin/sites")
async def list_admin_sites():
    data = await run_in_threadpool(list_admin_sites_payload)
    return JSONResponse({"data": data})


@router.post("/admin/sites")
async def create_admin_site_route(request: Request):
    body = await _read_json_body(request)
    ok, result, error = await run_in_threadpool(create_admin_site, body)
    if not ok:
        if isinstance(error, Sub2ApiUpstreamError):
            status, response = sub2api_proxy_error_response(
                error.payload, str(error), "sub2api 主站登录验证失败"
            )
            return JSONResponse(response, status_code=status)
        return JSONResponse(
            {"success": False, "message": error}, status_code=400
        )
    return JSONResponse({"success": True, "id": result})


@router.put("/admin/sites/{admin_site_id}")
async def update_admin_site_route(admin_site_id: int, request: Request):
    body = await _read_json_body(request)
    ok, error = await run_in_threadpool(update_admin_site, admin_site_id, body)
    if not ok:
        if isinstance(error, Sub2ApiUpstreamError):
            status, response = sub2api_proxy_error_response(
                error.payload, str(error), "sub2api 主站登录验证失败"
            )
            return JSONResponse(response, status_code=status)
        status_code = (
            409
            if error and "平台" in error and "不可修改" in error
            else 400
        )
        return JSONResponse(
            {"success": False, "message": error}, status_code=status_code
        )
    return JSONResponse({"success": True})


@router.delete("/admin/sites/{admin_site_id}")
async def delete_admin_site_route(admin_site_id: int):
    await run_in_threadpool(_delete_admin_site, admin_site_id)
    return JSONResponse({"success": True})
