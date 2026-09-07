import { cn } from "@/lib/cn";
import { HelpCircle } from "lucide-react";
import type { ReactNode } from "react";

/** 问号悬浮提示：纯 CSS hover/聚焦气泡，用于收纳表单与卡片的补充说明。 */
export function Hint({
  text,
  side = "top",
  align = "start",
  iconSize = 12,
  width = "w-52",
  className,
}: {
  text: ReactNode;
  side?: "top" | "bottom";
  align?: "start" | "center" | "end";
  iconSize?: number;
  width?: string;
  className?: string;
}) {
  return (
    <span className={cn("relative inline-flex group/hint", className)}>
      <HelpCircle
        size={iconSize}
        tabIndex={0}
        aria-label={typeof text === "string" ? text : undefined}
        className="shrink-0 cursor-help text-muted transition-colors hover:text-fg-secondary focus-visible:outline-none focus-visible:text-accent"
      />
      <span
        className={cn(
          "pointer-events-none absolute z-50 hidden rounded-md border border-border bg-surface px-2.5 py-2 text-left text-[0.65rem] leading-relaxed text-fg-secondary shadow-lg group-hover/hint:block group-focus-within/hint:block",
          width,
          side === "top" ? "bottom-[calc(100%+5px)]" : "top-[calc(100%+5px)]",
          align === "start" && "left-0",
          align === "center" && "left-1/2 -translate-x-1/2",
          align === "end" && "right-0",
        )}
      >
        {text}
      </span>
    </span>
  );
}
