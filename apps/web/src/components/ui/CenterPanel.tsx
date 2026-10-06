"use client";
// 大号居中弹窗面板：与 Modal 同一套 motion/overlay 语义，但面向整页级内容
// （笔记中心这类多栏工作台），头部带标题与右侧插槽，内容区不内边距。
// portal 到 body：fixed 定位不受父级 transform 影响；打开期间锁定背景滚动；
// Escape 只关最上层覆盖层（OverlayCoordinator，AC-30）。
import { useEffect, useId, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { cn } from "@/lib/cn";
import { useUIStore } from "@/lib/store";
import { t } from "@/lib/i18n";
import { MODAL_LAYER, isTopmost, lockBodyScroll, registerOverlay } from "@/lib/assistant/overlay";

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
  const overlayId = useId();
  const lang = useUIStore((s) => s.lang);
  useEffect(() => {
    if (!open) return;
    const unregister = registerOverlay({
      id: overlayId, kind: "modal", layer: MODAL_LAYER, onClose,
    });
    const unlock = lockBodyScroll();
    const fn = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isTopmost(overlayId)) onClose();
    };
    window.addEventListener("keydown", fn);
    return () => {
      window.removeEventListener("keydown", fn);
      unlock();
      unregister();
    };
  }, [open, onClose, overlayId]);

  if (!open || typeof document === "undefined") return null;
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="motion-fade absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${overlayId}-title`}
        className="motion-modal relative flex max-h-[calc(100vh-3rem)] flex-col overflow-hidden rounded-[16px] border border-border bg-surface shadow-2xl"
        style={{ width: `min(${width}px, 94vw)` }}
      >
        <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border bg-surface px-4">
          <div id={`${overlayId}-title`} className="min-w-0 flex-1 text-[15px] font-semibold text-fg">{title}</div>
          {extra}
          <button
            onClick={onClose}
            aria-label={t(lang, "common.close", "关闭")}
            className="shrink-0 cursor-pointer rounded-md p-1.5 text-muted outline-none transition-colors hover:bg-surface-hover hover:text-fg focus-visible:ring-2 focus-visible:ring-accent"
          >
            <X size={16} />
          </button>
        </div>
        <div className={cn("min-h-0 flex-1", bodyClassName)}>
          {children}
        </div>
      </div>
    </div>,
    document.body,
  );
}
