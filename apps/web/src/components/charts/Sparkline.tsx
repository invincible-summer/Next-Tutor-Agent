import { cn } from "@/lib/cn";

/** 迷你面积趋势线：近 N 天活动量等。 */
export function Sparkline({
  values,
  width = 240,
  height = 56,
  className,
  tone = "accent",
}: {
  values: number[];
  width?: number;
  height?: number;
  className?: string;
  tone?: "accent" | "accent2" | "success" | "warning";
}) {
  if (values.length === 0) values = [0];
  const max = Math.max(...values, 1);
  const step = width / Math.max(values.length - 1, 1);
  const pts = values.map((v, i) => [i * step, height - 4 - (v / max) * (height - 10)] as const);
  const line = pts.map((p) => p.join(",")).join(" ");
  const area = `0,${height} ${line} ${width},${height}`;
  const VARS: Record<"accent" | "accent2" | "success" | "warning", string> = {
    accent: "var(--accent)",
    accent2: "var(--accent2)",
    success: "var(--success)",
    warning: "var(--warning)",
  };
  const color = VARS[tone];
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className={cn("h-auto w-full", className)} role="img">
      <polygon points={area} fill={`rgb(${color} / 0.14)`} />
      <polyline points={line} fill="none" stroke={`rgb(${color})`} strokeWidth={1.6} strokeLinejoin="round" />
      {pts.length > 0 && (
        <circle cx={pts[pts.length - 1][0]} cy={pts[pts.length - 1][1]} r={2.6} fill={`rgb(${color})`} />
      )}
    </svg>
  );
}
