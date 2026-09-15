"use client";
// 单题练习卡（plan §14.5/§15.3）：服务端身份题卡——question_id 定位、
// 一次正式提交（/quiz/record 服务端判分+评价）、提示与揭晓都由服务端记录
// 并影响后续解释；反馈分两层（本题结果 / 学习反馈，SubmissionOutcome）。
// 刷新/重开经 q.result.attempt_id 恢复已提交状态；旧会话无服务端身份的题
// 只读陈列，不再本地判分。
import { useEffect, useRef, useState } from "react";
import { BookOpen, ChevronDown, Eye, Lightbulb, Loader2, Send } from "lucide-react";
import { useRouter } from "next/navigation";
import { cn } from "@/lib/cn";
import { useUIStore } from "@/lib/store";
import { t } from "@/lib/i18n";
import { fetchQuizHint, revealQuizAnswer, submitQuizAnswer, type QuizSubmitOutcome } from "@/lib/api";
import { getEvalJob } from "@/lib/api-modules";
import { Badge } from "@/components/ui/Badge";
import { MiniMarkdown } from "./markdown";
import { SubmissionOutcome } from "@/components/learning-evaluation/SubmissionOutcome";
import type { QuizQuestion, QuizSourceRef } from "@/lib/types";

/** 教材依据定位行：filename · 章节路径 · 页码（只展示学习者可理解的信息）。 */
function sourceLocation(ref: QuizSourceRef): string {
  const parts: string[] = [];
  if (ref.filename) parts.push(ref.filename);
  const section = (ref.section_path || []).filter(Boolean).join(" · ");
  if (section) parts.push(section);
  if (ref.printed_page != null) parts.push(`教材第 ${ref.printed_page} 页`);
  else if (ref.page != null) parts.push(`PDF 第 ${ref.page} 页`);
  return parts.join(" · ");
}

export function QuizQuestionCard({
  question: q,
  index,
  sessionId,
}: {
  question: QuizQuestion;
  index: number;
  sessionId?: string;
}) {
  const { lang } = useUIStore();
  const router = useRouter();
  const tr = (k: string, fb?: string) => t(lang, k, fb);
  // 服务端身份：无 question_id 的旧题只读陈列（不允许本地判分提交）。
  const qid = q.question_id || "";
  const rev = q.question_revision || 1;
  const hasIdentity = !!qid;
  const savedResult = q.result && q.result.verdict ? q.result : null;

  const [selected, setSelected] = useState<string | null>(savedResult?.student_answer ?? null);
  const [outcome, setOutcome] = useState<QuizSubmitOutcome | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [hint, setHint] = useState("");
  const [hintLoading, setHintLoading] = useState(false);
  const [revealed, setRevealed] = useState<{ answer: string; explanation: string } | null>(null);
  // R11（update_plan §4）：202 受理后语义评价的轮询状态（提交不再等模型）
  const [evalFollowUp, setEvalFollowUp] = useState<"" | "pending" | "ready" | "failed">("");
  const [expOpen, setExpOpen] = useState(false);
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const followCleanupRef = useRef<(() => void) | null>(null);
  useEffect(() => () => followCleanupRef.current?.(), []);

  const submitted = !!outcome || !!savedResult;
  const isMC = q.type === "multiple_choice" && !!q.options;
  const options = q.options ? Object.entries(q.options) : [];

  const sourceRefs = (q.source_refs || []).filter((r) => r && r.file_id);
  const textbookGrounded = q.grounding_mode === "textbook" || q.grounding_mode === "reference+textbook";
  const partialGrounded = textbookGrounded && q.grounding_tier === "partial";

  async function loadHint() {
    if (!hasIdentity || hintLoading || submitted) return;
    setHintLoading(true);
    try {
      const res = await fetchQuizHint(qid, rev);
      setHint(res.status === "ok" ? res.hint : (res.message || tr("quiz.hint.none")));
    } catch {
      setHint(tr("quiz.hint.error"));
    } finally {
      setHintLoading(false);
    }
  }

  async function reveal() {
    if (!hasIdentity || revealed) return;
    try {
      const res = await revealQuizAnswer(qid, rev);
      if (res.status === "ok") {
        setRevealed({ answer: res.answer, explanation: res.explanation });
        setExpOpen(true);
      }
    } catch {
      /* 揭晓失败静默：按钮可重试 */
    }
  }

  async function followEvaluation(jobId: string) {
    // 有界轮询：2s 间隔、最多 ~40s；组件卸载后不再 setState。
    let alive = true;
    const cleanup = () => { alive = false; };
    followCleanupRef.current = cleanup;
    for (let i = 0; i < 20 && alive; i++) {
      try {
        const job = await getEvalJob(jobId);
        if (job.state === "succeeded" || job.state === "abstained") {
          setEvalFollowUp("ready");
          return;
        }
        if (job.state === "failed" || job.state === "cancelled") {
          setEvalFollowUp("failed");
          return;
        }
      } catch {
        /* 网络抖动：继续轮询直到有界结束 */
      }
      await new Promise((r) => setTimeout(r, 2000));
    }
    if (alive) setEvalFollowUp("pending"); // 超时：保持等待态（下次打开页面可见结果）
  }

  async function submit() {
    if (!hasIdentity || !selected || submitting || submitted) return;
    setSubmitting(true);
    setSubmitError("");
    try {
      const res = await submitQuizAnswer({
        question_id: qid,
        question_revision: rev,
        student_answer: selected,
        session_id: sessionId,
      });
      setOutcome(res);
      // 揭晓视图：已评价的正式提交后可见（服务端判定）。
      if (res.task_result?.verdict) {
        setRevealed({ answer: q.answer, explanation: q.explanation });
      }
      // R02/R11：202 已受理——MC 本题结果即时；语义学习评价由后台
      // worker 执行，此处轮询 job 到终态（pending→ready/failed）。
      if (res.job_id && res.evaluation?.status === "pending") {
        setEvalFollowUp("pending");
        void followEvaluation(res.job_id);
      }
    } catch (e) {
      setSubmitError((e as Error).message || tr("quiz.grade.error"));
    } finally {
      setSubmitting(false);
    }
  }

  const answerText = revealed?.answer ?? q.answer;

  return (
    <div className="rounded-[10px] border border-border bg-surface p-3 shadow-sm" data-testid="quiz-card">
      {/* 题号 + 题型 + 教材依据 */}
      <div className="flex items-center gap-2">
        <span className="flex h-5 w-5 items-center justify-center rounded-[5px] bg-accent-soft font-mono text-[0.68rem] font-semibold text-accent-strong">
          {index + 1}
        </span>
        <Badge tone="outline">{tr(`quiz.type.${q.type || "multiple_choice"}`, q.type)}</Badge>
        {textbookGrounded && (
          <Badge tone="accent" testId="quiz-source-badge">
            <BookOpen size={11} className="mr-0.5" />
            {partialGrounded ? tr("quiz.grounding.partial", "部分教材依据") : tr("quiz.grounding.textbook", "教材依据")}
          </Badge>
        )}
        {q.difficulty && (
          <span className="ml-auto text-[0.65rem] text-muted/70">
            {tr("quiz.difficulty")} {q.difficulty}
          </span>
        )}
      </div>

      <MiniMarkdown className="chat-prose mt-2 text-[0.82rem] font-medium text-fg">{q.stem}</MiniMarkdown>

      {isMC ? (
        <div className="mt-2.5 space-y-1.5">
          {options.map(([key, val]) => {
            const isSelected = selected === key;
            const isCorrect = submitted && answerText === key;
            let cls = "border-border-light bg-bg hover:border-accent/40 hover:bg-surface";
            if (!submitted && isSelected) cls = "border-accent bg-accent-soft/50 ring-1 ring-accent/30";
            if (submitted && isCorrect) cls = "border-success/50 bg-success/8";
            else if (submitted && isSelected && !isCorrect) cls = "border-danger/50 bg-danger/8";
            else if (submitted) cls = "border-border-light bg-bg opacity-55";
            return (
              <button
                key={key}
                disabled={submitted || !hasIdentity}
                onClick={() => setSelected(key)}
                className={cn(
                  "flex w-full items-center gap-2.5 rounded-[8px] border px-3 py-2 text-left text-[0.8rem] transition-all",
                  cls,
                  !submitted && hasIdentity ? "cursor-pointer" : "cursor-default",
                )}
              >
                <span className={cn(
                  "flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[0.68rem] font-semibold",
                  !submitted && isSelected ? "border-accent bg-accent text-white" : "border-border text-fg-secondary",
                  submitted && isCorrect ? "border-success bg-success text-white" : "",
                  submitted && isSelected && !isCorrect ? "border-danger bg-danger text-white" : "",
                )}>
                  {key}
                </span>
                <MiniMarkdown className="chat-prose flex-1 text-[0.8rem] text-fg-secondary">{val}</MiniMarkdown>
              </button>
            );
          })}
        </div>
      ) : (
        <div className="mt-2.5">
          <textarea
            disabled={submitted || submitting || !hasIdentity}
            placeholder={hasIdentity ? tr("quiz.answer.placeholder") : tr("quiz.legacy.note", "旧题目仅供回看，新练习请让教练重新出题")}
            className="w-full resize-none rounded-[8px] border border-border bg-bg px-3 py-2 text-[0.8rem] text-fg outline-none placeholder:text-muted focus:border-accent/40 disabled:opacity-60"
            rows={2}
            value={selected ?? ""}
            onChange={(e) => setSelected(e.target.value)}
          />
        </div>
      )}

      {/* 操作行：提交一次 + 提示 + 揭晓（均服务端记录） */}
      {hasIdentity && !submitted && (
        <div className="mt-2.5 flex items-center gap-3">
          <button
            onClick={() => void submit()}
            disabled={!selected || submitting}
            className="flex items-center gap-1.5 text-[0.75rem] font-medium text-accent transition-colors hover:text-accent-strong disabled:cursor-not-allowed disabled:opacity-40"
          >
            {submitting ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} />}
            {submitting ? tr("quiz.grading") : tr("quiz.submit")}
          </button>
          <button
            onClick={() => void loadHint()}
            disabled={hintLoading || !!hint}
            className="flex items-center gap-1 text-[0.7rem] text-muted transition-colors hover:text-accent disabled:cursor-not-allowed disabled:opacity-50"
          >
            {hintLoading ? <Loader2 size={12} className="animate-spin" /> : <Lightbulb size={12} />}
            {tr("quiz.hint")}
          </button>
          <button
            onClick={() => void reveal()}
            className="flex items-center gap-1 text-[0.7rem] text-muted transition-colors hover:text-accent"
          >
            <Eye size={12} /> {tr("quiz.reveal")}
          </button>
        </div>
      )}
      {submitError && (
        <p className="mt-1.5 text-[0.72rem] text-danger">{submitError}</p>
      )}
      {!hasIdentity && (
        <p className="mt-1.5 text-[0.7rem] text-muted/70">
          {tr("quiz.legacy.note", "旧题目仅供回看，新练习请让教练重新出题")}
        </p>
      )}

      {/* 关键步骤提示（服务端量规派生；不含答案） */}
      {hint && !submitted && (
        <div className="mt-2 rounded-[8px] border border-warning/30 bg-warning/5 px-3 py-2">
          <p className="whitespace-pre-wrap text-[0.75rem] leading-relaxed text-fg-secondary">{hint}</p>
        </div>
      )}

      {/* 两层反馈：本题结果 + 学习反馈（§14.5） */}
      {submitted && (outcome || savedResult) && (
        <div className="mt-2.5">
          <SubmissionOutcome
            lang={lang}
            data={{
              taskResult: outcome?.task_result ?? {
                verdict: savedResult?.verdict ?? null,
              },
              evaluationStatus: evalFollowUp === "ready"
                ? "ready"
                : evalFollowUp === "failed"
                  ? "failed"
                  : outcome?.evaluation?.status || "ready",
              learnerFeedback: outcome?.feedback || "",
            }}
            onViewEvidence={() => router.push("/memory")}
          />
        </div>
      )}

      {/* 解析折叠（提交/揭晓后可见） */}
      {(submitted || revealed) && !!(revealed?.explanation || q.explanation) && (
        <div className="mt-2.5 border-t border-border-light pt-2">
          <button
            onClick={() => setExpOpen((v) => !v)}
            className="flex items-center gap-1 text-[0.72rem] font-medium text-muted transition-colors hover:text-fg-secondary"
          >
            <Lightbulb size={12} className="text-warning" />
            {tr("quiz.explanation")}
            <ChevronDown size={12} className={cn("transition-transform", expOpen ? "" : "-rotate-90")} />
          </button>
          {expOpen && (
            <>
              <MiniMarkdown className="chat-prose mt-1.5 text-[0.78rem] leading-relaxed text-fg-secondary">
                {revealed?.explanation || q.explanation}
              </MiniMarkdown>
              {sourceRefs.length > 0 && (
                <div className="mt-2 space-y-1.5" data-testid="quiz-source-refs">
                  {(sourcesOpen ? sourceRefs : sourceRefs.slice(0, 2)).map((ref, i) => (
                    <div key={`${ref.chunk_id}-${i}`} className="rounded-[6px] border border-border-light bg-bg/60 px-2.5 py-1.5">
                      <p className="flex items-center gap-1 text-[0.68rem] font-medium text-accent-strong">
                        <BookOpen size={11} className="shrink-0" />
                        {sourceLocation(ref)}
                      </p>
                      {ref.excerpt && (
                        <p className="mt-0.5 line-clamp-3 whitespace-pre-wrap text-[0.7rem] leading-relaxed text-muted">{ref.excerpt}</p>
                      )}
                    </div>
                  ))}
                  {sourceRefs.length > 2 && (
                    <button
                      onClick={() => setSourcesOpen((v) => !v)}
                      className="flex items-center gap-1 text-[0.68rem] text-muted transition-colors hover:text-accent"
                    >
                      <ChevronDown size={11} className={cn("transition-transform", sourcesOpen ? "" : "-rotate-90")} />
                      {sourcesOpen
                        ? tr("quiz.grounding.collapse", "收起依据")
                        : tr("quiz.grounding.more", `还有 ${sourceRefs.length - 2} 条依据`).replace("%n", String(sourceRefs.length - 2))}
                    </button>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
