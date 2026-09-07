"use client";
import { useState } from "react";
import { Brain, ChevronDown, Loader2 } from "lucide-react";
import { cn } from "@/lib/cn";
import { useUIStore } from "@/lib/store";
import { t } from "@/lib/i18n";

/** 思考过程折叠条：默认收起为细条（深度思考 · N 字），展开为等宽小字。
 *  流式期间自动展开（出题/拟合模式可用 defaultCollapsed 强制收起）。 */
export function ThinkingBlock({
  text,
  isStreaming,
  defaultCollapsed,
}: {
  text: string;
  isStreaming?: boolean;
  defaultCollapsed?: boolean;
}) {
  const { lang } = useUIStore();
  const tr = (k: string, fb?: string) => t(lang, k, fb);
  const [open, setOpen] = useState(defaultCollapsed ? false : !!isStreaming);

  return (
    <div className="mb-2.5">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 rounded-[6px] py-1 text-[0.7rem] transition-colors hover:opacity-80"
      >
        {isStreaming ? (
          <Loader2 className="h-3 w-3 animate-spin text-accent" />
        ) : (
          <Brain className="h-3 w-3 text-muted/60" />
        )}
        <span className={cn("font-medium", isStreaming ? "text-accent" : "text-muted")}>
          {isStreaming ? tr("thinking.streaming") : tr("thinking.done")}
        </span>
        {!isStreaming && (
          <span className="tnum text-muted/50">
            · {text.length} {tr("unit.chars")}
          </span>
        )}
        <ChevronDown className={cn("h-3 w-3 text-muted/50 transition-transform", open ? "" : "-rotate-90")} />
      </button>
      {open && (
        <div className="mt-1.5 rounded-[8px] bg-surface-sunken px-3 py-2.5">
          <p className="whitespace-pre-wrap font-mono text-[0.72rem] leading-[1.7] text-fg-secondary">{text}</p>
        </div>
      )}
    </div>
  );
}
