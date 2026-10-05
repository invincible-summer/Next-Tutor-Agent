import React from "react";
import { Button, Sheet } from "@/ui";
import { SheetBody, SheetHeader } from "./Elements";
import { useCopy } from "@/lib/copy";
export function EditSheet({
  open,
  title,
  onClose,
  onSave,
  pending = false,
  disabled = false,
  children,
  saveLabel,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  onSave: () => void;
  pending?: boolean;
  disabled?: boolean;
  children: React.ReactNode;
  saveLabel?: string;
}) {
  const c = useCopy();
  return (
    <Sheet open={open} onClose={onClose} label={title}>
      <SheetHeader title={title} onClose={onClose} />
      <SheetBody>
        {children}
        <Button
          title={saveLabel ?? c("保存", "Save")}
          onPress={onSave}
          loading={pending}
          disabled={disabled}
        />
      </SheetBody>
    </Sheet>
  );
}
