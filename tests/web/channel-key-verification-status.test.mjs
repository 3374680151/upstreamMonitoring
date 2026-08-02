import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const channelsPageUrl = new URL(
  "../../apps/web/src/pages/ChannelsPage.tsx",
  import.meta.url,
);

test("missing channel key verification is presented as a warning", async () => {
  const source = await readFile(channelsPageUrl, "utf8");
  const start = source.indexOf("function bindingTone(");
  const end = source.indexOf("\n}\n\nexport function ChannelsPage", start);

  assert.notEqual(start, -1, "bindingTone function is missing");
  assert.notEqual(end, -1, "bindingTone function boundary is missing");

  const bindingTone = source.slice(start, end);
  assert.match(
    bindingTone,
    /status === ["']needs_key_verification["']\) return ["']warning["']/,
  );
});
