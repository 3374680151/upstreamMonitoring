export const BRIDGE_VERSION = "upstream-session-bridge/v2";

export function normalizeOrigin(value) {
  try {
    const url = new URL(String(value || ""));
    if (!new Set(["http:", "https:"]).has(url.protocol)) return "";
    if (url.username || url.password || !url.hostname) return "";
    return url.origin.toLowerCase();
  } catch {
    return "";
  }
}

export function readSub2ApiSessionValues(getItem) {
  const accessToken = String(getItem("auth_token") || "").trim();
  const refreshToken = String(getItem("refresh_token") || "").trim();
  const tokenExpiresAt = String(getItem("token_expires_at") || "").trim();
  if (!accessToken || !refreshToken) return null;
  return {
    access_token: accessToken,
    refresh_token: refreshToken,
    token_expires_at: tokenExpiresAt,
  };
}

export function completionPayload(observedOrigin, session) {
  const origin = normalizeOrigin(observedOrigin);
  if (!session) {
    return {
      status: "no_session",
      platform: "sub2api",
      observed_origin: origin,
    };
  }
  return {
    status: "session_found",
    platform: "sub2api",
    observed_origin: origin,
    session,
  };
}

export function isLoopbackCompletionUrl(value) {
  try {
    const url = new URL(String(value || ""));
    const loopbackHosts = new Set(["localhost", "127.0.0.1", "[::1]"]);
    return (
      new Set(["http:", "https:"]).has(url.protocol) &&
      loopbackHosts.has(url.hostname) &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash &&
      /^\/api\/session-sync\/requests\/[A-Za-z0-9_-]{1,64}\/complete$/.test(
        url.pathname,
      )
    );
  } catch {
    return false;
  }
}

export function tokenFreePageResult(value = {}) {
  return {
    ok: value.success === true || value.ok === true,
    status: String(value.status || "failed"),
    code: String(value.code || ""),
    message: String(value.message || ""),
  };
}
