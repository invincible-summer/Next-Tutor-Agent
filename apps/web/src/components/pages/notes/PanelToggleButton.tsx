"use client";
// 侧栏折叠按钮：随开合状态切换图标，嵌入中栏头部使用（VSCode 式低调入口）。
import {
  PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen,
} from "lucide-react";
import { cn } from "@/lib/cn";

export function PanelToggleButton({
  side,
  open,
  onToggle,
  label,
  className,
}: {
  side: "left" | "right";
  open: boolean;
  onToggle: () => void;
  label: string;
  className?: string;
}) {
  const Icon = side === "left"
    ? (open ? PanelLeftClose : PanelLeftOpen)
    : (open ? PanelRightClose : PanelRightOpen);
  return (
    <button
      onClick={onToggle}
      title={label}
      aria-label={label}
      className={cn(
        "shrink-0 cursor-pointer rounded-md p-1.5 text-muted transition-colors hover:bg-surface-hover hover:text-accent",
        className,
      )}
    >
      <Icon size={15} />
    </button>
  );
}
