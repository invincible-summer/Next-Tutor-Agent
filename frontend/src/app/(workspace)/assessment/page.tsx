"use client";

// /assessment 测评中心：M4 CAT 自适应测试全流程。
// 状态机：idle（配置卡）→ asking（答题）→ feedback（即时判分）→ done（总结报告）。
import { useEffect, useMemo, useState } from "react";
import { ModuleBadge } from "@/components/ui/Badge";
import { EmptyState, ErrorNote, Skeleton } from "@/components/ui/EmptyState";
import {
  assessmentAbandon,
  assessmentActive,
  assessmentAnswer,
  assessmentNext,
  assessmentStart,
  getErrorNotebook,
  getRecentQuizQuestions,
} from "@/lib/api-modules";
import { listSessions } from "@/lib/api";
import { makePageT } from "@/lib/i18n-page";
import { useUIStore } from "@/lib/store";
import type {
  AssessmentQuestion,
  AssessmentSummary,
  ErrorNotebookItem,
  RecentQuizQuestion,
} from "@/lib/types-modules";
import type { SessionItem } from "@/lib/types";
import { STRINGS } from "./strings";
import { ConfigCard, type AssessmentStartIntent } from "@/components/pages/assessment/ConfigCard";
import { QuestionCard } from "@/components/pages/assessment/QuestionCard";
import { FeedbackCard, type AnswerResult } from "@/components/pages/assessment/FeedbackCard";
import { SummaryCard } from "@/components/pages/assessment/SummaryCard";
import { RecentSessions } from "@/components/pages/assessment/RecentSessions";
import { RecentQuestions } from "@/components/pages/assessment/RecentQuestions";
import { ErrorNotebook } from "@/components/pages/assessment/ErrorNotebook";
import { difficultyOf } from "@/components/pages/assessment/common";

type Stage = "idle" | "asking" | "feedback" | "done";

export default function AssessmentPage() {
  const lang = useUIStore((s) => s.lang);
  const grade = useUIStore((s) => s.grade);
  const tr = useMemo(() => makePageT(lang, STRINGS), [lang]);

  // --- CAT 流程状态 ---
  const [stage, setStage] = useState<Stage>("idle");
  const [assessmentId, setAssessmentId] = useState("");
  const [question, setQuestion] = useState<AssessmentQuestion | null>(null);
  const [difficulty, setDifficulty] = useState(0);
  const [answered, setAnswered] = useState(0);
  const [qIndex, setQIndex] = useState(0);
  const [result, setResult] = useState<AnswerResult | null>(null);
  const [stopReason, setStopReason] = useState<string | null>(null);
  const [summary, setSummary] = useState<AssessmentSummary | null>(null);
  const [disabled, setDisabled] = useState(false);
  const [probing, setProbing] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retryFn, setRetryFn] = useState<(() => void) | null>(null);

  // --- 近期练习会话 ---
  const [sessions, setSessions] = useState<SessionItem[] | null>(null);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const [sessionsError, setSessionsError] = useState<string | null>(null);

  // --- 最近习题（跨会话，后端上限 100 道） ---
  const [quizQuestions, setQuizQuestions] = useState<RecentQuizQuestion[] | null>(null);
  const [quizLoading, setQuizLoading] = useState(true);
  const [quizError, setQuizError] = useState<string | null>(null);

  // --- 错题本（跨会话聚合答错/半对） ---
  const [errorItems, setErrorItems] = useState<ErrorNotebookItem[] | null>(null);
  const [errorLoading, setErrorLoading] = useState(true);
  const [errorError, setErrorError] = useState<string | null>(null);

  const [sessionsVersion, setSessionsVersion] = useState(0);

  const fail = (message: string, retry: () => void) => {
    setError(message);
    setRetryFn(() => retry);
  };

  const retrySessions = () => {
    setSessions(null);
    setSessionsLoading(true);
    setSessionsError(null);
    setQuizQuestions(null);
    setQuizLoading(true);
    setQuizError(null);
    setErrorItems(null);
    setErrorLoading(true);
    setErrorError(null);
    setSessionsVersion((v) => v + 1);
  };

  // 挂载后探测 M4 开关并恢复进行中/已终止的 CAT（sessionsVersion 变化时重取）。
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // W2/A03：服务端状态是唯一事实——刷新/重开/双标签都从这里恢复，
        // 不再只探测 disabled 后重置为 idle。
        const res = await assessmentActive();
        if (cancelled) return;
        if (res.status === "disabled") {
          setDisabled(true);
          return;
        }
        if (res.status !== "ok") return; // none：无会话，停留 idle
        if (res.session_status === "active") {
          // 恢复进行中的 CAT（§11.5）：assessment_id 必须一起恢复，
          // 否则恢复出的题目无法提交（handleSubmit 的守卫会静默 return）。
          setAssessmentId(res.assessment_id || "");
          setAnswered(res.answered ?? 0);
          setQIndex(res.answered ?? 0);
          if (res.question) {
            setQuestion(res.question);
            setDifficulty(difficultyOf(res.question));
            setStage("asking");
          } else if (res.assessment_id) {
            // 上一题已提交但还没取下一题：刷新后自动续跑取下一题，
            // 不能把进行中的实例渲染成"总结"（会误导用户已结束）。
            try {
              const nx = await assessmentNext(res.assessment_id);
              if (cancelled) return;
              if (nx.status === "ok" && nx.question && !nx.stop_reason) {
                setQuestion(nx.question);
                setDifficulty(nx.difficulty ?? difficultyOf(nx.question));
                setStage("asking");
              } else {
                setSummary(nx.summary ?? res.summary ?? null);
                setStopReason(nx.stop_reason || res.stop_reason || null);
                setStage("done");
              }
            } catch {
              // 取下一题失败：保留恢复出的总结兜底，可点"再来一次"重开
              setSummary(res.summary ?? null);
              setStage("done");
            }
          }
        } else if (res.summary) {
          setSummary(res.summary);
          setStopReason(res.stop_reason || null);
          setAnswered(res.answered ?? 0);
          setStage("done");
        }
      } catch {
        // 探测失败不阻塞页面：真正调用时会再报
      } finally {
        if (!cancelled) setProbing(false);
      }
    })();
    (async () => {
      try {
        const res = await listSessions();
        const practice = (res.sessions ?? [])
          .filter((s) => s.quiz_count > 0)
          .sort((a, b) => b.updated_at - a.updated_at);
        if (!cancelled) {
          setSessions(practice);
          setSessionsLoading(false);
        }
      } catch {
        if (!cancelled) {
          setSessionsError(makePageT(lang, STRINGS)("recent.loadFail"));
          setSessionsLoading(false);
        }
      }
    })();
    (async () => {
      try {
        const res = await getRecentQuizQuestions();
        if (!cancelled) {
          setQuizQuestions(Array.isArray(res.questions) ? res.questions : []);
          setQuizLoading(false);
        }
      } catch {
        if (!cancelled) {
          setQuizError(makePageT(lang, STRINGS)("rq.loadFail"));
          setQuizLoading(false);
        }
      }
    })();
    (async () => {
      try {
        const res = await getErrorNotebook();
        if (!cancelled) {
          setErrorItems(Array.isArray(res.items) ? res.items : []);
          setErrorLoading(false);
        }
      } catch {
        if (!cancelled) {
          setErrorError(makePageT(lang, STRINGS)("eb.loadFail"));
          setErrorLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionsVersion, lang]);

  const resetFlow = () => {
    setStage("idle");
    setAssessmentId("");
    setQuestion(null);
    setResult(null);
    setSummary(null);
    setStopReason(null);
    setAnswered(0);
    setDifficulty(0);
    setError(null);
    setRetryFn(null);
  };

  const handleStart = async (intent: AssessmentStartIntent) => {
    setBusy(true);
    setError(null);
    try {
      const res = await assessmentStart({
        workspace_id: intent.workspaceId,
        concept_keys: intent.conceptKeys,
        goal: { purpose: intent.purpose },
        count: intent.count,
        grade,
      });
      if (res.status === "disabled") {
        setDisabled(true);
        return;
      }
      if (res.status !== "ok" || !res.question) {
        fail(res.message || tr("err.start"), () => handleStart(intent));
        return;
      }
      setAssessmentId(res.assessment_id || "");
      setQuestion(res.question);
      setDifficulty(res.difficulty ?? difficultyOf(res.question));
      setAnswered(0);
      setQIndex(0);
      setStopReason(null);
      setSummary(null);
      setStage("asking");
    } catch {
      fail(tr("err.start"), () => handleStart(intent));
    } finally {
      setBusy(false);
    }
  };

  const handleSubmit = async (studentAnswer: string) => {
    if (!question || !assessmentId || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await assessmentAnswer({
        assessment_id: assessmentId,
        question_id: question.question_id,
        question_revision: question.question_revision || 1,
        student_answer: studentAnswer,
      });
      if (res.status === "disabled") {
        setDisabled(true);
        return;
      }
      if (res.status !== "ok") {
        fail(tr("err.answer"), () => handleSubmit(studentAnswer));
        return;
      }
      setResult({
        taskResult: res.task_result ?? null,
        evaluationStatus: res.evaluation?.status || "pending",
        learnerFeedback: "",
      });
      setAnswered((n) => n + 1);
      setStopReason(res.stop_reason || null);
      if (res.summary) setSummary(res.summary);
      setStage("feedback");
    } catch {
      fail(tr("err.answer"), () => handleSubmit(studentAnswer));
    } finally {
      setBusy(false);
    }
  };

  const handleNext = async () => {
    // answer 响应已带 stop_reason：直接进总结，无需再调 next。
    if (stopReason) {
      setStage("done");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await assessmentNext(assessmentId);
      if (res.status === "disabled") {
        setDisabled(true);
        return;
      }
      if (res.status !== "ok") {
        fail(tr("err.noActive"), resetFlow);
        return;
      }
      if (res.stop_reason) {
        setSummary(res.summary ?? summary);
        setStage("done");
        return;
      }
      if (!res.question) {
        // 无新题且无停止理由（如旧数据终态无 stop_reason）：有总结则进总结。
        if (res.summary) {
          setSummary(res.summary);
          setStage("done");
        } else {
          fail(tr("err.next"), handleNext);
        }
        return;
      }
      setQuestion(res.question);
      setDifficulty(res.difficulty ?? difficultyOf(res.question));
      setQIndex((i) => i + 1);
      setResult(null);
      setStage("asking");
    } catch {
      fail(tr("err.next"), handleNext);
    } finally {
      setBusy(false);
    }
  };

  const handleAbandon = async () => {
    setBusy(true);
    setError(null);
    try {
      await assessmentAbandon(assessmentId);
      resetFlow();
    } catch {
      fail(tr("err.abandon"), handleAbandon);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="h-full overflow-y-auto p-6 page-in">
      <div className="mx-auto flex max-w-[1200px] flex-col gap-4">
        {/* 页头 */}
        <div className="flex items-start gap-3">
          <div>
            <h1 className="flex items-center gap-2.5 font-serif text-xl font-semibold text-fg">
              {tr("nav.assessment")}
              <ModuleBadge id="M4" />
            </h1>
            <p className="mt-1 text-sm text-muted">{tr("page.desc")}</p>
          </div>
        </div>

        {/* CAT 流程卡 */}
        {probing ? (
          <Skeleton className="h-56" />
        ) : disabled ? (
          <EmptyState
            title={tr("disabled.title")}
            desc={tr("disabled.desc")}
          />
        ) : (
          <>
            {error && (
              <ErrorNote
                message={error}
                retry={retryFn ?? undefined}
              />
            )}
            {stage === "idle" && (
              <ConfigCard tr={tr} lang={lang} busy={busy} onStart={handleStart} />
            )}
            {stage === "asking" && question && (
              <QuestionCard
                key={qIndex}
                tr={tr}
                question={question}
                difficulty={difficulty}
                answered={answered}
                busy={busy}
                onSubmit={handleSubmit}
                onAbandon={handleAbandon}
              />
            )}
            {stage === "feedback" && result && (
              <FeedbackCard
                tr={tr}
                lang={lang}
                result={result}
                stop={!!stopReason}
                busy={busy}
                onNext={handleNext}
                onAbandon={handleAbandon}
              />
            )}
            {stage === "done" &&
              (summary ? (
                <SummaryCard tr={tr} lang={lang} summary={summary} onAgain={resetFlow} />
              ) : (
                <EmptyState
                  title={tr("sum.empty")}
                  action={
                    <button
                      onClick={resetFlow}
                      className="cursor-pointer text-sm font-medium text-accent underline"
                    >
                      {tr("sum.again")}
                    </button>
                  }
                />
              ))}
          </>
        )}

        {/* 错题本（跨会话聚合，分页 + 重练深链） */}
        <ErrorNotebook
          tr={tr}
          lang={lang}
          items={errorItems}
          loading={errorLoading}
          error={errorError}
          onRetry={retrySessions}
        />

        {/* 最近习题（跨会话，分页） */}
        <RecentQuestions
          tr={tr}
          lang={lang}
          questions={quizQuestions}
          loading={quizLoading}
          error={quizError}
          onRetry={retrySessions}
        />

        {/* 近期练习会话 */}
        <RecentSessions
          tr={tr}
          lang={lang}
          sessions={sessions}
          loading={sessionsLoading}
          error={sessionsError}
          onRetry={retrySessions}
        />
      </div>
    </div>
  );
}
