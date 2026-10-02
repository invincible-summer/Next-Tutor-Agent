"use client";
// 侧栏拖宽手柄：贴在边栏内缘，pointer capture 拖拽调宽，双击复位默认宽。
// 拖拽期间给 body 加 select-none，避免划过文本时触发选择。
import { useRef } from "react";
import { cn } from "@/lib/cn";

export function PanelResizer({
  side,
  width,
  onResize,
  onReset,
  className,
}: {
  /** 边栏在屏幕哪一侧：left = 左栏（拖右变宽），right = 右栏（拖左变宽）。 */
  side: "left" | "right";
  width: number;
  onResize: (width: number) => void;
  onReset: () => void;
  className?: string;
}) {
  const startX = useRef(0);
  const startWidth = useRef(0);
  const dragging = useRef(false);

  const stop = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging.current) return;
    dragging.current = false;
    e.currentTarget.releasePointerCapture(e.pointerId);
    document.body.classList.remove("select-none", "cursor-col-resize");
  };

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      onPointerDown={(e) => {
        e.preventDefault();
        dragging.current = true;
        startX.current = e.clientX;
        startWidth.current = width;
        e.currentTarget.setPointerCapture(e.pointerId);
        document.body.classList.add("select-none", "cursor-col-resize");
      }}
      onPointerMove={(e) => {
        if (!dragging.current) return;
        const dx = e.clientX - startX.current;
        onResize(side === "left" ? startWidth.current + dx : startWidth.current - dx);
      }}
      onPointerUp={stop}
      onPointerCancel={stop}
      onDoubleClick={onReset}
      title={undefined}
      className={cn(
        "group relative z-10 w-1.5 shrink-0 cursor-col-resize bg-transparent transition-colors",
        "hover:bg-accent/30 active:bg-accent/50",
        side === "left" ? "ml-[-2px]" : "mr-[-2px]",
        className,
      )}
    >
      {/* 拖拽热区比视觉线宽：中线用伪元素收窄到 1px */}
      <span className="pointer-events-none absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-border group-hover:bg-accent/50" />
    </div>
  );
}
