"use client";

import { useEffect, useState } from "react";
import { API_BASE } from "@/lib/api";
import { apiFetch } from "@/lib/api-fetch";
import type { QuestionIllustrationData } from "@/lib/types";
import type { AssessmentQuestion } from "@/lib/types-modules";

export type IllustrationEnrichmentState =
  | "idle"
  | "generating"
  | "ready"
  | "not_required"
  | "failed";

type IllustrationEnrichmentResponse = {
  status: "ready" | "not_required" | "failed" | string;
  illustration?: QuestionIllustrationData | null;
  code?: string;
  retryable?: boolean;
  metrics?: Record<string, number>;
};

async function fetchIllustration(
  questionId: string,
  questionRevision: number,
  signal: AbortSignal,
): Promise<IllustrationEnrichmentResponse> {
  const response = await apiFetch(
    `${API_BASE}/assessment/questions/${encodeURIComponent(questionId)}/illustration`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question_revision: questionRevision }),
      signal,
    },
  );
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const code = payload?.detail?.error?.code || payload?.error?.code || `status_${response.status}`;
    throw new Error(String(code));
  }
  return payload as IllustrationEnrichmentResponse;
}

/**
 * CAT is text-first: the frozen question is immediately usable, while this
 * hook independently enriches that exact question identity with a reviewed
 * SVG.  Illustration work never participates in answer-button disabled state.
 */
export function useIllustrationEnrichment(question: AssessmentQuestion | null) {
  const revision = question?.question_revision || 1;
  const frozenIllustration = question?.illustration ?? null;
  const frozenHash = frozenIllustration?.content_hash || "";
  const draft = Boolean(question?.question_id?.startsWith("q_draft_"));
  const [illustration, setIllustration] = useState<QuestionIllustrationData | null>(frozenIllustration);
  const [state, setState] = useState<IllustrationEnrichmentState>(
    frozenIllustration ? "ready" : draft || !question ? "idle" : "generating",
  );
  const [failureCode, setFailureCode] = useState("");
  const [retryVersion, setRetryVersion] = useState(0);

  useEffect(() => {
    setIllustration(frozenIllustration);
    setFailureCode("");
    if (!question || draft) {
      setState("idle");
      return;
    }
    if (frozenIllustration) {
      setState("ready");
      return;
    }

    let active = true;
    const controller = new AbortController();
    setState("generating");
    void fetchIllustration(question.question_id, revision, controller.signal)
      .then((result) => {
        if (!active) return;
        if (result.status === "ready" && result.illustration) {
          setIllustration(result.illustration);
          setState("ready");
          return;
        }
        if (result.status === "not_required") {
          setIllustration(null);
          setState("not_required");
          return;
        }
        setFailureCode(result.code || "illustration_generation_failed");
        setState("failed");
      })
      .catch((error: unknown) => {
        if (!active || controller.signal.aborted) return;
        setFailureCode(error instanceof Error ? error.message : "illustration_generation_failed");
        setState("failed");
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [question?.question_id, revision, frozenHash, retryVersion]);

  return {
    illustration,
    state,
    failureCode,
    retry: () => setRetryVersion((value) => value + 1),
  };
}
