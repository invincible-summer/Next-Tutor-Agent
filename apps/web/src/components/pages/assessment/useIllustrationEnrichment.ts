"use client";

import { useEffect, useSyncExternalStore } from "react";
import { DEMO_MODE } from "@/lib/demo";
import { useAuthStore } from "@/lib/auth-store";
import { getFrozenIllustration, getIllustrationJob, IllustrationRequestError, retryIllustrationJob, startIllustration, type IllustrationJob } from "@/lib/api-illustrations";
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
  flight?: symbol;
  promise?: Promise<IllustrationEnrichmentResponse>;
};

const CLIENT_CACHE_LIMIT = 100;
const CLIENT_FLIGHT_TIMEOUT_MS = 150_000;
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
  clientEntries.set(key, entry);
  // A pending entry owns the shared flight. Evict only settled entries so a
  // second mount cannot start another request while the first is still active.
  while (clientEntries.size > CLIENT_CACHE_LIMIT) {
    const oldest = Array.from(clientEntries).find(([candidate, value]) => candidate !== key && value.state !== "generating")?.[0];
    if (!oldest) break;
    clientEntries.delete(oldest);
  }
  emitChange();
  return entry;
}

function forget(key: string): void {
  if (clientEntries.delete(key)) emitChange();
}

function terminalEntry(result: IllustrationEnrichmentResponse): ClientEntry {
  if (result.status === "ready" && result.illustration) {
    return { state: "ready", illustration: result.illustration, failureCode: "", jobId: result.job_id, retryable: false };
  }
  if (result.status === "not_required") {
    return { state: "not_required", illustration: null, failureCode: "", retryable: false };
  }
  return {
    state: "failed",
    illustration: null,
    failureCode: result.code || "illustration_generation_failed",
    jobId: result.job_id,
    retryable: result.failure?.retryable ?? result.retryable ?? true,
  };
}

function checkFlight(key: string, owner: string, flight: symbol): void {
  if ((useAuthStore.getState().user?.id ?? "local") !== owner) {
    if (clientEntries.get(key)?.flight === flight) forget(key);
    throw new IllustrationRequestError("identity_changed", false);
  }
  if (clientEntries.get(key)?.flight !== flight) {
    throw new IllustrationRequestError("illustration_request_superseded", false);
  }
}

function requestSignal(deadline: number, timeout: number): AbortSignal {
  const remaining = deadline - Date.now();
  if (remaining <= 0) throw new IllustrationRequestError("run_interrupted", true);
  return AbortSignal.timeout(Math.min(timeout, remaining));
}

async function fetchIllustration(
  questionId: string,
  questionRevision: number,
  key: string,
  owner: string,
  flight: symbol,
  retryJobId?: string,
): Promise<IllustrationEnrichmentResponse> {
  const deadline = Date.now() + CLIENT_FLIGHT_TIMEOUT_MS;
  let payload: IllustrationEnrichmentResponse;
  if (retryJobId) {
    // A transport failure does not stop the server job. Read it first so an
    // already-finished artifact is recovered without attempting to replace it.
    payload = await getIllustrationJob(retryJobId, requestSignal(deadline, 30_000));
    checkFlight(key, owner, flight);
    if (payload.status === "failed" && (payload.failure?.retryable ?? payload.retryable) !== false) {
      try {
        payload = await retryIllustrationJob(retryJobId, requestSignal(deadline, 120_000));
      } catch (error) {
        if (!(error instanceof IllustrationRequestError) || error.message !== "illustration_frozen") throw error;
        checkFlight(key, owner, flight);
        payload = await getFrozenIllustration(questionId, questionRevision, requestSignal(deadline, 30_000));
      }
    }
  } else {
    payload = await startIllustration(questionId, questionRevision, requestSignal(deadline, 120_000));
  }
  checkFlight(key, owner, flight);
  while (payload.status === "queued" || payload.status === "running") {
    if (!payload.job_id) throw new IllustrationRequestError("illustration_job_missing", false);
    remember(key, { ...clientEntries.get(key), state: "generating", illustration: null, failureCode: "", jobId: payload.job_id,
      stage: payload.progress?.stage });
    const wait = Math.min(1000, deadline - Date.now());
    if (wait <= 0) return { ...payload, status: "failed", code: "run_interrupted", retryable: true };
    await new Promise(resolve => setTimeout(resolve, wait));
    checkFlight(key, owner, flight);
    const remaining = deadline - Date.now();
    if (remaining <= 0) return { ...payload, status: "failed", code: "run_interrupted", retryable: true };
    payload = await getIllustrationJob(payload.job_id, AbortSignal.timeout(Math.min(30_000, remaining)));
    checkFlight(key, owner, flight);
  }
  return payload;
}

function startClientFlight(key: string, questionId: string, revision: number, owner: string, retryJobId?: string): ClientEntry {
  if ((useAuthStore.getState().user?.id ?? "local") !== owner) return IDLE_ENTRY;
  const existing = clientEntries.get(key);
  if (existing) return existing;

  const flight = Symbol(key);
  remember(key, { state: "generating", illustration: null, failureCode: "", flight, jobId: retryJobId });
  // Terminal state is committed to the external store before the shared
  // promise resolves/rejects. Card transitions therefore observe a completed
  // request as terminal, never as stale "generating" state.
  const promise = fetchIllustration(questionId, revision, key, owner, flight, retryJobId).then(
    (result) => {
      if (clientEntries.get(key)?.flight === flight) remember(key, terminalEntry(result));
      return result;
    },
    (error: unknown) => {
      if ((useAuthStore.getState().user?.id ?? "local") !== owner) {
        if (clientEntries.get(key)?.flight === flight) forget(key);
      } else if (clientEntries.get(key)?.flight === flight) {
        remember(key, {
          state: "failed",
          illustration: null,
          failureCode: error instanceof IllustrationRequestError ? error.message : "provider_unavailable",
          jobId: clientEntries.get(key)?.jobId,
          retryable: error instanceof IllustrationRequestError ? error.retryable : true,
        });
      }
      throw error;
    },
  );
  // Consume rejection here as well as exposing the promise for diagnostics;
  // the terminal failure has already been recorded above.
  void promise.catch(() => undefined);
  const pending = clientEntries.get(key);
  return pending?.flight === flight ? remember(key, { ...pending, promise }) : pending ?? GENERATING_ENTRY;
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
    if (!enabled || clientEntries.get(key) !== clientEntry || clientEntry.state !== "failed" || !clientEntry.retryable) return;
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
