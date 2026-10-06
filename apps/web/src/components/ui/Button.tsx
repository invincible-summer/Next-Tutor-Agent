"use client";
import { cn } from "@/lib/cn";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { DEMO_MODE, demoReadOnly } from "@/lib/demo";

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

// ghost 变体的色调：plain 与原行为一致；accent/danger 用于行内图标按钮
// （悬停显现黛青/朱砂语义），与 variant 记录同类替换，避免 class 冲突。
const GHOST_TONES = {
  plain: "text-fg-secondary hover:bg-surface-hover",
  accent: "text-muted hover:bg-accent-soft hover:text-accent",
  danger: "text-muted hover:bg-danger/10 hover:text-danger",
} as const;

/** ghost 的选中态（如面板/图谱开关）：accent-soft 底 + accent-strong 字。 */
const GHOST_SELECTED = "bg-accent-soft text-accent-strong hover:bg-accent-soft hover:text-accent-strong";

// 纯图标按钮：视觉保持紧凑，hit area 不小于 32px（触摸可靠），
// 取代各页面手写的 <button className="p-1"> 图标按钮。
const ICON_SIZES = {
  sm: "h-8 w-8 px-0 text-xs",
  md: "h-8.5 w-8.5 px-0 text-sm",
  lg: "h-10 w-10 px-0 text-sm",
};

/** 全站按钮。iconOnly 时传 icon + aria-label，尺寸取方形 hit area。 */
export function Button({
  variant = "primary",
  size = "md",
  className,
  children,
  icon,
  iconOnly = false,
  demoWrite = false,
  tone = "plain",
  selected = false,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: keyof typeof SIZES;
  icon?: ReactNode;
  /** 纯图标方形按钮（需配合 aria-label）；hit area ≥32px。 */
  iconOnly?: boolean;
  /** Persisted data or AI action, unavailable in the static showcase. */
  demoWrite?: boolean;
  /** ghost 变体的悬停色调（accent=黛青 / danger=朱砂），其余变体忽略。 */
  tone?: keyof typeof GHOST_TONES;
  /** ghost 变体的选中态（切换类按钮），其余变体忽略。 */
  selected?: boolean;
}) {
  const variantCls = variant === "ghost"
    ? cn("bg-transparent border border-transparent", selected ? GHOST_SELECTED : GHOST_TONES[tone])
    : VARIANTS[variant];
  return (
    <button
      aria-pressed={variant === "ghost" && selected ? true : undefined}
      className={cn(
        "inline-flex cursor-pointer items-center justify-center rounded-[8px] font-medium transition-all duration-200",
        "active:scale-[0.97] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40",
        "disabled:cursor-not-allowed disabled:opacity-50 disabled:active:scale-100",
        variantCls,
        iconOnly ? ICON_SIZES[size] : SIZES[size],
        className,
      )}
      {...rest}
      disabled={rest.disabled}
      onClick={(event) => {
        if (DEMO_MODE && (demoWrite || variant === "danger")) {
          event.preventDefault();
          event.stopPropagation();
          demoReadOnly();
          return;
        }
        rest.onClick?.(event);
      }}
    >
      {icon}
      {children}
    </button>
  );
}
