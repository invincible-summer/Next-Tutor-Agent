import { useMemo, useState } from "react";
import { ChevronRight, ScrollText } from "lucide-react";
import { cn } from "@/lib/cn";
import type { Lang } from "@/lib/i18n";
import { dt, modeTone } from "@/lib/labels";
import { fmtTime, relTime } from "@/lib/format";
import type { TeachingLogConcept } from "@/lib/types-modules";
import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Pager, paged, pageCount } from "@/components/ui/Pager";

type Tr = (key: string, fallback?: string) => string;

/** outcome 标签：correct/partial/wrong 走 dt(verdict.*)，engaged/unknown 用页面词条。 */
function outcomeLabel(lang: Lang, tr: Tr, outcome: string): string {
  return dt(lang, `verdict.${outcome}`, tr(`outcome.${outcome}`, outcome));
}

/** 教学日志卡：按概念分组（last_ts 倒序），行内展开时间线。 */
export function TeachingLog({
  concepts,
  lang,
  tr,
}: {
  concepts: Record<string, TeachingLogConcept>;
  lang: Lang;
  tr: Tr;
}) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [page, setPage] = useState(0);
  const rows = useMemo(
    () =>
      Object.entries(concepts)
        .map(([name, c]) => ({ name, ...c }))
        .sort((a, b) => b.last_ts - a.last_ts),
    [concepts],
  );
  // 概念行较紧凑：8 条/页；回源后条数变少时先钳位页码，避免停在空白页。
  const cur = Math.min(page, pageCount(rows.length, 8) - 1);
  const visible = paged(rows, cur, 8);

  const toggle = (name: string) =>
    setOpen((s) => {
      const next = new Set(s);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  return (
    <Card pad={false}>
      <div className="px-4 pt-4">
        <CardHeader icon={<ScrollText size={16} />} title={tr("log.title")} desc={tr("log.desc")} />
      </div>
      {rows.length === 0 ? (
        <div className="px-4 pb-4">
          <EmptyState title={tr("empty.title")} desc={tr("empty.desc")} />
        </div>
      ) : (
        <div className="flex flex-col divide-y divide-border-light">
          {visible.map((c) => {
            const expanded = open.has(c.name);
            return (
              <div key={c.name}>
                <button
                  onClick={() => toggle(c.name)}
                  className="flex w-full cursor-pointer items-center gap-2.5 px-4 py-2.5 text-left transition-colors hover:bg-surface-hover"
                >
                  <ChevronRight
                    size={14}
                    className={cn("shrink-0 text-muted transition-transform", expanded && "rotate-90")}
                  />
                  <span className="min-w-0 flex-1 truncate text-sm font-medium text-fg">
                    {c.name}
                  </span>
                  <Badge tone={modeTone(c.current_mode)}>
                    {dt(lang, `mode.${c.current_mode}`, c.current_mode)}
                  </Badge>
                  <span className="hidden shrink-0 text-xs text-fg-secondary sm:inline">
                    {outcomeLabel(lang, tr, c.current_outcome)}
                  </span>
                  <span className="hidden shrink-0 text-xs text-muted md:inline">
                    {relTime(c.last_ts, lang)}
                  </span>
                  <span className="tnum shrink-0 text-xs text-muted">
                    {c.entries.length} {tr("log.entries")}
                  </span>
                </button>
                {expanded && (
                  <div className="border-t border-border-light bg-surface-sunken/50 px-4 py-3">
                    <ol className="relative ml-1.5 flex flex-col gap-3 border-l border-border pl-4">
                      {c.entries.map((e, i) => (
                        <li key={`${e.ts}-${i}`} className="relative">
                          <span className="absolute top-1 -left-5 h-2 w-2 rounded-full border border-border bg-surface" />
                          <div className="flex flex-wrap items-center gap-2">
                            <Badge tone={modeTone(e.mode)}>{dt(lang, `mode.${e.mode}`, e.mode)}</Badge>
                            <span className="text-xs text-fg-secondary">
                              {outcomeLabel(lang, tr, e.outcome)}
                            </span>
                            <span className="tnum ml-auto text-xs text-muted">{fmtTime(e.ts)}</span>
                          </div>
                          {e.note && (
                            <div className="mt-1 text-xs leading-relaxed text-muted">{e.note}</div>
                          )}
                        </li>
                      ))}
                    </ol>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
      <Pager page={cur} total={rows.length} per={8} onPage={setPage}
        className="px-4 pb-3" />
    </Card>
  );
}
