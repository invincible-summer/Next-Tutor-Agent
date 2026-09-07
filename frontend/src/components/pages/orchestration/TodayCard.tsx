"use client";

import { useState } from "react";
import {
  BookOpen, Check, CheckCircle2, ListChecks, ListTodo, Network, Pencil, PencilLine, Plus,
  RotateCcw, Trash2, Undo2,
} from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { ConfirmModal, Modal } from "@/components/ui/Modal";
import { Pager, paged } from "@/components/ui/Pager";
import { cn } from "@/lib/cn";
import type { OrchTaskCreatePayload, OrchTaskPatchPayload } from "@/lib/api-modules";
import type { OrchCapacity, OrchDailyTask } from "@/lib/types-modules";
import { taskDisplayName, taskGoLabel, useTaskLaunch } from "./task-link";

type Tr = (key: string, fallback?: string) => string;

const KIND_ICON = {
  study: BookOpen,
  review: RotateCcw,
  practice: PencilLine,
  summary: ListChecks,
} as const;

const KINDS = ["study", "review", "practice", "summary"] as const;
const PHASES = ["foundation", "reinforce", "sprint"] as const;

const INPUT =
  "w-full rounded-[8px] border border-border bg-surface px-2.5 py-1.5 text-xs text-fg outline-none transition-colors focus:border-accent";

/** 本地时区的 YYYY-MM-DD（在事件回调里调用，保持渲染纯净）。 */
function todayStr(): string {
  return new Date().toLocaleDateString("en-CA");
}

type TaskModalState = { mode: "add"; day: string } | { mode: "edit"; task: OrchDailyTask } | null;

/** 添加 / 编辑任务弹窗。编辑态不暴露概念字段（PATCH 契约不含 concept_name）。 */
function TaskFormModal({
  modal,
  tr,
  failed,
  onClose,
  onSubmit,
}: {
  modal: NonNullable<TaskModalState>;
  tr: Tr;
  failed: boolean;
  onClose: () => void;
  onSubmit: (payload: OrchTaskCreatePayload | { id: string; patch: OrchTaskPatchPayload }) => void;
}) {
  const editing = modal.mode === "edit" ? modal.task : null;
  const [day, setDay] = useState(modal.mode === "add" ? modal.day : modal.task.day);
  const [title, setTitle] = useState(editing?.title ?? "");
  const [conceptName, setConceptName] = useState("");
  const [kind, setKind] = useState(editing?.kind ?? "study");
  const [phase, setPhase] = useState(editing?.phase ?? "");
  const [estimate, setEstimate] = useState(editing?.estimate_minutes ?? 30);
  const [priority, setPriority] = useState(editing?.priority ?? 3);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (editing) {
      onSubmit({
        id: editing.id,
        patch: {
          title: title.trim(),
          day,
          kind,
          phase,
          estimate_minutes: estimate,
          priority,
        },
      });
      return;
    }
    if (!title.trim() && !conceptName.trim()) return;
    onSubmit({
      day,
      title: title.trim(),
      concept_name: conceptName.trim() || undefined,
      kind,
      phase: phase || undefined,
      estimate_minutes: estimate,
      priority,
    });
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={editing ? tr("taskform.title.edit") : tr("taskform.title.add")}
      width={440}
    >
      <form onSubmit={submit} className="space-y-3">
        <div className="grid grid-cols-2 gap-2.5">
          <label className="block">
            <span className="mb-1 block text-[0.7rem] text-muted">{tr("taskform.day")}</span>
            <input type="date" className={cn(INPUT, "cursor-pointer")} value={day} required
              onChange={(e) => setDay(e.target.value)} />
          </label>
          <label className="block">
            <span className="mb-1 block text-[0.7rem] text-muted">{tr("taskform.estimate")}</span>
            <input type="number" min={5} max={240} step={5} className={INPUT} value={estimate}
              onChange={(e) => setEstimate(Number(e.target.value) || 0)} />
          </label>
        </div>
        <label className="block">
          <span className="mb-1 block text-[0.7rem] text-muted">{tr("taskform.name")}</span>
          <input className={INPUT} value={title} maxLength={60} placeholder={tr("taskform.name.ph")}
            onChange={(e) => setTitle(e.target.value)} />
        </label>
        {!editing && (
          <label className="block">
            <span className="mb-1 block text-[0.7rem] text-muted">{tr("taskform.concept")}</span>
            <input className={INPUT} value={conceptName} maxLength={60}
              placeholder={tr("taskform.concept.ph")}
              onChange={(e) => setConceptName(e.target.value)} />
          </label>
        )}
        <div className="grid grid-cols-3 gap-2.5">
          <label className="block">
            <span className="mb-1 block text-[0.7rem] text-muted">{tr("taskform.kind")}</span>
            <select className={cn(INPUT, "cursor-pointer")} value={kind}
              onChange={(e) => setKind(e.target.value)}>
              {KINDS.map((k) => (
                <option key={k} value={k}>{tr(`today.kind.${k}`)}</option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="mb-1 block text-[0.7rem] text-muted">{tr("taskform.phase")}</span>
            <select className={cn(INPUT, "cursor-pointer")} value={phase}
              onChange={(e) => setPhase(e.target.value)}>
              <option value="">{tr("taskform.phase.none")}</option>
              {PHASES.map((p) => (
                <option key={p} value={p}>{tr(`today.phase.${p}`)}</option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="mb-1 block text-[0.7rem] text-muted">{tr("taskform.priority")}</span>
            <select className={cn(INPUT, "cursor-pointer")} value={priority}
              onChange={(e) => setPriority(Number(e.target.value))}>
              {[1, 2, 3, 4, 5].map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
          </label>
        </div>
        <div className="flex items-center justify-end gap-2 pt-1">
          {failed && <span className="mr-auto text-[0.7rem] text-danger">{tr("taskform.failed")}</span>}
          <Button type="button" variant="ghost" size="sm" onClick={onClose}>
            {tr("common.cancel")}
          </Button>
          <Button size="sm">{tr("taskform.submit")}</Button>
        </div>
      </form>
    </Modal>
  );
}

function TaskRow({
  task,
  tr,
  completing,
  launching,
  carryover,
  onComplete,
  onLaunch,
  onEdit,
  onDelete,
  onReset,
}: {
  task: OrchDailyTask;
  tr: Tr;
  completing: boolean;
  launching: boolean;
  carryover: boolean;
  onComplete: (id: string) => void;
  onLaunch: (task: OrchDailyTask) => void;
  onEdit: (task: OrchDailyTask) => void;
  onDelete: (task: OrchDailyTask) => void;
  onReset: (id: string) => void;
}) {
  const done = task.status === "completed";
  const Icon = KIND_ICON[task.kind as keyof typeof KIND_ICON] ?? BookOpen;
  const name = taskDisplayName(task, tr);
  const resettable = task.status === "completed" || task.status === "skipped";
  return (
    <div className={cn("flex items-start gap-2.5 py-2", done && "opacity-55")}>
      <button
        onClick={() => !done && onComplete(task.id)}
        disabled={done || completing}
        aria-label="complete task"
        className={cn(
          "mt-0.5 flex h-5 w-5 shrink-0 cursor-pointer items-center justify-center rounded-[6px] border transition-colors",
          done
            ? "border-success bg-success text-white"
            : "border-border hover:border-accent hover:bg-accent-soft",
        )}
      >
        {done && <Check size={12} />}
      </button>
      <Icon size={13} className="mt-1 shrink-0 text-accent" />
      <div className="min-w-0 flex-1">
        <p className={cn("truncate text-[0.8rem] font-medium text-fg", done && "line-through")}>{name}</p>
        <p className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[0.66rem] text-muted">
          <Badge tone={task.kind === "review" ? "warning" : "muted"}>
            {tr(`today.kind.${task.kind}`, task.kind)}
          </Badge>
          {task.phase && (
            <Badge tone="info">{tr(`today.phase.${task.phase}`, task.phase)}</Badge>
          )}
          {task.custom && <Badge tone="outline">{tr("today.custom")}</Badge>}
          {carryover && <Badge tone="danger">{tr("today.carryover")}</Badge>}
          {done && task.completion_source === "self_report" && (
            <Badge tone="outline">{tr("today.selfreport")}</Badge>
          )}
          <span className="tnum">{task.estimate_minutes} {tr("today.min")}</span>
        </p>
        {task.reason && (
          <p className="mt-0.5 text-[0.66rem] leading-relaxed text-accent-strong/80">{task.reason}</p>
        )}
        {task.concept_id && (
          <a
            href={`/knowledge?concept=${encodeURIComponent(task.concept_id)}`}
            title={tr("goal.gap.locate", "在谱系中查看")}
            className="mt-0.5 inline-flex items-center gap-1 text-[0.66rem] text-muted transition-colors hover:text-accent"
          >
            <Network size={10} />
            {tr("goal.gap.locate", "在谱系中查看")}
          </a>
        )}
      </div>
      <span className="flex shrink-0 items-center gap-1">
        {!done && (
          <button
            onClick={() => onLaunch(task)}
            disabled={launching}
            className={cn(
              "text-[0.7rem] text-accent-strong hover:underline",
              launching && "cursor-wait opacity-60",
            )}
          >
            {launching ? tr("today.launching") : taskGoLabel(task, tr)}
          </button>
        )}
        {resettable ? (
          <button onClick={() => onReset(task.id)} title={tr("today.reset")} aria-label={tr("today.reset")}
            className="cursor-pointer rounded-[6px] p-1 text-muted transition-colors hover:bg-surface-hover hover:text-fg">
            <Undo2 size={12} />
          </button>
        ) : (
          <button onClick={() => onEdit(task)} title={tr("today.edit")} aria-label={tr("today.edit")}
            className="cursor-pointer rounded-[6px] p-1 text-muted transition-colors hover:bg-surface-hover hover:text-fg">
            <Pencil size={12} />
          </button>
        )}
        <button onClick={() => onDelete(task)} title={tr("today.delete")} aria-label={tr("today.delete")}
          className="cursor-pointer rounded-[6px] p-1 text-muted transition-colors hover:bg-danger/10 hover:text-danger">
          <Trash2 size={12} />
        </button>
      </span>
    </div>
  );
}

/** 今日任务卡：「学习计划 / 间隔复习」双子栏 + 结转区置顶 + 分页（5/页）
 * + 全交互（完成/行动/编辑/删除/重置/添加）。 */
export function TodayCard({
  tasks,
  pendingCount,
  tr,
  capacity,
  completingId,
  onComplete,
  onAdd,
  onUpdate,
  onDelete,
}: {
  tasks: OrchDailyTask[];
  pendingCount: number;
  tr: Tr;
  /** W4 容量可行性（/plan 附载；null/undefined = 无数据，不展示）。 */
  capacity?: OrchCapacity | null;
  completingId: string | null;
  onComplete: (id: string) => void;
  onAdd: (payload: OrchTaskCreatePayload) => Promise<boolean>;
  onUpdate: (id: string, patch: OrchTaskPatchPayload) => Promise<boolean>;
  onDelete: (id: string) => Promise<boolean>;
}) {
  const [modal, setModal] = useState<TaskModalState>(null);
  const [formFailed, setFormFailed] = useState(false);
  const [deleting, setDeleting] = useState<OrchDailyTask | null>(null);
  const [tab, setTab] = useState<"plan" | "review">("plan");
  const [page, setPage] = useState(0);
  // W4/A12：行动按钮走服务端 launch（绑定会话归因），失败回退纯文本深链。
  const { launch, launchingId } = useTaskLaunch(tr);

  // 间隔复习子栏：SRS 到期的 review 任务；其余归学习计划。
  const reviewTasks = tasks.filter((t) => t.kind === "review");
  const planTasks = tasks.filter((t) => t.kind !== "review");
  const reviewPending = reviewTasks.filter((t) => t.status !== "completed").length;
  const inTab = tab === "review" ? reviewTasks : planTasks;

  // 昨日结转区：后端把 overdue 且 day<today 的任务置顶返回。
  const carryover = inTab.filter((t) => t.status === "overdue");
  const rest = inTab.filter((t) => t.status !== "overdue");
  const open = rest.filter((t) => t.status !== "completed");
  const done = rest.filter((t) => t.status === "completed");
  const ordered = [...carryover, ...open, ...done];

  // W4 容量可行性：今日超载 advisory。渲染纯净——day 取自任务行自身
  //（无任务的天不可能超载，不引入渲染期时钟）。
  const todayLoad = (() => {
    if (!capacity?.days?.length) return null;
    const days = new Set(tasks.map((t) => t.day).filter(Boolean));
    const entry = capacity.days.find((d) => days.has(d.day));
    return entry?.overload ? entry : null;
  })();

  const submitModal = async (payload: OrchTaskCreatePayload | { id: string; patch: OrchTaskPatchPayload }) => {
    const ok =
      "id" in payload ? await onUpdate(payload.id, payload.patch) : await onAdd(payload);
    if (ok) {
      setFormFailed(false);
      setModal(null);
    } else {
      setFormFailed(true);
    }
  };

  const confirmDelete = async () => {
    if (!deleting) return;
    const ok = await onDelete(deleting.id);
    if (ok) setDeleting(null);
  };

  const tabBtn = (id: "plan" | "review", label: string, count: number) => (
    <button
      type="button"
      onClick={() => { setTab(id); setPage(0); }}
      className={cn(
        "cursor-pointer rounded-full border px-2.5 py-0.5 text-[0.68rem] transition-colors",
        tab === id
          ? "border-accent bg-accent-soft font-medium text-accent-strong"
          : "border-border text-muted hover:border-accent/50 hover:text-fg-secondary",
      )}
    >
      {label}
      {count > 0 && <span className="tnum ml-1">{count}</span>}
    </button>
  );

  return (
    <Card>
      <CardHeader
        icon={<ListTodo size={16} />}
        title={tr("today.title")}
        desc={tr("today.desc")}
        right={
          <span className="flex items-center gap-2">
            {pendingCount > 0 && <Badge tone="accent">{pendingCount} {tr("today.pending")}</Badge>}
            <Button size="sm" variant="outline" icon={<Plus size={12} />}
              onClick={() => { setFormFailed(false); setModal({ mode: "add", day: todayStr() }); }}>
              {tr("today.add")}
            </Button>
          </span>
        }
      />
      <div className="mb-2 flex gap-1.5">
        {tabBtn("plan", tr("today.tab.plan"), planTasks.filter((t) => t.status !== "completed").length)}
        {tabBtn("review", tr("today.tab.review"), reviewPending)}
      </div>
      {todayLoad && (
        <p className="mb-2 rounded-[8px] border border-warning/40 bg-warning/5 px-3 py-1.5 text-[0.7rem] text-warning">
          {tr("today.overload", "今日共 %p 分钟，超出计划的 %d 分钟")
            .replace("%p", String(todayLoad.planned_minutes))
            .replace("%d", String(capacity?.daily_minutes ?? ""))}
        </p>
      )}
      {ordered.length === 0 ? (
        <p className="py-3 text-center text-xs text-muted">
          {tab === "review" ? tr("today.review.empty") : tr("today.empty")}
        </p>
      ) : (
        <>
          {open.length === 0 && carryover.length === 0 && (
            <p className="mb-2 flex items-center gap-1.5 rounded-[8px] bg-success/10 px-3 py-2 text-[0.72rem] text-success">
              <CheckCircle2 size={13} /> {tr("today.done.all")}
            </p>
          )}
          <div className="divide-y divide-border-light">
            {paged(ordered, page).map((t) => (
              <TaskRow
                key={t.id}
                task={t}
                tr={tr}
                completing={completingId === t.id}
                launching={launchingId === t.id}
                carryover={t.status === "overdue"}
                onComplete={onComplete}
                onLaunch={(task) => void launch(task)}
                onEdit={(task) => { setFormFailed(false); setModal({ mode: "edit", task }); }}
                onDelete={setDeleting}
                onReset={(id) => void onUpdate(id, { status: "pending" })}
              />
            ))}
          </div>
          <Pager page={page} total={ordered.length} onPage={setPage} />
        </>
      )}
      {modal && (
        <TaskFormModal
          key={modal.mode === "edit" ? modal.task.id : `add-${modal.day}`}
          modal={modal}
          tr={tr}
          failed={formFailed}
          onClose={() => setModal(null)}
          onSubmit={(p) => void submitModal(p)}
        />
      )}
      <ConfirmModal
        open={!!deleting}
        onClose={() => setDeleting(null)}
        onConfirm={() => void confirmDelete()}
        title={tr("today.delete.confirm.title")}
        desc={tr("today.delete.confirm.desc").replace("%t", deleting ? taskDisplayName(deleting, tr) : "")}
        confirmText={tr("today.delete")}
        cancelText={tr("common.cancel")}
      />
    </Card>
  );
}
