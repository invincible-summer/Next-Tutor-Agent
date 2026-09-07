"use client";
// 锚定浮层基元：portal 到 body + position:fixed 按锚点 rect 定位，
// 滚动（capture，可捕获任意内部滚动容器——本应用 body 不滚，滚动都
// 发生在 overflow 容器里）与视口缩放时实时重定位，下方空间不足自动
// 上翻。解决 absolute 浮层被滚动容器裁剪、滚动时不跟随锚点的问题。
import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/cn";

export type PopoverPlacement = "bottom-start" | "bottom-end" | "top-start" | "top-end";

const GAP = 6;

export function AnchoredPopover({
  anchorRef,
  open,
  onClose,
  placement = "bottom-end",
  matchAnchorWidth = false,
  className,
  style,
  children,
}: {
  /** 锚点元素 ref（通常是 trigger 的 relative 包裹层或 trigger 本身） */
  anchorRef: RefObject<HTMLElement | null>;
  open: boolean;
  onClose: () => void;
  placement?: PopoverPlacement;
  /** 浮层宽度与锚点对齐（如搜索结果面板跟随输入框宽） */
  matchAnchorWidth?: boolean;
  className?: string;
  style?: CSSProperties;
  children: ReactNode;
}) {
  const popRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number; width?: number } | null>(null);

  useLayoutEffect(() => {
    if (!open) return;
    const anchor = anchorRef.current;
    const pop = popRef.current;
    if (!anchor || !pop) return;
    const update = () => {
      const r = anchor.getBoundingClientRect();
      const w = matchAnchorWidth ? r.width : pop.offsetWidth;
      const h = pop.offsetHeight;
      // bottom* 默认在锚点下方展开；放不下且上方放得下才上翻
      const wantBottom = placement.startsWith("bottom");
      const flip = wantBottom && r.bottom + GAP + h > window.innerHeight && r.top - GAP - h >= 0;
      const top = wantBottom
        ? (flip ? r.top - GAP - h : r.bottom + GAP)
        : Math.max(GAP, r.top - GAP - h);
      const left = Math.min(
        Math.max(GAP, placement.endsWith("start") ? r.left : r.right - w),
        window.innerWidth - w - GAP,
      );
      setPos(matchAnchorWidth ? { top, left, width: r.width } : { top, left });
    };
    update();
    // scroll 事件不冒泡但可捕获：capture 在 window 上能收到任意内部
    // 滚动容器的滚动，被动监听不阻塞滚动本身。
    window.addEventListener("scroll", update, { capture: true, passive: true });
    window.addEventListener("resize", update, { passive: true });
    return () => {
      window.removeEventListener("scroll", update, { capture: true });
      window.removeEventListener("resize", update);
    };
  }, [open, anchorRef, placement, matchAnchorWidth]);

  // 外点关闭：浮层 portal 在 body、不在 anchor 的 DOM 子树里，两者都算"内"
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node;
      if (anchorRef.current?.contains(t)) return;
      if (popRef.current?.contains(t)) return;
      onClose();
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open, onClose, anchorRef]);

  if (!open) return null;
  return createPortal(
    <div
      ref={popRef}
      className={cn("motion-pop", className)}
      style={{
        position: "fixed",
        top: pos?.top ?? 0,
        left: pos?.left ?? 0,
        width: pos?.width,
        visibility: pos ? undefined : "hidden",
        ...style,
      }}
    >
      {children}
    </div>,
    document.body,
  );
}
