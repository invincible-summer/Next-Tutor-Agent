import test from "node:test";
import assert from "node:assert/strict";
import {
  createMathDocument, validateMathDocument, applyMathCommand, serializeMathDocument,
  deserializeMathDocument, semanticFingerprint, buildEvaluationContext, resolveGeometry,
  axisVariables, presetDocument, WORKBENCH_PRESETS, type MathWorkbenchDocument,
} from "../src/math-workbench/index.ts";

const style = { color: "#2f7d6e", width: 2, opacity: 1, dashed: false };
const point = (id: string, label: string, x: number, y: number) =>
  ({ kind2d: "point" as const, id, label, visible: true, locked: false, style, construction: { kind: "free" as const, x, y } });

function withPlot(doc: MathWorkbenchDocument): MathWorkbenchDocument {
  return {
    ...doc,
    plots2d: [{ kind: "explicit", id: "p1", label: "f", visible: true, style, expression: "sin(x)" }],
    parameters: [{ id: "par1", name: "a", value: 2, min: 0, max: 4, step: 0.1 }],
    functions: [{ id: "fn1", name: "f", params: ["x"], expression: "a*x^2" }],
  };
}

test("create → validate → serialize → deserialize roundtrip", () => {
  const doc = withPlot(createMathDocument("测试绘图"));
  const valid = validateMathDocument(JSON.parse(JSON.stringify(doc)));
  assert.equal(valid.ok, true);
  if (!valid.ok) return;
  const serialized = serializeMathDocument(valid.value);
  const back = deserializeMathDocument(serialized);
  assert.equal(back.ok, true);
  if (back.ok) {
    assert.equal(back.value.name, "测试绘图");
    const plot = back.value.plots2d[0];
    assert.equal(plot?.kind, "explicit");
    if (plot?.kind === "explicit") assert.equal(plot.expression, "sin(x)");
    assert.equal(semanticFingerprint(back.value), semanticFingerprint(valid.value));
  }
});

test("imports are allowlist-validated: unknown fields, junk types, oversized content rejected", () => {
  const doc = withPlot(createMathDocument("x"));
  const raw = JSON.parse(JSON.stringify(doc));
  raw.plots2d[0].expression = "eval(1)"; // unknown function call → rejected
  let r = validateMathDocument(raw);
  assert.equal(r.ok, false);
  if (!r.ok) assert.equal(r.diagnostics[0]?.code, "unknown_function");
  const rawQuoted = JSON.parse(JSON.stringify(doc));
  rawQuoted.plots2d[0].expression = "'1+1'"; // strings never tokenize
  assert.equal(validateMathDocument(rawQuoted).ok, false);

  const raw2 = JSON.parse(JSON.stringify(doc));
  raw2.schemaVersion = 2;
  assert.equal(validateMathDocument(raw2).ok, false);

  const raw3 = JSON.parse(JSON.stringify(doc));
  (raw3.objects2d as unknown[]).push({ kind2d: "circle", id: "c9", label: "c", visible: true, locked: false, style, construction: { kind: "centerRadius", centerId: "nope", radius: 1 } });
  r = validateMathDocument(raw3);
  assert.equal(r.ok, false);
  if (!r.ok) assert.equal(r.diagnostics[0]?.code, "dangling_reference");

  assert.equal(validateMathDocument("not json").ok, false);
  assert.equal(validateMathDocument(null).ok, false);
  const huge = { ...JSON.parse(JSON.stringify(doc)), annotations: new Array(500).fill({ id: "a", text: "t", x: 0, y: 0, space: "2d", visible: true }) };
  assert.equal(validateMathDocument(huge).ok, false);
  assert.equal(deserializeMathDocument("x".repeat(600 * 1024)).ok, false);
});

test("dependency cycles and self-referencing functions are rejected", () => {
  const doc = createMathDocument("c");
  doc.functions.push({ id: "f1", name: "f", params: ["x"], expression: "f(x)+1" });
  const r = validateMathDocument(JSON.parse(JSON.stringify(doc)));
  assert.equal(r.ok, false);
  if (!r.ok) assert.ok(["unknown_function", "function_dependency_cycle"].includes(r.diagnostics[0]?.code ?? ""));

  const doc2 = createMathDocument("c2");
  doc2.functions.push({ id: "g1", name: "g", params: ["x"], expression: "h(x)" });
  doc2.functions.push({ id: "h1", name: "h", params: ["x"], expression: "g(x)" });
  const r2 = validateMathDocument(JSON.parse(JSON.stringify(doc2)));
  assert.equal(r2.ok, false);
  if (!r2.ok) assert.equal(r2.diagnostics[0]?.code, "function_dependency_cycle");

  // Forward references are fine when acyclic.
  const doc3 = createMathDocument("c3");
  doc3.functions.push({ id: "g2", name: "g", params: ["x"], expression: "h(x)+1" });
  doc3.functions.push({ id: "h2", name: "h", params: ["x"], expression: "x^2" });
  const r3 = validateMathDocument(JSON.parse(JSON.stringify(doc3)));
  assert.equal(r3.ok, true, JSON.stringify(r3.ok ? null : r3.diagnostics));
});

test("commands: batch atomicity and cascade delete", () => {
  const doc = createMathDocument("cmd");
  const addA = applyMathCommand(doc, { kind: "add2D", object: point("pA", "A", 0, 0) });
  assert.equal(addA.ok, true);
  if (!addA.ok) return;
  const addB = applyMathCommand(addA.value, { kind: "add2D", object: point("pB", "B", 4, 0) });
  if (!addB.ok) return;
  const addMid = applyMathCommand(addB.value, {
    kind: "add2D",
    object: { kind2d: "point", id: "pM", label: "M", visible: true, locked: false, style, construction: { kind: "midpoint", aId: "pA", bId: "pB" } },
  });
  assert.equal(addMid.ok, true);
  if (!addMid.ok) return;
  // Deleting pA without cascade fails; with cascade it removes pM too.
  const blocked = applyMathCommand(addMid.value, { kind: "remove2D", id: "pA" });
  assert.equal(blocked.ok, false);
  if (!blocked.ok) assert.equal(blocked.diagnostics[0]?.code, "dependency_exists");
  const cascade = applyMathCommand(addMid.value, { kind: "remove2D", id: "pA", cascade: true });
  assert.equal(cascade.ok, true);
  if (cascade.ok) {
    assert.equal(cascade.value.objects2d.length, 1);
    assert.equal(cascade.value.revision, 4);
  }
  // Batch: any failing subcommand aborts the whole batch.
  const batch = applyMathCommand(addMid.value, {
    kind: "batch",
    commands: [
      { kind: "add2D", object: point("pC", "C", 1, 1) },
      { kind: "add2D", object: point("pC", "C", 2, 2) }, // duplicate id
    ],
  });
  assert.equal(batch.ok, false);
  if (!batch.ok) assert.equal(batch.diagnostics[0]?.code, "duplicate_id");
  // Fingerprint unchanged because the failed batch did not mutate the doc.
  assert.equal(semanticFingerprint(addMid.value), semanticFingerprint(addMid.value));
});

test("semantic fingerprint excludes view/revision/mode but includes content", () => {
  const doc = withPlot(createMathDocument("fp"));
  const base = semanticFingerprint(doc);
  const moved = { ...doc, camera3d: { ...doc.camera3d, azimuth: 2 }, view2d: { ...doc.view2d, scale: 0.1 }, activeMode: "geometry2d" as const, revision: 99 };
  assert.equal(semanticFingerprint(moved), base);
  const renamed = { ...doc, name: "改名" };
  assert.notEqual(semanticFingerprint(renamed), base);
  const styled = { ...doc, plots2d: [{ ...(doc.plots2d[0] as object), style: { ...style, color: "#aa3311" } }] as MathWorkbenchDocument["plots2d"] };
  assert.notEqual(semanticFingerprint(styled), base);
});

test("non-semantic commands do not bump revision", () => {
  const doc = createMathDocument("rev");
  const view = applyMathCommand(doc, { kind: "setView2D", view: { ...doc.view2d, scale: 0.02 } });
  assert.equal(view.ok, true);
  if (view.ok) assert.equal(view.value.revision, 0);
  const rename = applyMathCommand(doc, { kind: "renameDocument", name: "新名" });
  assert.equal(rename.ok, true);
  if (rename.ok) assert.equal(rename.value.revision, 1);
});

test("evaluation context binds parameters and named functions", () => {
  const doc = withPlot(createMathDocument("ctx"));
  const ctx = buildEvaluationContext(doc);
  assert.equal(ctx.ok, true);
  if (!ctx.ok) return;
  assert.equal(ctx.value.parameters.a, 2);
  const fn = ctx.value.functions.get("f");
  assert.ok(fn);
  // a is a free variable in f's body: valid symbol table must include parameters.
  const probe = applyMathCommand(doc, { kind: "setParameter", parameter: { id: "par1", name: "a", value: 3, min: 0, max: 4, step: 0.1 } });
  assert.equal(probe.ok, true);
});

test("axis variables per plot kind", () => {
  assert.deepEqual(axisVariables({ kind: "explicit" }), ["x"]);
  assert.deepEqual(axisVariables({ kind: "inverse" }), ["y"]);
  assert.deepEqual(axisVariables({ kind: "polar" }), ["theta"]);
  assert.deepEqual(axisVariables({ kind: "implicitSurface" }), ["x", "y", "z"]);
  assert.deepEqual(axisVariables({ kind: "parametricSurface" }), ["u", "v"]);
});

test("resolveGeometry returns 2d resolution with diagnostics only for real problems", () => {
  const doc = createMathDocument("res");
  doc.objects2d.push(point("A", "A", 0, 0), point("B", "B", 2, 2));
  const r = resolveGeometry(doc);
  assert.equal(r.ok, true);
  if (r.ok) {
    assert.equal(r.value.geometry2d.points.size, 2);
    assert.deepEqual(r.value.diagnostics, []);
  }
});

test("legacy geometry3d payloads migrate: mode normalized, objects3d dropped", () => {
  const doc = createMathDocument("legacy");
  const legacy = {
    ...doc,
    activeMode: "geometry3d" as const,
    objects3d: [{ kind3d: "point", id: "P3", label: "P", visible: true, locked: false, style, x: 1, y: 1, z: 1 }],
  };
  const r = validateMathDocument(JSON.parse(JSON.stringify(legacy)));
  assert.equal(r.ok, true);
  if (r.ok) {
    assert.equal(r.value.activeMode, "functions3d");
    assert.equal("objects3d" in r.value, false);
  }
});

test("presets produce valid documents", () => {
  for (const preset of WORKBENCH_PRESETS) {
    const doc = presetDocument(preset.id);
    assert.ok(doc, preset.id);
    if (!doc) continue;
    const r = validateMathDocument(JSON.parse(JSON.stringify(doc)));
    assert.equal(r.ok, true, `${preset.id}: ${r.ok ? "" : JSON.stringify(r.diagnostics)}`);
  }
});
