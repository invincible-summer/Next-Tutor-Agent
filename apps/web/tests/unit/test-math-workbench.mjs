import test from "node:test";
import assert from "node:assert/strict";

// Pure-logic unit tests for the math workbench web layer (no DOM needed:
// localStorage is shimmed; React modules are only imported for their pure
// helpers). The package test script registers the repository TypeScript loader,
// so this stays runnable on Node builds without native type stripping.

const { freshWorkbenchState, computeDirty, isHistoryCommand } = await import("../../src/components/pages/tools/math-workbench/document-state.ts");
const {
  saveDrawing, saveAsDrawing, deleteDrawing, renameDrawing, loadDrawingStore, importDrawingFile,
  writeRecoveryDraft, readRecoveryDraft, clearRecoveryDraft, uniqueDrawingName,
} = await import("../../src/components/pages/tools/math-workbench/storage.ts");
const { isTaskResponse } = await import("../../src/components/pages/tools/math-workbench/worker/protocol.ts");
const { semanticFingerprint, createMathDocument } = await import("@next-tutor/domain");

/* ------------------------- browser storage shims ------------------------- */
class MemoryStorage {
  constructor() { this.map = new Map(); }
  get length() { return this.map.size; }
  key(index) { return [...this.map.keys()][index] ?? null; }
  getItem(key) { return this.map.has(key) ? this.map.get(key) : null; }
  setItem(key, value) { this.map.set(String(key), String(value)); }
  removeItem(key) { this.map.delete(key); }
  clear() { this.map.clear(); }
}
globalThis.window = globalThis;
globalThis.localStorage = new MemoryStorage();
globalThis.sessionStorage = new MemoryStorage();

const style = { color: "#2f7d6e", width: 2, opacity: 1, dashed: false };

test("dirty state machine: new → saved → edited → undo restores clean", () => {
  const state = freshWorkbenchState("测试");
  assert.deepEqual(computeDirty(state), { dirty: true, reason: "new" });

  const fingerprint = semanticFingerprint(state.document);
  const saved = { ...state, baseline: { documentId: state.document.id, fingerprint, savedAt: Date.now() } };
  assert.deepEqual(computeDirty(saved), { dirty: false, reason: null });

  // View/camera/mode edits are transient — never dirty (plan D6 asymmetry).
  const moved = {
    ...saved,
    document: {
      ...saved.document,
      revision: 99,
      activeMode: "geometry2d",
      view2d: { ...saved.document.view2d, scale: 0.01, centerX: 40 },
      camera3d: { ...saved.document.camera3d, azimuth: 2 },
    },
  };
  assert.deepEqual(computeDirty(moved), { dirty: false, reason: null });

  // Semantic content edits dirty the document again.
  const edited = {
    ...saved,
    document: {
      ...saved.document,
      plots2d: [{ kind: "explicit", id: "p1", label: "f", visible: true, style, expression: "sin(x)" }],
    },
  };
  assert.deepEqual(computeDirty(edited), { dirty: true, reason: "document_changed" });

  // Uncommitted expression drafts keep the doc dirty even after commit-less typing.
  const drafted = { ...saved, drafts: { p1: "cos(x)" } };
  assert.equal(computeDirty(drafted).dirty, true);
  assert.equal(computeDirty(drafted).reason, "uncommitted_formula");
});

test("storage: explicit save roundtrips per owner and reports failures honestly", () => {
  const owner = "user-a";
  const doc = createMathDocument("我的绘图");
  const first = saveDrawing(owner, loadDrawingStore(owner), doc, "我的绘图");
  assert.equal(first.result.ok, true);
  assert.equal(first.store.items.length, 1);

  const reopened = loadDrawingStore(owner);
  assert.equal(reopened.items.length, 1);
  assert.equal(reopened.items[0]?.name, "我的绘图");

  // Owner isolation: another owner sees nothing.
  assert.equal(loadDrawingStore("user-b").items.length, 0);

  // Quota failure keeps dirty semantics: result fails, store unchanged.
  const originalSet = globalThis.localStorage.setItem.bind(globalThis.localStorage);
  globalThis.localStorage.setItem = () => {
    const error = new Error("full");
    error.name = "QuotaExceededError";
    throw error;
  };
  const failed = saveDrawing(owner, loadDrawingStore(owner), { ...doc, name: "again" }, "again");
  assert.equal(failed.result.ok, false);
  assert.equal(failed.result.code, "quota_exceeded");
  globalThis.localStorage.setItem = originalSet;

  // save-as creates a NEW id and never overwrites the original.
  const asCopy = saveAsDrawing(owner, loadDrawingStore(owner), doc, "副本");
  assert.equal(asCopy.result.ok, true);
  assert.equal(asCopy.store.items.length, 2);
  assert.notEqual(asCopy.documentId, doc.id);

  // Rename + delete work on the archive.
  const renamed = renameDrawing(owner, loadDrawingStore(owner), doc.id, "改名");
  assert.equal(renamed.items.find((i) => i.id === doc.id)?.name, "改名");
  const deleted = deleteDrawing(owner, loadDrawingStore(owner), doc.id);
  assert.equal(deleted.items.find((i) => i.id === doc.id), undefined);
  assert.equal(uniqueDrawingName(deleted, "改名"), "改名");
});

test("import: tampered JSON is rejected, valid imports become new documents", async () => {
  const doc = createMathDocument("导出");
  const valid = JSON.stringify(doc);
  const imported = await importDrawingFile(new File([valid], "drawing.json", { type: "application/json" }));
  assert.equal(imported.id !== doc.id, true); // fresh id, never overwrites
  assert.equal(imported.name, "导出");

  const tampered = JSON.stringify({ ...doc, schemaVersion: 99 });
  await assert.rejects(importDrawingFile(new File([tampered], "bad.json")));
  const evil = JSON.stringify({ ...doc, plots2d: [{ kind: "explicit", id: "x", label: "f", visible: true, style, expression: "eval(1)" }] });
  await assert.rejects(importDrawingFile(new File([evil], "evil.json")));
});

test("recovery drafts live in sessionStorage and expire", () => {
  const owner = "user-a";
  const doc = createMathDocument("草稿");
  writeRecoveryDraft(owner, doc.id, doc);
  const recovered = readRecoveryDraft(owner, doc.id);
  assert.ok(recovered);
  assert.equal(recovered?.name, "草稿");
  clearRecoveryDraft(owner, doc.id);
  assert.equal(readRecoveryDraft(owner, doc.id), null);
  // Corrupt entries return null instead of throwing.
  globalThis.sessionStorage.setItem(`next-tutor.math-workbench.recovery:${owner}:${doc.id}`, "{not json");
  assert.equal(readRecoveryDraft(owner, doc.id), null);
});

test("worker protocol rejects malformed responses", () => {
  assert.equal(isTaskResponse({ id: "1", documentId: "d", revision: 3, ok: true, result: {} }), true);
  assert.equal(isTaskResponse({ id: "1", ok: true }), false);
  assert.equal(isTaskResponse(null), false);
  assert.equal(isTaskResponse("nope"), false);
});

test("per-mode bundles: fresh documents carry their mode; view commands never enter history", () => {
  assert.equal(freshWorkbenchState("", "geometry2d").document.activeMode, "geometry2d");
  assert.equal(freshWorkbenchState("", "functions3d").document.activeMode, "functions3d");
  assert.equal(freshWorkbenchState("", "functions2d").document.activeMode, "functions2d");

  const view = freshWorkbenchState("", "functions2d").document.view2d;
  assert.equal(isHistoryCommand({ kind: "setView2D", view }), false);
  assert.equal(isHistoryCommand({ kind: "setCamera3D", camera: { kind: "perspective", azimuth: 1, elevation: 1, distance: 10, target: { x: 0, y: 0, z: 0 } } }), false);
  // A batch only matters for history when it contains at least one edit.
  assert.equal(isHistoryCommand({ kind: "batch", commands: [{ kind: "setView2D", view }] }), false);
  assert.equal(isHistoryCommand({
    kind: "addPlot2D",
    plot: { kind: "explicit", id: "p1", label: "f", visible: true, style, expression: "sin(x)" },
  }), true);
});

test("storage v2: per-mode active drawings, v1 archive migrates", () => {
  const owner = "user-modes";
  const f2 = createMathDocument("二维", "functions2d");
  const g2 = createMathDocument("平面", "geometry2d");
  const s3 = createMathDocument("三维", "functions3d");
  const working = [
    () => saveDrawing(owner, loadDrawingStore(owner), f2, "二维").store,
    (store) => saveDrawing(owner, store, g2, "平面").store,
    (store) => saveDrawing(owner, store, s3, "三维").store,
  ].reduce((store, step) => step(store), loadDrawingStore(owner));
  assert.equal(working.items.length, 3);

  const reopened = loadDrawingStore(owner);
  assert.equal(reopened.version, 2);
  assert.equal(reopened.activeIds.functions2d, f2.id);
  assert.equal(reopened.activeIds.geometry2d, g2.id);
  assert.equal(reopened.activeIds.functions3d, s3.id);
  assert.equal("geometry3d" in reopened.activeIds, false);

  // v1 archive (single shared activeId) files it under the doc's own mode.
  const v1 = { version: 1, activeId: f2.id, items: reopened.items.map((i) => ({ ...i })) };
  globalThis.localStorage.setItem(`next-tutor.math-workbench.v1:${owner}`, JSON.stringify(v1));
  const migrated = loadDrawingStore(owner);
  assert.equal(migrated.version, 2);
  assert.equal(migrated.activeIds.functions2d, f2.id);
  assert.equal(migrated.activeIds.geometry2d, "");
});
