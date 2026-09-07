"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { CalendarRange, Check, ChevronDown, ChevronRight, MessagesSquare, Network, Plus, Sparkles, Trash2, X } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { ConfirmModal, Modal } from "@/components/ui/Modal";
import { Pager, paged } from "@/components/ui/Pager";
import { cn } from "@/lib/cn";
import { fmtDate } from "@/lib/format";
import type { OrchWeek, OrchWeekTask } from "@/lib/types-modules";
import type { OrchSubtaskPayload, OrchWeekConceptPayload, OrchWeekPayload, OrchWeekTaskPayload } from "@/lib/api-modules";
import { ConceptMultiPick, type ConceptOption } from "./concept-pick";

type Tr = (key: string, fallback?: string) => string;

/** 「添加一周」弹窗：本周重点 + 概念多选。 */
function WeekFormModal({
  open,
  options,
  failed,
  submitting,
  tr,
  onClose,
  onSubmit,
}: {
  open: boolean;
  options: ConceptOption[];
  failed: boolean;
  submitting: boolean;
  tr: Tr;
  onClose: () => void;
  onSubmit: (p: OrchWeekPayload) => void;
}) {
  const [focus, setFocus] = useState("");
  const [concepts, setConcepts] = useState<ConceptOption[]>([]);
  const close = () => {
    setFocus("");
    setConcepts([]);
    onClose();
  };
  return (
    <Modal open={open} onClose={close} title={tr("weekform.title")}>
      <div className="space-y-3">
        <div>
          <p className="mb-1 text-xs font-medium text-fg-secondary">{tr("weekform.focus")}</p>
          <input
            value={focus}
            onChange={(e) => setFocus(e.target.value)}
            maxLength={60}
            placeholder={tr("weekform.focus.ph")}
            className="h-8.5 w-full rounded-[8px] border border-border bg-surface px-2.5 text-xs text-fg outline-none placeholder:text-muted focus:border-accent"
          />
        </div>
        <div>
          <p className="mb-1 text-xs font-medium text-fg-secondary">{tr("weekform.concepts")}</p>
          <ConceptMultiPick options={options} selected={concepts} onChange={setConcepts} tr={tr} />
        </div>
        {failed && <p className="text-xs text-danger">{tr("weekform.failed")}</p>}
        <div className="flex justify-end">
          <Button
            size="sm"
            disabled={submitting || (!focus.trim() && concepts.length === 0)}
            onClick={() =>
              onSubmit({
                focus: focus.trim(),
                concepts: concepts.map((c) => ({ concept_id: c.id, name: c.name })),
              })
            }
          >
            {tr("weekform.submit")}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

/** 「添加概念」小弹窗：单个概念（下拉或自由文本）。 */
function ConceptFormModal({
  open,
  options,
  failed,
  submitting,
  tr,
  onClose,
  onSubmit,
}: {
  open: boolean;
  options: ConceptOption[];
  failed: boolean;
  submitting: boolean;
  tr: Tr;
  onClose: () => void;
  onSubmit: (p: OrchWeekConceptPayload) => void;
}) {
  const [picked, setPicked] = useState("");
  const [free, setFree] = useState("");
  const close = () => {
    setPicked("");
    setFree("");
    onClose();
  };
  const chosen = options.find((o) => (o.id || o.name) === picked) ?? null;
  const payload: OrchWeekConceptPayload | null = chosen
    ? { concept_id: chosen.id, name: chosen.name }
    : free.trim()
      ? { name: free.trim() }
      : null;
  return (
    <Modal open={open} onClose={close} title={tr("week.concept.add")}>
      <div className="space-y-3">
        {options.length > 0 && (
          <select
            value={picked}
            onChange={(e) => { setPicked(e.target.value); setFree(""); }}
            className="h-8.5 w-full cursor-pointer rounded-[8px] border border-border bg-surface px-2 text-xs text-fg-secondary outline-none focus:border-accent"
          >
            <option value="">{tr("concept.pick")}</option>
            {options.map((o) => (
              <option key={o.id || o.name} value={o.id || o.name}>{o.name}</option>
            ))}
          </select>
        )}
        <input
          value={free}
          onChange={(e) => { setFree(e.target.value); setPicked(""); }}
          placeholder={tr("taskform.concept.ph")}
          className="h-8.5 w-full rounded-[8px] border border-border bg-surface px-2.5 text-xs text-fg outline-none placeholder:text-muted focus:border-accent"
        />
        {failed && <p className="text-xs text-danger">{tr("weekform.failed")}</p>}
        <div className="flex justify-end">
          <Button size="sm" disabled={!payload || submitting}
            onClick={() => payload && onSubmit(payload)}>
            {tr("weekform.submit")}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

/** 单个周任务行：标题 + 来源徽标 + 可展开子任务（勾选/增删/✨推荐）。 */
function WeekTaskRow({
  task,
  expanded,
  tr,
  suggesting,
  suggestFailed,
  onToggleExpand,
  onDeleteTask,
  onToggleSubtask,
  onDeleteSubtask,
  onAddSubtask,
  onSuggest,
}: {
  task: OrchWeekTask;
  expanded: boolean;
  tr: Tr;
  suggesting: boolean;
  suggestFailed: boolean;
  onToggleExpand: () => void;
  onDeleteTask: () => void;
  onToggleSubtask: (subtaskId: string) => void;
  onDeleteSubtask: (subtaskId: string) => void;
  onAddSubtask: (p: OrchSubtaskPayload) => Promise<boolean>;
  onSuggest: () => void;
}) {
  const [subTitle, setSubTitle] = useState("");
  const [adding, setAdding] = useState(false);
  const doneCount = task.subtasks.filter((s) => s.done).length;
  const submitSub = () => {
    const t = subTitle.trim();
    if (!t || adding) return;
    setAdding(true);
    void onAddSubtask({ title: t }).then((ok) => { if (ok) setSubTitle(""); })
      .finally(() => setAdding(false));
  };
  return (
    <div className={cn(
      "rounded-[10px] border px-3 py-2",
      task.done ? "border-border-light bg-surface/50" : "border-border-light bg-surface",
    )}>
      <div className="flex items-center gap-2">
        <button type="button" onClick={onToggleExpand}
          className="shrink-0 cursor-pointer p-0.5 text-muted transition-colors hover:text-fg"
          aria-label="expand">
          {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        </button>
        <span className={cn(
          "min-w-0 flex-1 text-[0.8rem] font-medium leading-snug",
          task.done ? "text-muted line-through" : "text-fg",
        )}>
          {task.title}
        </span>
        {task.subtasks.length > 0 && (
          <span className="tnum shrink-0 text-[0.66rem] text-muted">
            {doneCount}/{task.subtasks.length}
          </span>
        )}
        <Badge tone="muted">{tr(`today.kind.${task.kind}`, task.kind)}</Badge>
        {task.source === "user" && <Badge tone="outline">{tr("week.task.custom")}</Badge>}
        <button
          type="button"
          onClick={onSuggest}
          disabled={suggesting}
          title={tr("week.sub.suggest")}
          aria-label={tr("week.sub.suggest")}
          className={cn(
            "flex shrink-0 cursor-pointer items-center gap-1 rounded-full border border-accent/40 px-2 py-0.5 text-[0.66rem] text-accent-strong transition-colors hover:bg-accent-soft disabled:opacity-50",
            suggesting && "animate-pulse",
          )}
        >
          <Sparkles size={11} />
          {suggesting ? tr("lt.suggesting") : tr("week.sub.suggest")}
        </button>
        <button type="button" onClick={onDeleteTask}
          className="shrink-0 cursor-pointer p-0.5 text-muted transition-colors hover:text-danger"
          aria-label={tr("today.delete")}>
          <Trash2 size={12} />
        </button>
      </div>
      {expanded && (
        <div className="mt-2 border-t border-border-light pt-2">
          {suggestFailed && (
            <p className="mb-1.5 text-[0.68rem] text-warning">{tr("week.sub.suggest.failed")}</p>
          )}
          {task.subtasks.length === 0 ? (
            <p className="py-1 text-[0.7rem] text-muted">{tr("week.sub.empty")}</p>
          ) : (
            <ul className="space-y-1">
              {task.subtasks.map((s) => (
                <li key={s.id} className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => onToggleSubtask(s.id)}
                    aria-label="toggle"
                    className={cn(
                      "flex h-4 w-4 shrink-0 cursor-pointer items-center justify-center rounded-[5px] border transition-colors",
                      s.done ? "border-success bg-success text-white" : "border-border hover:border-accent",
                    )}
                  >
                    {s.done && <Check size={11} strokeWidth={3} />}
                  </button>
                  <span className={cn(
                    "min-w-0 flex-1 text-[0.74rem] leading-snug",
                    s.done ? "text-muted line-through" : "text-fg-secondary",
                  )}>
                    {s.title}
                  </span>
                  <span className="tnum shrink-0 text-[0.64rem] text-muted">{s.estimate_minutes}{tr("week.sub.min")}</span>
                  <button type="button" onClick={() => onDeleteSubtask(s.id)}
                    className="shrink-0 cursor-pointer text-muted transition-colors hover:text-danger"
                    aria-label={tr("today.delete")}>
                    <X size={11} />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="mt-1.5 flex gap-1.5">
            <input
              value={subTitle}
              maxLength={80}
              onChange={(e) => setSubTitle(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); submitSub(); } }}
              placeholder={tr("week.sub.add.ph")}
              className="h-7 min-w-0 flex-1 rounded-[7px] border border-border bg-surface px-2 text-[0.7rem] text-fg outline-none placeholder:text-muted focus:border-accent"
            />
            <Button size="sm" variant="outline" icon={<Plus size={11} />}
              disabled={!subTitle.trim() || adding} onClick={submitSub}>
              {tr("week.sub.add")}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

/** 周计划卡：周 tabs + 概念胶囊 + 行动级周任务（子任务可勾选/增删/✨推荐）。
 * 支持整周增删与周内概念增删；已生成的每日任务不受影响。
 * reviewHref：「本周复盘」auto-send 深链（消息由父组件拼好本周 focus + 完成统计）。 */
export function WeeklyPlanCard({
  weeks,
  phaseByConcept,
  reviewHref,
  conceptOptions = [],
  nowTs = 0,
  onAddWeek,
  onDeleteWeek,
  onAddConcept,
  onRemoveConcept,
  onAddWeekTask,
  onDeleteWeekTask,
  onAddSubtask,
  onToggleSubtask,
  onDeleteSubtask,
  onSuggestSubtasks,
  tr,
}: {
  weeks: OrchWeek[];
  phaseByConcept?: Record<string, string>;
  reviewHref?: string;
  conceptOptions?: ConceptOption[];
  /** 父组件回源时刷新的当前时间戳（render 不取 Date.now）。 */
  nowTs?: number;
  onAddWeek?: (p: OrchWeekPayload) => Promise<boolean>;
  onDeleteWeek?: (weekIndex: number) => Promise<boolean>;
  onAddConcept?: (weekIndex: number, p: OrchWeekConceptPayload) => Promise<boolean>;
  onRemoveConcept?: (weekIndex: number, conceptKey: string) => Promise<boolean>;
  onAddWeekTask?: (weekIndex: number, p: OrchWeekTaskPayload) => Promise<boolean>;
  onDeleteWeekTask?: (weekIndex: number, taskId: string) => Promise<boolean>;
  onAddSubtask?: (weekIndex: number, taskId: string, p: OrchSubtaskPayload) => Promise<boolean>;
  onToggleSubtask?: (weekIndex: number, taskId: string, subtaskId: string) => Promise<boolean>;
  onDeleteSubtask?: (weekIndex: number, taskId: string, subtaskId: string) => Promise<boolean>;
  onSuggestSubtasks?: (weekIndex: number, taskId: string) => Promise<boolean>;
  tr: Tr;
}) {
  const [addOpen, setAddOpen] = useState(false);
  const [conceptWeek, setConceptWeek] = useState<number | null>(null);
  const [deleteWeek, setDeleteWeek] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [failed, setFailed] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [taskPage, setTaskPage] = useState(0);
  const [newTaskTitle, setNewTaskTitle] = useState("");
  const [addingTask, setAddingTask] = useState(false);
  const [suggestingId, setSuggestingId] = useState<string | null>(null);
  const [suggestFailedId, setSuggestFailedId] = useState<string | null>(null);
  const newTaskRef = useRef<HTMLInputElement>(null);

  // 本周索引：由父组件回源时刷新的 nowTs 纯计算（render 不取当前时间）。
  const currentIdx = (() => {
    if (!nowTs) return 0;
    const i = weeks.findIndex((w) => w.week_start <= nowTs && nowTs < w.week_start + 7 * 86400);
    return i >= 0 ? i : 0;
  })();
  const [selected, setSelected] = useState<number | null>(null);
  const sel = selected !== null && selected < weeks.length ? selected : currentIdx;
  const week = weeks[sel] ?? null;

  const wrap = (fn: () => Promise<boolean>, close: () => void) => {
    setSubmitting(true);
    setFailed(false);
    void fn()
      .then((ok) => (ok ? close() : setFailed(true)))
      .finally(() => setSubmitting(false));
  };

  const submitWeekTask = () => {
    const t = newTaskTitle.trim();
    if (!t || !week || !onAddWeekTask || addingTask) return;
    setAddingTask(true);
    void onAddWeekTask(week.week_index, { title: t })
      .then((ok) => { if (ok) setNewTaskTitle(""); })
      .finally(() => setAddingTask(false));
  };

  const suggest = (taskId: string) => {
    if (!week || !onSuggestSubtasks || suggestingId) return;
    setSuggestingId(taskId);
    setSuggestFailedId(null);
    void onSuggestSubtasks(week.week_index, taskId)
      .then((ok) => {
        if (ok) setExpandedId(taskId);
        else setSuggestFailedId(taskId);
      })
      .finally(() => setSuggestingId(null));
  };

  return (
    <Card>
      <CardHeader
        icon={<CalendarRange size={16} />}
        title={tr("week.title")}
        desc={tr("week.desc")}
        right={
          <span className="flex items-center gap-2">
            {reviewHref && (
              <Link
                href={reviewHref}
                className="inline-flex items-center gap-1.5 rounded-[8px] border border-border bg-surface px-2.5 py-1 text-xs font-medium text-fg-secondary transition-colors hover:border-accent hover:text-accent"
              >
                <MessagesSquare size={12} />
                {tr("week.review")}
              </Link>
            )}
            {onAddWeek && (
              <Button size="sm" variant="outline" icon={<Plus size={13} />}
                onClick={() => { setFailed(false); setAddOpen(true); }}>
                {tr("week.add")}
              </Button>
            )}
          </span>
        }
      />
      {weeks.length === 0 ? (
        <p className="py-2 text-xs text-muted">{tr("week.empty")}</p>
      ) : (
        <div>
          {/* 周 tabs：本周高亮 */}
          <div className="mb-3 flex flex-wrap gap-1.5">
            {weeks.map((w, i) => (
              <button
                key={w.week_index}
                type="button"
                onClick={() => { setSelected(i); setTaskPage(0); }}
                className={cn(
                  "cursor-pointer rounded-full border px-2.5 py-1 text-[0.7rem] transition-colors",
                  i === sel
                    ? "border-accent bg-accent-soft font-medium text-accent-strong"
                    : "border-border text-fg-secondary hover:border-accent/50",
                )}
              >
                {tr("week.n").replace("%n", String(w.week_index + 1))}
                {i === currentIdx && <span className="ml-1 text-accent">●</span>}
              </button>
            ))}
          </div>

          {week && (
            <div>
              <div className="mb-1.5 flex items-center gap-2">
                {week.week_start > 0 && (
                  <span className="tnum text-[0.66rem] text-muted">{fmtDate(week.week_start)}</span>
                )}
                {week.focus && <span className="min-w-0 flex-1 truncate text-[0.72rem] font-medium text-fg-secondary">{week.focus}</span>}
                {week.origin === "user" && <Badge tone="outline">{tr("week.task.custom")}</Badge>}
                {onAddConcept && (
                  <button
                    type="button"
                    onClick={() => { setFailed(false); setConceptWeek(week.week_index); }}
                    className="inline-flex shrink-0 cursor-pointer items-center gap-0.5 text-[0.66rem] text-muted transition-colors hover:text-accent"
                  >
                    <Plus size={11} />
                    {tr("week.concept.add")}
                  </button>
                )}
                {onDeleteWeek && (
                  <button
                    type="button"
                    onClick={() => setDeleteWeek(week.week_index)}
                    className="shrink-0 cursor-pointer text-muted transition-colors hover:text-danger"
                    aria-label={tr("today.delete")}
                  >
                    <Trash2 size={13} />
                  </button>
                )}
              </div>

              {week.concepts.length > 0 && (
                <div className="mb-2.5 flex flex-wrap gap-1.5">
                  {week.concepts.map((c) => {
                    const phase = phaseByConcept?.[c.concept_id];
                    return (
                      <span
                        key={c.concept_id || c.name}
                        title={`${tr("goal.progress")}: ${Math.round(c.planned_mastery * 100)}%`}
                        className="flex items-center gap-1.5 rounded-[7px] border border-border-light bg-bg px-2 py-1 text-[0.7rem] text-fg-secondary"
                      >
                        <span className="flex gap-0.5">
                          {[1, 2, 3, 4, 5].map((d) => (
                            <span
                              key={d}
                              className="h-1 w-1 rounded-full"
                              style={{ background: d <= c.difficulty ? "rgb(var(--accent))" : "rgb(var(--border))" }}
                            />
                          ))}
                        </span>
                        {c.name}
                        {c.concept_id && (
                          <a
                            href={`/knowledge?concept=${encodeURIComponent(c.concept_id)}`}
                            title={tr("goal.gap.locate", "在谱系中查看")}
                            className="text-muted/70 transition-colors hover:text-accent"
                            aria-label={tr("goal.gap.locate", "在谱系中查看")}
                          >
                            <Network size={10} />
                          </a>
                        )}
                        {phase && (
                          <Badge tone="info" className="px-1.5 py-0 text-[9px]">
                            {tr(`today.phase.${phase}`, phase)}
                          </Badge>
                        )}
                        {onRemoveConcept && (
                          <button
                            type="button"
                            onClick={() => void onRemoveConcept(week.week_index, c.concept_id || c.name)}
                            className="cursor-pointer text-muted transition-colors hover:text-danger"
                            aria-label={tr("week.concept.remove")}
                          >
                            <X size={10} />
                          </button>
                        )}
                      </span>
                    );
                  })}
                </div>
              )}

              {/* 行动级周任务（分页 5/页） */}
              {week.tasks.length === 0 ? (
                <div className="flex flex-wrap items-center gap-1.5 py-1.5">
                  <p className="text-[0.72rem] text-muted">{tr("week.task.empty")}</p>
                  {onAddWeekTask && (
                    <button
                      type="button"
                      onClick={() => newTaskRef.current?.focus()}
                      className="inline-flex cursor-pointer items-center gap-0.5 text-[0.72rem] font-medium text-accent transition-colors hover:text-accent-strong"
                    >
                      <Plus size={11} />
                      {tr("week.task.add")}
                    </button>
                  )}
                </div>
              ) : (
                <div className="space-y-1.5">
                  {paged(week.tasks, taskPage).map((t) => (
                    <WeekTaskRow
                      key={t.id}
                      task={t}
                      expanded={expandedId === t.id}
                      tr={tr}
                      suggesting={suggestingId === t.id}
                      suggestFailed={suggestFailedId === t.id}
                      onToggleExpand={() => setExpandedId(expandedId === t.id ? null : t.id)}
                      onDeleteTask={() => week && void onDeleteWeekTask?.(week.week_index, t.id)}
                      onToggleSubtask={(sid) => void onToggleSubtask?.(week.week_index, t.id, sid)}
                      onDeleteSubtask={(sid) => void onDeleteSubtask?.(week.week_index, t.id, sid)}
                      onAddSubtask={(p) => onAddSubtask ? onAddSubtask(week.week_index, t.id, p) : Promise.resolve(false)}
                      onSuggest={() => suggest(t.id)}
                    />
                  ))}
                </div>
              )}
              <Pager page={taskPage} total={week.tasks.length} onPage={setTaskPage} />

              {onAddWeekTask && (
                <div className="mt-2 flex gap-1.5">
                  <input
                    ref={newTaskRef}
                    value={newTaskTitle}
                    maxLength={80}
                    onChange={(e) => setNewTaskTitle(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); submitWeekTask(); } }}
                    placeholder={tr("week.task.add.ph")}
                    className="h-7.5 min-w-0 flex-1 rounded-[8px] border border-border bg-surface px-2 text-[0.72rem] text-fg outline-none placeholder:text-muted focus:border-accent"
                  />
                  <Button size="sm" variant="outline" icon={<Plus size={12} />}
                    disabled={!newTaskTitle.trim() || addingTask} onClick={submitWeekTask}>
                    {tr("week.task.add")}
                  </Button>
                </div>
              )}
            </div>
          )}
        </div>
      )}
      {onAddWeek && (
        <WeekFormModal
          open={addOpen}
          options={conceptOptions}
          failed={failed}
          submitting={submitting}
          tr={tr}
          onClose={() => setAddOpen(false)}
          onSubmit={(p) => wrap(() => onAddWeek(p), () => setAddOpen(false))}
        />
      )}
      {onAddConcept && conceptWeek !== null && (
        <ConceptFormModal
          open
          options={conceptOptions}
          failed={failed}
          submitting={submitting}
          tr={tr}
          onClose={() => setConceptWeek(null)}
          onSubmit={(p) => wrap(() => onAddConcept(conceptWeek, p), () => setConceptWeek(null))}
        />
      )}
      {onDeleteWeek && deleteWeek !== null && (
        <ConfirmModal
          open
          onClose={() => setDeleteWeek(null)}
          onConfirm={() => {
            const idx = deleteWeek;
            void onDeleteWeek(idx).then(() => setDeleteWeek(null));
          }}
          title={tr("week.delete.confirm.title")}
          desc={tr("week.delete.confirm.desc").replace("%n", String(deleteWeek + 1))}
          confirmText={tr("today.delete")}
          cancelText={tr("common.cancel")}
        />
      )}
    </Card>
  );
}
