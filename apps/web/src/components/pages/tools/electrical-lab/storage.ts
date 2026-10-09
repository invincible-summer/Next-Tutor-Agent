import { deserializeElectricalLabDocument, serializeElectricalLabDocument, validateElectricalLabDocument, type ElectricalLabDocument } from "@next-tutor/domain";

export interface SavedElectricalLab { id: string; name: string; createdAt: number; updatedAt: number; document: ElectricalLabDocument }
interface ElectricalLabStore { version: 1; activeId: string; items: SavedElectricalLab[] }
const MAX_ITEMS = 24;
function key(owner: string): string { return `next-tutor.electrical-lab.v1:${encodeURIComponent(owner || "guest")}`; }
export function loadElectricalLabs(owner: string): ElectricalLabStore {
  try {
    const raw = localStorage.getItem(key(owner)); if (!raw) return { version: 1, activeId: "", items: [] };
    const row = JSON.parse(raw) as Partial<ElectricalLabStore>;
    if (row.version !== 1 || !Array.isArray(row.items)) return { version: 1, activeId: "", items: [] };
    const items = row.items.flatMap(item => {
      try { if (!item || typeof item !== "object") return []; const value = item as SavedElectricalLab; const document = validateElectricalLabDocument(value.document); return [{ id: String(value.id), name: document.name, createdAt: Number(value.createdAt) || Date.now(), updatedAt: Number(value.updatedAt) || Date.now(), document }]; } catch { return []; }
    }).slice(0, MAX_ITEMS);
    return { version: 1, activeId: typeof row.activeId === "string" ? row.activeId : "", items };
  } catch { return { version: 1, activeId: "", items: [] }; }
}
export function persistElectricalLabs(owner: string, store: ElectricalLabStore): void {
  const trimmed = { ...store, items: store.items.slice(0, MAX_ITEMS) };
  try { localStorage.setItem(key(owner), JSON.stringify(trimmed)); } catch { /* in-memory fallback */ }
}
export function exportElectricalLab(document: ElectricalLabDocument): Blob { return new Blob([serializeElectricalLabDocument(document)], { type: "application/json" }); }
export async function importElectricalLab(file: File): Promise<ElectricalLabDocument> {
  if (file.size > 250_000) throw new Error("circuit_too_large");
  return deserializeElectricalLabDocument(await file.text());
}
