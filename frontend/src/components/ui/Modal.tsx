"use client";
import { useEffect, useState, type ReactNode } from "react";
import { Button } from "./Button";

/** 居中确认弹窗。 */
export function Modal({
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
  footer?: ReactNode;
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
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="motion-fade absolute inset-0 bg-black/30 backdrop-blur-[2px]" onClick={onClose} />
      <div
        className="motion-modal relative flex max-h-[calc(100vh-2rem)] flex-col rounded-[14px] border border-border bg-surface p-5 shadow-lg"
        style={{ width: `min(${width}px, 94vw)` }}
      >
        {title && <div className="mb-3 shrink-0 text-[15px] font-semibold text-fg">{title}</div>}
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain text-sm text-fg-secondary">{children}</div>
        {footer && <div className="mt-5 flex shrink-0 justify-end gap-2">{footer}</div>}
      </div>
    </div>
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
