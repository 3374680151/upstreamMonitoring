import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const read = (path) => readFile(new URL(`../../${path}`, import.meta.url), "utf8");

test("discovery client exposes candidates and idempotent import", async () => {
  const [apiSource, typeSource, panelSource] = await Promise.all([
    read("apps/web/src/lib/api.ts"),
    read("apps/web/src/lib/types.ts"),
    read("apps/web/src/components/ChannelDiscoveryPanel.tsx"),
  ]);
  assert.match(apiSource, /channelCandidates/);
  assert.match(
    apiSource,
    /\/api\/admin\/sites\/\$\{adminSiteId\}\/channel-candidates/,
  );
  assert.match(apiSource, /discovery-import/);
  assert.match(typeSource, /export type ChannelDiscoveryCandidate/);
  assert.match(panelSource, /从主站发现/);
  assert.match(panelSource, /添加并同步/);
  assert.match(panelSource, /interval_minutes: intervalMinutes/);
  assert.match(panelSource, /新建渠道监控间隔/);
  assert.match(panelSource, /session_sync_status|existing_site_status/);
});

test("add flow keeps manual mode and orchestrates imported browser sessions", async () => {
  const form = await read(
    "apps/web/src/components/SiteFormDialog.tsx",
  );
  const appSource = await read("apps/web/src/App.tsx");
  const panelSource = await read("apps/web/src/components/ChannelDiscoveryPanel.tsx");
  assert.match(form, /手动添加/);
  assert.match(form, /从主站发现/);
  assert.match(form, /ChannelDiscoveryPanel/);
  assert.match(panelSource, /api\.importDiscoveredSites/);
  assert.match(appSource, /SiteFormDialog/);
  assert.match(form, /syncSiteBrowserSession/);
});

test("discovery UI keeps explicit retry and no-session states", async () => {
  const panelSource = await read(
    "apps/web/src/components/ChannelDiscoveryPanel.tsx",
  );
  assert.match(panelSource, /没有登录态/);
  assert.match(panelSource, /重新同步/);
  assert.match(panelSource, /打开登录页/);
  assert.match(panelSource, /aria-label/);
  assert.match(panelSource, /existing_site_auth_mode/);
  assert.match(panelSource, /sm:hidden/);
});

test("discovery client exposes provenance query and import channels", async () => {
  const [apiSource, typeSource, panelSource] = await Promise.all([
    read("apps/web/src/lib/api.ts"),
    read("apps/web/src/lib/types.ts"),
    read("apps/web/src/components/ChannelDiscoveryPanel.tsx"),
  ]);
  assert.match(apiSource, /siteDiscoveryLinks/);
  assert.match(
    apiSource,
    /\/api\/sites\/\$\{siteId\}\/discovery-links/,
  );
  assert.match(typeSource, /export type SiteDiscoveryLink/);
  // import payload carries channel_names alongside channel_ids
  assert.match(
    panelSource,
    /channel_names: candidate\.channel_names/,
  );
});

test("discovery rows can open the imported site authentication editor", async () => {
  const [panel, form, app] = await Promise.all([
    read("apps/web/src/components/ChannelDiscoveryPanel.tsx"),
    read("apps/web/src/components/SiteFormDialog.tsx"),
    read("apps/web/src/App.tsx"),
  ]);
  assert.match(panel, /编辑认证/);
  assert.match(panel, /onEditSite/);
  assert.match(form, /onEditSite/);
  assert.match(app, /sites\.find/);
});

test("site detail renders discovery provenance without credentials", async () => {
  const [detail, apiSource, typeSource] = await Promise.all([
    read("apps/web/src/pages/DetailPage.tsx"),
    read("apps/web/src/lib/api.ts"),
    read("apps/web/src/lib/types.ts"),
  ]);
  assert.match(detail, /发现来源/);
  assert.match(detail, /siteDiscoveryLinks/);
  assert.doesNotMatch(detail, /access_token|login_password|refresh_token/);
  assert.match(apiSource, /siteDiscoveryLinks/);
  assert.match(typeSource, /export type SiteDiscoveryLink/);
});
