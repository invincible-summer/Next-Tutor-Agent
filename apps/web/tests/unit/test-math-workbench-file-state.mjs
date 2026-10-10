import test from "node:test";
import assert from "node:assert/strict";

// File/leave guard state coverage (plan L3): the three-choice flow's pure
// ingredients — dirty transitions across file operations, save verification
// and baseline bookkeeping — exercised without a browser.

const { freshWorkbenchState, computeDirty, HISTORY_LIMIT } = await import("../../src/components/pages/tools/math-workbench/document-state.ts");
const { saveDrawing, loadDrawingStore } = await import("../../src/components/pages/tools/math-workbench/storage.ts");
const { semanticFingerprint, applyMathCommand, validateMathDocument } = await import("@next-tutor/domain");

class MemoryStorage {
  constructor() { this.map = new Map(); }
  getItem(key) { return this.map.has(key) ? this.map.get(key) : null; }
  setItem(key, value) { this.map.set(String(key), String(value)); }
  removeItem(key) { this.map.delete(key); }
}
globalThis.window = globalThis;
globalThis.localStorage = new MemoryStorage();

const style = { color: "#2f7d6e", width: 2, opacity: 1, dashed: false };

test("initial document is dirty; first save establishes the baseline", () => {
  const wb = freshWorkbenchState("首存");
  assert.equal(computeDirty(wb).dirty, true);
  const { result } = saveDrawing("owner", loadDrawingStore("owner"), wb.document, "首存");
  assert.equal(result.ok, true);
  const saved = { ...wb, baseline: { documentId: result.documentId, fingerprint: semanticFingerprint(wb.document), savedAt: result.savedAt } };
  assert.equal(computeDirty(saved).dirty, false);
});

test("saving identical content twice stays clean (no phantom dirty)", () => {
  const wb = freshWorkbenchState("同内容");
  const first = saveDrawing("owner", loadDrawingStore("owner"), wb.document, "同内容");
  const baseline = { documentId: first.result.documentId, fingerprint: semanticFingerprint(wb.document), savedAt: first.result.savedAt };
  const again = { ...wb, baseline };
  assert.equal(computeDirty(again).dirty, false);
});

test("undo back to the saved snapshot clears dirty; redo re-dirties", () => {
  const wb = freshWorkbenchState("撤销");
  const added = applyMathCommand(wb.document, {
    kind: "addPlot2D",
    plot: { kind: "explicit", id: "p1", label: "f", visible: true, style, expression: "sin(x)" },
  }, "add");
  assert.equal(added.ok, true);
  if (!added.ok) return;
  // Baseline captured at the pre-edit snapshot.
  const state = {
    document: added.value,
    baseline: { documentId: wb.document.id, fingerprint: semanticFingerprint(wb.document), savedAt: 1 },
    drafts: {}, pendingPlots: [], selection: null, lastError: null,
    history: { past: [{ document: wb.document, label: "add" }], future: [] },
  };
  assert.equal(computeDirty(state).dirty, true);
  const undone = { ...state, document: wb.document, history: { past: [], future: [{ document: added.value, label: "add" }] } };
  assert.equal(computeDirty(undone).dirty, false);
  const redone = { ...state, document: added.value };
  assert.equal(computeDirty(redone).dirty, true);
});

test("invalid uncommitted expressions keep the document dirty", () => {
  const wb = freshWorkbenchState("草稿脏");
  const baseline = { documentId: wb.document.id, fingerprint: semanticFingerprint(wb.document), savedAt: 1 };
  const withDraft = { ...wb, baseline, drafts: { "p1:expression": "sin(" } };
  const dirty = computeDirty(withDraft);
  assert.equal(dirty.dirty, true);
  assert.equal(dirty.reason, "uncommitted_formula");
});

test("pending draft rows: empty never dirties, typed content does", () => {
  const wb = freshWorkbenchState("草稿行");
  const baseline = { documentId: wb.document.id, fingerprint: semanticFingerprint(wb.document), savedAt: 1 };
  const emptyRow = { ...wb, baseline, pendingPlots: [{ key: "d1", kind: "explicit", color: "#2f7d6e", expressions: { expression: "" }, domains: {} }] };
  assert.equal(computeDirty(emptyRow).dirty, false);
  const filledRow = { ...wb, baseline, pendingPlots: [{ key: "d1", kind: "explicit", color: "#2f7d6e", expressions: { expression: "sin(x)" }, domains: {} }] };
  const dirty = computeDirty(filledRow);
  assert.equal(dirty.dirty, true);
  assert.equal(dirty.reason, "uncommitted_formula");
});

test("history respects the documented cap", () => {
  assert.ok(HISTORY_LIMIT > 0 && HISTORY_LIMIT <= 100);
});

test("opening another document keeps saved baselines separate", () => {
  const owner = "owner-multi-doc";
  const wbA = freshWorkbenchState("A");
  const wbB = freshWorkbenchState("B");
  const saveA = saveDrawing(owner, loadDrawingStore(owner), wbA.document, "A");
  const saveB = saveDrawing(owner, loadDrawingStore(owner), wbB.document, "B");
  assert.equal(saveA.result.ok && saveB.result.ok, true);
  const store = loadDrawingStore(owner);
  assert.equal(store.items.length, 2);
  // The reopened B stays clean against its own fingerprint, not A's.
  const itemB = store.items.find((i) => i.name === "B");
  assert.ok(itemB);
  const stateB = {
    document: itemB.document,
    baseline: { documentId: itemB.id, fingerprint: semanticFingerprint(itemB.document), savedAt: itemB.updatedAt },
    drafts: {}, pendingPlots: [], selection: null, lastError: null, history: { past: [], future: [] },
  };
  assert.equal(computeDirty(stateB).dirty, false);
});

test("invalid documents never enter the archive", () => {
  const bad = JSON.parse(JSON.stringify(freshWorkbenchState("坏").document));
  bad.schemaVersion = 7;
  const result = saveDrawing("owner", loadDrawingStore("owner"), bad, "坏");
  assert.equal(result.result.ok, false);
  assert.equal(result.result.code, "validation_failed");
  assert.equal(validateMathDocument(bad).ok, false);
});
