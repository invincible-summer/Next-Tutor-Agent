// 情景配图（工具助手）客户端：会话/轮次/任务方法与丢包恢复语义
// （request_id 恢复、服务端终态为事实）在共享包 @next-tutor/api-client；
// 本文件保留 Web 类型别名并把 typed 错误适配为 Web 既有错误类型。
import type {
  DeletedSessionAck,
  IllustrationSessionList,
  ScenarioTurn,
  SelectedMaterialRef,
  ToolIllustrationJob,
} from "@next-tutor/contracts";
import { IllustrationApiError } from "@next-tutor/api-client";
import { apiClient } from "@/platform/api-client";

export type {
  DeletedSessionAck,
  IllustrationSession,
  IllustrationSessionList,
  IllustrationSessionSummary,
  ScenarioRevision as IllustrationRevision,
  ScenarioTurn,
  SelectedMaterialRef,
  ToolIllustrationJob,
} from "@next-tutor/contracts";

export type IllustrationMode = ToolIllustrationJob["mode"];
export type ToolIllustrationStatus = ToolIllustrationJob["status"];
export type IllustrationTurn = ScenarioTurn;

export class IllustrationToolError extends Error {
  constructor(readonly code: string, readonly status = 0) { super(code); this.name = "IllustrationToolError"; }
}

function adapt(error: unknown): never {
  if (error instanceof IllustrationApiError) {
    throw new IllustrationToolError(error.code, error.status);
  }
  throw error;
}

function tools() {
  return apiClient().tools.illustration;
}

export const listIllustrationSessions = (signal?: AbortSignal) =>
  tools().listSessions(signal).catch(adapt) as Promise<IllustrationSessionList>;
export const createIllustrationSession = (title = "", signal?: AbortSignal) =>
  tools().createSession(title, signal).catch(adapt);
export const getIllustrationSession = (id: string, signal?: AbortSignal) =>
  tools().getSession(id, signal).catch(adapt);
export const deleteIllustrationSession = (id: string, signal?: AbortSignal) =>
  tools().deleteSession(id, signal).catch(adapt) as Promise<DeletedSessionAck>;
export const startIllustrationTurn = (id: string, body: {
  message: string; mode: IllustrationMode; selected_materials: SelectedMaterialRef[]; base_revision: number; source_revision?: number; request_id: string;
}, signal?: AbortSignal) =>
  tools().submitTurn(id, body, signal).catch(adapt);
export const getToolIllustrationJob = (id: string, signal?: AbortSignal) =>
  tools().getJob(id, signal).catch(adapt);
export const retryToolIllustrationJob = (id: string, signal?: AbortSignal) =>
  tools().retryJob(id, signal).catch(adapt);
