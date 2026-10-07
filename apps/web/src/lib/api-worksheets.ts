import type {
  WorksheetCreateRequest, WorksheetDocument, WorksheetExport, WorksheetGenerateRequest,
  WorksheetGenerateResponse, WorksheetImageAttachRequest, WorksheetList,
  WorksheetPatchRequest, WorksheetQuestionPatchRequest, WorksheetRefineRequest, WorksheetQuestion,
} from "@next-tutor/contracts";
import { apiClient } from "@/platform/api-client";

function client() { return apiClient().tools.worksheet; }
export const listWorksheets = (signal?: AbortSignal) => client().list(signal);
export const createWorksheet = (body: WorksheetCreateRequest, signal?: AbortSignal) => client().create(body, signal);
export const getWorksheet = (id: string, signal?: AbortSignal) => client().get(id, signal);
export const patchWorksheet = (id: string, body: WorksheetPatchRequest, signal?: AbortSignal) => client().patch(id, body, signal);
export const deleteWorksheet = (id: string, signal?: AbortSignal) => client().remove(id, signal);
export const generateWorksheet = (id: string, body: WorksheetGenerateRequest, signal?: AbortSignal) => client().generate(id, body, signal);
export const patchWorksheetQuestion = (id: string, questionId: string, body: WorksheetQuestionPatchRequest, signal?: AbortSignal) => client().patchQuestion(id, questionId, body, signal);
export const deleteWorksheetQuestion = (id: string, questionId: string, etag: string, signal?: AbortSignal) => client().deleteQuestion(id, questionId, etag, signal);
export const refineWorksheetQuestion = (id: string, questionId: string, body: WorksheetRefineRequest, signal?: AbortSignal) => client().refine(id, questionId, body, signal);
export const attachWorksheetImage = (id: string, questionId: string, body: WorksheetImageAttachRequest, signal?: AbortSignal) => client().attachImage(id, questionId, body, signal);
export const exportWorksheet = (id: string, variant: "student" | "teacher", format: "markdown" | "html", signal?: AbortSignal) => client().export(id, variant, format, signal);
export type { WorksheetCreateRequest, WorksheetDocument, WorksheetExport, WorksheetGenerateRequest, WorksheetGenerateResponse, WorksheetImageAttachRequest, WorksheetList, WorksheetPatchRequest, WorksheetQuestionPatchRequest, WorksheetRefineRequest, WorksheetQuestion };
