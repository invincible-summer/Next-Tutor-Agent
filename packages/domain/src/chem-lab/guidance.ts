/**
 * Deterministic teaching guidance (no AI): hints, evidence, explanations.
 * Mirror of guidance.py — reads the pack's procedure, recent semantic
 * events and the current state diff; never mutates chemistry. Levels:
 * on_track / try_again / hint / explain / safety / complete.
 */
import type { AnyDict } from "./units.ts";

function _l10n(pair: Record<string, string>, language: string): string {
  return pair[language] ?? pair.zh ?? pair.en ?? "";
}

export function currentStep(pack: AnyDict, completed: string[]): AnyDict | null {
  const done = new Set(completed);
  for (const step of (pack.procedure ?? []) as AnyDict[]) {
    if (!done.has(step.id)) return step;
  }
  return null;
}

export function goalsStatus(pack: AnyDict, state: AnyDict): AnyDict[] {
  const emitted = new Set((state.observations as AnyDict[]).map((o) => o.key));
  const status: AnyDict[] = [];
  for (const goal of state.goals as AnyDict[]) {
    const spec = ((pack.goals ?? []) as AnyDict[]).find((g) => g.id === goal.id) ?? null;
    const met = spec !== null
      && (spec.requires_observations as string[]).every((key) => emitted.has(key));
    status.push({ id: goal.id, status: met ? "met" : "pending" });
  }
  return status;
}

export function allGoalsMet(pack: AnyDict, state: AnyDict): boolean {
  const status = goalsStatus(pack, state);
  return status.length > 0 && status.every((g) => g.status === "met");
}

export function buildGuidance(
  pack: AnyDict,
  state: AnyDict,
  options: { lastEvents: AnyDict[]; language: string },
): AnyDict {
  const { lastEvents, language } = options;
  if (state.phase === "safety_locked") {
    return {
      level: "safety",
      text: _l10n(pack.safety_profile.notes, language),
      evidence_event_seq: state.seq,
      concept_ids: [],
      model_scope: pack.model_fidelity,
    };
  }

  const step = state.mode !== "explore" ? currentStep(pack, state.completed_steps) : null;
  const rejected = lastEvents.filter((e) => e.kind === "command_rejected");
  const newObservations = lastEvents.filter((e) => e.kind === "observation_emitted");

  if (state.phase === "completed" || ((pack.goals ?? []).length > 0 && allGoalsMet(pack, state))) {
    return {
      level: "complete",
      text: _l10n({
        zh: "实验目标已达成，可以查看结果卡或从检查点创建分支继续探索。",
        en: "Experiment goals met. Review the result card or branch from a checkpoint.",
      }, language),
      evidence_event_seq: state.seq,
      concept_ids: [],
      model_scope: pack.model_fidelity,
    };
  }

  if (rejected.length > 0) {
    const last = rejected[rejected.length - 1] as AnyDict;
    const reason = last.data.reason ?? "invalid";
    return {
      level: "try_again",
      text: _rejectionText(reason, language),
      evidence_event_seq: last.seq,
      concept_ids: step ? step.concept_ids : [],
      model_scope: pack.model_fidelity,
    };
  }

  if (step !== null) {
    const visits = state.step_visits[step.id] ?? 0;
    const ladder = step.hint_ladder as AnyDict[];
    const index = Math.min(visits, ladder.length - 1);
    let text: string;
    let level: string;
    if (newObservations.length > 0 || visits === 0) {
      text = _l10n(step.objective, language);
      level = "on_track";
    } else {
      text = _l10n(ladder[index] as Record<string, string>, language);
      level = "hint";
    }
    return {
      level,
      text,
      step_id: step.id,
      hint_index: index,
      evidence_event_seq: state.seq,
      concept_ids: step.concept_ids,
      model_scope: pack.model_fidelity,
    };
  }

  if (newObservations.length > 0) {
    return {
      level: "explain",
      text: _l10n({
        zh: "观察到了新的现象，可以在观察记录中查看证据链。",
        en: "New phenomenon observed — open the log for the evidence chain.",
      }, language),
      evidence_event_seq: (newObservations[newObservations.length - 1] as AnyDict).seq,
      concept_ids: [],
      model_scope: pack.model_fidelity,
    };
  }

  return {
    level: "on_track",
    text: _l10n({
      zh: "继续你的实验；已建模范围见实验说明。",
      en: "Keep going; the modeled scope is described in the brief.",
    }, language),
    evidence_event_seq: state.seq,
    concept_ids: [],
    model_scope: pack.model_fidelity,
  };
}

function _rejectionText(reason: string, language: string): string {
  const table: Record<string, [string, string]> = {
    invalid_params: [
      "这条操作的参数不合法，请检查对象和数量。",
      "That operation's parameters were invalid — check the object and amount.",
    ],
    unknown_object: [
      "找不到这个器材或容器，请重新选择。",
      "That object could not be found — pick it again.",
    ],
    capacity_exceeded: [
      "目标容器装不下这么多液体，请减少用量或换更大的容器。",
      "The target vessel cannot hold that much — use less or a larger vessel.",
    ],
    instrument_empty: [
      "这个仪器现在是空的，请先吸取或装入试剂。",
      "The instrument is empty — draw up a reagent first.",
    ],
    not_clean: [
      "滴管里残留着其他试剂；请先清洗或换一支，避免污染。",
      "The dropper still holds another reagent — wash it or use a fresh one.",
    ],
    incompatible_device: [
      "这个容器不能放在该装置上加热。",
      "This vessel cannot be heated on that device.",
    ],
    not_connected: [
      "还没有连接导管，气体无法被收集。",
      "No delivery tube connected — the gas cannot be collected.",
    ],
    safety_locked: [
      "实验已因安全原因锁定，请查看原因并从检查点继续。",
      "The bench is safety-locked — read the reason and continue from a checkpoint.",
    ],
    phase: [
      "当前状态下不能执行这个操作。",
      "That operation is not available right now.",
    ],
    not_modeled: [
      "当前实验还没有描述这组物质混合后的变化。可以返回上一步或选择已支持的组合。",
      "This combination is not described by the current model. Step back or choose a supported one.",
    ],
  };
  const [zh, en] = table[reason] ?? (table.invalid_params as [string, string]);
  return _l10n({ zh, en }, language);
}

export function evidenceChain(
  pack: AnyDict,
  state: AnyDict,
  observation: AnyDict,
  events: AnyDict[],
): AnyDict {
  // Observation → events → rule → concept cards (证据层).
  const seq = observation.seq;
  const related = events.filter(
    (e) => e.seq <= seq && e.vessel_id === (observation.vessel_id ?? ""),
  );
  const ruleId = observation.rule_id ?? "";
  const rule = ((pack._rules ?? []) as AnyDict[]).find((r) => r.id === ruleId) ?? null;
  const observationConcepts = (observation.concept_ids ?? []) as string[];
  const concepts = ((pack._concepts ?? []) as AnyDict[]).filter(
    (c) => observationConcepts.includes(c.id),
  );
  return {
    observation,
    events: related.slice(-12),
    rule: rule ? { id: rule.id, public_note: rule.public_note } : null,
    concepts,
    model_scope: pack.model_fidelity,
  };
}
