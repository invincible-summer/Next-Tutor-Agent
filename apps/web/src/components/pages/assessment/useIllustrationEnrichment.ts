"use client";

import { useEffect, useSyncExternalStore } from "react";
import { DEMO_MODE } from "@/lib/demo";
import { useAuthStore } from "@/lib/auth-store";
import { getIllustrationJob, retryIllustrationJob, startIllustration, type IllustrationJob } from "@/lib/api-illustrations";
import type { QuestionIllustrationData } from "@/lib/types";
import type { AssessmentQuestion } from "@/lib/types-modules";

export type IllustrationEnrichmentState =
  | "idle"
  | "generating"
  | "ready"
  | "not_required"
  | "failed";

type IllustrationEnrichmentResponse = IllustrationJob;

type ClientEntry = {
  state: IllustrationEnrichmentState;
  illustration: QuestionIllustrationData | null;
  failureCode: string;
  jobId?: string;
  stage?: string;
  retryable?: boolean;
  promise?: Promise<IllustrationEnrichmentResponse>;
};

const CLIENT_CACHE_LIMIT = 100;
const clientEntries = new Map<string, ClientEntry>();
const listeners = new Set<() => void>();
const IDLE_ENTRY: ClientEntry = { state: "idle", illustration: null, failureCode: "" };
const GENERATING_ENTRY: ClientEntry = { state: "generating", illustration: null, failureCode: "" };

function questionKey(owner: string, questionId: string, revision: number): string {
  return `${owner}:${questionId}:${revision}`;
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
    return { state: "ready", illustration: result.illustration, failureCode: "", jobId: result.job_id };
  }
  if (result.status === "not_required") {
    return { state: "not_required", illustration: null, failureCode: "" };
  }
  return {
    state: "failed",
    illustration: null,
    failureCode: result.code || "illustration_generation_failed",
    jobId: result.job_id,
    retryable: result.failure?.retryable ?? result.retryable ?? true,
  };
}

async function fetchIllustration(
  questionId: string,
  questionRevision: number,
  key: string,
  owner: string,
  retryJobId?: string,
): Promise<IllustrationEnrichmentResponse> {
  let payload = retryJobId ? await retryIllustrationJob(retryJobId) : await startIllustration(questionId, questionRevision);
  const deadline = Date.now() + 90_000;
  while (payload.status === "queued" || payload.status === "running") {
    if (!payload.job_id) throw new Error("illustration_job_missing");
    remember(key, { state: "generating", illustration: null, failureCode: "", jobId: payload.job_id,
      stage: payload.progress?.stage });
    await new Promise(resolve => setTimeout(resolve, 1000));
    if ((useAuthStore.getState().user?.id ?? "local") !== owner) throw new Error("identity_changed");
    if (Date.now() > deadline) return { ...payload, status: "failed", code: "run_interrupted", retryable: true };
    payload = await getIllustrationJob(payload.job_id);
  }
  return payload;
}

function startClientFlight(key: string, questionId: string, revision: number, owner: string, retryJobId?: string): ClientEntry {
  const existing = clientEntries.get(key);
  if (existing) return existing;

  // Terminal state is committed to the external store before the shared
  // promise resolves/rejects. Card transitions therefore observe a completed
  // request as terminal, never as stale "generating" state.
  const promise = fetchIllustration(questionId, revision, key, owner, retryJobId).then(
    (result) => {
      remember(key, terminalEntry(result));
      return result;
    },
    (error: unknown) => {
      remember(key, {
        state: "failed",
        illustration: null,
        failureCode: error instanceof Error ? error.message : "illustration_generation_failed",
        jobId: clientEntries.get(key)?.jobId,
        retryable: true,
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
  const owner = useAuthStore(store => store.user?.id ?? "local");
  const questionId = question?.question_id || "";
  const revision = question?.question_revision || 1;
  const key = questionId ? questionKey(owner, questionId, revision) : "";
  const frozenIllustration = question?.illustration ?? null;
  const hasFrozenIllustration = Boolean(frozenIllustration);
  const draft = questionId.startsWith("q_draft_");
  const enabled = !DEMO_MODE && Boolean(questionId && !draft && !hasFrozenIllustration);

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
    startClientFlight(key, questionId, revision, owner);
  }, [questionId, revision, key, enabled, hasFrozenIllustration, owner]);

  function retry() {
    if (!enabled || clientEntry.state !== "failed") return;
    const jobId = clientEntry.jobId;
    forget(key);
    startClientFlight(key, questionId, revision, owner, jobId);
  }

  if (hasFrozenIllustration) {
    return {
      illustration: frozenIllustration,
      state: "ready" as const,
      failureCode: "",
      stage: "complete",
      retryable: false,
      retry,
    };
  }

  return {
    illustration: clientEntry.illustration,
    state: clientEntry.state,
    failureCode: clientEntry.failureCode,
    stage: clientEntry.stage ?? "preparation",
    retryable: clientEntry.retryable ?? true,
    retry,
  };
}
