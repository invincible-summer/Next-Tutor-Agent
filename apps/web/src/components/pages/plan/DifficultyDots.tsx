import { cn } from "@/lib/cn";

/** 1–5 难度圆点（填充数 = 难度值），仅 token 色。 */
export function DifficultyDots({
  value,
  max = 5,
  size = "sm",
  className,
}: {
  value: number;
  max?: number;
  size?: "sm" | "md";
  className?: string;
}) {
  const n = Math.max(0, Math.min(max, Math.round(value)));
  const dot = size === "md" ? "h-2.5 w-2.5" : "h-1.5 w-1.5";
  return (
    <span className={cn("inline-flex items-center gap-1", className)}>
      {Array.from({ length: max }, (_, i) => (
        <span
          key={i}
          className={cn("rounded-full", dot, i < n ? "bg-accent" : "bg-border")}
        />
      ))}
    </span>
  );
}
