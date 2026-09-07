"use client";
import { X } from "lucide-react";
import { useEffect, type ReactNode } from "react";
import { cn } from "@/lib/cn";

/** 右侧抽屉：详情展示（知识节点/文件/记忆详情）。 */
export function Drawer({
  open,
  onClose,
  title,
  children,
  width = 420,
}: {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  children: ReactNode;
  width?: number;
}) {
  useEffect(() => {
    if (!open) return;
    const fn = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", fn);
    return () => window.removeEventListener("keydown", fn);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50">
      <div className="motion-fade absolute inset-0 bg-black/25" onClick={onClose} />
      <div
        className={cn(
          "absolute right-0 top-0 flex h-full flex-col border-l border-border bg-surface shadow-lg",
          "motion-drawer",
        )}
        style={{ width: `min(${width}px, 92vw)` }}
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div className="text-sm font-semibold text-fg">{title}</div>
          <button
            onClick={onClose}
            className="cursor-pointer rounded-md p-1 text-muted hover:bg-surface-hover hover:text-fg"
            aria-label="close"
          >
            <X size={16} />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-4">{children}</div>
      </div>
    </div>
  );
}
