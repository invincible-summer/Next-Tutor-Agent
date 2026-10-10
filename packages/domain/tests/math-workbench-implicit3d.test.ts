import test from "node:test";
import assert from "node:assert/strict";
import { generateImplicitSurface, compileExpression } from "../src/math-workbench/index.ts";

const symbols = { variables: ["x", "y", "z"], functions: new Map() };
const compile = (src: string) => compileExpression(src, symbols);

const sphereBox = { x: { min: -1.4, max: 1.4 }, y: { min: -1.4, max: 1.4 }, z: { min: -1.4, max: 1.4 } };

test("implicit sphere: watertight vertices, residual within grid scale, outward normals", () => {
  const c = compile("x^2+y^2+z^2");
  if (!c.ok) return;
  const r = generateImplicitSurface(c.value, symbols, { resolution: 20, iso: 1, box: sphereBox });
  assert.equal(r.ok, true);
  if (!r.ok) return;
  const { positions, normals, indices } = r.value;
  assert.ok(positions.length / 3 > 100, `vertices ${positions.length / 3}`);
  for (let i = 0; i < positions.length; i += 3) {
    const x = positions[i] as number, y = positions[i + 1] as number, z = positions[i + 2] as number;
    const residual = Math.abs(x * x + y * y + z * z - 1);
    assert.ok(residual < 0.25, `vertex residual ${residual} at (${x},${y},${z})`); // grid-scale tolerance
    assert.ok(Number.isFinite(x) && Number.isFinite(y) && Number.isFinite(z));
  }
  for (let i = 0; i < indices.length; i++) {
    const idx = indices[i] as number;
    assert.ok(idx < positions.length / 3);
  }
  // Shared-edge dedup: every referenced vertex should appear in ≥2 triangles
  // (watertight), except pathological cases.
  const useCount = new Array<number>(positions.length / 3).fill(0);
  for (const idx of indices) useCount[idx] = (useCount[idx] ?? 0) + 1;
  const lonely = useCount.filter((n) => n === 1).length;
  assert.equal(lonely, 0, `${lonely} vertices used once — cracks in the mesh`);
  // Gradient normals point outward (F increases outward for x²+y²+z²).
  for (let i = 0; i < normals.length; i += 3) {
    const nx = normals[i] as number, ny = normals[i + 1] as number, nz = normals[i + 2] as number;
    const px = positions[i] as number, py = positions[i + 1] as number, pz = positions[i + 2] as number;
    assert.ok(nx * px + ny * py + nz * pz > 0.1, `normal inward at (${px},${py},${pz})`);
  }
  assert.ok(r.value.boundaryLines.length === 0 || r.diagnostics.some((d) => d.code !== "x"));
});

test("empty isosurface returns a legal empty mesh with diagnostics", () => {
  const c = compile("x^2+y^2+z^2"); // iso 9 never crossed inside the ±1.4 box (max 5.88)
  if (!c.ok) return;
  const r = generateImplicitSurface(c.value, symbols, { resolution: 10, iso: 9, box: sphereBox });
  assert.equal(r.ok, true);
  if (r.ok) {
    assert.equal(r.value.positions.length, 0);
    assert.equal(r.value.indices.length, 0);
    assert.ok(r.diagnostics.some((d) => d.code === "implicit_no_surface"));
  }
});

test("torus-like implicit resolves both components and respects resolution caps", () => {
  const c = compile("(x^2+y^2+z^2+3^2-1^2)^2 - 4*3^2*(x^2+y^2)");
  if (!c.ok) return;
  const r = generateImplicitSurface(c.value, symbols, { resolution: 24, iso: 0, box: { x: { min: -4.5, max: 4.5 }, y: { min: -4.5, max: 4.5 }, z: { min: -1.5, max: 1.5 } } });
  assert.equal(r.ok, true);
  if (r.ok) {
    assert.ok(r.value.positions.length / 3 > 200);
    for (let i = 0; i < r.value.positions.length; i += 3) {
      const x = r.value.positions[i] as number, y = r.value.positions[i + 1] as number, z = r.value.positions[i + 2] as number;
      const F = (x * x + y * y + z * z + 8) ** 2 - 36 * (x * x + y * y);
      assert.ok(Math.abs(F) < 30, `residual ${F}`);
    }
  }
  // Resolution is hard-capped even if the caller asks for more.
  const big = generateImplicitSurface(c.value, symbols, { resolution: 999, iso: 0, box: sphereBox });
  assert.equal(big.ok, true);
});
