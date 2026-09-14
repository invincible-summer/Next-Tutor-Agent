"use client";
import { useState } from "react";
import { BookOpen, ChevronDown, FileText, History, ScanSearch, Wrench } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/cn";
import { useChatStore, useUIStore } from "@/lib/store";
import { t } from "@/lib/i18n";
import { openFilePageSnapshot } from "@/lib/api";
import { QuizQuestionCard } from "./QuizCard";
import type { QuizQuestion } from "@/lib/types";

/** 图表证据可看原页：figure/table 块 + 有 file_id 与物理页码（页快照按 PDF 页渲染）。 */
function canViewPage(item: Record<string, unknown>): boolean {
  return (item.block_type === "figure" || item.block_type === "table")
    && Boolean(item.file_id) && Boolean(item.page);
}

const TOOL_ICONS: Record<string, LucideIcon> = {
  knowledge_search: BookOpen,
  generate_quiz: FileText,
  fit_quiz: ScanSearch,
  recall_history: History,
};

type ToolStatus = "running" | "success" | "partial" | "error";

/** 状态点：running 脉冲 / success 绿 / partial 黄 / error 红。 */
function StatusDot({ status }: { status: ToolStatus }) {
  if (status === "running") {
    return (
      <span className="relative flex h-2 w-2">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent opacity-60" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-accent" />
      </span>
    );
  }
  const color =
    status === "success" ? "bg-success" :
    status === "partial" ? "bg-warning" : "bg-danger";
  return <span className={cn("inline-flex h-2 w-2 rounded-full", color)} />;
}

/** 教具卡片：折叠式工具调用卡。头部 icon + 中文名 + 状态点，展开看结果；
 *  出题结果渲染为练习卡列表。 */
export function ToolCallCard({ name, result }: { name: string; result: unknown }) {
  const { lang } = useUIStore();
  // 会话 id：提交时服务端把作答归属到本会话（source_session_ref）。
  const sessionId = useChatStore((s) => s.sessionId);
  const tr = (k: string, fb?: string) => t(lang, k, fb);

  // Auto-expand generate_quiz on success so the interactive question cards
  // are the primary surface (avoids the assistant re-typing the questions).
  const r0 = result as { status?: string; data?: Record<string, unknown> } | undefined;
  const autoOpen = name === "generate_quiz" && r0?.status !== "error"
    && Array.isArray(r0?.data?.questions) && (r0!.data!.questions as unknown[]).length > 0;
  const [open, setOpen] = useState(autoOpen);

  const Icon = TOOL_ICONS[name] || Wrench;
  const label = tr(`tool.${name}`, name);
  const r = result as { status?: string; text?: string; data?: Record<string, unknown>; error?: { message?: string } } | undefined;
  const status: ToolStatus = !r ? "running" : r.status === "error" ? "error" : r.status === "partial" ? "partial" : "success";
  const isError = status === "error";
  const questions = (r?.data?.questions as QuizQuestion[]) || [];
  const statusText = !r
    ? tr("tool.running")
    : isError ? tr("tool.failed") : tr("tool.done");
  const knowledgeResults = name === "knowledge_search" && Array.isArray(r?.data?.results)
    ? (r!.data!.results as Array<Record<string, unknown>>)
    : [];

  return (
    <div className={cn(
      "overflow-hidden rounded-[10px] border shadow-sm",
      isError ? "border-danger/30 bg-danger/5" : "border-border bg-surface",
    )}>
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2.5 px-3 py-2 text-left transition-colors hover:bg-surface-hover"
      >
        <span className={cn(
          "flex h-6 w-6 shrink-0 items-center justify-center rounded-[6px]",
          isError ? "bg-danger/10 text-danger" : "bg-accent-soft text-accent-strong",
        )}>
          <Icon size={13} />
        </span>
        <span className="text-[0.78rem] font-medium text-fg-secondary">{label}</span>
        <span className="flex items-center gap-1.5 text-[0.68rem] text-muted">
          <StatusDot status={status} />
          {statusText}
        </span>
        {questions.length > 0 && (
          <span className="tnum text-[0.68rem] text-muted">
            · {questions.length} {tr("quiz.questions.unit")}
          </span>
        )}
        <ChevronDown size={13} className={cn("ml-auto shrink-0 text-muted transition-transform", open ? "" : "-rotate-90")} />
      </button>
      {open && (
        <div className="border-t border-border-light px-3 pb-3 pt-2.5">
          {isError ? (
            <p className="text-[0.75rem] text-danger">{r?.error?.message || r?.text || tr("tool.failed")}</p>
          ) : questions.length > 0 ? (
            <div className="space-y-2.5">
              {questions.map((q, i) => (
                <QuizQuestionCard key={i} question={q} index={i} sessionId={sessionId ?? undefined} />
              ))}
            </div>
          ) : name === "knowledge_search" && knowledgeResults.length > 0 ? (
            <div className="space-y-2.5">
              <div className="rounded-[8px] border border-accent/20 bg-accent-soft/20 px-2.5 py-2">
                <p className="mb-1 text-[0.68rem] font-semibold text-accent-strong">
                  {tr("tool.knowledge.sources", "命中来源")}
                  {Number(r?.data?.omitted_count || 0) > 0 && (
                    <span className="ml-1 font-normal text-muted">· {tr("tool.knowledge.filtered", "已过滤")} {String(r?.data?.omitted_count)} {tr("tool.knowledge.items", "条")}</span>
                  )}
                </p>
                <div className="space-y-1">
                  {knowledgeResults.map((item, i) => {
                    const filename = String(item.filename || item.source || "资料");
                    // 双轨页码：教材自标印刷页码优先，PDF 物理页码兜底
                    const printed = item.printed_page;
                    const page = printed
                      ? `${tr("tool.knowledge.printedPage", "教材第")} ${String(printed)} ${tr("tool.knowledge.pageUnit", "页")}`
                      : item.page ? `PDF 第 ${String(item.page)} 页` : tr("tool.knowledge.unpaged", "未标页");
                    const chapter = String(item.chapter || "").trim();
                    const section = String(item.section || "").trim();
                    // 章（单元）与节（课/篇目）并存且不同时都展示：如「第一单元 · 沁园春·长沙」
                    const location = [chapter, section !== chapter ? section : ""].filter(Boolean).join(" · ");
                    const blockBadge = item.block_type === "figure"
                      ? ` · [${tr("tool.knowledge.figure", "图")}]`
                      : item.block_type === "table" ? ` · [${tr("tool.knowledge.table", "表")}]` : "";
                    return (
                      <div key={`${String(item.chunk_id || i)}`} className="text-[0.7rem] leading-relaxed text-fg-secondary">
                        <span className="font-medium text-fg">{filename}</span>
                        <span className="text-muted"> · {page}{blockBadge}{location ? ` · ${location}` : ""}</span>
                        {item.confidence !== undefined && item.confidence !== null && (
                          <span className="text-muted"> · {Number(item.confidence) >= 0.65 ? tr("tool.knowledge.conf.high", "高置信") : tr("tool.knowledge.conf.medium", "中置信")}</span>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
              {knowledgeResults.map((item, i) => (
                <div key={`excerpt-${String(item.chunk_id || i)}`} className="rounded-[7px] border border-border-light px-2.5 py-2">
                  <p className="mb-1 flex items-center gap-1.5 text-[0.68rem] text-muted">
                    <span>
                      {String(item.filename || item.source || "资料")}
                      {item.printed_page
                        ? ` · ${tr("tool.knowledge.printedPage", "教材第")} ${String(item.printed_page)} ${tr("tool.knowledge.pageUnit", "页")}`
                        : item.page ? ` · PDF 第 ${String(item.page)} 页` : ""}
                    </span>
                    {canViewPage(item) && (
                      <button
                        onClick={() => void openFilePageSnapshot(
                          String(item.file_id), Number(item.page),
                        ).catch(() => undefined)}
                        className="cursor-pointer rounded px-1 font-medium text-accent hover:bg-accent-soft/40"
                        title={tr("tool.knowledge.viewPage", "查看教材原页")}
                      >
                        {tr("tool.knowledge.viewPage", "查看原页")}
                      </button>
                    )}
                  </p>
                  <p className="whitespace-pre-wrap text-[0.72rem] leading-relaxed text-fg-secondary">
                    {String(item.evidence_excerpt || item.text || "")}
                  </p>
                </div>
              ))}
            </div>
          ) : (
            <pre className="max-h-56 overflow-y-auto whitespace-pre-wrap font-mono text-[0.7rem] leading-relaxed text-muted">
              {r?.text || JSON.stringify(r?.data, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

/** 流式进行中工具卡：状态点脉冲 + 最新进度 + 心跳计时。 */
export function ActiveToolCard({
  name,
  progress,
  heartbeatElapsed,
}: {
  name: string;
  progress: string[];
  heartbeatElapsed: number;
}) {
  const { lang } = useUIStore();
  const tr = (k: string, fb?: string) => t(lang, k, fb);
  const Icon = TOOL_ICONS[name] || Wrench;
  const label = tr(`tool.${name}`, name);

  return (
    <div className="my-1.5 flex items-center gap-2.5 rounded-[10px] border border-border bg-surface px-3 py-2 shadow-sm">
      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-[6px] bg-accent-soft text-accent-strong">
        <Icon size={13} />
      </span>
      <span className="text-[0.78rem] font-medium text-fg-secondary">{label}</span>
      <StatusDot status="running" />
      <span className="min-w-0 flex-1 truncate text-[0.72rem] text-muted">
        {progress.length > 0 ? progress[progress.length - 1] : `${tr("tool.running")}…`}
      </span>
      {heartbeatElapsed > 0 && (
        <span className="tnum shrink-0 text-[0.68rem] text-muted/50">{heartbeatElapsed}s</span>
      )}
    </div>
  );
}
