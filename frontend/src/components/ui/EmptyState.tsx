import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/** 空状态：图标 + 标题 + 描述 + 可选操作。 */
export function EmptyState({
  icon,
  title,
  desc,
  action,
  className,
}: {
  icon?: ReactNode;
  title: string;
  desc?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-2 rounded-[10px] border border-dashed border-border px-6 py-12 text-center",
        className,
      )}
    >
      {icon && (
        <div className="mb-1 flex h-11 w-11 items-center justify-center rounded-full bg-accent-soft/60 text-accent">
          {icon}
        </div>
      )}
      <div className="text-sm font-medium text-fg-secondary">{title}</div>
      {desc && <div className="max-w-md text-xs leading-relaxed text-muted">{desc}</div>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

/** 骨架块。 */
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton", className)} />;
}

/** 页面级加载骨架：标题 + 三卡。 */
export function PageSkeleton() {
  return (
    <div className="flex flex-col gap-4">
      <Skeleton className="h-8 w-48" />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Skeleton className="h-24" />
        <Skeleton className="h-24" />
        <Skeleton className="h-24" />
        <Skeleton className="h-24" />
      </div>
      <Skeleton className="h-64" />
    </div>
  );
}

/** 错误提示条。 */
export function ErrorNote({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-[8px] border border-danger/30 bg-danger/8 px-3 py-2 text-sm text-danger">
      <span>{message}</span>
      {retry && (
        <button onClick={retry} className="cursor-pointer text-xs font-medium underline">
          重试
        </button>
      )}
    </div>
  );
}
