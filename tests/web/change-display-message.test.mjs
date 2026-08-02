import assert from "node:assert/strict";
import test from "node:test";

import {
  changeDisplayMessage,
  ratioLabel,
} from "../../apps/web/src/lib/format.ts";

test("preserves sub2api multiplier precision", () => {
  assert.equal(ratioLabel({ ratio: 0.045, ratio_type: "number" }), "0.045x");
  assert.equal(ratioLabel({ ratio: 0.1, ratio_type: "number" }), "0.10x");
});

test("adds the new group ratio to historical change messages", () => {
  assert.equal(
    changeDisplayMessage({
      id: 1,
      site_id: 9,
      change_type: "group_added",
      group_name: "plus",
      new_value: JSON.stringify({ ratio: 1.2, ratio_type: "number" }),
      message: "认证增强 新增分组 plus",
      created_at: "2026-07-28T22:32:57+08:00",
    }),
    "认证增强 新增分组 plus · 倍率 1.20x",
  );
});

test("does not duplicate a ratio already present in a new change message", () => {
  assert.equal(
    changeDisplayMessage({
      id: 2,
      site_id: 9,
      change_type: "group_added",
      group_name: "plus",
      new_value: JSON.stringify({ ratio: 1.2 }),
      message: "新增分组 plus · 倍率 1.20x",
      created_at: "2026-07-28T22:32:57+08:00",
    }),
    "新增分组 plus · 倍率 1.20x",
  );
});

test("keeps unrelated or malformed change messages unchanged", () => {
  const base = {
    id: 3,
    site_id: 9,
    group_name: "plus",
    created_at: "2026-07-28T22:32:57+08:00",
  };
  assert.equal(
    changeDisplayMessage({
      ...base,
      change_type: "group_removed",
      message: "删除分组 plus",
    }),
    "删除分组 plus",
  );
  assert.equal(
    changeDisplayMessage({
      ...base,
      change_type: "group_added",
      new_value: "not-json",
      message: "新增分组 plus",
    }),
    "新增分组 plus",
  );
});
