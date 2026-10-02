"use client";
import { memo, useState } from "react";
import { Check, Copy, FileText, RefreshCw, RotateCw } from "lucide-react";
import { cn } from "@/lib/cn";
import type { ChatMessage as ChatMessageType, RetryState } from "@/lib/types";
import { useUIStore } from "@/lib/store";
import { t } from "@/lib/i18n";
import { Markdown, StreamingMarkdown } from "./markdown";
import { ThinkingBlock } from "./ThinkingBlock";
import { ToolCallCard, ActiveToolCard } from "./ToolCallCard";

/* ---- 附件卡片：文件类型图标 + 文件名 + 字数 ---- */
function AttachmentCard({ filename, charCount }: { filename: string; charCount?: number }) {
  const { lang } = useUIStore();
  return (
    <span className="flex items-center gap-2 rounded-[8px] border border-border bg-surface px-2.5 py-1.5 shadow-sm">
      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-[5px] bg-accent-soft text-accent-strong">
        <FileText size={12} />
      </span>
      <span className="min-w-0">
        <span className="block max-w-40 truncate text-[0.7rem] font-medium text-fg-secondary">{filename}</span>
        {charCount ? (
          <span className="tnum block text-[0.62rem] text-muted/70">
            {charCount} {t(lang, "unit.chars")}
          </span>
        ) : null}
      </span>
    </span>
  );
}

/* ---- Hover actions for assistant messages ---- */
function AssistantActions({ msg, onRegenerate, disabled }: { msg: ChatMessageType; onRegenerate?: () => void; disabled?: boolean }) {
  const { lang } = useUIStore();
  const tr = (k: string, fb?: string) => t(lang, k, fb);
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(msg.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch { /* clipboard unavailable */ }
  };
  return (
    <div className="mt-1.5 flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
      <button
        onClick={copy}
        className="flex items-center gap-1 rounded-[6px] px-2 py-1 text-[0.68rem] text-muted transition-colors hover:bg-surface-hover hover:text-fg"
      >
        {copied ? <Check className="h-3 w-3 text-success" /> : <Copy className="h-3 w-3" />}
        {copied ? tr("msg.copied") : tr("msg.copy")}
      </button>
      {onRegenerate && (
        <button
          onClick={onRegenerate}
          disabled={disabled}
          className="flex items-center gap-1 rounded-[6px] px-2 py-1 text-[0.68rem] text-muted transition-colors hover:bg-surface-hover hover:text-fg disabled:opacity-40"
        >
          <RefreshCw className="h-3 w-3" />
          {tr("chat.regenerate")}
        </button>
      )}
    </div>
  );
}

/* ---- Main ChatMessage ----
 * 用户：右对齐气泡（accent-soft，12px 圆角）；AI：左对齐无气泡，accent 细竖线。
 * Memoized: during streaming the page re-renders on every throttled flush;
 * historical messages keep stable msg references and skip re-render entirely. */
function ChatMessageImpl({ msg, isPending, onRegenerate, disabled }: {
  msg: ChatMessageType; isPending?: boolean; onRegenerate?: () => void; disabled?: boolean;
}) {
  const isUser = msg.role === "user";
  const thinking = (isPending ? "" : msg.thinking) || "";

  if (isUser) {
    return (
      <div className="flex justify-end px-1 py-2.5">
        <div className="flex max-w-[78%] flex-col items-end">
          <div className="rounded-[12px] rounded-br-[4px] bg-accent-soft px-4 py-2.5 text-[0.85rem] leading-[1.7] text-fg shadow-sm">
            <p className="whitespace-pre-wrap">{msg.content}</p>
          </div>
          {msg.attachments && msg.attachments.length > 0 && (
            <div className="mt-1.5 flex flex-wrap justify-end gap-1.5">
              {msg.attachments.map((a, i) => (
                <AttachmentCard key={i} filename={a.filename} charCount={a.char_count} />
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="group px-1 py-3">
      <div className="border-l-2 border-accent/35 pl-4">
        {thinking && !isPending && <ThinkingBlock text={thinking} />}
        <div className="py-0.5">
          <Markdown>{msg.content || (isPending ? "…" : "")}</Markdown>
          {!isPending && msg.content && <AssistantActions msg={msg} onRegenerate={onRegenerate} disabled={disabled} />}
        </div>
        {/* 工具/答题卡按输出顺序放在正式回答之后（讲解在前，做题在后） */}
        {msg.toolCalls && msg.toolCalls.length > 0 && (
          <div className="mt-2.5 space-y-2">
            {msg.toolCalls.map((tc, i) => <ToolCallCard key={i} name={tc.name} result={tc.result} />)}
          </div>
        )}
      </div>
    </div>
  );
}

export const ChatMessage = memo(ChatMessageImpl);

/* ---- 步骤指示：理解 → 规划 → 思考 → 工具执行 的小圆点序列 ---- */
const STEP_ORDER = ["understanding", "planning", "thinking", "tool_executing"] as const;

function StepIndicator({ currentStep, heartbeatElapsed }: { currentStep: string; heartbeatElapsed: number }) {
  const { lang } = useUIStore();
  const tr = (k: string, fb?: string) => t(lang, k, fb);
  const currentIdx = STEP_ORDER.indexOf(currentStep as (typeof STEP_ORDER)[number]);
  const label = tr(`step.${currentStep}`, currentStep);

  return (
    <div className="mb-2 flex items-center gap-1.5">
      {STEP_ORDER.map((s, i) => {
        const done = currentIdx > i;
        const active = currentIdx === i;
        return (
          <span key={s} className="flex items-center gap-1.5">
            {i > 0 && <span className={cn("h-px w-3", done || active ? "bg-accent/50" : "bg-border")} />}
            <span
              title={tr(`step.${s}`, s)}
              className={cn(
                "h-1.5 w-1.5 rounded-full transition-colors",
                done ? "bg-accent" : active ? "animate-pulse bg-accent" : "bg-border",
              )}
            />
          </span>
        );
      })}
      <span className="ml-1 text-[0.72rem] text-muted">{label}…</span>
      {heartbeatElapsed > 0 && (
        <span className="tnum text-[0.68rem] text-muted/50">{heartbeatElapsed}s</span>
      )}
    </div>
  );
}

/* ---- StreamingMessage: live streaming UI ---- */
export function StreamingMessage({
  thinking, answer, activeTool, toolProgress, toolCalls, currentStep,
  heartbeatElapsed, retry,
}: {
  thinking: string; answer: string; activeTool: string | null;
  toolProgress: string[]; toolCalls: { name: string; result?: unknown }[];
  currentStep: string | null; heartbeatElapsed: number; retry: RetryState | null;
}) {
  const { lang } = useUIStore();
  const tr = (k: string, fb?: string) => t(lang, k, fb);
  const hasContent = thinking || answer || activeTool || toolCalls.length > 0 || currentStep;

  return (
    <div className="px-1 py-3">
      <div className="border-l-2 border-accent/35 pl-4">
        {/* Retry indicator */}
        {retry && retry.visible && (
          <div className="mb-2 flex items-center gap-2 rounded-[8px] bg-warning/10 px-3 py-1.5 text-[0.72rem] text-warning">
            <RotateCw size={12} className="animate-spin" />
            {tr("chat.retrying")} · {tr("chat.retry.attempt").replace("%n", String(retry.attempt))}: {retry.reason}…
          </div>
        )}
        {/* Step indicator (until the answer starts streaming) */}
        {currentStep && !answer && (
          <StepIndicator currentStep={currentStep} heartbeatElapsed={activeTool ? 0 : heartbeatElapsed} />
        )}
        {/* Thinking block: 流式期间自动展开，实时预览真实推理（有界） */}
        {thinking && (
          <ThinkingBlock
            text={thinking}
            isStreaming={true}
          />
        )}
        {/* Active tool indicator */}
        {activeTool && (
          <ActiveToolCard name={activeTool} progress={toolProgress} heartbeatElapsed={heartbeatElapsed} />
        )}
        {/* Streaming answer with cursor */}
        {answer && (
          <div className="py-0.5">
            <StreamingMarkdown>{answer}</StreamingMarkdown>
            <span className="streaming-cursor" />
          </div>
        )}
        {/* Completed tool cards（按输出顺序放在正式回答之后） */}
        {toolCalls.length > 0 && (
          <div className="mt-2.5 space-y-2">
            {toolCalls.map((tc, i) => <ToolCallCard key={i} name={tc.name} result={tc.result} />)}
          </div>
        )}
        {/* Bouncing dots when nothing yet */}
        {!hasContent && (
          <div className="dot-loader py-2">
            <span /><span /><span />
          </div>
        )}
      </div>
    </div>
  );
}
