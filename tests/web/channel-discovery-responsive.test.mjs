import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "vite";
import { chromium } from "playwright";

async function bootVite() {
  const server = await createServer({
    server: { port: 0, host: "127.0.0.1" },
    root: new URL("../../apps/web", import.meta.url).pathname,
  });
  await server.listen();
  const url = `http://127.0.0.1:${server.config.server.port}`;
  return { server, url };
}

test(
  "channel discovery UI stays in viewport at 1440/375/320 and dark theme",
  async () => {
    const { server, url } = await bootVite();
    const browser = await chromium.launch();
    try {
      const page = await browser.newPage();

      // Mock all API endpoints so the UI works without a live backend.
      await page.route("**/api/auth/status", (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ auth_required: false, authenticated: true }),
        }),
      );
      await page.route("**/api/sites", (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: [] }),
        }),
      );
      await page.route("**/api/admin/sites", (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: [
              {
                id: 3,
                name: "主站 A",
                platform: "newapi",
                base_url: "https://admin.example",
              },
            ],
          }),
        }),
      );
      await page.route("**/api/admin/sites/3/channel-candidates**", (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            success: true,
            data: [
              {
                base_url: "https://provider.example/very-long-provider-path",
                name: "Provider A",
                channel_ids: [12, 18],
                channel_names: ["主渠道", "备用渠道"],
                channel_count: 2,
                existing_site_id: null,
                existing_site_status: null,
                importable: true,
              },
            ],
          }),
        }),
      );
      // silence other calls
      page.on("requestfailed", () => {});
      await page.route("**/api/**", (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ success: true, data: [] }),
        }),
      );

      for (const viewport of [
        { width: 1440, height: 1000 },
        { width: 375, height: 812 },
        { width: 320, height: 720 },
      ]) {
        await page.setViewportSize(viewport);
        await page.goto(url);
        await page.getByRole("button", { name: "添加渠道" }).click();
        await page.getByRole("tab", { name: "从主站发现" }).click();
        await page.getByText("Provider A").first().waitFor({ timeout: 5000 });
        const bodyWidth = await page.evaluate(
          () => document.body.scrollWidth,
        );
        assert.ok(
          bodyWidth <= viewport.width + 1,
          `body scrollWidth ${bodyWidth} exceeds viewport ${viewport.width}`,
        );
      }

      // Dark theme check
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.evaluate(() =>
        document.documentElement.setAttribute("data-theme", "dark"),
      );
      await page.reload();
      await page.getByRole("button", { name: "添加渠道" }).click();
      await page.getByRole("tab", { name: "从主站发现" }).click();
      await page.getByText("从主站发现 · NewAPI").first().waitFor({ timeout: 5000 });
      const panelBackground = await page
        .locator('[role="dialog"]')
        .first()
        .evaluate((element) => getComputedStyle(element).backgroundColor);
      assert.notEqual(panelBackground, "rgb(255, 255, 255)");
    } finally {
      await browser.close();
      await server.close();
    }
  },
);
