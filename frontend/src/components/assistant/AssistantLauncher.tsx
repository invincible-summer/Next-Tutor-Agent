"use client";

// 收起态悬浮按钮（plan.md §3.1，A06）：56×56、右下 24px、圆角 20，
// 黛青底 + text-on-accent；Tooltip 悬停 200ms；未读小圆点；运行细环。
import { useEffect, useRef, useState } from "react";
import { MessageCircle } from "lucide-react";
import { cn } from "@/lib/cn";
import { useAssistantStore } from "@/lib/assistant/store";
import { stringsFor } from "./strings";

export function AssistantLauncher({
  bottomPx, className, buttonRef,
}: {
  bottomPx?: number;
  className?: string;
  buttonRef?: React.RefObject<HTMLButtonElement | null>;
}) {
  const mode = useAssistantStore((s) => s.mode);
  const unread = useAssistantStore((s) => s.unread);
  const turnState = useAssistantStore((s) => s.turnState);
  const lang = useAssistantStore((s) => s.lang);
  const open = useAssistantStore((s) => s.open);
  const [tooltip, setTooltip] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const localRef = useRef<HTMLButtonElement>(null);
  const t = stringsFor(lang);
  const running = ["accepting", "running", "reconnecting"].includes(turnState);

  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  if (mode !== "collapsed") return null;

  return (
    <div
      className={cn("assistant-launcher", className)}
      style={bottomPx !== undefined ? { bottom: bottomPx } : undefined}
      onMouseEnter={() => {
        timer.current = setTimeout(() => setTooltip(true), 200);
      }}
      onMouseLeave={() => {
        if (timer.current) clearTimeout(timer.current);
        setTooltip(false);
      }}
    >
      {tooltip && (
        <span role="tooltip" className="assistant-tooltip">
          {t.launcherTooltip}
        </span>
      )}
      <button
        ref={(node) => {
          localRef.current = node;
          if (buttonRef) buttonRef.current = node;
        }}
        type="button"
        aria-label={t.panelTitle}
        aria-expanded={false}
        onClick={() => {
          open();
          requestAnimationFrame(() =>
            (buttonRef?.current ?? localRef.current)?.focus());
        }}
        onFocus={() => setTooltip(true)}
        onBlur={() => setTooltip(false)}
        className={cn(
          "assistant-launcher-btn",
          running && "assistant-launcher-running",
        )}
      >
        <MessageCircle size={24} strokeWidth={1.8} aria-hidden />
        {unread && <span className="assistant-unread-dot" aria-label={t.unreadDot} />}
      </button>
    </div>
  );
}
