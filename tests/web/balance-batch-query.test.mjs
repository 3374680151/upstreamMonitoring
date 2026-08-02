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
