"use client";

// 办理事项视图（C03）：工作流列表按「进行中 / 等待我
// 处理 / 已完成」分组；每项展示目标、当前步骤、已完成步数与一个主
// 操作；进度只用「n/m 步骤」，不制造匀速进度条。展开项内提供计划
// 预览（将创建/修改对象）、批准、启动、取消、失败步骤重试与结果入口。
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ChevronDown, ChevronLeft, ListChecks, RefreshCw,
} from "lucide-react";
import { useAssistantStore } from "@/lib/assistant/store";
import { Pager } from "@/components/ui/Pager";
import { resolveTargetUrl } from "@/lib/assistant/routes";
import {
  approveAssistantWorkflow,
  cancelAssistantWorkflow,
  getAssistantWorkflow,
  resumeAssistantWorkflow,
  retryAssistantWorkflowStep,
  startAssistantWorkflow,
  type AssistantWorkflowView,
  type WorkflowPlanPreview,
} from "@/lib/assistant/api";
import type { NavigationTarget } from "@next-tutor/contracts/assistant";
import { stringsFor } from "./strings";

const RUNNING_STATES = new Set(["queued", "running", "waiting_domain_job"]);
const WAITING_STATES = new Set([
  "draft", "awaiting_approval", "paused_for_user", "interrupted",
]);
const DONE_STATES = new Set([
  "succeeded", "partially_succeeded", "failed", "cancelled",
]);
const POLL_MS = 5000;
const PAGE_SIZE = 20;

function doneSteps(wf: AssistantWorkflowView): number {
  return wf.steps.filter((s) => s.state === "succeeded").length;
}

function currentStep(wf: AssistantWorkflowView): string {
  const active = wf.steps.find((s) =>
    s.state === "running" || s.state === "waiting");
  if (active) return active.title;
  const failed = [...wf.steps].reverse().find((s) => s.state === "failed");
  if (failed) return failed.title;
  const next = wf.steps.find((s) =>
    s.state === "pending" || s.state === "ready");
  return next ? next.title : wf.steps[wf.steps.length - 1]?.title ?? "";
}

export function AssistantTaskCenter() {
  const lang = useAssistantStore((s) => s.lang);
  const setView = useAssistantStore((s) => s.setView);
  const items = useAssistantStore((s) => s.tasks);
  const total = useAssistantStore((s) => s.tasksTotal);
  const page = useAssistantStore((s) => s.tasksPage);
  const loading = useAssistantStore((s) => s.tasksLoading);
  const error = useAssistantStore((s) => s.tasksError);
  const refreshTasks = useAssistantStore((s) => s.refreshTasks);
  const router = useRouter();
  const t = stringsFor(lang);

  const [expanded, setExpanded] = useState<string | null>(null);
  const [preview, setPreview] = useState<WorkflowPlanPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    void refreshTasks(0);
  }, [refreshTasks]);

  // 有活跃事项时轻轮询（§23.6 收起不丢任务）；视图关闭即停。
  useEffect(() => {
    const hasActive = items.some((w) =>
      RUNNING_STATES.has(w.state) || WAITING_STATES.has(w.state));
    if (!hasActive) return;
    const timer = setInterval(() => void refreshTasks(page), POLL_MS);
    return () => clearInterval(timer);
  }, [items, page, refreshTasks]);

  // 展开项加载完整步骤与计划预览（只读；过期预览按 workflow_id 过滤）。
  useEffect(() => {
    if (!expanded) return;
    let cancelled = false;
    void getAssistantWorkflow(expanded).then((detail) => {
      if (!cancelled) setPreview(detail.preview);
    }).catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [expanded, busy]);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      await refreshTasks(page);
    } catch (err) {
      setActionError(String((err as Error)?.message ?? err));
    } finally {
      setBusy(false);
    }
  };

  const groups: Array<[string, AssistantWorkflowView[]]> = [
    [t.tasksGroupRunning, items.filter((w) => RUNNING_STATES.has(w.state))],
    [t.tasksGroupWaiting, items.filter((w) => WAITING_STATES.has(w.state))],
    [t.tasksGroupDone, items.filter((w) => DONE_STATES.has(w.state))],
  ];

  const openTarget = (target: Record<string, unknown>) => {
    const url = resolveTargetUrl(target as unknown as NavigationTarget);
    if (url) router.push(url);
  };

  return (
    <div className="assistant-history">
      <div className="assistant-history-head">
        <button
          type="button"
          className="assistant-icon-btn"
          aria-label={t.tasksBack}
          onClick={() => setView("conversation")}
        >
          <ChevronLeft size={16} aria-hidden />
        </button>
        <span className="assistant-history-title">{t.tasksTitle}</span>
        <button
          type="button"
          className="assistant-icon-btn"
          aria-label={t.taskRefresh}
          title={t.taskRefresh}
          onClick={() => void refreshTasks(page)}
        >
          <RefreshCw size={15} aria-hidden />
        </button>
      </div>
      <div className="assistant-history-list">
        {loading && items.length === 0 && (
          <div className="assistant-history-empty">…</div>
        )}
        {!loading && items.length === 0 && (
          <div className="assistant-history-empty">{t.tasksEmpty}</div>
        )}
        {(error || actionError) && (
          <div className="assistant-task-error">{error ?? actionError}</div>
        )}
        {groups.map(([label, group]) => group.length > 0 && (
          <div key={label} className="assistant-task-group">
            <div className="assistant-task-group-title">{label}</div>
            {group.map((wf) => (
              <div key={wf.workflow_id} className="assistant-task-item">
                <button
                  type="button"
                  className="assistant-task-open"
                  onClick={() => setExpanded(
                    expanded === wf.workflow_id ? null : wf.workflow_id)}
                >
                  <span className="assistant-task-name">
                    {wf.objective || wf.template}
                  </span>
                  <span className="assistant-task-meta">
                    {t.taskSteps(doneSteps(wf), wf.steps.length)}
                    {" · "}
                    {t.taskStateMap[wf.state] ?? wf.state}
                    {" · "}
                    {t.taskStartedAt}
                    {String(wf.created_at ?? "").slice(0, 10)}
                  </span>
                  <span className="assistant-task-step">
                    {t.taskCurrentStep}：{currentStep(wf)}
                  </span>
                  <ChevronDown
                    size={14}
                    aria-hidden
                    className={expanded === wf.workflow_id
                      ? "assistant-task-chevron assistant-task-chevron-open"
                      : "assistant-task-chevron"}
                  />
                </button>

                {expanded === wf.workflow_id && (
                  <TaskDetail
                    wf={wf}
                    preview={preview
                      && preview.workflow_id === wf.workflow_id
                      ? preview : null}
                    busy={busy}
                    t={t}
                    onApprove={() => {
                      if (!preview
                          || preview.workflow_id !== wf.workflow_id) return;
                      void run(() => approveAssistantWorkflow(
                        wf.workflow_id, {
                          expected_revision: preview.revision,
                          plan_hash: preview.plan_hash,
                          approved_step_ids: preview.steps.map(
                            (s) => s.step_id),
                        }));
                    }}
                    onStart={() => void run(() => startAssistantWorkflow(
                      wf.workflow_id, {
                        client_request_id: crypto.randomUUID(),
                        expected_revision: wf.revision,
                      }))}
                    onCancel={() => void run(() =>
                      cancelAssistantWorkflow(wf.workflow_id))}
                    onResume={() => void run(() => resumeAssistantWorkflow(
                      wf.workflow_id, { expected_revision: wf.revision }))}
                    onRetry={(stepId) => void run(() =>
                      retryAssistantWorkflowStep(wf.workflow_id, stepId, {
                        expected_revision: wf.revision,
                        client_request_id: crypto.randomUUID(),
                      }))}
                    onOpenTarget={openTarget}
                  />
                )}
              </div>
            ))}
          </div>
        ))}
      </div>
      {total > PAGE_SIZE && (
        <Pager
          page={page}
          total={total}
          per={PAGE_SIZE}
          onPage={(p) => void refreshTasks(p)}
        />
      )}
    </div>
  );
}

function TaskDetail({
  wf, preview, busy, t, onApprove, onStart, onCancel, onResume,
  onRetry, onOpenTarget,
}: {
  wf: AssistantWorkflowView;
  preview: WorkflowPlanPreview | null;
  busy: boolean;
  t: ReturnType<typeof stringsFor>;
  onApprove: () => void;
  onStart: () => void;
  onCancel: () => void;
  onResume: () => void;
  onRetry: (stepId: string) => void;
  onOpenTarget: (target: Record<string, unknown>) => void;
}) {
  return (
    <div className="assistant-task-detail">
      <ol className="assistant-task-steps">
        {wf.steps.map((s) => (
          <li key={s.step_id}
              className="assistant-task-step-row"
              data-state={s.state}>
            <span className="assistant-task-step-title">{s.title}</span>
            <span className="assistant-task-step-state">
              {t.taskStepStateMap[s.state] ?? s.state}
            </span>
            {s.state === "failed" && s.error && (
              <span className="assistant-task-step-error">
                {t.taskErrorHint}{s.error.message}
              </span>
            )}
            {s.state === "failed" && (
              <button
                type="button"
                className="assistant-task-mini-btn"
                disabled={busy}
                onClick={() => onRetry(s.step_id)}
              >
                {t.taskRetryStep}
              </button>
            )}
          </li>
        ))}
      </ol>

      {preview && (
        <div className="assistant-task-plan">
          <div className="assistant-task-plan-title">
            {t.taskPlanWillChange}
          </div>
          <ul>
            {preview.plan.will_create_or_modify.map((c) => (
              <li key={c.step_id}>
                {preview.steps.find((s) => s.step_id === c.step_id)?.title
                  ?? c.step_id}
                {"（"}{c.operation}{"）"}
              </li>
            ))}
            {preview.plan.will_create_or_modify.length === 0 && (
              <li>{t.tasksEmpty}</li>
            )}
          </ul>
          {preview.plan.user_involvement.length > 0 && (
            <div className="assistant-task-plan-title">
              {t.taskPlanUserInvolvement}
              {preview.plan.user_involvement.map((u) => u.title).join("、")}
            </div>
          )}
        </div>
      )}

      <div className="assistant-task-actions">
        {(wf.state === "draft" || wf.state === "awaiting_approval") && (
          <>
            <button
              type="button"
              className="assistant-btn-primary"
              disabled={busy || !preview}
              onClick={onApprove}
            >
              {t.taskApprove}
            </button>
            {wf.state === "awaiting_approval" && (
              <button
                type="button"
                className="assistant-btn-outline"
                disabled={busy}
                onClick={onStart}
              >
                {t.taskStart}
              </button>
            )}
          </>
        )}
        {RUNNING_STATES.has(wf.state) && (
          <button
            type="button"
            className="assistant-btn-outline"
            disabled={busy}
            onClick={onCancel}
          >
            {t.taskCancel}
          </button>
        )}
        {(wf.state === "paused_for_user" || wf.state === "interrupted"
          || wf.state === "waiting_domain_job") && (
          <button
            type="button"
            className="assistant-btn-primary"
            disabled={busy}
            onClick={onResume}
          >
            {t.taskResume}
          </button>
        )}
        {(wf.state === "succeeded" || wf.state === "partially_succeeded")
          && (wf.result_targets?.length ?? 0) > 0 && (
          <button
            type="button"
            className="assistant-btn-primary"
            onClick={() => {
              const target = wf.result_targets?.[0];
              if (target) onOpenTarget(target);
            }}
          >
            <ListChecks size={14} aria-hidden />
            {t.taskViewResult}
          </button>
        )}
      </div>
    </div>
  );
}
