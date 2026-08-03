import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const read = (path) => readFile(new URL(`../../${path}`, import.meta.url), "utf8");

test("frontend types expose browser auth and token-free sync status", async () => {
  const source = await read("apps/web/src/lib/types.ts");
  assert.match(source, /export type AuthMode = "password" \| "token" \| "browser"/);
  assert.match(source, /export type SessionSyncStatus/);
  for (const status of [
    "not_requested",
    "pending",
    "validating",
    "ready",
    "no_session",
    "expired",
    "permission_required",
    "extension_unavailable",
    "failed",
  ]) {
    assert.match(source, new RegExp(`"${status}"`));
  }
  assert.match(source, /session_sync_status\?: SessionSyncStatus/);
  assert.match(source, /session_sync_error\?: string \| null/);
  assert.match(source, /session_synced_at\?: string \| null/);
  assert.match(source, /export type SessionSyncTargetKind = "site" \| "admin_site"/);
  assert.match(source, /target_kind: SessionSyncTargetKind/);
  assert.match(source, /has_browser_session\?: boolean/);
});

test("API client uses exact authenticated site sync routes", async () => {
  const source = await read("apps/web/src/lib/api.ts");
  assert.match(source, /createSiteSessionSync/);
  assert.match(source, /`\/api\/sites\/\$\{siteId\}\/session-sync\/requests`/);
  assert.match(source, /getSiteSessionSync/);
  assert.match(
    source,
    /`\/api\/sites\/\$\{siteId\}\/session-sync\/requests\/\$\{requestId\}`/,
  );
  assert.match(source, /failSiteSessionSync/);
  assert.match(source, /\/fail`/);
});

test("API client keeps ordinary and admin sync routes separate", async () => {
  const source = await read("apps/web/src/lib/api.ts");
  assert.match(source, /createAdminSiteSessionSync/);
  assert.match(
    source,
    /`\/api\/admin\/sites\/\$\{adminSiteId\}\/session-sync\/requests`/,
  );
  assert.match(source, /getAdminSiteSessionSync/);
  assert.match(source, /failAdminSiteSessionSync/);
});

test("browser bridge is versioned, bounded, cleaned up and token-free", async () => {
  const source = await read("apps/web/src/lib/browserSessionBridge.ts");
  assert.match(source, /upstream-session-bridge\/v2/);
  assert.match(source, /event\.source !== window/);
  assert.match(source, /event\.origin !== window\.location\.origin/);
  assert.match(source, /removeEventListener\("message"/);
  assert.match(source, /setTimeout/);
  assert.match(source, /clearTimeout/);
  assert.match(source, /crypto\.randomUUID/);
  assert.doesNotMatch(source, /access_token|refresh_token|token_expires_at/);
  assert.match(source, /target_kind: request\.target_kind/);
  assert.match(source, /COOKIE_PERMISSION_REQUIRED/);
  assert.match(source, /NewAPI 登录 Cookie/);
  assert.match(source, /0\.1\.2/);
  assert.match(
    source,
    /reportBridgeFailure\([\s\S]*?"EXTENSION_UNAVAILABLE",[\s\S]*?EXTENSION_REQUIRED_MESSAGE[\s\S]*?\)/,
  );
});

test("browser bridge keeps a redacted extension diagnostic message for the UI", async () => {
  const source = await read("apps/web/src/lib/browserSessionBridge.ts");
  assert.match(source, /messageOverride\?: string/);
  assert.match(source, /message: messageOverride \|\| response\.data\.message/);
  assert.match(
    source,
    /reportBridgeFailure\([\s\S]*?"SYNC_FAILED",[\s\S]*?bridgeResult\.message[\s\S]*?\)/,
  );
});

test("shared orchestration supports the admin target without mixing routes", async () => {
  const source = await read("apps/web/src/lib/browserSessionBridge.ts");
  assert.match(source, /export async function syncAdminSiteBrowserSession/);
  assert.match(source, /api\.createAdminSiteSessionSync\(adminSiteId\)/);
  assert.match(source, /api\.getAdminSiteSessionSync/);
  assert.match(source, /api\.failAdminSiteSessionSync/);
});

test("shared orchestration creates, starts, reports and polls a sync request", async () => {
  const source = await read("apps/web/src/lib/browserSessionBridge.ts");
  assert.match(source, /export async function syncSiteBrowserSession/);
  assert.match(source, /api\.createSiteSessionSync\(siteId\)/);
  assert.match(source, /probeSessionBridge/);
  assert.match(source, /startSessionBridgeRequest/);
  assert.match(source, /api\.failSiteSessionSync/);
  assert.match(source, /api\.getSiteSessionSync/);
  assert.match(source, /SESSION_SYNC_TERMINAL_STATUSES/);
});

test("sub2api form defaults to browser sync and retains failed saved sites", async () => {
  const source = await read("apps/web/src/components/SiteFormDialog.tsx");
  assert.match(source, /auth_mode: "browser"/);
  assert.match(source, /const browserMode =/);
  assert.match(source, /syncSiteBrowserSession\(targetSiteId\)/);
  assert.match(source, /正在查找浏览器登录态/);
  assert.match(source, /打开上游登录页/);
  assert.match(source, /重新同步/);
  assert.match(source, /稍后处理/);
  assert.match(source, /ExternalLink/);
  assert.match(source, /RefreshCw/);
  assert.match(source, /h-8/);
});

test("sub2api browser form preserves optional password fallback credentials", async () => {
  const source = await read("apps/web/src/components/SiteFormDialog.tsx");
  assert.match(source, /兜底用户邮箱（可选）/);
  assert.match(source, /兜底用户密码（可选）/);
  assert.match(source, /浏览器登录态 → refresh_token → 账号密码/);
  assert.doesNotMatch(
    source,
    /browserMode[\s\S]{0,500}login_username:\s*""[\s\S]{0,120}login_password:\s*""/,
  );
});

test("manual check syncs only after a recoverable browser auth failure and retries once", async () => {
  const [appSource, apiSource, typesSource] = await Promise.all([
    read("apps/web/src/App.tsx"),
    read("apps/web/src/lib/api.ts"),
    read("apps/web/src/lib/types.ts"),
  ]);
  assert.match(typesSource, /export type SiteCheckResponse/);
  assert.match(typesSource, /browser_sync_required\?: boolean/);
  assert.match(apiSource, /request<SiteCheckResponse>\(`\/api\/sites\/\$\{id\}\/check`/);
  assert.match(appSource, /const firstResult = await api\.checkSite\(site\.id\)/);
  assert.match(appSource, /firstResult\.browser_sync_required/);
  assert.match(appSource, /const syncResult = await syncSiteBrowserSession\(site\.id\)/);
  assert.match(appSource, /syncResult\.status !== "ready"/);
  assert.match(appSource, /const retryResult = await api\.checkSite\(site\.id\)/);
  assert.doesNotMatch(appSource, /while\s*\([^)]*browser_sync_required/);
});

test("NewAPI form uses manual token mode and has no browser sync option", async () => {
  const source = await read("apps/web/src/components/SiteFormDialog.tsx");
  assert.match(source, /<option value="token">手动系统访问令牌<\/option>/);
  assert.doesNotMatch(source, /NewAPI Cookie/);
  assert.match(source, /auth_mode: isSub2api \? form\.auth_mode : "token"/);
  assert.doesNotMatch(source, /platform === "newapi"[\s\S]{0,500}syncSiteBrowserSession/);
});

test("admin form can save then sync browser state without replacing token or 2FA", async () => {
  const source = await read("apps/web/src/components/AdminSiteFormDialog.tsx");
  assert.match(source, /syncAdminSiteBrowserSession/);
  assert.match(source, /保存并同步登录态/);
  assert.match(source, /同步浏览器登录态/);
  assert.match(source, /管理员系统访问令牌/);
  assert.match(source, /主站 2FA 验证码/);
  assert.match(source, /COOKIE_PERMISSION_REQUIRED/);
  assert.match(source, /扩展 0\.1\.2 加载时已统一申请站点和 NewAPI Cookie 权限/);
  assert.doesNotMatch(source, /browser_refresh_cookie|browser_session_id|browser_access_token/);
});

test("site table shows compact retry only for retryable browser states", async () => {
  const source = await read("apps/web/src/components/SiteTable.tsx");
  assert.match(source, /onSyncSession/);
  assert.match(source, /isSessionSyncRetryable/);
  assert.match(source, /同步登录态/);
  assert.match(source, /session_sync_status/);
  assert.match(source, /site\.auth_mode === "browser"/);
  assert.match(source, /site\.platform === "sub2api"/);
  assert.match(source, /未登录，需要配置登录/);
  assert.doesNotMatch(source, /aria-label="配置登录"/);
  assert.match(source, /className="shrink-0"/);
  assert.doesNotMatch(
    source,
    /\["pending",\s*"validating"[^\]]*\][^\n]*isSessionSyncRetryable/s,
  );
});

test("site list puts browser sync before main-site sync and scopes it to sub2api", async () => {
  const source = await read("apps/web/src/pages/SitesPage.tsx");
  assert.match(source, /syncSub2ApiBrowserSessions/);
  assert.match(source, /site\.platform === "sub2api" && site\.auth_mode === "browser"/);
  assert.match(source, /仅同步 sub2api 渠道的浏览器登录态/);
  assert.ok(source.indexOf("同步登录态") < source.indexOf("从主站同步"));
});

test("app wires session retry through overview and sites pages", async () => {
  const [appSource, overview, sites] = await Promise.all([
    read("apps/web/src/App.tsx"),
    read("apps/web/src/pages/OverviewPage.tsx"),
    read("apps/web/src/pages/SitesPage.tsx"),
  ]);
  assert.match(appSource, /handleSessionSync/);
  assert.match(appSource, /onSyncSession: handleSessionSync/);
  assert.match(overview, /onSyncSession/);
  assert.match(sites, /onSyncSession/);
});
