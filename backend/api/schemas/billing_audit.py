"""Billing-audit request schemas（对应 routers/billing_audit.py）。

响应沿用全局 ``common.SuccessResponse`` 信封；查询类端点的过滤参数直接用
FastAPI query 参数（snake_case 与 lib/api/billingAudit.ts 一一对应）。
"""

from __future__ import annotations

from pydantic import BaseModel


class BillingAuditSettingsUpdateRequest(BaseModel):
    """整体保存设置；None 字段沿用当前值，数值范围在 repository 钳位。"""

    enabled: bool | None = None
    interval_minutes: int | None = None
    tolerance_percent: float | None = None
    match_window_seconds: int | None = None
    retention_days: int | None = None
    quota_per_unit: int | None = None
    push_on_red: bool | None = None


class OfficialModelPriceUpsertRequest(BaseModel):
    """按 model_name upsert；保存后 source 归为 manual。"""

    model_name: str
    quota_type: str = "per_token"
    input_usd_per_m: float | None = None
    cached_input_usd_per_m: float | None = None
    cache_write_usd_per_m: float | None = None
    output_usd_per_m: float | None = None
    price_per_call_usd: float | None = None


class BillingAuditRunRequest(BaseModel):
    """手动触发一轮核对；admin_site_id 缺省 = 全部 NewAPI 主站。"""

    admin_site_id: int | None = None
