"use client";
import { useMemo, useState } from "react";
import { BookOpen, ClipboardCheck, Dot, Target } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Pager } from "@/components/ui/Pager";
import { Progress } from "@/components/ui/Progress";
import type { Lang } from "@/lib/i18n";
import { dayKey, fmtTime } from "@/lib/format";
import { dt } from "@/lib/labels";
import type { Episode } from "@/lib/types-modules";

type Tr = (key: string, fallback?: string) => string;

function EventIcon({ type }: { type: string }) {
  const cls = "h-4 w-4";
  switch (type) {
    case "concept_taught":
      return <BookOpen className={cls} />;
    case "quiz_graded":
      return <ClipboardCheck className={cls} />;
    case "goal_set":
      return <Target className={cls} />;
    default:
      return <Dot className={cls} />;
  }
}

function fmtScore(score: number): string {
  return score >= 0 && score <= 1 ? `${Math.round(score * 100)}%` : String(score);
}

/** 情景记忆时间线：按日分组（新→旧），一页一天；「加载更多」向服务器取更早的分组。 */
export function EpisodeTimeline({
  episodes,
  hasMore,
  loadingMore,
  onLoadMore,
  lang,
  tr,
}: {
  episodes: Episode[];
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
  lang: Lang;
  tr: Tr;
}) {
  const [page, setPage] = useState(0);
  const groups = useMemo(() => {
    const m = new Map<string, Episode[]>();
    for (const e of episodes) {
      const k = dayKey(e.ts);
      const arr = m.get(k);
      if (arr) arr.push(e);
      else m.set(k, [e]);
    }
    return [...m.entries()];
  }, [episodes]);

  if (episodes.length === 0) {
    return <EmptyState title={tr("mem.episodes.empty")} desc={tr("mem.episodes.emptyDesc")} />;
  }

  // 一页一个日期分组；加载更多后分组数增加，页码钳位防越界。
  const cur = Math.min(page, groups.length - 1);
  const day = groups[cur];

  return (
    <div className="flex flex-col gap-4">
      {day && (
        <section key={day[0]}>
          <div className="mb-2 text-xs font-semibold tracking-wide text-muted tnum">{day[0]}</div>
          <div className="flex flex-col gap-2">
            {day[1].map((e) => (
              <div
                key={e.id}
                className="flex gap-3 rounded-[10px] border border-border bg-surface p-3 shadow-sm"
              >
                <div className="mt-0.5 shrink-0 text-accent">
                  <EventIcon type={e.event_type} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-xs font-medium text-fg-secondary">
                      {dt(lang, `event.${e.event_type}`, e.event_type)}
                    </span>
                    {e.subject && <Badge tone="accent">{e.subject}</Badge>}
                    {e.scope && <Badge tone="outline">{e.scope}</Badge>}
                    <span className="ml-auto shrink-0 text-xs text-muted tnum">{fmtTime(e.ts)}</span>
                  </div>
                  <p className="mt-1 text-sm leading-relaxed text-fg">{e.summary}</p>
                  {e.importance > 0 && (
                    <div className="mt-2 flex items-center gap-2">
                      <span className="shrink-0 text-[11px] text-muted">{tr("mem.importance")}</span>
                      <Progress value={e.importance} height={4} className="max-w-[140px]" tone="muted" />
                    </div>
                  )}
                </div>
                {e.score != null && (
                  <div className="shrink-0 self-center pl-2 text-lg font-semibold text-fg tnum">
                    {fmtScore(e.score)}
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>
      )}
      <div className="flex items-center justify-between gap-2">
        {hasMore ? (
          <Button variant="outline" size="sm" onClick={onLoadMore} disabled={loadingMore}>
            {loadingMore ? tr("mem.loadingMore") : tr("mem.loadMore")}
          </Button>
        ) : (
          <span />
        )}
        <Pager page={cur} total={groups.length} per={1} onPage={setPage} className="pt-0" />
      </div>
    </div>
  );
}
