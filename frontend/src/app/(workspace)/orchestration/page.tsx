"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { AlertTriangle, MessagesSquare, Play, Plus, Target, X } from "lucide-react";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { cn } from "@/lib/cn";
import {
  addOrchSubtask,
  addOrchTask,
  addOrchWeek,
  addOrchWeekConcept,
  addOrchWeekTask,
  completeOrchTask,
  deleteOrchGoal,
  deleteOrchSubtask,
  deleteOrchTask,
  deleteOrchWeek,
  deleteOrchWeekTask,
  getOrchPlan,
  getOrchToday,
  patchOrchGoal,
  removeOrchWeekConcept,
  setOrchGoal,
  suggestOrchSubtasks,
  toggleOrchSubtask,
  updateOrchTask,
  type OrchSubtaskPayload,
  type OrchTaskCreatePayload,
  type OrchTaskPatchPayload,
  type OrchWeekConceptPayload,
  type OrchWeekPayload,
  type OrchWeekTaskPayload,
} from "@/lib/api-modules";
import type {
  OrchDailyTask,
  OrchGoal,
  OrchPlanSummary,
  OrchWeek,
} from "@/lib/types-modules";
import { Card } from "@/components/ui/Card";
import { ModuleBadge } from "@/components/ui/Badge";
import { ConfirmModal, Modal } from "@/components/ui/Modal";
import { EmptyState, ErrorNote, PageSkeleton } from "@/components/ui/EmptyState";
import { GoalCard, GoalForm } from "@/components/pages/orchestration/GoalCard";
import { TodayCard } from "@/components/pages/orchestration/TodayCard";
import { WeeklyPlanCard } from "@/components/pages/orchestration/WeeklyPlanCard";
import { HabitCard } from "@/components/pages/orchestration/HabitCard";
import { useTaskLaunch } from "@/components/pages/orchestration/task-link";
import { STRINGS } from "./strings";

type LoadState = "loading" | "ok" | "error";

/** 与后端 _MAX_GOALS 一致：达到上限后隐藏「添加目标」入口。 */
const MAX_GOALS = 4;

type GoalPayload = {
  title: string;
  description: string;
  goal_type: string;
  subjects: string[];
  target_concept_ids: string[];
  deadline: number;
};

/** 目标设定/编辑成功后的 kickoff 载荷（周计划 + 首个任务 CTA）。 */
interface Kickoff {
  weeks: OrchWeek[];
  firstTask: OrchDailyTask | null;
}

export default function OrchestrationPage() {
  const { lang } = useUIStore();
  const tr = makePageT(lang, STRINGS);

  const [state, setState] = useState<LoadState>("loading");
  const [plan, setPlan] = useState<OrchPlanSummary | null>(null);
  const [today, setToday] = useState<OrchDailyTask[]>([]);

  const [submitting, setSubmitting] = useState(false);
  const [formFailed, setFormFailed] = useState(false);
  const [completingId, setCompletingId] = useState<string | null>(null);
  const [kickoff, setKickoff] = useState<Kickoff | null>(null);
  // W4/A12：kickoff CTA 走服务端 launch（绑定会话归因），失败回退纯文本深链。
  const { launch: kickoffLaunch, launchingId: kickoffLaunching } = useTaskLaunch(tr);
  // 多目标：新增与编辑分别记录打开状态 / 正在编辑的目标
  const [goalAddOpen, setGoalAddOpen] = useState(false);
  const [editingGoal, setEditingGoal] = useState<Partial<OrchGoal> | null>(null);
  const [deletingGoal, setDeletingGoal] = useState<Partial<OrchGoal> | null>(null);
  // 渲染期不取当前时间（react-hooks/purity）：随每次数据回源一起刷新。
  const [nowTs, setNowTs] = useState(0);

  // M9 读端点无 status 信封：空态靠空字段表达，永不 disabled。
  const fetchData = useCallback(() => {
    return Promise.all([getOrchPlan(), getOrchToday()])
      .then(([p, t]) => {
        setPlan(p);
        setToday(Array.isArray(t) ? t : []);
        setNowTs(Date.now() / 1000);
        setState("ok");
      })
      .catch(() => setState("error"));
  }, []);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  // 任务状态会随对话中的学习行为被后端自动推进：页面重新聚焦时回源。
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") void fetchData();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [fetchData]);

  const retry = useCallback(() => {
    setState("loading");
    void fetchData();
  }, [fetchData]);

  // 目标创建/编辑成功后的统一收尾：展示 kickoff（周计划 + 第一个任务 CTA），
  // 后端已立即生成周计划并物化今日任务。
  const afterGoalSaved = useCallback(
    async (r: { ok: boolean; weeks?: OrchWeek[]; first_task?: OrchDailyTask | null }) => {
      if (!r.ok) throw new Error("save goal failed");
      setKickoff({ weeks: r.weeks ?? [], firstTask: r.first_task ?? null });
      await fetchData();
    },
    [fetchData],
  );

  const handleSetGoal = useCallback(
    (payload: GoalPayload) => {
      setSubmitting(true);
      setFormFailed(false);
      setOrchGoal(payload)
        .then(afterGoalSaved)
        .then(() => setGoalAddOpen(false))
        .catch(() => setFormFailed(true))
        .finally(() => setSubmitting(false));
    },
    [afterGoalSaved, setGoalAddOpen],
  );

  const handlePatchGoal = useCallback(
    (payload: GoalPayload) => {
      if (!editingGoal) return;
      const goalId = editingGoal.id ?? "";
      setSubmitting(true);
      setFormFailed(false);
      patchOrchGoal(goalId, payload)
        .then(async (r) => {
          await afterGoalSaved(r);
          setEditingGoal(null);
        })
        .catch(() => setFormFailed(true))
        .finally(() => setSubmitting(false));
    },
    [afterGoalSaved, editingGoal, setEditingGoal],
  );

  const confirmDeleteGoal = useCallback(async () => {
    const goal = deletingGoal;
    if (!goal) return;
    setSubmitting(true);
    try {
      await deleteOrchGoal(goal.id ?? "");
      setKickoff(null);
      await fetchData();
      setDeletingGoal(null);
    } catch {
      setFormFailed(true);
    } finally {
      setSubmitting(false);
    }
  }, [deletingGoal, fetchData, setDeletingGoal, setFormFailed, setKickoff, setSubmitting]);

  const handleComplete = useCallback(
    (taskId: string) => {
      setCompletingId(taskId);
      // 乐观更新：先本地打勾，失败则整表回源。
      setToday((prev) => prev.map((t) => (t.id === taskId ? { ...t, status: "completed" } : t)));
      completeOrchTask(taskId)
        .then((r) => {
          if (!r.ok) void fetchData();
          else void getOrchPlan().then(setPlan).catch(() => {});
        })
        .catch(() => void fetchData())
        .finally(() => setCompletingId(null));
    },
    [fetchData],
  );

  const handleAddTask = useCallback(
    async (payload: OrchTaskCreatePayload) => {
      try {
        const r = await addOrchTask(payload);
        if (!r.ok) return false;
        await fetchData();
        return true;
      } catch {
        return false;
      }
    },
    [fetchData],
  );

  const handleUpdateTask = useCallback(
    async (taskId: string, patch: OrchTaskPatchPayload) => {
      try {
        const r = await updateOrchTask(taskId, patch);
        if (!r.ok) return false;
        await fetchData();
        return true;
      } catch {
        return false;
      }
    },
    [fetchData],
  );

  const handleDeleteTask = useCallback(
    async (taskId: string) => {
      try {
        const r = await deleteOrchTask(taskId);
        if (!r.ok) return false;
        await fetchData();
        return true;
      } catch {
        return false;
      }
    },
    [fetchData],
  );

  // 计划层级增删（长期任务/周/周内概念/周任务）的统一包装：成功后整表回源。
  const mutatePlan = useCallback(
    async (fn: () => Promise<{ ok: boolean }>) => {
      try {
        const r = await fn();
        if (!r.ok) return false;
        await fetchData();
        return true;
      } catch {
        return false;
      }
    },
    [fetchData],
  );

  const handleAddWeek = useCallback(
    (p: OrchWeekPayload) => mutatePlan(() => addOrchWeek(p)),
    [mutatePlan],
  );
  const handleDeleteWeek = useCallback(
    (i: number) => mutatePlan(() => deleteOrchWeek(i)),
    [mutatePlan],
  );
  const handleAddWeekConcept = useCallback(
    (i: number, p: OrchWeekConceptPayload) => mutatePlan(() => addOrchWeekConcept(i, p)),
    [mutatePlan],
  );
  const handleRemoveWeekConcept = useCallback(
    (i: number, key: string) => mutatePlan(() => removeOrchWeekConcept(i, key)),
    [mutatePlan],
  );
  const handleAddWeekTask = useCallback(
    (i: number, p: OrchWeekTaskPayload) => mutatePlan(() => addOrchWeekTask(i, p)),
    [mutatePlan],
  );
  const handleDeleteWeekTask = useCallback(
    (i: number, id: string) => mutatePlan(() => deleteOrchWeekTask(i, id)),
    [mutatePlan],
  );
  const handleAddSubtask = useCallback(
    (i: number, tid: string, p: OrchSubtaskPayload) => mutatePlan(() => addOrchSubtask(i, tid, p)),
    [mutatePlan],
  );
  const handleToggleSubtask = useCallback(
    (i: number, tid: string, sid: string) => mutatePlan(() => toggleOrchSubtask(i, tid, sid)),
    [mutatePlan],
  );
  const handleDeleteSubtask = useCallback(
    (i: number, tid: string, sid: string) => mutatePlan(() => deleteOrchSubtask(i, tid, sid)),
    [mutatePlan],
  );
  const handleSuggestSubtasks = useCallback(
    (i: number, tid: string) => mutatePlan(() => suggestOrchSubtasks(i, tid)),
    [mutatePlan],
  );

  const goals = useMemo(
    () => (plan?.goals ?? []).filter((g) => !!g.title),
    [plan],
  );
  const hasGoal = goals.length > 0;

  // 目标 ↔ 差距分析配对：优先 goal_id 精确匹配，旧数据按下标对齐。
  const statesByGoal = useMemo(() => {
    const states = plan?.goal_states ?? [];
    return goals.map(
      (g, i) =>
        (g.id ? states.find((s) => s.goal_id === g.id) : undefined) ??
        states[i] ??
        {},
    );
  }, [goals, plan]);

  // 周计划概念 → 阶段徽标（取该概念最近一次物化任务的 phase）。
  const phaseByConcept = useMemo(() => {
    const map: Record<string, string> = {};
    for (const t of plan?.daily_tasks ?? []) {
      if (t.concept_id && t.phase) map[t.concept_id] = t.phase;
    }
    return map;
  }, [plan]);

  // 概念选择器选项：计划载荷里已知概念的 id+name 并集（去重）。
  const conceptOptions = useMemo(() => {
    const seen = new Map<string, { id: string; name: string }>();
    for (const w of plan?.weekly_plan ?? []) {
      for (const c of w.concepts) {
        const key = c.concept_id || c.name;
        if (key && !seen.has(key)) seen.set(key, { id: c.concept_id, name: c.name });
      }
    }
    return [...seen.values()];
  }, [plan]);

  // 「本周复盘」深链：当前周 focus + 全计划完成统计。
  const weekReviewHref = useMemo(() => {
    const ws = plan?.weekly_plan ?? [];
    if (ws.length === 0 || nowTs === 0) return undefined;
    const cur =
      ws.find((w) => w.week_start > 0 && nowTs >= w.week_start && nowTs < w.week_start + 7 * 86400) ??
      ws[0];
    const all = plan?.daily_tasks ?? [];
    const doneCount = all.filter((t) => t.status === "completed").length;
    const msg = tr("week.review.msg")
      .replace("%f", cur.focus || tr("week.n").replace("%n", String(cur.week_index + 1)))
      .replace("%d", String(doneCount))
      .replace("%t", String(all.length));
    return `/chat?q=${encodeURIComponent(msg)}&send=1`;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan, nowTs, lang]);

  return (
    <div className="h-full overflow-y-auto p-6 page-in">
      <div className="mx-auto flex max-w-[1200px] flex-col gap-4">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <h1 className="font-serif text-xl font-semibold text-fg">{tr("page.title")}</h1>
              <ModuleBadge id="M9" />
            </div>
            <p className="mt-0.5 text-xs text-muted">{tr("page.desc")}</p>
          </div>
        </header>

        {state === "loading" ? (
          <PageSkeleton />
        ) : state === "error" ? (
          <Card>
            <ErrorNote message={tr("err.load")} retry={retry} />
          </Card>
        ) : !hasGoal ? (
          /* 无目标：整页引导设定长期目标 */
          <div className="mx-auto mt-6 w-full max-w-xl">
            <EmptyState icon={<Target size={22} />} title={tr("empty.title")} desc={tr("empty.desc")} />
            <Card className="mt-4">
              <p className="mb-0.5 text-sm font-semibold text-fg">{tr("form.title")}</p>
              <p className="mb-3 text-xs text-muted">{tr("form.desc")}</p>
              <GoalForm tr={tr} submitting={submitting} failed={formFailed} onSubmit={handleSetGoal} />
            </Card>
          </div>
        ) : (
          <>
            {plan!.needs_replan && (
              <Card className="border-warning/40 bg-warning/5">
                <div className="flex flex-wrap items-center gap-3">
                  <AlertTriangle size={16} className="shrink-0 text-warning" />
                  <p className="min-w-0 flex-1 text-xs text-fg-secondary">{tr("replan.banner")}</p>
                  {/* 旧「重新规划」按钮对用户是死按钮（确定性重算后页面无可见
                      变化）；真正能改变局面的是教练对话——深链带上下文直达。 */}
                  <Link
                    href={`/chat?q=${encodeURIComponent(tr("replan.msg"))}&send=1`}
                    className="inline-flex items-center gap-1.5 rounded-[8px] border border-border bg-surface px-2.5 py-1 text-xs font-medium text-fg-secondary transition-colors hover:border-accent hover:text-accent"
                  >
                    <MessagesSquare size={12} />
                    {tr("replan.action")}
                  </Link>
                </div>
              </Card>
            )}

            {kickoff && (
              <Card className={kickoff.weeks.length
                ? "border-accent/40 bg-accent-soft/30"
                : "border-warning/40 bg-warning/5"}>
                <div className="flex flex-wrap items-center gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold text-fg">
                      {kickoff.weeks.length ? tr("kickoff.title") : tr("kickoff.title.empty")}
                    </p>
                    <p className="mt-0.5 text-xs text-muted">
                      {kickoff.weeks.length
                        ? tr("kickoff.desc").replace("%n", String(kickoff.weeks.length))
                        : tr("kickoff.desc.empty")}
                    </p>
                  </div>
                  {kickoff.firstTask && (() => {
                    const firstTask = kickoff.firstTask;
                    const launching = kickoffLaunching === firstTask.id;
                    return (
                      <button
                        onClick={() => void kickoffLaunch(firstTask)}
                        disabled={launching}
                        className={cn(
                          "inline-flex h-8.5 cursor-pointer items-center gap-2 rounded-[8px] bg-accent px-3.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-accent-strong",
                          launching && "cursor-wait opacity-70",
                        )}
                      >
                        <Play size={14} />
                        {launching ? tr("today.launching") : tr("kickoff.cta")}
                      </button>
                    );
                  })()}
                  <button onClick={() => setKickoff(null)} aria-label={tr("kickoff.dismiss")}
                    className="cursor-pointer rounded-[6px] p-1 text-muted transition-colors hover:bg-surface-hover hover:text-fg">
                    <X size={14} />
                  </button>
                </div>
              </Card>
            )}

            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              <div className="flex min-w-0 flex-col gap-4">
                {goals.map((g, i) => (
                  <GoalCard key={g.id ?? i} goal={g} gs={statesByGoal[i]} tr={tr}
                    onEdit={() => { setFormFailed(false); setEditingGoal(g); }}
                    onDelete={() => { setFormFailed(false); setDeletingGoal(g); }}
                  />
                ))}
                {goals.length < MAX_GOALS && (
                  <button
                    type="button"
                    onClick={() => { setFormFailed(false); setGoalAddOpen(true); }}
                    className="flex cursor-pointer items-center justify-center gap-1.5 rounded-[10px] border border-dashed border-border py-2.5 text-xs text-muted transition-colors hover:border-accent hover:text-accent"
                  >
                    <Plus size={13} />
                    {tr("goal.add")}
                  </button>
                )}
              </div>
              <TodayCard
                tasks={today}
                pendingCount={plan!.pending_today ?? today.filter((t) => t.status === "pending").length}
                tr={tr}
                completingId={completingId}
                onComplete={handleComplete}
                onAdd={handleAddTask}
                onUpdate={handleUpdateTask}
                onDelete={handleDeleteTask}
              />
            </div>
            <HabitCard habit={plan!.habit ?? {}} tr={tr} />
            <WeeklyPlanCard
              weeks={plan!.weekly_plan ?? []}
              phaseByConcept={phaseByConcept}
              reviewHref={weekReviewHref}
              conceptOptions={conceptOptions}
              nowTs={nowTs}
              onAddWeek={handleAddWeek}
              onDeleteWeek={handleDeleteWeek}
              onAddConcept={handleAddWeekConcept}
              onRemoveConcept={handleRemoveWeekConcept}
              onAddWeekTask={handleAddWeekTask}
              onDeleteWeekTask={handleDeleteWeekTask}
              onAddSubtask={handleAddSubtask}
              onToggleSubtask={handleToggleSubtask}
              onDeleteSubtask={handleDeleteSubtask}
              onSuggestSubtasks={handleSuggestSubtasks}
              tr={tr}
            />
          </>
        )}
      </div>

      {/* 多目标：新增目标（未达上限时）与按 id 编辑各一个弹窗 */}
      <Modal open={goalAddOpen} onClose={() => setGoalAddOpen(false)} title={tr("goal.add.title")} width={460}>
        <GoalForm
          tr={tr}
          submitting={submitting}
          failed={formFailed}
          submitLabel={tr("form.submit")}
          onSubmit={handleSetGoal}
        />
      </Modal>
      <Modal open={!!editingGoal} onClose={() => setEditingGoal(null)} title={tr("goal.edit.title")} width={460}>
        {editingGoal && (
          <GoalForm
            tr={tr}
            submitting={submitting}
            failed={formFailed}
            initial={editingGoal}
            submitLabel={tr("form.update")}
            onSubmit={handlePatchGoal}
          />
        )}
      </Modal>
      <ConfirmModal
        open={!!deletingGoal}
        onClose={() => setDeletingGoal(null)}
        onConfirm={() => void confirmDeleteGoal()}
        title={tr("goal.del.confirm.title")}
        desc={tr("goal.del.confirm.desc").replace("%t", deletingGoal?.title ?? "")}
        confirmText={tr("goal.del")}
        cancelText={tr("common.cancel")}
      />
    </div>
  );
}
