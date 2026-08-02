# NewAPI Browser Session Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the verified browser-session bridge to import and refresh NewAPI ordinary-user and admin dashboard sessions without weakening existing system-token or 2FA-proof boundaries.

**Architecture:** Add a NewAPI extension adapter with two explicit variants: legacy page-storage credentials and modern session-bound auth refreshed in the target page Origin plus a narrowly named HttpOnly refresh cookie. The generic sync request table targets either `sites` or `admin_sites`; backend adapters validate the target kind and write only the corresponding existing/new browser-session columns.

**Tech Stack:** Existing Python/PyMySQL backend, React/TypeScript UI, the phase-one MV3 extension, Chrome Cookies and Scripting APIs, Node built-in tests.

**Prerequisite:** Complete every sub2api plan task and retain its passing verification evidence before starting this plan.

---

### Task 1: Generalize Sync Requests To `admin_sites`

**Files:**
- Modify: `app.py`
- Modify: `tests/test_browser_session_sync.py`

- [ ] Add failing tests for exactly-one-target rows, admin request creation, site/admin route separation, and cross-target replay rejection.
- [ ] Run `python3 -m unittest tests.test_browser_session_sync -v` and confirm failures.
- [ ] Implement `create_admin_site_session_sync_request(admin_site_id)` and generic target resolution while retaining exact platform/origin binding.
- [ ] Run the focused suite and confirm pass.

Core target shape:

```python
{"target_kind": "site", "site_id": 12, "admin_site_id": None}
{"target_kind": "admin_site", "site_id": None, "admin_site_id": 7}
```

### Task 2: Add NewAPI Browser Session Columns And Refresh Adapter For `sites`

**Files:**
- Modify: `app.py`
- Create: `tests/test_newapi_browser_session_sync.py`

- [ ] Write failing tests for `browser_refresh_cookie`, `browser_session_id`, and `browser_access_expires_at` site migrations; legacy token persistence; modern session persistence; exact Origin refresh; and no admin-token leakage through site payloads.
- [ ] Run `python3 -m unittest tests.test_newapi_browser_session_sync -v` and confirm failures.
- [ ] Add incremental site columns and a NewAPI site browser-session helper that mirrors existing admin refresh semantics without sharing rows.
- [ ] Validate ordinary sessions with `/api/user/self` and group endpoints; reject missing user ID and do not treat admin role alone as adequate ordinary-user configuration.
- [ ] Run focused NewAPI tests and existing `tests.test_newapi_channel_key_matching`.

### Task 3: Implement Legacy And Modern NewAPI Extension Adapters

**Files:**
- Create: `extensions/upstream-session-bridge/adapters/newapi.js`
- Modify: `extensions/upstream-session-bridge/manifest.json`
- Modify: `extensions/upstream-session-bridge/service-worker.js`
- Create: `extensions/upstream-session-bridge/tests/newapi-adapter.test.js`

- [ ] Write failing pure tests for allowlisted legacy storage shapes, user-ID extraction, modern refresh bundle normalization, exact cookie-name selection, and rejection of partial/mixed-version sessions.
- [ ] Run `node --test extensions/upstream-session-bridge/tests/*.test.js` and confirm failures.
- [ ] Implement legacy extraction from explicitly listed NewAPI keys only.
- [ ] Implement modern same-Origin `POST /api/user/auth/refresh` execution in the page main world, then read only `new_api_refresh` through `chrome.cookies.get` after NewAPI-specific permission approval.
- [ ] Ensure sub2api never requests `cookies`; run all extension tests.

Modern normalized payload:

```javascript
{
  access_token: bundle.access_token,
  access_user_id: String(bundle.user.id),
  browser_session_id: bundle.session.sid,
  browser_refresh_cookie: `new_api_refresh=${cookie.value}`,
  browser_access_expires_at: bundle.access_expires_at,
}
```

### Task 4: Add NewAPI Completion Validation And Persistence

**Files:**
- Modify: `app.py`
- Modify: `tests/test_newapi_browser_session_sync.py`
- Modify: `tests/test_browser_session_sync.py`

- [ ] Write failing tests for legacy/modern payload allowlists, site/admin target dispatch, exact Origin mismatch, cookie redaction, invalid session preservation, and one-time replay.
- [ ] Run focused tests and confirm failures.
- [ ] Add `validate_newapi_site_browser_session`, `persist_newapi_site_browser_session`, and admin persistence dispatch using existing `_persist_admin_browser_auth`.
- [ ] Keep system access token and browser session as separate credentials; never overwrite `admin_sites.access_token` with dashboard AT.
- [ ] Run focused tests and confirm pass.

### Task 5: Add NewAPI UI Modes And Permission Guidance

**Files:**
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/components/SiteFormDialog.tsx`
- Modify: `apps/web/src/components/AdminSiteFormDialog.tsx`
- Modify: `apps/web/src/components/SiteTable.tsx`
- Modify: `tests/web/browser-session-sync.test.mjs`

- [ ] Extend failing UI contracts for NewAPI browser mode, modern Cookie permission guidance, ordinary/admin target distinction, and preserved manual system-token mode.
- [ ] Run the web test and confirm failures.
- [ ] Add browser sync as an explicit NewAPI option rather than replacing existing system-token fields. Show Cookie permission only for modern NewAPI and retain the existing 2FA proof control for channel-key reads.
- [ ] Run all web tests and the production build.

### Task 6: Full NewAPI Regression And Browser Verification

**Files:**
- Modify if needed: `extensions/upstream-session-bridge/README.md`
- Modify if needed: `README.md`

- [ ] Run `python3 -m unittest discover -s tests`.
- [ ] Run `node --test tests/web/*.test.mjs extensions/upstream-session-bridge/tests/*.test.js`.
- [ ] Run `cd apps/web && npm run build`.
- [ ] With action-time permission confirmation, verify one legacy NewAPI site and `https://aiinfinite.online` modern session import without printing tokens/cookies.
- [ ] Verify `admin_sites` import leaves the system token and 2FA proof fields unchanged.
- [ ] Verify sub2api sync still requests no Cookie permission and passes its real target flow.

