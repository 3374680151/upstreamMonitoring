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
