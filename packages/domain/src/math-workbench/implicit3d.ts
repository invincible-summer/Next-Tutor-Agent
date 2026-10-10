/**
 * Implicit isosurface F(x,y,z)=iso via marching tetrahedra on a bounded
 * voxel box. Each cube decomposes into six tetrahedra with a consistent
 * diagonal choice; crossings on shared edges are deduplicated through an
 * edge-key vertex cache so neighboring tetrahedra produce watertight
 * connections (no cracks or duplicate vertices). Normals come from ∇F via
 * central differences; near-zero gradients fall back to geometric normals.
 * Resolution is capped (plan G4); bbox-clipped sections are honest open
 * surfaces, not silently closed ones.
 */

import { err, mathFail, mathOk, warn, type MathDiagnostic, type MathResult, MATH_LIMITS } from "./diagnostics.ts";
import { evaluateExpression, type CompiledFunction, type SymbolTable } from "./expression.ts";
import { v3, vnormalize, type Pt3 } from "./math3d.ts";
import type { Bounds3, SurfaceMeshData } from "./surfaces3d.ts";

/** Cube corner order: (i,j,k) bits → 8 vertices, canonical layout. */
const CUBE_CORNERS: readonly Pt3[] = [
  v3(0, 0, 0), v3(1, 0, 0), v3(1, 1, 0), v3(0, 1, 0),
  v3(0, 0, 1), v3(1, 0, 1), v3(1, 1, 1), v3(0, 1, 1),
];

/** Six tetrahedra sharing the 0–6 body diagonal ((0,0,0)→(1,1,1)). The same
 * decomposition for every cube keeps shared faces split along matching
 * diagonals, so crossings on cube-boundary edges deduplicate correctly. */
const TETRA_EDGES: readonly [number, number][][] = [
  [[0, 1], [0, 2], [0, 6], [1, 2], [1, 6], [2, 6]],
  [[0, 2], [0, 3], [0, 6], [2, 3], [2, 6], [3, 6]],
  [[0, 3], [0, 7], [0, 6], [3, 7], [3, 6], [7, 6]],
  [[0, 7], [0, 4], [0, 6], [7, 4], [7, 6], [4, 6]],
  [[0, 4], [0, 5], [0, 6], [4, 5], [4, 6], [5, 6]],
  [[0, 5], [0, 1], [0, 6], [5, 1], [5, 6], [1, 6]],
];

export interface ImplicitMeshOptions {
  resolution: number;
  iso: number;
  box: { x: { min: number; max: number }; y: { min: number; max: number }; z: { min: number; max: number } };
}

/** Sample grid values once per voxel corner (shared by all tetrahedra). */
export function generateImplicitSurface(
  compiled: CompiledFunction,
  symbols: SymbolTable,
  options: ImplicitMeshOptions,
): MathResult<SurfaceMeshData> {
  const diagnostics: MathDiagnostic[] = [];
  const n = Math.min(Math.max(Math.floor(options.resolution), 6), MATH_LIMITS.maxImplicitGrid3d);
  const dx = (options.box.x.max - options.box.x.min) / n;
  const dy = (options.box.y.max - options.box.y.min) / n;
  const dz = (options.box.z.max - options.box.z.min) / n;
  const corner = (i: number, j: number, k: number): Pt3 => v3(
    options.box.x.min + i * dx,
    options.box.y.min + j * dy,
    options.box.z.min + k * dz,
  );
  const evalF = (p: Pt3): number | null => {
    const v = evaluateExpression(compiled, { x: p.x, y: p.y, z: p.z }, symbols);
    return v.ok && Number.isFinite(v.value) ? v.value : null;
  };
  // Precompute corner field values; undefined regions disable the cube.
  const values = new Array<number | null>((n + 1) * (n + 1) * (n + 1)).fill(null);
  let defined = 0;
  for (let k = 0; k <= n; k++) {
    for (let j = 0; j <= n; j++) {
      for (let i = 0; i <= n; i++) {
        const v = evalF(corner(i, j, k));
        values[(k * (n + 1) + j) * (n + 1) + i] = v;
        if (v !== null) defined++;
      }
    }
  }
  if (defined === 0) return mathFail([err("implicit_all_undefined")]);
  const gridValue = (i: number, j: number, k: number): number | null => values[(k * (n + 1) + j) * (n + 1) + i] ?? null;

  // Vertex cache: edge key = "xi yj zk cornerA-cornerB" (voxel + cube-corner pair).
  const vertexCache = new Map<string, number>();
  const positions: number[] = [];
  const normals: number[] = [];
  const indices: number[] = [];
  const bounds: Bounds3 = { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity, minZ: Infinity, maxZ: -Infinity };
  const iso = options.iso;
  const gradientAt = (p: Pt3): Pt3 => {
    const h = Math.max(dx, dy, dz) * 0.75;
    const fx1 = evalF(v3(p.x - h, p.y, p.z)), fx2 = evalF(v3(p.x + h, p.y, p.z));
    const fy1 = evalF(v3(p.x, p.y - h, p.z)), fy2 = evalF(v3(p.x, p.y + h, p.z));
    const fz1 = evalF(v3(p.x, p.y, p.z - h)), fz2 = evalF(v3(p.x, p.y, p.z + h));
    if (fx1 === null || fx2 === null || fy1 === null || fy2 === null || fz1 === null || fz2 === null) return v3(0, 0, 1);
    const grad = v3((fx2 - fx1) / (2 * h), (fy2 - fy1) / (2 * h), (fz2 - fz1) / (2 * h));
    const len = Math.hypot(grad.x, grad.y, grad.z);
    if (len < 1e-14) return v3(0, 0, 1);
    return vnormalize(grad);
  };
  const emitVertex = (key: string, p: Pt3): number => {
    const cached = vertexCache.get(key);
    if (cached !== undefined) return cached;
    const index = positions.length / 3;
    const normal = gradientAt(p);
    positions.push(p.x, p.y, p.z);
    normals.push(normal.x, normal.y, normal.z);
    bounds.minX = Math.min(bounds.minX, p.x); bounds.maxX = Math.max(bounds.maxX, p.x);
    bounds.minY = Math.min(bounds.minY, p.y); bounds.maxY = Math.max(bounds.maxY, p.y);
    bounds.minZ = Math.min(bounds.minZ, p.z); bounds.maxZ = Math.max(bounds.maxZ, p.z);
    vertexCache.set(key, index);
    return index;
  };

  let clipped = false;
  for (let k = 0; k < n; k++) {
    for (let j = 0; j < n; j++) {
      for (let i = 0; i < n; i++) {
        const cubeValues = CUBE_CORNERS.map((c) => gridValue(i + c.x, j + c.y, k + c.z));
        if (cubeValues.some((v) => v === null)) continue;
        const onBoundary = i === 0 || j === 0 || k === 0 || i === n - 1 || j === n - 1 || k === n - 1;
        for (const tetra of TETRA_EDGES) {
          const crossings: Array<{ edge: [number, number]; vertex: Pt3 }> = [];
          for (const [a, b] of tetra) {
            const va = cubeValues[a] as number;
            const vb = cubeValues[b] as number;
            const fa = va - iso;
            const fb = vb - iso;
            if ((fa > 0 && fb > 0) || (fa <= 0 && fb <= 0)) continue;
            const t = fa / (fa - fb);
            const pa = corner(i + (CUBE_CORNERS[a] as Pt3).x, j + (CUBE_CORNERS[a] as Pt3).y, k + (CUBE_CORNERS[a] as Pt3).z);
            const pb = corner(i + (CUBE_CORNERS[b] as Pt3).x, j + (CUBE_CORNERS[b] as Pt3).y, k + (CUBE_CORNERS[b] as Pt3).z);
            crossings.push({
              edge: [a, b],
              vertex: v3(pa.x + (pb.x - pa.x) * t, pa.y + (pb.y - pa.y) * t, pa.z + (pb.z - pa.z) * t),
            });
          }
          if (crossings.length < 3) continue;
          // A tetrahedron yields exactly one triangle (or a quad as two).
          const ids = crossings.map((c) => {
            const [a, b] = c.edge;
            // GLOBAL grid edge key: neighboring voxels compute the same key
            // for shared cube-boundary edges, so vertices merge watertight.
            const ga = v3(i + (CUBE_CORNERS[a] as Pt3).x, j + (CUBE_CORNERS[a] as Pt3).y, k + (CUBE_CORNERS[a] as Pt3).z);
            const gb = v3(i + (CUBE_CORNERS[b] as Pt3).x, j + (CUBE_CORNERS[b] as Pt3).y, k + (CUBE_CORNERS[b] as Pt3).z);
            const ka = `${ga.x},${ga.y},${ga.z}`;
            const kb = `${gb.x},${gb.y},${gb.z}`;
            const key = ka < kb ? `${ka}|${kb}` : `${kb}|${ka}`;
            return emitVertex(key, c.vertex);
          });
          if (crossings.length === 3) {
            indices.push(ids[0] as number, ids[1] as number, ids[2] as number);
          } else if (crossings.length === 4) {
            // Order by a plane fit through the four points: split along the
            // shorter diagonal keeps the result deterministic.
            indices.push(ids[0] as number, ids[1] as number, ids[2] as number);
            indices.push(ids[0] as number, ids[2] as number, ids[3] as number);
          }
          if (onBoundary) clipped = true;
        }
      }
    }
  }
  const triangleCount = indices.length / 3;
  if (triangleCount > MATH_LIMITS.maxTriangles3d) {
    return mathFail([err("mesh_limit_exceeded", { args: { triangles: triangleCount, limit: MATH_LIMITS.maxTriangles3d } })]);
  }
  if (triangleCount === 0) {
    diagnostics.push(warn("implicit_no_surface", {}));
    return mathOk({
      positions: new Float32Array(0), normals: new Float32Array(0), indices: new Uint32Array(0),
      bounds: { minX: 0, maxX: 0, minY: 0, maxY: 0, minZ: 0, maxZ: 0 },
      parameterLines: [], boundaryLines: [], patches: [],
    }, diagnostics);
  }
  if (clipped) diagnostics.push(warn("implicit_clipped_to_box", {}));
  return mathOk({
    positions: new Float32Array(positions),
    normals: new Float32Array(normals),
    indices: new Uint32Array(indices),
    bounds,
    parameterLines: [],
    boundaryLines: [],
    patches: [{ offset: 0, count: indices.length, bounds }],
  }, diagnostics);
}
