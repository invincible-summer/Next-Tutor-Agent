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

type ClientEntry = {
  state: IllustrationEnrichmentState;
  illustration: QuestionIllustrationData | null;
  failureCode: string;
  promise?: Promise<IllustrationEnrichmentResponse>;
};

const CLIENT_CACHE_LIMIT = 100;
const clientEntries = new Map<string, ClientEntry>();

function questionKey(questionId: string, revision: number): string {
  return `${questionId}:${revision}`;
}

function remember(key: string, entry: ClientEntry): ClientEntry {
  if (!clientEntries.has(key) && clientEntries.size >= CLIENT_CACHE_LIMIT) {
    const oldest = clientEntries.keys().next().value as string | undefined;
    if (oldest) clientEntries.delete(oldest);
  }
  clientEntries.set(key, entry);
  return entry;
}

function terminalEntry(result: IllustrationEnrichmentResponse): ClientEntry {
  if (result.status === "ready" && result.illustration) {
    return { state: "ready", illustration: result.illustration, failureCode: "" };
  }
  if (result.status === "not_required") {
    return { state: "not_required", illustration: null, failureCode: "" };
  }
  return {
    state: "failed",
    illustration: null,
    failureCode: result.code || "illustration_generation_failed",
  };
}

async function fetchIllustration(
  questionId: string,
  questionRevision: number,
): Promise<IllustrationEnrichmentResponse> {
  const response = await apiFetch(
    `${API_BASE}/assessment/questions/${encodeURIComponent(questionId)}/illustration`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question_revision: questionRevision }),
    },
  );
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const code = payload?.detail?.error?.code || payload?.error?.code || `status_${response.status}`;
    throw new Error(String(code));
  }
  return payload as IllustrationEnrichmentResponse;
}

function startClientFlight(key: string, questionId: string, revision: number): ClientEntry {
  // The shared promise writes the terminal cache entry *before* it resolves or
  // rejects to subscribers. This makes card transitions deterministic: a new
  // mount can never observe a completed request as still "generating".
  const promise = fetchIllustration(questionId, revision).then(
    (result) => {
      remember(key, terminalEntry(result));
      return result;
    },
    (error: unknown) => {
      remember(key, {
        state: "failed",
        illustration: null,
        failureCode: error instanceof Error ? error.message : "illustration_generation_failed",
      });
      throw error;
    },
  );
  return remember(key, {
    state: "generating",
    illustration: null,
    failureCode: "",
    promise,
  });
}

/**
 * CAT is text-first: the frozen question is immediately usable, while this
 * hook independently enriches that exact question identity with a reviewed
 * SVG. Illustration work never participates in answer-button disabled state.
 *
 * QuestionCard and FeedbackCard are separate mounts. A bounded module-local
 * entry keeps one client-side flight/terminal state per authoritative question
 * identity so that crossing that UI boundary neither cancels an in-flight
 * request nor silently retries a completed failure. Only retry() starts a new
 * request after a failed terminal state.
 */
export function useIllustrationEnrichment(question: AssessmentQuestion | null) {
  const questionId = question?.question_id || "";
  const revision = question?.question_revision || 1;
  const key = questionId ? questionKey(questionId, revision) : "";
  const frozenIllustration = question?.illustration ?? null;
  const hasFrozenIllustration = Boolean(frozenIllustration);
  const draft = questionId.startsWith("q_draft_");

  const initial = (() => {
    if (hasFrozenIllustration) {
      return { state: "ready" as const, illustration: frozenIllustration, failureCode: "" };
    }
    if (!questionId || draft) {
      return { state: "idle" as const, illustration: null, failureCode: "" };
    }
    return clientEntries.get(key) ?? {
      state: "generating" as const,
      illustration: null,
      failureCode: "",
    };
  })();
  const [local, setLocal] = useState<ClientEntry>(initial);
  const [retryVersion, setRetryVersion] = useState(0);

  useEffect(() => {
    if (hasFrozenIllustration) {
      if (key) clientEntries.delete(key);
      setLocal({ state: "ready", illustration: frozenIllustration, failureCode: "" });
      return;
    }
    if (!questionId || draft) {
      setLocal({ state: "idle", illustration: null, failureCode: "" });
      return;
    }

    let active = true;
    let entry = clientEntries.get(key);
    if (!entry || retryVersion > 0) {
      entry = startClientFlight(key, questionId, revision);
    }
    setLocal(entry);

    const pending = entry.promise;
    if (pending) {
      const syncSettled = () => {
        if (!active) return;
        const settled = clientEntries.get(key);
        if (settled) setLocal(settled);
      };
      // Two-branch then consumes rejection; unlike finally(), it does not leave
      // a newly-created rejected promise unobserved.
      void pending.then(syncSettled, syncSettled);
    }
    return () => { active = false; };
  }, [questionId, revision, key, draft, hasFrozenIllustration, frozenIllustration, retryVersion]);

  function retry() {
    if (!questionId || draft || hasFrozenIllustration || local.state !== "failed") return;
    clientEntries.delete(key);
    setLocal({ state: "generating", illustration: null, failureCode: "" });
    setRetryVersion((value) => value + 1);
  }

  return {
    illustration: frozenIllustration ?? local.illustration,
    state: hasFrozenIllustration ? "ready" as const : local.state,
    failureCode: local.failureCode,
    retry,
  };
}
