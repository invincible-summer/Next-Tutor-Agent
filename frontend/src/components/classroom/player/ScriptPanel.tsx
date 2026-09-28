"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { AudioLines, LocateFixed, Play } from "lucide-react";
import { Markdown } from "@/components/chat/markdown";
import { cn } from "@/lib/cn";
import type { FlatSegment } from "@/lib/classroom/useClassroomPlayer";

export interface ScriptPanelStrings {
  pageOf: (order: number) => string;
  follow: string;
  play: string;
  hint: string;
}

export function ScriptPanel({ segments, slideTitles, slideOrders, current, onSelect, s }: {
  segments: FlatSegment[];
  slideTitles: Map<number, string>;
  slideOrders: number[];
  current: FlatSegment | null;
  onSelect: (segmentId: string) => void;
  s: ScriptPanelStrings;
}) {
  const [follow, setFollow] = useState(true);
  const groups = useMemo(() => slideOrders.map((order) => ({
    order, title: slideTitles.get(order) ?? "—",
    items: segments.filter((seg) => seg.slideOrder === order),
  })).filter((g) => g.items.length > 0), [segments, slideTitles, slideOrders]);
  const currentRowRef = useRef<HTMLLIElement | null>(null);
  useEffect(() => {
    if (follow) currentRowRef.current?.scrollIntoView({ block: "nearest" });
  }, [current?.segmentId, follow]);

  return (
    <div className="space-y-6">
      <div className="sticky -top-4 z-10 -mx-1 flex items-center justify-between gap-2 border-b border-border-light bg-surface px-1 pb-3 pt-1">
        <span className="text-[11px] text-muted">{s.hint}</span>
        <button type="button" aria-pressed={follow} onClick={() => setFollow((v) => !v)} className={cn("inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1.5 text-[11px]", follow ? "bg-accent-soft text-accent-strong" : "bg-surface-hover text-muted")}><LocateFixed size={12} />{s.follow}</button>
      </div>
      {groups.map((group) => (
        <section key={group.order}>
          <h3 className="mb-3 flex items-start gap-2.5 text-xs font-semibold leading-5 text-fg-secondary">
            <span className="tnum shrink-0 text-[10px] font-medium tracking-wider text-muted">{String(group.order).padStart(2, "0")}</span>
            <span>{group.title}</span>
          </h3>
          <ol className="space-y-2.5">
            {group.items.map((seg) => {
              const active = seg.seq === current?.seq;
              const seconds = Math.max(0, Math.round(seg.estimatedMs / 1000));
              return (
                <li key={seg.segmentId} ref={active ? currentRowRef : undefined} aria-current={active ? "step" : undefined} className={cn("script-segment scroll-m-16 rounded-xl border p-3.5", active ? "border-accent/25 bg-accent-soft/35" : "border-transparent bg-bg/50")}>
                  <div className="mb-2 flex items-center gap-2 text-[10px] text-muted">
                    {active ? <AudioLines size={13} className="text-accent" /> : <span className="tnum">{String(seg.seq + 1).padStart(2, "0")}</span>}
                    <span className="tnum">{s.pageOf(group.order)}{seconds > 0 ? ` · ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}` : ""}</span>
                    <button type="button" onClick={() => onSelect(seg.segmentId)} title={s.play} aria-label={`${s.play} · ${seg.seq + 1}`} className="ml-auto flex h-7 w-7 items-center justify-center rounded-full text-accent-strong transition-colors hover:bg-accent-soft"><Play size={12} /></button>
                  </div>
                  <Markdown className="chat-prose classroom-prose">{seg.displayText || seg.spokenText}</Markdown>
                </li>
              );
            })}
          </ol>
        </section>
      ))}
    </div>
  );
}
