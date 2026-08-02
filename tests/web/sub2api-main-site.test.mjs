import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const apiUrl = new URL("../../apps/web/src/lib/api.ts", import.meta.url);
const typesUrl = new URL("../../apps/web/src/lib/types.ts", import.meta.url);
const helpersUrl = new URL(
  "../../apps/web/src/lib/sub2apiChannel.ts",
  import.meta.url,
);
const adminFormUrl = new URL(
  "../../apps/web/src/components/AdminSiteFormDialog.tsx",
  import.meta.url,
);
const channelsPageUrl = new URL(
  "../../apps/web/src/pages/ChannelsPage.tsx",
  import.meta.url,
);
const sub2apiTableUrl = new URL(
  "../../apps/web/src/components/Sub2ApiChannelTable.tsx",
  import.meta.url,
);
const healthPanelUrl = new URL(
  "../../apps/web/src/components/MainSiteHealthPanel.tsx",
  import.meta.url,
);
const healthHelpersUrl = new URL(
  "../../apps/web/src/lib/mainSiteHealth.ts",
  import.meta.url,
);

test("main-site API exposes the unified connection test", async () => {
  const source = await readFile(apiUrl, "utf8");
  assert.match(source, /testAdminSite/);
  assert.match(source, /\/api\/admin\/sites\/test/);
});

test("main-site contracts include capabilities and sub2api pricing", async () => {
  const source = await readFile(typesUrl, "utf8");
  assert.match(source, /AdminSiteCapabilities/);
  assert.match(source, /Sub2ApiModelPricing/);
  assert.match(source, /Sub2ApiAccountStatsPricingRule/);
  assert.match(source, /source_platform/);
  assert.match(source, /normalized_status/);
  assert.match(source, /rate_multiplier/);
});

test("sub2api helper preserves explicit empty values", async () => {
  const helpers = await import(helpersUrl);
  const original = {
    id: 1,
    name: "A",
    group_ids: [1],
    model_mapping: { anthropic: { a: "b" } },
  };
  const edited = { ...original, group_ids: [], model_mapping: {} };
  assert.deepEqual(helpers.buildSub2ApiChannelPatch(original, edited), {
    group_ids: [],
    model_mapping: {},
  });
});

test("status normalization supports both main-site platforms", async () => {
  const helpers = await import(helpersUrl);
  assert.equal(helpers.normalizedChannelStatus({ status: 1 }), "active");
  assert.equal(helpers.normalizedChannelStatus({ status: 2 }), "disabled");
  assert.equal(helpers.normalizedChannelStatus({ status: 3 }), "error");
  assert.equal(
    helpers.normalizedChannelStatus({ status: "active" }),
    "active",
  );
  assert.equal(
    helpers.normalizedChannelStatus({ status: "disabled" }),
    "disabled",
  );
  assert.equal(
    helpers.normalizedChannelStatus({ normalized_status: "error" }),
    "error",
  );
});

test("sub2api features text supports string and array responses", async () => {
  const helpers = await import(helpersUrl);
  assert.equal(helpers.sub2ApiFeaturesText(""), "");
  assert.equal(helpers.sub2ApiFeaturesText("cache, vision"), "cache, vision");
  assert.equal(helpers.sub2ApiFeaturesText(["cache", "vision"]), "cache, vision");
  assert.equal(
    helpers.editedSub2ApiFeatures("", "cache, vision"),
    "cache, vision",
  );
  assert.deepEqual(
    helpers.editedSub2ApiFeatures([], "cache, vision"),
    ["cache", "vision"],
  );
});

test("sub2api token prices convert between per-token storage and MTok display", async () => {
  const helpers = await import(helpersUrl);
  assert.equal(helpers.sub2ApiPerTokenToMTok(0.000003), 3);
  assert.equal(helpers.sub2ApiPerTokenToMTok(0.00000005), 0.05);
  assert.equal(helpers.sub2ApiPerTokenToMTok(null), null);
  assert.equal(helpers.sub2ApiMTokToPerToken(3), 0.000003);
  assert.equal(helpers.sub2ApiMTokToPerToken(0.05), 0.00000005);
  assert.equal(helpers.sub2ApiMTokToPerToken(null), null);
});

test("admin-site form selects a platform only during creation", async () => {
  const source = await readFile(adminFormUrl, "utf8");
  assert.match(source, /option value="newapi"/);
  assert.match(source, /option value="sub2api"/);
  assert.match(source, /disabled=\{editing\}/);
});

test("sub2api form uses administrator credentials and unified test API", async () => {
  const source = await readFile(adminFormUrl, "utf8");
  assert.match(source, /sub2api 管理员邮箱/);
  assert.match(source, /sub2api 管理员密码/);
  assert.match(source, /api\.testAdminSite\(/);
  assert.match(source, /admin_site_id: site\?\.id/);
  assert.match(source, /form\.platform === "sub2api"/);
  assert.match(source, /form\.platform === "newapi"/);
});

test("sub2api editor exposes every supported configuration section", async () => {
  const source = await readFile(
    new URL(
      "../../apps/web/src/components/Sub2ApiChannelDialog.tsx",
      import.meta.url,
    ),
    "utf8",
  );
  for (const label of ["基本信息", "绑定分组", "模型定价", "模型映射", "高级计费"]) {
    assert.match(source, new RegExp(label));
  }
  assert.match(source, /buildSub2ApiChannelPatch/);
  assert.match(source, /features_config/);
  assert.match(source, /account_stats_pricing_rules/);
  assert.doesNotMatch(source, /删除渠道|新建渠道/);
  assert.doesNotMatch(source, /api\/v1\/admin\/accounts|api\.accounts/);
});

test("sub2api billing model source matches the upstream channel contract", async () => {
  const source = await readFile(
    new URL(
      "../../apps/web/src/components/Sub2ApiChannelDialog.tsx",
      import.meta.url,
    ),
    "utf8",
  );
  const values = [...source.matchAll(/<option value="([^"]+)">/g)].map(
    (match) => match[1],
  );
  assert.deepEqual(
    values.filter((value) =>
      ["requested", "upstream", "channel_mapped", "channel", "group"].includes(
        value,
      ),
    ),
    ["channel_mapped", "requested", "upstream"],
  );
});

test("pricing editor supports complete interval prices", async () => {
  const source = await readFile(
    new URL(
      "../../apps/web/src/components/Sub2ApiPricingEditor.tsx",
      import.meta.url,
    ),
    "utf8",
  );
  for (const field of [
    "min_tokens",
    "max_tokens",
    "cache_write_price",
    "cache_read_price",
    "image_input_price",
    "image_output_price",
    "per_request_price",
  ]) {
    assert.match(source, new RegExp(field));
  }
  assert.match(source, /Plus/);
  assert.match(source, /Trash2/);
  assert.match(source, /\$\/MTok/);
  assert.match(source, /sub2ApiPerTokenToMTok/);
  assert.match(source, /sub2ApiMTokToPerToken/);
});

test("shared tabs expose accessible tab semantics", async () => {
  const source = await readFile(
    new URL("../../apps/web/src/components/ui.tsx", import.meta.url),
    "utf8",
  );
  assert.match(source, /role="tablist"/);
  assert.match(source, /role="tab"/);
  assert.match(source, /aria-selected/);
});

test("main-site page renders sub2api channels by capabilities", async () => {
  const [page, table] = await Promise.all([
    readFile(channelsPageUrl, "utf8"),
    readFile(sub2apiTableUrl, "utf8"),
  ]);
  assert.match(page, /currentAdminSite\?\.platform === "sub2api"/);
  assert.match(page, /Sub2ApiChannelDialog/);
  assert.match(page, /toggle_channel/);
  assert.match(table, /模型定价/);
  assert.match(table, /分组倍率/);
  assert.match(table, /normalizedChannelStatus/);
  assert.match(table, /Pencil/);
  assert.match(table, /PowerOff/);
  assert.match(table, /RefreshCw/);
});

test("sub2api table keeps desktop columns within its width budget", async () => {
  const source = await readFile(sub2apiTableUrl, "utf8");
  const colgroup = source.match(/<colgroup>([\s\S]*?)<\/colgroup>/)?.[1] || "";
  const widths = [...colgroup.matchAll(/w-\[([0-9]+)px\]/g)].map((match) =>
    Number(match[1]),
  );
  assert.equal(widths.length, 7);
  assert.ok(
    widths.reduce((total, width) => total + width, 0) <= 1000,
    "sub2api table columns exceed the 1000px desktop budget",
  );
});

test("sub2api actions never expose create, delete, account-pool, or key reads", async () => {
  const page = await readFile(channelsPageUrl, "utf8");
  assert.doesNotMatch(page, /api\.createChannel\(/);
  assert.doesNotMatch(page, /api\.deleteChannel\(/);
  assert.doesNotMatch(page, /api\.channelDetail\(/);
  assert.doesNotMatch(page, /api\/v1\/admin\/accounts|api\.accounts/);
  assert.doesNotMatch(page, /添加 sub2api 渠道|删除 sub2api 渠道/);
});

test("main-site health uses normalized mixed-platform statuses", async () => {
  const source = await readFile(healthPanelUrl, "utf8");
  assert.match(source, /normalizedChannelStatus/);
  assert.match(source, /运行中/);
  assert.match(source, /已停用/);
  assert.match(source, /异常/);
  assert.doesNotMatch(source, /你自己的中转站（NewAPI 后台）/);
});

test("main-site health busy state is scoped by site and channel", async () => {
  const source = await readFile(healthPanelUrl, "utf8");
  assert.match(source, /`\$\{siteId\}:\$\{ch\.id\}`/);
  assert.doesNotMatch(source, /setBusyId\(ch\.id\)/);
});

test("main-site selector displays each platform label", async () => {
  const source = await readFile(channelsPageUrl, "utf8");
  assert.match(source, /site\.platform_label/);
});

test("failed main-site health refresh keeps the last successful channels", async () => {
  const helpers = await import(healthHelpersUrl);
  const site = { id: 1, platform: "sub2api", name: "sub", base_url: "https://sub" };
  const previous = [{ site, channels: [{ id: 8, status: "active" }] }];
  const refreshed = [{ site, channels: [], error: "HTTP 503" }];
  assert.deepEqual(
    helpers.retainLastSuccessfulMainSiteChannels(previous, refreshed),
    [{ site, channels: [{ id: 8, status: "active" }], error: "HTTP 503" }],
  );
});

test("failed main-site list refresh does not clear previous health rows", async () => {
  const source = await readFile(healthPanelUrl, "utf8");
  const failureBranch = source.match(
    /\} catch \(err\) \{([\s\S]*?)\} finally \{/,
  )?.[1];
  assert.ok(failureBranch, "main-site health failure branch is missing");
  assert.doesNotMatch(failureBranch, /setRows\(\[\]\)/);
  assert.match(failureBranch, /setError\(message\)/);
});
