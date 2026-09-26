"use client";
/* 课堂检查点面板（plan.md §13.1/§13.3，阶段 I02）。
 *
 * 检查点在其所属页的最后一段结束后触发（页面层控制时机）：reflect 型
 * 只做思考停顿（继续/再想一会儿，不判分不写证据）；question 型复用
 * QuizQuestionCard 同一视图，transport 走 R/checkpoints/{cid}/*——
 * 提示/揭晓先在服务端持久化帮助事件，跳过不算答错（§13.3）。
 */
import { useEffect, useMemo, useState } from "react";
import { Timer, X } from "lucide-react";
import {
  QuizQuestionCard, type QuizCardTransport,
} from "@/components/chat/QuizCard";
import type { QuizQuestion } from "@/lib/types";
import { apiFetch } from "@/lib/api-fetch";
import { API_BASE } from "@/lib/api";

export interface CheckpointPanelStrings {
  reflectTitle: string;
  reflectContinue: string;
  reflectThinkMore: string;
  questionTitle: string;
  skip: string;
  skipped: string;
}

interface CheckpointData {
  checkpoint_id: string;
  kind: "reflect" | "question";
  prompt: string;
  reflection_seconds?: number | null;
  run_state?: "pending" | "answered" | "skipped" | null;
  question?: {
    question_id: string;
    question_revision: number;
    q_type: string;
    stem: string;
    options?: Record<string, string>;
    source_badge?: string;
  } | null;
}

export function CheckpointPanel(
  { workspaceId, lessonId, runId, checkpointId, onResolved, onSkip, s }:
  {
    workspaceId: string;
    lessonId: string;
    runId: string;
    checkpointId: string;
    /** 作答/揭晓/跳过后回调（页面解除翻页闸门）。 */
    onResolved: () => void;
    onSkip?: () => void;
    s: CheckpointPanelStrings;
  },
) {
  const [data, setData] = useState<CheckpointData | null>(null);
  const [skipped, setSkipped] = useState(false);

  const base = useMemo(
    () => `${API_BASE}/workspaces/${encodeURIComponent(workspaceId)}` +
      `/classroom/lessons/${encodeURIComponent(lessonId)}` +
      `/runs/${encodeURIComponent(runId)}/checkpoints/` +
      encodeURIComponent(checkpointId),
    [workspaceId, lessonId, runId, checkpointId]);

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const res = await apiFetch(base);
        const body = await res.json() as CheckpointData;
        if (alive && res.ok) setData(body);
      } catch { /* 面板静默重试由刷新触发 */ }
    })();
    return () => { alive = false; };
  }, [base]);

  const resolved = data?.run_state === "answered"
    || data?.run_state === "skipped" || skipped;

  const doSkip = async () => {
    try {
      const res = await apiFetch(base + "/skip", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_state_revision: null }),
      });
      if (res.ok) {
        setSkipped(true);
        onSkip?.();
        onResolved();
      }
    } catch { /* 保留面板可重试 */ }
  };

  if (!data) return null;

  // reflect：思考停顿，不自动判分、不写学习证据（§13.1）
  if (data.kind !== "question" || !data.question) {
    return (
      <section aria-label={s.reflectTitle}
               className="motion-pop absolute inset-x-4 bottom-4 z-10 mx-auto max-w-2xl rounded-[14px] border border-border bg-surface p-4 shadow-2xl">
        <p className="flex items-start gap-2 text-[0.92rem] font-medium text-fg">
          <Timer size={15} className="mt-0.5 shrink-0 text-accent" />
          {data.prompt}
        </p>
        <div className="mt-3 flex items-center gap-2">
          <button type="button" onClick={onResolved}
                  className="inline-flex h-11 cursor-pointer items-center rounded-[10px] bg-accent px-5 text-sm font-medium text-on-accent hover:bg-accent/85">
            {s.reflectContinue}
          </button>
          {data.reflection_seconds ? (
            <span className="text-xs text-muted">
              {s.reflectThinkMore}
            </span>
          ) : null}
        </div>
      </section>
    );
  }

  const q = data.question;
  const questionView: QuizQuestion = {
    question_id: q.question_id,
    question_revision: q.question_revision,
    type: q.q_type,
    stem: q.stem,
    options: q.options,
  } as QuizQuestion;

  const qid = q.question_id;
  const rev = q.question_revision;
  const transport: QuizCardTransport = {
    submit: async (body) => {
      const res = await apiFetch(base + "/submit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question_ref: `${qid}:${rev}`,
          student_answer: body.student_answer,
          idempotency_key: `ck-${qid}-${body.student_answer.length}`
            + `-${body.student_answer.slice(0, 8)}`.slice(0, 128),
        }),
      });
      if (!res.ok) throw new Error(`submit failed: ${res.status}`);
      const payload = await res.json() as {
        task_result?: { verdict?: string } | null;
        evaluation_status?: string;
      };
      // 受理后本面板进入已答形态（✓ + 继续上课）；服务端已持久化。
      setData((d) => d ? { ...d, run_state: "answered" } : d);
      return {
        task_result: payload.task_result ?? null,
        evaluation: { status: payload.evaluation_status ?? "pending" },
        feedback: "",
      } as unknown as Awaited<ReturnType<QuizCardTransport["submit"]>>;
    },
    hint: async () => {
      const key = `ck-hint-${qid}-${Date.now()}`.slice(0, 128);
      const res = await apiFetch(base + "/hint", {
        method: "POST",
        headers: { "Idempotency-Key": key },
      });
      if (!res.ok) throw new Error("hint failed");
      const body = await res.json() as { hint: string };
      return { status: "ok", hint: body.hint };
    },
    reveal: async () => {
      const res = await apiFetch(base + "/reveal", {
        method: "POST",
        headers: { "Idempotency-Key": `ck-reveal-${qid}`.slice(0, 128) },
      });
      if (!res.ok) throw new Error("reveal failed");
      return await res.json() as { status: "ok"; answer: string;
                                   explanation: string };
    },
    submission: async () => {
      const res = await apiFetch(base + "/submission");
      if (!res.ok) return { submission: null };
      return await res.json() as Awaited<ReturnType<
        QuizCardTransport["submission"]>>;
    },
  };

  return (
    <section aria-label={s.questionTitle}
             className="motion-drawer absolute inset-y-0 right-0 z-20 flex w-full max-w-[480px] flex-col overflow-y-auto border-l border-border bg-surface p-4 shadow-xl sm:w-[480px]">
      <div className="flex items-center gap-2">
        <h2 className="flex-1 text-sm font-semibold text-fg">
          {s.questionTitle}
        </h2>
        {resolved ? (
          <span className="rounded-full border border-border px-2.5 py-0.5 text-xs text-muted">
            {skipped || data.run_state === "skipped" ? s.skipped : "✓"}
          </span>
        ) : (
          <button type="button" onClick={() => void doSkip()}
                  className="inline-flex h-9 cursor-pointer items-center gap-1 rounded-full border border-border px-3 text-xs text-muted transition-colors hover:text-fg">
            <X size={12} /> {s.skip}
          </button>
        )}
      </div>
      <div className="mt-3">
        <QuizQuestionCard question={questionView} index={0}
                          transport={transport} />
      </div>
      {resolved && (
        <button type="button" onClick={onResolved}
                className="mt-3 inline-flex h-11 w-full cursor-pointer items-center justify-center rounded-[10px] bg-accent text-sm font-medium text-on-accent hover:bg-accent/85">
          {s.reflectContinue}
        </button>
      )}
    </section>
  );
}
