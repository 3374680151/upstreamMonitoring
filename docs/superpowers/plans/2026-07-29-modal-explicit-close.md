# Modal Explicit-Close Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all shared modals ignore backdrop pointer interactions while preserving explicit close controls.

**Architecture:** Keep dismissal ownership in the shared `Modal` component. A Playwright regression test exercises the real site form modal so the browser's cross-boundary drag event sequence is covered, then the shared backdrop listener is removed.

**Tech Stack:** React 19, TypeScript, Vite, Node test runner, Playwright

---

### Task 1: Reproduce the dismissal regression

**Files:**
- Create: `tests/web/modal-explicit-close.test.mjs`

- [x] **Step 1: Write the failing browser test**

```javascript
import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { chromium } from "../../apps/web/node_modules/playwright/index.mjs";
import { createServer } from "../../apps/web/node_modules/vite/dist/node/index.js";

const webRoot = fileURLToPath(new URL("../../apps/web/", import.meta.url));

test("modal ignores backdrop interactions and closes from its close button", async (t) => {
  const server = await createServer({
    root: webRoot,
    logLevel: "silent",
    server: { host: "127.0.0.1", port: 0 },
  });
  await server.listen();
  const address = server.httpServer?.address();
  assert.ok(address && typeof address === "object");
  const webUrl = `http://127.0.0.1:${address.port}`;

  const browser = await chromium.launch({ headless: true });
  t.after(async () => {
    await browser.close();
    await server.close();
  });
  const page = await browser.newPage({ viewport: { width: 1250, height: 900 } });
  await page.route("**/api/**", (route) => {
    const authStatus = route.request().url().endsWith("/api/auth/status");
    return route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(
        authStatus
          ? { success: true, auth_required: false, authenticated: true }
          : { success: true, data: [] },
      ),
    });
  });
  await page.goto(webUrl, { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "添加渠道" }).click();

  const dialog = page.getByRole("dialog", { name: "添加渠道" });
  const input = page.getByLabel("渠道名称", { exact: true });
  await input.fill("用于复现拖选关闭问题的较长渠道名称");
  const inputBox = await input.boundingBox();
  const dialogBox = await dialog.boundingBox();
  assert.ok(inputBox && dialogBox);

  const outsideX = dialogBox.x - 20;
  const inputY = inputBox.y + inputBox.height / 2;
  await page.mouse.move(inputBox.x + inputBox.width - 10, inputY);
  await page.mouse.down({ button: "left" });
  await page.mouse.move(outsideX, inputY, { steps: 8 });
  await page.mouse.up({ button: "left" });
  assert.equal(await dialog.isVisible(), true);

  for (const button of ["left", "middle", "right"]) {
    await page.mouse.click(outsideX, inputY, { button });
    assert.equal(await dialog.isVisible(), true);
  }

  await dialog.getByRole("button", { name: "关闭" }).click();
  assert.equal(await dialog.isVisible(), false);
});
```

- [x] **Step 2: Run the test and verify RED**

Run: `node --test tests/web/modal-explicit-close.test.mjs`

Expected: FAIL after the primary-button drag because the current backdrop `onClick` closes the dialog.

### Task 2: Remove implicit backdrop dismissal

**Files:**
- Modify: `apps/web/src/components/ui.tsx`

- [x] **Step 1: Remove backdrop and propagation click handlers**

```tsx
<div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-[var(--color-overlay)] p-4 md:p-8">
  <div
    role="dialog"
    aria-modal="true"
    aria-label={title}
    className={`my-4 w-full rounded-2xl border border-[var(--color-border)] bg-[var(--color-panel)] shadow-[var(--shadow-floating)] ${
      wide ? "max-w-5xl" : "max-w-xl"
    }`}
  >
```

- [x] **Step 2: Run the targeted test and verify GREEN**

Run: `node --test tests/web/modal-explicit-close.test.mjs`

Expected: PASS; drag and backdrop buttons do nothing, while the close button dismisses the modal.

- [x] **Step 3: Run the complete web regression suite**

Run: `node --test tests/web/*.test.mjs`

Expected: all tests pass.

- [x] **Step 4: Build the production frontend**

Run: `npm run build`

Expected: TypeScript and Vite build exit successfully.

- [x] **Step 5: Verify the real interaction in desktop and mobile viewports**

Open the local app, repeat drag/backdrop/close-button interactions at desktop and mobile widths, and verify there is no visual regression in either theme.
