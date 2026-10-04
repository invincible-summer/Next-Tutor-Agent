/**
 * Classroom domain (mirrors `app/api/v1/classroom.py`): lesson authoring
 * (create/revise/jobs with SSE progress), playback runs with lease +
 * progress + per-segment audio, checkpoints, assets, exports and voice
 * previews. DTOs are imported type-only from `@next-tutor/contracts/classroom`
 * (single source of truth).
 *
 * Error model: the backend wraps `ClassroomError` codes in
 * `detail.error.{code,message}` — the transport's envelope parser already
 * surfaces them as `ApiError.code` (`classroom_disabled`,
 * `revision_conflict`, `lease_conflict`, `budget_exceeded`, …), so this
 * module throws the shared error classes instead of a domain wrapper.
 *
 * Idempotency: classroom create-class operations require an
 * `Idempotency-Key` header (16–128 printable chars) — callers mint one per
 * logical action and pass it explicitly; the header alone does not opt the
 * call into blind write retries. Checkpoint submit carries its key in the
 * body instead. Binary bodies (frame HTML aside) resolve to bytes; platform
 * layers turn them into blob URLs / files.
 */
import type {
  AbortSignalLike,
  FormDataLike,
  RequestOptions,
  Transport,
} from "./types.ts";
import { errorFromResponse } from "./errors.ts";
import { readSseFrames } from "./sse/decoder.ts";
import type {
  AssetUploadResponse,
  AudioProfileRequest,
  AudioRequest,
  AudioResponse,
  BriefPatchRequest,
  CancelJobRequest,
  CheckpointPublic,
  CheckpointSubmitRequest,
  ClassroomCapabilities,
  ClassroomSummary,
  ClassroomTemplates,
  ClipStatus,
  ContinueJobRequest,
  CreateLessonRequest,
  CreateLessonResponse,
  CreateRevisionRequest,
  CreateRevisionResponse,
  CreateRunRequest,
  RunCreateResponse,
  ExportCreateRequest,
  ImageSearchRequest,
  ImageSearchResponse,
  JobPreviewResponse,
  JobPublic,
  JobSnapshotEvent,
  LeaseAcquireRequest,
  LeaseRenewRequest,
  LeaseResponse,
  LessonDetailPublic,
  LessonListResponse,
  OutlinePatchRequest,
  ProgressRequest,
  ProgressResponse,
  QaAudioRequest,
  QaAudioResponse,
  RetryJobRequest,
  RunNoteRequest,
  RunNoteResponse,
  SaveNoteRequest,
  SaveNoteResponse,
  VoicePreviewRequest,
  VoicePreviewResponse,
} from "@next-tutor/contracts/classroom";

/** `RunPublic` plus the playback extras the endpoint actually returns. */
export interface RunView {
  run_id: string;
  lesson_id: string;
  lesson_revision: number;
  status: "active" | "paused" | "completed" | "ended";
  state_revision: number;
  cursor: {
    slide_id: string;
    segment_id: string;
    chunk_index: number;
    offset_ms: number;
    last_completed_segment_id: string | null;
  } | null;
  resume_anchor: RunView["cursor"];
  audio_profile: Record<string, unknown>;
  qa_session_id: string | null;
  visited_slide_count: number;
  completed_kind: string;
  created_at: string;
  updated_at?: string;
  lease: {
    held: boolean;
    expired: boolean;
    client_id: string | null;
    lease_epoch: number;
    expires_at: string | null;
  };
  cursor_index: number;
  segment_total: number;
  tts_local_locked: boolean;
  tts_fallback_notified?: boolean;
  [key: string]: unknown;
}

export interface ExportHandle {
  export_id: string;
  format: string;
  revision: number;
  content_url: string;
  expires_at: number;
}

/** Job event stream frames (§14.4): snapshots until a terminal state. */
export type ClassroomJobEvent =
  | { type: "snapshot"; snapshot: JobSnapshotEvent }
  /** Terminal frame; `snapshot` is null when the job already vanished. */
  | { type: "terminal"; snapshot: JobSnapshotEvent | null };

export interface JobEventsOptions {
  /** Resume from a state revision (server replays the latest snapshot). */
  afterRevision?: number;
  signal?: AbortSignalLike | null;
}

export interface CheckpointSubmissionAck {
  attempt_id: string;
  source_id: string;
  job_id: string;
  question_id: string;
  question_revision: number;
  task_result: Record<string, unknown> | null;
  evaluation_status: string;
  evaluation_reason?: string;
  duplicate: boolean;
}

export interface ClassroomCallOptions {
  signal?: AbortSignalLike | null;
}

export interface ClassroomClient {
  // --- capabilities & templates -------------------------------------------
  capabilities(signal?: AbortSignalLike | null): Promise<ClassroomCapabilities>;
  templates(lang?: string, signal?: AbortSignalLike | null): Promise<ClassroomTemplates>;

  // --- lessons ---------------------------------------------------------------
  listLessons(
    workspaceId: string,
    options?: { page?: number; pageSize?: number; status?: string },
    signal?: AbortSignalLike | null,
  ): Promise<LessonListResponse>;
  createLesson(
    workspaceId: string,
    request: CreateLessonRequest,
    idempotencyKey: string,
  ): Promise<CreateLessonResponse>;
  createRevision(
    workspaceId: string,
    lessonId: string,
    request: CreateRevisionRequest,
    idempotencyKey: string,
  ): Promise<CreateRevisionResponse>;
  getLesson(workspaceId: string, lessonId: string, revision?: number, signal?: AbortSignalLike | null): Promise<LessonDetailPublic>;
  archiveLesson(workspaceId: string, lessonId: string): Promise<{ status: string; trash_item_id: string }>;
  listRevisions(
    workspaceId: string,
    lessonId: string,
    options?: { page?: number; pageSize?: number },
  ): Promise<{
    items: { revision: number; created_at: string; source_changed: boolean; available: boolean }[];
    total: number;
    page: number;
    page_size: number;
  }>;
  /** Authenticated frame HTML (iframe srcdoc on Web, WebView on mobile). */
  getRevisionFrame(
    workspaceId: string,
    lessonId: string,
    revision: number,
    mode?: "presentation" | "print",
  ): Promise<string>;

  // --- generation jobs --------------------------------------------------------
  getJob(workspaceId: string, lessonId: string, jobId: string, signal?: AbortSignalLike | null): Promise<JobPublic>;
  getJobPreview(
    workspaceId: string,
    lessonId: string,
    jobId: string,
    slideId?: string,
    signal?: AbortSignalLike | null,
  ): Promise<JobPreviewResponse>;
  cancelJob(workspaceId: string, lessonId: string, jobId: string, request: CancelJobRequest): Promise<JobPublic>;
  retryJob(workspaceId: string, lessonId: string, jobId: string, request: RetryJobRequest, idempotencyKey: string): Promise<JobPublic>;
  continueJob(workspaceId: string, lessonId: string, jobId: string, request: ContinueJobRequest): Promise<JobPublic>;
  patchOutline(workspaceId: string, lessonId: string, jobId: string, request: OutlinePatchRequest): Promise<JobPublic>;
  patchBrief(workspaceId: string, lessonId: string, jobId: string, request: BriefPatchRequest): Promise<JobPublic>;
  /**
   * GET …/jobs/{id}/events — SSE snapshots until the job reaches a terminal
   * state or the stream sends `terminal`. Reconnect/backoff stays with the
   * caller; abort via `options.signal`.
   */
  jobEvents(
    workspaceId: string,
    lessonId: string,
    jobId: string,
    options?: JobEventsOptions,
  ): AsyncGenerator<ClassroomJobEvent>;

  // --- images / assets / exports ---------------------------------------------
  imageSearch(workspaceId: string, request: ImageSearchRequest, idempotencyKey: string): Promise<ImageSearchResponse>;
  /** POST multipart `file`; the caller supplies the platform FormData. */
  uploadAsset(workspaceId: string, lessonId: string, form: FormDataLike): Promise<AssetUploadResponse>;
  assetContent(workspaceId: string, lessonId: string, assetId: string, signal?: AbortSignalLike | null): Promise<ArrayBuffer>;
  createExport(
    workspaceId: string,
    lessonId: string,
    request: ExportCreateRequest,
    idempotencyKey: string,
  ): Promise<ExportHandle>;
  exportContent(workspaceId: string, lessonId: string, exportId: string, signal?: AbortSignalLike | null): Promise<ArrayBuffer>;

  // --- voice preview ------------------------------------------------------------
  createVoicePreview(workspaceId: string, request: VoicePreviewRequest, idempotencyKey: string): Promise<VoicePreviewResponse>;
  voicePreviewContent(workspaceId: string, clipId: string, signal?: AbortSignalLike | null): Promise<ArrayBuffer>;

  // --- playback runs -------------------------------------------------------------
  createRun(workspaceId: string, lessonId: string, request: CreateRunRequest, idempotencyKey: string): Promise<RunCreateResponse>;
  getRun(workspaceId: string, lessonId: string, runId: string, signal?: AbortSignalLike | null): Promise<RunView>;
  acquireLease(workspaceId: string, lessonId: string, runId: string, request: LeaseAcquireRequest): Promise<LeaseResponse>;
  renewLease(workspaceId: string, lessonId: string, runId: string, request: LeaseRenewRequest): Promise<LeaseResponse>;
  /** DELETE with a JSON body (lease release protocol). */
  releaseLease(workspaceId: string, lessonId: string, runId: string, request: LeaseRenewRequest): Promise<void>;
  updateProgress(workspaceId: string, lessonId: string, runId: string, request: ProgressRequest): Promise<ProgressResponse>;
  updateAudioProfile(workspaceId: string, lessonId: string, runId: string, request: AudioProfileRequest): Promise<RunView>;
  addRunNote(workspaceId: string, lessonId: string, runId: string, request: RunNoteRequest): Promise<RunNoteResponse>;
  saveRunNote(workspaceId: string, lessonId: string, runId: string, request: SaveNoteRequest, idempotencyKey: string): Promise<SaveNoteResponse>;
  requestRunAudio(workspaceId: string, lessonId: string, runId: string, request: AudioRequest, idempotencyKey: string): Promise<AudioResponse>;
  requestQaAudio(workspaceId: string, lessonId: string, runId: string, request: QaAudioRequest, idempotencyKey: string): Promise<QaAudioResponse>;
  getClipStatus(workspaceId: string, lessonId: string, runId: string, clipId: string, signal?: AbortSignalLike | null): Promise<ClipStatus>;
  clipContent(workspaceId: string, lessonId: string, runId: string, clipId: string, signal?: AbortSignalLike | null): Promise<ArrayBuffer>;

  // --- checkpoints ------------------------------------------------------------------
  getCheckpoint(workspaceId: string, lessonId: string, runId: string, checkpointId: string, signal?: AbortSignalLike | null): Promise<CheckpointPublic>;
  submitCheckpoint(
    workspaceId: string,
    lessonId: string,
    runId: string,
    checkpointId: string,
    request: CheckpointSubmitRequest,
  ): Promise<CheckpointSubmissionAck>;
  checkpointHint(workspaceId: string, lessonId: string, runId: string, checkpointId: string, idempotencyKey: string): Promise<{ hint: string }>;
  checkpointReveal(
    workspaceId: string,
    lessonId: string,
    runId: string,
    checkpointId: string,
    idempotencyKey: string,
  ): Promise<{ answer: string; explanation: string }>;
  skipCheckpoint(
    workspaceId: string,
    lessonId: string,
    runId: string,
    checkpointId: string,
    expectedStateRevision?: number | null,
  ): Promise<{ status: string }>;
  getCheckpointSubmission(
    workspaceId: string,
    lessonId: string,
    runId: string,
    checkpointId: string,
    signal?: AbortSignalLike | null,
  ): Promise<{ submission: Record<string, unknown> | null }>;
  runSummary(workspaceId: string, lessonId: string, runId: string, signal?: AbortSignalLike | null): Promise<ClassroomSummary>;
}

const TERMINAL_JOB_STATES = new Set(["succeeded", "failed", "cancelled"]);

export function createClassroomClient(transport: Transport): ClassroomClient {
  const lessonBase = (workspaceId: string, lessonId: string) =>
    `/workspaces/${encodeURIComponent(workspaceId)}/classroom/lessons/${encodeURIComponent(lessonId)}`;
  const jobBase = (workspaceId: string, lessonId: string, jobId: string) =>
    `${lessonBase(workspaceId, lessonId)}/jobs/${encodeURIComponent(jobId)}`;
  const runBase = (workspaceId: string, lessonId: string, runId: string) =>
    `${lessonBase(workspaceId, lessonId)}/runs/${encodeURIComponent(runId)}`;
  const checkpointBase = (workspaceId: string, lessonId: string, runId: string, checkpointId: string) =>
    `${runBase(workspaceId, lessonId, runId)}/checkpoints/${encodeURIComponent(checkpointId)}`;
  const request = <T>(path: string, init: RequestOptions) =>
    transport.request<T>(path, init).then((result) => result.body);
  return {
    capabilities: (signal) => request<ClassroomCapabilities>("/classroom/capabilities", { signal }),
    templates: (lang, signal) =>
      request<ClassroomTemplates>("/classroom/templates", {
        query: lang ? { lang } : undefined,
        signal,
      }),

    listLessons: (workspaceId, options = {}, signal) => {
      const { page = 1, pageSize = 5, status } = options;
      return request<LessonListResponse>(
        `/workspaces/${encodeURIComponent(workspaceId)}/classroom/lessons`,
        { query: { page, page_size: pageSize, status }, signal },
      );
    },
    createLesson: (workspaceId, body, idempotencyKey) =>
      request<CreateLessonResponse>(`/workspaces/${encodeURIComponent(workspaceId)}/classroom/lessons`, {
        method: "POST",
        json: body,
        idempotencyKey,
      }),
    createRevision: (workspaceId, lessonId, body, idempotencyKey) =>
      request<CreateRevisionResponse>(`${lessonBase(workspaceId, lessonId)}/revisions`, {
        method: "POST",
        json: body,
        idempotencyKey,
      }),
    getLesson: (workspaceId, lessonId, revision, signal) =>
      request<LessonDetailPublic>(lessonBase(workspaceId, lessonId), {
        query: revision ? { revision } : undefined,
        signal,
      }),
    archiveLesson: (workspaceId, lessonId) =>
      request<{ status: string; trash_item_id: string }>(lessonBase(workspaceId, lessonId), {
        method: "DELETE",
      }),
    listRevisions: (workspaceId, lessonId, options = {}) => {
      const { page = 1, pageSize = 20 } = options;
      return request<{
        items: { revision: number; created_at: string; source_changed: boolean; available: boolean }[];
        total: number;
        page: number;
        page_size: number;
      }>(`${lessonBase(workspaceId, lessonId)}/revisions`, {
        query: { page, page_size: pageSize },
      });
    },
    getRevisionFrame: (workspaceId, lessonId, revision, mode = "presentation") =>
      transport
        .request<string>(`${lessonBase(workspaceId, lessonId)}/revisions/${encodeURIComponent(String(revision))}/frame`, {
          responseType: "text",
          query: { mode },
        })
        .then((result) => result.body),

    getJob: (workspaceId, lessonId, jobId, signal) =>
      request<JobPublic>(jobBase(workspaceId, lessonId, jobId), { signal }),
    getJobPreview: (workspaceId, lessonId, jobId, slideId, signal) =>
      request<JobPreviewResponse>(`${jobBase(workspaceId, lessonId, jobId)}/preview`, {
        query: slideId ? { slide_id: slideId } : undefined,
        signal,
      }),
    cancelJob: (workspaceId, lessonId, jobId, body) =>
      request<JobPublic>(`${jobBase(workspaceId, lessonId, jobId)}/cancel`, {
        method: "POST",
        json: body,
      }),
    retryJob: (workspaceId, lessonId, jobId, body, idempotencyKey) =>
      request<JobPublic>(`${jobBase(workspaceId, lessonId, jobId)}/retry`, {
        method: "POST",
        json: body,
        idempotencyKey,
      }),
    continueJob: (workspaceId, lessonId, jobId, body) =>
      request<JobPublic>(`${jobBase(workspaceId, lessonId, jobId)}/continue`, {
        method: "POST",
        json: body,
      }),
    patchOutline: (workspaceId, lessonId, jobId, body) =>
      request<JobPublic>(`${jobBase(workspaceId, lessonId, jobId)}/outline`, {
        method: "PATCH",
        json: body,
      }),
    patchBrief: (workspaceId, lessonId, jobId, body) =>
      request<JobPublic>(`${jobBase(workspaceId, lessonId, jobId)}/brief`, {
        method: "PATCH",
        json: body,
      }),
    async *jobEvents(workspaceId, lessonId, jobId, options = {}) {
      const response = await transport.raw(`${jobBase(workspaceId, lessonId, jobId)}/events`, {
        headers: { Accept: "text/event-stream" },
        query: options.afterRevision ? { after_revision: options.afterRevision } : undefined,
        // An explicit signal keeps the transport's default read timeout away
        // from this long-lived GET; a fresh controller never aborts.
        signal: options.signal ?? new AbortController().signal,
      });
      if (!response.ok || !response.body) throw await errorFromResponse(response);
      for await (const frame of readSseFrames(response.body, { signal: options.signal })) {
        if (frame.event !== "snapshot" && frame.event !== "terminal") continue;
        let parsed: unknown = null;
        try {
          parsed = JSON.parse(frame.data);
        } catch {
          parsed = null;
        }
        const snapshot =
          parsed && typeof parsed === "object" && Object.keys(parsed as object).length > 0
            ? (parsed as JobSnapshotEvent)
            : null;
        if (frame.event === "terminal") {
          yield { type: "terminal", snapshot };
          return;
        }
        if (snapshot) {
          yield { type: "snapshot", snapshot };
          if (TERMINAL_JOB_STATES.has(snapshot.state)) return;
        }
      }
    },

    imageSearch: (workspaceId, body, idempotencyKey) =>
      request<ImageSearchResponse>(`/workspaces/${encodeURIComponent(workspaceId)}/classroom/image-search`, {
        method: "POST",
        json: body,
        idempotencyKey,
      }),
    uploadAsset: (workspaceId, lessonId, form) =>
      request<AssetUploadResponse>(`${lessonBase(workspaceId, lessonId)}/assets`, {
        method: "POST",
        body: form,
      }),
    assetContent: (workspaceId, lessonId, assetId, signal) =>
      transport
        .request<ArrayBuffer>(`${lessonBase(workspaceId, lessonId)}/assets/${encodeURIComponent(assetId)}/content`, {
          responseType: "bytes",
          signal,
        })
        .then((result) => result.body),
    createExport: (workspaceId, lessonId, body, idempotencyKey) =>
      request<ExportHandle>(`${lessonBase(workspaceId, lessonId)}/exports`, {
        method: "POST",
        json: body,
        idempotencyKey,
      }),
    exportContent: (workspaceId, lessonId, exportId, signal) =>
      transport
        .request<ArrayBuffer>(`${lessonBase(workspaceId, lessonId)}/exports/${encodeURIComponent(exportId)}/content`, {
          responseType: "bytes",
          signal,
        })
        .then((result) => result.body),

    createVoicePreview: (workspaceId, body, idempotencyKey) =>
      request<VoicePreviewResponse>(`/workspaces/${encodeURIComponent(workspaceId)}/classroom/voice-preview`, {
        method: "POST",
        json: body,
        idempotencyKey,
      }),
    voicePreviewContent: (workspaceId, clipId, signal) =>
      transport
        .request<ArrayBuffer>(
          `/workspaces/${encodeURIComponent(workspaceId)}/classroom/voice-previews/${encodeURIComponent(clipId)}/content`,
          { responseType: "bytes", signal },
        )
        .then((result) => result.body),

    createRun: (workspaceId, lessonId, body, idempotencyKey) =>
      request<RunCreateResponse>(`${lessonBase(workspaceId, lessonId)}/runs`, {
        method: "POST",
        json: body,
        idempotencyKey,
      }),
    getRun: (workspaceId, lessonId, runId, signal) =>
      request<RunView>(runBase(workspaceId, lessonId, runId), { signal }),
    acquireLease: (workspaceId, lessonId, runId, body) =>
      request<LeaseResponse>(`${runBase(workspaceId, lessonId, runId)}/lease`, {
        method: "POST",
        json: body,
      }),
    renewLease: (workspaceId, lessonId, runId, body) =>
      request<LeaseResponse>(`${runBase(workspaceId, lessonId, runId)}/lease`, {
        method: "PUT",
        json: body,
      }),
    releaseLease: (workspaceId, lessonId, runId, body) =>
      transport
        .request(`${runBase(workspaceId, lessonId, runId)}/lease`, {
          method: "DELETE",
          json: body,
          responseType: "none",
        })
        .then(() => undefined),
    updateProgress: (workspaceId, lessonId, runId, body) =>
      request<ProgressResponse>(`${runBase(workspaceId, lessonId, runId)}/progress`, {
        method: "PUT",
        json: body,
      }),
    updateAudioProfile: (workspaceId, lessonId, runId, body) =>
      request<RunView>(`${runBase(workspaceId, lessonId, runId)}/audio-profile`, {
        method: "PUT",
        json: body,
      }),
    addRunNote: (workspaceId, lessonId, runId, body) =>
      request<RunNoteResponse>(`${runBase(workspaceId, lessonId, runId)}/notes`, {
        method: "POST",
        json: body,
      }),
    saveRunNote: (workspaceId, lessonId, runId, body, idempotencyKey) =>
      request<SaveNoteResponse>(`${runBase(workspaceId, lessonId, runId)}/save-note`, {
        method: "POST",
        json: body,
        idempotencyKey,
      }),
    requestRunAudio: (workspaceId, lessonId, runId, body, idempotencyKey) =>
      request<AudioResponse>(`${runBase(workspaceId, lessonId, runId)}/audio`, {
        method: "POST",
        json: body,
        idempotencyKey,
      }),
    requestQaAudio: (workspaceId, lessonId, runId, body, idempotencyKey) =>
      request<QaAudioResponse>(`${runBase(workspaceId, lessonId, runId)}/qa-audio`, {
        method: "POST",
        json: body,
        idempotencyKey,
      }),
    getClipStatus: (workspaceId, lessonId, runId, clipId, signal) =>
      request<ClipStatus>(
        `${runBase(workspaceId, lessonId, runId)}/audio/${encodeURIComponent(clipId)}`,
        { signal },
      ),
    clipContent: (workspaceId, lessonId, runId, clipId, signal) =>
      transport
        .request<ArrayBuffer>(
          `${runBase(workspaceId, lessonId, runId)}/audio/${encodeURIComponent(clipId)}/content`,
          { responseType: "bytes", signal },
        )
        .then((result) => result.body),

    getCheckpoint: (workspaceId, lessonId, runId, checkpointId, signal) =>
      request<CheckpointPublic>(checkpointBase(workspaceId, lessonId, runId, checkpointId), { signal }),
    submitCheckpoint: (workspaceId, lessonId, runId, checkpointId, body) =>
      request<CheckpointSubmissionAck>(`${checkpointBase(workspaceId, lessonId, runId, checkpointId)}/submit`, {
        method: "POST",
        json: body,
      }),
    checkpointHint: (workspaceId, lessonId, runId, checkpointId, idempotencyKey) =>
      request<{ hint: string }>(`${checkpointBase(workspaceId, lessonId, runId, checkpointId)}/hint`, {
        method: "POST",
        idempotencyKey,
      }),
    checkpointReveal: (workspaceId, lessonId, runId, checkpointId, idempotencyKey) =>
      request<{ answer: string; explanation: string }>(
        `${checkpointBase(workspaceId, lessonId, runId, checkpointId)}/reveal`,
        { method: "POST", idempotencyKey },
      ),
    skipCheckpoint: (workspaceId, lessonId, runId, checkpointId, expectedStateRevision = null) =>
      request<{ status: string }>(`${checkpointBase(workspaceId, lessonId, runId, checkpointId)}/skip`, {
        method: "POST",
        json: { expected_state_revision: expectedStateRevision },
      }),
    getCheckpointSubmission: (workspaceId, lessonId, runId, checkpointId, signal) =>
      request<{ submission: Record<string, unknown> | null }>(
        `${checkpointBase(workspaceId, lessonId, runId, checkpointId)}/submission`,
        { signal },
      ),
    runSummary: (workspaceId, lessonId, runId, signal) =>
      request<ClassroomSummary>(`${runBase(workspaceId, lessonId, runId)}/summary`, { signal }),
  };
}
