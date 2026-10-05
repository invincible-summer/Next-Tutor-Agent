"use client";

// 动作卡（A06 视觉 / A10 execute-ack 接线 / B03 预览审批 / B05 撤销入口）。
// 每卡一个主按钮；状态徽标不只靠颜色区分。automatic 在面板收起/
// 标签页隐藏/epoch 变化时由 store 降级为本地点击执行。
// domain_write 动作：先「查看变更」加载预览；预览在场时主按钮让位给
// ActionPreviewCard 的确认/取消（每卡仅一个实心主按钮，§3.4-7）。
// succeeded 且带回执撤销快照（§21.5）时给「撤销」次按钮；已撤销显示回执。
import { AlertCircle, ArrowRight, Check, Loader2, Undo2, X } from "lucide-react";
import { cn } from "@/lib/cn";
import { useAssistantStore } from "@/lib/assistant/store";
import { stringsFor } from "../strings";
import { ActionPreviewCard } from "./ActionPreviewCard";
import type { AssistantAction } from "@next-tutor/contracts/assistant";

const STATE_LABEL_ZH: Record<string, string> = {
  proposed: "待执行", executing: "正在执行", awaiting_ack: "正在打开",
  succeeded: "已完成", failed: "失败", cancelled: "已取消",
  expired: "已过期", needs_attention: "需处理",
};
const STATE_LABEL_EN: Record<string, string> = {
  proposed: "Pending", executing: "Executing", awaiting_ack: "Opening",
  succeeded: "Done", failed: "Failed", cancelled: "Cancelled",
  expired: "Expired", needs_attention: "Needs attention",
};

export function actionLabel(action: AssistantAction, lang: "zh" | "en"): string {
  const table = lang === "zh" ? STATE_LABEL_ZH : STATE_LABEL_EN;
  const state = action.state ?? "proposed";
  return table[state] ?? state;
}

export function ActionCard({ action }: { action: AssistantAction }) {
  const lang = useAssistantStore((s) => s.lang);
  const runAction = useAssistantStore((s) => s.runAction);
  const undoAction = useAssistantStore((s) => s.undoAction);
  const loadPreview = useAssistantStore((s) => s.loadActionPreview);
  const setView = useAssistantStore((s) => s.setView);
  const preview = useAssistantStore(
    (s) => s.actionPreviews[action.action_id]);
  const busy = useAssistantStore((s) => !!s.actionBusy[action.action_id]);
  const previewBusy = useAssistantStore(
    (s) => !!s.previewBusy[action.action_id]);
  const previewError = useAssistantStore(
    (s) => s.previewError[action.action_id]);
  const downgraded = useAssistantStore(
    (s) => !!s.downgradedActions[action.action_id]);
  const t = stringsFor(lang);
  const state = action.state ?? "proposed";
  const manual = downgraded || (action.execution ?? "user_click") === "user_click";
  const isDomainWrite = action.payload?.kind === "domain_write";
  // §23.5 start_workflow：execute 只创建 draft；成功后到办理事项里批准。
  const isStartWorkflow = action.payload?.kind === "start_workflow";
  const workflowCreated = state === "succeeded"
    && action.business_result?.kind === "workflow";
  // 预览在场时主按钮隐藏，确认动作由预览卡承载。
  const previewShown = isDomainWrite && Boolean(preview)
    && state === "proposed";
  const needsPreviewFirst = isDomainWrite && !preview
    && state === "proposed";
  const clickable = !busy && !previewShown
    && ((state === "proposed" && manual)
        || state === "failed"
        || state === "needs_attention");
  // §21.5 撤销：已完成、带回执快照、未撤销过且不在撤销中。
  const undone = Boolean(action.undo_result);
  const undoable = state === "succeeded" && !undone
    && Boolean(action.business_result?.undo);

  return (
    <div className={cn("assistant-card", "assistant-action-card")}
      data-state={state}>
      <div className="assistant-action-head">
        <span className="assistant-action-label">{action.label}</span>
        <span className="assistant-action-state">
          {state === "succeeded" && <Check size={13} aria-hidden />}
          {(state === "executing" || state === "awaiting_ack" || busy) &&
            <Loader2 size={13} className="animate-spin" aria-hidden />}
          {state === "failed" && <AlertCircle size={13} aria-hidden />}
          {actionLabel(action, lang)}
        </span>
      </div>
      {previewError && state === "proposed" && (
        <p className="assistant-preview-error" role="alert">
          <AlertCircle size={12} aria-hidden />
          {previewError}
        </p>
      )}
      {workflowCreated && (
        <button
          type="button"
          className="assistant-btn-primary"
          onClick={() => setView("tasks")}
        >
          {t.actionOpenTasks}
          <ArrowRight size={14} aria-hidden />
        </button>
      )}
      {!previewShown && !workflowCreated && (
        <button
          type="button"
          className="assistant-btn-primary"
          disabled={!clickable}
          onClick={() => {
            if (needsPreviewFirst) void loadPreview(action);
            else void runAction(action);
          }}
        >
          {previewBusy && needsPreviewFirst
            ? t.previewLoading
            : (isStartWorkflow
                ? t.actionCreateWorkflow
                : (needsPreviewFirst ? t.previewViewChanges : t.actionPrimary))}
          <ArrowRight size={14} aria-hidden />
        </button>
      )}
      {/* domain_write 预览：变更字段、影响与确认（§21.4） */}
      {previewShown && preview && (
        <ActionPreviewCard action={action} preview={preview} />
      )}
      {/* §21.5 撤销：真实补偿入口；窗口/并发冲突由服务端判定。 */}
      {undoable && (
        <button
          type="button"
          className="assistant-btn-outline"
          disabled={busy}
          onClick={() => { void undoAction(action); }}
        >
          {busy ? t.actionUndoBusy : t.actionUndo}
          <Undo2 size={14} aria-hidden />
        </button>
      )}
      {undone && (
        <p className="assistant-msg-meta">
          <Undo2 size={12} aria-hidden />
          {t.actionUndone}
          {action.undo_result?.message
            ? ` · ${action.undo_result.message}` : ""}
        </p>
      )}
      {state === "cancelled" && (
        <span className="assistant-msg-meta"><X size={12} aria-hidden /></span>
      )}
    </div>
  );
}
