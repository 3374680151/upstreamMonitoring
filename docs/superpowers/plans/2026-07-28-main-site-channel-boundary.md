# Main-Site Channel Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make local `sites` the only full CRUD monitoring configuration, while limiting the main-site channel page to read-only display plus priority-only editing.

**Architecture:** Keep all backend routes and database tables intact. Simplify `ChannelsPage` so it only reads main-site channels, refreshes ratio matches, and submits `{ priority }`; move the priority form into a focused `ChannelPriorityDialog` component. Add source-contract tests matching the repository's existing `node:test` pattern to prevent destructive main-site controls from returning.

**Tech Stack:** React 19, TypeScript, Vite, Tailwind CSS, Node.js built-in test runner, Python `unittest`, existing stdlib HTTP backend.

---

## File Structure

- Create `apps/web/src/components/ChannelPriorityDialog.tsx`: priority-only edit modal with local validation and error state.
- Modify `apps/web/src/pages/ChannelsPage.tsx`: remove main-site deletion and full channel management; retain display, filtering, ratio matching, and priority editing.
- Modify `apps/web/src/components/SiteFormDialog.tsx`: describe local monitoring credentials as the source reused by main-site channel matching.
- Create `tests/web/main-site-channel-boundary.test.mjs`: static contract tests that reject destructive main-site UI calls and verify priority-only updates.

### Task 1: Add Failing Main-Site Boundary Tests

**Files:**
- Create: `tests/web/main-site-channel-boundary.test.mjs`
- Test: `tests/web/main-site-channel-boundary.test.mjs`

- [ ] **Step 1: Write the failing page-boundary test**

```js
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const channelsPageUrl = new URL(
  "../../apps/web/src/pages/ChannelsPage.tsx",
  import.meta.url,
);
const priorityDialogUrl = new URL(
  "../../apps/web/src/components/ChannelPriorityDialog.tsx",
  import.meta.url,
);
const siteDialogUrl = new URL(
  "../../apps/web/src/components/SiteFormDialog.tsx",
  import.meta.url,
);

test("main-site page exposes no destructive channel management", async () => {
  const source = await readFile(channelsPageUrl, "utf8");

  assert.doesNotMatch(source, /api\.deleteAdminSite\(/);
  assert.doesNotMatch(source, /api\.createChannel\(/);
  assert.doesNotMatch(source, /api\.deleteChannel\(/);
  assert.doesNotMatch(source, /api\.batchChannels\(/);
  assert.doesNotMatch(source, /api\.channelDetail\(/);
  assert.doesNotMatch(source, /删除主站|添加渠道|批量删除渠道/);
});

test("main-site channel updates are priority-only", async () => {
  const source = await readFile(channelsPageUrl, "utf8");

  assert.match(
    source,
    /api\.updateChannel\(siteId!, channel\.id, \{ priority \}\)/,
  );
  assert.doesNotMatch(source, /ChannelFormDialog/);
  assert.match(source, /ChannelPriorityDialog/);
});

test("priority dialog contains no other channel settings", async () => {
  const source = await readFile(priorityDialogUrl, "utf8");

  assert.match(source, /label="优先级 priority"/);
  assert.doesNotMatch(
    source,
    /密钥|Base URL|模型重定向|自动禁用|上游认证|访问令牌|用户密码/,
  );
});

test("local monitor form explains base-url credential reuse", async () => {
  const source = await readFile(siteDialogUrl, "utf8");

  assert.match(source, /按 Base URL 自动匹配并复用这里的登录态/);
  assert.doesNotMatch(source, /主站下渠道的增删改/);
  assert.doesNotMatch(source, /渠道单独配置了上游登录态/);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --test tests/web/main-site-channel-boundary.test.mjs`

Expected: FAIL because `ChannelPriorityDialog.tsx` does not exist and `ChannelsPage.tsx` still calls the main-site create/delete/batch/detail APIs.

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/web/main-site-channel-boundary.test.mjs
git commit -m "test: define main-site channel safety boundary"
```

### Task 2: Add the Priority-Only Dialog

**Files:**
- Create: `apps/web/src/components/ChannelPriorityDialog.tsx`
- Test: `tests/web/main-site-channel-boundary.test.mjs`

- [ ] **Step 1: Implement the focused dialog**

```tsx
import { useEffect, useState } from "react";
import type { Channel } from "@/lib/types";
import { Button, Field, Input, Modal } from "./ui";

export function ChannelPriorityDialog({
  open,
  channel,
  onClose,
  onSubmit,
}: {
  open: boolean;
  channel: Channel | null;
  onClose: () => void;
  onSubmit: (priority: number) => Promise<void>;
}) {
  const [priority, setPriority] = useState(0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setPriority(Number(channel?.priority ?? 0));
    setError("");
  }, [open, channel?.id]);

  async function save() {
    if (!Number.isFinite(priority)) {
      setError("请输入有效的优先级");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await onSubmit(priority);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      title={`编辑优先级 · ${channel?.name || channel?.id || "渠道"}`}
      subtitle="仅调整主站调度优先级，其他渠道配置保持不变"
      onClose={onClose}
    >
      <Field label="优先级 priority" help="数值越大越优先被调度">
        <Input
          type="number"
          value={priority}
          onChange={(event) => setPriority(Number(event.target.value))}
        />
      </Field>
      {error ? (
        <div className="mt-3 rounded-md bg-[var(--color-danger-bg)] px-3 py-2 text-xs text-[var(--color-danger-text)]">
          {error}
        </div>
      ) : null}
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose} disabled={saving}>取消</Button>
        <Button onClick={save} loading={saving}>保存优先级</Button>
      </div>
    </Modal>
  );
}
```

- [ ] **Step 2: Run the focused test**

Run: `node --test tests/web/main-site-channel-boundary.test.mjs`

Expected: The dialog field test passes; page-boundary tests still fail.

- [ ] **Step 3: Commit the dialog**

```bash
git add apps/web/src/components/ChannelPriorityDialog.tsx
git commit -m "feat: add priority-only channel dialog"
```

### Task 3: Remove Main-Site Destructive Controls

**Files:**
- Modify: `apps/web/src/pages/ChannelsPage.tsx`
- Test: `tests/web/main-site-channel-boundary.test.mjs`

- [ ] **Step 1: Remove full-management state and handlers**

Delete imports and state for `ChannelFormDialog`, `ConfirmDialog`, batch modals, selected checkboxes, revealed plaintext keys, row drafts, main-site deletion, channel creation, channel deletion, status toggles, and weight edits. Keep `adminSites`, filters, selected channel ratio detail, action errors, and match refresh state.

Replace the full edit state with:

```tsx
const [priorityChannel, setPriorityChannel] = useState<Channel | null>(null);
```

- [ ] **Step 2: Add the priority-only update handler**

```tsx
async function updatePriority(channel: Channel, priority: number) {
  setActionError("");
  try {
    const response = await api.updateChannel(siteId!, channel.id, { priority });
    if (!response.success) throw new Error(response.message || "优先级保存失败");
    await load(keyword);
    toast.success(`渠道「${channel.name || `#${channel.id}`}」优先级已更新`);
  } catch (err) {
    const message = errorText(err, "优先级保存失败");
    setActionError(`优先级保存失败：${message}`);
    toast.error(`优先级保存失败：${message}`);
    throw err;
  }
}
```

- [ ] **Step 3: Simplify the toolbar and table**

Keep main-site selection, “编辑主站”, “添加主站”, search, group filter, ratio refresh, and static channel fields. Remove “删除主站”, “添加渠道”, all checkboxes, batch toolbar, status click handlers, plaintext key buttons, editable weight/priority inputs, and delete buttons.

Render weight, priority, status, and masked key as read-only values:

```tsx
<td className="py-3 pr-3 tabular-nums">{Number(channel.weight ?? 0)}</td>
<td className="py-3 pr-3 tabular-nums">{Number(channel.priority ?? 0)}</td>
<td className="py-3 pr-3"><Badge tone={meta.tone} dot>{meta.label}</Badge></td>
<td className="py-3 pr-3"><code>{channel.key || "—"}</code></td>
```

The action cell contains only “刷新倍率” and “编辑优先级”.

- [ ] **Step 4: Mount the priority dialog**

```tsx
<ChannelPriorityDialog
  open={priorityChannel !== null}
  channel={priorityChannel}
  onClose={() => setPriorityChannel(null)}
  onSubmit={(priority) => updatePriority(priorityChannel!, priority)}
/>
```

- [ ] **Step 5: Run the boundary and existing web tests**

Run: `node --test tests/web/*.test.mjs`

Expected: all web tests PASS.

- [ ] **Step 6: Commit the page change**

```bash
git add apps/web/src/pages/ChannelsPage.tsx tests/web/main-site-channel-boundary.test.mjs
git commit -m "fix: limit main-site channels to priority edits"
```

### Task 4: Correct Local Monitor Guidance

**Files:**
- Modify: `apps/web/src/components/SiteFormDialog.tsx`
- Test: `tests/web/main-site-channel-boundary.test.mjs`

- [ ] **Step 1: Replace the misleading NewAPI guidance**

Replace the sentences that direct users to manage main-site channels and describe channel-specific binding precedence with:

```tsx
主站监控中的真实渠道会按 Base URL 自动匹配并复用这里的登录态，用于读取该上游账号的分组和倍率。
真实主站渠道的新增、删除和其他配置请在主站后台完成。
```

- [ ] **Step 2: Run the guidance contract test**

Run: `node --test tests/web/main-site-channel-boundary.test.mjs`

Expected: all four tests PASS.

- [ ] **Step 3: Commit the copy correction**

```bash
git add apps/web/src/components/SiteFormDialog.tsx
git commit -m "docs: clarify local monitor credential reuse"
```

### Task 5: Full Verification and Browser QA

**Files:**
- Verify: `apps/web/src/pages/ChannelsPage.tsx`
- Verify: `apps/web/src/components/ChannelPriorityDialog.tsx`
- Verify: `apps/web/src/components/SiteFormDialog.tsx`

- [ ] **Step 1: Run all web contract tests**

Run: `node --test tests/web/*.test.mjs`

Expected: all tests PASS with zero failures.

- [ ] **Step 2: Run all Python tests**

Run: `python3 -m unittest discover -s tests -p 'test_*.py'`

Expected: all tests PASS with zero failures.

- [ ] **Step 3: Build the production frontend**

Run: `npm run build` from `apps/web`.

Expected: TypeScript and Vite build complete successfully.

- [ ] **Step 4: Start the local backend**

Run: `python3 app.py` from the repository root. If port 8000 is occupied, use the project's supported port environment setting with an unused port.

Expected: backend serves the production frontend and `/api/sites` plus `/api/overview` return HTTP 200.

- [ ] **Step 5: Inspect the main-site page at desktop and mobile widths**

Verify in the browser:

- No “删除主站”, “添加渠道”, channel delete, batch, status toggle, weight input, or plaintext key action appears.
- “编辑优先级” opens a one-field dialog.
- Table content does not overlap at desktop or mobile widths and retains horizontal scrolling where needed.
- Search, group filtering, ratio refresh, main-site add/edit, and selected-channel ratio details still work.

- [ ] **Step 6: Verify priority request payload without changing a real channel**

Use browser request interception or a mocked local response to capture the save request. Confirm the request path is `PUT /api/admin/sites/:id/channels/:channel_id` and the JSON body is exactly `{ "priority": <number> }`. Do not submit against the user's real main site during this verification.

- [ ] **Step 7: Review the final diff**

Run: `git diff --check`

Expected: no whitespace errors. Confirm the diff contains no backend route removal, database migration, credential output, or unrelated file changes.
