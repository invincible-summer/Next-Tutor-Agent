"use client";

import { useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/cn";

/** 每页条数：所有任务类列表统一 5 条/页，防止页面无界拉长。 */
export const PAGE_SIZE = 5;

/** 客户端分页切片。page 为 0 基。 */
export function paged<T>(items: T[], page: number, per: number = PAGE_SIZE): T[] {
  return items.slice(page * per, page * per + per);
}

export function pageCount(total: number, per: number = PAGE_SIZE): number {
  return Math.max(1, Math.ceil(total / per));
}

/** 迷你分页条：‹ [页码输入]/总页数 ›。页数 ≤1 时不渲染。
 *  页码可直接输入（Enter 或失焦提交），自动钳位到 [1, pages]。 */
export function Pager({
  page,
  total,
  onPage,
  per = PAGE_SIZE,
  className,
}: {
  page: number;
  total: number;
  onPage: (p: number) => void;
  per?: number;
  className?: string;
}) {
  // draft=null 表示未在编辑，输入框跟随当前页；编辑中以 draft 为准。
  const [draft, setDraft] = useState<string | null>(null);
  const pages = pageCount(total, per);
  if (pages <= 1) return null;
  const cur = Math.min(page, pages - 1);

  const commit = () => {
    if (draft === null) return;
    const n = parseInt(draft, 10);
    if (!Number.isNaN(n)) onPage(Math.min(pages, Math.max(1, n)) - 1);
    setDraft(null);
  };

  const btn =
    "cursor-pointer rounded-[6px] p-1 text-muted transition-colors hover:bg-surface-hover hover:text-fg disabled:cursor-default disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-muted";
  return (
    <div className={cn("flex items-center justify-end gap-1 pt-1.5 text-[0.68rem] text-muted", className)}>
      <button type="button" className={btn} disabled={cur <= 0}
        onClick={() => onPage(cur - 1)} aria-label="prev">
        <ChevronLeft size={13} />
      </button>
      <input
        value={draft ?? String(cur + 1)}
        onChange={(e) => setDraft(e.target.value.replace(/[^0-9]/g, ""))}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit();
            (e.target as HTMLInputElement).blur();
          } else if (e.key === "Escape") {
            setDraft(null);
            (e.target as HTMLInputElement).blur();
          }
        }}
        inputMode="numeric"
        aria-label="页码"
        className="tnum w-9 rounded-[5px] border border-border bg-surface px-1 py-0.5 text-center text-[0.68rem] text-fg outline-none focus:border-accent"
      />
      <span className="tnum pr-1">/ {pages}</span>
      <button type="button" className={btn} disabled={cur >= pages - 1}
        onClick={() => onPage(cur + 1)} aria-label="next">
        <ChevronRight size={13} />
      </button>
    </div>
  );
}
