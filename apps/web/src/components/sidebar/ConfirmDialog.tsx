"use client";
import { useUIStore } from "@/lib/store";
import { t } from "@/lib/i18n";
import { ConfirmModal } from "@/components/ui/Modal";

/** 确认弹窗：复用全局 ConfirmModal（替代旧原生 confirm() 与自定义层）。 */
export function ConfirmDialog({
  title,
  desc,
  onCancel,
  onConfirm,
}: {
  title: string;
  desc?: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const { lang } = useUIStore();
  const tr = (k: string, fb?: string) => t(lang, k, fb);
  return (
    <ConfirmModal
      open
      onClose={onCancel}
      onConfirm={onConfirm}
      title={title}
      desc={desc ?? ""}
      confirmText={tr("common.confirm")}
      cancelText={tr("common.cancel")}
    />
  );
}
