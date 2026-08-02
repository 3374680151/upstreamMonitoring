import assert from "node:assert/strict";
import test from "node:test";

import { successTone } from "../../apps/web/src/lib/perf.ts";

test("maps success-rate thresholds to global semantic tones", () => {
  assert.equal(successTone(80.01), "success");
  assert.equal(successTone(80), "warning");
  assert.equal(successTone(60), "warning");
  assert.equal(successTone(59.99), "danger");
});

test("keeps missing or invalid success rates neutral", () => {
  assert.equal(successTone(null), "neutral");
  assert.equal(successTone(Number.NaN), "neutral");
  assert.equal(successTone(Number.POSITIVE_INFINITY), "neutral");
});
