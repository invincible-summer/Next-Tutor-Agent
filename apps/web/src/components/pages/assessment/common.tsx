// 测评中心共享小组件与类型。
import { cn } from "@/lib/cn";

/** makePageT 返回的页面级翻译函数。 */
export type PageTr = (key: string, fallback?: string) => string;

/** 难度指示：1–5 圆点，实心 = 当前难度。 */
export function DifficultyDots({ level, className }: { level: number; className?: string }) {
  const n = Math.max(0, Math.min(5, Math.round(level)));
  return (
    <span className={cn("inline-flex items-center gap-1", className)} aria-label={`difficulty ${n}/5`}>
      {Array.from({ length: 5 }, (_, i) => (
        <span
          key={i}
          className={cn(
            "h-2 w-2 rounded-full",
            i < n ? "bg-accent" : "bg-surface-hover border border-border",
          )}
        />
      ))}
    </span>
  );
}

/** 题目难度字段兼容 number 与 "easy|medium|hard" 字符串。 */
export function difficultyOf(q: { difficulty?: string | number } | null | undefined): number {
  const d = q?.difficulty;
  if (typeof d === "number" && Number.isFinite(d)) return d;
  if (d === "easy") return 2;
  if (d === "medium") return 3;
  if (d === "hard") return 5;
  return 3;
}
