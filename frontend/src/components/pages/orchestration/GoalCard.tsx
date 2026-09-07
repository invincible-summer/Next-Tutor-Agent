"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { CalendarClock, Flag, Pencil, Target, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { Progress } from "@/components/ui/Progress";
import { SubjectSelect } from "@/components/ui/SubjectSelect";
import {
  GenealogyConceptPicker,
  type PickedConcept,
} from "@/components/shared/GenealogyConceptPicker";
import { fmtDate } from "@/lib/format";
import { masteryColor } from "@/lib/labels";
import { cn } from "@/lib/cn";
import type { OrchGap, OrchGoal, OrchGoalState } from "@/lib/types-modules";

type Tr = (key: string, fallback?: string) => string;

const INPUT =
  "w-full rounded-[8px] border border-border bg-surface px-2.5 py-1.5 text-xs text-fg outline-none transition-colors focus:border-accent";

function daysLeft(deadline: number): number {
  return Math.ceil((deadline * 1000 - Date.now()) / 86400000);
}

function GapRow({ gap, tr }: { gap: OrchGap; tr: Tr }) {
  const router = useRouter();
  const learn = () =>
    router.push(`/chat?q=${encodeURIComponent(tr("goal.gap.learn", "帮我学") + gap.name)}&send=1`);
  return (
    <div className="group/gap flex items-center gap-2 py-1.5">
      {/* W4/A13：unknown=未测（中性，不宣称缺口）；weak=有证据的确认薄弱；
          legacy "missing" 行按旧语义渲染为 danger。 */}
      <Badge tone={
        gap.status === "unknown" ? "muted"
          : gap.status === "missing" ? "danger" : "warning"
      }>
        {tr(`goal.gap.${gap.status}`, gap.status)}
      </Badge>
      <span className="min-w-0 flex-1 truncate text-xs font-medium text-fg">{gap.name}</span>
      <span className="tnum text-[0.68rem] text-muted">
        {Math.round(gap.current_mastery * 100)}% → {Math.round(gap.target_mastery * 100)}%
      </span>
      <button
        type="button"
        onClick={learn}
        title={tr("goal.gap.learn", "去学这个概念")}
        className="shrink-0 cursor-pointer rounded-[6px] px-1.5 py-0.5 text-[0.66rem] text-accent-strong opacity-0 transition-opacity group-hover/gap:opacity-100 focus-visible:opacity-100"
      >
        {tr("goal.gap.learn", "去学")}
      </button>
      {gap.skill_id && (
        <a
          href={`/knowledge?concept=${encodeURIComponent(gap.skill_id)}`}
          title={tr("goal.gap.locate", "在谱系中查看")}
          className="shrink-0 text-[0.66rem] text-muted opacity-0 transition-opacity group-hover/gap:opacity-100 focus-visible:opacity-100 hover:text-accent"
        >
          {tr("goal.gap.locate", "谱系")}
        </a>
      )}
    </div>
  );
}

/** 差距路线图：gaps 按拓扑层级分组（第 1 层现在就能学 → 第 N 层需先补下层）。 */
function GapLayers({ gaps, tr }: { gaps: OrchGap[]; tr: Tr }) {
  const byLayer = new Map<number, OrchGap[]>();
  for (const g of gaps) {
    const layer = g.layer && g.layer > 0 ? g.layer : 0;
    const list = byLayer.get(layer) ?? [];
    list.push(g);
    byLayer.set(layer, list);
  }
  const layers = [...byLayer.entries()].sort((a, b) => {
    if (a[0] === 0) return 1; // 未分层旧数据放最后
    if (b[0] === 0) return -1;
    return a[0] - b[0];
  });
  return (
    <div className="space-y-2">
      {layers.map(([layer, items]) => (
        <div key={layer}>
          <p className="mb-0.5 text-[0.68rem] font-medium text-fg-secondary">
            {layer === 1
              ? tr("goal.layer.first", "第 1 层 · 现在就能学")
              : layer > 1
                ? tr("goal.layer.n", `第 ${layer} 层`).replace("%n", String(layer)) +
                  " · " + tr("goal.layer.pre", "需先补上层")
                : tr("goal.layer.unknown", "未分层（无前置数据）")}
            <span className="ml-1.5 font-normal text-muted">({items.length})</span>
          </p>
          <div className="divide-y divide-border-light rounded-[8px] border border-border-light px-2">
            {items.slice(0, 8).map((g) => <GapRow key={g.skill_id} gap={g} tr={tr} />)}
          </div>
          {items.length > 8 && (
            <p className="mt-0.5 pl-2 text-[0.66rem] text-muted">
              +{items.length - 8} …
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

/** 长期目标卡：目标信息 + GoalAnalyzer 差距分析。多目标下每目标一张。 */
export function GoalCard({ goal, gs, tr, onEdit, onDelete }: {
  goal: Partial<OrchGoal>;
  gs: Partial<OrchGoalState>;
  tr: Tr;
  onEdit?: () => void;
  onDelete?: () => void;
}) {
  const dl = goal.deadline && goal.deadline > 0 ? daysLeft(goal.deadline) : null;
  const ratio = gs.mastered_ratio ?? 0;
  const gaps = gs.gaps ?? [];
  const est = gs.estimate;
  const chain = gs.chain_mode === "concept_chain";
  const boundCount = goal.target_concept_ids?.length ?? 0;
  const fitTone =
    est?.fit === "tight" ? "danger" : est?.fit === "ok" ? "warning" : "success";
  return (
    <Card>
      <CardHeader
        icon={<Target size={16} />}
        title={tr("goal.title")}
        desc={tr("goal.desc")}
        right={
          <span className="flex items-center gap-2">
            {chain && (
              <span title={tr("goal.chain.desc", "差距与进度按目标概念的前置链计算")}>
                <Badge tone="accent">
                  {tr("goal.chain", "目标链")} · {boundCount}
                </Badge>
              </span>
            )}
            <Badge tone="accent">{tr(`goal.type.${goal.goal_type || "ability"}`)}</Badge>
            {onDelete && (
              <button onClick={onDelete} title={tr("goal.del")} aria-label={tr("goal.del")}
                className="cursor-pointer rounded-[6px] p-1 text-muted transition-colors hover:bg-surface-hover hover:text-danger">
                <Trash2 size={13} />
              </button>
            )}
            {onEdit && (
              <button onClick={onEdit} title={tr("goal.edit")} aria-label={tr("goal.edit")}
                className="cursor-pointer rounded-[6px] p-1 text-muted transition-colors hover:bg-surface-hover hover:text-fg">
                <Pencil size={13} />
              </button>
            )}
          </span>
        }
      />
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <span className="font-serif text-lg font-semibold text-fg">{goal.title}</span>
        {(goal.subjects || []).map((s) => (
          <Badge key={s} tone="outline">{s}</Badge>
        ))}
      </div>
      {goal.description && <p className="mb-2 text-xs leading-relaxed text-muted">{goal.description}</p>}

      <div className="mb-3 flex items-center gap-2 text-[0.72rem] text-muted">
        <CalendarClock size={12} />
        {dl === null ? (
          <span>{tr("goal.deadline.none")}</span>
        ) : (
          <span className={cn(dl < 0 && "text-danger")}>
            {fmtDate(goal.deadline!)} ·{" "}
            {dl < 0 ? tr("goal.deadline.overdue") : `${dl} ${tr("goal.deadline.left")}`}
          </span>
        )}
      </div>

      <div className="mb-1 flex items-center justify-between text-[0.72rem]">
        <span className="text-muted">
          {tr("goal.progress")}
          {chain && <span className="ml-1 text-muted/70">({tr("goal.progress.chain", "目标链口径")})</span>}
        </span>
        <span className="tnum font-medium" style={{ color: masteryColor(ratio) }}>
          {Math.round(ratio * 100)}%
          <span className="ml-1.5 text-muted/70">
            {gs.mastered_skills ?? 0}/{gs.total_skills ?? 0} {tr("goal.skills")}
          </span>
        </span>
      </div>
      <Progress value={ratio} className="mb-2" />

      {est && est.est_weeks > 0 && (
        <p className="mb-3 flex flex-wrap items-center gap-x-2 gap-y-0.5 rounded-[8px] bg-surface-hover/50 px-3 py-1.5 text-[0.7rem] text-fg-secondary">
          <span className="tnum">
            {tr("goal.estimate", "按每周 %p 个概念，约需 %e 周")
              .replace("%p", String(est.weekly_pace))
              .replace("%e", String(est.est_weeks))}
          </span>
          {est.est_weeks_min != null && est.est_weeks_max != null
            && est.est_weeks_min !== est.est_weeks_max && (
            <span className="tnum text-muted">
              · {tr("goal.estimate.range", "按你的可用时间约 %a–%b 周")
                .replace("%a", String(est.est_weeks_min))
                .replace("%b", String(est.est_weeks_max))}
            </span>
          )}
          {est.weeks_left != null && est.weeks_left > 0 && (
            <span className="tnum">
              · {tr("goal.estimate.left", "距截止还有 %w 周").replace("%w", String(est.weeks_left))}
            </span>
          )}
          {est.fit !== "none" && (
            <Badge tone={fitTone}>
              {tr(`goal.fit.${est.fit}`, est.fit)}
            </Badge>
          )}
        </p>
      )}

      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[0.72rem] text-muted">
        <span>
          {tr("goal.level")}: {tr(`goal.level.${gs.current_level || "novice"}`)}
          <span className="mx-1 text-border">{tr("goal.level.to")}</span>
          <span className="text-accent-strong">{tr(`goal.level.${gs.target_level || "proficient"}`)}</span>
        </span>
        {(gs.urgency ?? 0) > 0 && (
          <span className="flex shrink-0 items-center gap-1.5 whitespace-nowrap">
            {tr("goal.urgency")}
            <Progress value={gs.urgency ?? 0} tone={(gs.urgency ?? 0) > 0.7 ? "danger" : "warning"} className="w-16" height={4} />
          </span>
        )}
      </div>

      {gs.recommended_strategy && (
        <p className="mb-3 rounded-[8px] bg-accent-soft/40 px-3 py-2 text-[0.72rem] leading-relaxed text-accent-strong">
          <Flag size={11} className="mr-1 inline" />
          {tr(`goal.strategy.${gs.recommended_strategy}`, gs.recommended_strategy)}
        </p>
      )}

      <div className="border-t border-border-light pt-2">
        <p className="mb-1.5 flex items-center justify-between text-[0.72rem] font-medium text-fg-secondary">
          {tr("goal.gaps")}
          <span className="font-normal text-muted">
            {chain
              ? tr("goal.gaps.chain", "达成目标所需的前置链")
              : tr("goal.gaps.subject", "该学科未掌握概念")}
          </span>
        </p>
        {gaps.length === 0 ? (
          <p className="py-1 text-[0.72rem] text-muted">{tr("goal.gaps.empty")}</p>
        ) : (
          <GapLayers gaps={gaps} tr={tr} />
        )}
      </div>
    </Card>
  );
}

/** 目标设置表单：无目标时的整页引导 + 卡片内复用 + 编辑预填（initial）。
 *  学科输入走 M5.8 学科目录两级下拉（catalog 失败时 SubjectSelect 回退自由文本，仍按逗号拆分）；
 *  概念绑定走谱系选择器（学段→学科→教材→整章勾选）；description 预填现值（修复编辑覆写为空的 bug）。 */
export function GoalForm({ tr, submitting, failed, initial, submitLabel, onSubmit }: {
  tr: Tr;
  submitting: boolean;
  failed: boolean;
  initial?: Partial<OrchGoal>;
  submitLabel?: string;
  onSubmit: (payload: {
    title: string;
    description: string;
    goal_type: string;
    subjects: string[];
    target_concept_ids: string[];
    deadline: number;
  }) => void;
}) {
  const [title, setTitle] = useState(initial?.title ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [goalType, setGoalType] = useState(initial?.goal_type || "ability");
  const [level, setLevel] = useState("");
  const [subject, setSubject] = useState((initial?.subjects ?? []).join(", "));
  const [picked, setPicked] = useState<PickedConcept[]>(() =>
    // 编辑预填：概念名未知时以 id 末段作展示名，保住既有绑定不被编辑清空。
    (initial?.target_concept_ids ?? []).map((id) => ({
      id,
      name: id.split(".").pop() || id,
    })),
  );
  const [deadline, setDeadline] = useState(
    initial?.deadline && initial.deadline > 0
      ? new Date(initial.deadline * 1000).toLocaleDateString("en-CA")
      : "",
  );

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim() || submitting) return;
    onSubmit({
      title: title.trim(),
      description: description.trim(),
      goal_type: goalType,
      subjects: subject.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
      target_concept_ids: picked.map((p) => p.id),
      deadline: deadline ? new Date(`${deadline}T23:59:59`).getTime() / 1000 : 0,
    });
  };

  return (
    <form onSubmit={submit} className="space-y-3">
      <label className="block">
        <span className="mb-1 block text-[0.7rem] text-muted">{tr("form.name")}</span>
        <input className={INPUT} value={title} maxLength={60} required
          placeholder={tr("form.name.ph")}
          onChange={(e) => setTitle(e.target.value)} />
      </label>
      <label className="block">
        <span className="mb-1 block text-[0.7rem] text-muted">{tr("form.desc", "目标描述（可选）")}</span>
        <textarea className={cn(INPUT, "min-h-16 resize-y")} value={description} maxLength={200}
          placeholder={tr("form.desc.ph", "例如：期末物理上册考到 85 分，重点补力学")}
          onChange={(e) => setDescription(e.target.value)} />
      </label>
      <div>
        <span className="mb-1 block text-[0.7rem] text-muted">{tr("form.type")}</span>
        <div className="flex gap-1.5">
          {["exam", "ability", "interest"].map((gt) => (
            <button
              key={gt}
              type="button"
              onClick={() => setGoalType(gt)}
              className={cn(
                "flex-1 cursor-pointer rounded-[8px] border px-2 py-1.5 text-xs transition-colors",
                goalType === gt
                  ? "border-accent bg-accent-soft font-medium text-accent-strong"
                  : "border-border text-fg-secondary hover:border-accent/50",
              )}
            >
              {tr(`goal.type.${gt}`)}
            </button>
          ))}
        </div>
      </div>
      <SubjectSelect
        tr={tr}
        level={level}
        subject={subject}
        disabled={submitting}
        onChange={(lv, s) => {
          setLevel(lv);
          setSubject(s);
        }}
      />
      <GenealogyConceptPicker selected={picked} onChange={setPicked} />
      <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
        <label className="block">
          <span className="mb-1 block text-[0.7rem] text-muted">{tr("form.deadline")}</span>
          <input type="date" className={cn(INPUT, "cursor-pointer")} value={deadline}
            onChange={(e) => setDeadline(e.target.value)} />
        </label>
      </div>
      <div className="flex items-center gap-2">
        <Button size="sm" disabled={submitting || !title.trim()}>
          {submitting ? tr("form.submitting") : (submitLabel ?? tr("form.submit"))}
        </Button>
        {failed && <span className="text-[0.7rem] text-danger">{tr("form.failed")}</span>}
      </div>
    </form>
  );
}
