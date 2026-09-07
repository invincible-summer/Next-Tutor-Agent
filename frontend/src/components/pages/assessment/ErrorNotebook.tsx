"use client";

// 错题本卡（P3）：跨会话聚合答错/半对的题，分页展示 + 一键重练（深链到对话，
// 由教练用 fit_quiz 出变式——复用答题卡/批改闭环，不在本页复制一套作答 UI）。
import { useState } from "react";
import Link from "next/link";
import { BookX, ChevronDown, RefreshCcw } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { EmptyState, ErrorNote, Skeleton } from "@/components/ui/EmptyState";
import { Pager, paged, pageCount } from "@/components/ui/Pager";
import { relTime } from "@/lib/format";
import { dt, verdictTone } from "@/lib/labels";
import type { Lang } from "@/lib/i18n";
import type { ErrorNotebookItem } from "@/lib/types-modules";
import type { PageTr } from "./common";

/** 每页 5 条：行内含作答对比与可展开解析，比纯行高列表占空间。 */
const PER_PAGE = 5;

/** 重练深链消息：带题干让教练仿照出变式（走既有 ?q=&send=1 契约）。 */
function reworkHref(q: ErrorNotebookItem, tr: PageTr): string {
  const stem = q.stem.length > 80 ? `${q.stem.slice(0, 80)}…` : q.stem;
  const msg = tr("eb.rework.msg").replace("%s", stem);
  return `/chat?q=${encodeURIComponent(msg)}&send=1`;
}

export function ErrorNotebook({
  tr,
  lang,
  items,
  loading,
  error,
  onRetry,
}: {
  tr: PageTr;
  lang: Lang;
  items: ErrorNotebookItem[] | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}) {
  const [page, setPage] = useState(0);
  const [openIdx, setOpenIdx] = useState<number | null>(null);
  const list = items ?? [];
  // 判分回源后条数可能变化：先钳位页码，避免停在空白页。
  const cur = Math.min(page, pageCount(list.length, PER_PAGE) - 1);
  const rows = paged(list, cur, PER_PAGE);

  return (
    <Card>
      <CardHeader icon={<BookX size={16} />} title={tr("eb.title")} desc={tr("eb.desc")} />
      {loading ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-14" />
          <Skeleton className="h-14" />
        </div>
      ) : error ? (
        <ErrorNote message={error} retry={onRetry} />
      ) : list.length === 0 ? (
        <EmptyState title={tr("eb.empty")} desc={tr("eb.empty.desc")} />
      ) : (
        <>
          <div className="flex flex-col divide-y divide-border-light">
            {rows.map((q, i) => {
              const idx = cur * PER_PAGE + i;
              const open = openIdx === idx;
              return (
                <div key={`${q.source_session_id || q.session_id}-${idx}`} className="py-2.5">
                  <div className="flex items-start gap-3 px-2">
                    <div className="min-w-0 flex-1">
                      <button
                        type="button"
                        onClick={() => setOpenIdx(open ? null : idx)}
                        className="block w-full cursor-pointer text-left"
                      >
                        <span className="line-clamp-2 text-sm text-fg">{q.stem}</span>
                      </button>
                      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
                        <Badge tone={verdictTone(q.verdict)}>
                          {dt(lang, `verdict.${q.verdict}`, q.verdict)}
                        </Badge>
                        <Badge tone="outline">{tr(`rq.type.${q.type}`, q.type)}</Badge>
                        <span className="truncate">{q.knowledge_point || q.topic || "—"}</span>
                        <span className="tnum shrink-0">{relTime(q.ts, lang)}</span>
                        {q.source_status === "deleted" && <span className="text-danger">{q.source_message || "来源对话已删除，无法查看"}</span>}
                        {q.source_status === "independent" && <span>{q.source_message || "独立测评记录"}</span>}
                      </div>
                    </div>
                    <Link
                      href={reworkHref(q, tr)}
                      className="inline-flex shrink-0 cursor-pointer items-center gap-1 rounded-[7px] border border-border bg-surface px-2 py-1 text-[0.7rem] font-medium text-fg-secondary transition-colors hover:border-accent hover:text-accent"
                    >
                      <RefreshCcw size={11} />
                      {tr("eb.rework")}
                    </Link>
                    <ChevronDown
                      size={14}
                      className={`mt-1 shrink-0 cursor-pointer text-muted transition-transform ${open ? "rotate-180" : ""}`}
                      onClick={() => setOpenIdx(open ? null : idx)}
                    />
                  </div>
                  {open && (
                    <div className="mx-2 mt-2 flex flex-col gap-1.5 rounded-[8px] bg-surface-sunken/60 px-3 py-2.5 text-xs leading-relaxed">
                      <div>
                        <span className="text-muted">{tr("eb.your.answer")}：</span>
                        <span className="text-danger">{q.student_answer || "—"}</span>
                      </div>
                      <div>
                        <span className="text-muted">{tr("eb.correct.answer")}：</span>
                        <span className="text-success">{q.correct_answer || "—"}</span>
                      </div>
                      {q.explanation && (
                        <div className="text-muted">{q.explanation}</div>
                      )}
                      <Link
                        href={`/chat/${encodeURIComponent(q.session_id)}`}
                        className="mt-0.5 font-medium text-accent hover:underline"
                      >
                        {tr("eb.goto.session")} →
                      </Link>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          <Pager page={cur} total={list.length} per={PER_PAGE} onPage={setPage} />
        </>
      )}
    </Card>
  );
}
