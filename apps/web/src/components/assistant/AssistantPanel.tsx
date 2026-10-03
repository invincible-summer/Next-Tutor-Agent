"use client";

// 助手面板（A06）：标准 420 × min(640, dvh-48)，
// 放大 min(720, vw-48) × min(780, dvh-48)；右下角与按钮同锚点；
// 非模态 dialog（aria-modal=false），打开聚焦输入，收起归还焦点。
import { useEffect, useId, useRef } from "react";
import { History, Inbox, ListChecks, Maximize2, Minimize2, X } from "lucide-react";
import { cn } from "@/lib/cn";
import { useAssistantStore } from "@/lib/assistant/store";
import {
  ASSISTANT_LAYER,
  isTopmost,
  registerOverlay,
} from "@/lib/assistant/overlay";
import { stringsFor } from "./strings";
import { AssistantComposer } from "./AssistantComposer";
import { AssistantVoiceControls } from "./AssistantVoiceControls";
import { AssistantMessages } from "./AssistantMessages";
import { AssistantHistory } from "./AssistantHistory";
import { AssistantTaskCenter } from "./AssistantTaskCenter";
import { AssistantInbox } from "./AssistantInbox";
import { SourceList } from "./cards/SourceList";

export function AssistantPanel({
  launcherRef,
}: { launcherRef?: React.RefObject<HTMLButtonElement | null> }) {
  const mode = useAssistantStore((s) => s.mode);
  const view = useAssistantStore((s) => s.view);
  const lang = useAssistantStore((s) => s.lang);
  const scopeMode = useAssistantStore((s) => s.scopeMode);
  const scopeLabel = useAssistantStore((s) => s.scopeLabel);
  const setMode = useAssistantStore((s) => s.setMode);
  const collapse = useAssistantStore((s) => s.collapse);
  const setView = useAssistantStore((s) => s.setView);
  const inboxUnread = useAssistantStore((s) => s.inboxUnread);
  const refreshInboxUnread = useAssistantStore((s) => s.refreshInboxUnread);
  const t = stringsFor(lang);

  const dialogRef = useRef<HTMLDivElement>(null);
  const expanded = mode === "expanded";
  const overlayId = useId();

  // 覆盖层协作（§5.5）：面板打开时注册；Escape 仅在最上层时收起，
  // 高层 Modal/Drawer 打开时不抢键（AC-30）。
  useEffect(() => {
    if (mode === "collapsed") return;
    const unregister = registerOverlay({
      id: overlayId, kind: "assistant-panel", layer: ASSISTANT_LAYER,
      onClose: collapse,
    });
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isTopmost(overlayId)
          && view === "conversation") {
        collapse();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      unregister();
    };
  }, [mode, view, overlayId, collapse]);

  useEffect(() => {
    if (mode !== "collapsed") {
      const input = dialogRef.current?.querySelector<HTMLTextAreaElement>(
        "textarea");
      input?.focus();
    } else {
      launcherRef?.current?.focus();
    }
  }, [mode, view, launcherRef]);

  // 打开面板时刷新收件箱未读计数（§25.5 柔和未读点；失败静默）。
  useEffect(() => {
    if (mode !== "collapsed") void refreshInboxUnread();
  }, [mode, view, refreshInboxUnread]);

  if (mode === "collapsed") return null;

  const scopeText = scopeMode === "all_workspaces"
    ? t.scopeAllWorkspaces
    : scopeMode === "workspace"
      ? `${t.scopeWorkspace}${t.scopeSeparator}${scopeLabel ?? ""}`
      : t.scopeFollowPage;

  return (
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal={false}
      aria-label={t.panelTitle}
      data-mode={mode}
      className={cn("assistant-panel", "motion-pop", expanded && "assistant-panel-expanded")}
    >
      <header className="assistant-panel-head">
        <span className="assistant-panel-title">{t.panelTitle}</span>
        <div className="assistant-panel-actions">
          <button
            type="button"
            className="assistant-icon-btn"
            aria-label={t.inboxButton}
            title={t.inboxButton}
            data-active={view === "inbox"}
            onClick={() => setView(view === "inbox" ? "conversation" : "inbox")}
          >
            <Inbox size={16} aria-hidden />
            {inboxUnread > 0 && view !== "inbox" && (
              <span className="assistant-inbox-dot" aria-hidden
                    title={t.inboxUnreadDot} />
            )}
          </button>
          <button
            type="button"
            className="assistant-icon-btn"
            aria-label={t.tasksButton}
            title={t.tasksButton}
            data-active={view === "tasks"}
            onClick={() => setView(view === "tasks" ? "conversation" : "tasks")}
          >
            <ListChecks size={16} aria-hidden />
          </button>
          <button
            type="button"
            className="assistant-icon-btn"
            aria-label={t.historyButton}
            title={t.historyButton}
            data-active={view === "history"}
            onClick={() => setView(view === "history" ? "conversation" : "history")}
          >
            <History size={16} aria-hidden />
          </button>
          <button
            type="button"
            className="assistant-icon-btn"
            aria-label={expanded ? t.shrinkButton : t.expandButton}
            title={expanded ? t.shrinkButton : t.expandButton}
            onClick={() => setMode(expanded ? "standard" : "expanded")}
          >
            {expanded
              ? <Minimize2 size={16} aria-hidden />
              : <Maximize2 size={16} aria-hidden />}
          </button>
          <button
            type="button"
            className="assistant-icon-btn"
            aria-label={t.collapseButton}
            title={t.collapseButton}
            onClick={collapse}
          >
            <X size={16} aria-hidden />
          </button>
        </div>
      </header>
      <div className="assistant-scope-row" title={scopeText}>
        <span className="assistant-scope-text">{scopeText}</span>
      </div>
      <div className="assistant-panel-body">
        {view === "history"
          ? <AssistantHistory />
          : view === "tasks"
            ? <AssistantTaskCenter />
            : view === "inbox"
              ? <AssistantInbox />
              : <AssistantMessages />}
      </div>
      {view === "conversation" && (
        <>
          <SourceStrip />
          <AssistantComposer />
          <AssistantVoiceControls />
        </>
      )}
    </div>
  );
}

function SourceStrip() {
  // 最近一条消息的来源入口。
  const messages = useAssistantStore((s) => s.messages);
  const last = [...messages].reverse().find((m) => m.role === "assistant"
    && m.sources?.length);
  if (!last) return null;
  return <SourceList sources={last.sources ?? []} />;
}
