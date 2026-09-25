"use client";

import { useState } from "react";
import { Eye, FileQuestion, Flag, ImageIcon, Lightbulb, LoaderCircle, RefreshCw, Send } from "lucide-react";
import { QuestionIllustration } from "@/components/quiz/QuestionIllustration";
import { MiniMarkdown } from "@/components/chat/markdown";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { Textarea } from "@/components/ui/Input";
import { assessmentHint, assessmentReveal } from "@/lib/api-modules";
import { cn } from "@/lib/cn";
import { useUIStore } from "@/lib/store";
import type { AssessmentQuestion } from "@/lib/types-modules";
import { DifficultyDots, difficultyOf, type PageTr } from "./common";
import { useIllustrationEnrichment } from "./useIllustrationEnrichment";

export function isMultipleChoice(question: AssessmentQuestion): boolean {
  return (question.q_type === "multiple_choice" || question.type === "multiple_choice")
    && Boolean(question.options && Object.keys(question.options).length);
}

type Props = {
  tr: PageTr;
  question: AssessmentQuestion;
  difficulty: number;
  answered: number;
  busy: boolean;
  onSubmit: (answer: string) => void;
  onAbandon: () => void;
};

export function QuestionCard(props: Props) {
  return <QuestionBody key={`${props.question.question_id}:${props.question.question_revision}`} {...props} />;
}

function QuestionBody({ tr, question, difficulty, answered, busy, onSubmit, onAbandon }: Props) {
  const english = useUIStore((store) => store.lang === "en");
  const [selected, setSelected] = useState("");
  const [text, setText] = useState("");
  const [hint, setHint] = useState("");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const [revealed, setRevealed] = useState<{ answer: string; explanation: string } | null>(null);
  const draft = question.question_id.startsWith("q_draft_");
  const multipleChoice = isMultipleChoice(question);
  const answer = multipleChoice ? selected : text.trim();
  const disabled = busy || working;
  const level = difficulty > 0 ? difficulty : difficultyOf(question);
  const options = multipleChoice ? Object.entries(question.options ?? {})
    .sort(([first], [second]) => first.localeCompare(second)) : [];
  const illustration = useIllustrationEnrichment(question);

  async function assistance(kind: "hint" | "reveal") {
    if (disabled || draft) return;
    setWorking(true);
    setError("");
    try {
      const revision = question.question_revision || 1;
      if (kind === "hint") {
        const response = await assessmentHint(question.question_id, revision);
        if (response.status === "ok") setHint(response.hint);
      } else {
        const response = await assessmentReveal(question.question_id, revision);
        if (response.status === "ok") setRevealed(response);
      }
    } catch {
      setError(english ? "Could not load help. Please try again." : "暂时无法读取提示或答案，请稍后再试。");
    } finally {
      setWorking(false);
    }
  }

  return (
    <Card className="min-w-0">
      <CardHeader icon={<FileQuestion size={16} />} title={draft ? (english ? "Practice draft" : "自检草稿") : (
        <span className="flex items-center gap-2">{tr("ask.difficulty")}<DifficultyDots level={level} /></span>
      )} right={
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="muted">{tr("ask.answered")} {answered}</Badge>
          <Button type="button" variant="ghost" size="sm" icon={<Flag size={13} />}
            disabled={disabled} onClick={onAbandon}>{tr("abandon")}</Button>
        </div>
      } />
      {draft && <div role="status" className="mb-3 rounded-lg border border-warning/30 bg-warning/5 p-3 text-sm leading-6">
        {english
          ? "This version has not passed review. It is for self-checking only; this page does not submit or grade draft answers. Your notes stay on this page only—copy them before leaving."
          : "正式题目未完成审核，先保留这一版供自检。本页不会提交草稿答案、自动评分或把它当作已审核题。输入内容仅保留在当前页面，离开前请自行复制。"}
      </div>}
      <div className="chat-prose min-w-0 break-words"><MiniMarkdown>{question.stem}</MiniMarkdown></div>
      {illustration.state === "generating" && <div data-testid="assessment-illustration-generating" role="status"
        className="mt-3 flex items-center gap-2 rounded-lg border border-border-light bg-surface-sunken px-3 py-2 text-xs leading-5 text-muted">
        <LoaderCircle size={14} aria-hidden="true" className="shrink-0 animate-spin" />
        <span>{english
          ? "The text question is ready. Generating and checking a diagram in the background…"
          : "文字题已可作答，正在生成并审核配图…"}</span>
      </div>}
      {illustration.state === "failed" && <div data-testid="assessment-illustration-failed" role="status"
        className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-warning/30 bg-warning/5 px-3 py-2 text-xs leading-5 text-muted">
        <span className="flex min-w-0 items-center gap-2">
          <ImageIcon size={14} aria-hidden="true" className="shrink-0" />
          {english ? "The question is usable, but the diagram did not finish." : "文字题仍可正常作答，但本次配图未完成。"}
        </span>
        <Button type="button" size="sm" variant="outline" icon={<RefreshCw size={13} aria-hidden="true" />}
          onClick={illustration.retry}>{english ? "Retry diagram" : "重试配图"}</Button>
      </div>}
      <QuestionIllustration illustration={illustration.illustration} />
      {multipleChoice ? <div className="mt-4 flex flex-col gap-2">
        {options.map(([key, value]) => <button key={key} type="button" disabled={disabled}
          aria-pressed={selected === key} onClick={() => setSelected(key)}
          className={cn("flex items-start gap-3 rounded-lg border p-3 text-left text-sm disabled:opacity-60",
            selected === key ? "border-accent bg-accent-soft text-fg" : "border-border text-fg-secondary hover:border-accent")}>
          <span className="shrink-0 font-medium">{key}.</span>
          <span className="chat-prose min-w-0 break-words"><MiniMarkdown>{value}</MiniMarkdown></span>
        </button>)}
      </div> : <Textarea value={text} onChange={(event) => setText(event.target.value)}
        disabled={disabled} placeholder={tr("ask.answerPh")} rows={5} className="mt-4 resize-y" />}
      {hint && <p className="mt-3 whitespace-pre-wrap rounded-lg bg-warning/5 p-3 text-sm">{hint}</p>}
      {error && <p role="alert" className="mt-3 text-sm text-danger">{error}</p>}
      <div className="mt-4 flex flex-wrap justify-end gap-2">
        {draft ? <Button type="button" variant="outline" disabled={disabled} onClick={onAbandon}>
          {english ? "Back to setup" : "返回配置"}
        </Button> : <>
          <Button type="button" variant="ghost" size="sm" disabled={disabled || Boolean(hint)}
            icon={<Lightbulb size={13} />} onClick={() => void assistance("hint")}>{tr("ask.hint", "关键步骤提示")}</Button>
          <Button type="button" variant="ghost" size="sm" disabled={disabled || Boolean(revealed)}
            icon={<Eye size={13} />} onClick={() => void assistance("reveal")}>{tr("ask.reveal", "看答案")}</Button>
          <Button type="button" size="lg" icon={<Send size={15} />} disabled={disabled || !answer}
            onClick={() => { if (!disabled && answer) onSubmit(answer); }}>
            {busy ? tr("ask.submitting") : tr("ask.submit")}
          </Button>
        </>}
      </div>
      {revealed && <section className="mt-3 rounded-lg border border-border-light p-3 text-sm">
        <p>{tr("ask.revealed.answer", "答案")}: {revealed.answer}</p>
        <div className="chat-prose mt-2"><MiniMarkdown>{revealed.explanation}</MiniMarkdown></div>
      </section>}
    </Card>
  );
}
