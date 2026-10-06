"use client";
import { Badge } from "@/components/ui/Badge";
import { useUIStore } from "@/lib/store";
import { localeFor, type Lang } from "@/lib/i18n";
import { verdictTone } from "@/lib/labels";
import type { RecentQuizQuestion } from "@/lib/types-modules";
import type { Tr } from "./shared";
import { SectionHeader, CompactEmpty } from "./sections";

function verdictLabel(tr: Tr, verdict: string): string {
  if (verdict === "correct") return tr("answers.verdict.correct");
  if (verdict === "partial") return tr("answers.verdict.partial");
  if (verdict === "wrong") return tr("answers.verdict.wrong");
  return tr("answers.verdict.unknown");
}

function fmtIso(iso: string, lang: Lang): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(localeFor(lang));
}

/** 最近作答（回顾层，扁平分区——原始作答投影 journal /quiz/recent，
 * 题目与判定是本次事实，不折算成能力档案）。 */
export function RecentAnswersCard({
  items,
  tr,
}: {
  items: RecentQuizQuestion[];
  tr: Tr;
}) {
  const lang = useUIStore((s) => s.lang);
  const rows = items.slice(0, 10);
  return (
    <section>
      <SectionHeader
        icon={<span className="text-accent">✓</span>}
        title={tr("answers.title")}
        desc={tr("answers.desc")}
      />
      {rows.length === 0 ? (
        <CompactEmpty title={tr("answers.empty")} desc={tr("answers.empty.desc")} />
      ) : (
        <ul className="flex flex-col divide-y divide-border-light" data-testid="recent-answers">
          {rows.map((r) => (
            <li key={r.id || `${r.question_id}-${r.ts}`} className="flex items-center gap-3 py-2">
              <Badge tone={verdictTone(r.verdict)}>
                {verdictLabel(tr, r.verdict)}
              </Badge>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm text-fg">
                  {r.knowledge_point || r.topic || r.stem}
                </div>
                <div className="mt-0.5 truncate text-[11px] text-muted">{r.stem}</div>
              </div>
              <div className="shrink-0 text-right">
                <div className="tnum text-[11px] text-muted">{fmtIso(r.ts, lang)}</div>
                {r.evaluation_status && r.evaluation_status !== "ready" && (
                  <div className="text-[11px] text-info">{tr("answers.evaluating")}</div>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
