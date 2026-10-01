"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { AudioLines, ChevronDown, LocateFixed, Play } from "lucide-react";
import { Markdown } from "@/components/chat/markdown";
import { cn } from "@/lib/cn";
import type { FlatSegment } from "@/lib/classroom/useClassroomPlayer";

export interface ScriptPanelStrings {
  pageOf: (order: number) => string;
  follow: string;
  play: string;
  hint: string;
}

export function ScriptPanel({ segments, slideTitles, slideOrders, current, onSelect, s, disabled = false, active = true }: {
  segments: FlatSegment[];
  slideTitles: Map<number, string>;
  slideOrders: number[];
  current: FlatSegment | null;
  onSelect: (segmentId: string) => void;
  s: ScriptPanelStrings;
  disabled?: boolean;
  active?: boolean;
}) {
  const [follow, setFollow] = useState(true);
  const groups = useMemo(() => slideOrders.map((order) => ({
    order, title: slideTitles.get(order) ?? "—",
    items: segments.filter((seg) => seg.slideOrder === order),
  })).filter((g) => g.items.length > 0), [segments, slideTitles, slideOrders]);
  const currentPageRef = useRef<HTMLDetailsElement | null>(null);
  useEffect(() => {
    if (follow && active) currentPageRef.current?.scrollIntoView({ block: "nearest" });
  }, [current?.slideOrder, follow, active]);

  return (
    <div className="space-y-3">
      <div className="sticky -top-4 z-10 flex items-center justify-between gap-2 border-b border-border-light bg-surface pb-3 pt-1">
        <span className="text-[11px] text-muted">{s.hint}</span>
        <button type="button" aria-pressed={follow} onClick={() => setFollow((v) => !v)} className={cn("inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1.5 text-[11px]", follow ? "bg-accent-soft text-accent-strong" : "bg-surface-hover text-muted")}><LocateFixed size={12} />{s.follow}</button>
      </div>
      {groups.map((group) => {
        const selected = group.order === current?.slideOrder;
        const text = group.items.map((seg) => seg.spokenText || seg.displayText).join("\n\n");
        return (
          <details key={group.order} ref={selected ? currentPageRef : undefined} data-script-page={group.order} className={cn("group scroll-m-16 rounded-xl border", selected ? "border-accent/30 bg-accent-soft/20" : "border-border-light bg-bg/40")}>
            <summary aria-current={selected ? "page" : undefined} className="flex cursor-pointer list-none items-start gap-2 px-3 py-3.5 text-xs font-medium leading-5 text-fg-secondary [&::-webkit-details-marker]:hidden">
              <span className="shrink-0 text-muted">{s.pageOf(group.order)}</span>
              <span className="min-w-0 flex-1 break-words">{group.title}</span>
              {selected && <AudioLines size={14} className="mt-0.5 shrink-0 text-accent" />}
              <ChevronDown size={14} className="mt-0.5 shrink-0 text-muted transition-transform group-open:rotate-180" />
            </summary>
            <div className="border-t border-border-light px-3 pb-4 pt-3">
              <button type="button" disabled={disabled} onClick={() => onSelect(group.items[0].segmentId)} className="mb-3 inline-flex items-center gap-1.5 text-[11px] text-accent-strong disabled:opacity-40"><Play size={12} />{s.play}</button>
              <Markdown className="chat-prose classroom-prose min-w-0 break-words [overflow-wrap:anywhere]">{text}</Markdown>
            </div>
          </details>
        );
      })}
    </div>
  );
}
