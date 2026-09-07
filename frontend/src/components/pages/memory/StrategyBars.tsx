"use client";
import { useMemo, useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Pager, paged, pageCount } from "@/components/ui/Pager";
import { Bars, type BarItem } from "@/components/charts/Bars";
import type { Lang } from "@/lib/i18n";
import { dt, masteryColor } from "@/lib/labels";
import type { ProceduralStrategy } from "@/lib/types-modules";

type Tr = (key: string, fallback?: string) => string;

/** 每页 8 条策略条形。 */
const PER_PAGE = 8;

/** 程序性记忆：各教学策略的成功率条形列表。 */
export function StrategyBars({
  strategies,
  lang,
  tr,
}: {
  strategies: ProceduralStrategy[];
  lang: Lang;
  tr: Tr;
}) {
  const [page, setPage] = useState(0);
  const items: BarItem[] = useMemo(
    () =>
      [...strategies]
        .sort((a, b) => b.success_rate - a.success_rate)
        .map((s) => ({
          label: dt(lang, `mode.${s.strategy}`, s.strategy),
          value: s.success_rate,
          display: `${Math.round(s.success_rate * 100)}%`,
          hint: `${s.subject} · ${s.scope} · ${s.trials} ${tr("mem.trials")}`,
          color: masteryColor(s.success_rate),
        })),
    [strategies, lang, tr],
  );

  if (strategies.length === 0) {
    return <EmptyState title={tr("mem.procedural.empty")} desc={tr("mem.procedural.emptyDesc")} />;
  }

  const cur = Math.min(page, pageCount(items.length, PER_PAGE) - 1);
  const visible = paged(items, cur, PER_PAGE);
  const subjects = [...new Set(strategies.map((s) => s.subject).filter(Boolean))];
  const scopes = [...new Set(strategies.map((s) => s.scope).filter(Boolean))];

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-1.5">
        {subjects.map((s) => (
          <Badge key={s} tone="accent">
            {s}
          </Badge>
        ))}
        {scopes.map((s) => (
          <Badge key={s} tone="outline">
            {s}
          </Badge>
        ))}
      </div>
      <Bars items={visible} />
      <Pager page={cur} total={items.length} per={PER_PAGE} onPage={setPage} className="pt-0" />
      <p className="text-xs leading-relaxed text-muted">{tr("mem.trialsNote")}</p>
    </div>
  );
}
