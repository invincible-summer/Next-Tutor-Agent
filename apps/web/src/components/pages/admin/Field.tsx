"use client";
import type { ReactNode } from "react";
import { Hint } from "@/components/ui/Hint";

/** 管理台表单字段：标签 +（可选问号悬浮提示）+ 控件 +（可选常驻帮助小字）。 */
export function Field({ label, helper, hint, children }: {
  label: string;
  helper?: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="flex min-w-0 flex-col gap-1.5">
      <span className="flex items-center gap-1 text-xs text-muted">
        {label}
        {hint && <Hint text={hint} />}
      </span>
      {children}
      {helper && <span className="text-[0.6875rem] leading-snug text-muted">{helper}</span>}
    </label>
  );
}

/** 管理台面板共享的词条查询函数类型（makePageT 的返回值）。 */
export type Tr = (key: string, fallback?: string) => string;

/** 面板共享的输入框样式。 */
export const inputCls = "tnum h-8 w-full rounded-[8px] border border-border bg-surface px-2 text-sm text-fg outline-none transition-colors focus:border-accent";
