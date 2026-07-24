import type {
  Change,
  NotificationSettings,
  Overview,
  Site,
  SiteFormPayload,
} from "./types";

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
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
  if (!res.ok) {
    throw new Error(body?.message || body?.error || `HTTP ${res.status}`);
  }
  return body as T;
}

export const api = {
  overview: () => request<Overview>("/api/overview"),
  sites: () => request<{ data: Site[] }>("/api/sites"),
  changes: (limit = 100) =>
    request<{ data: Change[] }>(`/api/changes?limit=${limit}`),
  siteChanges: (siteId: number, limit = 50) =>
    request<{ data: Change[] }>(
      `/api/sites/${siteId}/changes?limit=${limit}`,
    ),
  siteModels: (siteId: number) =>
    request<{
      models_by_group?: Record<string, any[]>;
      fetched_at?: string;
      message?: string;
    }>(`/api/sites/${siteId}/models`),
  notificationSettings: () =>
    request<{ data: NotificationSettings }>("/api/notifications/settings"),
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
  updateSite: (id: number, payload: SiteFormPayload) =>
    request<{ success: boolean }>(`/api/sites/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  deleteSite: (id: number) =>
    request<{ success: boolean }>(`/api/sites/${id}`, { method: "DELETE" }),
  checkSite: (id: number) =>
    request<Record<string, unknown>>(`/api/sites/${id}/check`, {
      method: "POST",
      body: "{}",
    }),
  checkConnection: (payload: Record<string, unknown>) =>
    request<{ success: boolean; message?: string; groups_count?: number }>(
      "/api/check-connection",
      { method: "POST", body: JSON.stringify(payload) },
    ),
  checkLogin: (payload: Record<string, unknown>) =>
    request<{ success: boolean; message?: string; groups_count?: number }>(
      "/api/check-login",
      { method: "POST", body: JSON.stringify(payload) },
    ),
};
