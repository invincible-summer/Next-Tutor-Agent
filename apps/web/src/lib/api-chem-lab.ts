// 化学实验台客户端：命令幂等/冲突重同步语义在共享包 @next-tutor/api-client；
// 本文件保留 Web 类型别名并把 typed 错误适配为 Web 既有错误类型。
import type {
  ChemLabCommandRequest,
  ChemLabCreateSession,
  ChemLabForkRequest,
  ChemLabResetRequest,
} from "@next-tutor/contracts";
import { ChemLabApiError } from "@next-tutor/api-client";
import { apiClient } from "@/platform/api-client";

export type {
  ChemLabCatalog,
  ChemLabCommandAck,
  ChemLabCommandRequest,
  ChemLabCreateSession,
  ChemLabDeletedAck,
  ChemLabEnginePack,
  ChemLabEvent,
  ChemLabEventPage,
  ChemLabExperimentDetail,
  ChemLabExperimentSummary,
  ChemLabForkRequest,
  ChemLabForkResult,
  ChemLabGoalStatus,
  ChemLabGuidance,
  ChemLabObservation,
  ChemLabRenderFrame,
  ChemLabResetRequest,
  ChemLabResultCard,
  ChemLabRevisionView,
  ChemLabCheckpoint,
  ChemLabSessionList,
  ChemLabSessionSnapshot,
  ChemLabSessionSummary,
  ChemLabVesselFrame,
  LabCommand,
} from "@next-tutor/contracts";

export class ChemLabToolError extends Error {
  constructor(readonly code: string, readonly status = 0) {
    super(code);
    this.name = "ChemLabToolError";
  }
}

function adapt(error: unknown): never {
  if (error instanceof ChemLabApiError) {
    throw new ChemLabToolError(error.code, error.status);
  }
  throw error;
}

function lab() {
  return apiClient().tools.chemLab;
}

export const getChemLabCatalog = (signal?: AbortSignal) =>
  lab().listCatalog(signal).catch(adapt);
export const getChemLabExperiment = (experimentId: string, version?: string | null, signal?: AbortSignal) =>
  lab().getExperiment(experimentId, version, signal).catch(adapt);
export const getChemLabEnginePack = (experimentId: string, version?: string | null, signal?: AbortSignal) =>
  lab().getEnginePack(experimentId, version, signal).catch(adapt);
export const listChemLabSessions = (cursor?: string | null, signal?: AbortSignal) =>
  lab().listSessions(cursor, signal).catch(adapt);
export const createChemLabSession = (body: ChemLabCreateSession, signal?: AbortSignal) =>
  lab().createSession(body, signal).catch(adapt);
export const getChemLabSession = (id: string, signal?: AbortSignal) =>
  lab().getSession(id, signal).catch(adapt);
export const getChemLabRevision = (id: string, revision: number, signal?: AbortSignal) =>
  lab().getRevision(id, revision, signal).catch(adapt);
export const deleteChemLabSession = (id: string, signal?: AbortSignal) =>
  lab().deleteSession(id, signal).catch(adapt);
export const postChemLabCommand = (id: string, body: ChemLabCommandRequest, signal?: AbortSignal) =>
  lab().postCommand(id, body, signal).catch(adapt);
export const getChemLabEvents = (id: string, afterSeq: number, limit?: number, signal?: AbortSignal) =>
  lab().getEvents(id, afterSeq, limit, signal).catch(adapt);
export const createChemLabCheckpoint = (id: string, label: string, signal?: AbortSignal) =>
  lab().createCheckpoint(id, label, signal).catch(adapt);
export const forkChemLabSession = (id: string, body: ChemLabForkRequest, signal?: AbortSignal) =>
  lab().fork(id, body, signal).catch(adapt);
export const resetChemLabSession = (id: string, body: ChemLabResetRequest, signal?: AbortSignal) =>
  lab().reset(id, body, signal).catch(adapt);
export const finishChemLabSession = (id: string, signal?: AbortSignal) =>
  lab().finish(id, signal).catch(adapt);
