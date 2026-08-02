import type {
  SessionSyncStatus,
  SessionSyncTargetKind,
  SiteSessionSyncRequest,
  SiteSessionSyncState,
} from "./types";
import { api } from "./api";

const BRIDGE_VERSION = "upstream-session-bridge/v2";
const PAGE_SOURCE = "upstream-console";
const EXTENSION_SOURCE = "upstream-session-bridge";
const EXTENSION_REQUIRED_MESSAGE =
  "浏览器同步扩展未连接或版本过旧，请重新加载桌面项目中的 0.1.2 扩展并刷新页面";
const SESSION_SYNC_TERMINAL_STATUSES = new Set<SessionSyncStatus>([
  "ready",
  "no_session",
  "expired",
  "permission_required",
  "extension_unavailable",
  "failed",
]);
const SESSION_SYNC_RETRYABLE_STATUSES = new Set<SessionSyncStatus>([
  "no_session",
  "expired",
  "permission_required",
  "extension_unavailable",
  "failed",
]);

type BridgeResult = {
  ok: boolean;
  status: SessionSyncStatus;
  code: string;
  message: string;
};

function correlationId(): string {
  return crypto.randomUUID();
}

function waitForBridgeMessage(
  correlation: string,
  expectedType: string,
  timeoutMs: number,
  send: () => void,
): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    let settled = false;
    const cleanup = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      window.removeEventListener("message", onMessage);
    };
    const onMessage = (event: MessageEvent) => {
      if (
        event.source !== window ||
        event.origin !== window.location.origin ||
        event.data?.source !== EXTENSION_SOURCE ||
        event.data?.version !== BRIDGE_VERSION ||
        event.data?.type !== expectedType ||
        event.data?.correlation_id !== correlation
      ) {
        return;
      }
      const result =
        event.data.result && typeof event.data.result === "object"
          ? event.data.result
          : {};
      cleanup();
      resolve(result);
    };
    const timeout = window.setTimeout(() => {
      cleanup();
      reject(new Error("浏览器同步扩展未响应"));
    }, timeoutMs);
    window.addEventListener("message", onMessage);
    send();
  });
}

export async function probeSessionBridge(timeoutMs = 800): Promise<boolean> {
  const correlation = correlationId();
  try {
    const result = await waitForBridgeMessage(
      correlation,
      "UPSTREAM_SESSION_BRIDGE_ACK",
      timeoutMs,
      () => {
        window.postMessage(
          {
            source: PAGE_SOURCE,
            version: BRIDGE_VERSION,
            type: "UPSTREAM_SESSION_BRIDGE_PROBE",
            correlation_id: correlation,
          },
          window.location.origin,
        );
      },
    );
    return result.ok === true;
  } catch {
    return false;
  }
}

export async function startSessionBridgeRequest(
  request: SiteSessionSyncRequest,
  timeoutMs = 25000,
): Promise<BridgeResult> {
  const correlation = correlationId();
  const backendCompleteUrl = new URL(
    `/api/session-sync/requests/${request.request_id}/complete`,
    window.location.origin,
  ).toString();
  const result = await waitForBridgeMessage(
    correlation,
    "UPSTREAM_SESSION_BRIDGE_RESULT",
    timeoutMs,
    () => {
      window.postMessage(
        {
          source: PAGE_SOURCE,
          version: BRIDGE_VERSION,
          type: "UPSTREAM_SESSION_BRIDGE_START",
          correlation_id: correlation,
          request: {
            request_id: request.request_id,
            secret: request.secret,
            target_kind: request.target_kind,
            platform: request.platform,
            target_origin: request.target_origin,
            backend_complete_url: backendCompleteUrl,
          },
        },
        window.location.origin,
      );
    },
  );
  return {
    ok: result.ok === true,
    status: String(result.status || "failed") as SessionSyncStatus,
    code: String(result.code || ""),
    message: String(result.message || ""),
  };
}

function fallbackState(
  request: SiteSessionSyncRequest,
  status: SessionSyncStatus,
  code: string,
  message: string,
): SiteSessionSyncState {
  return {
    request_id: request.request_id,
    target_kind: request.target_kind,
    platform: request.platform,
    target_origin: request.target_origin,
    status,
    error_code: code,
    message,
  };
}

async function reportBridgeFailure(
  targetKind: SessionSyncTargetKind,
  targetId: number,
  request: SiteSessionSyncRequest,
  code:
    | "EXTENSION_UNAVAILABLE"
      | "ORIGIN_PERMISSION_REQUIRED"
      | "COOKIE_PERMISSION_REQUIRED"
      | "SYNC_FAILED",
  messageOverride?: string,
): Promise<SiteSessionSyncState> {
  try {
    if (targetKind === "admin_site") {
      await api.failAdminSiteSessionSync(targetId, request.request_id, code);
    } else {
      await api.failSiteSessionSync(targetId, request.request_id, code);
    }
    const response =
      targetKind === "admin_site"
        ? await api.getAdminSiteSessionSync(targetId, request.request_id)
        : await api.getSiteSessionSync(targetId, request.request_id);
    return {
      ...response.data,
      message: messageOverride || response.data.message,
    };
  } catch {
    const states: Record<typeof code, SessionSyncStatus> = {
      EXTENSION_UNAVAILABLE: "extension_unavailable",
      ORIGIN_PERMISSION_REQUIRED: "permission_required",
      COOKIE_PERMISSION_REQUIRED: "permission_required",
      SYNC_FAILED: "failed",
    };
    const messages: Record<typeof code, string> = {
      EXTENSION_UNAVAILABLE: EXTENSION_REQUIRED_MESSAGE,
      ORIGIN_PERMISSION_REQUIRED: "扩展需要该站点的读取权限",
      COOKIE_PERMISSION_REQUIRED:
        "扩展需要读取 NewAPI 登录 Cookie 的权限，请允许后重新同步",
      SYNC_FAILED: "登录态同步失败",
    };
    return fallbackState(
      request,
      states[code],
      code,
      messageOverride || messages[code],
    );
  }
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

export async function syncSiteBrowserSession(
  siteId: number,
): Promise<SiteSessionSyncState> {
  const created = await api.createSiteSessionSync(siteId);
  return syncTargetBrowserSession("site", siteId, created.data);
}

export async function syncAdminSiteBrowserSession(
  adminSiteId: number,
): Promise<SiteSessionSyncState> {
  const created = await api.createAdminSiteSessionSync(adminSiteId);
  return syncTargetBrowserSession("admin_site", adminSiteId, created.data);
}

async function syncTargetBrowserSession(
  targetKind: SessionSyncTargetKind,
  targetId: number,
  request: SiteSessionSyncRequest,
): Promise<SiteSessionSyncState> {
  if (!(await probeSessionBridge())) {
    return reportBridgeFailure(
      targetKind,
      targetId,
      request,
      "EXTENSION_UNAVAILABLE",
      EXTENSION_REQUIRED_MESSAGE,
    );
  }

  let bridgeResult: BridgeResult;
  try {
    bridgeResult = await startSessionBridgeRequest(request);
  } catch {
    return reportBridgeFailure(targetKind, targetId, request, "SYNC_FAILED");
  }
  if (bridgeResult.code === "ORIGIN_PERMISSION_REQUIRED") {
    return reportBridgeFailure(
      targetKind,
      targetId,
      request,
      "ORIGIN_PERMISSION_REQUIRED",
    );
  }
  if (bridgeResult.code === "COOKIE_PERMISSION_REQUIRED") {
    return reportBridgeFailure(
      targetKind,
      targetId,
      request,
      "COOKIE_PERMISSION_REQUIRED",
    );
  }
  if (bridgeResult.code === "SYNC_FAILED") {
    return reportBridgeFailure(
      targetKind,
      targetId,
      request,
      "SYNC_FAILED",
      bridgeResult.message,
    );
  }

  const deadline = Date.now() + Math.max(5000, request.expires_in * 1000 + 3000);
  while (Date.now() < deadline) {
    const response =
      targetKind === "admin_site"
        ? await api.getAdminSiteSessionSync(targetId, request.request_id)
        : await api.getSiteSessionSync(targetId, request.request_id);
    if (SESSION_SYNC_TERMINAL_STATUSES.has(response.data.status)) {
      return response.data;
    }
    await delay(400);
  }
  return reportBridgeFailure(targetKind, targetId, request, "SYNC_FAILED");
}

export function isSessionSyncRetryable(status?: SessionSyncStatus): boolean {
  return SESSION_SYNC_RETRYABLE_STATUSES.has(status || "not_requested");
}
