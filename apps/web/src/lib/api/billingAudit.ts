/**
 * 计费核对 — 对应后端 `backend/api/routers/billing_audit.py`。
 * GET  /api/billing-audit/overview          总览 KPI
 * GET  /api/billing-audit/requests          明细列表（分页）
 * GET  /api/billing-audit/requests/{id}     详情（含两侧 other JSON）
 * GET/PUT /api/billing-audit/settings       运行设置
 * GET/PUT /api/billing-audit/prices         官方价格表（按 model_name upsert）
 * DELETE /api/billing-audit/prices/{model}
 * POST /api/billing-audit/run               手动触发一轮核对
 */
import { request } from "./client";
import type {
  BillingAuditOverview,
  BillingAuditSettings,
  BillingRequestCheck,
  BillingRunSiteSummary,
  OfficialModelPrice,
} from "../types";

export type BillingChecksQuery = {
  page?: number;
  page_size?: number;
  status?: "" | BillingRequestCheck["status"];
  admin_site_id?: number | null;
  model?: string;
  start_at?: string;
  end_at?: string;
};

function toQuery(query: BillingChecksQuery): string {
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") return;
    params.set(key, String(value));
  });
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export const billingAuditApi = {
  billingOverview: (query: BillingChecksQuery = {}) =>
    request<{ success: boolean; data: BillingAuditOverview }>(
      `/api/billing-audit/overview${toQuery(query)}`,
    ),
  billingRequests: (query: BillingChecksQuery = {}) =>
    request<{
      success: boolean;
      data: {
        total: number;
        page: number;
        page_size: number;
        items: BillingRequestCheck[];
      };
    }>(`/api/billing-audit/requests${toQuery(query)}`),
  billingCheckDetail: (id: number) =>
    request<{ success: boolean; data: BillingRequestCheck }>(
      `/api/billing-audit/requests/${id}`,
    ),
  billingSettings: () =>
    request<{ success: boolean; data: BillingAuditSettings }>(
      "/api/billing-audit/settings",
    ),
  saveBillingSettings: (payload: Partial<BillingAuditSettings>) =>
    request<{ success: boolean; data: BillingAuditSettings }>(
      "/api/billing-audit/settings",
      { method: "PUT", body: JSON.stringify(payload) },
    ),
  billingPrices: () =>
    request<{ success: boolean; data: { items: OfficialModelPrice[] } }>(
      "/api/billing-audit/prices",
    ),
  saveBillingPrice: (
    payload: Partial<OfficialModelPrice> & { model_name: string },
  ) =>
    request<{ success: boolean; data: OfficialModelPrice }>(
      "/api/billing-audit/prices",
      { method: "PUT", body: JSON.stringify(payload) },
    ),
  deleteBillingPrice: (modelName: string) =>
    request<{ success: boolean }>(
      `/api/billing-audit/prices/${encodeURIComponent(modelName)}`,
      { method: "DELETE" },
    ),
  runBillingAudit: (adminSiteId?: number | null) =>
    request<{
      success: boolean;
      data: { triggered: boolean; executed: boolean; sites?: BillingRunSiteSummary[] };
    }>("/api/billing-audit/run", {
      method: "POST",
      body: JSON.stringify(adminSiteId ? { admin_site_id: adminSiteId } : {}),
    }),
};
