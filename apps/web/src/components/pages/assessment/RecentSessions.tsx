"use client";

// 近期练习会话卡：listSessions 过滤 quiz_count>0，分页展示。
import { useState } from "react";
import Link from "next/link";
import { ChevronRight, History } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { EmptyState, ErrorNote, Skeleton } from "@/components/ui/EmptyState";
import { Pager, paged, pageCount } from "@/components/ui/Pager";
import { relTime } from "@/lib/format";
import type { Lang } from "@/lib/i18n";
import type { SessionItem } from "@/lib/types";
import type { PageTr } from "./common";

const PER_PAGE = 8;

export function RecentSessions({
  tr,
  lang,
  sessions,
  loading,
  error,
  onRetry,
}: {
  tr: PageTr;
  lang: Lang;
  sessions: SessionItem[] | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}) {
  const [page, setPage] = useState(0);
  const list = sessions ?? [];
  // 回源后条数可能变少：先钳位页码，避免停在空白页。
  const cur = Math.min(page, pageCount(list.length, PER_PAGE) - 1);
  const rows = paged(list, cur, PER_PAGE);

  return (
    <Card>
      <CardHeader icon={<History size={16} />} title={tr("recent.title")} desc={tr("recent.desc")} />
      {loading ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-11" />
          <Skeleton className="h-11" />
          <Skeleton className="h-11" />
        </div>
      ) : error ? (
        <ErrorNote message={error} retry={onRetry} />
      ) : list.length === 0 ? (
        <EmptyState title={tr("recent.empty")} />
      ) : (
        <>
          <div className="flex flex-col divide-y divide-border-light">
            {rows.map((s) => (
              <Link
                key={s.session_id}
                href={`/chat/${encodeURIComponent(s.session_id)}`}
                className="group flex items-center gap-3 rounded-[8px] px-2 py-2.5 transition-colors hover:bg-surface-hover"
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm text-fg">{s.title || s.session_id}</div>
                  <div className="mt-0.5 text-xs text-muted">{relTime(s.updated_at, lang)}</div>
                </div>
                <Badge tone="accent" className="tnum shrink-0">
                  {s.quiz_count} {tr("recent.quizUnit")}
                </Badge>
                <ChevronRight
                  size={15}
                  className="shrink-0 text-muted transition-transform group-hover:translate-x-0.5 group-hover:text-accent"
                />
              </Link>
            ))}
          </div>
          <Pager page={cur} total={list.length} per={PER_PAGE} onPage={setPage} />
        </>
      )}
    </Card>
  );
}
