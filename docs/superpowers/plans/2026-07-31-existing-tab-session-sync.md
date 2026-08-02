# Existing-Tab Session Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent browser-session sync from opening a fresh upstream page and timing out; use only an already logged-in matching Chrome tab.

**Architecture:** Move same-origin tab selection into a small pure extension adapter so it can be tested without Chrome APIs. The service worker will return the existing token-free `no_session` completion when no matching tab is open, and will never create or wait on a background tab.

**Tech Stack:** Chrome Manifest V3, ES modules, Node built-in test runner.

---

### Task 1: Cover Existing-Tab Selection

**Files:**
- Create: `extensions/upstream-session-bridge/adapters/target-tab.js`
- Modify: `extensions/upstream-session-bridge/tests/session-bridge.test.js`

- [ ] **Step 1: Write the failing test**

```js
test("selects only an already-open tab with the requested origin", () => {
  assert.deepEqual(
    selectExistingTargetTab(
      [
        { id: 3, url: "https://other.example/" },
        { id: 7, url: "https://aiinfinite.online/usage-logs/common" },
      ],
      "https://aiinfinite.online",
    ),
    { id: 7, url: "https://aiinfinite.online/usage-logs/common" },
  );
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `node --test extensions/upstream-session-bridge/tests/session-bridge.test.js`

Expected: the new `target-tab.js` module is missing.

- [ ] **Step 3: Add the pure same-origin selector**

```js
export function selectExistingTargetTab(tabs, targetOrigin) {
  return (Array.isArray(tabs) ? tabs : []).find(
    (tab) => tab?.id && normalizeOrigin(tab.url || tab.pendingUrl || "") === targetOrigin,
  ) || null;
}
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `node --test extensions/upstream-session-bridge/tests/session-bridge.test.js`

Expected: PASS.

### Task 2: Remove Background Tab Creation

**Files:**
- Modify: `extensions/upstream-session-bridge/service-worker.js`
- Modify: `extensions/upstream-session-bridge/tests/session-bridge.test.js`

- [ ] **Step 1: Write the failing worker-contract test**

```js
assert.doesNotMatch(worker, /chrome\.tabs\.create/);
assert.doesNotMatch(worker, /waitForTabComplete/);
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `node --test extensions/upstream-session-bridge/tests/session-bridge.test.js`

Expected: failure because the worker still creates a tab and waits for `complete`.

- [ ] **Step 3: Complete with `no_session` when no matching tab is open**

```js
const tabId = await findExistingTargetTabId(request.targetOrigin);
if (!tabId) {
  const payload = request.platform === "newapi"
    ? newApiCompletionPayload(request.targetOrigin, null)
    : sub2ApiCompletionPayload(request.targetOrigin, null);
  return submitCompletion(request, payload);
}
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `node --test extensions/upstream-session-bridge/tests/session-bridge.test.js`

Expected: PASS.

### Task 3: Build And Verify The Live Flow

**Files:**
- Verify: `extensions/upstream-session-bridge/tests/newapi-adapter.test.js`
- Verify: `tests/test_browser_session_sync.py`
- Verify: `tests/test_newapi_browser_session_sync.py`
- Verify: `apps/web`

- [ ] **Step 1: Run the extension and backend suites**

Run: `node --test extensions/upstream-session-bridge/tests/newapi-adapter.test.js extensions/upstream-session-bridge/tests/session-bridge.test.js && python3 -m unittest tests.test_browser_session_sync tests.test_newapi_browser_session_sync -v`

Expected: all tests pass.

- [ ] **Step 2: Build the production frontend**

Run: `cd apps/web && npm run build`

Expected: Vite build succeeds.

- [ ] **Step 3: Reload the unpacked extension and rerun browser sync**

Expected: the request reaches `ready` and is consumed, without replacing the system token or 2FA state.
