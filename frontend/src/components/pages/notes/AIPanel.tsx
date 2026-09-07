"use client";
// 笔记助手面板（2026-09 重构：每笔记专属智能体）。
// - 消息观感对齐原生 chat：复用 ChatMessage（用户 accent-soft 气泡 / 助手
//   accent 左竖线 + chat-prose 排版 + hover 复制），流式块用 ThinkingBlock
//   （深度思考实时展开）+ ActiveToolCard/NotesToolCard + StreamingMarkdown
//   + streaming-cursor，公式在流式期间也有保护。
// - 每篇笔记一个智能体：历史/模式/待批复计划各自独立（切换笔记即切换
//   对话），无线程概念；plan 模式产出计划卡（服务端 pending_plan 状态机），
//   pending 时输入框上方出现一次批复条，批准后 mode_changed 自动切授权。
// - 图片附件：上传（/notes/upload，OCR 预览）→ 发送时 <ocr_material> 前缀
//   + attachments（MULTIMODAL 配置时走视觉通道）。
import { useEffect, useRef, useState } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Bot, Check, ChevronDown, CircleStop, ClipboardList, Eraser, FileText,
  HelpCircle, ImagePlus, Loader2, PanelRightClose, ScanLine,
  Send, ShieldCheck, Sparkles, X,
} from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Input";
import { StreamingMarkdown } from "@/components/chat/markdown";
import { ChatMessage } from "@/components/chat/ChatMessage";
import { ThinkingBlock } from "@/components/chat/ThinkingBlock";
import { ActiveToolCard } from "@/components/chat/ToolCallCard";
import { useClickOutside } from "@/components/sidebar/Dropdown";
import { cn } from "@/lib/cn";
import { VAULT_AGENT_KEY, notesChatStream, notesUpload } from "@/lib/api-notes";
import { useNotesStore } from "@/lib/store-notes";
import type { AgentMode } from "@/lib/types-notes";
import { NotesToolCard, toolDisplayName } from "./NotesToolCard";
import { PlanCard, stripPlanCardJson } from "./PlanCard";

const MODES: { key: AgentMode; icon: LucideIcon; labelKey: string; descKey: string }[] = [
  { key: "ask", icon: HelpCircle, labelKey: "ai.mode.ask", descKey: "ai.mode.ask.desc" },
  { key: "plan", icon: ClipboardList, labelKey: "ai.mode.plan", descKey: "ai.mode.plan.desc" },
  { key: "authorize", icon: ShieldCheck, labelKey: "ai.mode.authorize", descKey: "ai.mode.authorize.desc" },
];

interface ToolActivity {
  name: string;
  status: string;
  text: string;
}

export function AIPanel({
  tr,
  onRemoteUpdate,
  onVaultChanged,
  onClose,
}: {
  tr: (k: string, fallback?: string) => string;
  onRemoteUpdate: (noteId: string, content: string, revision: number, title: string) => void;
  onVaultChanged: () => void;
  /** 抽屉场景传入（关闭抽屉）；内联场景缺省 = 折叠面板 */
  onClose?: () => void;
}) {
  const {
    currentId, detail, agentMode, setAgentMode, applyModeFromServer,
    agent, agentMessages, loadAgent, setAgentPlan, clearAgentChat,
    vault, toggleAiPanel,
  } = useNotesStore();
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [streamAnswer, setStreamAnswer] = useState("");
  const [streamThinking, setStreamThinking] = useState("");
  const [pendingUser, setPendingUser] = useState("");
  const [activeTool, setActiveTool] = useState("");
  const [doneTools, setDoneTools] = useState<ToolActivity[]>([]);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [modeMenuOpen, setModeMenuOpen] = useState(false);
  const [imgAttachments, setImgAttachments] = useState<{ id: string; filename: string }[]>([]);
  const [ocrText, setOcrText] = useState("");
  const [ocrLoading, setOcrLoading] = useState(false);
  const [imgError, setImgError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const modeMenuRef = useClickOutside<HTMLDivElement>(
    () => setModeMenuOpen(false), modeMenuOpen);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const pendingPlan = agent?.pending_plan ?? null;
  const showPlanBar = pendingPlan?.status === "pending" && !streaming;
  const activeMode = MODES.find((m) => m.key === agentMode) ?? MODES[0];
  const agentKey = currentId || VAULT_AGENT_KEY;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [agentMessages.length, streamAnswer, streamThinking, doneTools.length]);

  const handleImage = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    if (imgAttachments.length >= 3) {
      setImgError(tr("ai.upload.limit", "最多 {n} 张图片").replace("{n}", "3"));
      return;
    }
    const img = files[0];
    const isImg = /^image\/(png|jpe?g|webp|bmp|tiff)$/i.test(img.type)
      || /\.(png|jpe?g|webp|bmp|tiff)$/i.test(img.name);
    if (!isImg) {
      setImgError(tr("ai.upload.unsupported"));
      return;
    }
    setImgError("");
    setOcrLoading(true);
    try {
      const res = await notesUpload([img]);
      const okr = res.results.filter((r) => r.id && !r.error);
      if (okr.length === 0) {
        setImgError(res.results[0]?.error || tr("ai.error"));
      } else {
        setImgAttachments((prev) => [
          ...prev, { id: okr[0].id!, filename: okr[0].filename },
        ]);
        const preview = okr[0]?.preview_text?.trim() || "";
        if (preview) setOcrText(preview);
      }
    } catch (e) {
      setImgError("OCR: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setOcrLoading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const flashToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(""), 4000);
  };

  const send = async (action: "" | "approve_plan" | "reject_plan" = "") => {
    if (streaming) return;
    const trimmed = input.trim();
    const ocr = ocrText.trim();
    if (!action && !trimmed && !ocr && imgAttachments.length === 0) return;
    const message = ocr
      ? `<ocr_material>${ocr}</ocr_material>\n\n${trimmed || tr("ai.img.default")}`
      : trimmed;
    const attachments = [...imgAttachments];
    const draft = { input, ocrText, attachments: [...imgAttachments] };
    setOcrText("");
    setImgAttachments([]);
    setImgError("");
    setError("");
    setStreamAnswer("");
    setStreamThinking("");
    setDoneTools([]);
    setActiveTool("");
    setStreaming(true);
    const controller = new AbortController();
    abortRef.current = controller;
    useNotesStore.setState({ aiStreaming: true });
    let completed = false;
    try {
      for await (const ev of notesChatStream(
        {
          message, mode: agentMode, action,
          context: currentId
            ? { note_id: currentId, scope: "note" }
            : { scope: "vault" },
          attachments: attachments.length > 0 ? attachments : undefined,
        }, controller.signal)) {
        switch (ev.type) {
          case "run_start":
            setInput("");
            setPendingUser(message);
            break;
          case "answer":
            if (ev.is_delta) setStreamAnswer((prev) => prev + String(ev.content ?? ""));
            break;
          case "thinking":
            if (ev.is_delta) setStreamThinking((prev) => prev + String(ev.content ?? ""));
            break;
          case "step":
            break;
          case "tool_start":
            setActiveTool(String(ev.name ?? ""));
            break;
          case "tool_result": {
            const result = (ev.result ?? {}) as { tool?: string; status?: string; text?: string };
            setActiveTool("");
            setDoneTools((prev) => [...prev, {
              name: String(result.tool ?? ev.name ?? ""),
              status: String(result.status ?? "success"),
              text: String(result.text ?? ""),
            }]);
            break;
          }
          case "note_updated":
            onRemoteUpdate(String(ev.note_id ?? ""), String(ev.content ?? ""),
              Number(ev.revision ?? 0), String(ev.title ?? ""));
            flashToast(tr("ai.updated", "助手已更新《{title}》")
              .replace("{title}", String(ev.title ?? "")));
            break;
          case "mode_changed":
            // 批复后服务端持久切到授权模式：同步选择器（不再回写 PATCH）
            applyModeFromServer(String(ev.mode ?? "authorize"));
            break;
          case "plan_card": {
            const plan = ev.plan as typeof pendingPlan;
            if (plan) {
              setAgentPlan(plan);
              flashToast(plan.status === "rejected"
                ? tr("ai.plan.rejected.toast")
                : tr("ai.plan.pending"));
            }
            break;
          }
          case "error":
            setError(String(ev.message ?? tr("ai.error")));
            setInput((current) => current || draft.input);
            setOcrText((current) => current || draft.ocrText);
            setImgAttachments((current) => current.length > 0 ? current : draft.attachments);
            break;
          case "done":
            completed = true;
            setStreamAnswer("");
            break;
          default:
            break;
        }
      }
    } catch (e) {
      setInput((current) => current || draft.input);
      setOcrText((current) => current || draft.ocrText);
      setImgAttachments((current) => current.length > 0 ? current : draft.attachments);
      if (!(e instanceof DOMException && e.name === "AbortError")) {
        setError(e instanceof Error ? e.message : tr("ai.error"));
      }
    } finally {
      if (!completed) {
        setInput((current) => current || draft.input);
        setOcrText((current) => current || draft.ocrText);
        setImgAttachments((current) => current.length > 0 ? current : draft.attachments);
      }
      setStreaming(false);
      setActiveTool("");
      abortRef.current = null;
      useNotesStore.setState({ aiStreaming: false, pendingAnswer: "" });
      // 统一收尾：重拉该笔记的智能体状态（消息/计划/模式）与仓库列表
      void loadAgent(agentKey).finally(() => setPendingUser(""));
      onVaultChanged();
    }
  };

  const noteTitle = currentId
    ? detail?.note.title || tr("ai.context.note")
    : tr("ai.context.vault");

  return (
    <div className="flex h-full w-full flex-col border-l border-border bg-surface">
      {/* 头部：专属智能体标题 + 清空 + 面板动作（无线程下拉） */}
      <div className="border-b border-border px-3 py-2">
        <div className="flex items-center gap-1.5">
          <Bot size={14} className="shrink-0 text-accent" />
          <span className="min-w-0 flex-1 truncate text-xs font-medium text-fg">
            {noteTitle}
          </span>
          <button
            onClick={() => { if (window.confirm(tr("ai.clear.confirm"))) void clearAgentChat(); }}
            title={tr("ai.clear")} aria-label={tr("ai.clear")}
            disabled={streaming || agentMessages.length === 0}
            className="rounded-[6px] p-1.5 text-muted transition-colors hover:bg-danger/10 hover:text-danger disabled:opacity-40"
          ><Eraser size={13} /></button>
          <button
            onClick={onClose ?? toggleAiPanel}
            title={tr("tb.toggleAi")}
            className="rounded-[6px] p-1.5 text-muted transition-colors hover:bg-surface-hover hover:text-accent"
          ><PanelRightClose size={14} /></button>
        </div>
        <div className="mt-1.5 flex min-w-0 flex-wrap items-center gap-1.5">
          <span className="flex min-w-0 items-center gap-1 rounded-full bg-accent-soft px-2 py-0.5 text-[10px] text-accent-strong">
            {currentId ? <FileText size={10} /> : <Sparkles size={10} />}
            <span className="truncate">{currentId ? tr("ai.context.note") : tr("ai.context.vault")}</span>
          </span>
          <span className="rounded-full bg-surface-sunken px-2 py-0.5 text-[10px] text-muted">仓库 · {vault?.notes.length || 0} 篇</span>
        </div>
      </div>

      {/* 对话区：原生 chat 观感（ChatMessage 复用） */}
      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
        {agentMessages.length === 0 && !streaming && !pendingUser && (
          <div className="px-2 py-8 text-center text-xs leading-relaxed text-muted">
            {tr("ai.empty")}
          </div>
        )}
        {agentMessages.map((m, i) => (
          <ChatMessage
            key={`${m.ts}-${i}`}
            msg={{
              role: m.role,
              // 计划 JSON 围栏由 PlanCard 结构化呈现，消息正文剥掉原始 JSON
              content: m.role === "assistant" ? stripPlanCardJson(m.content) : m.content,
            }}
          />
        ))}
        {/* 计划卡：跟在消息流后，状态徽标随批复流转 */}
        {pendingPlan && !streaming && <PlanCard plan={pendingPlan} tr={tr} />}
        {pendingUser && (
          <ChatMessage msg={{ role: "user", content: pendingUser }} />
        )}
        {streaming && (
          <div className="px-1 py-3">
            <div className="border-l-2 border-accent/35 pl-4">
              {streamThinking && (
                <ThinkingBlock text={streamThinking} isStreaming />
              )}
              {doneTools.map((t, i) => (
                <NotesToolCard key={`${t.name}-${i}`} name={t.name} status={t.status} text={t.text} />
              ))}
              {activeTool && (
                <div className="mb-2">
                  <ActiveToolCard name={activeTool} progress={[toolDisplayName(activeTool)]} heartbeatElapsed={0} />
                </div>
              )}
              {streamAnswer ? (
                <div className="py-0.5">
                  <StreamingMarkdown>{stripPlanCardJson(streamAnswer)}</StreamingMarkdown>
                  <span className="streaming-cursor" />
                </div>
              ) : (!activeTool && !streamThinking && <span className="dot-loader" />)}
            </div>
          </div>
        )}
        {error && <div className="px-2 py-1 text-xs text-danger">{error}</div>}
        {toast && (
          <div className="mx-1 my-1 rounded-md bg-success/10 px-2 py-1 text-[11px] text-success">
            {toast}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* 输入区：批复条 + 图片附件 + 模式选择器（VSCode 式左下角） */}
      <div className="border-t border-border px-3 py-2">
        {showPlanBar && (
          <div className="mb-2 flex items-center gap-1.5 rounded-[10px] border border-accent/20 bg-accent-soft/60 px-2.5 py-1.5">
            <ClipboardList size={12} className="shrink-0 text-accent-strong" />
            <span className="min-w-0 flex-1 truncate text-[11px] text-accent-strong">
              {tr("ai.plan.pending")}
            </span>
            <Button
              variant="ghost" size="sm" icon={<X size={12} />}
              onClick={() => void send("reject_plan")}
            >
              {tr("ai.plan.reject")}
            </Button>
            <Button
              variant="primary" size="sm" icon={<Check size={12} />}
              onClick={() => void send("approve_plan")}
            >
              {tr("ai.plan.approve")}
            </Button>
          </div>
        )}
        <Textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
          placeholder={tr("ai.placeholder")}
          rows={2}
          className="max-h-28 min-h-[2.4rem] resize-none"
        />
        {/* 图片附件：OCR 预览卡 + 附件 chips（随下一条消息一并提交） */}
        {ocrLoading && (
          <div className="mt-1.5 flex items-center gap-2 rounded-[10px] border border-accent/20 bg-accent-soft/40 px-2.5 py-1.5">
            <Loader2 size={12} className="animate-spin text-accent" />
            <span className="text-[11px] text-fg-secondary">{tr("ai.upload.ocr")}</span>
          </div>
        )}
        {ocrText && !ocrLoading && (
          <div className="mt-1.5 rounded-[10px] border border-accent/20 bg-accent-soft/40 px-2.5 py-1.5">
            <div className="flex items-center gap-1.5">
              <ScanLine size={12} className="shrink-0 text-accent" />
              <span className="flex-1 text-[10px] font-medium text-accent-strong">
                {tr("ai.ocr.preview")}
              </span>
              <button
                onClick={() => setOcrText("")}
                className="cursor-pointer text-muted transition-colors hover:text-danger"
                aria-label="clear OCR text"
              >
                <X size={12} />
              </button>
            </div>
            <p className="mt-1 max-h-20 overflow-y-auto whitespace-pre-wrap text-[11px] leading-relaxed text-fg-secondary">
              {ocrText}
            </p>
          </div>
        )}
        {imgAttachments.length > 0 && (
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {imgAttachments.map((a) => (
              <span key={a.id} className="flex items-center gap-1 rounded-full bg-surface-sunken px-2.5 py-0.5 text-[11px] text-fg-secondary">
                <ImagePlus size={11} className="shrink-0 text-accent" />
                <span className="max-w-36 truncate">{a.filename}</span>
                <button
                  onClick={() => setImgAttachments((prev) => prev.filter((x) => x.id !== a.id))}
                  className="cursor-pointer text-muted transition-colors hover:text-danger"
                  aria-label={`remove ${a.filename}`}
                >
                  <X size={11} />
                </button>
              </span>
            ))}
          </div>
        )}
        {imgError && <div className="mt-1.5 text-[11px] text-danger">{imgError}</div>}
        <div className="mt-1.5 flex items-center gap-1.5">
          <input
            ref={fileRef}
            type="file"
            accept="image/png,image/jpeg,image/webp,image/bmp,image/tiff"
            className="hidden"
            onChange={(e) => { void handleImage(e.target.files); }}
          />
          <button
            onClick={() => fileRef.current?.click()}
            title={tr("ai.upload")}
            aria-label={tr("ai.upload")}
            className="shrink-0 cursor-pointer rounded-md p-1.5 text-muted transition-colors hover:bg-surface-hover hover:text-accent"
          >
            <ImagePlus size={14} />
          </button>
          {/* 模式选择器（VSCode 式，输入框左下角） */}
          <div className="relative shrink-0" ref={modeMenuRef}>
            <button
              onClick={() => setModeMenuOpen((v) => !v)}
              title={tr("ai.mode.pick")}
              className="flex cursor-pointer items-center gap-1 rounded-md border border-border px-1.5 py-1 text-[11px] text-fg-secondary transition-colors hover:border-accent hover:text-accent"
            >
              <activeMode.icon size={12} className="text-accent" />
              {tr(activeMode.labelKey)}
              <ChevronDown size={10} className="text-muted" />
            </button>
            {modeMenuOpen && (
              <div className="motion-pop absolute bottom-8 left-0 z-30 w-64 rounded-[10px] border border-border bg-surface p-1 shadow-lg">
                {MODES.map((m) => (
                  <button
                    key={m.key}
                    onClick={() => { setAgentMode(m.key); setModeMenuOpen(false); }}
                    className={cn(
                      "flex w-full cursor-pointer items-start gap-2 rounded-md px-2 py-1.5 text-left transition-colors",
                      agentMode === m.key ? "bg-accent-soft" : "hover:bg-surface-hover",
                    )}
                  >
                    <m.icon size={13} className={cn(
                      "mt-0.5 shrink-0",
                      agentMode === m.key ? "text-accent-strong" : "text-muted",
                    )} />
                    <span className="min-w-0">
                      <span className={cn(
                        "block text-xs",
                        agentMode === m.key ? "font-medium text-accent-strong" : "text-fg",
                      )}>
                        {tr(m.labelKey)}
                      </span>
                      <span className="block text-[10px] leading-snug text-muted">
                        {tr(m.descKey)}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="ml-auto shrink-0">
            {streaming ? (
              <Button variant="outline" size="sm" icon={<CircleStop size={13} />}
                onClick={() => abortRef.current?.abort()}>
                {tr("ai.stop")}
              </Button>
            ) : (
              <Button variant="primary" size="sm" icon={<Send size={13} />}
                disabled={!input.trim() && !ocrText.trim() && imgAttachments.length === 0 && !(pendingPlan?.status === "pending")}
                onClick={() => void send()}>
                {tr("ai.send")}
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
