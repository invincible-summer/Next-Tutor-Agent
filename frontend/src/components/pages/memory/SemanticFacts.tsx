"use client";
import { useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Pager, paged, pageCount } from "@/components/ui/Pager";
import { Progress } from "@/components/ui/Progress";
import { cn } from "@/lib/cn";
import type { SemanticFact } from "@/lib/types-modules";

type Tr = (key: string, fallback?: string) => string;

/** 每页 6 张：两列三行，一屏内读完。 */
const PER_PAGE = 6;

/** 语义记忆：事实卡片网格，被取代的事实灰显并可展开审计链。 */
export function SemanticFacts({ facts, tr }: { facts: SemanticFact[]; tr: Tr }) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [page, setPage] = useState(0);

  // hooks 之后的早退：空态不渲染分页。
  const cur = Math.min(page, pageCount(facts.length, PER_PAGE) - 1);
  const rows = paged(facts, cur, PER_PAGE);

  if (facts.length === 0) {
    return <EmptyState title={tr("mem.semantic.empty")} desc={tr("mem.semantic.emptyDesc")} />;
  }

  const toggle = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {rows.map((f, i) => {
        const key = f.id ?? String(i);
        const superseded = f.superseded_by != null && f.superseded_by !== "";
        const confidence = typeof f.confidence === "number" ? f.confidence : null;
        const evidenceCount = Array.isArray(f.evidence) ? f.evidence.length : 0;
        const open = expanded.has(key);
        return (
          <Card
            key={key}
            className={cn("flex flex-col", superseded && "opacity-50")}
            onClick={superseded ? () => toggle(key) : undefined}
            hover={superseded}
          >
            <p className="text-sm leading-relaxed text-fg">{f.text || f.fact}</p>
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              {f.category && <Badge tone="accent2">{f.category}</Badge>}
              {f.scope && <Badge tone="outline">{f.scope}</Badge>}
              {superseded && <Badge tone="warning">{tr("mem.superseded")}</Badge>}
            </div>
            {confidence != null && (
              <div className="mt-3">
                <div className="flex items-baseline justify-between text-[11px] text-muted">
                  <span>{tr("mem.confidence")}</span>
                  <span className="tnum">{Math.round(confidence * 100)}%</span>
                </div>
                <Progress value={confidence} tone="accent2" height={4} className="mt-1" />
              </div>
            )}
            <div className="mt-2 flex items-center justify-between text-[11px] text-muted">
              <span className="tnum">
                {evidenceCount} {tr("mem.evidence")}
              </span>
            </div>
            {superseded && open && (
              <div className="mt-2 rounded-[6px] bg-surface-sunken px-2 py-1.5 font-mono text-[11px] text-muted">
                {tr("mem.supersededBy")}: {f.superseded_by}
              </div>
            )}
          </Card>
        );
        })}
      </div>
      <Pager page={cur} total={facts.length} per={PER_PAGE} onPage={setPage} />
    </>
  );
}
