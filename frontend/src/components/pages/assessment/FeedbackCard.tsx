"use client";

// feedback 阶段：即时判分反馈卡。
import { ArrowRight, Flag, ListChecks } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { dt, verdictTone } from "@/lib/labels";
import type { Lang } from "@/lib/i18n";
import type { PageTr } from "./common";

export interface AnswerResult {
  verdict?: string;
  score?: number;
  feedback?: string;
  [key: string]: unknown;
}

export function FeedbackCard({
  tr,
  lang,
  result,
  stop,
  busy,
  onNext,
  onAbandon,
}: {
  tr: PageTr;
  lang: Lang;
  result: AnswerResult;
  stop: boolean;
  busy: boolean;
  onNext: () => void;
  onAbandon: () => void;
}) {
  const verdict = result.verdict || "unknown";
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
      <div className="flex items-center gap-3">
        <Badge tone={verdictTone(verdict)}>
          {dt(lang, `verdict.${verdict}`, tr("verdict.unknown"))}
        </Badge>
        {typeof result.score === "number" && (
          <span className="text-sm text-fg-secondary">
            {tr("fb.score")}
            <span className="tnum ml-1.5 font-semibold text-fg">{result.score}</span>
            <span className="text-xs text-muted"> / 1</span>
          </span>
        )}
      </div>
      {result.feedback && (
        <div className="chat-prose mt-3 whitespace-pre-wrap rounded-[8px] bg-surface-sunken px-3.5 py-3">
          {result.feedback}
        </div>
      )}
      <div className="mt-4 flex justify-end">
        <Button size="lg" icon={<ArrowRight size={15} />} disabled={busy} onClick={onNext}>
          {busy ? tr("fb.loading") : stop ? tr("fb.finish") : tr("fb.next")}
        </Button>
      </div>
    </Card>
  );
}
