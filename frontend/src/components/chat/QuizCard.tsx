"use client";
import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Eye, Lightbulb, Loader2, Send, X } from "lucide-react";
import { cn } from "@/lib/cn";
import { useUIStore } from "@/lib/store";
import { t } from "@/lib/i18n";
import { fetchQuizHint, gradeAnswer, recordAnswer, type GradeVerdict, type QuizStructuredFeedback } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { MiniMarkdown } from "./markdown";
import type { QuizQuestion } from "@/lib/types";
import { useChatStore } from "@/lib/store";

interface MasteryResult {
  score: number;
  concept_status: string;
}

/** 单题练习卡：题号 + 题型徽章 + 题干 + 选项/作答交互 + 提交/揭晓 + 解析折叠。
 *  MC 本地判分并上报 /quiz/record（掌握度闭环）；填空/简答走 gradeAnswer SSE。 */
export function QuizQuestionCard({
  question: q,
  index,
  grade,
  sessionId,
}: {
  question: QuizQuestion;
  index: number;
  grade?: string;
  sessionId?: string;
}) {
  const { lang } = useUIStore();
  const tr = (k: string, fb?: string) => t(lang, k, fb);
  // 已作答恢复（刷新/重开会话后锁定为已答状态，禁止重复作答——同一题多条
  // 作答记录会让后续 Agent 分析混乱）
  const savedResult = q.result && q.result.verdict ? q.result : null;
  const [selected, setSelected] = useState<string | null>(savedResult?.student_answer ?? null);
  const [revealed, setRevealed] = useState(!!savedResult);
  const [expOpen, setExpOpen] = useState(false);
  const [mastery, setMastery] = useState<MasteryResult | null>(null);
  const reportedRef = useRef(!!savedResult);
  // LLM grading state for fill-in / short-answer
  const [grading, setGrading] = useState(false);
  const [verdict, setVerdict] = useState<GradeVerdict>((savedResult?.verdict as GradeVerdict) ?? null);
  const [feedback, setFeedback] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const noteFiredRef = useRef(false);
  const appendAssistantNote = useChatStore((s) => s.appendAssistantNote);
  // W3/F01 结构化批改明细（量规条目驱动；刷新后从已存 result 恢复）。
  const [structured, setStructured] = useState<QuizStructuredFeedback | null>(
    (savedResult as { structured?: QuizStructuredFeedback } | null)?.structured ?? null,
  );
  const nextStepFiredRef = useRef(false);
  // W3/F02 关键步骤提示（量规派生；服务端记录 assistance=hint）。
  const [hint, setHint] = useState("");
  const [hintLoading, setHintLoading] = useState(false);

  async function loadHint() {
    if (!sessionId || hintLoading) return;
    setHintLoading(true);
    try {
      const res = await fetchQuizHint(sessionId, q.stem);
      setHint(res.status === "ok" ? res.hint : (res.message || tr("quiz.hint.none")));
    } catch {
      setHint(tr("quiz.hint.error"));
    } finally {
      setHintLoading(false);
    }
  }

  /** F01「就此练习」：把下一步追问追加到对话流，学生在对话里回应——
   *  D02 之后短消息/追问的指代由会话上下文保持。 */
  function fireNextStep() {
    const next = structured?.feedback?.next_step?.trim();
    if (nextStepFiredRef.current || !next) return;
    nextStepFiredRef.current = true;
    appendAssistantNote(next);
  }

  const isMC = q.type === "multiple_choice" && q.options;
  const correct = selected !== null && selected === q.answer;
  const options = q.options ? Object.entries(q.options) : [];

  /** 互动后在对话流底部追加一条 agent 点评（判定 + 解析），每轮作答只发一次。 */
  function fireFollowupNote(text: string) {
    if (noteFiredRef.current || !text.trim()) return;
    noteFiredRef.current = true;
    appendAssistantNote(text);
  }

  function handleMcReveal() {
    if (!selected) return;
    setRevealed(true);
    void streamMcCommentary(selected);
  }

  /** MC 点评：走 /quiz/grade（record=false，判定已由 /quiz/record 确定性记录），
   *  拿 LLM 生成的个性化讲解作为对话流里的 agent 反馈——不再复制卡片静态解析。 */
  async function streamMcCommentary(choice: string) {
    const isRight = choice === q.answer;
    const head = tr(isRight ? "quiz.followup.correct" : "quiz.followup.wrong")
      .replace("%n", String(index + 1))
      .replace("%s", choice)
      .replace("%a", q.answer);
    let fb = "";
    try {
      for await (const ev of gradeAnswer({
        stem: q.stem,
        q_type: "multiple_choice",
        student_answer: choice,
        correct_answer: q.answer,
        explanation: q.explanation || "",
        knowledge_point: q.knowledge_point || "",
        grade: grade || "本科",
        session_id: sessionId,
        record: false,
      })) {
        if (ev.type === "done") fb = ev.feedback || ev.full || "";
      }
    } catch { /* 点评失败时只发判定行 */ }
    fireFollowupNote(fb ? `${head}\n\n${fb}` : head);
  }

  // M4: report the MC result once it is revealed so it feeds the Student Model
  // mastery loop (closes the section-14.14 gap where MC was client-only). Fire
  // and forget; failures never block the UI. Reports exactly once per reveal.
  useEffect(() => {
    if (!isMC || !revealed || reportedRef.current || selected === null) return;
    reportedRef.current = true;
    recordAnswer({
      stem: q.stem,
      q_type: q.type || "multiple_choice",
      student_answer: selected,
      correct_answer: q.answer,
      options: q.options || {},
      explanation: q.explanation || "",
      knowledge_point: q.knowledge_point || "",
      grade: grade || "本科",
      session_id: sessionId,
      difficulty: 3,
    })
      .then((res) => {
        const r = res?.result;
        if (r && typeof r.score === "number" && r.concept_status) {
          setMastery({ score: r.score, concept_status: r.concept_status });
        }
      })
      .catch(() => { /* best-effort; never block */ });
  }, [isMC, revealed, selected, q, grade, sessionId]);

  async function submitForGrading() {
    if (!selected || grading) return;
    const ac = new AbortController();
    abortRef.current = ac;
    setGrading(true);
    setFeedback("");
    setVerdict(null);
    try {
      for await (const ev of gradeAnswer({
        stem: q.stem,
        q_type: q.type || "short_answer",
        student_answer: selected,
        correct_answer: q.answer,
        explanation: q.explanation || "",
        knowledge_point: q.knowledge_point || "",
        grade: grade || "本科",
        session_id: sessionId,
      }, ac.signal)) {
        if (ev.type === "delta") setFeedback((p) => p + ev.content);
        else if (ev.type === "done") {
          setVerdict(ev.verdict);
          if (ev.structured) setStructured(ev.structured);
          // 完整批改讲解移到对话流底部的 agent 点评，卡片只保留判定，
          // 避免卡片与 agent 反馈内容重复。
          const fb = ev.feedback || ev.full || "";
          setFeedback("");
          const vLabel = ev.verdict === "correct" ? tr("quiz.correct")
            : ev.verdict === "partial" ? tr("quiz.partial")
            : tr("quiz.wrong");
          let note = tr("quiz.followup.graded")
            .replace("%n", String(index + 1))
            .replace("%v", vLabel);
          if (fb) note += `\n\n${fb}`;
          fireFollowupNote(note);
        }
        else if (ev.type === "error") { setFeedback(tr("quiz.grade.error") + "：" + ev.message); setVerdict("wrong"); }
      }
    } catch (e) {
      const aborted = ac.signal.aborted || (e instanceof DOMException && e.name === "AbortError");
      if (!aborted) setFeedback(tr("quiz.grade.error") + "：" + ((e as Error).message || String(e)));
    } finally {
      setGrading(false);
      abortRef.current = null;
    }
  }

  const graded = isMC ? revealed : !!verdict || revealed;

  return (
    <div className="rounded-[10px] border border-border bg-surface p-3 shadow-sm">
      {/* 题号 + 题型 */}
      <div className="flex items-center gap-2">
        <span className="flex h-5 w-5 items-center justify-center rounded-[5px] bg-accent-soft font-mono text-[0.68rem] font-semibold text-accent-strong">
          {index + 1}
        </span>
        <Badge tone="outline">{tr(`quiz.type.${q.type || "multiple_choice"}`, q.type)}</Badge>
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
            const isCorrect = key === q.answer;
            let cls = "border-border-light bg-bg hover:border-accent/40 hover:bg-surface";
            // Selected-but-not-yet-revealed: give a clear visual so clicking
            // actually shows feedback (the old bug was no highlight on select,
            // so it looked like clicks did nothing).
            if (!revealed && isSelected) cls = "border-accent bg-accent-soft/50 ring-1 ring-accent/30";
            if (revealed && isCorrect) cls = "border-success/50 bg-success/8";
            else if (revealed && isSelected && !isCorrect) cls = "border-danger/50 bg-danger/8";
            else if (revealed) cls = "border-border-light bg-bg opacity-55";
            return (
              <button
                key={key}
                disabled={revealed}
                onClick={() => setSelected(key)}
                className={cn(
                  "flex w-full items-center gap-2.5 rounded-[8px] border px-3 py-2 text-left text-[0.8rem] transition-all",
                  cls,
                  !revealed ? "cursor-pointer" : "cursor-default",
                )}
              >
                <span className={cn(
                  "flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[0.68rem] font-semibold",
                  !revealed && isSelected ? "border-accent bg-accent text-white" : "border-border text-fg-secondary",
                  revealed && isCorrect ? "border-success bg-success text-white" : "",
                  revealed && isSelected && !isCorrect ? "border-danger bg-danger text-white" : "",
                )}>
                  {key}
                </span>
                <MiniMarkdown className="chat-prose flex-1 text-[0.8rem] text-fg-secondary">{val}</MiniMarkdown>
                {revealed && isCorrect && <Check size={14} className="shrink-0 text-success" />}
                {revealed && isSelected && !isCorrect && <X size={14} className="shrink-0 text-danger" />}
              </button>
            );
          })}
        </div>
      ) : (
        <div className="mt-2.5">
          <textarea
            disabled={grading || !!verdict || revealed}
            placeholder={tr("quiz.answer.placeholder")}
            className="w-full resize-none rounded-[8px] border border-border bg-bg px-3 py-2 text-[0.8rem] text-fg outline-none placeholder:text-muted focus:border-accent/40 disabled:opacity-60"
            rows={2}
            value={selected ?? ""}
            onChange={(e) => setSelected(e.target.value)}
          />
        </div>
      )}

      {/* 操作行：先答后揭晓 */}
      <div className="mt-2.5 flex items-center gap-3">
        {isMC && !revealed && (
          <button
            onClick={handleMcReveal}
            disabled={!selected}
            className="flex items-center gap-1.5 text-[0.75rem] font-medium text-accent transition-colors hover:text-accent-strong disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Eye size={13} /> {tr("quiz.reveal")}
          </button>
        )}
        {!isMC && !verdict && !revealed && (
          <button
            onClick={submitForGrading}
            disabled={!selected || grading}
            className="flex items-center gap-1.5 text-[0.75rem] font-medium text-accent transition-colors hover:text-accent-strong disabled:cursor-not-allowed disabled:opacity-40"
          >
            {grading ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} />}
            {grading ? tr("quiz.grading") : tr("quiz.submit")}
          </button>
        )}
        {!isMC && !verdict && !revealed && !grading && (
          <button
            onClick={() => setRevealed(true)}
            className="text-[0.7rem] text-muted transition-colors hover:text-fg"
          >
            {tr("quiz.reference")}
          </button>
        )}
        {sessionId && !graded && (
          <button
            onClick={loadHint}
            disabled={hintLoading || !!hint}
            className="flex items-center gap-1 text-[0.7rem] text-muted transition-colors hover:text-accent disabled:cursor-not-allowed disabled:opacity-50"
          >
            {hintLoading ? <Loader2 size={12} className="animate-spin" /> : <Lightbulb size={12} />}
            {tr("quiz.hint")}
          </button>
        )}
      </div>

      {/* W3/F02 关键步骤提示（不含答案，先答后揭晓不破） */}
      {hint && !graded && (
        <div className="mt-2 rounded-[8px] border border-warning/30 bg-warning/5 px-3 py-2">
          <p className="whitespace-pre-wrap text-[0.75rem] leading-relaxed text-fg-secondary">{hint}</p>
        </div>
      )}

      {/* 判定结果 */}
      {isMC && revealed && (
        <p className={cn("mt-2.5 flex items-center gap-1.5 text-[0.78rem] font-medium", correct ? "text-success" : "text-danger")}>
          {correct
            ? <><Check size={14} /> {tr("quiz.correct")}</>
            : <><X size={14} /> {tr("quiz.answer.label")}: {q.answer}</>}
        </p>
      )}
      {!isMC && verdict && (
        <div className="mt-2.5 space-y-1.5">
          <p className={cn("flex items-center gap-1.5 text-[0.78rem] font-medium", verdict === "correct" ? "text-success" : "text-danger")}>
            {verdict === "correct" ? <Check size={14} /> : <X size={14} />}
            {verdict === "correct" ? tr("quiz.correct") : tr("quiz.wrong")}
          </p>
          {feedback && (
            <div className="flex items-start gap-1.5 text-muted">
              <Lightbulb size={13} className="mt-0.5 shrink-0 text-warning" />
              <MiniMarkdown className="chat-prose whitespace-pre-wrap text-[0.78rem] leading-relaxed">{feedback}</MiniMarkdown>
            </div>
          )}
          <p className="text-[0.68rem] text-muted/70">
            {tr("quiz.answer.reference")}: {q.answer}
            {q.knowledge_point ? ` · ${tr("quiz.knowledge")}: ${q.knowledge_point}` : ""}
          </p>
        </div>
      )}
      {/* Streaming feedback while grading */}
      {!isMC && grading && feedback && (
        <div className="mt-2.5 flex items-start gap-1.5 text-muted">
          <Lightbulb size={13} className="mt-0.5 shrink-0 text-warning" />
          <MiniMarkdown className="chat-prose whitespace-pre-wrap text-[0.78rem] leading-relaxed">{feedback}</MiniMarkdown>
        </div>
      )}
      {/* Reference-only reveal fallback */}
      {!isMC && !verdict && revealed && (
        <div className="mt-2.5 space-y-1.5">
          <p className="flex items-center gap-1.5 text-[0.78rem] font-medium text-success">
            <Check size={14} /> {tr("quiz.answer.reference")}: {q.answer}
          </p>
          <button onClick={() => setRevealed(false)} className="text-[0.72rem] text-accent transition-colors hover:text-accent-strong">
            {tr("quiz.back.grading")}
          </button>
        </div>
      )}

      {/* W3/F01 结构化明细：首个实质错误 / 此前做对的部分 / 错因假设 / 下一步 */}
      {graded && structured && (verdict || revealed) && (
        <div className="mt-2.5 space-y-1.5 rounded-[8px] border border-border-light bg-bg/60 px-3 py-2">
          {!!structured.first_error?.description && (
            <p className="text-[0.75rem] text-fg-secondary">
              <span className="font-medium text-danger">{tr("quiz.f01.first_error")}</span>
              {" "}{structured.first_error.description}
              {!!structured.first_error.preceding_correct && (
                <span className="text-muted"> · {tr("quiz.f01.correct_prefix")}{structured.first_error.preceding_correct}</span>
              )}
            </p>
          )}
          {!!structured.hypotheses?.length && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[0.7rem] text-muted">{tr("quiz.f01.hypotheses")}</span>
              {structured.hypotheses.slice(0, 2).map((h, i) => (
                <Badge key={i} tone="outline">{h.statement}</Badge>
              ))}
            </div>
          )}
          {!!structured.feedback?.next_step && (
            <div className="flex items-start justify-between gap-2">
              <p className="text-[0.75rem] text-accent-strong">
                {tr("quiz.f01.next_step")}{structured.feedback.next_step}
              </p>
              <button
                onClick={fireNextStep}
                className="shrink-0 text-[0.7rem] font-medium text-accent transition-colors hover:text-accent-strong"
              >
                {tr("quiz.f01.practice")}
              </button>
            </div>
          )}
        </div>
      )}

      {/* 解析折叠 */}
      {graded && q.explanation && (
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
            <MiniMarkdown className="chat-prose mt-1.5 text-[0.78rem] leading-relaxed text-fg-secondary">{q.explanation}</MiniMarkdown>
          )}
        </div>
      )}

      {/* 掌握度闭环提示 */}
      {mastery && (
        <p className="mt-2 flex items-center gap-1 text-[0.68rem] text-muted">
          <Check size={11} className="text-success" />
          {tr("quiz.mastery.updated")}
          {q.knowledge_point ? ` · ${q.knowledge_point}` : ""}
          <span className="tnum"> {Math.round(mastery.score * 100)}%</span>
        </p>
      )}
      {isMC && revealed && !mastery && q.knowledge_point && (
        <p className="mt-2 text-[0.68rem] text-muted/60">
          {tr("quiz.knowledge")}: {q.knowledge_point}
        </p>
      )}
    </div>
  );
}
