/**
 * 站点监控 — 对应后端 `backend/api/routers/monitoring.py`。
 *
 * 覆盖：总览 / 站点 CRUD / 变化记录 / 快照 / 账户额度 / 模型与倍率 /
 * 性能指标 / 主站同步 / 渠道发现导入 / 来源关联 / 连接探测。
 */
import { request } from "./client";
import type {
  PricingResponse,
  PerfSummaryResponse,
  PerfDetailResponse,
} from "../perf";
import type {
  Change,
  ChannelDiscoveryCandidate,
  ChannelDiscoveryImportItem,
  ChannelDiscoveryImportResult,
  Overview,
  Site,
  SiteAccountResponse,
  SiteCheckResponse,
  SiteDiscoveryLink,
  SiteFormPayload,
  SiteSnapshot,
} from "../types";

export const monitoringApi = {
  overview: () => request<Overview>("/api/overview"),
  sites: () => request<{ data: Site[] }>("/api/sites"),
  /** 手动触发当前主站的完整渠道/分组同步与本地关联对账 */
  syncMainSites: (adminSiteId?: number) =>
    request<{
      success: boolean;
      data?: unknown[];
      mode?: string;
      sync_all?: boolean;
      imported?: number;
      conflicts?: number;
      excluded?: number;
      channels_changed?: boolean;
      groups_changed?: boolean;
      keys_refreshed?: number;
      keys_changed?: number;
      keys_failed?: number;
      key_errors?: string[];
      disabled?: number;
      reenabled?: number;
      deleted?: number;
      failed?: number;
    }>("/api/sites/sync", {
      method: "POST",
      body: JSON.stringify(
        adminSiteId ? { admin_site_id: adminSiteId } : {},
      ),
    }),
  changes: (limit = 100) =>
    request<{ data: Change[] }>(`/api/changes?limit=${limit}`),
  siteChanges: (siteId: number, limit = 50) =>
    request<{ data: Change[] }>(
      `/api/sites/${siteId}/changes?limit=${limit}`,
    ),
  /** 站点历史快照（后端已有路由，前端补全契约） */
  siteSnapshots: (siteId: number) =>
    request<{ data: SiteSnapshot[] }>(`/api/sites/${siteId}/snapshots`),
  /** 账户额度：NewAPI /api/user/self 或 sub2api /api/v1/auth/me */
  siteAccount: (siteId: number) =>
    request<SiteAccountResponse>(`/api/sites/${siteId}/account`),
  siteModels: (siteId: number, opts?: { refresh?: boolean }) =>
    request<{
      models_by_group?: Record<string, any[]>;
      fetched_at?: string;
      message?: string;
    }>(`/api/sites/${siteId}/models${opts?.refresh ? "?refresh=true" : ""}`),
  /** NewAPI: model catalog (enable_groups / usable_group / ratios) */
  sitePricing: (siteId: number, opts?: { refresh?: boolean }) =>
    request<PricingResponse>(
      `/api/sites/${siteId}/pricing${opts?.refresh ? "?refresh=true" : ""}`,
    ),
  /** NewAPI: site-wide model status summary (list badges; NOT group-scoped) */
  sitePerfSummary: (siteId: number, hours = 24, opts?: { refresh?: boolean }) =>
    request<PerfSummaryResponse>(
      `/api/sites/${siteId}/perf-metrics/summary?hours=${hours}${opts?.refresh ? "&refresh=true" : ""}`,
    ),
  /** NewAPI: per-group status + series for one model */
  sitePerfDetail: (
    siteId: number,
    model: string,
    hours = 24,
    group?: string,
  ) => {
    const qs = new URLSearchParams({
      model,
      hours: String(hours),
    });
    if (group) qs.set("group", group);
    return request<PerfDetailResponse>(
      `/api/sites/${siteId}/perf-metrics?${qs.toString()}`,
    );
  },
  createSite: (payload: SiteFormPayload) =>
    request<{ success: boolean; id?: number }>("/api/sites", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  channelCandidates: (adminSiteId: number, keyword = "") =>
    request<{
      success: boolean;
      data?: ChannelDiscoveryCandidate[];
      meta?: { total?: number; source_channel_total?: number };
      message?: string;
    }>(
      `/api/admin/sites/${adminSiteId}/channel-candidates?keyword=${encodeURIComponent(keyword)}`,
    ),
  importDiscoveredSites: (payload: {
    admin_site_id: number;
    interval_minutes: number;
    items: ChannelDiscoveryImportItem[];
  }) =>
    request<{
      success: boolean;
      data?: ChannelDiscoveryImportResult[];
      message?: string;
    }>("/api/sites/discovery-import", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  /** 来源关联查询（脱敏）：当前站点所属的主站/渠道溯源 */
  siteDiscoveryLinks: (siteId: number) =>
    request<{ success: boolean; data?: SiteDiscoveryLink[]; message?: string }>(
      `/api/sites/${siteId}/discovery-links`,
    ),
  updateSite: (id: number, payload: SiteFormPayload) =>
    request<{ success: boolean }>(`/api/sites/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  /** 手动重新生成兜底系统访问令牌（会重置上游该账号的系统访问令牌） */
  regenerateSystemToken: (siteId: number) =>
    request<{ success: boolean; message?: string }>(
      `/api/sites/${siteId}/system-token`,
      { method: "POST" },
    ),
  loginNewApiSite: (siteId: number, twoFactorCode = "") =>
    request<{
      success: boolean;
      requires_2fa?: boolean;
      message?: string;
      groups_count?: number;
      warning?: string | null;
    }>(`/api/sites/${siteId}/auth/login`, {
      method: "POST",
      body: JSON.stringify({ two_factor_code: twoFactorCode }),
    }),
  deleteSite: (id: number) =>
    request<{ success: boolean }>(`/api/sites/${id}`, { method: "DELETE" }),
  checkSite: (id: number) =>
    request<SiteCheckResponse>(`/api/sites/${id}/check`, {
      method: "POST",
      body: "{}",
    }),
  checkConnection: (payload: Record<string, unknown>) =>
    request<{ success: boolean; message?: string; groups_count?: number }>(
      "/api/check-connection",
      { method: "POST", body: JSON.stringify(payload) },
    ),
  checkLogin: (payload: Record<string, unknown>) =>
    request<{
      success: boolean;
      requires_2fa?: boolean;
      message?: string;
      groups_count?: number;
    }>(
      "/api/check-login",
      { method: "POST", body: JSON.stringify(payload) },
    ),
};
