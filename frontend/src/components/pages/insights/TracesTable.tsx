"use client";

// TurnTrace 黑盒表格：最近 50 轮，行可展开查看诊断细节。
import { useState } from "react";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Pager, paged, pageCount } from "@/components/ui/Pager";
import { cn } from "@/lib/cn";
import { fmtTime } from "@/lib/format";
import { dt, modeTone, verdictTone, type Lang } from "@/lib/labels";
import type { EvalTrace } from "@/lib/types-modules";
import { failureLabel, failureTone, fmtGain, type Tr } from "./helpers";

function outcomeLabel(tr: Tr, lang: Lang, outcome: string): string {
  const o = (outcome || "unknown").toLowerCase();
  if (o === "engaged") return tr("ins.outcome.engaged");
  return dt(lang, `verdict.${o}`, outcome || "—");
}

function outcomeTone(outcome: string): BadgeTone {
  const o = (outcome || "").toLowerCase();
  if (o === "engaged") return "info";
  return verdictTone(o);
}

export function TracesTable({
  traces,
  tr,
  lang,
}: {
  traces: EvalTrace[];
  tr: Tr;
  lang: Lang;
}) {
  const [openId, setOpenId] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const ordered = [...traces].sort((a, b) => b.ts - a.ts);
  // 表格行较密：10 条/页；回源后条数变少时先钳位页码，避免停在空白页。
  const cur = Math.min(page, pageCount(ordered.length, 10) - 1);
  const rows = paged(ordered, cur, 10);

  return (
    <Card pad={false}>
      <div className="p-4 pb-0">
        <CardHeader title={tr("ins.traces.title")} desc={tr("ins.traces.desc")} />
      </div>
      {ordered.length === 0 ? (
        <div className="p-4 pt-0">
          <EmptyState title={tr("ins.traces.empty")} />
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-y border-border-light bg-surface-sunken text-muted">
                <th className="px-4 py-2 font-medium">{tr("ins.col.time")}</th>
                <th className="px-3 py-2 font-medium">{tr("ins.col.concept")}</th>
                <th className="px-3 py-2 font-medium">{tr("ins.col.subject")}</th>
                <th className="px-3 py-2 font-medium">{tr("ins.col.mode")}</th>
                <th className="px-3 py-2 font-medium">{tr("ins.col.outcome")}</th>
                <th className="px-3 py-2 text-right font-medium">{tr("ins.col.gain")}</th>
                <th className="px-4 py-2 font-medium">{tr("ins.col.failure")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((t) => {
                const open = openId === t.id;
                return [
                  <tr
                    key={t.id}
                    onClick={() => setOpenId(open ? null : t.id)}
                    className={cn(
                      "cursor-pointer border-b border-border-light transition-colors hover:bg-surface-hover",
                      open && "bg-surface-hover",
                    )}
                  >
                    <td className="tnum whitespace-nowrap px-4 py-2 text-muted">{fmtTime(t.ts)}</td>
                    <td className="max-w-[180px] truncate px-3 py-2 text-fg">{t.concept || "—"}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-fg-secondary">{t.subject || "—"}</td>
                    <td className="whitespace-nowrap px-3 py-2">
                      <Badge tone={modeTone(t.mode)}>{dt(lang, `mode.${t.mode}`, t.mode || "—")}</Badge>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2">
                      <Badge tone={outcomeTone(t.outcome)}>{outcomeLabel(tr, lang, t.outcome)}</Badge>
                    </td>
                    <td
                      className={cn(
                        "tnum whitespace-nowrap px-3 py-2 text-right",
                        t.learning_gain == null
                          ? "text-muted"
                          : t.learning_gain >= 0
                            ? "text-success"
                            : "text-danger",
                      )}
                    >
                      {fmtGain(t.learning_gain)}
                    </td>
                    <td className="whitespace-nowrap px-4 py-2">
                      <Badge tone={failureTone(t.failure_type)}>
                        {failureLabel(tr, t.failure_type)}
                      </Badge>
                    </td>
                  </tr>,
                  open ? (
                    <tr key={`${t.id}-detail`} className="border-b border-border-light bg-surface-sunken/50">
                      <td colSpan={7} className="px-4 py-3">
                        <div className="flex flex-col gap-2">
                          {t.failure_cause && (
                            <div className="text-xs leading-relaxed text-fg-secondary">
                              <span className="font-medium text-fg">{tr("ins.trace.cause")}：</span>
                              {t.failure_cause}
                            </div>
                          )}
                          <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-muted">
                            <span>
                              {tr("ins.trace.intent")}：<span className="text-fg-secondary">{t.intent || "—"}</span>
                            </span>
                            <span>
                              {tr("ins.trace.tools")}：<span className="tnum text-fg-secondary">{t.tool_count}</span>
                            </span>
                            <span>
                              {tr("ins.trace.tokens")}：<span className="tnum text-fg-secondary">{t.tokens_used}</span>
                            </span>
                            <span>
                              {tr("ins.trace.duration")}：
                              <span className="tnum text-fg-secondary">{t.duration_sec.toFixed(1)}s</span>
                            </span>
                            {t.session_id && (
                              <a
                                href={`/chat/${encodeURIComponent(t.session_id)}`}
                                onClick={(e) => e.stopPropagation()}
                                className="ml-auto font-medium text-accent hover:underline"
                              >
                                {tr("ins.trace.resume")} →
                              </a>
                            )}
                          </div>
                        </div>
                      </td>
                    </tr>
                  ) : null,
                ];
              })}
            </tbody>
          </table>
        </div>
      )}
      <Pager page={cur} total={ordered.length} per={10} onPage={setPage}
        className="px-4 pb-3" />
    </Card>
  );
}
