import { cn } from "@/lib/cn";
import type { InputHTMLAttributes, ReactNode, TextareaHTMLAttributes } from "react";

/**
 * 全站表单控件统一样式。聚焦光环由 globals.css 的 input:focus 全局规则
 * 提供，这里补齐 hover 边色、占位色与禁用态。
 */
export const INPUT_CLS =
  "w-full rounded-[8px] border border-border bg-surface px-3 py-2.5 text-sm text-fg outline-none transition-colors placeholder:text-muted hover:border-muted/70 focus:border-accent disabled:cursor-not-allowed disabled:opacity-60";

/** 紧凑控件（h-9，下拉/行内输入用），SubjectSelect 等共用。 */
export const FIELD_CLS =
  "h-9 w-full rounded-[8px] border border-border bg-surface px-2.5 text-sm text-fg outline-none transition-colors placeholder:text-muted hover:border-muted/70 focus:border-accent disabled:cursor-not-allowed disabled:opacity-60";

export const LABEL_CLS = "mb-1.5 block text-xs font-medium text-fg-secondary";

/** 文本输入框。 */
export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(INPUT_CLS, className)} {...rest} />;
}

/** 多行输入框。 */
export function Textarea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn(INPUT_CLS, className)} {...rest} />;
}

/** 标签 + 控件的字段容器。 */
export function Field({
  label,
  children,
  className,
}: {
  label: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={className}>
      <label className={LABEL_CLS}>{label}</label>
      {children}
    </div>
  );
}
