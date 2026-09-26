// 课堂模式 API 客户端（plan.md §14.1）。
// 全部走 apiFetch（带 Authorization）；SSE 用 ReadableStream 手解析——
// 原生 EventSource 不能设置 Authorization 头（§14.4）。
import { apiFetch } from "./api-fetch";
import { API_BASE } from "./api";
import type {
  CancelJobRequest, ClassroomCapabilities, ClassroomTemplates,
  ContinueJobRequest, CreateLessonRequest, CreateLessonResponse,
  CreateRevisionRequest, CreateRevisionResponse, BriefPatchRequest,
  JobPublic, JobSnapshotEvent, LessonDetailPublic, LessonListResponse,
  OutlinePatchRequest, RetryJobRequest,
} from "./types-classroom.generated";

const BASE = API_BASE;

export class ClassroomApiError extends Error {
  code: string;
  status: number;
  constructor(code: string, message: string, status: number) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

/** 统一错误抽取：FastapiHTTPException → ClassroomApiError（§14.3 错误码）。 */
async function readError(res: Response): Promise<ClassroomApiError> {
  let code = "storage_unavailable";
  let message = `HTTP ${res.status}`;
  try {
    const body = await res.json();
    code = body?.detail?.error?.code ?? body?.error?.code ?? code;
    message = body?.detail?.error?.message ?? body?.error?.message ?? message;
  } catch { /* keep defaults */ }
  return new ClassroomApiError(code, message, res.status);
}

async function jsonOf<T>(res: Response): Promise<T> {
  if (!res.ok) throw await readError(res);
  return res.json() as Promise<T>;
}

const W = (workspaceId: string) =>
  `/workspaces/${encodeURIComponent(workspaceId)}/classroom`;

export async function getClassroomCapabilities(): Promise<ClassroomCapabilities> {
  return jsonOf(await apiFetch(`${BASE}/classroom/capabilities`));
}

export async function getClassroomTemplates(lang?: string): Promise<ClassroomTemplates> {
  const q = lang ? `?lang=${encodeURIComponent(lang)}` : "";
  return jsonOf(await apiFetch(`${BASE}/classroom/templates${q}`));
}

export async function listLessons(workspaceId: string, opts?: {
  page?: number; pageSize?: number; status?: string;
}): Promise<LessonListResponse> {
  const p = new URLSearchParams();
  p.set("page", String(opts?.page ?? 1));
  p.set("page_size", String(opts?.pageSize ?? 5));
  if (opts?.status) p.set("status", opts.status);
  return jsonOf(await apiFetch(`${BASE}${W(workspaceId)}/lessons?${p}`));
}

export async function createLesson(
  workspaceId: string, request: CreateLessonRequest, idempotencyKey: string,
): Promise<CreateLessonResponse> {
  const res = await apiFetch(`${BASE}${W(workspaceId)}/lessons`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(request),
  });
  if (res.status === 409) throw await readError(res);
  return jsonOf(res);
}

export async function createRevision(
  workspaceId: string, lessonId: string, request: CreateRevisionRequest,
  idempotencyKey: string,
): Promise<CreateRevisionResponse> {
  const res = await apiFetch(
    `${BASE}${W(workspaceId)}/lessons/${encodeURIComponent(lessonId)}/revisions`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(request),
    });
  if (res.status === 409) throw await readError(res);
  return jsonOf(res);
}

/** GET L：详情/预览数据；未完成时只有 pending（brief/progress），无 slides。 */
export async function getLesson(
  workspaceId: string, lessonId: string, revision?: number,
): Promise<LessonDetailPublic> {
  const q = revision ? `?revision=${revision}` : "";
  return jsonOf(await apiFetch(
    `${BASE}${W(workspaceId)}/lessons/${encodeURIComponent(lessonId)}${q}`));
}

export async function getJob(
  workspaceId: string, lessonId: string, jobId: string,
): Promise<JobPublic> {
  return jsonOf(await apiFetch(
    `${BASE}${W(workspaceId)}/lessons/${encodeURIComponent(lessonId)}` +
    `/jobs/${encodeURIComponent(jobId)}`));
}

export async function cancelJob(
  workspaceId: string, lessonId: string, jobId: string,
  request: CancelJobRequest,
): Promise<JobPublic> {
  return jsonOf(await apiFetch(
    `${BASE}${W(workspaceId)}/lessons/${encodeURIComponent(lessonId)}` +
    `/jobs/${encodeURIComponent(jobId)}/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    }));
}

export async function retryJob(
  workspaceId: string, lessonId: string, jobId: string,
  request: RetryJobRequest,
): Promise<JobPublic> {
  return jsonOf(await apiFetch(
    `${BASE}${W(workspaceId)}/lessons/${encodeURIComponent(lessonId)}` +
    `/jobs/${encodeURIComponent(jobId)}/retry`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    }));
}

export async function continueJob(
  workspaceId: string, lessonId: string, jobId: string,
  request: ContinueJobRequest,
): Promise<JobPublic> {
  return jsonOf(await apiFetch(
    `${BASE}${W(workspaceId)}/lessons/${encodeURIComponent(lessonId)}` +
    `/jobs/${encodeURIComponent(jobId)}/continue`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    }));
}

export async function patchOutline(
  workspaceId: string, lessonId: string, jobId: string,
  request: OutlinePatchRequest,
): Promise<JobPublic> {
  return jsonOf(await apiFetch(
    `${BASE}${W(workspaceId)}/lessons/${encodeURIComponent(lessonId)}` +
    `/jobs/${encodeURIComponent(jobId)}/outline`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    }));
}

export async function patchBrief(
  workspaceId: string, lessonId: string, jobId: string,
  request: BriefPatchRequest,
): Promise<JobPublic> {
  return jsonOf(await apiFetch(
    `${BASE}${W(workspaceId)}/lessons/${encodeURIComponent(lessonId)}` +
    `/jobs/${encodeURIComponent(jobId)}/brief`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    }));
}

/** 受鉴权的课件 HTML 文本（父页面写入 iframe srcdoc）。 */
export async function getRevisionFrame(
  workspaceId: string, lessonId: string, revision: number,
  mode: "presentation" | "reading" = "presentation",
): Promise<string> {
  const res = await apiFetch(
    `${BASE}${W(workspaceId)}/lessons/${encodeURIComponent(lessonId)}` +
    `/revisions/${revision}/frame?mode=${mode}`);
  if (!res.ok) throw await readError(res);
  return res.text();
}

// ---------------------------------------------------------------------------
// SSE（§14.4）：GET J/events 经认证 fetch 读流；每 15s 心跳注释；显式
// AbortSignal（避开 apiFetch 的 GET 30s 超时）；离开页面立即 abort。
// ---------------------------------------------------------------------------

export interface JobEventsHandlers {
  onSnapshot: (snapshot: JobSnapshotEvent) => void;
  onTerminal?: (snapshot: JobSnapshotEvent) => void;
  onError?: (err: unknown) => void;
}

/** 解析 text/event-stream 帧（id/event/data 多行合并；注释行忽略）。 */
export async function* parseSSE(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<{ id?: string; event?: string; data: string }> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // 帧以空行分隔；保留不完整尾部。
      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const raw = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const frame: { id?: string; event?: string; data: string[] } = { data: [] };
        for (const line of raw.split("\n")) {
          if (line.startsWith(":")) continue; // heartbeat comment
          if (line.startsWith("id:")) frame.id = line.slice(3).trim();
          else if (line.startsWith("event:")) frame.event = line.slice(6).trim();
          else if (line.startsWith("data:")) frame.data.push(line.slice(5).trimStart());
        }
        if (frame.data.length > 0) {
          yield { id: frame.id, event: frame.event, data: frame.data.join("\n") };
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

const TERMINAL_STATES = new Set(["succeeded", "failed", "cancelled"]);

/** 订阅 J/events；返回 abort 函数。断线由调用方重连（§14.4 指数退避）。 */
export function subscribeJobEvents(
  workspaceId: string, lessonId: string, jobId: string,
  handlers: JobEventsHandlers, signal: AbortSignal,
): void {
  const url =
    `${BASE}${W(workspaceId)}/lessons/${encodeURIComponent(lessonId)}` +
    `/jobs/${encodeURIComponent(jobId)}/events`;
  void (async () => {
    try {
      const res = await apiFetch(url, { signal, headers: { Accept: "text/event-stream" } });
      if (!res.ok || !res.body) throw await readError(res);
      for await (const frame of parseSSE(res.body)) {
        if (signal.aborted) return;
        if (frame.event !== "snapshot" && frame.event !== "terminal") continue;
        const data = JSON.parse(frame.data) as JobSnapshotEvent;
        if (frame.event === "terminal") handlers.onTerminal?.(data);
        else handlers.onSnapshot(data);
        if (TERMINAL_STATES.has(data.state) || frame.event === "terminal") return;
      }
    } catch (err) {
      if (signal.aborted) return;
      handlers.onError?.(err);
    }
  })();
}
