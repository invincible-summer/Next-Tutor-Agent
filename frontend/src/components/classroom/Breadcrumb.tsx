"use client";
/* 备课上课面包屑（Phase C：替代「对话|课堂」模式条）。
 * 备课上课(/course) › 学习区课程列表 › 当前页；真实链接，可中键新开。 */
import Link from "next/link";
import { ChevronRight } from "lucide-react";
import { useUIStore } from "@/lib/store";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/cn";
import { classroomPath } from "@/lib/classroom/paths";

export function ClassroomBreadcrumb({ workspaceId, listLabel, current,
  className }: {
  /** 提供时渲染第二级（学习区课程列表）。 */
  workspaceId?: string;
  /** 第二级文案：工作区名优先，缺数据时传列表页通用标签。 */
  listLabel?: string;
  /** 当前页（纯文本，aria-current=page）。 */
  current?: string;
  className?: string;
}) {
  const { lang } = useUIStore();
  const sep = (
    <ChevronRight size={12} className="shrink-0 text-muted/50" aria-hidden />
  );
  const crumbCls = "min-w-0 truncate transition-colors hover:text-fg-secondary";
  return (
    <nav
      aria-label="breadcrumb"
      className={cn(
        "flex min-w-0 items-center gap-1 text-xs text-muted",
        className,
      )}
    >
      <Link href="/course" prefetch={false} className={cn("shrink-0", crumbCls)}>
        {t(lang, "nav.course")}
      </Link>
      {listLabel && (
        <>
          {sep}
          {workspaceId ? (
            <Link href={classroomPath(workspaceId)} prefetch={false}
              className={crumbCls}>
              {listLabel}
            </Link>
          ) : (
            <span className="min-w-0 truncate">{listLabel}</span>
          )}
        </>
      )}
      {current && (
        <>
          {sep}
          <span aria-current="page"
            className="min-w-0 truncate text-fg-secondary">
            {current}
          </span>
        </>
      )}
    </nav>
  );
}
