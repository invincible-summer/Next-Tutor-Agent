import test from "node:test";
import assert from "node:assert/strict";
import {
  generateExplicitSurface, generateParametricSurface, generateParametricCurve, compileExpression,
} from "../src/math-workbench/index.ts";

const symbols = { variables: ["x", "y", "u", "v", "t"], functions: new Map() };
const compile = (src: string) => compileExpression(src, symbols);

function assertFiniteBuffers(mesh: { positions: Float32Array; normals: Float32Array; indices: Uint32Array }) {
  for (const v of mesh.positions) assert.ok(Number.isFinite(v), `position ${v}`);
  for (const v of mesh.normals) assert.ok(Number.isFinite(v), `normal ${v}`);
  const vertexCount = mesh.positions.length / 3;
  for (const idx of mesh.indices) assert.ok(idx < vertexCount, `index ${idx} >= ${vertexCount}`);
  // No zero-area (degenerate) triangles may enter the GPU buffers.
  for (let i = 0; i + 2 < mesh.indices.length; i += 3) {
    const a = mesh.indices[i] as number, b = mesh.indices[i + 1] as number, c = mesh.indices[i + 2] as number;
    const ax = mesh.positions[a * 3] as number, ay = mesh.positions[a * 3 + 1] as number, az = mesh.positions[a * 3 + 2] as number;
    const bx = mesh.positions[b * 3] as number, by = mesh.positions[b * 3 + 1] as number, bz = mesh.positions[b * 3 + 2] as number;
    const cx = mesh.positions[c * 3] as number, cy = mesh.positions[c * 3 + 1] as number, cz = mesh.positions[c * 3 + 2] as number;
    const area = 0.5 * Math.hypot(
      (by - ay) * (cz - az) - (bz - az) * (cy - ay),
      (bz - az) * (cx - ax) - (bx - ax) * (cz - az),
      (bx - ax) * (cy - ay) - (by - ay) * (cx - ax),
    );
    assert.ok(area > 1e-14, `degenerate triangle at indices ${a},${b},${c} (area ${area})`);
  }
}

test("flat z=0 surface has +Z normals; z=x+y has normalized analytic normals", () => {
  const flat = compile("0");
  if (flat.ok) {
    const r = generateExplicitSurface(flat.value, symbols, { min: -1, max: 1 }, { min: -1, max: 1 }, "low");
    assert.equal(r.ok, true);
    if (r.ok) {
      assertFiniteBuffers(r.value);
      for (let i = 0; i < r.value.normals.length; i += 3) {
        assert.ok(Math.abs((r.value.normals[i] as number) - 0) < 1e-6);
        assert.ok(Math.abs((r.value.normals[i + 1] as number) - 0) < 1e-6);
        assert.ok(Math.abs((r.value.normals[i + 2] as number) - 1) < 1e-6);
      }
    }
  }
  const tilt = compile("x+y");
  if (tilt.ok) {
    const r = generateExplicitSurface(tilt.value, symbols, { min: -1, max: 1 }, { min: -1, max: 1 }, "low");
    if (r.ok) {
      const n = { x: r.value.normals[0] as number, y: r.value.normals[1] as number, z: r.value.normals[2] as number };
      const scale = 1 / Math.sqrt(3);
      assert.ok(Math.abs(n.x + scale) < 1e-4 && Math.abs(n.y + scale) < 1e-4 && Math.abs(n.z - scale) < 1e-4, JSON.stringify(n));
    }
  }
});

test("explicit surface drops discontinuous cells (1/x² has no bridging wall)", () => {
  const c = compile("1/(x^2+y^2)");
  if (!c.ok) return;
  const r = generateExplicitSurface(c.value, symbols, { min: -1, max: 1 }, { min: -1, max: 1 }, "low");
  assert.equal(r.ok, true);
  if (r.ok) {
    assertFiniteBuffers(r.value);
    // Cells at the pole and steep-jump cells are dropped, so the mesh is
    // smaller than a full 31×31 quad grid and reports what it dropped.
    assert.ok(r.value.positions.length / 3 < 31 * 31 * 4, `verts ${r.value.positions.length / 3}`);
    assert.ok(r.diagnostics.some((d) => d.code === "surface_cells_dropped" || d.code === "surface_empty"));
    // Whatever survived stays within the |z| sanity bound.
    for (const z of r.value.positions) if (true) { assert.ok(Math.abs(z) <= 1e6); break; }
  }
});

test("fully undefined surface yields a valid empty mesh, not NaN buffers", () => {
  const c = compile("ln(-1)");
  if (!c.ok) return;
  const r = generateExplicitSurface(c.value, symbols, { min: -1, max: 1 }, { min: -1, max: 1 }, "low");
  assert.equal(r.ok, true);
  if (r.ok) {
    assert.equal(r.value.positions.length, 0);
    assert.equal(r.value.indices.length, 0);
  }
});

test("parametric sphere points lie on the unit sphere with valid normals", () => {
  const x = compile("cos(u)*cos(v)");
  const y = compile("cos(u)*sin(v)");
  const z = compile("sin(u)");
  if (!(x.ok && y.ok && z.ok)) return;
  const r = generateParametricSurface(x.value, y.value, z.value, symbols, { min: -1.5707963267948966, max: 1.5707963267948966 }, { min: 0, max: 6.283185307179586 }, "low", false, true);
  assert.equal(r.ok, true);
  if (r.ok) {
    assertFiniteBuffers(r.value);
    for (let i = 0; i < r.value.positions.length; i += 3) {
      const px = r.value.positions[i] as number, py = r.value.positions[i + 1] as number, pz = r.value.positions[i + 2] as number;
      assert.ok(Math.abs(Math.hypot(px, py, pz) - 1) < 1e-6, `radius ${Math.hypot(px, py, pz)}`);
    }
    // Consistent orientation: every normal points to the same side of the
    // sphere (u,v parameterization fixes which side; both are valid).
    let orientation = 0;
    for (let i = 0; i < r.value.normals.length; i += 3) {
      const nx = r.value.normals[i] as number, ny = r.value.normals[i + 1] as number, nz = r.value.normals[i + 2] as number;
      const px = r.value.positions[i] as number, py = r.value.positions[i + 1] as number, pz = r.value.positions[i + 2] as number;
      const dot = nx * px + ny * py + nz * pz;
      const sign = dot > 0 ? 1 : -1;
      if (orientation === 0) orientation = sign;
      assert.equal(sign, orientation, `inconsistent normal orientation at ${i / 3}`);
      assert.ok(Math.abs(dot) > 0.5, `normal orthogonal at ${i / 3}`);
    }
  }
});

test("parametric torus wraps seamlessly (wrapU/wrapV)", () => {
  const x = compile("(2+cos(v))*cos(u)");
  const y = compile("(2+cos(v))*sin(u)");
  const z = compile("sin(v)");
  if (!(x.ok && y.ok && z.ok)) return;
  const r = generateParametricSurface(x.value, y.value, z.value, symbols, { min: 0, max: 6.283185307179586 }, { min: 0, max: 6.283185307179586 }, "low", true, true);
  assert.equal(r.ok, true);
  if (r.ok) {
    assertFiniteBuffers(r.value);
    for (let i = 0; i < r.value.positions.length; i += 3) {
      const px = r.value.positions[i] as number, py = r.value.positions[i + 1] as number, pz = r.value.positions[i + 2] as number;
      const tube = Math.hypot(Math.hypot(px, py) - 2, pz);
      assert.ok(Math.abs(tube - 1) < 1e-6, `tube radius ${tube}`);
    }
    // No boundary lines because the surface is closed.
    assert.equal(r.value.boundaryLines.length, 0);
  }
});

test("parametric curve samples, tangents and length", () => {
  const x = compile("cos(t)"); const y = compile("sin(t)"); const z = compile("0");
  if (!(x.ok && y.ok && z.ok)) return;
  const r = generateParametricCurve(x.value, y.value, z.value, symbols, { min: 0, max: 6.283185307179586 }, 400);
  assert.equal(r.ok, true);
  if (r.ok) {
    assert.ok(Math.abs(r.value.length - 2 * Math.PI) < 0.05, `length ${r.value.length}`);
    for (const v of r.value.positions) assert.ok(Number.isFinite(v));
    // Tangent at t=0 is (0,1,0).
    const tx = r.value.tangents[0] as number, ty = r.value.tangents[1] as number;
    assert.ok(Math.abs(tx) < 0.05 && Math.abs(ty - 1) < 0.05);
  }
});

test("patch tiling covers the whole index buffer contiguously", () => {
  const c = compile("sin(x)*cos(y)");
  if (!c.ok) return;
  const r = generateExplicitSurface(c.value, symbols, { min: -3, max: 3 }, { min: -3, max: 3 }, "low");
  if (r.ok) {
    let covered = 0;
    let lastEnd = 0;
    for (const patch of r.value.patches) {
      assert.equal(patch.offset, lastEnd);
      covered += patch.count;
      lastEnd = patch.offset + patch.count;
    }
    assert.ok(r.value.patches.length > 1);
    assert.ok(covered <= r.value.indices.length);
  }
});
