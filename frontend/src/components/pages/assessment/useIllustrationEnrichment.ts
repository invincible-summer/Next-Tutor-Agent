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
 * SVG. Illustration work never participates in answer-button disabled state.
 */
export function useIllustrationEnrichment(question: AssessmentQuestion | null) {
  const questionId = question?.question_id || "";
  const revision = question?.question_revision || 1;
  const frozenIllustration = question?.illustration ?? null;
  const hasFrozenIllustration = Boolean(frozenIllustration);
  const draft = questionId.startsWith("q_draft_");
  const [generatedIllustration, setGeneratedIllustration] = useState<QuestionIllustrationData | null>(null);
  const [state, setState] = useState<IllustrationEnrichmentState>(
    hasFrozenIllustration ? "ready" : draft || !questionId ? "idle" : "generating",
  );
  const [failureCode, setFailureCode] = useState("");
  const [retryVersion, setRetryVersion] = useState(0);

  useEffect(() => {
    if (!questionId || draft || hasFrozenIllustration) return;

    let active = true;
    const controller = new AbortController();
    void fetchIllustration(questionId, revision, controller.signal)
      .then((result) => {
        if (!active) return;
        if (result.status === "ready" && result.illustration) {
          setGeneratedIllustration(result.illustration);
          setState("ready");
          return;
        }
        if (result.status === "not_required") {
          setGeneratedIllustration(null);
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
  }, [questionId, revision, draft, hasFrozenIllustration, retryVersion]);

  function retry() {
    if (!questionId || draft || hasFrozenIllustration) return;
    setGeneratedIllustration(null);
    setFailureCode("");
    setState("generating");
    setRetryVersion((value) => value + 1);
  }

  return {
    illustration: frozenIllustration ?? generatedIllustration,
    state: hasFrozenIllustration ? "ready" as const : state,
    failureCode,
    retry,
  };
}
