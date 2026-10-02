import { cn } from "@/lib/cn";

/** 细进度条。value 0..1。 */
export function Progress({
  value,
  tone = "accent",
  className,
  height = 5,
}: {
  value: number;
  tone?: "accent" | "success" | "warning" | "danger" | "accent2" | "muted";
  className?: string;
  height?: number;
}) {
  const color = {
    accent: "var(--accent)",
    success: "var(--success)",
    warning: "var(--warning)",
    danger: "var(--danger)",
    accent2: "var(--accent2)",
    muted: "var(--muted)",
  }[tone];
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div
      className={cn("w-full overflow-hidden rounded-full bg-surface-hover", className)}
      style={{ height }}
      role="progressbar"
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div
        className="h-full rounded-full transition-[width] duration-500"
        style={{ width: `${pct}%`, background: `rgb(${color})` }}
      />
    </div>
  );
}
