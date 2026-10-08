"use client";

/**
 * Branch comparison: two sessions side by side — per-vessel visible state,
 * readings, sim time and pack version. The variable analysis derives changed
 * operation categories from the two event logs; when more than one category
 * changed it says so, instead of dressing two different pictures up as a
 * single-variable conclusion.
 */
import { useMemo } from "react";
import { Drawer } from "@/components/ui/Drawer";
import type { ChemLabEvent, ChemLabRenderFrame } from "@/lib/api-chem-lab";
import { vesselVisual } from "./chem-lab-renderer";

type AnyRecord = Record<string, unknown>;

export interface BranchSide {
  label: string;
  frame: ChemLabRenderFrame | null;
  engineState: AnyRecord | null;
  events: ChemLabEvent[];
  revision: number;
  simTimeMs: number;
  packHash: string;
}

export interface BranchCompareProps {
  pack: AnyRecord | null;
  base: BranchSide | null;
  branch: BranchSide | null;
  language: string;
  open: boolean;
  onClose: () => void;
  tr: (key: string, fallback?: string) => string;
}

function simClock(ms: number): string {
  const total = Math.floor(ms / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/** Operation categories one comparison may attribute a difference to. */
const VARIABLE_CATEGORY: Array<{ key: string; kinds: string[] }> = [
  { key: "amount", kinds: ["liquid_transferred", "liquid_poured", "reagent_measured"] },
  { key: "heat", kinds: ["heat_applied", "temperature_changed"] },
  { key: "stir", kinds: ["stirred"] },
  { key: "connection", kinds: ["instrument_connected"] },
];

function changedCategories(events: ChemLabEvent[]): Set<string> {
  const kinds = new Set(events.map((event) => event.kind));
  const out = new Set<string>();
  for (const category of VARIABLE_CATEGORY) {
    if (category.kinds.some((kind) => kinds.has(kind))) out.add(category.key);
  }
  return out;
}

function SideSummary({
  side,
  pack,
  language,
  tr,
}: {
  side: BranchSide;
  pack: AnyRecord | null;
  language: string;
  tr: (key: string, fallback?: string) => string;
}) {
  const vessels = (side.engineState?.vessels ?? {}) as AnyRecord;
  const rows = Object.keys(vessels)
    .sort()
    .map((id) => vesselVisual(pack, side.frame, side.engineState, id, language));
  const start = (pack?.starting_state ?? {}) as AnyRecord;
  const labels = new Map<string, string>();
  for (const vessel of Array.isArray(start.vessels) ? (start.vessels as AnyRecord[]) : []) {
    const entry = vessel.label as AnyRecord | undefined;
    labels.set(String(vessel.id ?? ""), String(entry?.[language] ?? entry?.zh ?? vessel.id ?? ""));
  }
  return (
    <div className="min-w-0 flex-1 rounded-[12px] border border-border-light bg-surface p-3">
      <p className="truncate text-xs font-semibold text-fg">{side.label}</p>
      <p className="tnum mt-0.5 text-[10px] text-muted">
        {tr("simTime")} {simClock(side.simTimeMs)} · {tr("revisionLabel").replace("%n", String(side.revision))}
      </p>
      <ul className="mt-2 space-y-1.5">
        {rows.map((row) => (
          <li key={row.vesselId} className="rounded-[8px] bg-bg px-2 py-1.5 text-[11px] leading-4">
            <span className="block truncate font-medium text-fg">{labels.get(row.vesselId) ?? row.vesselId}</span>
            <span className="text-muted">
              {[
                row.volumeUL > 0 ? `${(row.volumeUL / 1000).toFixed(1)} mL` : tr("empty"),
                row.liquidLabel && row.fillRatio > 0 ? row.liquidLabel : "",
                row.precipitate ? tr("hasPrecipitate") : "",
                row.bubbles ? tr("hasBubbles") : "",
                row.temperatureMilliC !== null ? `${(row.temperatureMilliC / 1000).toFixed(1)} °C` : "",
              ]
                .filter(Boolean)
                .join(" · ")}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function BranchCompare({
  pack,
  base,
  branch,
  language,
  open,
  onClose,
  tr,
}: BranchCompareProps) {
  const analysis = useMemo(() => {
    if (!base || !branch) return null;
    const baseVars = changedCategories(base.events);
    const branchVars = changedCategories(branch.events);
    const union = new Set([...baseVars, ...branchVars]);
    const sameVersion = base.packHash === branch.packHash;
    return { variables: [...union], single: union.size === 1, sameVersion };
  }, [base, branch]);

  return (
    <Drawer open={open} onClose={onClose} title={tr("compareTitle")} width={560}>
      {!base || !branch ? (
        <p className="text-xs leading-5 text-muted">{tr("compareEmpty")}</p>
      ) : (
        <div className="space-y-3" data-testid="chem-lab-branch-compare">
          <div className="flex gap-2.5">
            <SideSummary side={base} pack={pack} language={language} tr={tr} />
            <SideSummary side={branch} pack={pack} language={language} tr={tr} />
          </div>
          {analysis && (
            <div className="rounded-[10px] border border-border-light bg-surface px-3 py-2 text-[11px] leading-5 text-fg-secondary">
              <p>
                {tr("compareVariables")}：
                {analysis.variables.length
                  ? analysis.variables.map((key) => tr(`variable.${key}`)).join("、")
                  : tr("variable.none")}
              </p>
              {!analysis.single && analysis.variables.length > 1 && (
                <p className="mt-1 text-[#8a6420]">{tr("compareMultiVariable")}</p>
              )}
              {!analysis.sameVersion && (
                <p className="mt-1 text-danger">{tr("compareVersionDiff")}</p>
              )}
            </div>
          )}
        </div>
      )}
    </Drawer>
  );
}
