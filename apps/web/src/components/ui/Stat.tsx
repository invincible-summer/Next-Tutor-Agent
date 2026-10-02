import { cn } from "@/lib/cn";
import type { ReactNode } from "react";
import { Card } from "./Card";

/** 顶部统计卡：大数字 + 标签 + 图标 + 可选脚注。 */
export function Stat({
  icon,
  label,
  value,
  unit,
  foot,
  tone = "default",
}: {
  icon?: ReactNode;
  label: string;
  value: ReactNode;
  unit?: string;
  foot?: ReactNode;
  tone?: "default" | "accent" | "accent2" | "success" | "warning" | "danger";
}) {
  const toneCls = {
    default: "text-fg",
    accent: "text-accent",
    accent2: "text-accent2",
    success: "text-success",
    warning: "text-warning",
    danger: "text-danger",
  }[tone];
  return (
    <Card className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted">{label}</span>
        {icon && <span className="text-muted">{icon}</span>}
      </div>
      <div className={cn("tnum text-2xl font-semibold leading-none", toneCls)}>
        {value}
        {unit && <span className="ml-1 text-sm font-normal text-muted">{unit}</span>}
      </div>
      {foot && <div className="text-xs text-muted">{foot}</div>}
    </Card>
  );
}
