/**
 * 管理站点与渠道管理 — 对应后端 `backend/api/routers/admin_sites.py`。
 *
 * 统一入口按 NewAPI / sub2api 平台适配：
 * - 主站 CRUD / 测试 / key 安全验证
 * - 渠道列表（密钥掩码）/ 详情（明文按需）/ 分组倍率
 * - 渠道上游绑定（来源关联）/ 匹配 / key 刷新
 * - 渠道 CRUD / 测试 / 批量操作
 */
import { request } from "./client";
import type {
  AdminSite,
  AdminSiteFormPayload,
  Channel,
  ChannelDetailResponse,
  ChannelGroupsResponse,
  ChannelListResponse,
  ChannelUpstreamBinding,
  ChannelUpstreamBindingPayload,
  Platform,
} from "../types";

export const adminSitesApi = {
  // ---- 管理站点（NewAPI / sub2api 统一入口）----
  adminSites: () => request<{ data: AdminSite[] }>("/api/admin/sites"),
  testAdminSite: (
    payload: AdminSiteFormPayload & { admin_site_id?: number },
  ) =>
    request<{
      success: boolean;
      platform?: Platform;
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
  refreshChannelKey: (adminSiteId: number, channelId: number) =>
    request<{
      success: boolean;
      message?: string;
      code?: string;
      data?: {
        channel_id: number;
        changed: boolean;
        first_fetch: boolean;
        fetched_at: string;
        match_success: boolean;
        match_message?: string;
        binding?: ChannelUpstreamBinding;
      };
    }>(`/api/admin/sites/${adminSiteId}/channels/${channelId}/key/refresh`, {
      method: "POST",
      body: "{}",
    }),
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
};
