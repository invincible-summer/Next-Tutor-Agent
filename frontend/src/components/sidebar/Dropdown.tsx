"use client";
import { useEffect, useRef, type RefObject } from "react";
import type { LucideIcon } from "lucide-react";
import { AnchoredPopover } from "@/components/ui/AnchoredPopover";

/** Close whatever is open when the user presses outside `ref`. */
export function useClickOutside<T extends HTMLElement>(
  onClose: () => void,
  active: boolean,
): RefObject<T | null> {
  const ref = useRef<T>(null);
  useEffect(() => {
    if (!active) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [active, onClose]);
  return ref;
}

export interface DropdownItem {
  icon: LucideIcon;
  label: string;
  danger?: boolean;
  dividerBefore?: boolean;
  /** 悬停提示（如上传入口注明支持的格式） */
  title?: string;
  onClick: () => void;
}

/** 行菜单面板：portal 锚定到 trigger（`anchorRef` 指向其 relative 包裹层），
 *  滚动/缩放实时跟随、不再被滚动容器裁剪；外点关闭由浮层自理。 */
export function Dropdown({
  items,
  onClose,
  anchorRef,
}: {
  items: DropdownItem[];
  onClose: () => void;
  anchorRef: RefObject<HTMLElement | null>;
}) {
  return (
    <AnchoredPopover
      anchorRef={anchorRef}
      open
      onClose={onClose}
      placement="bottom-end"
      className="z-50 w-44 rounded-[10px] border border-border bg-surface p-1 shadow-lg"
    >
      {items.map((item, i) => (
        <div key={i}>
          {item.dividerBefore && <div className="border-t border-border-light my-0.5" />}
          <button
            onClick={(e) => {
              e.stopPropagation();
              onClose();
              item.onClick();
            }}
            title={item.title}
            className={`flex items-center gap-2 w-full rounded-md px-2 py-1.5 text-xs transition-colors ${
              item.danger ? "text-danger hover:bg-danger/10" : "text-fg-secondary hover:bg-surface-hover"
            }`}
          >
            <item.icon size={12} /> {item.label}
          </button>
        </div>
      ))}
    </AnchoredPopover>
  );
}
