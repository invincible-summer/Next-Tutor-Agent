"use client";

import Link from "next/link";
import { ArrowRight, BookOpen, Flame, ListChecks, ListTodo, PencilLine, RotateCcw } from "lucide-react";
import { Card } from "@/components/ui/Card";
import type { OrchDailyTask, OrchPlanSummary } from "@/lib/types-modules";

type Tr = (key: string, fallback?: string) => string;

const KIND_ICON = {
  study: BookOpen,
  review: RotateCcw,
  practice: PencilLine,
  summary: ListChecks,
} as const;

/** M9 今日任务速览：今日待办前 3 条 + 连击，点击进 /orchestration。
 * 未设长期目标时整卡隐藏（无目标即无任务体系）。多目标下展示目标数。 */
export function TodayTasksCard({ plan, tasks, tr }: {
  plan: OrchPlanSummary | null;
  tasks: OrchDailyTask[];
  tr: Tr;
}) {
  const goals = (plan?.goals ?? []).filter((g) => !!g.title);
  if (goals.length === 0) return null;
  const streak = plan?.habit?.current_streak ?? 0;
  const open = tasks.filter((t) => t.status !== "completed").slice(0, 3);
  return (
    <Card>
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[8px] bg-accent-soft text-accent-strong">
            <ListTodo size={15} />
          </span>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-fg">{tr("orch.title")}</p>
            <p className="truncate text-[0.68rem] text-muted">
              {tr("orch.goal")}: {goals[0].title}
              {goals.length > 1 && ` +${goals.length - 1}`}
            </p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          {streak > 0 && (
            <span className="flex items-center gap-1 text-accent2">
              <Flame size={14} />
              <span className="tnum text-sm font-semibold">{streak}</span>
              <span className="text-[0.66rem] text-muted">{tr("orch.days")}</span>
            </span>
          )}
          <Link href="/orchestration" className="flex items-center gap-0.5 text-[0.72rem] text-accent-strong hover:underline">
            {tr("orch.more")} <ArrowRight size={11} />
          </Link>
        </div>
      </div>
      <div className="mt-2.5">
        {open.length === 0 ? (
          <p className="py-1 text-[0.72rem] text-muted">{tr("orch.empty")}</p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {open.map((t) => {
              const Icon = KIND_ICON[t.kind as keyof typeof KIND_ICON] ?? BookOpen;
              return (
                <span
                  key={t.id}
                  className="flex items-center gap-1.5 rounded-[7px] border border-border-light bg-bg px-2 py-1 text-[0.7rem] text-fg-secondary"
                >
                  <Icon size={11} className="text-accent" />
                  {t.concept_name || tr("orch.summary")}
                  <span className="tnum text-muted/70">{t.estimate_minutes}′</span>
                </span>
              );
            })}
          </div>
        )}
      </div>
    </Card>
  );
}
