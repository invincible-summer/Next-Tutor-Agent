"use client";

/**
 * Worker message protocol (plan I1): discriminated tasks with id + documentId
 * + revision so stale results can never overwrite newer edits. Payloads are
 * plain JSON describing WHAT to compute — never code or user scripts.
 */

export type MathTaskKind =
  | "plot2d"
  | "surfaceExplicit"
  | "surfaceParametric"
  | "surfaceImplicit"
  | "curve3d";

export interface SurfaceRequestBase {
  parameters: Record<string, number>;
  functions: Array<{ name: string; params: string[]; expression: string }>;
}

export interface TaskRequest {
  id: string;
  documentId: string;
  revision: number;
  task: MathTaskKind;
  payload: unknown;
}

export interface MeshPayload {
  positions: ArrayBuffer;
  normals: ArrayBuffer;
  indices: ArrayBuffer;
  bounds: { minX: number; maxX: number; minY: number; maxY: number; minZ: number; maxZ: number };
  parameterLineCount: number;
  diagnostics: string[];
}

export type TaskResponse =
  | { id: string; documentId: string; revision: number; ok: true; result: MeshPayload; diagnostics: unknown[] }
  | { id: string; documentId: string; revision: number; ok: false; diagnostics: Array<{ code: string }> };

export function isTaskResponse(value: unknown): value is TaskResponse {
  if (typeof value !== "object" || value === null) return false;
  const row = value as Partial<TaskResponse>;
  return typeof row.id === "string" && typeof row.documentId === "string" && typeof row.revision === "number" && typeof row.ok === "boolean";
}
