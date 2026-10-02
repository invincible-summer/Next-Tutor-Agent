"use client";
import { cn } from "@/lib/cn";
import type { ReactNode } from "react";

export interface TabItem {
  key: string;
  label: ReactNode;
  badge?: ReactNode;
}

/** 下划线式 Tabs（受控）。 */
export function Tabs({
  items,
  active,
  onChange,
  className,
}: {
  items: TabItem[];
  active: string;
  onChange: (key: string) => void;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center gap-1 border-b border-border", className)}>
      {items.map((it) => {
        const on = it.key === active;
        return (
          <button
            key={it.key}
            onClick={() => onChange(it.key)}
            className={cn(
              "relative -mb-px cursor-pointer px-3 pb-2 pt-1 text-sm transition-colors duration-200",
              on ? "font-semibold text-accent" : "text-muted hover:text-fg",
            )}
          >
            <span className="inline-flex items-center gap-1.5">
              {it.label}
              {it.badge}
            </span>
            {/* 激活下划线：scaleX 过渡 */}
            <span
              aria-hidden
              className={cn(
                "absolute inset-x-3 -bottom-px h-0.5 origin-left rounded-full bg-accent transition-transform duration-300 ease-out",
                on ? "scale-x-100" : "scale-x-0",
              )}
            />
          </button>
        );
      })}
    </div>
  );
}
