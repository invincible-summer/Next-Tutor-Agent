"use client";

// asking 阶段：答题卡（题干 + 选项/简答 + 提交）。
import { useState } from "react";
import { Send, FileQuestion, Flag } from "lucide-react";
import { cn } from "@/lib/cn";
import { Card, CardHeader } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import type { AssessmentQuestion } from "@/lib/types-modules";
import { MiniMarkdown } from "@/components/chat/markdown";
import { DifficultyDots, difficultyOf, type PageTr } from "./common";

export function isMultipleChoice(q: AssessmentQuestion): boolean {
  return (
    (q.q_type === "multiple_choice" || q.type === "multiple_choice") &&
    !!q.options &&
    Object.keys(q.options).length > 0
  );
}

export function QuestionCard({
  tr,
  question,
  difficulty,
  answered,
  busy,
  onSubmit,
  onAbandon,
}: {
  tr: PageTr;
  question: AssessmentQuestion;
  difficulty: number;
  answered: number;
  busy: boolean;
  onSubmit: (answer: string) => void;
  onAbandon: () => void;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [text, setText] = useState("");

  const mc = isMultipleChoice(question);
  const options = mc
    ? Object.entries(question.options ?? {}).sort(([a], [b]) => a.localeCompare(b))
    : [];
  const answer = mc ? selected ?? "" : text.trim();
  const level = difficulty > 0 ? difficulty : difficultyOf(question);
  const bloomLv = typeof question.bloom_level === "string" ? question.bloom_level : "";
  const BLOOM_ZH: Record<string, string> = {
    remember: "记忆", understand: "理解", apply: "应用",
    analyze: "分析", evaluate: "评价", create: "创造",
  };

  return (
    <Card>
      <CardHeader
        icon={<FileQuestion size={16} />}
        title={
          <span className="flex items-center gap-2">
            {tr("ask.difficulty")}
            <DifficultyDots level={level} />
            {bloomLv && (
              <Badge tone="info">
                {BLOOM_ZH[bloomLv] ?? bloomLv}
              </Badge>
            )}
          </span>
        }
        right={
          <div className="flex items-center gap-2">
            <Badge tone="muted" className="tnum">
              {tr("ask.answered")} {answered}
            </Badge>
            <Button variant="ghost" size="sm" icon={<Flag size={13} />} disabled={busy} onClick={onAbandon}>
              {tr("abandon")}
            </Button>
          </div>
        }
      />
      <div className="chat-prose">
        <MiniMarkdown>{question.stem}</MiniMarkdown>
      </div>

      {mc ? (
        <div className="mt-4 flex flex-col gap-2">
          {options.map(([key, value]) => (
            <button
              key={key}
              type="button"
              disabled={busy}
              onClick={() => setSelected(key)}
              className={cn(
                "flex cursor-pointer items-start gap-3 rounded-[8px] border px-3.5 py-2.5 text-left text-sm transition-colors",
                "disabled:cursor-not-allowed disabled:opacity-60",
                selected === key
                  ? "border-accent bg-accent-soft/60 text-fg"
                  : "border-border bg-surface text-fg-secondary hover:border-accent/50 hover:bg-surface-hover",
              )}
            >
              <span
                className={cn(
                  "mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[11px] font-semibold",
                  selected === key
                    ? "border-accent bg-accent text-white"
                    : "border-border text-muted",
                )}
              >
                {key}
              </span>
              <span className="chat-prose leading-relaxed">
                <MiniMarkdown>{value}</MiniMarkdown>
              </span>
            </button>
          ))}
        </div>
      ) : (
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={tr("ask.answerPh")}
          disabled={busy}
          rows={4}
          className="mt-4 w-full resize-y rounded-[8px] border border-border px-3 py-2.5 text-sm leading-relaxed"
        />
      )}

      <div className="mt-4 flex justify-end">
        <Button
          size="lg"
          icon={<Send size={15} />}
          disabled={busy || !answer}
          onClick={() => answer && onSubmit(answer)}
        >
          {busy ? tr("ask.submitting") : tr("ask.submit")}
        </Button>
      </div>
    </Card>
  );
}
