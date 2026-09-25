"use client";

// 错题本卡（plan §11.2/§14.6）：journal 投影 wrong/partial 题目，分页展示。
// 展开行 = 打开证据详情（原始作答/原题/揭晓答案与解析/帮助记录）——原始
// 材料与判分全部回源服务端，不在列表里复制一份；重练走 chat 深链变式。
import { useState } from "react";
import Link from "next/link";
import { BookX, RefreshCcw } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { EmptyState, ErrorNote, Skeleton } from "@/components/ui/EmptyState";
import { Pager, paged, pageCount } from "@/components/ui/Pager";
import { EvidenceDetailDrawer } from "@/components/learning-evaluation/EvidenceTimeline";
import { dt, verdictTone } from "@/lib/labels";
import type { Lang } from "@/lib/i18n";
import type { ErrorNotebookItem } from "@/lib/types-modules";
import type { PageTr } from "./common";

const PER_PAGE = 5;

function fmtIso(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString();
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
  const [openSource, setOpenSource] = useState<string | null>(null);
  const list = items ?? [];
  const cur = Math.min(page, pageCount(list.length, PER_PAGE) - 1);
  const rows = paged(list, cur, PER_PAGE);

  const reworkHref = (q: ErrorNotebookItem): string => {
    const stem = q.stem.length > 80 ? `${q.stem.slice(0, 80)}…` : q.stem;
    return `/chat?q=${encodeURIComponent(tr("eb.rework.msg").replace("%s", stem))}&send=1`;
  };

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
          <div className="flex flex-col divide-y divide-border-light" data-testid="error-notebook">
            {rows.map((q, i) => (
              <div key={q.source_id || i} className="flex items-start gap-3 px-2 py-2.5">
                <div className="min-w-0 flex-1">
                  <button
                    type="button"
                    onClick={() => setOpenSource(q.source_id)}
                    className="block w-full cursor-pointer text-left"
                  >
                    <span className="line-clamp-2 text-sm text-fg">{q.stem}</span>
                  </button>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
                    <Badge tone={verdictTone(q.verdict)}>
                      {dt(lang, `verdict.${q.verdict}`, q.verdict)}
                    </Badge>
                    <span className="truncate">{q.knowledge_point || q.topic || "—"}</span>
                    <span className="tnum shrink-0">{fmtIso(q.ts)}</span>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setOpenSource(q.source_id)}
                  className="shrink-0 cursor-pointer rounded-[7px] border border-border bg-surface px-2 py-1 text-[0.7rem] font-medium text-fg-secondary transition-colors hover:border-accent hover:text-accent"
                >
                  {tr("eb.detail", "看原题与答案")}
                </button>
                <Link
                  href={reworkHref(q)}
                  className="inline-flex shrink-0 cursor-pointer items-center gap-1 rounded-[7px] border border-border bg-surface px-2 py-1 text-[0.7rem] font-medium text-fg-secondary transition-colors hover:border-accent hover:text-accent"
                >
                  <RefreshCcw size={11} />
                  {tr("eb.rework")}
                </Link>
              </div>
            ))}
          </div>
          <Pager page={cur} total={list.length} per={PER_PAGE} onPage={setPage} />
        </>
      )}
      <EvidenceDetailDrawer
        sourceId={openSource}
        onClose={() => setOpenSource(null)}
        lang={lang}
      />
    </Card>
  );
}
