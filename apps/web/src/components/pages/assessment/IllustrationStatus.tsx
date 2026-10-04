"use client";

import { ImageIcon, LoaderCircle, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { illustrationFailure, illustrationStage } from "@/lib/api-illustrations";
import type { useIllustrationEnrichment } from "./useIllustrationEnrichment";

export function IllustrationStatus({ illustration, english, essentialPending = false, answered = false, allowRetry = true }: {
  illustration: ReturnType<typeof useIllustrationEnrichment>;
  english: boolean;
  essentialPending?: boolean;
  answered?: boolean;
  allowRetry?: boolean;
}) {
  if (illustration.state === "generating") return <div data-testid="assessment-illustration-generating" role="status"
    className="mt-3 flex items-center gap-2 rounded-lg border border-border-light bg-surface-sunken px-3 py-2 text-xs leading-5 text-muted">
    <LoaderCircle size={14} aria-hidden="true" className="shrink-0 animate-spin" />
    <span>{illustrationStage(illustration.stage, english)} · {answered
      ? (english ? "Your answer has been recorded." : "作答已记录。")
      : essentialPending ? (english ? "The diagram must be ready before answering." : "必要题图完成前不能作答。")
      : (english ? "The text question is ready to answer." : "文字题已可作答。")}</span>
  </div>;

  if (illustration.state !== "failed") return null;
  return <div data-testid="assessment-illustration-failed" role="status"
    className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-warning/30 bg-warning/5 px-3 py-2 text-xs leading-5 text-muted">
    <span className="flex min-w-0 items-center gap-2">
      <ImageIcon size={14} aria-hidden="true" className="shrink-0" />
      <span>{illustrationFailure(illustration.failureCode, english)} · {answered
        ? (english ? "Your answer feedback is preserved." : "作答反馈已保留。")
        : essentialPending ? (english ? "Question material is incomplete." : "题目材料尚未完整，暂不能作答。")
        : (english ? "The text question remains usable." : "补充图不是作答依据，文字题仍可正常作答。")}</span>
    </span>
    {allowRetry && <Button type="button" size="sm" variant="outline" icon={<RefreshCw size={13} aria-hidden="true" />}
      disabled={!illustration.retryable} onClick={illustration.retry}>{english ? "Retry diagram" : "重试配图"}</Button>}
  </div>;
}
