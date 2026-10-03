"use client";

// 领域写动作的变更预览卡（B03 前端收口）。
// 展示字段 before/after、影响说明、可逆性与有效期；review_required
// 必须在此确认后才执行（approve 只授予许可，不等于已执行）。
import { useEffect, useState } from "react";
import { AlertCircle, Check, ShieldCheck, Undo2 } from "lucide-react";
import { cn } from "@/lib/cn";
import { useAssistantStore } from "@/lib/assistant/store";
import { stringsFor } from "../strings";
import type { ActionPreview, AssistantAction } from "@/lib/assistant/types.generated";

function PreviewChanges({ preview }: { preview: ActionPreview }) {
  const lang = useAssistantStore((s) => s.lang);
  const changes = preview.changes ?? [];
  if (!changes.length) return null;
  return (
    <div className="assistant-preview-section">
      {(changes).map((c) => (
        <div key={c.field} className="assistant-preview-change">
          <span className="assistant-preview-field">{c.label}</span>
          <span className="assistant-preview-values">
            {c.before ? (
              <span className="assistant-preview-before">{c.before}</span>
            ) : null}
            <span className="assistant-preview-after">
              {c.after ?? stringsFor(lang).previewEmpty}
            </span>
          </span>
        </div>
      ))}
    </div>
  );
}

export function ActionPreviewCard({ action, preview }: {
  action: AssistantAction;
  preview: ActionPreview;
}) {
  const lang = useAssistantStore((s) => s.lang);
  const confirmPreview = useAssistantStore((s) => s.confirmActionPreview);
  const runAction = useAssistantStore((s) => s.runAction);
  const busy = useAssistantStore((s) => !!s.previewBusy[action.action_id]);
  const t = stringsFor(lang);
  const reviewRequired = preview.approval === "review_required";
  // 过期态由定时器翻转（渲染期不读时钟，保持纯函数）。
  const [expired, setExpired] = useState(false);
  const expiresMs = preview.expires_at
    ? new Date(preview.expires_at).getTime() : 0;
  useEffect(() => {
    if (!expiresMs) return;
    const timer = window.setTimeout(
      () => setExpired(Date.now() >= expiresMs),
      Math.max(0, expiresMs - Date.now()));
    return () => window.clearTimeout(timer);
  }, [expiresMs]);

  return (
    <div className="assistant-preview-card" data-review={reviewRequired}>
      <p className="assistant-preview-summary">{preview.summary}</p>
      <PreviewChanges preview={preview} />
      {(preview.side_effects ?? []).length > 0 && (
        <ul className="assistant-preview-effects">
          {(preview.side_effects ?? []).map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
      )}
      <div className="assistant-preview-meta">
        {preview.reversible
          ? <span><Undo2 size={12} aria-hidden />{t.previewReversible}</span>
          : <span><ShieldCheck size={12} aria-hidden />{t.previewNotReversible}</span>}
        <span className={cn(expired && "assistant-preview-expired")}>
          {expired ? t.previewExpired : t.previewTtl}
        </span>
      </div>
      {expired && (
        <p className="assistant-preview-error">
          <AlertCircle size={12} aria-hidden />
          {t.previewStaleHint}
        </p>
      )}
      <div className="assistant-preview-actions">
        <button
          type="button"
          className="assistant-btn-primary"
          disabled={busy || expired || action.state !== "proposed"}
          onClick={() => {
            // review_required：先取审批许可再执行；intent_sufficient：确认即执行。
            if (reviewRequired) void confirmPreview(action, "approve");
            else void runAction(action);
          }}
        >
          {busy ? t.previewWorking : t.previewConfirm}
          <Check size={14} aria-hidden />
        </button>
        {reviewRequired && (
          <button
            type="button"
            className="assistant-btn-outline"
            disabled={busy}
            onClick={() => { void confirmPreview(action, "reject"); }}
          >
            {t.previewReject}
          </button>
        )}
      </div>
    </div>
  );
}
