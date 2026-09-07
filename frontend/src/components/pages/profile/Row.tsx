import { cn } from "@/lib/cn";
import type { ReactNode } from "react";

/** 键值行：左侧 muted 标签，右侧正文值。本页各卡片共用。 */
export function Row({ label, value, className }: { label: ReactNode; value: ReactNode; className?: string }) {
  return (
    <div className={cn("flex items-center justify-between gap-3 py-1.5", className)}>
      <span className="text-xs text-muted">{label}</span>
      <span className="text-right text-xs font-medium text-fg">{value}</span>
    </div>
  );
}
