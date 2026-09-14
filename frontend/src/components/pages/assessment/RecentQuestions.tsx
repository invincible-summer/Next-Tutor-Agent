"use client";

// 最近习题卡：跨会话汇集最近生成的题目（后端每学生上限 100 道），分页展示。
import { useState } from "react";
import Link from "next/link";
import { ChevronRight, ClipboardList } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { EmptyState, ErrorNote, Skeleton } from "@/components/ui/EmptyState";
import { Pager, paged, pageCount } from "@/components/ui/Pager";
import { dt, verdictTone } from "@/lib/labels";
import type { Lang } from "@/lib/i18n";
import type { RecentQuizQuestion } from "@/lib/types-modules";
import type { PageTr } from "./common";

/** 每页 8 条：行高约 52px，一页一屏内可读完。 */
const PER_PAGE = 8;

function fmtIso(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString();
}

/** 单行：可用会话→深链回对话；已删除/独立测评→纯行（§11.2 不编死深链）。 */
function RecentRow({
  q,
  tr,
  lang,
}: {
  q: RecentQuizQuestion;
  tr: PageTr;
  lang: Lang;
}) {
  const dead = !q.session_id || q.availability === "deleted";
  const body = (
    <>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm text-fg">{q.stem}</div>
        <div className="mt-0.5 flex items-center gap-2 text-xs text-muted">
          <span className="truncate">{q.knowledge_point || q.topic || "—"}</span>
          <span className="tnum shrink-0">{fmtIso(q.ts)}</span>
        </div>
        {dead && q.availability === "deleted" && (
          <div className="mt-1 text-[10px] text-danger">{tr("rq.source.deleted", "来源对话已删除，无法查看")}</div>
        )}
      </div>
      {q.type && (
        <Badge tone="outline" className="shrink-0">
          {tr(`rq.type.${q.type}`, q.type)}
        </Badge>
      )}
      {q.verdict ? (
        <Badge tone={verdictTone(q.verdict)} className="shrink-0">
          {dt(lang, `verdict.${q.verdict}`, q.verdict)}
        </Badge>
      ) : (
        <Badge tone="info" className="shrink-0">{tr("rq.unanswered")}</Badge>
      )}
    </>
  );
  if (dead) {
    return <div className="flex cursor-default items-center gap-3 rounded-[8px] px-2 py-2.5 opacity-70">{body}</div>;
  }
  return (
    <Link
      href={`/chat/${encodeURIComponent(q.session_id)}`}
      className="group flex items-center gap-3 rounded-[8px] px-2 py-2.5 transition-colors hover:bg-surface-hover"
    >
      {body}
      <ChevronRight
        size={15}
        className="shrink-0 text-muted transition-transform group-hover:translate-x-0.5 group-hover:text-accent"
      />
    </Link>
  );
}

export function RecentQuestions({
  tr,
  lang,
  questions,
  loading,
  error,
  onRetry,
}: {
  tr: PageTr;
  lang: Lang;
  questions: RecentQuizQuestion[] | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}) {
  const [page, setPage] = useState(0);
  const list = questions ?? [];
  // 判分/出题回源后条数可能变化：先钳位页码，避免停在空白页。
  const cur = Math.min(page, pageCount(list.length, PER_PAGE) - 1);
  const rows = paged(list, cur, PER_PAGE);

  return (
    <Card>
      <CardHeader icon={<ClipboardList size={16} />} title={tr("rq.title")} desc={tr("rq.desc")} />
      {loading ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-11" />
          <Skeleton className="h-11" />
          <Skeleton className="h-11" />
        </div>
      ) : error ? (
        <ErrorNote message={error} retry={onRetry} />
      ) : list.length === 0 ? (
        <EmptyState title={tr("rq.empty")} />
      ) : (
        <>
          <div className="flex flex-col divide-y divide-border-light">
            {rows.map((q, i2) => (
              <RecentRow key={q.id || `${q.question_id}-${i2}`} q={q} tr={tr} lang={lang} />
            ))}
          </div>
          <Pager page={cur} total={list.length} per={PER_PAGE} onPage={setPage} />
        </>
      )}
    </Card>
  );
}
