/** Shared domain label dictionary (migrated from apps/web/src/lib/labels.ts). */
import { test } from "node:test";
import assert from "node:assert/strict";
import { KNOWLEDGE_LEVEL_ORDER, dt } from "../src/index.ts";

test("dt translates both languages for every documented namespace", () => {
  for (const key of [
    "mode.introduction",
    "edge.prerequisite",
    "edge.misconception",
    "event.quiz_graded",
    "verdict.partial",
  ]) {
    assert.equal(typeof dt("zh", key), "string");
    assert.equal(typeof dt("en", key), "string");
    assert.notEqual(dt("zh", key), dt("en", key));
  }
});

test("dt falls back to the provided fallback then the key itself", () => {
  assert.equal(dt("zh", "edge.unknown", "fallback"), "fallback");
  assert.equal(dt("en", "edge.unknown"), "edge.unknown");
});

test("knowledge level order is the fixed taxonomy display order", () => {
  assert.deepEqual(KNOWLEDGE_LEVEL_ORDER, ["小学", "初中", "高中", "本科", "其他"]);
});
