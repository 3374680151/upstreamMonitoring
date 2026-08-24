/**
 * 控制台鉴权 — 对应后端 `backend/api/routers/auth.py`。
 * GET  /api/auth/status
 * POST /api/auth/login
 * POST /api/auth/logout
 */
import { request } from "./client";

export const authApi = {
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
