import assert from "node:assert/strict";
import test from "node:test";

import { claimAutomaticRefresh } from "../../apps/web/src/lib/automaticRefresh.ts";

test("claims one automatic refresh per site", () => {
  const claimedSiteIds = new Set();

  assert.equal(claimAutomaticRefresh(claimedSiteIds, 2), true);
  assert.equal(claimAutomaticRefresh(claimedSiteIds, 2), false);
  assert.equal(claimAutomaticRefresh(claimedSiteIds, 3), true);
});
