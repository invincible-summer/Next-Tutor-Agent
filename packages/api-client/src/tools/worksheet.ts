import type {
  WorksheetCreateRequest,
  WorksheetDocument,
  WorksheetExport,
  WorksheetGenerateRequest,
  WorksheetGenerateResponse,
  WorksheetImageAttachRequest,
  WorksheetList,
  WorksheetPatchRequest,
  WorksheetQuestionPatchRequest,
  WorksheetRefineRequest,
} from "@next-tutor/contracts";
import type { AbortSignalLike, Transport } from "../types.ts";

export interface WorksheetClient {
  list(signal?: AbortSignalLike | null): Promise<WorksheetList>;
  create(body: WorksheetCreateRequest, signal?: AbortSignalLike | null): Promise<WorksheetDocument>;
  get(id: string, signal?: AbortSignalLike | null): Promise<WorksheetDocument>;
  patch(id: string, body: WorksheetPatchRequest, signal?: AbortSignalLike | null): Promise<WorksheetDocument>;
  remove(id: string, signal?: AbortSignalLike | null): Promise<{ deleted: boolean }>;
  generate(id: string, body: WorksheetGenerateRequest, signal?: AbortSignalLike | null): Promise<WorksheetGenerateResponse>;
  patchQuestion(id: string, questionId: string, body: WorksheetQuestionPatchRequest, signal?: AbortSignalLike | null): Promise<WorksheetDocument>;
  deleteQuestion(id: string, questionId: string, etag: string, signal?: AbortSignalLike | null): Promise<WorksheetDocument>;
  refine(id: string, questionId: string, body: WorksheetRefineRequest, signal?: AbortSignalLike | null): Promise<WorksheetDocument>;
  attachImage(id: string, questionId: string, body: WorksheetImageAttachRequest, signal?: AbortSignalLike | null): Promise<WorksheetDocument>;
  export(id: string, variant: "student" | "teacher", format: "markdown" | "html", signal?: AbortSignalLike | null): Promise<WorksheetExport>;
}

export function createWorksheetClient(transport: Transport): WorksheetClient {
  const call = async <T>(path: string, init?: Parameters<Transport["request"]>[1]): Promise<T> =>
    transport.request<T>(`/tools/worksheets${path}`, init).then((result) => result.body);
  return {
    list: (signal) => call<WorksheetList>("", { signal }),
    create: (body, signal) => call<WorksheetDocument>("", { method: "POST", json: body, signal }),
    get: (id, signal) => call<WorksheetDocument>(`/${encodeURIComponent(id)}`, { signal }),
    patch: (id, body, signal) => call<WorksheetDocument>(`/${encodeURIComponent(id)}`, { method: "PATCH", json: body, signal }),
    remove: (id, signal) => call<{ deleted: boolean }>(`/${encodeURIComponent(id)}`, { method: "DELETE", signal }),
    generate: (id, body, signal) => call<WorksheetGenerateResponse>(`/${encodeURIComponent(id)}/generate`, { method: "POST", json: body, signal }),
    patchQuestion: (id, questionId, body, signal) => call<WorksheetDocument>(`/${encodeURIComponent(id)}/questions/${encodeURIComponent(questionId)}`, { method: "PATCH", json: body, signal }),
    deleteQuestion: (id, questionId, etag, signal) => call<WorksheetDocument>(`/${encodeURIComponent(id)}/questions/${encodeURIComponent(questionId)}`, { method: "DELETE", query: { etag }, signal }),
    refine: (id, questionId, body, signal) => call<WorksheetDocument>(`/${encodeURIComponent(id)}/questions/${encodeURIComponent(questionId)}/refine`, { method: "POST", json: body, signal }),
    attachImage: (id, questionId, body, signal) => call<WorksheetDocument>(`/${encodeURIComponent(id)}/questions/${encodeURIComponent(questionId)}/attach-image`, { method: "POST", json: body, signal }),
    export: (id, variant, format, signal) => call<WorksheetExport>(`/${encodeURIComponent(id)}/export`, { query: { variant, format }, signal }),
  };
}
