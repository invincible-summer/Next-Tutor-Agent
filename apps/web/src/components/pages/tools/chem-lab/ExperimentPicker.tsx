"use client";

/**
 * Experiment catalog grid for /tools/lab: one card per published experiment
 * pack — title, summary, audience, modes and the model-fidelity badge. Pure
 * display; picking a card navigates to the preparation view (no session is
 * created implicitly).
 */
import type { ChemLabExperimentSummary } from "@/lib/api-chem-lab";

function l10n(entry: Record<string, string>, language: string): string {
  return entry[language] ?? entry.zh ?? entry.en ?? "";
}

export interface ExperimentPickerProps {
  experiments: ChemLabExperimentSummary[];
  language: string;
  busy?: boolean;
  tr: (key: string, fallback?: string) => string;
  onPick: (experimentId: string) => void;
}

export function ExperimentPicker({ experiments, language, busy, tr, onPick }: ExperimentPickerProps) {
  if (!experiments.length) {
    return <p className="py-10 text-center text-sm text-muted" role="status">{tr("catalogEmpty")}</p>;
  }
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3" data-testid="chem-lab-catalog">
      {experiments.map((experiment) => (
        <button
          key={`${experiment.id}@${experiment.pack_version}`}
          type="button"
          disabled={busy}
          onClick={() => onPick(experiment.id)}
          data-testid={`chem-lab-experiment-${experiment.id}`}
          className="group flex min-h-[150px] cursor-pointer flex-col rounded-[14px] border border-border bg-surface p-4 text-left transition-[border-color,box-shadow,transform] duration-200 hover:-translate-y-0.5 hover:border-accent/60 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 motion-reduce:transform-none"
        >
          <div className="flex items-center gap-1.5">
            {experiment.modes.map((mode) => (
              <span key={mode} className="rounded-full bg-accent-soft px-2 py-0.5 text-[10px] font-medium text-accent-strong">
                {tr(`mode.${mode}`)}
              </span>
            ))}
          </div>
          <h3 className="mt-2.5 font-serif text-[15px] font-semibold leading-6 text-fg">
            {l10n(experiment.title, language)}
          </h3>
          <p className="mt-1.5 line-clamp-3 text-xs leading-5 text-fg-secondary">
            {l10n(experiment.summary, language)}
          </p>
          <div className="mt-auto flex items-center justify-between gap-2 pt-3 text-[10px] text-muted">
            <span>{experiment.audience.map((key) => tr(`audience.${key}`)).join(" · ")}</span>
            <span className="rounded-full border border-border-light px-1.5 py-0.5">{tr("fidelity")}</span>
          </div>
        </button>
      ))}
    </div>
  );
}
