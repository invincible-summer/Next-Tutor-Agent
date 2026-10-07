"use client";

import Image from "next/image";
import { Markdown } from "@/components/chat/markdown";
import { apiAssetUrl } from "@/lib/api";
import type { WorksheetDocument, WorksheetQuestion } from "@/lib/api-worksheets";

export type PreviewCopy = {
  answerField: string; explanation: string; image: string; select: string;
  paperDuration: string; paperTotal: string; studentName: string; studentClass: string;
  defaultInstructions: string; pointsUnit: string;
};

export function QuestionPreview({ question, showAnswers, tr }: { question: WorksheetQuestion; showAnswers: boolean; tr: PreviewCopy }) {
  return (
    <section className="worksheet-question min-w-0">
      <h3 className="flex items-baseline gap-1.5 text-sm font-semibold">
        <span>{question.number}.</span><span className="text-xs font-normal text-muted">({question.score} {tr.pointsUnit})</span>
      </h3>
      <Markdown className="chat-prose worksheet-prose mt-3">{question.stem}</Markdown>
      {Object.entries(question.options ?? {}).length > 0 && (
        <div className="mt-3 space-y-2">
          {Object.entries(question.options ?? {}).map(([key, value]) => (
            <div key={key} className="flex min-w-0 items-baseline gap-2.5 text-sm">
              <span className="shrink-0 font-medium">{key}.</span>
              <Markdown className="chat-prose worksheet-prose min-w-0 flex-1">{value}</Markdown>
            </div>
          ))}
        </div>
      )}
      {question.image && (
        <Image unoptimized src={question.image.data_url || apiAssetUrl(question.image.url)} alt={question.image.alt || tr.image}
          width={640} height={400} className="mx-auto my-5 max-h-72 max-w-full object-contain" />
      )}
      {showAnswers ? (
        <div className="worksheet-answer mt-5 space-y-2 rounded-[10px] border border-border bg-surface-sunken px-4 py-3 text-sm">
          <div><h4 className="mb-1 text-xs font-semibold">{tr.answerField}</h4><Markdown className="chat-prose worksheet-prose">{question.answer || "—"}</Markdown></div>
          <div><h4 className="mb-1 text-xs font-semibold">{tr.explanation}</h4><Markdown className="chat-prose worksheet-prose">{question.explanation || "—"}</Markdown></div>
        </div>
      ) : question.type !== "multiple_choice" ? <div className="min-h-20" /> : null}
    </section>
  );
}

export function SingleQuestionPreview({ question, showAnswers, tr }: { question: WorksheetQuestion | null; showAnswers: boolean; tr: PreviewCopy }) {
  if (!question) return <div className="flex min-h-64 items-center justify-center px-5 text-center text-sm text-muted">{tr.select}</div>;
  return <div className="worksheet-paper m-4 rounded-[10px] border border-border p-5 sm:p-6"><QuestionPreview question={question} showAnswers={showAnswers} tr={tr} /></div>;
}

export function PaperPreview({ document, showAnswers, tr }: { document: WorksheetDocument; showAnswers: boolean; tr: PreviewCopy }) {
  return (
    <article className="worksheet-paper paper-preview mx-auto max-w-[800px] px-5 py-8 sm:px-10 sm:py-10">
      <header className="border-b-2 border-fg pb-5 text-center">
        <h2 className="font-serif text-2xl font-semibold leading-normal">{document.title}</h2>
        <p className="mt-2 text-sm text-muted">{[document.subject, document.grade, document.unit].filter(Boolean).join(" · ")}</p>
      </header>
      <div className="flex flex-wrap justify-between gap-x-5 gap-y-2 border-b border-border py-4 text-xs text-fg-secondary">
        <span>{tr.paperDuration} {document.duration_minutes}　{tr.paperTotal} {document.total_score} {tr.pointsUnit}</span>
        <span>{tr.studentName} __________　{tr.studentClass} __________</span>
      </div>
      <Markdown className="chat-prose worksheet-prose mt-5">{document.instructions || tr.defaultInstructions}</Markdown>
      <div className="mt-8 space-y-7">
        {(document.questions ?? []).map((question) => <QuestionPreview key={question.id} question={question} showAnswers={showAnswers} tr={tr} />)}
      </div>
    </article>
  );
}
