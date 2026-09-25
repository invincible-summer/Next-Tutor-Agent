"use client";
// 共用提交结果组件（plan §14.5/§15.3）：正式提交后的两层反馈——
// ①本题结果：对/部分对/错/未判定（服务端确定性判分 + 量规明细）。
// ②学习反馈：一两句具体判断；没有评价时显示「评价中/未绑定学科/暂时
// 无法完成评价」，绝不用本题正误填补学习评价。
// 供 assessment（反馈阶段/总结行内）与 chat QuizCard 复用；状态文案统一
// 走 evaluation-labels。
import { Badge } from "@/components/ui/Badge";
import { et, type Lang } from "@/lib/evaluation-labels";
import { verdictTone } from "@/lib/labels";

export interface SubmissionTaskResult {
  verdict?: string | null;
  grading_status?: string;
  task_score?: number | null;
  criterion_results?: Array<{ criterion_id: string; result: string; comment?: string }>;
  first_error?: { description?: string } | null;
  hypotheses?: Array<{ statement?: string }>;
  feedback?: { strengths?: string[]; improvement?: string; next_step?: string } | null;
}

export interface SubmissionOutcomeData {
  taskResult: SubmissionTaskResult | null;
  /** evaluation.status：ready | pending | …；异常态只说明状态不下结论。 */
  evaluationStatus?: string;
  /** 学习反馈（LearnerInterpretation.feedback；空=尚未生成）。 */
  learnerFeedback?: string;
}

export function SubmissionOutcome({
  data,
  lang,
  onViewEvidence,
  className,
}: {
  data: SubmissionOutcomeData;
  lang: Lang;
  /** 「查看依据」入口（如 /memory）；不传则不渲染。 */
  onViewEvidence?: () => void;
  className?: string;
}) {
  const t = (zh: string, en: string) => (lang === "en" ? en : zh);
  const verdict = data.taskResult?.verdict ?? null;
  const grading = data.taskResult?.grading_status || "";
  const undetermined = !verdict || grading === "indeterminate" || grading === "unverified";
  const fb = data.taskResult?.feedback || null;
  const evalStatus = data.evaluationStatus || "";
  const learnerFb = (data.learnerFeedback || "").trim();
  const verdictLabel = !verdict
    ? t("未判定", "Not determined")
    : verdict === "correct"
      ? t("回答正确", "Correct")
      : verdict === "partial"
        ? t("部分正确", "Partially correct")
        : t("回答错误", "Wrong");

  return (
    <div className={className} data-testid="submission-outcome">
      {/* ① 本题结果 */}
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={undetermined ? "muted" : verdictTone(verdict)}>
          {verdictLabel}
        </Badge>
        {fb?.strengths?.slice(0, 2).map((s, i) => (
          <Badge key={i} tone="success">{s}</Badge>
        ))}
      </div>
      {(fb?.improvement || data.taskResult?.first_error?.description) && (
        <p className="mt-1.5 text-xs leading-relaxed text-fg-secondary">
          {data.taskResult?.first_error?.description && (
            <>
              <span className="font-medium text-danger">{t("首个错误：", "First error: ")}</span>
              {data.taskResult.first_error.description}
            </>
          )}
          {fb?.improvement && (
            <span className={data.taskResult?.first_error?.description ? "block" : ""}>
              {fb.improvement}
            </span>
          )}
        </p>
      )}
      {fb?.next_step && (
        <p className="mt-1 text-xs leading-relaxed text-accent-strong">
          {t("下一步：", "Next step: ")}{fb.next_step}
        </p>
      )}
      {data.taskResult?.hypotheses?.length ? (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          <span className="text-[0.7rem] text-muted">{t("待验证猜想：", "Hypotheses:")}</span>
          {data.taskResult.hypotheses.slice(0, 2).map((h, i) => (
            <Badge key={i} tone="outline">{h.statement}</Badge>
          ))}
        </div>
      ) : null}

      {/* ② 学习反馈：没有评价时如实说明状态，不用正误填补 */}
      <div className="mt-2 rounded-[8px] border border-border-light bg-surface px-3 py-2">
        <p className="mb-0.5 text-[0.7rem] font-medium text-fg-secondary">
          {t("学习反馈", "Learning feedback")}
        </p>
        {learnerFb ? (
          <p className="text-xs leading-relaxed text-fg">{learnerFb}</p>
        ) : evalStatus === "pending" ? (
          <p className="text-xs text-muted">{et(lang, "eval.status.pending")}…</p>
        ) : evalStatus && evalStatus !== "ready" ? (
          <p className="text-xs text-muted">{et(lang, `eval.status.${evalStatus}`, evalStatus)}</p>
        ) : (
          <p className="text-xs text-muted">
            {t("本次作答未形成新的学习评价（未绑定学科或暂无可观察表现）。",
               "No new learning observation from this answer (unbound subject or nothing observable).")}
          </p>
        )}
        {onViewEvidence && learnerFb && (
          <button
            type="button"
            onClick={onViewEvidence}
            className="mt-1 cursor-pointer text-xs text-accent hover:underline"
          >
            {t("查看依据", "View evidence")}
          </button>
        )}
      </div>
    </div>
  );
}
