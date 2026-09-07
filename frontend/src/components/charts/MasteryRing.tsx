import { cn } from "@/lib/cn";

/** 掌握度圆环：单值 0..1，中心显示百分比。 */
export function MasteryRing({
  value,
  size = 72,
  thickness = 7,
  label,
  className,
}: {
  value: number; // 0..1
  size?: number;
  thickness?: number;
  label?: string;
  className?: string;
}) {
  const v = Math.max(0, Math.min(1, value));
  const r = (size - thickness) / 2;
  const circ = 2 * Math.PI * r;
  const color =
    v >= 0.8 ? "var(--m-mastered)" : v >= 0.5 ? "var(--m-learning)" : v > 0.15 ? "var(--m-weak)" : "var(--m-todo)";
  return (
    <div className={cn("relative inline-block", className)} style={{ width: size, height: size }}>
      <svg viewBox={`0 0 ${size} ${size}`} className="h-full w-full -rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgb(var(--surface-hover))" strokeWidth={thickness} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={`rgb(${color})`}
          strokeWidth={thickness}
          strokeLinecap="round"
          strokeDasharray={`${v * circ} ${circ}`}
          className="transition-[stroke-dasharray] duration-700"
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="tnum text-sm font-semibold text-fg">{Math.round(v * 100)}%</span>
        {label && <span className="text-[10px] text-muted">{label}</span>}
      </div>
    </div>
  );
}
