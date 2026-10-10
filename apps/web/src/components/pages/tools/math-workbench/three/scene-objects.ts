"use client";

/**
 * Document → Three object builders. Surfaces/curves come from the domain
 * samplers as math-space typed arrays and are converted once through the
 * z-up adapter. Materials follow the H1/H2 policy: opaque writes depth,
 * transparent does not; intersecting transparent surfaces stay approximate.
 * Solid-geometry construction objects were removed with the geometry3d mode
 * (ADR-0022); this builder is function plotting only.
 */

import * as THREE from "three";
import { LineSegments2 } from "three/examples/jsm/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/examples/jsm/lines/LineSegmentsGeometry.js";
import { LineMaterial } from "three/examples/jsm/lines/LineMaterial.js";
import {
  buildEvaluationContext, compileExpression,
  generateExplicitSurface, generateParametricSurface, generateImplicitSurface, generateParametricCurve,
  type MathWorkbenchDocument,
} from "../workbench-types.ts";
import { mathToThree, type Pt3 } from "./stage-adapter.ts";

export interface BuiltScene {
  group: THREE.Group;
  diagnostics: string[];
}

function makeLineMaterial(color: number, width: number, dashed: boolean, resolution: { width: number; height: number }): LineMaterial {
  const material = new LineMaterial({ color, linewidth: width, dashed });
  material.resolution.set(resolution.width, resolution.height);
  material.transparent = true;
  material.depthTest = true;
  material.depthWrite = false;
  return material;
}

function toThreePositions(math: ArrayLike<number>): Float32Array {
  const out = new Float32Array(math.length);
  for (let i = 0; i + 2 < math.length; i += 3) {
    const t = mathToThree({ x: math[i] as number, y: math[i + 1] as number, z: math[i + 2] as number });
    out[i] = t.x; out[i + 1] = t.y; out[i + 2] = t.z;
  }
  return out;
}

function colorOf(hex: string): number {
  return Number.parseInt(hex.replace("#", ""), 16);
}

export function buildSceneObjects(document: MathWorkbenchDocument, resolution: { width: number; height: number }, options?: { deferImplicit?: boolean }): BuiltScene {
  const group = new THREE.Group();
  const diagnostics: string[] = [];
  const ctx = buildEvaluationContext(document);
  if (!ctx.ok) {
    for (const d of ctx.diagnostics) diagnostics.push(d.code);
    return { group, diagnostics };
  }
  // Implicit isosurfaces are the heavy job: the stage defers them to the
  // worker when it is healthy and computes them inline as the fallback.
  const deferredImplicit = new Set<string>();
  if (options?.deferImplicit) {
    for (const plot of document.plots3d) {
      if (plot.kind === "implicitSurface" && plot.visible) deferredImplicit.add(plot.id);
    }
  }

  for (const plot of document.plots3d) {
    if (!plot.visible) continue;
    const axesOf = plot.kind === "parametricSurface" ? ["u", "v"] : plot.kind === "parametricCurve" ? ["t"] : plot.kind === "implicitSurface" ? ["x", "y", "z"] : ["x", "y"];
    const variables = [...axesOf, ...Object.keys(ctx.value.parameters)];
    const symbols = { variables, functions: ctx.value.functions };
    let mesh: THREE.Object3D | null = null;
    if (plot.kind === "explicitSurface") {
      const compiled = compileExpression(plot.expression, symbols);
      if (compiled.ok) {
        const result = generateExplicitSurface(compiled.value, symbols, plot.xDomain, plot.yDomain, plot.quality);
        if (result.ok) mesh = surfaceMesh(result.value.positions, result.value.normals, result.value.indices, plot.style.color, plot.opacity, plot.showGrid, resolution, result.value.parameterLines);
        else diagnostics.push(...result.diagnostics.map((d) => d.code));
      } else diagnostics.push(...compiled.diagnostics.map((d) => d.code));
    } else if (plot.kind === "parametricSurface") {
      const x = compileExpression(plot.xExpression, symbols);
      const y = compileExpression(plot.yExpression, symbols);
      const z = compileExpression(plot.zExpression, symbols);
      if (x.ok && y.ok && z.ok) {
        const result = generateParametricSurface(x.value, y.value, z.value, symbols, plot.uDomain, plot.vDomain, plot.quality, plot.wrapU, plot.wrapV);
        if (result.ok) mesh = surfaceMesh(result.value.positions, result.value.normals, result.value.indices, plot.style.color, plot.opacity, plot.showGrid, resolution, result.value.parameterLines);
        else diagnostics.push(...result.diagnostics.map((d) => d.code));
      }
    } else if (plot.kind === "implicitSurface") {
      if (deferredImplicit.has(plot.id)) continue;
      const compiled = compileExpression(plot.expression, symbols);
      if (compiled.ok) {
        const result = generateImplicitSurface(compiled.value, symbols, { resolution: plot.resolution, iso: plot.iso, box: plot.box });
        if (result.ok) mesh = surfaceMesh(result.value.positions, result.value.normals, result.value.indices, plot.style.color, plot.opacity, false, resolution, []);
        else diagnostics.push(...result.diagnostics.map((d) => d.code));
      }
    } else if (plot.kind === "parametricCurve") {
      const x = compileExpression(plot.xExpression, symbols);
      const y = compileExpression(plot.yExpression, symbols);
      const z = compileExpression(plot.zExpression, symbols);
      if (x.ok && y.ok && z.ok) {
        const result = generateParametricCurve(x.value, y.value, z.value, symbols, plot.tDomain, plot.samples);
        if (result.ok) {
          const geometry = new THREE.BufferGeometry();
          geometry.setAttribute("position", new THREE.BufferAttribute(toThreePositions(result.value.positions), 3));
          const line = new THREE.Line(geometry, new THREE.LineBasicMaterial({ color: colorOf(plot.style.color) }));
          mesh = line;
        }
      }
    }
    if (mesh) {
      mesh.userData.id = plot.id;
      group.add(mesh);
    }
  }
  return { group, diagnostics };
}

function surfaceMesh(
  positions: Float32Array, normals: Float32Array, indices: Uint32Array,
  colorHex: string, opacity: number, showGrid: boolean,
  resolution: { width: number; height: number }, parameterLines: Float32Array[],
): THREE.Object3D {
  const threePositions = toThreePositions(positions);
  const threeNormals = new Float32Array(normals.length);
  for (let i = 0; i + 2 < normals.length; i += 3) {
    const t = mathToThree({ x: normals[i] as number, y: normals[i + 1] as number, z: normals[i + 2] as number });
    threeNormals[i] = t.x; threeNormals[i + 1] = t.y; threeNormals[i + 2] = t.z;
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(threePositions, 3));
  geometry.setAttribute("normal", new THREE.BufferAttribute(threeNormals, 3));
  geometry.setIndex(new THREE.BufferAttribute(new Uint32Array(indices), 1));
  const transparent = opacity < 1;
  const material = new THREE.MeshStandardMaterial({
    color: colorOf(colorHex),
    transparent,
    opacity,
    side: THREE.DoubleSide,
    depthWrite: !transparent,
    roughness: 0.85,
    metalness: 0,
  });
  const mesh = new THREE.Mesh(geometry, material);
  if (showGrid && parameterLines.length > 0) {
    const vertices: number[] = [];
    for (const line of parameterLines) {
      const converted = toThreePositions(line);
      for (let i = 0; i + 5 < converted.length; i += 3) {
        const ax = converted[i] as number, ay = converted[i + 1] as number, az = converted[i + 2] as number;
        const bx = converted[i + 3] as number, by = converted[i + 4] as number, bz = converted[i + 5] as number;
        if (Number.isFinite(ax) && Number.isFinite(bx)) vertices.push(ax, ay, az, bx, by, bz);
      }
    }
    if (vertices.length > 0) {
      const gridGeometry = new LineSegmentsGeometry().setPositions(vertices);
      const gridMaterial = makeLineMaterial(0x4a6f68, 1.2, false, resolution);
      gridMaterial.opacity = 0.5;
      mesh.add(new LineSegments2(gridGeometry, gridMaterial));
    }
  }
  return mesh;
}

/** Build a Three mesh from a worker MeshPayload (implicit isosurfaces). */
export function meshFromPayload(payload: {
  positions: ArrayBuffer; normals: ArrayBuffer; indices: ArrayBuffer;
}, colorHex: string, opacity: number): THREE.Mesh {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(toThreePositions(new Float32Array(payload.positions)), 3));
  geometry.setAttribute("normal", new THREE.BufferAttribute(toThreePositions(new Float32Array(payload.normals)), 3));
  const indices = new Uint32Array(payload.indices);
  geometry.setIndex(new THREE.BufferAttribute(indices, 1));
  const transparent = opacity < 1;
  return new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({
    color: colorOf(colorHex),
    transparent,
    opacity,
    side: THREE.DoubleSide,
    depthWrite: !transparent,
    roughness: 0.85,
    metalness: 0,
  }));
}

/** Grid + axes group in math space (z up), converted through the adapter. */
export function buildEnvironment(showGridXY: boolean, showAxes: boolean): THREE.Group {
  const group = new THREE.Group();
  const gridVertices: number[] = [];
  const size = 10;
  const stepCount = 10;
  if (showGridXY) {
    for (let i = 0; i <= stepCount; i++) {
      const t = -size + (2 * size * i) / stepCount;
      const a = mathToThree({ x: t, y: -size, z: 0 });
      const b = mathToThree({ x: t, y: size, z: 0 });
      const c = mathToThree({ x: -size, y: t, z: 0 });
      const d = mathToThree({ x: size, y: t, z: 0 });
      gridVertices.push(a.x, a.y, a.z, b.x, b.y, b.z, c.x, c.y, c.z, d.x, d.y, d.z);
    }
  }
  const axesVertices: number[] = [];
  const axisColors: number[] = [];
  if (showAxes) {
    const axes: Array<{ dir: Pt3; color: [number, number, number] }> = [
      { dir: { x: 1, y: 0, z: 0 }, color: [0.72, 0.31, 0.24] },
      { dir: { x: 0, y: 1, z: 0 }, color: [0.30, 0.47, 0.63] },
      { dir: { x: 0, y: 0, z: 1 }, color: [0.28, 0.49, 0.39] },
    ];
    for (const axis of axes) {
      const o = mathToThree({ x: 0, y: 0, z: 0 });
      const tip = mathToThree({ x: axis.dir.x * size, y: axis.dir.y * size, z: axis.dir.z * size });
      axesVertices.push(o.x, o.y, o.z, tip.x, tip.y, tip.z);
      axisColors.push(...axis.color, ...axis.color);
    }
  }
  if (gridVertices.length > 0) {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(gridVertices, 3));
    group.add(new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({ color: 0x9aa79f, transparent: true, opacity: 0.35 })));
  }
  if (axesVertices.length > 0) {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(axesVertices, 3));
    geometry.setAttribute("color", new THREE.Float32BufferAttribute(axisColors, 3));
    group.add(new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({ vertexColors: true })));
  }
  return group;
}
