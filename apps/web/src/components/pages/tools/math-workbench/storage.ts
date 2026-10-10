"use client";

/**
 * Explicit local-file persistence for the math workbench (plan J1, ADR-0022).
 * Only user-triggered save/save-as/delete writes the archive key; crash
 * recovery drafts use a separate sessionStorage key and never masquerade as
 * saves. Data is per-browser, per-owner — never described as cloud sync.
 *
 * Store v2: each drawing mode keeps its own independent active document
 * (`activeIds`); a v1 archive (single shared `activeId`) migrates on load by
 * filing the old active drawing under its own mode's slot.
 */

import {
  deserializeMathDocument, serializeMathDocument, semanticFingerprint, validateMathDocument, MATH_LIMITS,
  type DrawingMode, type MathWorkbenchDocument,
} from "./workbench-types.ts";

export interface SavedDrawing {
  id: string;
  name: string;
  createdAt: number;
  updatedAt: number;
  document: MathWorkbenchDocument;
}

export type ModeActiveIds = Record<DrawingMode, string>;

export interface DrawingStore {
  version: 2;
  activeIds: ModeActiveIds;
  items: SavedDrawing[];
}

export type SaveResult =
  | { ok: true; documentId: string; savedAt: number }
  | { ok: false; code: "invalid_draft" | "storage_unavailable" | "quota_exceeded" | "validation_failed" };

const KEY_PREFIX = "next-tutor.math-workbench.v1:";
const RECOVERY_PREFIX = "next-tutor.math-workbench.recovery:";
const MAX_ITEMS = MATH_LIMITS.maxSavedDocuments;
const MAX_JSON_BYTES = MATH_LIMITS.maxDocumentBytes;
const RECOVERY_MAX_AGE_MS = 1000 * 60 * 60 * 24 * 3; // recovery entries expire after 3 days

function archiveKey(owner: string): string {
  return `${KEY_PREFIX}${encodeURIComponent(owner || "guest")}`;
}

function recoveryKey(owner: string, draftId: string): string {
  return `${RECOVERY_PREFIX}${encodeURIComponent(owner)}:${encodeURIComponent(draftId)}`;
}

export function emptyActiveIds(): ModeActiveIds {
  return { functions2d: "", geometry2d: "", functions3d: "" };
}

export function emptyDrawingStore(): DrawingStore {
  return { version: 2, activeIds: emptyActiveIds(), items: [] };
}

function readItems(raw: unknown): SavedDrawing[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const record = item as { createdAt?: unknown; updatedAt?: unknown; document?: unknown };
    const validated = validateMathDocument(record.document);
    if (!validated.ok) return [];
    return [{
      id: validated.value.id,
      name: validated.value.name,
      createdAt: Number(record.createdAt) || Date.now(),
      updatedAt: Number(record.updatedAt) || Date.now(),
      document: validated.value,
    }];
  }).slice(0, MAX_ITEMS);
}

/** Slot each item's id under its own mode when it is the stored active doc. */
function migrateActiveIds(activeId: unknown, items: SavedDrawing[]): ModeActiveIds {
  const activeIds = emptyActiveIds();
  if (typeof activeId !== "string" || !activeId) return activeIds;
  const item = items.find((i) => i.id === activeId);
  if (item) activeIds[item.document.activeMode] = item.id;
  return activeIds;
}

function normalizeActiveIds(activeIds: unknown, items: SavedDrawing[]): ModeActiveIds {
  const known = new Set(items.map((i) => i.id));
  const out = emptyActiveIds();
  if (!activeIds || typeof activeIds !== "object") return out;
  const record = activeIds as Record<string, unknown>;
  for (const mode of Object.keys(out) as DrawingMode[]) {
    const id = record[mode];
    if (typeof id === "string" && known.has(id)) out[mode] = id;
  }
  return out;
}

export function loadDrawingStore(owner: string): DrawingStore {
  if (typeof window === "undefined") return emptyDrawingStore();
  try {
    const raw = window.localStorage.getItem(archiveKey(owner));
    if (!raw) return emptyDrawingStore();
    const parsed = JSON.parse(raw) as { version?: unknown; activeId?: unknown; activeIds?: unknown; items?: unknown };
    const items = readItems(parsed.items);
    if (parsed.version === 1) {
      // v1: one shared activeId across all modes — file it under its own mode.
      return { version: 2, activeIds: migrateActiveIds(parsed.activeId, items), items };
    }
    if (parsed.version !== 2 || !Array.isArray(parsed.items)) return emptyDrawingStore();
    return { version: 2, activeIds: normalizeActiveIds(parsed.activeIds, items), items };
  } catch {
    return emptyDrawingStore();
  }
}

/** Read-back verification: only a verified write reports success (D6). */
function persistStore(owner: string, store: DrawingStore): SaveResult {
  if (typeof window === "undefined") return { ok: false, code: "storage_unavailable" };
  const trimmed: DrawingStore = { version: 2, activeIds: store.activeIds, items: store.items.slice(0, MAX_ITEMS) };
  const payload = JSON.stringify(trimmed);
  if (payload.length > MAX_JSON_BYTES * MAX_ITEMS) return { ok: false, code: "quota_exceeded" };
  try {
    window.localStorage.setItem(archiveKey(owner), payload);
    const readBack = window.localStorage.getItem(archiveKey(owner));
    if (readBack !== payload) return { ok: false, code: "storage_unavailable" };
    return { ok: true, documentId: "", savedAt: Date.now() };
  } catch (error) {
    const name = (error as { name?: string })?.name ?? "";
    if (name === "QuotaExceededError" || name === "NS_ERROR_DOM_QUOTA_REACHED") return { ok: false, code: "quota_exceeded" };
    return { ok: false, code: "storage_unavailable" };
  }
}

export function uniqueDrawingName(store: DrawingStore, base: string): string {
  const names = new Set(store.items.map((i) => i.name));
  if (!names.has(base)) return base;
  let n = 2;
  while (names.has(`${base} ${n}`)) n++;
  return `${base} ${n}`;
}

export function saveDrawing(owner: string, store: DrawingStore, document: MathWorkbenchDocument, name?: string): { result: SaveResult; store: DrawingStore } {
  const validation = validateMathDocument(JSON.parse(serializeMathDocument(document)));
  if (!validation.ok) return { result: { ok: false, code: "validation_failed" }, store };
  const finalName = (name && name.trim().length > 0 ? name.trim() : document.name.trim()) || uniqueDrawingName(store, "未命名绘图 1");
  const named: MathWorkbenchDocument = { ...validation.value, name: finalName };
  const now = Date.now();
  const index = store.items.findIndex((i) => i.id === named.id);
  const items = [...store.items];
  if (index >= 0) {
    items[index] = { id: named.id, name: finalName, createdAt: items[index]?.createdAt ?? now, updatedAt: now, document: named };
  } else {
    items.unshift({ id: named.id, name: finalName, createdAt: now, updatedAt: now, document: named });
  }
  // Saving marks this document as its mode's active drawing.
  const activeIds = { ...store.activeIds, [named.activeMode]: named.id };
  const next: DrawingStore = { version: 2, activeIds, items };
  const persisted = persistStore(owner, next);
  const result: SaveResult = persisted.ok ? { ok: true, documentId: named.id, savedAt: now } : persisted;
  return { result, store: persisted.ok ? next : store };
}

export function saveAsDrawing(owner: string, store: DrawingStore, document: MathWorkbenchDocument, name: string): { result: SaveResult; store: DrawingStore; documentId?: string } {
  const validation = validateMathDocument(JSON.parse(serializeMathDocument(document)));
  if (!validation.ok) return { result: { ok: false, code: "validation_failed" }, store };
  const finalName = name.trim() || uniqueDrawingName(store, "未命名绘图 1");
  const newId = `doc${Date.now().toString(36)}${Math.floor(Math.random() * 46656).toString(36)}`;
  const renamed: MathWorkbenchDocument = { ...validation.value, id: newId, name: finalName };
  const now = Date.now();
  const activeIds = { ...store.activeIds, [renamed.activeMode]: newId };
  const next: DrawingStore = {
    version: 2,
    activeIds,
    items: [{ id: newId, name: finalName, createdAt: now, updatedAt: now, document: renamed }, ...store.items].slice(0, MAX_ITEMS),
  };
  const persisted = persistStore(owner, next);
  const result: SaveResult = persisted.ok ? { ok: true, documentId: newId, savedAt: now } : persisted;
  return { result, store: persisted.ok ? next : store, documentId: newId };
}

export function deleteDrawing(owner: string, store: DrawingStore, documentId: string): DrawingStore {
  const activeIds = { ...store.activeIds };
  for (const mode of Object.keys(activeIds) as DrawingMode[]) {
    if (activeIds[mode] === documentId) activeIds[mode] = "";
  }
  const next: DrawingStore = { version: 2, activeIds, items: store.items.filter((i) => i.id !== documentId) };
  if (typeof window !== "undefined") {
    try { window.localStorage.setItem(archiveKey(owner), JSON.stringify(next)); } catch { /* archive stays in memory */ }
  }
  return next;
}

export function renameDrawing(owner: string, store: DrawingStore, documentId: string, name: string): DrawingStore {
  const next: DrawingStore = {
    ...store,
    items: store.items.map((i) => (i.id === documentId ? { ...i, name, updatedAt: Date.now(), document: { ...i.document, name } } : i)),
  };
  if (typeof window !== "undefined") {
    try { window.localStorage.setItem(archiveKey(owner), JSON.stringify(next)); } catch { /* keep memory copy */ }
  }
  return next;
}

/** Remember which document a mode had open last (no content write). */
export function setActiveDrawing(owner: string, store: DrawingStore, mode: DrawingMode, documentId: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(archiveKey(owner), JSON.stringify({ ...store, activeIds: { ...store.activeIds, [mode]: documentId } }));
  } catch { /* non-fatal */ }
}

export function importDrawingFile(file: File): Promise<MathWorkbenchDocument> {
  return file.text().then((text) => {
    if (text.length > MAX_JSON_BYTES) throw new Error("document_too_large");
    const parsed = deserializeMathDocument(text);
    if (!parsed.ok) throw new Error(parsed.diagnostics[0]?.code ?? "invalid_json");
    // Imports become unsaved new documents with fresh ids (never overwrite).
    const newId = `doc${Date.now().toString(36)}${Math.floor(Math.random() * 46656).toString(36)}`;
    return { ...parsed.value, id: newId };
  });
}

export function exportDrawingJson(document: MathWorkbenchDocument): Blob {
  return new Blob([serializeMathDocument(document)], { type: "application/json" });
}

/* ------------------------------ crash recovery ------------------------------ */

export function writeRecoveryDraft(owner: string, draftId: string, document: MathWorkbenchDocument): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(recoveryKey(owner, draftId), JSON.stringify({ at: Date.now(), document }));
  } catch { /* best effort by design */ }
}

export function readRecoveryDraft(owner: string, draftId: string): MathWorkbenchDocument | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(recoveryKey(owner, draftId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { at?: number; document?: unknown };
    if (typeof parsed.at !== "number" || Date.now() - parsed.at > RECOVERY_MAX_AGE_MS) {
      window.sessionStorage.removeItem(recoveryKey(owner, draftId));
      return null;
    }
    const validated = validateMathDocument(parsed.document);
    return validated.ok ? validated.value : null;
  } catch {
    return null;
  }
}

export function clearRecoveryDraft(owner: string, draftId: string): void {
  if (typeof window === "undefined") return;
  try { window.sessionStorage.removeItem(recoveryKey(owner, draftId)); } catch { /* ignore */ }
}

/** Deterministic fingerprint helper re-export for callers of storage APIs. */
export { semanticFingerprint };
