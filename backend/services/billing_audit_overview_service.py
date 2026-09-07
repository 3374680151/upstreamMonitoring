"""Billing-audit overview payload service (dashboard aggregations).

对应 routers/billing_audit.py 的 ``GET /api/billing-audit/overview``。时间
边界归一（``normalizeTimeBound`` 复用 ``billing_audit_service``（services 互调只走
顶层公共函数）；聚合 SQL 全部在
``repositories/billing_audit_overview.py``。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from backend.repositories import billing_audit_overview as overviewRepo
from backend.services.billing_audit_service import normalizeTimeBound


def billingAuditOverviewPayload(
    adminSiteId: Optional[int],
    upstreamSiteId: Optional[int],
    channelId: Optional[int],
    model: Optional[str],
    startAt: Optional[str],
    endAt: Optional[str],
    bucketMinutes: int = 5,
    reasonCode: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        safeBucket = int(bucketMinutes)
    except (TypeError, ValueError):
        safeBucket = 5
    return overviewRepo.overviewBillingChecks(
        adminSiteId, upstreamSiteId, channelId, model,
        normalizeTimeBound(startAt), normalizeTimeBound(endAt),
        bucketMinutes=safeBucket,
        reasonCode=reasonCode,
    )
