import { cn } from "@/lib/cn";

export interface DonutSlice {
  label: string;
  value: number;
  color: string; // CSS color，例如 "rgb(var(--m-mastered))"
}

/** 环形图：分布类数据（失败类型/掌握状态分布）。 */
export function Donut({
  slices,
  size = 180,
  thickness = 22,
  centerLabel,
  centerValue,
  className,
}: {
  slices: DonutSlice[];
  size?: number;
  thickness?: number;
  centerLabel?: string;
  centerValue?: string;
  className?: string;
}) {
  const total = slices.reduce((s, x) => s + x.value, 0) || 1;
  const cx = size / 2;
  const r = (size - thickness) / 2;
  const circ = 2 * Math.PI * r;
  // Precompute cumulative offsets immutably (lint: no reassignment in render).
  const fracs = slices.map((s) => s.value / total);
  const offsets = fracs.map((_, i) => -fracs.slice(0, i).reduce((a, b) => a + b, 0) * circ);
  return (
    <div className={cn("relative inline-block", className)} style={{ width: size, height: size }}>
      <svg viewBox={`0 0 ${size} ${size}`} className="h-full w-full -rotate-90">
        <circle cx={cx} cy={cx} r={r} fill="none" stroke="rgb(var(--surface-hover))" strokeWidth={thickness} />
        {slices.map((s, i) => (
          <circle
            key={i}
            cx={cx}
            cy={cx}
            r={r}
            fill="none"
            stroke={s.color}
            strokeWidth={thickness}
            strokeDasharray={`${fracs[i] * circ} ${circ}`}
            strokeDashoffset={offsets[i]}
          />
        ))}
      </svg>
      {(centerValue || centerLabel) && (
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          {centerValue && <div className="tnum text-xl font-semibold text-fg">{centerValue}</div>}
          {centerLabel && <div className="text-xs text-muted">{centerLabel}</div>}
        </div>
      )}
    </div>
  );
}

/** 图例。 */
export function DonutLegend({ slices }: { slices: DonutSlice[] }) {
  return (
    <div className="flex flex-col gap-1.5">
      {slices.map((s) => (
        <div key={s.label} className="flex items-center gap-2 text-xs text-fg-secondary">
          <span className="h-2.5 w-2.5 rounded-[3px]" style={{ background: s.color }} />
          <span className="flex-1">{s.label}</span>
          <span className="tnum text-muted">{s.value}</span>
        </div>
      ))}
    </div>
  );
}
