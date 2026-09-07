"use client";
// 笔记智能体的工具活动卡：已完成工具的紧凑结果卡（风格对齐原生 chat 的
// ToolCallCard，但不引入其 useChatStore/sessionId/答题卡耦合）。
// knowledge_search 的公开结果是脱敏的证据列表（来源 + 摘要），折叠展示。
import { useMemo, useState } from "react";
import {
  BookOpen, Check, FileText, Pencil, Search, Wrench, X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/cn";

const TOOL_META: Record<string, { icon: LucideIcon; label: string }> = {
  knowledge_search: { icon: BookOpen, label: "检索教材资料" },
  notes_search: { icon: Search, label: "检索笔记仓库" },
  notes_read: { icon: FileText, label: "读取笔记" },
  notes_write: { icon: Pencil, label: "修改笔记" },
};

export function toolDisplayName(name: string): string {
  return TOOL_META[name]?.label ?? name;
}

export function NotesToolCard({
  name, status, text,
}: {
  name: string;
  status: string;
  text?: string;
}) {
  const [open, setOpen] = useState(false);
  // 直接映射取组件（与 chat/ToolCallCard 的 TOOL_ICONS 同一模式），
  // 不在渲染期通过函数调用创建组件。
  const Icon = TOOL_META[name]?.icon ?? Wrench;
  const isError = status === "error";
  const body = useMemo(() => (text || "").trim(), [text]);
  return (
    <div className={cn(
      "my-1 w-full rounded-[10px] border px-2.5 py-1.5",
      isError ? "border-danger/30 bg-danger/5" : "border-border-light bg-surface-sunken/60",
    )}>
      <button
        onClick={() => body ? setOpen((v) => !v) : undefined}
        className={cn(
          "flex w-full items-center gap-1.5 text-left text-[11px]",
          body ? "cursor-pointer" : "cursor-default",
        )}
      >
        <Icon size={12} className={cn("shrink-0", isError ? "text-danger" : "text-accent")} />
        <span className="min-w-0 flex-1 truncate text-fg-secondary">
          {toolDisplayName(name)}
        </span>
        {isError
          ? <X size={12} className="shrink-0 text-danger" />
          : <Check size={12} className="shrink-0 text-success" />}
      </button>
      {open && body && (
        <p className="mt-1.5 max-h-44 overflow-y-auto whitespace-pre-wrap break-words border-t border-border-light pt-1.5 text-[10px] leading-relaxed text-muted">
          {body}
        </p>
      )}
    </div>
  );
}
