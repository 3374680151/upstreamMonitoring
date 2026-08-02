# Single-Channel Ratio Refresh Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the row-level “刷新倍率” action refresh only current upstream group/ratio data while reusing a persisted channel key and reaching the protected main-site key endpoint only when no key is available.

**Architecture:** Keep the existing channel-scoped backend endpoint and its key lookup order unchanged. Remove the frontend's accidental `forceRefresh=true` argument, lock that request policy with a focused source regression, then verify the existing backend persisted-key behavior.

**Tech Stack:** React 19, TypeScript, Node.js built-in test runner, Python `unittest`, PyMySQL, Vite.

---

## File Map

- Create `tests/web/channel-ratio-refresh.test.mjs`: regression proving the manual row action does not request forced channel-key refresh.
- Modify `apps/web/src/pages/ChannelsPage.tsx`: remove the force-refresh boolean from the manual ratio-refresh request.
- Verify `tests/test_newapi_channel_key_matching.py`: existing backend coverage for persisted-key reuse and protected-endpoint fallback.
- Do not modify `app.py` or `apps/web/src/lib/api.ts`; their current default `forceRefresh=false` behavior already matches the approved design.

The repository is a shared dirty `master` checkout. `ChannelsPage.tsx` and the test tree are part of the user's current uncommitted work, so implementation files must remain uncommitted. Preserve all unrelated staged and unstaged changes.

### Task 1: Lock the Manual Row Request Policy

**Files:**
- Create: `tests/web/channel-ratio-refresh.test.mjs`
- Modify: `apps/web/src/pages/ChannelsPage.tsx:391-397`

- [ ] **Step 1: Write the failing frontend regression**

Create `tests/web/channel-ratio-refresh.test.mjs`:

```js
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const channelsPageUrl = new URL(
  "../../apps/web/src/pages/ChannelsPage.tsx",
  import.meta.url,
);

test("manual ratio refresh reuses the current channel key", async () => {
  const source = await readFile(channelsPageUrl, "utf8");
  const start = source.indexOf("  async function matchUpstream(ch: Channel)");
  const end = source.indexOf("\n  async function submitForm", start);

  assert.notEqual(start, -1, "matchUpstream function is missing");
  assert.notEqual(end, -1, "matchUpstream function boundary is missing");

  const matchUpstream = source.slice(start, end);
  assert.match(
    matchUpstream,
    /api\.matchChannelUpstreamBinding\(siteId!, ch\.id\)/,
  );
  assert.doesNotMatch(
    matchUpstream,
    /api\.matchChannelUpstreamBinding\(siteId!, ch\.id,\s*true\)/,
  );
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
node --test tests/web/channel-ratio-refresh.test.mjs
```

Expected: one failure because `matchUpstream` currently calls `matchChannelUpstreamBinding(siteId!, ch.id, true)`.

- [ ] **Step 3: Implement the minimal frontend fix**

In `matchUpstream`, replace only the request line:

```ts
const resp = await api.matchChannelUpstreamBinding(siteId!, ch.id);
```

Do not change automatic page-entry refresh, row state, error handling, or the API helper.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```bash
node --test tests/web/channel-ratio-refresh.test.mjs
```

Expected: one test passes and zero fail.

- [ ] **Step 5: Verify the backend key lookup contract**

Run:

```bash
python3 -m unittest \
  tests.test_newapi_channel_key_matching.NewApiChannelKeyMatchingTests.test_uses_persisted_admin_key_without_requesting_2fa_protected_endpoint \
  tests.test_newapi_channel_key_matching.NewApiChannelKeyMatchingTests.test_retries_channel_key_once_after_access_token_expiry \
  tests.test_newapi_channel_key_matching.NewApiChannelKeyMatchingTests.test_caches_successful_channel_key_until_forced_refresh \
  -v
```

Expected: all three tests pass. Normal matching skips the protected endpoint when a
persisted key exists, missing-key lookup can still reach and retry that endpoint, and
explicit forced refresh remains available.

- [ ] **Step 6: Preserve the shared working tree**

Run `git status --short` and `git diff -- apps/web/src/pages/ChannelsPage.tsx tests/web/channel-ratio-refresh.test.mjs`.

Expected: the intended one-line production change and new regression are visible while all pre-existing user changes remain untouched. Do not commit implementation files.

### Task 2: Full Verification and Running Service

**Files:**
- Verify only.

- [ ] **Step 1: Run all automated checks**

```bash
python3 -m unittest discover -s tests -v
node --experimental-strip-types --test tests/web/*.test.mjs
npm --prefix apps/web run build
python3 -m py_compile app.py
docker compose config --quiet
git diff --check
```

Expected: every command exits zero; all Python and Node tests pass; Vite creates a production build without warnings or errors.

- [ ] **Step 2: Restore the intentional generated-file deletion state**

If the build regenerates `apps/web/tsconfig.tsbuildinfo`, remove only that generated file with `apply_patch` and leave its existing staged deletion intact. `git status --short -- apps/web/tsconfig.tsbuildinfo` must report `D  apps/web/tsconfig.tsbuildinfo`.

- [ ] **Step 3: Restart the optimized local service**

Stop the currently running process on port 8001, then run:

```bash
PORT=8001 python3 app.py
```

Keep the process running. Expected startup line: `Upstream Ratio Watch running at http://127.0.0.1:8001 (ui=apps/web/dist)`.

- [ ] **Step 4: Smoke-test the console and single-channel endpoint**

Run the public route checks without printing response bodies:

```bash
for endpoint in /api/overview /api/sites /api/admin/sites /channels; do
  /usr/bin/curl -sS --max-time 15 -o /dev/null \
    -w "$endpoint %{http_code} %{time_total}\n" \
    "http://127.0.0.1:8001$endpoint"
done
```

Then issue one normal channel-scoped ratio refresh without printing its body:

```bash
/usr/bin/curl -sS --max-time 30 -o /dev/null \
  -w "/api/admin/sites/2/channels/10/match %{http_code} %{time_total}\n" \
  -X POST -H 'Content-Type: application/json' -d '{}' \
  http://127.0.0.1:8001/api/admin/sites/2/channels/10/match
```

Expected: all console routes and the match request return HTTP 200. The match URL has no `refresh=1`, targets channel 10 only, and uses its persisted key.

- [ ] **Step 5: Final working-tree and runtime check**

```bash
/usr/bin/curl -sS --max-time 5 -o /dev/null \
  -w "health %{http_code} %{time_total}\n" \
  http://127.0.0.1:8001/api/auth/status
git diff --check
git status --short
```

Expected: health returns HTTP 200, the server remains running, the diff check exits zero, and no unrelated working-tree state has changed.
