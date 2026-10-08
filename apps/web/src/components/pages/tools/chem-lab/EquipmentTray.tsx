"use client";

/**
 * Equipment rail: every instrument/device in the session with its live state
 * (load, cleanliness, connection). Selection feeds the same operation panel
 * as the stage — the tray never builds commands itself.
 */
import { useMemo } from "react";
import type { ChemLabDisplay } from "./useChemLabSession";
import type { ChemLabObjectRef, OperationDraft } from "./interaction";

type AnyRecord = Record<string, unknown>;

function l10n(entry: unknown, language: string): string {
  if (entry && typeof entry === "object" && !Array.isArray(entry)) {
    const row = entry as AnyRecord;
    const value = row[language] ?? row.zh ?? row.en;
    if (typeof value === "string") return value;
  }
  return "";
}

export interface EquipmentTrayProps {
  pack: AnyRecord | null;
  display: ChemLabDisplay;
  language: string;
  selected: ChemLabObjectRef | null;
  busy: boolean;
  onSelect: (ref: ChemLabObjectRef) => void;
  onOperate: (draft: OperationDraft) => void;
  strings: {
    title: string;
    empty: string;
    clean: string;
    dirty: string;
    loaded: string;
    connected: string;
    wash: string;
    measure: string;
  };
}

function formatMl(volumeUL: number): string {
  const ml = volumeUL / 1000;
  return ml >= 10 ? ml.toFixed(1).replace(/\.0$/, "") : ml.toFixed(1);
}

export function EquipmentTray({
  pack,
  display,
  language,
  selected,
  busy,
  onSelect,
  onOperate,
  strings: tr,
}: EquipmentTrayProps) {
  const equipmentDefs = useMemo(
    () => ((pack?._equipment_defs ?? pack?.equipment_defs ?? {}) as AnyRecord),
    [pack],
  );

  const items = useMemo(() => {
    const equipment = (display.engineState?.equipment ?? {}) as AnyRecord;
    return Object.keys(equipment)
      .sort()
      .map((id) => {
        const item = equipment[id] as AnyRecord;
        const kind = String(item.kind ?? "");
        const def = equipmentDefs[kind] as AnyRecord | undefined;
        const load = (item.load ?? {}) as AnyRecord;
        const volume = Number(load.volume_uL ?? 0);
        const clean = Boolean(load.clean ?? true);
        const connected = item.connected as AnyRecord | null;
        const measures = Array.isArray(def?.measures) ? (def.measures as string[]) : [];
        return {
          id,
          kind,
          name: l10n(def?.name, language) || id,
          volume,
          clean,
          connected: Boolean(connected),
          measures,
        };
      });
  }, [display.engineState, equipmentDefs, language]);

  return (
    <section aria-label={tr.title} data-testid="chem-lab-equipment-tray">
      <h3 className="mb-2 text-[11px] font-medium tracking-wide text-muted">{tr.title}</h3>
      {!items.length ? (
        <p className="text-xs leading-5 text-muted">{tr.empty}</p>
      ) : (
        <ul className="space-y-1.5">
          {items.map((item) => {
            const isOn = selected?.type === "equipment" && selected.id === item.id;
            const status = [
              item.volume > 0 ? tr.loaded.replace("%n", formatMl(item.volume)) : "",
              !item.clean ? tr.dirty : "",
              item.connected ? tr.connected : "",
            ]
              .filter(Boolean)
              .join(" · ");
            return (
              <li key={item.id}>
                <div
                  className={`flex items-center gap-1 rounded-[10px] border px-2 py-1.5 transition-colors ${
                    isOn ? "border-accent/40 bg-accent-soft" : "border-transparent hover:bg-surface-hover"
                  }`}
                >
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => onSelect({ type: "equipment", id: item.id })}
                    aria-pressed={isOn}
                    data-testid={`chem-lab-tray-${item.id}`}
                    className="min-h-[36px] min-w-0 flex-1 cursor-pointer rounded-md px-1 text-left focus-visible:outline-2 focus-visible:outline-accent"
                  >
                    <span className="block truncate text-xs font-medium text-fg">{item.name}</span>
                    <span className="mt-0.5 block text-[10px] text-muted">
                      {status || tr.clean}
                    </span>
                  </button>
                  {!item.clean && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => onOperate({ action: "wash", instrumentId: item.id })}
                      className="min-h-[32px] shrink-0 cursor-pointer rounded-md px-2 text-[10px] font-medium text-accent hover:bg-accent-soft focus-visible:outline-2 focus-visible:outline-accent"
                    >
                      {tr.wash}
                    </button>
                  )}
                  {item.measures.length > 0 && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => onOperate({ action: "measure", instrumentId: item.id })}
                      className="min-h-[32px] shrink-0 cursor-pointer rounded-md px-2 text-[10px] font-medium text-accent hover:bg-accent-soft focus-visible:outline-2 focus-visible:outline-accent"
                    >
                      {tr.measure}
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
