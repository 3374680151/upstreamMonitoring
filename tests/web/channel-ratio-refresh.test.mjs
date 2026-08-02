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
