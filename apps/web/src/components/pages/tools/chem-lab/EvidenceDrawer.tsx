"use client";

/**
 * Evidence drawer (观察 → 操作 → 规则 → 概念): the chain is computed on the
 * client by the shared domain engine from the pinned pack + candidate state
 * — the same deterministic content the server would cite, never a model
 * generation.
 */
import { useMemo } from "react";
import { Drawer } from "@/components/ui/Drawer";
import { evidenceChain, type AnyDict } from "@next-tutor/domain";
import type { ChemLabEvent, ChemLabObservation } from "@/lib/api-chem-lab";

type AnyRecord = Record<string, unknown>;

function l10n(entry: unknown, language: string): string {
  if (entry && typeof entry === "object" && !Array.isArray(entry)) {
    const row = entry as AnyRecord;
    const value = row[language] ?? row.zh ?? row.en;
    if (typeof value === "string") return value;
  }
  return "";
}

function simClock(ms: number): string {
  const total = Math.floor(ms / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

export interface EvidenceDrawerProps {
  pack: AnyRecord | null;
  engineState: AnyRecord | null;
  events: ChemLabEvent[];
  observation: ChemLabObservation | null;
  language: string;
  open: boolean;
  onClose: () => void;
  tr: (key: string, fallback?: string) => string;
}

export function EvidenceDrawer({
  pack,
  engineState,
  events,
  observation,
  language,
  open,
  onClose,
  tr,
}: EvidenceDrawerProps) {
  const chain = useMemo(() => {
    if (!pack || !engineState || !observation) return null;
    try {
      return evidenceChain(pack as AnyDict, engineState as AnyDict, observation as AnyDict, events as AnyDict[]);
    } catch {
      return null;
    }
  }, [pack, engineState, observation, events]);

  const chainEvents = useMemo(
    () => (chain ? (chain.events as AnyRecord[]) : []),
    [chain],
  );
  const rule = chain?.rule as AnyRecord | null | undefined;
  const concepts = useMemo(
    () => (chain ? (chain.concepts as AnyRecord[]) : []),
    [chain],
  );

  return (
    <Drawer open={open} onClose={onClose} title={tr("evidenceTitle")} width={440}>
      {!chain ? (
        <p className="text-xs leading-5 text-muted">{tr("evidenceEmpty")}</p>
      ) : (
        <div className="space-y-4" data-testid="chem-lab-evidence-chain">
          <section>
            <h3 className="mb-1.5 text-[11px] font-medium tracking-wide text-muted">{tr("evidenceObservation")}</h3>
            <p className="rounded-[10px] border border-border-light bg-surface px-3 py-2 text-xs leading-5 text-fg">
              {tr(`obs.${observation?.key ?? ""}`, observation?.key ?? "")}
              <span className="tnum ml-2 text-[10px] text-muted">{simClock(observation?.sim_time_ms ?? 0)}</span>
            </p>
          </section>
          <section>
            <h3 className="mb-1.5 text-[11px] font-medium tracking-wide text-muted">{tr("evidenceOperations")}</h3>
            {chainEvents.length === 0 ? (
              <p className="text-xs leading-5 text-muted">{tr("evidenceNoEvents")}</p>
            ) : (
              <ol className="space-y-1">
                {chainEvents.map((event) => (
                  <li key={Number(event.seq)}
                    className="flex items-baseline gap-2 rounded-[8px] bg-surface px-2.5 py-1.5 text-[11px] leading-4 text-fg-secondary">
                    <span className="tnum shrink-0 text-[10px] text-muted">#{String(event.seq)}</span>
                    <span>{tr(`evt.${String(event.kind ?? "")}`, String(event.kind ?? ""))}</span>
                    <span className="tnum ml-auto shrink-0 text-[10px] text-muted">
                      {simClock(Number(event.sim_time_ms ?? 0))}
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </section>
          {rule && (
            <section>
              <h3 className="mb-1.5 text-[11px] font-medium tracking-wide text-muted">{tr("evidenceRule")}</h3>
              <p className="rounded-[10px] border border-accent/25 bg-accent-soft/40 px-3 py-2 text-xs leading-5 text-fg">
                {l10n(rule.public_note, language) || String(rule.id ?? "")}
              </p>
            </section>
          )}
          {concepts.length > 0 && (
            <section>
              <h3 className="mb-1.5 text-[11px] font-medium tracking-wide text-muted">{tr("concepts")}</h3>
              <div className="space-y-2">
                {concepts.map((concept) => (
                  <div key={String(concept.id)}
                    className="rounded-[10px] border border-border-light bg-surface px-3 py-2">
                    <p className="text-xs font-medium text-fg">{l10n(concept.title, language)}</p>
                    <p className="mt-1 text-[11px] leading-5 text-muted">{l10n(concept.body, language)}</p>
                  </div>
                ))}
              </div>
            </section>
          )}
          <p className="text-[10px] leading-4 text-muted">{tr("evidenceModelNote")}</p>
        </div>
      )}
    </Drawer>
  );
}
