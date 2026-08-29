/**
 * 全局应用设置 — 对应后端 `backend/api/routers/settings.py`。
 * GET /api/settings
 * PUT /api/settings
 *
 * 主站同步范围（原 main_site_sync_all 全局开关）已移除：
 * 改为每次同步时在 POST /api/sites/sync 请求里传 scope。
 */
import { request } from "./client";

export interface ConsoleSettings {
  main_site_reconcile_mode?: string;
}

export const settingsApi = {
  /** 全局设置：消失渠道对账模式 */
  getSettings: () =>
    request<{ success: boolean; data?: ConsoleSettings }>("/api/settings"),
  saveSettings: (patch: ConsoleSettings) =>
    request<{ success: boolean; data?: ConsoleSettings; message?: string }>(
      "/api/settings",
      { method: "PUT", body: JSON.stringify(patch) },
    ),
};
