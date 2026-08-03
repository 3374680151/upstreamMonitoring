# sub2api Browser Session Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an on-demand Chrome extension bridge that imports a logged-in sub2api browser session when a monitor site is created or retried, validates it server-side, persists it in MySQL, and resumes normal monitoring.

**Architecture:** `SiteFormDialog` creates the site first, then asks the authenticated backend for a 60-second one-time sync request. A localhost content script passes the request to an MV3 service worker, which reads only the target Origin's three sub2api Local Storage keys and submits them directly to a secret-protected backend endpoint. The backend validates `/api/v1/auth/me` and group access before atomically replacing credentials and running `detect_site`.

**Tech Stack:** Python 3 stdlib HTTP server, PyMySQL, React 19, TypeScript, Chrome Manifest V3, Node built-in test runner.

**Worktree note:** Execute in the repository root. The current dirty files contain the user's active baseline, so do not create a clean worktree or commit shared dirty files without explicit approval.

---

### Task 1: Add Session Sync Schema And Pure Domain Helpers

**Files:**
- Modify: `app.py` near `DDL_STATEMENTS` and `SITES_COLUMN_ADDITIONS`
- Create: `tests/test_browser_session_sync.py`

- [ ] **Step 1: Write failing schema and normalization tests**

```python
class BrowserSessionSyncTests(unittest.TestCase):
    def test_site_schema_exposes_browser_sync_columns(self):
        self.assertEqual(app.SITES_COLUMN_ADDITIONS["session_sync_status"], "VARCHAR(32) NOT NULL DEFAULT 'not_requested'")
        self.assertIn("browser_session_sync_requests", "\n".join(app.DDL_STATEMENTS))

    def test_normalize_session_expiry_accepts_ms_seconds_and_iso(self):
        self.assertEqual(app.normalize_session_expiry("1785340000000"), "2026-07-29T")
        self.assertEqual(app.normalize_session_expiry("1785340000"), "2026-07-29T")
        self.assertEqual(app.normalize_session_expiry("2026-07-29T23:00:00+08:00"), "2026-07-29T23:00:00+08:00")
        self.assertEqual(app.normalize_session_expiry("bad"), "")
```

- [ ] **Step 2: Run the new test and confirm red state**

Run: `python3 -m unittest tests.test_browser_session_sync -v`

Expected: failures for missing columns/table/helper.

- [ ] **Step 3: Add incremental columns, request table, constants, and helpers**

Implement:

```python
BROWSER_AUTH_MODE = "browser"
SESSION_SYNC_TTL_SECONDS = 60
SESSION_SYNC_TERMINAL_STATUSES = {
    "ready", "no_session", "expired", "permission_required",
    "extension_unavailable", "failed",
}

def normalize_session_expiry(value: Any) -> str:
    # Parse epoch ms, epoch seconds, or timezone-aware ISO; return normalized ISO or "".

def site_origin(base_url: str) -> str:
    # Return exact scheme://netloc for valid HTTP(S) URLs with no embedded credentials.
```

Add `session_sync_status`, `session_sync_error`, and `session_synced_at` to the `sites` table and `SITES_COLUMN_ADDITIONS`. Add `browser_session_sync_requests` with nullable `site_id` and `admin_site_id`, dual foreign keys, and an exactly-one-target check.

- [ ] **Step 4: Run focused tests**

Run: `python3 -m unittest tests.test_browser_session_sync -v`

Expected: all Task 1 tests pass.

### Task 2: Implement One-Time Request Lifecycle

**Files:**
- Modify: `app.py` after database helper functions
- Modify: `tests/test_browser_session_sync.py`

- [ ] **Step 1: Write failing lifecycle tests**

Cover:

```python
def test_create_request_binds_site_platform_and_origin(self): ...
def test_new_request_expires_previous_pending_request(self): ...
def test_secret_is_hashed_and_never_returned_by_status_payload(self): ...
def test_expired_or_consumed_request_cannot_be_claimed(self): ...
def test_claim_uses_constant_time_secret_comparison(self): ...
```

Mock `db_query_one`, `db_query_all`, and `db_execute`; assert SQL parameters never contain the returned plaintext secret except in the caller return value.

- [ ] **Step 2: Verify failures**

Run: `python3 -m unittest tests.test_browser_session_sync.BrowserSessionSyncTests -v`

Expected: missing lifecycle functions.

- [ ] **Step 3: Implement request lifecycle functions**

```python
def create_site_session_sync_request(site_id: int) -> Tuple[bool, Dict[str, Any], Optional[str]]: ...
def get_site_session_sync_request(site_id: int, request_id: str) -> Optional[Dict[str, Any]]: ...
def fail_site_session_sync_request(site_id: int, request_id: str, error_code: str) -> Tuple[bool, Optional[str]]: ...
def claim_session_sync_request(request_id: str, secret: str) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]: ...
def finish_session_sync_request(request_id: str, status: str, code: str = "", message: str = "") -> None: ...
```

Use `secrets.token_urlsafe(32)`, SHA-256, `hmac.compare_digest`, UTC expiry, allowlisted page failure codes, and parameterized SQL. Invalidate older pending/validating requests for the same site before inserting a new one.

- [ ] **Step 4: Run focused tests**

Run: `python3 -m unittest tests.test_browser_session_sync.BrowserSessionSyncTests -v`

Expected: lifecycle tests pass.

### Task 3: Validate And Persist sub2api Browser Sessions

**Files:**
- Modify: `app.py` near sub2api token helpers
- Modify: `tests/test_browser_session_sync.py`
- Modify: `tests/test_sub2api_channel_key_matching.py`

- [ ] **Step 1: Write failing validation and preservation tests**

```python
def test_valid_browser_session_requires_account_and_groups(self): ...
def test_invalid_browser_session_does_not_overwrite_saved_tokens(self): ...
def test_browser_auth_mode_uses_token_path_and_refreshes(self): ...
def test_missing_browser_token_returns_login_required_message(self): ...
def test_refresh_auth_failure_marks_browser_session_expired(self): ...
```

Assert successful persistence updates `auth_mode`, AT, RT, normalized expiry and sync fields in one transaction-sized helper. Assert failures update only sync status/error.

- [ ] **Step 2: Verify failures**

Run: `python3 -m unittest tests.test_browser_session_sync tests.test_sub2api_channel_key_matching -v`

Expected: browser mode currently falls into password login and tests fail.

- [ ] **Step 3: Implement validator and browser-mode routing**

```python
def validate_sub2api_browser_session(base_url: str, access_token: str) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    account_ok, account, account_error = fetch_sub2api_account_by_token(base_url, access_token)
    if not account_ok:
        return False, {}, account_error or "登录态已过期，请重新登录"
    groups_ok, groups, groups_error = fetch_sub2api_groups_by_token(base_url, access_token)
    if not groups_ok:
        return False, {}, groups_error or "当前登录态无法读取分组"
    return True, {"account": account, "groups": groups}, None

def persist_site_browser_session(site_id: int, access_token: str, refresh_token: str, expires_at: str) -> None: ...
```

Treat both `token` and `browser` as token-backed in group, model, key and account helpers. When browser mode lacks AT, return exactly `没有登录态，请提前登录`. Reuse existing refresh locks and persist rotated credentials; set `session_sync_status=expired` only on authenticated expiry, not network/5xx.

- [ ] **Step 4: Run focused suites**

Run: `python3 -m unittest tests.test_browser_session_sync tests.test_sub2api_channel_key_matching -v`

Expected: all pass.

### Task 4: Add Authenticated And Secret-Protected HTTP Routes

**Files:**
- Modify: `app.py` request handler routing and public-route guard
- Modify: `tests/test_browser_session_sync.py`

- [ ] **Step 1: Write failing route tests**

Exercise handler methods with mocks for:

```text
POST /api/sites/:id/session-sync/requests
GET  /api/sites/:id/session-sync/requests/:request_id
POST /api/sites/:id/session-sync/requests/:request_id/fail
POST /api/session-sync/requests/:request_id/complete
```

Assert only the complete endpoint bypasses console auth; it still rejects missing/wrong secret, Origin mismatch, platform mismatch, oversize fields, expired requests, and replays. Assert responses never include tokens.

- [ ] **Step 2: Verify failures**

Run: `python3 -m unittest tests.test_browser_session_sync -v`

Expected: 404/auth failures before routes exist.

- [ ] **Step 3: Implement routes and bounded input**

Add `is_public_api_path(path)` so only the exact dynamic complete route bypasses console auth. Add a bounded JSON reader for this endpoint, allow only `session_found` and `no_session`, validate exact origin/platform, claim before network validation, persist only after validation, call `detect_site`, and return status summaries.

Page-side failure must accept only:

```python
SESSION_SYNC_PAGE_FAILURES = {
    "EXTENSION_UNAVAILABLE": ("extension_unavailable", "未安装或未连接浏览器同步扩展"),
    "ORIGIN_PERMISSION_REQUIRED": ("permission_required", "扩展需要该站点的读取权限"),
    "SYNC_FAILED": ("failed", "登录态同步失败"),
}
```

- [ ] **Step 4: Run focused tests**

Run: `python3 -m unittest tests.test_browser_session_sync -v`

Expected: all route and redaction tests pass.

### Task 5: Build The Manifest V3 Extension And Unit-Test Its Core

**Files:**
- Create: `extensions/upstream-session-bridge/manifest.json`
- Create: `extensions/upstream-session-bridge/service-worker.js`
- Create: `extensions/upstream-session-bridge/content-script.js`
- Create: `extensions/upstream-session-bridge/adapters/sub2api.js`
- Create: `extensions/upstream-session-bridge/popup.html`
- Create: `extensions/upstream-session-bridge/popup.js`
- Create: `extensions/upstream-session-bridge/README.md`
- Create: `extensions/upstream-session-bridge/tests/session-bridge.test.js`

- [ ] **Step 1: Write failing pure-logic tests**

```javascript
import test from "node:test";
import assert from "node:assert/strict";
import { normalizeOrigin, readSub2ApiSessionValues, completionPayload } from "../adapters/sub2api.js";

test("reads only the three sub2api keys", () => {
  const values = new Map([["auth_token", "at"], ["refresh_token", "rt"], ["token_expires_at", "1"], ["password", "secret"]]);
  assert.deepEqual(readSub2ApiSessionValues((key) => values.get(key)), {
    access_token: "at", refresh_token: "rt", token_expires_at: "1",
  });
});
```

Also test exact Origin comparison, no-session payload, backend URL loopback restriction, and that no token enters extension storage or page response payloads.

- [ ] **Step 2: Verify failures**

Run: `node --test extensions/upstream-session-bridge/tests/*.test.js`

Expected: module-not-found failure.

- [ ] **Step 3: Implement extension modules**

Manifest uses MV3, module service worker, `tabs`, `scripting`, `storage`, loopback content-script matches, and optional HTTP(S) host permissions. The content script exposes a versioned `window.postMessage` handshake. The service worker validates loopback backend origin, requests exact target permission, uses an existing exact-Origin tab or creates an inactive temporary tab, executes the self-contained sub2api reader, posts directly to the complete endpoint, closes temporary tabs in `finally`, and sends only status back to the page.

- [ ] **Step 4: Run extension tests**

Run: `node --test extensions/upstream-session-bridge/tests/*.test.js`

Expected: all pass.

### Task 6: Add Frontend Types, API Client, And Bridge Client

**Files:**
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/lib/api.ts`
- Create: `apps/web/src/lib/browserSessionBridge.ts`
- Create: `tests/web/browser-session-sync.test.mjs`

- [ ] **Step 1: Write failing source-contract tests**

Assert `AuthMode` includes `browser`, `Site` exposes sync status fields, API methods use the exact routes, and the bridge uses versioned messages with a bounded handshake timeout and never places session keys in page messages.

- [ ] **Step 2: Verify failures**

Run: `node --test tests/web/browser-session-sync.test.mjs`

Expected: missing types/API/bridge failures.

- [ ] **Step 3: Implement frontend contracts**

```typescript
export type AuthMode = "password" | "token" | "browser";
export type SessionSyncStatus = "not_requested" | "pending" | "validating" | "ready" | "no_session" | "expired" | "permission_required" | "extension_unavailable" | "failed";
```

Add `createSiteSessionSync`, `getSiteSessionSync`, and `failSiteSessionSync`. Implement `probeSessionBridge()` and `startSessionBridgeRequest()` using random correlation IDs, exact source checks, listener cleanup, and no token-shaped fields.

- [ ] **Step 4: Run contract test and build**

Run: `node --test tests/web/browser-session-sync.test.mjs`

Run: `cd apps/web && npm run build`

Expected: both pass.

### Task 7: Integrate Save-Time Sync And Row Retry UI

**Files:**
- Modify: `apps/web/src/components/SiteFormDialog.tsx`
- Modify: `apps/web/src/components/SiteTable.tsx`
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/pages/OverviewPage.tsx`
- Modify: `apps/web/src/pages/SitesPage.tsx`
- Modify: `tests/web/browser-session-sync.test.mjs`

- [ ] **Step 1: Extend failing UI source tests**

Assert browser mode is the sub2api default, browser mode hides credential inputs, save waits for sync terminal state, exact messages/actions exist, row retry appears only for retryable sync states, and buttons use Lucide icons and stable compact dimensions.

- [ ] **Step 2: Verify failures**

Run: `node --test tests/web/browser-session-sync.test.mjs`

Expected: UI assertions fail.

- [ ] **Step 3: Implement a shared orchestration function**

In `browserSessionBridge.ts`, add `syncSiteBrowserSession(siteId)` that creates a request, probes/starts the extension, reports page-side bridge failures, polls authenticated status until a terminal state or deadline, and returns a token-free result.

- [ ] **Step 4: Implement form and table behavior**

For new sub2api browser-mode sites, save first, retain returned ID, show `正在查找浏览器登录态`, then run sync and close only on ready. Preserve the created site on failure and show `打开上游登录页`, `重新同步`, and `稍后处理`. Add a compact session badge and retry action to `SiteTable`; wire it through page props and `App` with toast/refresh behavior.

- [ ] **Step 5: Run web tests and build**

Run: `node --test tests/web/*.test.mjs`

Run: `cd apps/web && npm run build`

Expected: all pass.

### Task 8: Full Verification And Real Chrome Handoff

**Files:**
- Modify if needed: `README.md`
- Modify if needed: `extensions/upstream-session-bridge/README.md`

- [ ] **Step 1: Run complete automated verification**

Run: `python3 -m unittest discover -s tests`

Run: `node --test tests/web/*.test.mjs extensions/upstream-session-bridge/tests/*.test.js`

Run: `cd apps/web && npm run build`

Expected: zero failures and build exit 0.

- [ ] **Step 2: Start the production-style local app**

Run: `cd apps/web && npm run build && cd ../.. && python3 app.py`

Verify `/api/overview`, `/api/sites`, sync request creation, and that no secret appears in HTTP logs.

- [ ] **Step 3: Load the unpacked extension with user confirmation**

Open `chrome://extensions`, enable Developer mode, and load `extensions/upstream-session-bridge`. This is a browser extension installation and must receive action-time user confirmation before the final load click.

- [ ] **Step 4: Exercise the real target flow without printing secrets**

Use `https://api.stpatrickschoolgnv.org` to verify logged-in, no-session, permission-denied, and expired states. Query MySQL only for `has_access_token`, `has_refresh_token`, sync status, group count, and timestamps.

- [ ] **Step 5: Capture desktop/mobile UI verification**

Use Playwright screenshots for the add form, no-session state, and site table at desktop and mobile widths. Confirm no overlap, stable button sizing, PriceAI tokens, and dark theme readability.
