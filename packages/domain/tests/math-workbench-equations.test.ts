import test from "node:test";
import assert from "node:assert/strict";
import {
  solveLinear, solveQuadratic, solveLinearSystem, solveInterval, tryExactPolynomial, parseEquation,
} from "../src/math-workbench/index.ts";

test("linear: unique / none / infinite", () => {
  const r = solveLinear(2, -6);
  assert.equal(r.ok, true);
  if (r.ok) assert.deepEqual(r.value.roots, [3]);
  assert.equal(solveLinear(0, 1).ok, false);
  assert.equal(solveLinear(0, 0).ok, false);
});

test("quadratic: stable roots incl. cancellation case", () => {
  const r = solveQuadratic(1, 0, -4); // x²-4=0
  assert.equal(r.ok, true);
  if (r.ok) assert.deepEqual(r.value.roots.map((x) => Math.round(x * 1e9) / 1e9), [-2, 2]);
  const dbl = solveQuadratic(1, -2, 1); // (x-1)²
  assert.equal(dbl.ok, true);
  if (dbl.ok) {
    assert.deepEqual(dbl.value.roots, [1, 1]);
    assert.deepEqual(dbl.value.kinds, ["double", "double"]);
  }
  const none = solveQuadratic(1, 0, 1);
  assert.equal(none.ok, true);
  if (none.ok) assert.deepEqual(none.value.roots, []);
  // Catastrophic-cancellation-safe: roots of x² - 1e8 x + 1.
  const big = solveQuadratic(1, -1e8, 1);
  if (big.ok) {
    const [r1, r2] = big.value.roots as [number, number];
    assert.ok(Math.abs(r1 * r2 - 1) < 1e-6 * Math.abs(r2), `${r1} * ${r2}`);
    assert.ok(Math.abs(r1 + r2 - 1e8) < 1e-6 * 1e8);
  }
});

test("linear systems: unique, inconsistent, underdetermined", () => {
  const unique = solveLinearSystem([[2, 1], [1, -1]], [5, 1]); // x=2, y=1
  assert.equal(unique.ok, true);
  if (unique.ok) {
    assert.equal(unique.value.status, "unique");
    assert.deepEqual(unique.value.x?.map((v) => Math.round(v * 1e9) / 1e9), [2, 1]);
    assert.ok((unique.value.residual ?? 1) < 1e-9);
  }
  const none = solveLinearSystem([[1, 1], [2, 2]], [1, 3]);
  assert.equal(none.ok, true);
  if (none.ok) assert.equal(none.value.status, "none");
  const under = solveLinearSystem([[1, 1], [2, 2]], [2, 4]);
  assert.equal(under.ok, true);
  if (under.ok) assert.equal(under.value.status, "underdetermined");
  const three = solveLinearSystem([[1, 1, 1], [2, -1, 3], [1, 2, -1]], [6, 9, 2]);
  if (three.ok && three.value.status === "unique") {
    assert.deepEqual(three.value.x?.map((v) => Math.round(v * 1e9) / 1e9), [1, 2, 3]);
  } else {
    assert.fail("3x3 unique system should solve");
  }
});

test("interval roots: brackets, residuals, pole rejection", () => {
  const r = solveInterval((x) => x * x - 4, [-5, 5]);
  assert.equal(r.ok, true);
  if (r.ok) {
    assert.equal(r.value.roots.length, 2);
    assert.ok(Math.abs((r.value.roots[0] as number) + 2) < 1e-6);
    assert.ok(Math.abs((r.value.roots[1] as number) - 2) < 1e-6);
  }
  const tan = solveInterval(Math.tan, [-4, 4]);
  assert.equal(tan.ok, true);
  if (tan.ok) {
    // tan poles at ±π/2 must NOT be reported as roots; ±π are true zeros.
    for (const root of tan.value.roots) {
      assert.ok(Math.abs(Math.tan(root)) < 1e-6, `pole leaked as root: ${root}`);
      assert.ok(Math.abs(Math.abs(root) - Math.PI / 2) > 1e-3, `pole at ${root}`);
    }
    assert.ok(tan.value.roots.some((root) => Math.abs(root - Math.PI) < 1e-4), "π zero missing");
    assert.ok(tan.value.roots.some((root) => Math.abs(root + Math.PI) < 1e-4), "-π zero missing");
    assert.ok(tan.value.roots.every((root) => Math.abs(Math.tan(root)) < 1e-6));
  }
  const recip = solveInterval((x) => 1 / (x - 1), [0, 2]);
  assert.equal(recip.ok, true);
  if (recip.ok) assert.deepEqual(recip.value.roots, []); // 1/(x-1)=0 has no root
});

test("tangential double roots are detected with warnings", () => {
  const r = solveInterval((x) => (x - 1) ** 2, [-2, 4]);
  assert.equal(r.ok, true);
  if (r.ok) {
    assert.ok(r.value.roots.some((root) => Math.abs(root - 1) < 1e-3), `roots: ${r.value.roots}`);
    assert.ok(r.diagnostics.some((d) => d.code === "root_tangential"));
  }
});

test("exact polynomial branch extracts degree ≤ 2 equations", () => {
  const eq = parseEquation("x^2 - 4 = 0");
  assert.equal(eq.ok, true);
  if (!eq.ok) return;
  const exact = tryExactPolynomial(eq.value.lhs, eq.value.rhs, "x");
  assert.equal(exact.exact, true);
  if (exact.exact) {
    assert.equal(exact.result.ok, true);
    if (exact.result.ok) {
      assert.deepEqual(exact.result.value.roots.map((v) => Math.round(v * 1e6) / 1e6), [-2, 2]);
      assert.equal(exact.result.value.method, "quadratic");
    }
  }
  const cubic = parseEquation("x^3 = 1");
  if (cubic.ok) assert.equal(tryExactPolynomial(cubic.value.lhs, cubic.value.rhs, "x").exact, false);
});
