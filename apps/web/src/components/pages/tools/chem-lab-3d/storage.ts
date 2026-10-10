/**
 * 本地存档（plan §7.2）：localStorage `next-tutor.chem-lab.v1:<owner>`，
 * owner 按登录用户分区（匿名用 guest 命名空间）。12 存档上限、单文档 150KB；
 * 写入前经 domain 严格序列化，读取经 parseDocument 校验；失败明确抛出，
 * 不假装成功。不引入 IndexedDB / 服务端同步。
 */
import {
  LIMITS, parseDocument, serializeDocument, type LabDocument,
} from "@next-tutor/domain";

const MAX_SAVES = 12;

export interface ChemLabSaveMeta { id: string; name: string; stageId: string; savedAt: number }

interface SaveRow extends ChemLabSaveMeta { data: string }
interface StoreShape { version: 1; items: SaveRow[] }

export type ChemLabStorageCode =
  | "too_many_saves" | "too_large" | "quota" | "serialize_failed" | "not_found" | "bad_save";

export class ChemLabStorageError extends Error {
  code: ChemLabStorageCode;
  constructor(code: ChemLabStorageCode) { super(code); this.name = "ChemLabStorageError"; this.code = code; }
}

function storageKey(owner: string): string {
  return `next-tutor.chem-lab.v1:${encodeURIComponent(owner || "guest")}`;
}

function readStore(owner: string): StoreShape {
  try {
    const raw = localStorage.getItem(storageKey(owner));
    if (!raw) return { version: 1, items: [] };
    const row = JSON.parse(raw) as Partial<StoreShape>;
    if (row.version !== 1 || !Array.isArray(row.items)) return { version: 1, items: [] };
    const items = row.items.filter((item): item is SaveRow =>
      Boolean(item) && typeof item.id === "string" && typeof item.data === "string");
    return { version: 1, items: items.slice(0, MAX_SAVES) };
  } catch {
    return { version: 1, items: [] };
  }
}

function writeStore(owner: string, store: StoreShape): void {
  try {
    localStorage.setItem(storageKey(owner), JSON.stringify({ version: 1, items: store.items.slice(0, MAX_SAVES) }));
  } catch {
    throw new ChemLabStorageError("quota");
  }
}

export function listChemLabSaves(owner: string): ChemLabSaveMeta[] {
  return readStore(owner).items
    .map(({ id, name, stageId, savedAt }) => ({ id, name, stageId, savedAt }))
    .sort((a, b) => b.savedAt - a.savedAt);
}

/** 保存（新槽或按 overwriteId 覆盖）；满/超限/配额失败都明确抛出。 */
export function saveChemLabDocument(owner: string, doc: LabDocument, name: string, overwriteId?: string): ChemLabSaveMeta {
  let data: string;
  try {
    data = serializeDocument(doc);
  } catch {
    throw new ChemLabStorageError("serialize_failed");
  }
  if (data.length > LIMITS.maxDocumentBytes) throw new ChemLabStorageError("too_large");
  const store = readStore(owner);
  const existing = overwriteId ? store.items.findIndex(i => i.id === overwriteId) : -1;
  if (existing >= 0) {
    store.items[existing] = { id: overwriteId!, name, stageId: doc.stageId, savedAt: Date.now(), data };
  } else {
    if (store.items.length >= MAX_SAVES) throw new ChemLabStorageError("too_many_saves");
    store.items.push({ id: `save-${Date.now().toString(36)}-${Math.floor(Math.random() * 1e4).toString(36)}`, name, stageId: doc.stageId, savedAt: Date.now(), data });
  }
  writeStore(owner, store);
  const saved = overwriteId ? store.items.find(i => i.id === overwriteId)! : store.items[store.items.length - 1]!;
  return { id: saved.id, name: saved.name, stageId: saved.stageId, savedAt: saved.savedAt };
}

export function loadChemLabSave(owner: string, saveId: string): LabDocument {
  const row = readStore(owner).items.find(i => i.id === saveId);
  if (!row) throw new ChemLabStorageError("not_found");
  try {
    return parseDocument(row.data);
  } catch {
    throw new ChemLabStorageError("bad_save");
  }
}

export function deleteChemLabSave(owner: string, saveId: string): boolean {
  const store = readStore(owner);
  const next = store.items.filter(i => i.id !== saveId);
  if (next.length === store.items.length) return false;
  writeStore(owner, { version: 1, items: next });
  return true;
}
