import { cn } from "@/lib/cn";
import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "outline" | "ghost" | "danger" | "accent2";

const VARIANTS: Record<Variant, string> = {
  primary:
    "bg-accent text-white hover:bg-accent-strong border border-transparent shadow-sm hover:shadow-md",
  outline:
    "bg-surface text-fg-secondary border border-border hover:border-accent hover:text-accent",
  ghost: "bg-transparent text-fg-secondary border border-transparent hover:bg-surface-hover",
  danger: "bg-surface text-danger border border-danger/40 hover:bg-danger/10",
  accent2: "bg-accent2 text-white hover:bg-accent2-strong border border-transparent shadow-sm hover:shadow-md",
};

const SIZES = {
  sm: "h-7 px-2.5 text-xs gap-1.5",
  md: "h-8.5 px-3.5 text-sm gap-2",
  lg: "h-10 px-5 text-sm gap-2",
};

/** 全站按钮。 */
export function Button({
  variant = "primary",
  size = "md",
  className,
  children,
  icon,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: keyof typeof SIZES;
  icon?: ReactNode;
}) {
  return (
    <button
      className={cn(
        "inline-flex cursor-pointer items-center justify-center rounded-[8px] font-medium transition-all duration-200",
        "active:scale-[0.97] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40",
        "disabled:cursor-not-allowed disabled:opacity-50 disabled:active:scale-100",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...rest}
    >
      {icon}
      {children}
    </button>
  );
}
