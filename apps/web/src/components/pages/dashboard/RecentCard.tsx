import { useMemo, useState } from "react";
import Link from "next/link";
import { History } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/Card";
import { Badge, ModuleBadge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Pager, paged, pageCount } from "@/components/ui/Pager";
import { relTime } from "@/lib/format";
import { dt, modeTone } from "@/lib/labels";
import type { Lang } from "@/lib/i18n";
import type { TeachingLogResp } from "@/lib/types-modules";
import type { Tr } from "./shared";

/** 最近学习：teaching-log 按 last_ts 排序，分页展示。 */
export function RecentCard({
  teachingLog,
  lang,
  tr,
}: {
  teachingLog: TeachingLogResp;
  lang: Lang;
  tr: Tr;
}) {
  const [page, setPage] = useState(0);
  const disabled = teachingLog.status === "disabled";
  const rows = useMemo(
    () =>
      Object.entries(teachingLog.concepts ?? {})
        .map(([concept, c]) => ({ concept, ...c }))
        .sort((a, b) => b.last_ts - a.last_ts),
    [teachingLog],
  );
  const cur = Math.min(page, pageCount(rows.length) - 1);
  const visible = paged(rows, cur);

  return (
    <Card>
      <CardHeader
        icon={<History size={16} />}
        title={tr("recent.title")}
        desc={tr("recent.desc")}
        right={<ModuleBadge id="M3" />}
      />
      {disabled ? (
        <EmptyState title={tr("empty.recent")} desc={tr("empty.disabled")} />
      ) : rows.length === 0 ? (
        <EmptyState title={tr("empty.recent")} desc={tr("empty.recent.desc")} />
      ) : (
        <div className="-mx-2 flex flex-col">
          {visible.map((r) => (
            <Link
              key={r.concept}
              href="/knowledge"
              className="flex items-center justify-between gap-3 rounded-[8px] px-2 py-2 transition-colors hover:bg-surface-hover"
            >
              <div className="flex min-w-0 items-center gap-2">
                <span className="truncate text-sm text-fg">{r.concept}</span>
                <Badge tone={modeTone(r.current_mode)}>
                  {dt(lang, `mode.${r.current_mode}`, r.current_mode)}
                </Badge>
              </div>
              <span className="shrink-0 text-xs text-muted">{relTime(r.last_ts, lang)}</span>
            </Link>
          ))}
        </div>
      )}
      <Pager page={cur} total={rows.length} onPage={setPage} />
    </Card>
  );
}
