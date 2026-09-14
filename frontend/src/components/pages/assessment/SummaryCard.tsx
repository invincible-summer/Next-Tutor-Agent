"use client";
// done 阶段：测评总结卡（cat.report，§11.5）——本次表现 + 语义总结。
// 只展示本次真实观察：题数/判定计数/每题反馈（SubmissionOutcome 行内）；
// 删除旧 Bloom partial=0.5 百分比色条与正确率能力档案（§14.7）。
import { Award, RotateCcw } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { SubmissionOutcome } from "@/components/learning-evaluation/SubmissionOutcome";
import { et, type Lang } from "@/lib/evaluation-labels";
import type { AssessmentSummary } from "@/lib/types-modules";
import { DifficultyDots, type PageTr } from "./common";

function fmtIso(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function SummaryCard({
  tr,
  lang,
  summary,
  onAgain,
}: {
  tr: PageTr;
  lang: Lang;
  summary: AssessmentSummary;
  onAgain: () => void;
}) {
  const counts = summary.counts || {};
  const items = summary.items || [];
  return (
    <Card>
      <CardHeader
        icon={<Award size={16} />}
        title={tr("sum.title")}
      />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label={tr("sum.asked")} value={summary.asked ?? "—"} />
        <Stat label={tr("sum.graded", "已判分")} value={summary.graded ?? "—"} />
        <Stat label={tr("sum.pending", "评价中")} value={summary.pending ?? 0} />
        <div className="flex flex-col gap-1 rounded-[8px] bg-surface-sunken px-3 py-2.5">
          <span className="text-xs text-muted">{tr("sum.final_difficulty")}</span>
          <span className="flex h-7 items-center gap-2">
            {typeof summary.difficulty === "number" && summary.difficulty > 0 ? (
              <>
                <DifficultyDots level={summary.difficulty} />
                <span className="tnum text-sm font-semibold text-fg">{summary.difficulty}</span>
              </>
            ) : (
              <span className="tnum text-lg font-semibold text-fg">—</span>
            )}
          </span>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        {(counts.correct ?? 0) > 0 && <Badge tone="success" className="tnum">{tr("sum.correct")} {counts.correct}</Badge>}
        {(counts.partial ?? 0) > 0 && <Badge tone="warning" className="tnum">{tr("sum.partial")} {counts.partial}</Badge>}
        {(counts.wrong ?? 0) > 0 && <Badge tone="danger" className="tnum">{tr("sum.wrong")} {counts.wrong}</Badge>}
        {summary.stop_code && (
          <span className="text-xs text-muted">
            {tr("sum.stop_reason")}：{tr(`sum.stop_reason.${summary.stop_code}`, summary.stop_code)}
          </span>
        )}
        {summary.stop_reason && (
          <span className="text-xs leading-relaxed text-muted/80">{summary.stop_reason}</span>
        )}
      </div>

      {items.length > 0 && (
        <div className="mt-4 flex flex-col gap-3" data-testid="assessment-summary-items">
          {items.map((it, i) => (
            <div key={it.attempt_id || `${it.question_id}-${i}`} className="rounded-[8px] border border-border-light bg-surface px-3 py-2.5">
              <div className="mb-1.5 flex items-center gap-2 text-[0.7rem] text-muted">
                <span className="tnum">{fmtIso(it.observed_at)}</span>
                {it.evaluation_status && it.evaluation_status !== "ready" && (
                  <Badge tone="info">{et(lang, `eval.status.${it.evaluation_status}`, it.evaluation_status)}</Badge>
                )}
              </div>
              <SubmissionOutcome
                lang={lang}
                data={{
                  taskResult: it.task_result ?? null,
                  evaluationStatus: it.evaluation_status || "ready",
                  learnerFeedback: it.feedback || "",
                }}
              />
            </div>
          ))}
        </div>
      )}

      <p className="mt-3 text-[0.7rem] leading-relaxed text-muted">
        {tr("sum.scope.note", "以上是本次测评的真实观察；整体学习评价以学习档案中的证据主张为准。")}
      </p>

      <div className="mt-4 flex flex-wrap justify-end gap-2">
        <Button size="lg" icon={<RotateCcw size={15} />} onClick={onAgain}>
          {tr("sum.again")}
        </Button>
      </div>
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1 rounded-[8px] bg-surface-sunken px-3 py-2.5">
      <span className="text-xs text-muted">{label}</span>
      <span className="tnum text-lg font-semibold text-fg">{value}</span>
    </div>
  );
}
