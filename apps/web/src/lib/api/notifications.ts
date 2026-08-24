/**
 * 邮件 / 企业微信推送 — 对应后端 `backend/api/routers/notifications.py`。
 * GET /api/notifications/settings
 * PUT /api/notifications/settings
 * GET /api/notifications/logs        ← 前端此前未接，补全契约
 * POST /api/notifications/test-email
 * POST /api/notifications/test-wecom
 */
import { request } from "./client";
import type { NotificationLog, NotificationSettings } from "../types";

export const notificationsApi = {
  notificationSettings: () =>
    request<{ data: NotificationSettings }>("/api/notifications/settings"),
  /** 推送历史日志（后端已有路由，前端按需消费） */
  notificationLogs: () =>
    request<{ data: NotificationLog[] }>("/api/notifications/logs"),
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
};
