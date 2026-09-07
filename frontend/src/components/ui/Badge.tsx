import { cn } from "@/lib/cn";
import type { ReactNode } from "react";

export type BadgeTone =
  | "accent"
  | "accent2"
  | "success"
  | "warning"
  | "danger"
  | "info"
  | "muted"
  | "outline";

const TONES: Record<BadgeTone, string> = {
  accent: "bg-accent-soft text-accent-strong border-transparent",
  accent2: "bg-accent2-soft text-accent2-strong border-transparent",
  success: "bg-success/12 text-success border-transparent",
  warning: "bg-warning/12 text-warning border-transparent",
  danger: "bg-danger/12 text-danger border-transparent",
  info: "bg-info/12 text-info border-transparent",
  muted: "bg-surface-hover text-fg-secondary border-transparent",
  outline: "bg-transparent text-fg-secondary border-border",
};

/** 小徽章：状态/标签/计数。 */
export function Badge({
  children,
  tone = "muted",
  className,
  dot = false,
}: {
  children: ReactNode;
  tone?: BadgeTone;
  className?: string;
  dot?: boolean;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-medium leading-4",
        TONES[tone],
        className,
      )}
    >
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

/** M1–M8 模块徽章：篆刻式小方章，系统识别符。 */
export function ModuleBadge({ id, className }: { id: string; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex h-[18px] items-center rounded-[4px] border border-accent/40 px-1",
        "font-mono text-[10px] font-semibold tracking-wide text-accent",
        className,
      )}
    >
      {id}
    </span>
  );
}
