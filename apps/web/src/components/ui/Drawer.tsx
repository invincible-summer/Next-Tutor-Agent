"use client";
import { X } from "lucide-react";
import { useEffect, useId, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/cn";
import { useUIStore } from "@/lib/store";
import { t } from "@/lib/i18n";
import { MODAL_LAYER, isTopmost, lockBodyScroll, registerOverlay } from "@/lib/assistant/overlay";

/** 右侧抽屉：详情展示（知识节点/文件/记忆详情）。
 *  Escape 只关最上层覆盖层（OverlayCoordinator，AC-30）。
 *  portal 到 body：fixed 定位不受父级 transform/filter/contain 影响，
 *  页面或内部容器滚动时抽屉始终贴合视口右侧；打开期间锁定背景滚动。 */
export function Drawer({
  open,
  onClose,
  title,
  children,
  footer,
  width = 420,
}: {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  children: ReactNode;
  /** 底部固定操作区（与 Modal.footer 对称），不随内容滚动。 */
  footer?: ReactNode;
  width?: number;
}) {
  const overlayId = useId();
  const lang = useUIStore((s) => s.lang);
  useEffect(() => {
    if (!open) return;
    const unregister = registerOverlay({
      id: overlayId, kind: "drawer", layer: MODAL_LAYER, onClose,
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
    <div className="fixed inset-0 z-50">
      <div className="motion-fade absolute inset-0 bg-black/25" onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? `${overlayId}-title` : undefined}
        className={cn(
          "absolute right-0 top-0 flex h-full flex-col border-l border-border bg-surface shadow-lg",
          "motion-drawer",
        )}
        style={{ width: `min(${width}px, 92vw)` }}
      >
        <div className="flex shrink-0 items-center justify-between border-b border-border px-4 py-3">
          <div id={`${overlayId}-title`} className="text-sm font-semibold text-fg">{title}</div>
          <button
            onClick={onClose}
            className="cursor-pointer rounded-md p-1 text-muted outline-none transition-colors hover:bg-surface-hover hover:text-fg focus-visible:ring-2 focus-visible:ring-accent"
            aria-label={t(lang, "common.close", "关闭")}
          >
            <X size={16} />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4">{children}</div>
        {footer && <div className="shrink-0 border-t border-border px-4 py-3">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}
