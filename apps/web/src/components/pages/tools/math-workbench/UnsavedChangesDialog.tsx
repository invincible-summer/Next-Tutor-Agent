"use client";

import { useEffect, useRef } from "react";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";

/** D7 three-choice in-app transition confirmation (save-and-continue /
 *  discard / cancel) with single-flight pending intent. The switch-mode
 *  variant (ADR-0023) keeps the data in memory, so "discard" reads as a
 *  plain "switch anyway" instead of a lossy discard. */
export function UnsavedChangesDialog({
  open, tr, error, onCancel, onSave, onDiscard, variant = "default",
}: {
  open: boolean;
  tr: (key: string) => string;
  error: string | null;
  onCancel: () => void;
  onSave: () => void;
  onDiscard: () => void;
  variant?: "default" | "switch-mode";
}) {
  const saveRef = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (open) saveRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
  }, [open]);
  return (
    <Modal
      open={open}
      onClose={onCancel}
      title={tr("unsavedTitle")}
      testId="geometry-unsaved-dialog"
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onCancel}>{tr("cancel")}</Button>
          <Button variant="danger" size="sm" onClick={onDiscard}>
            {variant === "switch-mode" ? tr("switchDirectly") : tr("discardChanges")}
          </Button>
          <span ref={saveRef}><Button variant="primary" size="sm" onClick={onSave}>{tr("saveAndContinue")}</Button></span>
        </>
      }
    >
      <p>{variant === "switch-mode" ? tr("unsavedSwitchBody") : tr("unsavedBody")}</p>
      {error && <p className="text-danger" role="alert">{error}</p>}
    </Modal>
  );
}
