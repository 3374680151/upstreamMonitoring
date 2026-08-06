"""Monitoring and site-facing API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend import legacy_runtime as legacy
from backend.api.routers.common import forward_request
from backend.services.monitoring_service import MonitoringService


router = APIRouter()
service = MonitoringService()


@router.get("/overview")
def overview() -> dict[str, Any]:
    return service.overview()


@router.get("/sites")
def sites() -> dict[str, Any]:
    data, auto_sync = service.list_sites()
    return {"data": data, "auto_sync": auto_sync}


@router.get("/changes")
def changes(limit: int = 100) -> dict[str, Any]:
    return {"data": service.list_changes(limit)}


@router.get("/sites/{site_id}/snapshots")
def snapshots(site_id: int) -> dict[str, Any]:
    return {"data": service.snapshots(site_id)}


@router.get("/sites/{site_id}/changes")
def site_changes(site_id: int, limit: int = 100) -> dict[str, Any]:
    return {"data": service.list_site_changes(site_id, limit)}


@router.get("/sites/{site_id}/account")
def account(site_id: int):
    site, error, status = legacy.get_site_or_404(site_id)
    if error:
        return JSONResponse(error, status_code=status)
    response_status, payload = service.account(site)
    return JSONResponse(payload, status_code=response_status)


@router.get("/sites/{site_id}/discovery-links")
def discovery_links(site_id: int):
    site, error, status = legacy.get_site_or_404(site_id)
    if error:
        return JSONResponse(error, status_code=status)
    return {"success": True, "data": legacy.list_site_discovery_links(site_id)}


@router.get("/sites/{site_id}/models")
async def models(request: Request):
    return await forward_request(request)


@router.api_route(
    "/sites/{site_id}/pricing",
    methods=["GET"],
)
async def pricing(request: Request):
    return await forward_request(request)


@router.api_route(
    "/sites/{site_id}/perf-metrics",
    methods=["GET"],
)
async def perf_metrics(request: Request):
    return await forward_request(request)


@router.api_route(
    "/sites/{site_id}/perf-metrics/summary",
    methods=["GET"],
)
async def perf_summary(request: Request):
    return await forward_request(request)


@router.api_route("/sites", methods=["POST"])
@router.api_route("/sites/{site_id}", methods=["PUT", "DELETE"])
@router.api_route("/sites/{site_id}/check", methods=["POST"])
@router.api_route("/sites/sync", methods=["POST"])
@router.api_route("/sites/discovery-import", methods=["POST"])
@router.api_route("/check-connection", methods=["POST"])
@router.api_route("/check-login", methods=["POST"])
async def site_mutation(request: Request):
    return await forward_request(request)
