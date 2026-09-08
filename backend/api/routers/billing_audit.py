"""Billing-audit routes: overview, check list/detail, settings, prices, run.

对应设计稿 ``docs/billing-audit/billing-audit-方案.md`` 第 6 节，契约以
Apifox「计费核对/billing-audit」目录为准。业务全部下沉
``services/billing_audit_service.py``，这里只做参数接驳与信封包装；
阻塞 I/O 经 ``run_in_threadpool`` 派发。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from backend.api.schemas.billing_audit import (
    BillingAuditRunRequest,
    BillingAuditSettingsUpdateRequest,
    OfficialModelPriceUpsertRequest,
)
from backend.services import billing_audit_overview_service as overviewService
from backend.services import billing_audit_service as service

router = APIRouter()


def _validationError(message: str) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"success": False, "message": message, "code": "validation_error"},
    )


@router.get("/billing-audit/overview")
async def billingAuditOverview(
    admin_site_id: Optional[int] = None,
    upstream_site_id: Optional[int] = None,
    channel_id: Optional[int] = None,
    model: Optional[str] = None,
    start_at: Optional[str] = None,
    end_at: Optional[str] = None,
    bucket_minutes: int = Query(5, ge=1, le=1440),
    reason_code: Optional[str] = None,
):
    try:
        data = await run_in_threadpool(
            overviewService.billingAuditOverviewPayload,
            admin_site_id, upstream_site_id, channel_id, model, start_at, end_at,
            bucket_minutes, reason_code,
        )
    except ValueError as exc:
        raise _validationError(str(exc))
    return {"success": True, "data": data}


@router.get("/billing-audit/requests")
async def billingAuditRequests(
    page: int = 1,
    page_size: int = 20,
    status: Optional[str] = None,
    admin_site_id: Optional[int] = None,
    upstream_site_id: Optional[int] = None,
    channel_id: Optional[int] = None,
    model: Optional[str] = None,
    start_at: Optional[str] = None,
    end_at: Optional[str] = None,
    reason_code: Optional[str] = None,
):
    try:
        data = await run_in_threadpool(
            service.billingChecksListPayload,
            page, page_size, status, admin_site_id, upstream_site_id,
            channel_id, model, start_at, end_at, reason_code,
        )
    except ValueError as exc:
        raise _validationError(str(exc))
    return {"success": True, "data": data}


@router.get("/billing-audit/requests/{check_id}")
async def billingAuditCheckDetail(check_id: int):
    data = await run_in_threadpool(service.billingCheckDetailPayload, check_id)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail={"success": False, "message": "核对记录不存在", "code": "not_found"},
        )
    return {"success": True, "data": data}


@router.get("/billing-audit/settings")
async def billingAuditSettings():
    data = await run_in_threadpool(service.billingAuditSettingsPayload)
    return {"success": True, "data": data}


@router.put("/billing-audit/settings")
async def updateBillingAuditSettingsRoute(body: BillingAuditSettingsUpdateRequest):
    try:
        data = await run_in_threadpool(
            service.updateBillingAuditSettingsPayload, body.model_dump(exclude_none=True)
        )
    except ValueError as exc:
        raise _validationError(str(exc))
    return {"success": True, "data": data}


@router.get("/billing-audit/prices")
async def billingAuditPrices():
    data = await run_in_threadpool(service.officialPricesPayload)
    return {"success": True, "data": data}


@router.put("/billing-audit/prices")
async def upsertBillingAuditPriceRoute(body: OfficialModelPriceUpsertRequest):
    try:
        data = await run_in_threadpool(
            service.upsertOfficialPricePayload, body.model_dump()
        )
    except ValueError as exc:
        raise _validationError(str(exc))
    return {"success": True, "data": data}


@router.delete("/billing-audit/prices/{model_name}")
async def deleteBillingAuditPriceRoute(model_name: str):
    deleted = await run_in_threadpool(service.deleteOfficialPrice, model_name)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail={"success": False, "message": "价格记录不存在", "code": "not_found"},
        )
    return {"success": True, "data": {"deleted": True}}


@router.post("/billing-audit/run")
async def runBillingAuditRoute(body: BillingAuditRunRequest | None = None):
    adminSiteId = body.admin_site_id if body else None
    data = await run_in_threadpool(service.runBillingAuditOnce, adminSiteId)
    return {"success": True, "data": data}
