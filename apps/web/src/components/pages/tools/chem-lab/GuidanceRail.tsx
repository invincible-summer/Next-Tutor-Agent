"use client";

/**
 * Guidance rail: current goals, the deterministic guidance message with its
 * evidence pointer ("based on your operation #N") and linked concept cards.
 * Guidance is content + deterministic rules — no AI in v1.
 */
import { useMemo } from "react";
import type { ChemLabGoalStatus, ChemLabGuidance } from "@/lib/api-chem-lab";

const LEVEL_STYLE: Record<string, string> = {
  on_track: "border-accent/25 bg-accent-soft/50 text-fg",
  try_again: "border-[#e0a23c]/40 bg-[#e0a23c]/10 text-fg",
  hint: "border-[#e0a23c]/40 bg-[#e0a23c]/10 text-fg",
  explain: "border-accent/25 bg-accent-soft/50 text-fg",
  safety: "border-danger/40 bg-danger/10 text-fg",
  complete: "border-accent/40 bg-accent-soft text-fg",
};

export interface GuidanceRailProps {
  goals: ChemLabGoalStatus[];
  goalTitles: Map<string, string>;
  guidance: ChemLabGuidance | null;
  concepts: Map<string, { title: string; body: string }>;
  modelScopeText: string;
  rejected: boolean;
  tr: (key: string, fallback?: string) => string;
}

export function GuidanceRail({
  goals,
  goalTitles,
  guidance,
  concepts,
  modelScopeText,
  rejected,
  tr,
}: GuidanceRailProps) {
  const conceptCards = useMemo(() => {
    const ids = guidance?.concept_ids ?? [];
    return ids
      .map((id) => ({ id, ...(concepts.get(id) ?? { title: id, body: "" }) }))
      .filter((card) => card.title);
  }, [guidance, concepts]);

  return (
    <div className="space-y-4" data-testid="chem-lab-guidance-rail">
      {goals.length > 0 && (
        <section aria-label={tr("goals")}>
          <h3 className="mb-2 text-[11px] font-medium tracking-wide text-muted">{tr("goals")}</h3>
          <ul className="space-y-1.5">
            {goals.map((goal) => (
              <li key={goal.id} className="flex items-start gap-2 text-xs leading-5">
                <span
                  aria-hidden
                  className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${goal.status === "met" ? "bg-accent" : "bg-border"}`}
                />
                <span className={goal.status === "met" ? "text-fg" : "text-muted"}>
                  {goalTitles.get(goal.id) ?? goal.id}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
      <section aria-label={tr("guidance")} aria-live="polite">
        <h3 className="mb-2 text-[11px] font-medium tracking-wide text-muted">{tr("guidance")}</h3>
        {guidance ? (
          <div
            className={`rounded-[12px] border px-3 py-2.5 text-xs leading-5 ${LEVEL_STYLE[guidance.level] ?? LEVEL_STYLE.on_track}`}
            data-testid="chem-lab-guidance"
            data-level={guidance.level}
          >
            <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide opacity-70">
              {tr(`level.${guidance.level}`)}
              {rejected && guidance.level !== "safety" ? ` · ${tr("rejected")}` : ""}
            </p>
            <p>{guidance.text}</p>
            {guidance.evidence_event_seq > 0 && (
              <p className="mt-1.5 text-[10px] opacity-60">
                {tr("evidenceSeq").replace("%n", String(guidance.evidence_event_seq))}
              </p>
            )}
          </div>
        ) : (
          <p className="text-xs leading-5 text-muted">{tr("noGuidance")}</p>
        )}
      </section>
      {conceptCards.length > 0 && (
        <section aria-label={tr("concepts")}>
          <h3 className="mb-2 text-[11px] font-medium tracking-wide text-muted">{tr("concepts")}</h3>
          <div className="space-y-2">
            {conceptCards.map((card) => (
              <details key={card.id} className="rounded-[10px] border border-border-light bg-surface px-3 py-2">
                <summary className="cursor-pointer text-xs font-medium text-fg">{card.title}</summary>
                {card.body && <p className="mt-1.5 text-[11px] leading-5 text-muted">{card.body}</p>}
              </details>
            ))}
          </div>
        </section>
      )}
      {modelScopeText && (
        <p className="text-[10px] leading-4 text-muted" data-testid="chem-lab-model-scope">
          {tr("modelScope")}：{modelScopeText}
        </p>
      )}
    </div>
  );
}
