import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const channelsPageUrl = new URL(
  "../../apps/web/src/pages/ChannelsPage.tsx",
  import.meta.url,
);
const appPageUrl = new URL("../../apps/web/src/App.tsx", import.meta.url);
const priorityDialogUrl = new URL(
  "../../apps/web/src/components/ChannelPriorityDialog.tsx",
  import.meta.url,
);
const priorityParserUrl = new URL(
  "../../apps/web/src/lib/channelPriority.ts",
  import.meta.url,
);
const siteDialogUrl = new URL(
  "../../apps/web/src/components/SiteFormDialog.tsx",
  import.meta.url,
);
const detailPageUrl = new URL(
  "../../apps/web/src/pages/DetailPage.tsx",
  import.meta.url,
);

async function readOptional(url) {
  try {
    return await readFile(url, "utf8");
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    throw error;
  }
}

test("main-site page exposes no destructive channel management", async () => {
  const source = await readFile(channelsPageUrl, "utf8");

  assert.doesNotMatch(source, /api\.deleteAdminSite\(/);
  assert.doesNotMatch(source, /api\.createChannel\(/);
  assert.doesNotMatch(source, /api\.deleteChannel\(/);
  assert.doesNotMatch(source, /api\.batchChannels\(/);
  assert.doesNotMatch(source, /api\.channelDetail\(/);
  assert.doesNotMatch(source, /删除主站|添加渠道|批量删除渠道/);
});

test("NewAPI updates stay priority-only while sub2api uses its dedicated editor", async () => {
  const source = await readFile(channelsPageUrl, "utf8");

  assert.match(
    source,
    /api\.updateChannel\(siteId!, channel\.id, \{ priority \}\)/,
  );
  assert.doesNotMatch(source, /ChannelFormDialog/);
  assert.match(source, /ChannelPriorityDialog/);
  assert.match(source, /Sub2ApiChannelDialog/);
  assert.match(source, /currentAdminSite\?\.platform === "sub2api"/);
});

test("main-site sync reloads channel data and waits for ratio rematching", async () => {
  const source = await readFile(channelsPageUrl, "utf8");

  assert.match(
    source,
    /const synced = await onSyncMainSites\(siteId\);[\s\S]*?await load\("", \{ refreshMatches: true, waitForMatches: true \}\)/,
  );
});

test("failed main-site sync is not reported as current data", async () => {
  const source = await readFile(appPageUrl, "utf8");

  assert.match(source, /return result\.success !== false && !result\.failed/);
});

test("priority dialog contains no other channel settings", async () => {
  const source = await readOptional(priorityDialogUrl);

  assert.notEqual(source, null, "ChannelPriorityDialog.tsx is missing");
  assert.match(source, /label="优先级 priority"/);
  assert.doesNotMatch(
    source,
    /密钥|Base URL|模型重定向|自动禁用|上游认证|访问令牌|用户密码/,
  );
});

test("priority parser accepts integers and rejects empty or fractional input", async () => {
  const parserModule = await import(priorityParserUrl).catch((error) => {
    if (error?.code === "ERR_MODULE_NOT_FOUND") return null;
    throw error;
  });

  assert.notEqual(parserModule, null, "channelPriority.ts is missing");
  assert.equal(parserModule.parseChannelPriority("100"), 100);
  assert.equal(parserModule.parseChannelPriority("-2"), -2);
  assert.equal(parserModule.parseChannelPriority(""), null);
  assert.equal(parserModule.parseChannelPriority("  "), null);
  assert.equal(parserModule.parseChannelPriority("1.5"), null);
  assert.equal(parserModule.parseChannelPriority("abc"), null);
});

test("local monitor form explains base-url credential reuse", async () => {
  const source = await readFile(siteDialogUrl, "utf8");

  assert.match(source, /按 Base URL 自动匹配并复用这里的登录态/);
  assert.doesNotMatch(source, /主站下渠道的增删改/);
  assert.doesNotMatch(source, /渠道单独配置了上游登录态/);
});

test("failed refresh keeps ratios and renders an explicit cause", async () => {
  const source = await readFile(channelsPageUrl, "utf8");

  assert.match(source, /刷新失败，显示上次成功倍率/);
  assert.match(source, /错误原因：\{bindingError\.summary\}/);
  assert.match(source, /status === "needs_key_verification"/);
  assert.match(source, /status === "missing_key"/);
  assert.match(source, /上次成功数据/);
  assert.doesNotMatch(
    source,
    /:\s*binding\?\.match_message \|\| "未匹配"/,
  );
});

test("local detail displays a cause and the original error", async () => {
  const source = await readFile(detailPageUrl, "utf8");

  assert.match(source, /explainUpstreamError/);
  assert.match(source, /错误原因：\{siteError\.summary\}/);
  assert.match(source, /原始错误：\{siteError\.raw\}/);
});
