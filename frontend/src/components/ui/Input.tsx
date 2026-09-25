import type { InputHTMLAttributes, ReactNode, TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

export const INPUT_CLS =
  "w-full rounded-[8px] border border-border bg-surface px-3 py-2.5 text-sm text-fg outline-none transition-colors placeholder:text-muted hover:border-muted/70 focus:border-accent disabled:cursor-not-allowed disabled:opacity-60";
export const FIELD_CLS =
  "h-9 w-full rounded-[8px] border border-border bg-surface px-2.5 text-sm text-fg outline-none transition-colors placeholder:text-muted hover:border-muted/70 focus:border-accent disabled:cursor-not-allowed disabled:opacity-60";
export const LABEL_CLS = "mb-1.5 block text-xs font-medium text-fg-secondary";

export function Input({ type, className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  const selection = type === "checkbox" || type === "radio";
  return (
    <input
      {...props}
      type={type}
      className={cn(
        selection
          ? "size-4 shrink-0 cursor-pointer accent-accent disabled:cursor-not-allowed disabled:opacity-60"
          : INPUT_CLS,
        className,
      )}
    />
  );
}

export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={cn(INPUT_CLS, className)} />;
}

export function Field({ label, children, className }: {
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