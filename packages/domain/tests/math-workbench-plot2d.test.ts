import test from "node:test";
import assert from "node:assert/strict";
import {
  sampleExplicit, sampleInverse, sampleParametric2D, samplePolar, implicitContour, compileExpression,
} from "../src/math-workbench/index.ts";

const symbols = { variables: ["x", "y", "t", "theta"], functions: new Map() };
const compile = (src: string) => compileExpression(src, symbols);
const viewport = { minX: -10, maxX: 10, minY: -6, maxY: 6 };

test("tan(x) never bridges asymptotes with a diagonal", () => {
  const c = compile("tan(x)");
  assert.equal(c.ok, true);
  if (!c.ok) return;
  const r = sampleExplicit(c.value, symbols, viewport, undefined);
  assert.equal(r.ok, true);
  if (!r.ok) return;
  assert.ok(r.value.polylines.length >= 2, `expected split polylines, got ${r.value.polylines.length}`);
  for (const line of r.value.polylines) {
    for (let i = 1; i < line.length; i++) {
      const a = line[i - 1] as { x: number; y: number };
      const b = line[i] as { x: number; y: number };
      // No single segment may leap across a pole: huge opposite-sign values
      // joined in one step is exactly the diagonal-bridge artifact.
      const leaps = (a.y > 40 && b.y < -40) || (a.y < -40 && b.y > 40);
      assert.ok(!leaps, `bridge: (${a.x},${a.y})->(${b.x},${b.y})`);
    }
  }
  // No polyline contains points on both sides of π/2 within a tiny neighborhood.
  for (const line of r.value.polylines) {
    const xs = line.map((p) => p.x);
    const hasLeft = xs.some((x) => x > Math.PI / 2 - 0.05 && x < Math.PI / 2);
    const hasRight = xs.some((x) => x > Math.PI / 2 && x < Math.PI / 2 + 0.05);
    assert.ok(!(hasLeft && hasRight), "asymptote bridged");
  }
});

test("circle via implicit contour is closed and near unit radius", () => {
  const c = compile("x^2+y^2-1");
  if (!c.ok) return;
  const r = implicitContour(c.value, symbols, 0, { x: { min: -2, max: 2 }, y: { min: -2, max: 2 } }, 80);
  assert.equal(r.ok, true);
  if (!r.ok) return;
  assert.ok(r.value.polylines.length >= 1);
  const all = r.value.polylines.flat();
  assert.ok(all.length > 100);
  for (const p of all) {
    const radius = Math.hypot(p.x, p.y);
    assert.ok(Math.abs(radius - 1) < 0.05, `radius ${radius} at (${p.x},${p.y})`);
  }
  // Chained into few closed loops (ideally 1).
  assert.ok(r.value.polylines.length <= 3, `loops: ${r.value.polylines.length}`);
});

test("saddle implicit curve resolves both branches", () => {
  const c = compile("x*y-1");
  if (!c.ok) return;
  const r = implicitContour(c.value, symbols, 0, { x: { min: -4, max: 4 }, y: { min: -4, max: 4 } }, 60);
  if (!r.ok) return;
  const all = r.value.polylines.flat();
  assert.ok(all.length > 50);
  for (const p of all) assert.ok(Math.abs(p.x * p.y - 1) < 0.1);
});

test("explicit sampling respects bounded domains", () => {
  const c = compile("sin(x)");
  if (!c.ok) return;
  const r = sampleExplicit(c.value, symbols, viewport, { min: -2, max: 2 });
  if (!r.ok) return;
  for (const line of r.value.polylines) {
    for (const p of line) {
      assert.ok(p.x >= -2.001 && p.x <= 2.001);
      assert.ok(Math.abs(p.y - Math.sin(p.x)) < 0.01);
    }
  }
});

test("inverse x=f(y) plots swap axes correctly", () => {
  const c = compile("y^2");
  if (!c.ok) return;
  const r = sampleInverse(c.value, symbols, viewport, undefined);
  if (!r.ok) return;
  const all = r.value.polylines.flat();
  assert.ok(all.length > 10);
  for (const p of all) assert.ok(Math.abs(p.x - p.y * p.y) < 0.05);
});

test("parametric circle and polar rose sample their domains", () => {
  const x = compile("cos(t)"); const y = compile("sin(t)");
  if (x.ok && y.ok) {
    const r = sampleParametric2D(x.value, y.value, symbols, { min: 0, max: 6.283185307179586 });
    if (r.ok) {
      const pts = r.value.polylines.flat();
      assert.ok(pts.length > 100);
      for (const p of pts) assert.ok(Math.abs(Math.hypot(p.x, p.y) - 1) < 0.01);
    }
  }
  const rose = compile("cos(3*theta)");
  if (rose.ok) {
    const r = samplePolar(rose.value, symbols, { min: 0, max: 6.283185307179586 });
    if (r.ok) {
      const pts = r.value.polylines.flat();
      assert.ok(pts.length > 100);
      assert.ok(pts.some((p) => Math.hypot(p.x, p.y) > 0.98));
      assert.ok(pts.some((p) => Math.hypot(p.x, p.y) < 0.02));
    }
  }
});
