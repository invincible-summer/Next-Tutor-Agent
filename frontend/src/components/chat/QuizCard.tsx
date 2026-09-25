"use client";
import { QuestionIllustration } from "@/components/quiz/QuestionIllustration";
// 单题练习卡（plan §14.5/§15.3）：服务端身份题卡——question_id 定位、
// 一次正式提交（/quiz/record 服务端判分+评价）、提示与揭晓都由服务端记录
// 并影响后续解释；反馈分两层（本题结果 / 学习反馈，SubmissionOutcome）。
// 刷新/重开经 q.result.attempt_id 恢复已提交状态；旧会话无服务端身份的题
// 只读陈列，不再本地判分。
import { useEffect, useState } from "react";
import { BookOpen, ChevronDown, Eye, Lightbulb, Loader2, Send } from "lucide-react";
import { useRouter } from "next/navigation";
import { cn } from "@/lib/cn";
import { useUIStore } from "@/lib/store";
import { t } from "@/lib/i18n";
import { fetchQuizHint, fetchQuizSubmission, revealQuizAnswer, submitQuizAnswer, type QuizSubmitOutcome } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Textarea } from "@/components/ui/Input";
import { MiniMarkdown } from "./markdown";
import { SubmissionOutcome } from "@/components/learning-evaluation/SubmissionOutcome";
import type { QuizQuestion, QuizSourceRef } from "@/lib/types";

// Streaming tool-result reconciliation can briefly remount a question card while
// the user is typing. Keep a small in-memory draft keyed by authoritative
// question identity so that remounts do not turn an enabled submit button into
// a disabled empty card. Server submission remains the only persisted answer.
const QUIZ_DRAFT_LIMIT = 100;
const quizAnswerDrafts = new Map<string, string>();

function rememberQuizDraft(key: string, value: string): void {
  if (!key) return;
  if (!value) {
    quizAnswerDrafts.delete(key);
    return;
  }
  if (!quizAnswerDrafts.has(key) && quizAnswerDrafts.size >= QUIZ_DRAFT_LIMIT) {
    const oldest = quizAnswerDrafts.keys().next().value as string | undefined;
    if (oldest) quizAnswerDrafts.delete(oldest);
  }
  quizAnswerDrafts.set(key, value);
}

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
  const draftKey = hasIdentity ? `${qid}:${rev}` : "";
  const savedResult = q.result && (q.result.attempt_id || q.result.verdict) ? q.result : null;

  const [selected, setSelected] = useState<string | null>(
    savedResult?.student_answer ?? (draftKey ? quizAnswerDrafts.get(draftKey) ?? null : null),
  );
  const [outcome, setOutcome] = useState<QuizSubmitOutcome | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [hint, setHint] = useState("");
  const [hintLoading, setHintLoading] = useState(false);
  const [revealed, setRevealed] = useState<{ answer: string; explanation: string } | null>(null);
  const [restoring, setRestoring] = useState(hasIdentity && !savedResult);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [canRefresh, setCanRefresh] = useState(false);
  const [expOpen, setExpOpen] = useState(false);
  const [sourcesOpen, setSourcesOpen] = useState(false);

  function updateDraft(value: string) {
    setSelected(value);
    rememberQuizDraft(draftKey, value);
  }

  useEffect(() => {
    if (!qid) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let attempts = 0;
    async function refresh() {
      attempts += 1;
      try {
        const { submission } = await fetchQuizSubmission(qid, rev);
        if (!alive) return;
        if (submission) {
          rememberQuizDraft(draftKey, "");
          setOutcome(submission);
          setSelected(submission.student_answer);
          if (submission.revealed) setRevealed(submission.revealed);
        }
        setRestoring(false);
        if (!submission?.pending) {
          setCanRefresh(false);
          return;
        }
      } catch {
        if (!alive) return;
        // Keep the accepted answer locked on transient lookup failures.
        setRestoring(false);
      }
      if (attempts < 20) timer = setTimeout(() => void refresh(), 2000);
      else setCanRefresh(true);
    }
    void refresh();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [qid, rev, draftKey, refreshVersion]);

  const submitted = !!outcome || !!savedResult;
  // The POST outcome intentionally omits the answer; the controlled selection
  // is the submitted value, while persisted cards hydrate it from result.
  const submittedAnswer = selected ?? savedResult?.student_answer ?? "";
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

  async function submit() {
    if (!hasIdentity || !selected?.trim() || restoring || submitting || submitted) return;
    setSubmitting(true);
    setSubmitError("");
    try {
      const res = await submitQuizAnswer({
        question_id: qid,
        question_revision: rev,
        student_answer: selected,
        session_id: sessionId,
      });
      rememberQuizDraft(draftKey, "");
      setOutcome(res);
      // 揭晓视图：已评价的正式提交后可见（服务端判定）。
      if (res.task_result?.verdict) {
        setRevealed({ answer: q.answer, explanation: q.explanation });
      }
      // Poll the actual result, including task-only grading outside a workspace.
      setRefreshVersion((v) => v + 1);
    } catch (e) {
      setSubmitError((e as Error).message || tr("quiz.grade.error"));
      // A lost POST response or a concurrent-tab submission may already have
      // committed. Recover by identity without issuing another submission.
      setRefreshVersion((v) => v + 1);
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
      <QuestionIllustration illustration={q.illustration} />

      {isMC ? (
        <div className="mt-2.5 space-y-1.5">
          {options.map(([key, val]) => {
            const isSelected = (submitted ? submittedAnswer : selected) === key;
            const isCorrect = submitted && answerText === key;
            let cls = "border-border-light bg-bg hover:border-accent/40 hover:bg-surface";
            if (!submitted && isSelected) cls = "border-accent bg-accent-soft/50 ring-1 ring-accent/30";
            if (submitted && isCorrect) cls = "border-success/50 bg-success/8";
            else if (submitted && isSelected && !isCorrect) cls = "border-danger/50 bg-danger/8";
            else if (submitted) cls = "border-border-light bg-bg opacity-55";
            return (
              <button
                key={key}
                disabled={submitted || submitting || restoring || !hasIdentity}
                onClick={() => updateDraft(key)}
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
      ) : submitted ? (
        <div className="mt-2.5 rounded-[8px] border border-border-light bg-bg px-3 py-2" data-testid="quiz-submitted-answer">
          <p className="mb-1 text-[0.7rem] font-medium text-muted">
            {lang === "en" ? "Submitted answer" : "已提交的答案"}
          </p>
          <MiniMarkdown className="chat-prose whitespace-pre-wrap break-words text-[0.8rem] text-fg">
            {submittedAnswer}
          </MiniMarkdown>
        </div>
      ) : (
        <div className="mt-2.5">
          <Textarea
            disabled={submitting || restoring || !hasIdentity}
            placeholder={hasIdentity ? tr("quiz.answer.placeholder") : tr("quiz.legacy.note", "旧题目仅供回看，新练习请让教练重新出题")}
            className="resize-none text-[0.8rem]"
            rows={2}
            value={selected ?? ""}
            onChange={(e) => updateDraft(e.target.value)}
          />
        </div>
      )}

      {/* 操作行：提交一次 + 提示 + 揭晓（均服务端记录） */}
      {hasIdentity && !submitted && (
        <div className="mt-2.5 flex items-center gap-3">
          <button
            onClick={() => void submit()}
            disabled={!selected?.trim() || submitting || restoring}
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
          <p className="mb-2 text-[0.72rem] font-medium text-muted" data-testid="quiz-submitted-status">
            {lang === "en" ? "Submitted" : "已提交"}
          </p>
          <SubmissionOutcome
            lang={lang}
            data={{
              taskResult: outcome?.task_result ?? {
                verdict: savedResult?.verdict ?? null,
              },
              evaluationStatus: outcome?.evaluation?.status || savedResult?.evaluation?.status || "pending",
              learnerFeedback: outcome?.feedback || "",
            }}
            onViewEvidence={() => router.push("/memory")}
          />
          {canRefresh && (
            <button
              type="button"
              className="mt-2 text-xs text-accent hover:underline"
              onClick={() => { setCanRefresh(false); setRefreshVersion((v) => v + 1); }}
            >
              {lang === "en" ? "Refresh feedback" : "刷新评价"}
            </button>
          )}
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
