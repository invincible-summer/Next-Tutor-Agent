import { cn } from "@/lib/cn";
import type { HTMLAttributes, ReactNode } from "react";

/** 纸面卡片：全站基础容器（透传 data-* 等原生属性作深链锚点）。 */
export function Card({
  children,
  className,
  pad = true,
  hover = false,
  onClick,
  ...rest
}: {
  children: ReactNode;
  className?: string;
  pad?: boolean;
  hover?: boolean;
  onClick?: () => void;
} & HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      onClick={onClick}
      {...rest}
      className={cn(
        // 默认 flat：只用 border 界定；hover/overlay 才升 elevation，
        // 避免 Dashboard 一类页面的「border + 明显 shadow」同权卡矩阵感。
        "rounded-[10px] border border-border bg-surface",
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
