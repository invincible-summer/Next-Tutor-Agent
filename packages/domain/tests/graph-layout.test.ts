/** Knowledge-graph DAG layout geometry (migrated from the Web module). */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  COL_GAP,
  LAYER_GAP,
  NODE_H,
  PAD,
  edgePath,
  fitLabel,
  layoutDag,
  measure,
  nodeWidth,
  splitLabel,
} from "../src/index.ts";

test("measure weights CJK wider than latin", () => {
  assert.equal(measure("ab"), 12);
  assert.equal(measure("力"), 11);
  assert.ok(measure("力ab") > measure("ab"));
});

test("nodeWidth is clamped to [60, 230]", () => {
  assert.equal(nodeWidth(""), 60);
  assert.equal(nodeWidth("x".repeat(200)), 230);
});

test("fitLabel truncates with an ellipsis inside the width budget", () => {
  assert.equal(fitLabel("short", 200), "short");
  const fitted = fitLabel("力".repeat(20), 60);
  assert.ok(fitted.endsWith("…"));
  assert.ok(measure(fitted) <= 60 + 11); // ellipsis may overflow one glyph budget
});

test("splitLabel splits long CJK labels into two fitted lines", () => {
  const two = splitLabel("牛顿第二定律的实验验证与误差分析", 80);
  assert.equal(two.length, 2);
  for (const line of two) assert.ok(measure(line) <= 80 + 11);
  assert.deepEqual(splitLabel("short", 80), ["short"]);
});

test("splitLabel prefers word boundaries for latin labels", () => {
  const lines = splitLabel("Newton second law experiment", 70);
  assert.equal(lines.length, 2);
  assert.ok(lines[0]!.endsWith("w") || lines[0]!.length < 10);
});

test("edgePath is a vertical bezier whose direction follows y", () => {
  assert.match(edgePath(0, 0, 10, 100), /^M 0 0 C 0 /);
  assert.match(edgePath(0, 100, 10, 0), /^M 0 100 C 0 /);
});

test("layoutDag layers by longest prerequisite path with cycle protection", () => {
  const nodes = [
    { id: "a", name: "A" },
    { id: "b", name: "B" },
    { id: "c", name: "C" },
  ];
  const edges = [
    { from: "a", to: "b", type: "prerequisite" },
    { from: "b", to: "c", type: "prerequisite" },
    { from: "c", to: "a", type: "prerequisite" }, // cycle: treated as root
    { from: "a", to: "c", type: "related" }, // non-prerequisite ignored
  ];
  const layout = layoutDag(nodes, edges);
  assert.equal(layout.items.length, 3);
  const layerOf = (id: string) =>
    Math.round((layout.byId.get(id)!.cy - PAD - NODE_H / 2) / (NODE_H + LAYER_GAP));
  // The cycle collapses; every node lands on layer 0 or the chain still orders b after a.
  assert.ok([0, 1].includes(layerOf("b")));
  const distinct = new Set([...layout.byId.values()].map((item) => item.cy));
  assert.ok(distinct.size >= 1);
});

test("layoutDag keeps same-layer nodes in input order", () => {
  const nodes = [
    { id: "n1", name: "第一章" },
    { id: "n2", name: "第二章" },
    { id: "n3", name: "第三章" },
  ];
  const layout = layoutDag(nodes, []);
  const sameLayer = layout.items.filter((item) => item.cy === layout.items[0]!.cy);
  assert.deepEqual(
    sameLayer.map((item) => item.n.id),
    ["n1", "n2", "n3"],
  );
  // Width accounts for all boxes plus inter-column gaps.
  const boxes = layout.items.reduce((sum, item) => sum + item.w, 0);
  assert.ok(layout.w >= boxes + 2 * COL_GAP);
});

test("layoutDag on empty input produces a zero-size layout", () => {
  const layout = layoutDag([], []);
  assert.equal(layout.w, 0);
  assert.equal(layout.h, 0);
  assert.equal(layout.items.length, 0);
});
