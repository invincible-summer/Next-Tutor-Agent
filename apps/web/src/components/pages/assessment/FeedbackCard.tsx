"use client";
// feedback 阶段：两层反馈卡（§14.5）——本题结果 + 学习反馈走共用
// SubmissionOutcome；提交身份由服务端记录（attempt_id），pending 可离开。
import { ArrowRight, Flag, ListChecks, LoaderCircle } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { SubmissionOutcome } from "@/components/learning-evaluation/SubmissionOutcome";
import type { Lang } from "@/lib/i18n";
import type { AssessmentQuestion } from "@/lib/types-modules";
import type { PageTr } from "./common";
import { QuestionIllustration } from "@/components/quiz/QuestionIllustration";
import { MiniMarkdown } from "@/components/chat/markdown";
import { useIllustrationEnrichment } from "./useIllustrationEnrichment";

export interface AnswerResult {
  taskResult: {
    verdict?: string | null;
    grading_status?: string;
    criterion_results?: Array<{ criterion_id: string; result: string; comment?: string }>;
    first_error?: { description?: string } | null;
    hypotheses?: Array<{ statement?: string }>;
    feedback?: { strengths?: string[]; improvement?: string; next_step?: string } | null;
  } | null;
  evaluationStatus: string;
  learnerFeedback: string;
}

export function FeedbackCard({
  tr,
  lang,
  question,
  result,
  stop,
  busy,
  onNext,
  onAbandon,
}: {
  tr: PageTr;
  lang: Lang;
  question: AssessmentQuestion | null;
  result: AnswerResult;
  stop: boolean;
  busy: boolean;
  onNext: () => void;
  onAbandon: () => void;
}) {
  const illustration = useIllustrationEnrichment(question);
  return (
    <Card>
      <CardHeader
        icon={<ListChecks size={16} />}
        title={tr("fb.title")}
        right={
          <Button variant="ghost" size="sm" icon={<Flag size={13} />} disabled={busy} onClick={onAbandon}>
            {tr("abandon")}
          </Button>
        }
      />
      {question && (
        <div className="mb-4 border-b border-border-light pb-4" data-testid="assessment-feedback-question">
          <div className="chat-prose">
            <MiniMarkdown>{question.stem}</MiniMarkdown>
          </div>
          {illustration.state === "generating" && <p role="status"
            className="mt-2 flex items-center gap-2 text-xs leading-5 text-muted">
            <LoaderCircle size={13} aria-hidden="true" className="animate-spin" />
            {lang === "en" ? "Finishing the diagram…" : "正在完成配图…"}
          </p>}
          <QuestionIllustration illustration={illustration.illustration} />
        </div>
      )}
      <SubmissionOutcome
        lang={lang}
        data={{
          taskResult: result.taskResult,
          evaluationStatus: result.evaluationStatus,
          learnerFeedback: result.learnerFeedback,
        }}
      />
      <div className="mt-4 flex justify-end">
        <Button size="lg" icon={<ArrowRight size={15} />} disabled={busy} onClick={onNext}>
          {busy ? tr("fb.loading") : stop ? tr("fb.finish") : tr("fb.next")}
        </Button>
      </div>
    </Card>
  );
}
