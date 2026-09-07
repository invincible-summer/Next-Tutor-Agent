import { cn } from "@/lib/cn";
import type { ReactNode } from "react";

/** 纸面卡片：全站基础容器。 */
export function Card({
  children,
  className,
  pad = true,
  hover = false,
  onClick,
}: {
  children: ReactNode;
  className?: string;
  pad?: boolean;
  hover?: boolean;
  onClick?: () => void;
}) {
  return (
    <div
      onClick={onClick}
      className={cn(
        "rounded-[10px] border border-border bg-surface shadow-sm",
        pad && "p-4",
        hover && "cursor-pointer transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md",
        onClick && "cursor-pointer",
        className,
      )}    >
      {children}
    </div>
  );
}

/** 卡片标题行：icon + 标题 + 右侧操作区。 */
export function CardHeader({
  icon,
  title,
  desc,
  right,
}: {
  icon?: ReactNode;
  title: ReactNode;
  desc?: ReactNode;
  right?: ReactNode;
}) {
  return (
    <div className="mb-3 flex items-start justify-between gap-3">
      <div className="flex items-start gap-2.5">
        {icon && <div className="mt-0.5 text-accent">{icon}</div>}
        <div>
          <div className="text-sm font-semibold text-fg">{title}</div>
          {desc && <div className="mt-0.5 text-xs text-muted">{desc}</div>}
        </div>
      </div>
      {right}
    </div>
  );
}
