/**
 * Quiz illustration domain (V1/V2/V3 read models). Mirrors the Web client's
 * semantics: POSTs observe the server's 120-second budget; aborted or timed
 * out observation maps to `run_interrupted` (retryable — the job keeps
 * running server-side); server failure codes surface as
 * `IllustrationApiError` with the envelope's retryable hint.
 *
 * The server answers `QuizIllustrationJob | QuizIllustrationStatus`
 * depending on whether a job exists (union response_model — see
 * app/schemas/illustration.py).
 */
import type { QuizIllustrationJob, QuizIllustrationStatus } from "@next-tutor/contracts";
import type { AbortSignalLike, RequestOptions, Transport } from "./types.ts";
import { ApiError, IllustrationApiError, NetworkError, illustrationErrorFrom } from "./errors.ts";

/** Jobs are queued promptly; polling observes the server's 120s budget. */
export const ILLUSTRATION_POST_TIMEOUT_MS = 120_000;

export type QuizIllustrationView = QuizIllustrationJob | QuizIllustrationStatus;

export interface IllustrationCallOptions {
  signal?: AbortSignalLike | null;
  timeoutMs?: number;
}

export interface IllustrationClient {
  /** POST /assessment/questions/{id}/illustration (body `{question_revision}`). */
  start(
    questionId: string,
    questionRevision: number,
    options?: IllustrationCallOptions,
  ): Promise<QuizIllustrationView>;
  /** GET /illustration-jobs/{id}. */
  getJob(jobId: string, signal?: AbortSignalLike | null): Promise<QuizIllustrationView>;
  /** POST /illustration-jobs/{id}/retry. */
  retry(jobId: string, options?: IllustrationCallOptions): Promise<QuizIllustrationView>;
  /** GET /questions/{id}/illustration?question_revision=N (frozen artifact). */
  frozen(
    questionId: string,
    questionRevision: number,
    signal?: AbortSignalLike | null,
  ): Promise<QuizIllustrationView>;
}

export function createIllustrationClient(transport: Transport): IllustrationClient {
  const call = async <T>(path: string, options?: RequestOptions): Promise<T> => {
    try {
      const result = await transport.request<T>(path, options);
      return result.body as T;
    } catch (error) {
      throw toIllustrationError(error);
    }
  };
  const postTimeout = (options?: IllustrationCallOptions): RequestOptions => ({
    timeoutMs: options?.timeoutMs ?? ILLUSTRATION_POST_TIMEOUT_MS,
    signal: options?.signal ?? null,
  });
  return {
    start: (questionId, questionRevision, options) =>
      call<QuizIllustrationView>(
        `/assessment/questions/${encodeURIComponent(questionId)}/illustration`,
        {
          method: "POST",
          json: { question_revision: questionRevision },
          ...postTimeout(options),
        },
      ),
    getJob: (jobId, signal) =>
      call<QuizIllustrationView>(`/illustration-jobs/${encodeURIComponent(jobId)}`, { signal }),
    retry: (jobId, options) =>
      call<QuizIllustrationView>(`/illustration-jobs/${encodeURIComponent(jobId)}/retry`, {
        method: "POST",
        ...postTimeout(options),
      }),
    frozen: (questionId, questionRevision, signal) =>
      call<QuizIllustrationView>(
        `/questions/${encodeURIComponent(questionId)}/illustration`,
        { query: { question_revision: questionRevision }, signal },
      ),
  };
}

/**
 * Shared error mapping for illustration calls: interrupted observation is
 * retryable `run_interrupted`; connectivity loss propagates; every HTTP
 * failure becomes a typed `IllustrationApiError` preserving the server code.
 */
export function toIllustrationError(error: unknown): Error {
  if (error instanceof IllustrationApiError) return error;
  if (error instanceof NetworkError) {
    if (!error.retryable) {
      // Aborted/timed out while observing — the server-side job is unaffected.
      return new IllustrationApiError("run_interrupted", error.message, 0, true);
    }
    return error;
  }
  if (error instanceof ApiError) return illustrationErrorFrom(error);
  return error instanceof Error ? error : new Error(String(error));
}
