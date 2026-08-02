import { normalizeOrigin } from "./sub2api.js";

const LEGACY_STORAGE_KEYS = ["user", "access_token", "token", "user_id"];

function text(value) {
  return String(value ?? "").trim();
}

export function readNewApiLegacySessionValues(getItem) {
  const values = Object.fromEntries(
    LEGACY_STORAGE_KEYS.map((key) => [key, getItem(key)]),
  );
  let user = {};
  const rawUser = text(values.user);
  if (rawUser) {
    try {
      user = JSON.parse(rawUser);
    } catch {
      return null;
    }
    if (!user || typeof user !== "object" || Array.isArray(user)) return null;
  }
  const accessToken = text(
    user.access_token || user.token || values.access_token || values.token,
  );
  const accessUserId = text(
    user.id || user.user_id || user.userId || values.user_id,
  );
  if (!accessToken || !accessUserId) return null;
  return {
    access_token: accessToken,
    access_user_id: accessUserId,
  };
}

export function selectNewApiRefreshCookie(cookies) {
  if (!Array.isArray(cookies)) return "";
  const cookie = cookies.find((item) => item?.name === "new_api_refresh");
  return text(cookie?.value);
}

export function normalizeNewApiInMemoryAuth(auth) {
  if (!auth || typeof auth !== "object" || Array.isArray(auth)) return null;
  const accessToken = text(auth.accessToken);
  const accessUserId = text(auth.user?.id);
  const sessionId = text(auth.session?.sid);
  const expiresAt = Number(auth.accessExpiresAt);
  if (
    !accessToken ||
    !accessUserId ||
    !sessionId ||
    !Number.isFinite(expiresAt) ||
    expiresAt <= 0
  ) {
    return null;
  }
  return {
    access_token: accessToken,
    access_user_id: accessUserId,
    browser_session_id: sessionId,
    browser_access_expires_at: expiresAt,
  };
}

export function normalizeNewApiRefreshBundle(bundle, refreshCookieValue) {
  if (!bundle || typeof bundle !== "object" || Array.isArray(bundle)) return null;
  const auth = normalizeNewApiInMemoryAuth({
    accessToken: bundle.access_token,
    accessExpiresAt: bundle.access_expires_at,
    user: bundle.user,
    session: bundle.session,
  });
  const refreshCookie = text(refreshCookieValue);
  if (!auth || !refreshCookie) {
    return null;
  }
  return {
    ...auth,
    browser_refresh_cookie: `new_api_refresh=${refreshCookie}`,
  };
}

export function newApiCompletionPayload(observedOrigin, session) {
  const origin = normalizeOrigin(observedOrigin);
  if (!session) {
    return {
      status: "no_session",
      platform: "newapi",
      observed_origin: origin,
    };
  }
  return {
    status: "session_found",
    platform: "newapi",
    observed_origin: origin,
    session,
  };
}
