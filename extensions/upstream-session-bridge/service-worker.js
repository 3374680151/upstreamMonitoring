import {
  BRIDGE_VERSION,
  completionPayload as sub2ApiCompletionPayload,
  isLoopbackCompletionUrl,
  normalizeOrigin,
  tokenFreePageResult,
} from "./adapters/sub2api.js";
import {
  newApiCompletionPayload,
  normalizeNewApiInMemoryAuth,
  normalizeNewApiRefreshBundle,
  selectNewApiBrowserCookie,
  selectNewApiRefreshCookie,
} from "./adapters/newapi.js";
import { classifyExtensionSyncFailure } from "./adapters/sync-errors.js";
import { selectExistingTargetTab } from "./adapters/target-tab.js";

const PAGE_REQUEST_TYPE = "UPSTREAM_SESSION_BRIDGE_START";
const TARGET_TAB_OPEN_TIMEOUT_MS = 20000;
const TARGET_TAB_SETTLE_MS = 2500;

function readSub2ApiSessionInPage() {
  const accessToken = String(localStorage.getItem("auth_token") || "").trim();
  const refreshToken = String(localStorage.getItem("refresh_token") || "").trim();
  const tokenExpiresAt = String(
    localStorage.getItem("token_expires_at") || "",
  ).trim();
  if (!accessToken || !refreshToken) return null;
  return {
    access_token: accessToken,
    refresh_token: refreshToken,
    token_expires_at: tokenExpiresAt,
  };
}

function readNewApiLegacySessionInPage() {
  const keys = ["user", "access_token", "token", "user_id", "uid"];
  const values = Object.fromEntries(
    keys.map((key) => [key, localStorage.getItem(key)]),
  );
  let user = {};
  const rawUser = String(values.user || "").trim();
  if (rawUser) {
    try {
      user = JSON.parse(rawUser);
    } catch {
      return null;
    }
    if (!user || typeof user !== "object" || Array.isArray(user)) return null;
  }
  const accessToken = String(
    user.access_token || user.token || values.access_token || values.token || "",
  ).trim();
  const accessUserId = String(
    user.id || user.user_id || user.userId || values.user_id || values.uid || "",
  ).trim();
  if (!accessUserId) return null;
  return accessToken
    ? { access_token: accessToken, access_user_id: accessUserId }
    : { access_user_id: accessUserId };
}

async function verifyNewApiCookieSessionInPage() {
  try {
    const accountResponse = await fetch("/api/user/self", {
      credentials: "include",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const accountPayload = await accountResponse.json();
    if (!accountResponse.ok || accountPayload?.success !== true) return null;
    const account = accountPayload?.data;
    const accessUserId = String(account?.id || "").trim();
    if (!accessUserId) return null;
    for (const path of ["/api/user/self/groups", "/api/user/groups"]) {
      const groupsResponse = await fetch(path, {
        credentials: "include",
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      let groupsPayload = null;
      try {
        groupsPayload = await groupsResponse.json();
      } catch {
        continue;
      }
      if (groupsResponse.ok && groupsPayload?.success === true) {
        return { access_user_id: accessUserId };
      }
    }
  } catch {
    return null;
  }
  return null;
}

function readNewApiInMemorySessionInPage() {
  try {
    const chunks = globalThis.webpackChunknew_api;
    if (!Array.isArray(chunks)) return null;
    let webpackRequire = null;
    chunks.push([
      [Date.now()],
      {},
      (runtimeRequire) => {
        webpackRequire = runtimeRequire;
      },
    ]);
    chunks.pop();
    if (!webpackRequire?.c) return null;
    for (const cachedModule of Object.values(webpackRequire.c)) {
      const exports = cachedModule?.exports;
      if (!exports || typeof exports !== "object") continue;
      for (const candidate of Object.values(exports)) {
        if (!candidate || typeof candidate.getState !== "function") continue;
        const auth = candidate.getState()?.auth;
        if (
          !auth ||
          typeof auth.accessToken !== "string" ||
          typeof auth.accessExpiresAt !== "number" ||
          !auth.user ||
          !auth.session
        ) {
          continue;
        }
        return {
          accessToken: auth.accessToken,
          accessExpiresAt: auth.accessExpiresAt,
          user: { id: auth.user.id },
          session: { sid: auth.session.sid },
        };
      }
    }
  } catch {
    // Different NewAPI builds may not expose a webpack runtime.
  }
  return null;
}

async function refreshNewApiSessionInPage() {
  try {
    const response = await fetch("/api/user/auth/refresh", {
      method: "POST",
      credentials: "include",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      return { kind: "invalid" };
    }
    if (response.status === 404 || response.status === 405) {
      return { kind: "unsupported" };
    }
    if (!response.ok || payload?.success !== true) {
      return { kind: "failed" };
    }
    return { kind: "success", bundle: payload.data };
  } catch {
    return { kind: "failed" };
  }
}

function requestShape(value) {
  if (!value || typeof value !== "object") return null;
  const requestId = String(value.request_id || "");
  const secret = String(value.secret || "");
  const platform = String(value.platform || "").toLowerCase();
  const targetKind = String(value.target_kind || "").toLowerCase();
  const targetOrigin = normalizeOrigin(value.target_origin);
  const backendCompleteUrl = String(value.backend_complete_url || "");
  if (
    !/^[A-Za-z0-9_-]{1,64}$/.test(requestId) ||
    !secret ||
    secret.length > 256 ||
    !new Set(["sub2api", "newapi"]).has(platform) ||
    !new Set(["site", "admin_site"]).has(targetKind) ||
    !targetOrigin ||
    !isLoopbackCompletionUrl(backendCompleteUrl)
  ) {
    return null;
  }
  const backendUrl = new URL(backendCompleteUrl);
  if (
    backendUrl.pathname !==
    `/api/session-sync/requests/${requestId}/complete`
  ) {
    return null;
  }
  return {
    requestId,
    secret,
    platform,
    targetKind,
    targetOrigin,
    backendCompleteUrl,
  };
}

async function findExistingTargetTabId(targetOrigin) {
  const tabs = await chrome.tabs.query({});
  return selectExistingTargetTab(tabs, targetOrigin)?.id || null;
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function waitForTargetTabReady(tabId, timeoutMs) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (ready) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(onUpdated);
      chrome.tabs.onRemoved.removeListener(onRemoved);
      resolve(ready);
    };
    const onUpdated = (updatedTabId, changeInfo) => {
      if (updatedTabId === tabId && changeInfo.status === "complete") {
        finish(true);
      }
    };
    const onRemoved = (removedTabId) => {
      if (removedTabId === tabId) finish(false);
    };
    const timer = setTimeout(() => finish(true), timeoutMs);
    chrome.tabs.onUpdated.addListener(onUpdated);
    chrome.tabs.onRemoved.addListener(onRemoved);
    chrome.tabs
      .get(tabId)
      .then((tab) => {
        if (tab?.status === "complete") finish(true);
      })
      .catch(() => finish(false));
  });
}

async function ensureTargetTab(targetOrigin) {
  const existingTabId = await findExistingTargetTabId(targetOrigin);
  if (existingTabId) return { tabId: existingTabId, created: false };
  const createdTab = await chrome.tabs.create({
    url: `${targetOrigin}/`,
    active: false,
  });
  const tabId = createdTab?.id ?? null;
  if (tabId == null) return { tabId: null, created: false };
  const ready = await waitForTargetTabReady(tabId, TARGET_TAB_OPEN_TIMEOUT_MS);
  if (ready) await delay(TARGET_TAB_SETTLE_MS);
  return { tabId, created: true };
}

async function readSub2ApiTargetSession(tabId) {
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    func: readSub2ApiSessionInPage,
  });
  return results?.[0]?.result || null;
}

async function readNewApiTargetSession(
  tabId,
  targetOrigin,
  targetKind,
  setDiagnosticStage = () => {},
) {
  if (targetKind === "site") {
    setDiagnosticStage("page_storage_read");
    const legacyResults = await chrome.scripting.executeScript({
      target: { tabId },
      func: readNewApiLegacySessionInPage,
    });
    const legacySession = legacyResults?.[0]?.result || null;
    if (legacySession?.access_token) return { session: legacySession };
    if (legacySession?.access_user_id) {
      setDiagnosticStage("page_cookie_probe");
      const verified = await chrome.scripting.executeScript({
        target: { tabId },
        func: verifyNewApiCookieSessionInPage,
        world: "MAIN",
      });
      const cookieIdentity = verified?.[0]?.result;
      if (cookieIdentity?.access_user_id === legacySession.access_user_id) {
        setDiagnosticStage("cookie_read");
        const cookies = await chrome.cookies.getAll({ url: targetOrigin });
        const browserCookie = selectNewApiBrowserCookie(cookies);
        if (browserCookie) {
          return {
            session: {
              access_user_id: cookieIdentity.access_user_id,
              browser_cookie: browserCookie,
            },
          };
        }
      }
    }
  }

  setDiagnosticStage("page_memory_read");
  const inMemoryResults = await chrome.scripting.executeScript({
    target: { tabId },
    func: readNewApiInMemorySessionInPage,
    world: "MAIN",
  });
  const inMemoryAuth = normalizeNewApiInMemoryAuth(
    inMemoryResults?.[0]?.result,
  );
  if (inMemoryAuth) {
    setDiagnosticStage("cookie_read");
    const cookie = await chrome.cookies.get({
      url: targetOrigin,
      name: "new_api_refresh",
    });
    const refreshCookie = selectNewApiRefreshCookie(cookie ? [cookie] : []);
    if (refreshCookie) {
      return {
        session: {
          ...inMemoryAuth,
          browser_refresh_cookie: `new_api_refresh=${refreshCookie}`,
        },
      };
    }
  }

  setDiagnosticStage("refresh_fallback");
  const refreshResults = await chrome.scripting.executeScript({
    target: { tabId },
    func: refreshNewApiSessionInPage,
    world: "MAIN",
  });
  const refreshResult = refreshResults?.[0]?.result;
  if (refreshResult?.kind !== "success") return { session: null };

  setDiagnosticStage("cookie_read");
  const cookie = await chrome.cookies.get({
    url: targetOrigin,
    name: "new_api_refresh",
  });
  const refreshCookie = selectNewApiRefreshCookie(cookie ? [cookie] : []);
  return {
    session: normalizeNewApiRefreshBundle(
      refreshResult.bundle,
      refreshCookie,
    ),
  };
}

async function submitCompletion(request, payload) {
  const response = await fetch(request.backendCompleteUrl, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Upstream-Sync-Token": request.secret,
    },
    body: JSON.stringify(payload),
    cache: "no-store",
    credentials: "omit",
  });
  let result = {};
  try {
    result = await response.json();
  } catch {
    result = {
      success: false,
      status: "failed",
      code: "BACKEND_RESPONSE_INVALID",
      message: "本地后端返回了无效响应",
    };
  }
  return tokenFreePageResult(result);
}

async function handleStart(rawRequest) {
  const request = requestShape(rawRequest);
  if (!request) {
    return tokenFreePageResult({
      status: "failed",
      code: "INVALID_REQUEST",
      message: "浏览器同步请求无效",
    });
  }
  let diagnosticStage = "target_tab_query";
  let createdTabId = null;
  try {
    const { tabId: targetTabId, created: createdTab } = await ensureTargetTab(
      request.targetOrigin,
    );
    createdTabId = createdTab ? targetTabId : null;
    if (targetTabId == null) {
      const payload =
        request.platform === "newapi"
          ? newApiCompletionPayload(request.targetOrigin, null)
          : sub2ApiCompletionPayload(request.targetOrigin, null);
      diagnosticStage = "backend_completion";
      return await submitCompletion(request, payload);
    }
    let session = null;
    if (request.platform === "newapi") {
      const newApiResult = await readNewApiTargetSession(
        targetTabId,
        request.targetOrigin,
        request.targetKind,
        (stage) => {
          diagnosticStage = stage;
        },
      );
      if (newApiResult.result) return newApiResult.result;
      session = newApiResult.session;
    } else {
      diagnosticStage = "page_storage_read";
      session = await readSub2ApiTargetSession(targetTabId);
    }
    const payload =
      request.platform === "newapi"
        ? newApiCompletionPayload(request.targetOrigin, session)
        : sub2ApiCompletionPayload(request.targetOrigin, session);
    diagnosticStage = "backend_completion";
    return await submitCompletion(request, payload);
  } catch (error) {
    return tokenFreePageResult(
      classifyExtensionSyncFailure(error, diagnosticStage),
    );
  } finally {
    if (createdTabId != null) {
      chrome.tabs.remove(createdTabId).catch(() => {});
    }
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.version !== BRIDGE_VERSION) {
    return false;
  }
  if (message?.type === "UPSTREAM_SESSION_BRIDGE_PROBE") {
    sendResponse(
      tokenFreePageResult({
        ok: true,
        status: "ready",
      }),
    );
    return false;
  }
  if (message?.type !== PAGE_REQUEST_TYPE) return false;
  handleStart(message.request).then(sendResponse);
  return true;
});
