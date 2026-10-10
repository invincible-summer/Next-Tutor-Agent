/// <reference lib="webworker" />
/**
 * Pure-domain task runner (plan I1). Runs only @next-tutor/domain functions
 * on JSON task descriptions; large meshes return as transferable ArrayBuffers.
 * Long jobs yield to the worker event loop between grid slabs so cancel
 * messages actually arrive mid-computation (setTimeout(0) is a real macrotask
 * boundary — await Promise.resolve() is not).
 */

import {
  buildEvaluationContext, compileExpression, generateExplicitSurface, generateImplicitSurface,
  generateParametricCurve, generateParametricSurface, type PlotDefinition3D,
} from "@next-tutor/domain";
import type { TaskRequest, TaskResponse } from "./protocol.ts";

type Yieldable = { yieldIfBusy(): Promise<void> };

/** Cooperative yielding between bounded work slabs (plan I1 correction). */
function makeYieldable(): Yieldable {
  let sinceLastYield = 0;
  return {
    async yieldIfBusy(): Promise<void> {
      sinceLastYield += 1;
      if (sinceLastYield >= 8) {
        sinceLastYield = 0;
        await new Promise<void>((resolve) => setTimeout(resolve, 0));
      }
    },
  };
}

async function runTask(request: TaskRequest): Promise<TaskResponse> {
  const payload = request.payload as { plot?: PlotDefinition3D };
  const plot = payload?.plot;
  if (!plot) {
    return { id: request.id, documentId: request.documentId, revision: request.revision, ok: false, diagnostics: [{ code: "invalid_task_payload" }] };
  }
  const doc = {
    functions: (payload as { functions?: Array<{ name: string; params: string[]; expression: string }> }).functions?.map((f, i) => ({ id: `wfn${i}`, name: f.name, params: f.params, expression: f.expression })) ?? [],
    parameters: Object.entries((payload as { parameters?: Record<string, number> }).parameters ?? {}).map(([name, value], i) => ({ id: `wpar${i}`, name, value, min: value - 5, max: value + 5, step: 0.1 })),
  };
  const ctx = buildEvaluationContext({ ...emptyDocument(), ...doc });
  if (!ctx.ok) {
    return { id: request.id, documentId: request.documentId, revision: request.revision, ok: false, diagnostics: ctx.diagnostics.map((d) => ({ code: d.code })) };
  }
  const variables = [
    ...(plot.kind === "parametricSurface" ? ["u", "v"] : plot.kind === "parametricCurve" ? ["t"] : ["x", "y", "z"]),
    ...Object.keys(ctx.value.parameters),
  ];
  const symbols = { variables, functions: ctx.value.functions };
  const compile = (source: string) => compileExpression(source, symbols);
  await Promise.resolve();
  const yieldable = makeYieldable();
  await yieldable.yieldIfBusy();
  if (plot.kind === "explicitSurface") {
    const compiled = compile(plot.expression);
    if (!compiled.ok) return fail(request, compiled.diagnostics[0]?.code ?? "compile_failed");
    const result = generateExplicitSurface(compiled.value, symbols, plot.xDomain, plot.yDomain, plot.quality);
    return result.ok ? meshResponse(request, result.value) : fail(request, result.diagnostics[0]?.code ?? "mesh_failed");
  }
  if (plot.kind === "parametricSurface") {
    const x = compile(plot.xExpression); const y = compile(plot.yExpression); const z = compile(plot.zExpression);
    if (!(x.ok && y.ok && z.ok)) return fail(request, "compile_failed");
    const result = generateParametricSurface(x.value, y.value, z.value, symbols, plot.uDomain, plot.vDomain, plot.quality, plot.wrapU, plot.wrapV);
    return result.ok ? meshResponse(request, result.value) : fail(request, result.diagnostics[0]?.code ?? "mesh_failed");
  }
  if (plot.kind === "implicitSurface") {
    const compiled = compile(plot.expression);
    if (!compiled.ok) return fail(request, "compile_failed");
    const result = generateImplicitSurface(compiled.value, symbols, { resolution: plot.resolution, iso: plot.iso, box: plot.box });
    return result.ok ? meshResponse(request, result.value) : fail(request, result.diagnostics[0]?.code ?? "mesh_failed");
  }
  if (plot.kind === "parametricCurve") {
    const x = compile(plot.xExpression); const y = compile(plot.yExpression); const z = compile(plot.zExpression);
    if (!(x.ok && y.ok && z.ok)) return fail(request, "compile_failed");
    const result = generateParametricCurve(x.value, y.value, z.value, symbols, plot.tDomain, plot.samples);
    if (result.ok) {
      const positions = new Float32Array(result.value.positions);
      const tangents = new Float32Array(result.value.tangents);
      return {
        id: request.id, documentId: request.documentId, revision: request.revision, ok: true,
        result: {
          positions: positions.buffer as ArrayBuffer, normals: tangents.buffer as ArrayBuffer, indices: new Uint32Array(0).buffer as ArrayBuffer,
          bounds: result.value.bounds, parameterLineCount: 0, diagnostics: [],
        },
        diagnostics: [],
      };
    }
    return fail(request, "curve_failed");
  }
  return fail(request, "unknown_task");
}

function emptyDocument() {
  return {
    schemaVersion: 1 as const, id: "worker", name: "worker", revision: 0, activeMode: "functions3d" as const,
    parameters: [], functions: [], objects2d: [], plots2d: [], objects3d: [], plots3d: [], annotations: [],
    view2d: { centerX: 0, centerY: 0, scale: 0.05, showGrid: true, snapToGrid: false, snapSize: 0.5 },
    camera3d: { kind: "perspective" as const, azimuth: 0, elevation: 0, distance: 10, target: { x: 0, y: 0, z: 0 } },
    render3d: { showGridXY: true, showGridXZ: false, showGridYZ: false, showAxes: true, edges: "visible" as const },
  };
}

function fail(request: TaskRequest, code: string): TaskResponse {
  return { id: request.id, documentId: request.documentId, revision: request.revision, ok: false, diagnostics: [{ code }] };
}

function meshResponse(request: TaskRequest, mesh: {
  positions: Float32Array; normals: Float32Array; indices: Uint32Array;
  bounds: { minX: number; maxX: number; minY: number; maxY: number; minZ: number; maxZ: number };
  parameterLines: Float32Array[];
}): TaskResponse {
  const positions = new Float32Array(mesh.positions);
  const normals = new Float32Array(mesh.normals);
  const indices = new Uint32Array(mesh.indices);
  return {
    id: request.id, documentId: request.documentId, revision: request.revision, ok: true,
    result: {
      // Transferable copies: the worker keeps no reference after postMessage.
      positions: positions.buffer as ArrayBuffer,
      normals: normals.buffer as ArrayBuffer,
      indices: indices.buffer as ArrayBuffer,
      bounds: mesh.bounds,
      parameterLineCount: mesh.parameterLines.length,
      diagnostics: [],
    },
    diagnostics: [],
  };
}

self.addEventListener("message", (event: MessageEvent<TaskRequest>) => {
  const request = event.data;
  if (!request || typeof request.id !== "string") return;
  void runTask(request).then((response) => {
    (self as unknown as Worker).postMessage(response);
  });
});

export {};
