import type {
  AdminSite,
  AdminSiteFormPayload,
  Change,
  Channel,
  ChannelDetailResponse,
  ChannelGroupsResponse,
  ChannelListResponse,
  ChannelUpstreamBinding,
  ChannelUpstreamBindingPayload,
  NotificationSettings,
  Overview,
  Site,
  ChannelDiscoveryCandidate,
  ChannelDiscoveryImportItem,
  ChannelDiscoveryImportResult,
  SiteAccountResponse,
  SiteCheckResponse,
  SiteDiscoveryLink,
  SiteFormPayload,
  SiteSessionSyncRequest,
  SiteSessionSyncState,
} from "./types";

const TOKEN_KEY = "console_token";

export function getConsoleToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

export function setConsoleToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore storage errors (private mode etc.) */
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getConsoleToken();
  const res = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
    ...options,
  });
  const text = await res.text();
  let body: any = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { raw: text };
  }
  if (
    res.ok &&
    body &&
    typeof body === "object" &&
    typeof body.raw === "string"
  ) {
    throw new Error("接口返回了网页内容而不是 JSON，请重启后端服务");
  }
  if (res.status === 401 && !path.startsWith("/api/auth/")) {
    // 会话失效：清 token 并通知全局切回登录页
    setConsoleToken("");
    window.dispatchEvent(new CustomEvent("console-unauthorized"));
  }
  if (!res.ok) {
    throw new Error(body?.message || body?.error || `HTTP ${res.status}`);
  }
  return body as T;
}

export const api = {
  overview: () => request<Overview>("/api/overview"),
  sites: () => request<{ data: Site[] }>("/api/sites"),
  /** 手动触发当前主站的完整渠道/分组同步与本地关联对账 */
  syncMainSites: (adminSiteId?: number) =>
    request<{
      success: boolean;
      data?: unknown[];
      imported?: number;
      conflicts?: number;
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
  /** 账户额度：NewAPI /api/user/self 或 sub2api /api/v1/auth/me */
  siteAccount: (siteId: number) =>
    request<SiteAccountResponse>(`/api/sites/${siteId}/account`),
  siteModels: (siteId: number) =>
    request<{
      models_by_group?: Record<string, any[]>;
      fetched_at?: string;
      message?: string;
    }>(`/api/sites/${siteId}/models`),
  /** NewAPI: model catalog (enable_groups / usable_group / ratios) */
  sitePricing: (siteId: number) =>
    request<import("./perf").PricingResponse>(`/api/sites/${siteId}/pricing`),
  /** NewAPI: site-wide model status summary (list badges; NOT group-scoped) */
  sitePerfSummary: (siteId: number, hours = 24) =>
    request<import("./perf").PerfSummaryResponse>(
      `/api/sites/${siteId}/perf-metrics/summary?hours=${hours}`,
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
    return request<import("./perf").PerfDetailResponse>(
      `/api/sites/${siteId}/perf-metrics?${qs.toString()}`,
    );
  },
  notificationSettings: () =>
    request<{ data: NotificationSettings }>("/api/notifications/settings"),
  /** 全局设置：目前含 main_site_reconcile_mode(disable|delete) */
  getSettings: () =>
    request<{ success: boolean; data?: { main_site_reconcile_mode?: string } }>(
      "/api/settings",
    ),
  saveSettings: (patch: { main_site_reconcile_mode?: string }) =>
    request<{
      success: boolean;
      data?: { main_site_reconcile_mode?: string };
      message?: string;
    }>("/api/settings", { method: "PUT", body: JSON.stringify(patch) }),
  saveNotificationSettings: (payload: Record<string, unknown>) =>
    request<{ success: boolean; data: NotificationSettings }>(
      "/api/notifications/settings",
      { method: "PUT", body: JSON.stringify(payload) },
    ),
  testEmail: (payload: Record<string, unknown>) =>
    request<{ success: boolean; message?: string }>(
      "/api/notifications/test-email",
      { method: "POST", body: JSON.stringify(payload) },
    ),
  testWecom: (payload: Record<string, unknown>) =>
    request<{ success: boolean; message?: string }>(
      "/api/notifications/test-wecom",
      { method: "POST", body: JSON.stringify(payload) },
    ),
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
  createSiteSessionSync: (siteId: number) =>
    request<{ success: boolean; data: SiteSessionSyncRequest }>(
      `/api/sites/${siteId}/session-sync/requests`,
      { method: "POST", body: "{}" },
    ),
  getSiteSessionSync: (siteId: number, requestId: string) =>
    request<{ success: boolean; data: SiteSessionSyncState }>(
      `/api/sites/${siteId}/session-sync/requests/${requestId}`,
    ),
  failSiteSessionSync: (siteId: number, requestId: string, code: string) =>
    request<{ success: boolean; message?: string }>(
      `/api/sites/${siteId}/session-sync/requests/${requestId}/fail`,
      { method: "POST", body: JSON.stringify({ code }) },
    ),
  updateSite: (id: number, payload: SiteFormPayload) =>
    request<{ success: boolean }>(`/api/sites/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
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

  // ---- 管理站点（NewAPI / sub2api 统一入口）----
  adminSites: () => request<{ data: AdminSite[] }>("/api/admin/sites"),
  testAdminSite: (
    payload: AdminSiteFormPayload & { admin_site_id?: number },
  ) =>
    request<{
      success: boolean;
      platform?: import("./types").Platform;
      groups_count?: number;
      channels_count?: number;
      message?: string;
    }>("/api/admin/sites/test", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  createAdminSite: (payload: AdminSiteFormPayload) =>
    request<{ success: boolean; id?: number; message?: string }>(
      "/api/admin/sites",
      { method: "POST", body: JSON.stringify(payload) },
    ),
  updateAdminSite: (id: number, payload: Partial<AdminSiteFormPayload>) =>
    request<{ success: boolean; message?: string }>(`/api/admin/sites/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  verifyAdminSiteKeyAccess: (id: number, code: string) =>
    request<{ success: boolean; message?: string }>(
      `/api/admin/sites/${id}/key-verification`,
      { method: "POST", body: JSON.stringify({ code }) },
    ),
  deleteAdminSite: (id: number) =>
    request<{ success: boolean; message?: string }>(`/api/admin/sites/${id}`, {
      method: "DELETE",
    }),

  // ---- NewAPI 渠道管理（管理员薄代理，凭管理站点令牌）----
  /** 渠道列表（密钥掩码） */
  channels: (adminSiteId: number, keyword = "") => {
    const qs = keyword ? `?keyword=${encodeURIComponent(keyword)}` : "";
    return request<ChannelListResponse>(
      `/api/admin/sites/${adminSiteId}/channels${qs}`,
    );
  },
  /** 单渠道详情（明文密钥，仅点击「显示」时调用） */
  channelDetail: (adminSiteId: number, channelId: number) =>
    request<ChannelDetailResponse>(
      `/api/admin/sites/${adminSiteId}/channels/${channelId}`,
    ),
  /** 分组名 → 倍率/描述（供密钥比对） */
  channelGroups: (adminSiteId: number) =>
    request<ChannelGroupsResponse>(`/api/admin/sites/${adminSiteId}/groups`),
  channelUpstreamBindings: (adminSiteId: number) =>
    request<{ success: boolean; data?: Record<string, ChannelUpstreamBinding>; message?: string }>(
      `/api/admin/sites/${adminSiteId}/channel-mappings`,
    ),
  saveChannelUpstreamBinding: (
    adminSiteId: number,
    channelId: number,
    payload: ChannelUpstreamBindingPayload,
  ) =>
    request<{ success: boolean; data?: ChannelUpstreamBinding; message?: string }>(
      `/api/admin/sites/${adminSiteId}/channels/${channelId}/mapping`,
      { method: "PUT", body: JSON.stringify(payload) },
    ),
  matchChannelUpstreamBinding: (adminSiteId: number, channelId: number, forceRefresh = false) =>
    request<{ success: boolean; data?: ChannelUpstreamBinding; message?: string }>(
      `/api/admin/sites/${adminSiteId}/channels/${channelId}/match${forceRefresh ? "?refresh=1" : ""}`,
      { method: "POST", body: "{}" },
    ),
  createChannel: (adminSiteId: number, payload: Partial<Channel>) =>
    request<{ success: boolean; id?: number; message?: string }>(
      `/api/admin/sites/${adminSiteId}/channels`,
      { method: "POST", body: JSON.stringify(payload) },
    ),
  /** 更新渠道（切换状态/权重/优先级/分组等，后端 read-merge-write） */
  updateChannel: (
    adminSiteId: number,
    channelId: number,
    patch: Partial<Channel>,
  ) =>
    request<{ success: boolean; message?: string }>(
      `/api/admin/sites/${adminSiteId}/channels/${channelId}`,
      { method: "PUT", body: JSON.stringify(patch) },
    ),
  deleteChannel: (adminSiteId: number, channelId: number) =>
    request<{ success: boolean; message?: string }>(
      `/api/admin/sites/${adminSiteId}/channels/${channelId}`,
      { method: "DELETE" },
    ),
  testChannel: (adminSiteId: number, channelId: number) =>
    request<{ success: boolean; message?: string; time?: number }>(
      `/api/admin/sites/${adminSiteId}/channels/${channelId}/test`,
    ),
  /** 批量操作：enable/disable/delete/set_group/set_tag */
  batchChannels: (
    adminSiteId: number,
    action: "enable" | "disable" | "delete" | "set_group" | "set_tag",
    ids: number[],
    extra: { group?: string; tag?: string } = {},
  ) =>
    request<{
      success: boolean;
      message?: string;
      data?: {
        action: string;
        ok_count: number;
        fail_count: number;
        total: number;
        results: Array<{ id: number; ok: boolean; message?: string | null }>;
      };
    }>(`/api/admin/sites/${adminSiteId}/channels/batch`, {
      method: "POST",
      body: JSON.stringify({ action, ids, ...extra }),
    }),

  // ---- 控制台鉴权 ----
  authStatus: () =>
    request<{ success: boolean; auth_required: boolean; authenticated: boolean }>(
      "/api/auth/status",
    ),
  login: (password: string) =>
    request<{ success: boolean; token?: string; message?: string }>(
      "/api/auth/login",
      { method: "POST", body: JSON.stringify({ password }) },
    ),
  logout: () =>
    request<{ success: boolean }>("/api/auth/logout", {
      method: "POST",
      body: "{}",
    }),
};
