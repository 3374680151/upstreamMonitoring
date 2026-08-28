/**
 * 全局应用设置 — 对应后端 `backend/api/routers/settings.py`。
 * GET /api/settings
 * PUT /api/settings
 */
import { request } from "./client";

export interface ConsoleSettings {
  main_site_reconcile_mode?: string;
  main_site_sync_all?: boolean;
}

export const settingsApi = {
  /** 全局设置：消失渠道对账模式 + 主站同步范围开关 */
  getSettings: () =>
    request<{ success: boolean; data?: ConsoleSettings }>("/api/settings"),
  saveSettings: (patch: ConsoleSettings) =>
    request<{ success: boolean; data?: ConsoleSettings; message?: string }>(
      "/api/settings",
      { method: "PUT", body: JSON.stringify(patch) },
    ),
};
