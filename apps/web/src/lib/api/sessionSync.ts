/**
 * 浏览器会话同步 — 对应后端 `backend/api/routers/session_sync.py`。
 * 前端契约由 `lib/browserSessionBridge.ts` 消费，配合桌面扩展注入登录态。
 * POST /api/sites/{siteId}/session-sync/requests
 * GET  /api/sites/{siteId}/session-sync/requests/{requestId}
 * POST /api/sites/{siteId}/session-sync/requests/{requestId}/fail
 */
import { request } from "./client";
import type { SiteSessionSyncRequest, SiteSessionSyncState } from "../types";

export const sessionSyncApi = {
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
};
