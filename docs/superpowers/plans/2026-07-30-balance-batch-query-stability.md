# Balance Batch Query Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the balance page's one-click action query upstream sites one at a time so it has the same stable request cadence as a single-site query.

**Architecture:** Keep the existing `queryOne` function as the only per-site request and row-state owner. Change only `queryAll` orchestration from concurrent fan-out to an ordered loop that awaits each result and preserves the current aggregate notification behavior.

**Tech Stack:** React 19, TypeScript, Node.js built-in test runner, Vite

---

### Task 1: Serialize balance batch queries

**Files:**
- Create: `tests/web/balance-batch-query.test.mjs`
- Modify: `apps/web/src/lib/useBalances.ts:57-72`

- [x] **Step 1: Write the failing regression test**

```js
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const useBalancesUrl = new URL(
  "../../apps/web/src/lib/useBalances.ts",
  import.meta.url,
);

test("one-click balance query waits for each site before starting the next", async () => {
  const source = await readFile(useBalancesUrl, "utf8");
  const start = source.indexOf("  const queryAll = useCallback(async () => {");
  const end = source.indexOf("\n\n  const summary = useMemo", start);

  assert.notEqual(start, -1, "queryAll function is missing");
  assert.notEqual(end, -1, "queryAll function boundary is missing");

  const queryAll = source.slice(start, end);
  assert.match(
    queryAll,
    /const results: boolean\[\] = \[\];[\s\S]*for \(const site of sites\) \{\s*results\.push\(await queryOne\(site, false\)\);\s*\}/,
  );
  assert.doesNotMatch(queryAll, /Promise\.all/);
});
```

- [x] **Step 2: Run the focused test and verify RED**

Run: `node --test tests/web/balance-batch-query.test.mjs`

Expected: FAIL because the current `queryAll` contains `Promise.all` and no awaited ordered loop.

- [x] **Step 3: Implement the minimal ordered loop**

Replace the existing concurrent call inside `queryAll` with:

```ts
const results: boolean[] = [];
for (const site of sites) {
  results.push(await queryOne(site, false));
}
```

Update the nearby comment from “并发拉取” to “逐个拉取”. Do not change `queryOne`, API contracts, retries, row rendering, or toast wording.

- [x] **Step 4: Run the focused test and verify GREEN**

Run: `node --test tests/web/balance-batch-query.test.mjs`

Expected: PASS with one test and zero failures.

- [x] **Step 5: Run regression verification**

Run: `node --test tests/web/*.test.mjs`

Expected: all Web Node tests pass.

Run: `npm run build` from `apps/web`.

Expected: TypeScript and Vite production build exit with status 0.

Run: `python3 -m py_compile app.py`

Expected: exit status 0 with no output.

Run: `git diff --check -- apps/web/src/lib/useBalances.ts tests/web/balance-batch-query.test.mjs`

Expected: exit status 0 with no whitespace errors.

- [x] **Step 6: Preserve the dirty-worktree boundary**

Do not stage or commit the implementation: `apps/web/src/lib/useBalances.ts` is pre-existing untracked user work, so committing it would also claim unrelated content. Report the exact modified and added files instead.
