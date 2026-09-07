import { cn } from "@/lib/cn";

export interface RadarAxis {
  label: string;
  value: number; // 0..1
}

/** 雷达图：学科掌握度等 3-8 维数据。纯 SVG，主题感知。 */
export function Radar({
  axes,
  size = 220,
  className,
}: {
  axes: RadarAxis[];
  size?: number;
  className?: string;
}) {
  const cx = size / 2;
  const cy = size / 2;
  const r = size / 2 - 34;
  const n = Math.max(axes.length, 3);
  const pt = (i: number, v: number) => {
    const a = (Math.PI * 2 * i) / n - Math.PI / 2;
    return [cx + Math.cos(a) * r * v, cy + Math.sin(a) * r * v] as const;
  };
  const ring = (v: number) =>
    axes.map((_, i) => pt(i, v).join(",")).join(" ");
  const data = axes.map((ax, i) => pt(i, Math.max(0.03, Math.min(1, ax.value))).join(",")).join(" ");
  return (
    <svg viewBox={`0 0 ${size} ${size}`} className={cn("h-auto w-full", className)} role="img">
      {[0.25, 0.5, 0.75, 1].map((v) => (
        <polygon key={v} points={ring(v)} fill="none" stroke="rgb(var(--border))" strokeWidth={1} />
      ))}
      {axes.map((ax, i) => {
        const [x, y] = pt(i, 1);
        const [lx, ly] = pt(i, 1.22);
        return (
          <g key={ax.label}>
            <line x1={cx} y1={cy} x2={x} y2={y} stroke="rgb(var(--border))" strokeWidth={1} />
            <text
              x={lx}
              y={ly}
              textAnchor="middle"
              dominantBaseline="middle"
              fontSize={11}
              fill="rgb(var(--fg-secondary))"
            >
              {ax.label}
            </text>
          </g>
        );
      })}
      <polygon points={data} fill="rgb(var(--accent) / 0.22)" stroke="rgb(var(--accent))" strokeWidth={1.5} />
      {axes.map((ax, i) => {
        const [x, y] = pt(i, Math.max(0.03, Math.min(1, ax.value)));
        return <circle key={i} cx={x} cy={y} r={3} fill="rgb(var(--accent))" />;
      })}
    </svg>
  );
}
