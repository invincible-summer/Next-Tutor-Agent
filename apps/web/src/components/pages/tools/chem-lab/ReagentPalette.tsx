"use client";

/**
 * Reagent palette: the pack-declared reagents with live remaining volume.
 * Clicking a reagent selects its vessel and opens the shared measure/pour
 * panel — dragging works too, but dragging is never required (WCAG single
 * pointer alternative).
 */
import { useMemo, useState } from "react";
import { FIELD_CLS } from "@/components/ui/Input";
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

function formatMl(volumeUL: number): string {
  const ml = volumeUL / 1000;
  return ml >= 100 ? String(Math.round(ml)) : ml.toFixed(1).replace(/\.0$/, "");
}

export interface ReagentPaletteProps {
  pack: AnyRecord | null;
  display: ChemLabDisplay;
  language: string;
  selected: ChemLabObjectRef | null;
  busy: boolean;
  onSelect: (ref: ChemLabObjectRef) => void;
  onOperate: (draft: OperationDraft) => void;
  strings: {
    title: string;
    search: string;
    empty: string;
    noMatch: string;
    remaining: string;
    measureOut: string;
    pour: string;
  };
}

interface ReagentRow {
  id: string;
  vesselId: string;
  name: string;
  description: string;
  concentration: string;
  volumeUL: number;
}

export function ReagentPalette({
  pack,
  display,
  language,
  selected,
  busy,
  onSelect,
  onOperate,
  strings: tr,
}: ReagentPaletteProps) {
  const [query, setQuery] = useState("");

  const rows = useMemo<ReagentRow[]>(() => {
    const reagents = Array.isArray(pack?.reagents) ? (pack.reagents as AnyRecord[]) : [];
    const start = (pack?.starting_state ?? {}) as AnyRecord;
    const vesselLabels = new Map<string, string>();
    for (const vessel of Array.isArray(start.vessels) ? (start.vessels as AnyRecord[]) : []) {
      vesselLabels.set(String(vessel.id ?? ""), l10n(vessel.label, language));
    }
    const vessels = (display.engineState?.vessels ?? {}) as AnyRecord;
    return reagents.map((reagent) => {
      const vesselId = String(reagent.vessel_id ?? "");
      const vessel = vessels[vesselId] as AnyRecord | undefined;
      return {
        id: String(reagent.id ?? vesselId),
        vesselId,
        name: vesselLabels.get(vesselId) || vesselId,
        description: l10n(reagent.description, language),
        concentration: l10n(reagent.concentration_label, language),
        volumeUL: Number(vessel?.volume_uL ?? 0),
      };
    });
  }, [pack, display.engineState, language]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter((row) =>
      `${row.name} ${row.description} ${row.concentration}`.toLowerCase().includes(needle),
    );
  }, [rows, query]);

  return (
    <section aria-label={tr.title} data-testid="chem-lab-reagent-palette">
      <h3 className="mb-2 text-[11px] font-medium tracking-wide text-muted">{tr.title}</h3>
      {rows.length > 3 && (
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={tr.search}
          aria-label={tr.search}
          className={`${FIELD_CLS} mb-2 h-8 text-xs`}
          data-testid="chem-lab-reagent-search"
        />
      )}
      {!rows.length ? (
        <p className="text-xs leading-5 text-muted">{tr.empty}</p>
      ) : !filtered.length ? (
        <p className="text-xs leading-5 text-muted" role="status">{tr.noMatch}</p>
      ) : (
        <ul className="space-y-1.5">
          {filtered.map((row) => {
            const isOn = selected?.type === "vessel" && selected.id === row.vesselId;
            return (
              <li key={row.id}>
                <div
                  className={`rounded-[10px] border px-2 py-1.5 transition-colors ${
                    isOn ? "border-accent/40 bg-accent-soft" : "border-transparent hover:bg-surface-hover"
                  }`}
                >
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => onSelect({ type: "vessel", id: row.vesselId })}
                    aria-pressed={isOn}
                    data-testid={`chem-lab-reagent-${row.vesselId}`}
                    className="block min-h-[36px] w-full cursor-pointer rounded-md px-1 text-left focus-visible:outline-2 focus-visible:outline-accent"
                  >
                    <span className="flex items-baseline justify-between gap-2">
                      <span className="truncate text-xs font-medium text-fg">{row.name}</span>
                      <span className="tnum shrink-0 text-[10px] text-muted">
                        {tr.remaining.replace("%n", formatMl(row.volumeUL))}
                      </span>
                    </span>
                    {(row.concentration || row.description) && (
                      <span className="mt-0.5 block truncate text-[10px] text-muted">
                        {row.concentration || row.description}
                      </span>
                    )}
                  </button>
                  <div className="mt-1 flex gap-1">
                    <button
                      type="button"
                      disabled={busy || row.volumeUL <= 0}
                      onClick={() => {
                        onSelect({ type: "vessel", id: row.vesselId });
                        onOperate({ action: "pour", sourceId: row.vesselId });
                      }}
                      data-testid={`chem-lab-reagent-pour-${row.vesselId}`}
                      className="min-h-[32px] flex-1 cursor-pointer rounded-md bg-accent-soft px-2 text-[10px] font-medium text-accent-strong hover:bg-accent/20 focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-40"
                    >
                      {tr.pour}
                    </button>
                    <button
                      type="button"
                      disabled={busy || row.volumeUL <= 0}
                      onClick={() => {
                        onSelect({ type: "vessel", id: row.vesselId });
                        onOperate({ action: "aspirate", sourceId: row.vesselId });
                      }}
                      className="min-h-[32px] flex-1 cursor-pointer rounded-md bg-surface-hover px-2 text-[10px] font-medium text-fg-secondary hover:bg-border-light focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-40"
                    >
                      {tr.measureOut}
                    </button>
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
