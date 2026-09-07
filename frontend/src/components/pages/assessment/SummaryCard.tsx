"use client";

// done 阶段：测评总结卡（summary 各字段 prettify 渲染）。
// 布鲁姆层级分布（summary.bloom：题目层级标签 × 判分）+ 薄弱概念一键加入周计划。
import { Award, CalendarPlus, RotateCcw } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { dt, verdictTone } from "@/lib/labels";
import type { Lang } from "@/lib/i18n";
import type { AssessmentSummary } from "@/lib/types-modules";
import { DifficultyDots, type PageTr } from "./common";

const BLOOM_ORDER = ["remember", "understand", "apply", "analyze", "evaluate", "create"];
const BLOOM_ZH: Record<string, string> = {
  remember: "记忆", understand: "理解", apply: "应用",
  analyze: "分析", evaluate: "评价", create: "创造",
};

/** 已结构化渲染的字段，剩余标量字段走通用行。 */
const HANDLED = new Set([
  "concept",
  "verdict",
  "asked",
  "answered",
  "correct",
  "wrong",
  "partial",
  "accuracy",
  "final_difficulty",
  "recommendation",
  "bloom",
  // stop_reason / status 由专用行映射渲染：原始值（mastered 等）是诊断性
  // 内部结论，直接展示会夸大为「已掌握」（updatePlan.md A06）。
  "stop_reason",
  "status",
]);

function StatBox({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1 rounded-[8px] bg-surface-sunken px-3 py-2.5">
      <span className="text-xs text-muted">{label}</span>
      <span className="tnum text-lg font-semibold text-fg">{value}</span>
    </div>
  );
}

export function SummaryCard({
  tr,
  lang,
  summary,
  onAgain,
  onAddToPlan,
  addingToPlan,
  addedToPlan,
}: {
  tr: PageTr;
  lang: Lang;
  summary: AssessmentSummary;
  onAgain: () => void;
  onAddToPlan?: () => void;
  addingToPlan?: boolean;
  addedToPlan?: boolean;
}) {
  const asked = summary.asked ?? summary.answered;
  const accuracy = typeof summary.accuracy === "number" ? summary.accuracy : null;
  const finalDifficulty =
    typeof summary.final_difficulty === "number" ? summary.final_difficulty : null;
  const verdict = typeof summary.verdict === "string" && summary.verdict ? summary.verdict : null;

  const rest = Object.entries(summary).filter(
    ([k, v]) =>
      !HANDLED.has(k) && v != null && (typeof v === "string" || typeof v === "number"),
  );

  return (
    <Card>
      <CardHeader
        icon={<Award size={16} />}
        title={tr("sum.title")}
        right={
          verdict && (
            <Badge tone={verdictTone(verdict)}>
              {dt(lang, `verdict.${verdict}`, tr("verdict.unknown"))}
            </Badge>
          )
        }
      />

      {summary.concept && (
        <div className="mb-3 text-sm text-fg-secondary">
          {tr("sum.concept")}
          <span className="ml-2 font-medium text-fg">{summary.concept}</span>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatBox label={tr("sum.asked")} value={typeof asked === "number" ? asked : "—"} />
        <StatBox
          label={tr("sum.correct")}
          value={typeof summary.correct === "number" ? summary.correct : "—"}
        />
        <StatBox
          label={tr("sum.accuracy")}
          value={accuracy != null ? `${Math.round(accuracy * 100)}%` : "—"}
        />
        <div className="flex flex-col gap-1 rounded-[8px] bg-surface-sunken px-3 py-2.5">
          <span className="text-xs text-muted">{tr("sum.final_difficulty")}</span>
          <span className="flex h-7 items-center gap-2">
            {finalDifficulty != null ? (
              <>
                <DifficultyDots level={finalDifficulty} />
                <span className="tnum text-sm font-semibold text-fg">{finalDifficulty}</span>
              </>
            ) : (
              <span className="tnum text-lg font-semibold text-fg">—</span>
            )}
          </span>
        </div>
      </div>

      {(typeof summary.wrong === "number" || typeof summary.partial === "number") && (
        <div className="mt-3 flex flex-wrap gap-2">
          {typeof summary.wrong === "number" && (
            <Badge tone="danger" className="tnum">
              {tr("sum.wrong")} {summary.wrong}
            </Badge>
          )}
          {typeof summary.partial === "number" && (
            <Badge tone="warning" className="tnum">
              {tr("sum.partial")} {summary.partial}
            </Badge>
          )}
        </div>
      )}

      {(() => {
        const bloom = summary.bloom as Record<string, Record<string, number>> | undefined;
        if (!bloom || Object.keys(bloom).length === 0) return null;
        const rows = BLOOM_ORDER.filter((lv) => bloom[lv]);
        return (
          <div className="mt-4">
            <p className="mb-1.5 text-xs text-muted">{tr("sum.bloom")}</p>
            <div className="flex flex-col gap-1.5">
              {rows.map((lv) => {
                const b = bloom[lv];
                const ok = (b.correct ?? 0) + (b.partial ?? 0) * 0.5;
                const rate = b.asked ? ok / b.asked : 0;
                return (
                  <div key={lv} className="flex items-center gap-2.5 text-xs">
                    <span className="w-14 shrink-0 text-fg-secondary">
                      {lang === "en" ? lv : BLOOM_ZH[lv]}
                    </span>
                    <div className="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-surface-hover">
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${Math.round(rate * 100)}%`,
                          background: rate >= 0.6
                            ? "rgb(var(--success))"
                            : rate >= 0.3
                              ? "rgb(var(--warning))"
                              : "rgb(var(--danger))",
                        }}
                      />
                    </div>
                    <span className="tnum w-16 shrink-0 text-right text-[0.7rem] text-muted">
                      {(b.correct ?? 0) + (b.partial ?? 0) + (b.wrong ?? 0)}{" "}
                      {tr("sum.bloom.q")} · {Math.round(rate * 100)}%
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })()}

      {summary.recommendation && (
        <div className="mt-4">
          <div className="mb-1.5 text-xs text-muted">{tr("sum.recommendation")}</div>
          <div className="chat-prose whitespace-pre-wrap rounded-[8px] bg-surface-sunken px-3.5 py-3">
            {String(summary.recommendation)}
          </div>
        </div>
      )}

      {(() => {
        // 结束原因 / 状态映射为诊断性表述：不让内部枚举（mastered 等）
        // 以原文直达学生，避免把「本轮表现稳定」读成「长期已掌握」。
        const stopReason =
          typeof summary.stop_reason === "string" && summary.stop_reason
            ? summary.stop_reason
            : null;
        const status =
          typeof summary.status === "string" && summary.status
            ? summary.status
            : null;
        if (!stopReason && !status) return null;
        return (
          <div className="mt-4 flex flex-col gap-1.5 border-t border-border-light pt-3">
            {stopReason && (
              <div className="flex items-baseline gap-2 text-xs">
                <span className="shrink-0 text-muted">{tr("sum.stop_reason")}</span>
                <span className="text-fg-secondary">
                  {tr(`sum.stop_reason.${stopReason}`, stopReason)}
                </span>
              </div>
            )}
            {status && (
              <div className="flex items-baseline gap-2 text-xs">
                <span className="shrink-0 text-muted">{tr("sum.status")}</span>
                <span className="text-fg-secondary">
                  {tr(`sum.status.${status}`, status)}
                </span>
              </div>
            )}
          </div>
        );
      })()}

      {rest.length > 0 && (
        <div className="mt-4 flex flex-col gap-1.5 border-t border-border-light pt-3">
          {rest.map(([k, v]) => (
            <div key={k} className="flex items-baseline gap-2 text-xs">
              <span className="shrink-0 text-muted">{tr(`sum.${k}`, k)}</span>
              <span className="text-fg-secondary">{String(v)}</span>
            </div>
          ))}
        </div>
      )}

      <div className="mt-4 flex flex-wrap justify-end gap-2">
        {onAddToPlan && typeof summary.concept === "string" && summary.concept && (
          <Button
            size="lg"
            variant="outline"
            icon={<CalendarPlus size={15} />}
            disabled={addingToPlan || addedToPlan}
            onClick={onAddToPlan}
          >
            {addedToPlan ? tr("sum.added") : addingToPlan ? tr("sum.adding") : tr("sum.addToPlan")}
          </Button>
        )}
        <Button size="lg" icon={<RotateCcw size={15} />} onClick={onAgain}>
          {tr("sum.again")}
        </Button>
      </div>
    </Card>
  );
}
