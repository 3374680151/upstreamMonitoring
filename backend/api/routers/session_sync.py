"""Browser session synchronization routes."""
from __future__ import annotations

from fastapi import APIRouter, Request

from backend.services.session_sync_service import SessionSyncService


router = APIRouter()
service = SessionSyncService()


@router.post("/session-sync/requests/{request_id}/complete")
async def complete_session_sync(request_id: str, request: Request):
    """Browser-side endpoint; auth bypass is handled by ``is_public_api_path``.

    Reads the upstream-sync secret from the request header and forwards the
    JSON body to the service for validation and CAS persistence.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    secret = request.headers.get("X-Upstream-Sync-Token", "")
    return service.complete(request_id, secret, body)


@router.post("/sites/{site_id}/session-sync/requests", status_code=201)
def create_sync_request(site_id: int):
    return service.create_for_site(site_id)


@router.post("/admin/sites/{admin_site_id}/session-sync/requests", status_code=201)
def create_admin_sync_request(admin_site_id: int):
    return service.create_for_admin_site(admin_site_id)


@router.get("/sites/{site_id}/session-sync/requests/{request_id}")
def get_sync_request(site_id: int, request_id: str):
    return service.get_for_site(site_id, request_id)


@router.get("/admin/sites/{admin_site_id}/session-sync/requests/{request_id}")
def get_admin_sync_request(admin_site_id: int, request_id: str):
    return service.get_for_admin_site(admin_site_id, request_id)


@router.post("/sites/{site_id}/session-sync/requests/{request_id}/fail")
def fail_sync_request(site_id: int, request_id: str, payload: dict = None):
    return service.fail_for_site(site_id, request_id, (payload or {}).get("code", ""))


@router.post("/admin/sites/{admin_site_id}/session-sync/requests/{request_id}/fail")
def fail_admin_sync_request(
    admin_site_id: int, request_id: str, payload: dict = None
):
    return service.fail_for_admin_site(
        admin_site_id, request_id, (payload or {}).get("code", "")
    )
