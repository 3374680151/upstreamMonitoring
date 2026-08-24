/**
 * 数据层统一出口 — 镜像 FastAPI 后端 routers 分层。
 *
 * 各资源域模块对应后端 router：
 *   authApi          ← backend/api/routers/auth.py
 *   monitoringApi    ← backend/api/routers/monitoring.py
 *   notificationsApi ← backend/api/routers/notifications.py
 *   settingsApi      ← backend/api/routers/settings.py
 *   sessionSyncApi   ← backend/api/routers/session_sync.py
 *   adminSitesApi    ← backend/api/routers/admin_sites.py
 *
 * 对外保持与原 `lib/api.ts` 完全一致的 `api` 对象与方法签名，
 * 页面与组件零改动即可消费。新增 `siteSnapshots` / `notificationLogs`
 * 用于补齐后端已有但前端此前未接的契约。
 */
import { authApi } from "./auth";
import { monitoringApi } from "./monitoring";
import { notificationsApi } from "./notifications";
import { settingsApi } from "./settings";
import { sessionSyncApi } from "./sessionSync";
import { adminSitesApi } from "./adminSites";

export { getConsoleToken, setConsoleToken } from "./client";

export const api = {
  ...authApi,
  ...monitoringApi,
  ...notificationsApi,
  ...settingsApi,
  ...sessionSyncApi,
  ...adminSitesApi,
};
