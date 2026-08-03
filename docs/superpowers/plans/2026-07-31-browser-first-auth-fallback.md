# Browser-First Authentication Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make sub2api monitoring prefer saved browser AT/RT credentials, fall back to saved account credentials when refresh fails, and ask for an explicit browser sync only during interactive add/edit/manual-check flows.

**Architecture:** A shared backend sub2api authentication executor owns the AT -> RT -> password fallback order and persists every successful token rotation. Group, model, and account fetchers call that executor, while scheduled checks remain backend-only; the React manual-check flow recognizes a structured recoverable-auth response, runs the existing Chrome bridge, then retries once.

**Tech Stack:** Python 3.9 stdlib HTTP server, PyMySQL, React 19, TypeScript, Vite, Chrome Manifest V3, Python `unittest`, Node built-in test runner.

**Worktree note:** Execute in the repository root. Preserve all existing MySQL data and dirty worktree changes. Do not commit without explicit approval.

---

### Task 1: Lock The Browser-First Credential Contract With Backend Tests

**Files:**
- Modify: `tests/test_browser_session_sync.py`
- Modify: `tests/test_sub2api_admin_sites.py`

- [ ] **Step 1: Add a failing test for AT success**

Add a test that passes a browser-mode site with AT, RT, username, and password into the shared executor. Stub the token request to succeed and assert neither refresh nor password login is called.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python3 -m unittest tests.test_browser_session_sync.BrowserSessionSyncTests.test_browser_first_auth_uses_access_token_without_password -v`

Expected: FAIL because the shared executor does not exist.

- [ ] **Step 3: Add failing tests for refresh and password fallback**

Cover AT authentication failure -> RT refresh -> persisted rotated AT/RT; and AT authentication failure -> RT failure -> password login -> persisted replacement session. Assert network/5xx errors are not treated as credential expiry and do not trigger password login.

- [ ] **Step 4: Add a failing test for interactive-auth failure**

Make password login return a Turnstile/2FA error and assert the result exposes a stable `BROWSER_SESSION_REQUIRED` code plus `请先在浏览器登录并同步`, without updating `current_groups_json`.

- [ ] **Step 5: Add a failing persistence test**

Call `persist_site_browser_session` and assert its SQL updates AT/RT/session status without assigning empty values to `login_username` or `login_password`.

- [ ] **Step 6: Run the focused backend suites and confirm all new tests fail for the intended missing behavior**

Run: `python3 -m unittest tests.test_browser_session_sync tests.test_sub2api_admin_sites -v`

Expected: existing tests pass and the new browser-first assertions fail.

### Task 2: Implement One Shared sub2api Credential Executor

**Files:**
- Modify: `app.py` near `ensure_sub2api_admin_session`, `fetch_sub2api_model_data`, `fetch_sub2api_user_groups`, and `fetch_sub2api_account`
- Test: `tests/test_browser_session_sync.py`

- [ ] **Step 1: Add structured authentication helpers**

Introduce a small result contract carrying success, payload, redacted message, and optional error code. Classify only upstream 401/403/token-expired responses as authentication failures; keep network and 5xx failures terminal for the current request.

- [ ] **Step 2: Implement the AT -> RT -> password order**

For `auth_mode=browser`, try saved AT first. On authentication failure, refresh with RT under the existing site refresh lock and persist rotated tokens. If refresh is unavailable or fails for authentication reasons and username/password exist, login once and persist the returned session. Return `BROWSER_SESSION_REQUIRED` when credentials are missing or interactive verification blocks password login.

- [ ] **Step 3: Route groups, models, and account through the executor**

Pass the site ID and saved credentials through all three call paths so rotations are persisted consistently. Keep token-only and password-only legacy behavior compatible.

- [ ] **Step 4: Stop browser sync from deleting fallback credentials**

Remove username/password clearing from `persist_site_browser_session`; keep the AT/RT/session-sync update atomic and token values out of API responses and logs.

- [ ] **Step 5: Run focused suites and verify GREEN**

Run: `python3 -m unittest tests.test_browser_session_sync tests.test_sub2api_admin_sites tests.test_sub2api_channel_key_matching tests.test_sub2api_model_mapping -v`

Expected: all tests pass.

### Task 3: Preserve Previous Ratios And Expose A Recoverable Check Result

**Files:**
- Modify: `app.py` near `detect_site` and `/api/sites/:id/check`
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/lib/api.ts`
- Test: `tests/test_browser_session_sync.py`
- Test: `tests/web/browser-session-sync.test.mjs`

- [ ] **Step 1: Add failing tests for failed detection state**

Seed a site with existing `current_groups_json`, force `BROWSER_SESSION_REQUIRED`, and assert detection updates only status/error metadata. Assert the manual-check API returns `code=BROWSER_SESSION_REQUIRED`, `browser_sync_required=true`, and the site ID while never returning tokens.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python3 -m unittest tests.test_browser_session_sync -v && node --test tests/web/browser-session-sync.test.mjs`

Expected: structured recoverable response assertions fail.

- [ ] **Step 3: Implement the recoverable result**

Propagate the stable error code from the shared executor through `detect_site` and the check route. Keep `current_groups_json` untouched on every failed fetch, including CAPTCHA/2FA failures.

- [ ] **Step 4: Add matching frontend types**

Extend the check response type with optional `code` and `browser_sync_required`; do not add credential fields.

- [ ] **Step 5: Run focused backend and frontend tests and verify GREEN**

Run: `python3 -m unittest tests.test_browser_session_sync -v && node --test tests/web/browser-session-sync.test.mjs`

Expected: all focused tests pass.

### Task 4: Preserve Fallback Passwords In Forms And Sync During Manual Checks

**Files:**
- Modify: `apps/web/src/components/SiteFormDialog.tsx`
- Modify: `apps/web/src/App.tsx`
- Modify: `tests/web/browser-session-sync.test.mjs`

- [ ] **Step 1: Add failing source-contract tests**

Assert browser-mode sub2api forms render optional fallback username/password fields, submit non-empty replacements, preserve existing saved values when fields remain blank, and do not clear fallback credentials after synchronization. Assert manual detection calls the check API first, synchronizes only on `browser_sync_required`, retries exactly once after a ready sync, and never does this in scheduled backend code.

- [ ] **Step 2: Run frontend test and verify RED**

Run: `node --test tests/web/browser-session-sync.test.mjs`

Expected: new form and manual-check orchestration assertions fail.

- [ ] **Step 3: Update the PriceAI-style form**

Under browser mode, show compact optional fallback account fields with warning/info tokens and explain only the credential priority, without exposing AT/RT. Keep existing spacing, light/dark tokens, and button sizing.

- [ ] **Step 4: Implement interactive retry orchestration**

When a user manually checks a browser-mode sub2api site and receives `browser_sync_required`, call `syncSiteBrowserSession(site.id)`. If it returns `ready`, call the check API once more and refresh site data; otherwise display the returned redacted login/sync message. Do not loop and do not invoke the bridge for ordinary HTTP/network errors.

- [ ] **Step 5: Run frontend tests and production build**

Run: `node --test tests/web/*.test.mjs && cd apps/web && npm run build`

Expected: all Node tests pass and Vite exits 0.

### Task 5: Migrate Existing sub2api Sites Without Data Loss

**Files:**
- Modify: `app.py` near incremental schema/data migrations
- Modify: `tests/test_browser_session_sync.py`

- [ ] **Step 1: Add a failing migration test**

Assert the idempotent migration changes existing login-enabled sub2api password/token sites to browser-first even when no browser token has been synchronized yet, while never clearing `login_username`, `login_password`, manually saved AT/RT, `current_groups_json`, snapshots, or changes. Until browser synchronization succeeds, the shared executor continues to use the saved token or password fallback.

- [ ] **Step 2: Run the migration test and verify RED**

Run: `python3 -m unittest tests.test_browser_session_sync -v`

Expected: migration assertion fails because the browser-first migration is absent.

- [ ] **Step 3: Add an idempotent incremental migration**

Use a narrowly scoped parameterized `UPDATE sites` that only changes authentication/session metadata. Never delete rows, rebuild the table, or modify historical/current ratio JSON.

- [ ] **Step 4: Run the complete verification set**

Run: `python3 -m unittest discover -s tests -p 'test_*.py'`

Run: `node --test tests/web/*.test.mjs`

Run: `cd extensions/upstream-session-bridge && node --test tests/*.test.js`

Run: `cd apps/web && npm run build`

Expected: every command exits 0 with no test failures.

- [ ] **Step 5: Perform local API and Chrome regression without printing credentials**

Verify `/api/overview` and `/api/sites` return 200. In Chrome, manually recheck site 12, verify the bridge is only invoked after a recoverable authentication failure, and confirm the resulting site reports `session_sync_status=ready` with non-empty credential-presence booleans and populated groups. Never print AT, RT, passwords, cookies, or sync secrets.
