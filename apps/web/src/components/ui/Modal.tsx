"use client";
import { useEffect, useId, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Button } from "./Button";
import { MODAL_LAYER, isTopmost, lockBodyScroll, registerOverlay } from "@/lib/assistant/overlay";

/** 居中确认弹窗。Escape 只关最上层覆盖层（OverlayCoordinator，AC-30）；
 *  打开期间锁定背景滚动（Landing 等 body 可滚动页面不再跟着滚）。 */
export function Modal({
  open,
  onClose,
  title,
  ariaLabel,
  children,
  footer,
  width = 420,
  testId,
}: {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  ariaLabel?: string;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
  /** Forwarded as `data-testid` on the dialog element (custom components
   *  silently drop JSX `data-*` attributes, so it must be explicit). */
  testId?: string;
}) {
  const overlayId = useId();
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
      <div className="motion-fade absolute inset-0 bg-black/30 backdrop-blur-[2px]" onClick={onClose} />
      <div
        role="dialog" aria-modal="true" aria-label={ariaLabel} aria-labelledby={title ? `${overlayId}-title` : undefined}
        data-testid={testId}
        className="motion-modal relative flex max-h-[calc(100vh-2rem)] flex-col rounded-[14px] border border-border bg-surface p-5 shadow-lg"
        style={{ width: `min(${width}px, 94vw)` }}
      >
        {title && <div id={`${overlayId}-title`} className="mb-3 shrink-0 text-[15px] font-semibold text-fg">{title}</div>}
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain text-sm text-fg-secondary">{children}</div>
        {footer && <div className="mt-5 flex shrink-0 justify-end gap-2">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

/** 危险操作确认弹窗的便捷封装。confirmPhrase 设置后需键入完全一致的
 * 短语（如账号邮箱）才能点击确认，用于不可恢复操作的强确认。 */
export function ConfirmModal({
  open,
  onClose,
  onConfirm,
  title,
  desc,
  confirmText,
  cancelText,
  confirmPhrase,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  desc: ReactNode;
  confirmText: string;
  cancelText: string;
  confirmPhrase?: string;
}) {
  const [typed, setTyped] = useState("");
  // 每次关闭（取消/确认）都重置输入，下次打开从空开始。
  const close = () => { setTyped(""); onClose(); };
  const confirm = () => { setTyped(""); onConfirm(); };
  const armed = !confirmPhrase || typed.trim() === confirmPhrase;
  return (
    <Modal
      open={open}
      onClose={close}
      title={title}
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={close}>
            {cancelText}
          </Button>
          <Button variant="danger" size="sm" disabled={!armed} onClick={confirm}>
            {confirmText}
          </Button>
        </>
      }
    >
      {desc}
      {confirmPhrase && (
        <input
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder={confirmPhrase}
          spellCheck={false}
          autoComplete="off"
          className="mt-3 h-8 w-full rounded-[8px] border border-border bg-surface px-2 text-sm text-fg outline-none placeholder:text-muted focus:border-danger"
        />
      )}
    </Modal>
  );
}
