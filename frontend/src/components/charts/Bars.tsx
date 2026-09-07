import { cn } from "@/lib/cn";

export interface BarItem {
  label: string;
  value: number; // 0..1
  display?: string;
  color?: string; // CSS color
  hint?: string;
}

/** 水平条形列表：策略成功率、掌握度排行等。 */
export function Bars({
  items,
  className,
  defaultColor = "rgb(var(--accent))",
}: {
  items: BarItem[];
  className?: string;
  defaultColor?: string;
}) {
  return (
    <div className={cn("flex flex-col gap-2.5", className)}>
      {items.map((it, i) => (
        <div key={`${it.label}-${i}`} className="flex flex-col gap-1">
          <div className="flex items-baseline justify-between text-xs">
            <span className="text-fg-secondary">
              {it.label}
              {it.hint && <span className="ml-1.5 text-muted">{it.hint}</span>}
            </span>
            <span className="tnum text-muted">{it.display ?? `${Math.round(it.value * 100)}%`}</span>
          </div>
          <div className="h-[6px] w-full overflow-hidden rounded-full bg-surface-hover">
            <div
              className="h-full rounded-full transition-[width] duration-500"
              style={{
                width: `${Math.max(0, Math.min(1, it.value)) * 100}%`,
                background: it.color ?? defaultColor,
              }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}
