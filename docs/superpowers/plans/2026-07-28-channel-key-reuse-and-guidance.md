# Channel Key Reuse and Guidance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist every legitimately submitted channel key, reuse saved keys before protected NewAPI calls, and present missing proof as an actionable warning rather than a channel failure.

**Architecture:** Keep NewAPI unchanged. Centralize local cache synchronization in `app.py`, make matching resolve keys from local sources before the protected endpoint, and add a recoverable `needs_key_verification` API status consumed by the existing React status UI.

**Tech Stack:** Python 3 stdlib HTTP server, PyMySQL, React/TypeScript, Node test runner.

---

### Task 1: Lock the backend behavior with failing tests

**Files:**
- Modify: `tests/test_newapi_channel_key_matching.py`

- [ ] Add a test where a binding contains a real `channel_key`; assert matching succeeds and `fetch_newapi_channel_key` is not called.
- [ ] Add a test where the protected endpoint reports `主站尚未完成 key 读取安全验证`; assert the result is successful-but-pending with `match_status == "needs_key_verification"` and actionable guidance.
- [ ] Add handler tests proving channel creation and key update call `persist_admin_channel_key` with the returned channel ID.
- [ ] Run `python3 -m unittest tests.test_newapi_channel_key_matching -v` and verify the new assertions fail because the behavior is not implemented.

### Task 2: Implement backend key synchronization and resolution

**Files:**
- Modify: `app.py:608-639`
- Modify: `app.py:2724-2870`
- Modify: `app.py:5292-5384`

- [ ] Add `sync_admin_channel_key(admin_site_id, channel_id, submitted_key)` that persists a real key and clears the cache only for an explicitly invalid/empty key.
- [ ] In `match_channel_upstream_binding`, resolve key in this order: `admin_channel_keys`, binding `channel_key`, detail key, `fetch_newapi_channel_key`.
- [ ] Persist a real binding/detail key into `admin_channel_keys` before continuing.
- [ ] Add a helper that recognizes proof-required/invalid/expired errors and returns the approved guidance text.
- [ ] Return `configured: true`, `match_status: "needs_key_verification"`, `matched_groups: []` and no hard error when proof is the only blocker.
- [ ] On successful create/update, synchronize the submitted key instead of clearing it.
- [ ] Run the focused backend test and verify it passes.

### Task 3: Lock and implement the warning presentation

**Files:**
- Create: `tests/web/channel-key-verification-status.test.mjs`
- Modify: `apps/web/src/pages/ChannelsPage.tsx:80-90`

- [ ] Add a Node source-level regression test requiring `needs_key_verification` to map to `warning`.
- [ ] Run `node --test tests/web/channel-key-verification-status.test.mjs` and verify it fails before the UI change.
- [ ] Update `bindingTone` so `needs_key_verification` returns `warning`; leave true refresh and matching errors as danger.
- [ ] Re-run the focused Node test and verify it passes.

### Task 4: Full verification and runtime check

**Files:**
- Verify only; no additional production files expected.

- [ ] Run `python3 -m unittest discover -s tests -v`.
- [ ] Run `node --test tests/web/*.test.mjs`.
- [ ] Run `npm run build` from `apps/web`.
- [ ] Start the project on a free loopback port and verify `/api/auth/status`, `/api/overview`, and `/api/sites` return HTTP 200.
- [ ] Call the channel match endpoint for channel 22 and verify it returns `needs_key_verification` with the approved warning text, without exposing any secret.
- [ ] Inspect `git diff` to ensure no unrelated user changes were overwritten.
