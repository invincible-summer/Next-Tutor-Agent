"use client";

import { useEffect, useSyncExternalStore } from "react";
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
const listeners = new Set<() => void>();
const IDLE_ENTRY: ClientEntry = { state: "idle", illustration: null, failureCode: "" };
const GENERATING_ENTRY: ClientEntry = { state: "generating", illustration: null, failureCode: "" };

function questionKey(questionId: string, revision: number): string {
  return `${questionId}:${revision}`;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function emitChange(): void {
  for (const listener of listeners) listener();
}

function remember(key: string, entry: ClientEntry): ClientEntry {
  if (!clientEntries.has(key) && clientEntries.size >= CLIENT_CACHE_LIMIT) {
    const oldest = clientEntries.keys().next().value as string | undefined;
    if (oldest) clientEntries.delete(oldest);
  }
  clientEntries.set(key, entry);
  emitChange();
  return entry;
}

function forget(key: string): void {
  if (clientEntries.delete(key)) emitChange();
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
  const existing = clientEntries.get(key);
  if (existing) return existing;

  // Terminal state is committed to the external store before the shared
  // promise resolves/rejects. Card transitions therefore observe a completed
  // request as terminal, never as stale "generating" state.
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
  // Consume rejection here as well as exposing the promise for diagnostics;
  // the terminal failure has already been recorded above.
  void promise.catch(() => undefined);
  return remember(key, {
    state: "generating",
    illustration: null,
    failureCode: "",
    promise,
  });
}

function snapshotFor(key: string, enabled: boolean): ClientEntry {
  if (!enabled || !key) return IDLE_ENTRY;
  return clientEntries.get(key) ?? GENERATING_ENTRY;
}

/**
 * CAT is text-first: the frozen question is immediately usable, while this
 * hook independently enriches that exact question identity with a reviewed
 * SVG. Illustration work never participates in answer-button disabled state.
 *
 * QuestionCard and FeedbackCard are separate mounts. A bounded module-level
 * external store keeps one client flight/terminal state per authoritative
 * question identity. Crossing the UI boundary neither cancels an in-flight
 * request nor silently retries a completed failure; only retry() starts a new
 * request after a failed terminal state.
 */
export function useIllustrationEnrichment(question: AssessmentQuestion | null) {
  const questionId = question?.question_id || "";
  const revision = question?.question_revision || 1;
  const key = questionId ? questionKey(questionId, revision) : "";
  const frozenIllustration = question?.illustration ?? null;
  const hasFrozenIllustration = Boolean(frozenIllustration);
  const draft = questionId.startsWith("q_draft_");
  const enabled = Boolean(questionId && !draft && !hasFrozenIllustration);

  const clientEntry = useSyncExternalStore(
    subscribe,
    () => snapshotFor(key, enabled),
    () => IDLE_ENTRY,
  );

  useEffect(() => {
    if (hasFrozenIllustration) {
      if (key) forget(key);
      return;
    }
    if (!enabled) return;
    startClientFlight(key, questionId, revision);
  }, [questionId, revision, key, enabled, hasFrozenIllustration]);

  function retry() {
    if (!enabled || clientEntry.state !== "failed") return;
    forget(key);
    startClientFlight(key, questionId, revision);
  }

  if (hasFrozenIllustration) {
    return {
      illustration: frozenIllustration,
      state: "ready" as const,
      failureCode: "",
      retry,
    };
  }

  return {
    illustration: clientEntry.illustration,
    state: clientEntry.state,
    failureCode: clientEntry.failureCode,
    retry,
  };
}
