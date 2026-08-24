/**
 * 全局应用设置 — 对应后端 `backend/api/routers/settings.py`。
 * GET /api/settings
 * PUT /api/settings
 */
import { request } from "./client";

export const settingsApi = {
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
};
