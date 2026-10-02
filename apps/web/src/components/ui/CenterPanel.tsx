"use client";
// 大号居中弹窗面板：与 Modal 同一套 motion 语义，但面向整页级内容
// （笔记中心这类多栏工作台），头部带标题与右侧插槽，内容区不内边距。
import { useEffect, type ReactNode } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/cn";

export function CenterPanel({
  open,
  onClose,
  title,
  extra,
  children,
  width = 960,
  bodyClassName,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  /** 头部右侧插槽（统计、操作按钮等）。 */
  extra?: ReactNode;
  children: ReactNode;
  width?: number;
  bodyClassName?: string;
}) {
  useEffect(() => {
    if (!open) return;
    const fn = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", fn);
    return () => window.removeEventListener("keydown", fn);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="motion-fade absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose} />
      <div
        className="motion-modal relative flex max-h-[calc(100vh-3rem)] flex-col overflow-hidden rounded-[16px] border border-border bg-surface shadow-2xl"
        style={{ width: `min(${width}px, 94vw)` }}
      >
        <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border bg-surface px-4">
          <div className="min-w-0 flex-1 text-[15px] font-semibold text-fg">{title}</div>
          {extra}
          <button
            onClick={onClose}
            aria-label="close"
            className="shrink-0 cursor-pointer rounded-md p-1.5 text-muted transition-colors hover:bg-surface-hover hover:text-fg"
          >
            <X size={16} />
          </button>
        </div>
        <div className={cn("min-h-0 flex-1", bodyClassName)}>
          {children}
        </div>
      </div>
    </div>
  );
}
