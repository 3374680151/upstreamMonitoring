# Channel Match Error Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the last successful channel ratio through transient upstream failures and show both a clear Chinese cause and the original diagnostic error without breaking the table layout.

**Architecture:** Reuse `channel_upstream_bindings` as a result cache by creating credential-free placeholder rows for inherited local monitor matches. Add a pure frontend error-explanation helper, then use it in the main-site channel table and local channel detail banner. Existing main-site write boundaries and local `sites` CRUD remain unchanged.

**Tech Stack:** Python stdlib HTTP backend, MySQL/PyMySQL, React 19, TypeScript, Vite, Node.js test runner, Python `unittest`.

---

### Task 1: Persist Inherited Match Results

**Files:**
- Modify: `tests/test_newapi_channel_key_matching.py`
- Modify: `app.py:2735-2775`

- [ ] **Step 1: Add a failing placeholder-cache test**

```python
def test_creates_result_cache_for_inherited_monitor_match(self):
    inserted = {"matched_groups_json": None}
    groups = [{"name": "pro", "ratio": 0.05}]
    with patch.object(
        app,
        "get_channel_upstream_binding",
        side_effect=[None, inserted],
    ), patch.object(app, "db_execute") as execute:
        app.persist_channel_match(2, 22, "matched", "匹配成功", groups)

    self.assertEqual(execute.call_count, 2)
    self.assertIn("INSERT INTO channel_upstream_bindings", execute.call_args_list[0].args[0])
    self.assertIn("matched_groups_json = ?", execute.call_args_list[1].args[0])
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python3 -m unittest tests.test_newapi_channel_key_matching.NewApiChannelKeyMatchingTests.test_creates_result_cache_for_inherited_monitor_match`

Expected: FAIL because the current function returns without inserting.

- [ ] **Step 3: Insert a credential-free placeholder before persisting**

```python
binding = get_channel_upstream_binding(admin_site_id, channel_id)
if not binding:
    now = utc_now_iso()
    db_execute(
        """
        INSERT INTO channel_upstream_bindings
        (admin_site_id, channel_id, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON DUPLICATE KEY UPDATE channel_id = VALUES(channel_id)
        """,
        (admin_site_id, channel_id, now, now),
    )
    binding = get_channel_upstream_binding(admin_site_id, channel_id) or {
        "matched_groups_json": None,
    }
```

- [ ] **Step 4: Run persistence tests and verify GREEN**

Run: `python3 -m unittest tests.test_newapi_channel_key_matching.NewApiChannelKeyMatchingTests.test_creates_result_cache_for_inherited_monitor_match tests.test_newapi_channel_key_matching.NewApiChannelKeyMatchingTests.test_preserves_last_successful_groups_when_refresh_fails`

Expected: both tests PASS.

### Task 2: Add User-Facing Error Explanation

**Files:**
- Create: `apps/web/src/lib/upstreamError.ts`
- Create: `tests/web/upstream-error.test.mjs`

- [ ] **Step 1: Write failing explanation tests**

```js
import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = new URL("../../apps/web/src/lib/upstreamError.ts", import.meta.url);
const errorModule = await import(moduleUrl).catch((error) => {
  if (error?.code === "ERR_MODULE_NOT_FOUND") return null;
  throw error;
});

test("explains common upstream errors while retaining raw text", () => {
  assert.notEqual(errorModule, null, "upstreamError.ts is missing");
  assert.deepEqual(
    errorModule.explainUpstreamError("<urlopen error [Errno 54] Connection reset by peer>"),
    {
      summary: "上游主动重置连接",
      raw: "<urlopen error [Errno 54] Connection reset by peer>",
    },
  );
  assert.equal(errorModule.explainUpstreamError("timed out").summary, "连接上游超时");
  assert.equal(errorModule.explainUpstreamError("Name or service not known").summary, "无法解析上游域名");
  assert.equal(errorModule.explainUpstreamError("HTTP 403").summary, "上游拒绝访问（HTTP 403）");
  assert.equal(errorModule.explainUpstreamError("HTTP 429").summary, "上游触发请求限流（HTTP 429）");
});
```

- [ ] **Step 2: Run and verify RED**

Run: `node --test tests/web/upstream-error.test.mjs`

Expected: FAIL because `upstreamError.ts` does not exist.

- [ ] **Step 3: Implement the pure explanation helper**

```ts
export type UpstreamErrorExplanation = { summary: string; raw: string };

export function explainUpstreamError(value: unknown): UpstreamErrorExplanation {
  const raw = String(value || "未知错误").trim() || "未知错误";
  const lower = raw.toLowerCase();
  if (lower.includes("errno 54") || lower.includes("connection reset by peer") || lower.includes("econnreset")) {
    return { summary: "上游主动重置连接", raw };
  }
  if (lower.includes("timed out") || lower.includes("timeout")) {
    return { summary: "连接上游超时", raw };
  }
  if (lower.includes("name or service not known") || lower.includes("temporary failure in name resolution") || lower.includes("nodename nor servname")) {
    return { summary: "无法解析上游域名", raw };
  }
  if (/http\s*403\b/i.test(raw)) return { summary: "上游拒绝访问（HTTP 403）", raw };
  if (/http\s*401\b/i.test(raw)) return { summary: "上游认证失败（HTTP 401）", raw };
  if (/http\s*429\b/i.test(raw)) return { summary: "上游触发请求限流（HTTP 429）", raw };
  return { summary: "上游请求失败", raw };
}
```

- [ ] **Step 4: Run and verify GREEN**

Run: `node --test tests/web/upstream-error.test.mjs`

Expected: all explanation tests PASS.

### Task 3: Render Stale Ratios and Explicit Causes

**Files:**
- Modify: `apps/web/src/pages/ChannelsPage.tsx`
- Modify: `apps/web/src/pages/DetailPage.tsx`
- Modify: `tests/web/main-site-channel-boundary.test.mjs`

- [ ] **Step 1: Add failing UI contract assertions**

```js
test("failed refresh keeps ratios and renders an explicit cause", async () => {
  const source = await readFile(channelsPageUrl, "utf8");
  assert.match(source, /刷新失败，显示上次成功倍率/);
  assert.match(source, /错误原因：\{bindingError\.summary\}/);
  assert.doesNotMatch(source, /:\s*binding\?\.match_message \|\| "未匹配"/);
});
```

Also read `DetailPage.tsx` and assert it contains `错误原因：` plus `原始错误：` and calls `explainUpstreamError`.

- [ ] **Step 2: Run and verify RED**

Run: `node --test tests/web/main-site-channel-boundary.test.mjs`

Expected: FAIL because both pages still render raw errors directly.

- [ ] **Step 3: Update the channel table**

Import `explainUpstreamError`. For each row derive:

```tsx
const bindingError = explainUpstreamError(binding?.match_message);
const refreshFailed = binding?.match_status === "refresh_error" || binding?.match_status === "error";
```

Keep matched group badges when `matchedGroups.length > 0`. Under them render:

```tsx
{refreshFailed ? (
  <div className="mt-1 truncate text-[10px] text-[var(--color-warning-text)]" title={bindingError.raw}>
    刷新失败，显示上次成功倍率 · 错误原因：{bindingError.summary}
  </div>
) : null}
```

When no groups exist, render Badge text `刷新失败`, then a second line `错误原因：{bindingError.summary}` with the raw error in `title`.

- [ ] **Step 4: Update the local detail error banner**

Derive `const siteError = site.last_error ? explainUpstreamError(site.last_error) : null;` and render:

```tsx
<div className="rounded-lg bg-[var(--color-danger-bg)] px-4 py-3 text-sm text-[var(--color-danger-text)]">
  <div className="font-semibold">错误原因：{siteError.summary}</div>
  <div className="mt-1 break-all font-mono text-xs opacity-80">原始错误：{siteError.raw}</div>
</div>
```

- [ ] **Step 5: Run all Web tests and build**

Run: `node --test tests/web/*.test.mjs`

Expected: all tests PASS.

Run: `npm run build` from `apps/web`.

Expected: TypeScript and Vite build PASS.

### Task 4: Full Verification

**Files:**
- Verify all files above.

- [ ] **Step 1: Run all Python tests**

Run: `python3 -m unittest discover -s tests -p 'test_*.py'`

Expected: all tests PASS.

- [ ] **Step 2: Run all Web tests**

Run: `node --test tests/web/*.test.mjs`

Expected: all tests PASS.

- [ ] **Step 3: Build the frontend**

Run: `npm run build` from `apps/web`.

Expected: build succeeds with zero TypeScript errors.

- [ ] **Step 4: Browser QA**

Open `/channels` and `/detail/9` at desktop and 390px widths. Verify the error reason is visible, original error remains available, long text does not overlap columns, and no real main-site write action is invoked.

- [ ] **Step 5: Review diff**

Run: `git diff --check` for all touched files.

Expected: no whitespace errors or unrelated changes.
