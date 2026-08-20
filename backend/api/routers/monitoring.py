"""Monitoring and site-facing API routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from backend.services.connection_service import ConnectionService
from backend.services.discovery_service import DiscoveryService
from backend.services.model_service import ModelService
from backend.services.monitoring_service import MonitoringService
from backend.services.admin_site_service import AdminSiteService
from backend.services.site_service import SiteService
from backend.services.sync_service import SyncService


router = APIRouter()
monitoring = MonitoringService()
site = SiteService()
sync = SyncService()
discovery = DiscoveryService()
admin_sites = AdminSiteService()
model_service = ModelService()
connection = ConnectionService()


# ---------------------------------------------------------------------------
# Read-only endpoints (covered by Service directly)
# ---------------------------------------------------------------------------


@router.get("/overview")
def overview() -> dict[str, Any]:
    return monitoring.overview()


@router.get("/sites")
def sites() -> dict[str, Any]:
    data, auto_sync = monitoring.list_sites()
    return {"data": data, "auto_sync": auto_sync}


@router.get("/changes")
def changes(limit: int = 100) -> dict[str, Any]:
    return {"data": monitoring.list_changes(limit)}


@router.get("/sites/{site_id}/snapshots")
def snapshots(site_id: int) -> dict[str, Any]:
    return {"data": monitoring.snapshots(site_id)}


@router.get("/sites/{site_id}/changes")
def site_changes(site_id: int, limit: int = 100) -> dict[str, Any]:
    return {"data": monitoring.list_site_changes(site_id, limit)}


@router.get("/sites/{site_id}/account")
def account(site_id: int):
    site_row = site.get_or_404(site_id)
    return monitoring.account(site_row)


@router.get("/sites/{site_id}/discovery-links")
def discovery_links(site_id: int):
    site.get_or_404(site_id)
    return {"success": True, "data": discovery.links(site_id)}


@router.get("/sites/{site_id}/models")
def site_models(site_id: int):
    return model_service.get_models(site_id)


@router.get("/sites/{site_id}/pricing")
def site_pricing(site_id: int):
    return model_service.get_pricing(site_id)


@router.get("/sites/{site_id}/perf-metrics")
def site_perf_detail(
    site_id: int,
    model_name: str = Query(..., alias="model"),
    hours: float = Query(24.0),
    group: str = Query(""),
):
    return model_service.get_perf_detail(site_id, model_name, hours, group)


@router.get("/sites/{site_id}/perf-metrics/summary")
def site_perf_summary(site_id: int, hours: float = Query(24.0)):
    return model_service.get_perf_summary(site_id, hours)


# ---------------------------------------------------------------------------
# Mutating endpoints
# ---------------------------------------------------------------------------


@router.post("/sites")
def create_site(payload: dict = None):
    return site.create(payload or {})


@router.put("/sites/{site_id}")
def update_site(site_id: int, payload: dict = None):
    return site.update(site_id, payload or {})


@router.delete("/sites/{site_id}")
def delete_site(site_id: int):
    return site.delete(site_id)


@router.post("/sites/{site_id}/check")
def check_site(site_id: int):
    return site.check(site_id)


@router.post("/sites/sync")
def sync_sites(payload: dict = None):
    admin_site_id = (payload or {}).get("admin_site_id")
    try:
        admin_site_id = int(admin_site_id) if admin_site_id is not None else None
    except (TypeError, ValueError):
        return JSONResponse(
            {"success": False, "message": "管理站点 ID 无效"}, status_code=400
        )
    if admin_site_id is not None and admin_site_id <= 0:
        return JSONResponse(
            {"success": False, "message": "管理站点 ID 无效"}, status_code=400
        )
    return sync.run(admin_site_id)


@router.post("/sites/discovery-import")
def discovery_import(payload: dict = None):
    body = payload or {}
    try:
        admin_site_id = int(body.get("admin_site_id") or 0)
    except (TypeError, ValueError):
        return JSONResponse(
            {"success": False, "message": "管理站点 ID 无效"}, status_code=400
        )
    if admin_site_id <= 0:
        return JSONResponse(
            {"success": False, "message": "管理站点 ID 无效"}, status_code=400
        )
    # This endpoint receives an admin-site id.  Looking it up through
    # SiteService queries the ordinary monitoring ``sites`` table and can
    # accidentally import against an unrelated row with the same id.
    try:
        admin_site_row = admin_sites.get_site(admin_site_id)
    except Exception as exc:
        # Keep the existing JSON error envelope while letting the dedicated
        # admin-site service remain the source of truth for the lookup.
        from backend.core.errors import DomainError

        if isinstance(exc, DomainError):
            return JSONResponse(exc.to_envelope(), status_code=exc.status_code)
        raise
    if str(admin_site_row.get("platform") or "newapi").strip().lower() != "newapi":
        return JSONResponse(
            {
                "success": False,
                "message": "主站渠道发现导入仅支持 NewAPI",
            },
            status_code=405,
        )
    result = discovery.import_sites(admin_site_row, body)
    if isinstance(result, dict) and result.get("error"):
        status = 413 if result.get("error") == "too_many_items" else 400
        return JSONResponse(
            {
                "success": False,
                "message": result.get("message") or "导入请求无效",
                "error": result.get("error"),
            },
            status_code=status,
        )
    return {"success": True, "data": result or []}


@router.post("/check-connection")
def check_connection(payload: dict = None):
    return connection.check_connection(payload or {})


@router.post("/check-login")
def check_login(payload: dict = None):
    return connection.check_login(payload or {})


@router.post("/sites/{site_id}/auth/login")
def password_login(site_id: int, payload: dict = None):
    return site.password_login(site_id, (payload or {}).get("two_factor_code", ""))
