"use client";

/**
 * Preparation view (?experiment=<id>): the public experiment projection —
 * goals, procedure, reagents, safety and model scope — plus the mode chooser
 * and, for self_check, the predict-before-you-operate questions. Opening this
 * URL never creates a session; only the explicit start button does.
 */
import { useMemo } from "react";
import { Button } from "@/components/ui/Button";
import { Tabs } from "@/components/ui/Tabs";
import type { ChemLabExperimentDetail } from "@/lib/api-chem-lab";

type AnyRecord = Record<string, unknown>;

function l10n(entry: unknown, language: string): string {
  if (entry && typeof entry === "object" && !Array.isArray(entry)) {
    const row = entry as AnyRecord;
    const value = row[language] ?? row.zh ?? row.en;
    if (typeof value === "string") return value;
  }
  return "";
}

export type ChemLabMode = "guided" | "explore" | "self_check";

const MODES: ChemLabMode[] = ["guided", "explore", "self_check"];

export interface PreparationPanelProps {
  detail: ChemLabExperimentDetail;
  language: string;
  mode: ChemLabMode;
  answers: Record<string, string>;
  busy: boolean;
  tr: (key: string, fallback?: string) => string;
  onModeChange: (mode: ChemLabMode) => void;
  onAnswer: (predictionId: string, optionId: string) => void;
  onStart: () => void;
  onBack: () => void;
}

export function PreparationPanel({
  detail,
  language,
  mode,
  answers,
  busy,
  tr,
  onModeChange,
  onAnswer,
  onStart,
  onBack,
}: PreparationPanelProps) {
  const goals = useMemo(() => (detail.goals ?? []) as AnyRecord[], [detail]);
  const steps = useMemo(() => (detail.procedure ?? []) as AnyRecord[], [detail]);
  const reagents = useMemo(() => (detail.reagents ?? []) as AnyRecord[], [detail]);
  const predictions = useMemo(() => (detail.predictions ?? []) as AnyRecord[], [detail]);
  const safety = (detail.safety_profile ?? {}) as AnyRecord;
  const supportedModes = MODES.filter((value) => detail.modes.includes(value));
  const showPredictions = mode === "self_check" && predictions.length > 0;
  const answeredAll = predictions.every((prediction) => answers[String(prediction.id ?? "")]);

  return (
    <div className="mx-auto max-w-3xl px-5 py-6 sm:px-8" data-testid="chem-lab-preparation">
      <button
        type="button"
        onClick={onBack}
        className="mb-4 min-h-[36px] cursor-pointer rounded-md px-2 text-xs text-muted transition-colors hover:bg-surface-hover hover:text-fg focus-visible:outline-2 focus-visible:outline-accent"
      >
        ← {tr("backToCatalog")}
      </button>
      <header>
        <div className="flex flex-wrap items-center gap-1.5">
          {detail.audience.map((key) => (
            <span key={key} className="rounded-full bg-surface-hover px-2 py-0.5 text-[10px] text-fg-secondary">
              {tr(`audience.${key}`)}
            </span>
          ))}
        </div>
        <h1 className="mt-2 font-serif text-2xl font-semibold tracking-tight text-fg">
          {l10n(detail.title, language)}
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-fg-secondary">{l10n(detail.summary, language)}</p>
      </header>

      <section className="mt-6" aria-label={tr("goals")}>
        <h2 className="mb-2 text-xs font-semibold text-fg">{tr("goals")}</h2>
        <ul className="space-y-1.5">
          {goals.map((goal) => (
            <li key={String(goal.id)} className="flex items-start gap-2 text-xs leading-5 text-fg-secondary">
              <span aria-hidden className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
              {l10n(goal.title, language)}
            </li>
          ))}
        </ul>
      </section>

      {steps.length > 0 && (
        <section className="mt-5" aria-label={tr("procedure")}>
          <h2 className="mb-2 text-xs font-semibold text-fg">{tr("procedure")}</h2>
          <ol className="space-y-2">
            {steps.map((step, index) => (
              <li key={String(step.id)} className="rounded-[10px] border border-border-light bg-surface px-3 py-2">
                <p className="text-xs font-medium text-fg">
                  {index + 1}. {l10n(step.title, language)}
                </p>
                <p className="mt-0.5 text-[11px] leading-5 text-muted">{l10n(step.objective, language)}</p>
              </li>
            ))}
          </ol>
        </section>
      )}

      {reagents.length > 0 && (
        <section className="mt-5" aria-label={tr("reagents")}>
          <h2 className="mb-2 text-xs font-semibold text-fg">{tr("reagents")}</h2>
          <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {reagents.map((reagent) => (
              <li key={String(reagent.id)} className="rounded-[10px] border border-border-light bg-surface px-3 py-2">
                <p className="text-xs font-medium text-fg">{l10n(reagent.concentration_label, language) || String(reagent.id)}</p>
                <p className="mt-0.5 text-[11px] leading-5 text-muted">{l10n(reagent.description, language)}</p>
              </li>
            ))}
          </ul>
        </section>
      )}

      {l10n(safety.notes, language) && (
        <section className="mt-5" aria-label={tr("safety")}>
          <h2 className="mb-2 text-xs font-semibold text-fg">{tr("safety")}</h2>
          <p className="rounded-[10px] border border-[#e0a23c]/35 bg-[#e0a23c]/8 px-3 py-2 text-xs leading-5 text-fg-secondary">
            {l10n(safety.notes, language)}
          </p>
        </section>
      )}

      <section className="mt-5" aria-label={tr("modelScope")}>
        <h2 className="mb-2 text-xs font-semibold text-fg">{tr("modelScope")}</h2>
        <p className="text-[11px] leading-5 text-muted">{l10n(detail.model_scope, language)}</p>
      </section>

      <section className="mt-6" aria-label={tr("modeTitle")}>
        <h2 className="mb-2 text-xs font-semibold text-fg">{tr("modeTitle")}</h2>
        <Tabs
          items={supportedModes.map((value) => ({ key: value, label: tr(`mode.${value}`) }))}
          active={mode}
          onChange={(key) => onModeChange(key as ChemLabMode)}
        />
        <p className="mt-2 text-[11px] leading-5 text-muted">{tr(`mode.${mode}Desc`)}</p>
      </section>

      {showPredictions && (
        <section className="mt-6" aria-label={tr("predictTitle")} data-testid="chem-lab-predictions">
          <h2 className="mb-1 text-xs font-semibold text-fg">{tr("predictTitle")}</h2>
          <p className="mb-3 text-[11px] leading-5 text-muted">{tr("predictDesc")}</p>
          <div className="space-y-3">
            {predictions.map((prediction) => {
              const id = String(prediction.id ?? "");
              const options = Array.isArray(prediction.options) ? (prediction.options as AnyRecord[]) : [];
              return (
                <fieldset key={id} className="rounded-[12px] border border-border-light bg-surface px-3 py-2.5">
                  <legend className="px-1 text-xs font-medium text-fg">{l10n(prediction.question, language)}</legend>
                  <div className="mt-1 space-y-1">
                    {options.map((option) => {
                      const optionId = String(option.id ?? "");
                      const checked = answers[id] === optionId;
                      return (
                        <label
                          key={optionId}
                          className={`flex min-h-[36px] cursor-pointer items-center gap-2 rounded-[8px] px-2 py-1.5 text-xs leading-5 transition-colors ${
                            checked ? "bg-accent-soft text-fg" : "text-fg-secondary hover:bg-surface-hover"
                          }`}
                        >
                          <input
                            type="radio"
                            name={`prediction-${id}`}
                            checked={checked}
                            onChange={() => onAnswer(id, optionId)}
                            className="accent-[var(--color-accent,#2f6f6a)]"
                          />
                          {l10n(option.label, language)}
                        </label>
                      );
                    })}
                  </div>
                </fieldset>
              );
            })}
          </div>
        </section>
      )}

      <div className="mt-8 flex items-center gap-3">
        <Button
          size="lg"
          demoWrite
          disabled={busy || (showPredictions && !answeredAll)}
          onClick={onStart}
          data-testid="chem-lab-start"
        >
          {busy ? tr("starting") : tr("start")}
        </Button>
        {showPredictions && !answeredAll && (
          <span className="text-[11px] text-muted">{tr("predictFirst")}</span>
        )}
      </div>
    </div>
  );
}
