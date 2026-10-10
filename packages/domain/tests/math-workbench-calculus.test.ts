import test from "node:test";
import assert from "node:assert/strict";
import {
  compileExpression, differentiate, differentiateCompiled, evaluateExpression,
  integrateExpression, numericDerivative, symbolicAntiderivative, derivativeAtPoint, EMPTY_SYMBOLS,
  type SymbolTable,
} from "../src/math-workbench/index.ts";

const compile = (src: string) => compileExpression(src, { variables: ["x"], functions: new Map() });

function derivAt(src: string, x: number): number | null {
  const c = compile(src);
  if (!c.ok) return null;
  const d = differentiate(c.value.ast, "x");
  if (!d.ok) return null;
  const v = evaluateExpression(d.value, { x }, EMPTY_SYMBOLS);
  return v.ok ? v.value : null;
}

test("symbolic derivatives match analytic values", () => {
  const cases: Array<[string, number, number]> = [
    ["3*x^2 + 2*x - 7", 1.5, 6 * 1.5 + 2],
    ["sin(x)*cos(x)", 0.7, Math.cos(0.7) ** 2 - Math.sin(0.7) ** 2],
    ["x/exp(x)", 1.3, (1 - 1.3) / Math.exp(1.3)],
    ["sin(x^2)", 0.9, 2 * 0.9 * Math.cos(0.81)],
    ["ln(x)", 2.2, 1 / 2.2],
    ["sqrt(x)", 4, 0.25],
    ["tan(x)", 0.3, 1 / Math.cos(0.3) ** 2],
    ["x^x", 2, 4 * (1 + Math.log(2))], // x^x = e^{x ln x}
  ];
  for (const [src, x, expected] of cases) {
    const got = derivAt(src, x);
    assert.ok(got !== null, `derivative failed: ${src}`);
    assert.ok(Math.abs((got as number) - expected) < 1e-9 * Math.max(1, Math.abs(expected)), `${src} at ${x}: ${got} vs ${expected}`);
  }
});

test("partial derivative w.r.t. chosen variable only", () => {
  const c = compileExpression("x^2*y + sin(y)", { variables: ["x", "y"], functions: new Map() });
  assert.equal(c.ok, true);
  if (!c.ok) return;
  const dX = differentiate(c.value.ast, "x");
  const dY = differentiate(c.value.ast, "y");
  assert.equal(dX.ok, true);
  assert.equal(dY.ok, true);
  if (dX.ok) {
    const v = evaluateExpression(dX.value, { x: 3, y: 5 }, EMPTY_SYMBOLS);
    if (v.ok) assert.ok(Math.abs(v.value - 30) < 1e-12);
  }
  if (dY.ok) {
    const v = evaluateExpression(dY.value, { x: 3, y: 5 }, EMPTY_SYMBOLS);
    if (v.ok) assert.ok(Math.abs(v.value - (9 + Math.cos(5))) < 1e-12);
  }
});

test("non-differentiable constructs produce warnings or errors", () => {
  const c = compile("abs(x)");
  if (!c.ok) return;
  const d = differentiate(c.value.ast, "x");
  assert.equal(d.ok, true);
  if (d.ok) assert.ok(d.diagnostics.some((x) => x.code === "derivative_nondifferentiable"));
  // A call to a user-defined function cannot be differentiated symbolically.
  const inner = compile("x");
  if (!inner.ok) return;
  const userTable: SymbolTable = { variables: ["x"], functions: new Map([["myfn", { arity: 1, params: ["x"], body: inner.value.ast }]]) };
  const withUser = compileExpression("myfn(x)", userTable);
  assert.equal(withUser.ok, true);
  if (withUser.ok) {
    const bad = differentiate(withUser.value.ast, "x", userTable);
    assert.equal(bad.ok, false);
  }
});

test("numeric derivative is accurate on smooth functions", () => {
  const r = numericDerivative((x) => Math.sin(x), 0.8);
  assert.equal(r.ok, true);
  if (r.ok) assert.ok(Math.abs(r.value.value - Math.cos(0.8)) < 1e-6, String(r.value.value));
  const r2 = numericDerivative((x) => (x < 1 ? x : 2 - x), 1.0);
  assert.equal(r2.ok, true);
  if (r2.ok) assert.ok(r2.diagnostics.some((d) => d.code === "derivative_uncertain") || Math.abs(r2.value.value) < 1.2);
});

test("integral values match analytic results within 1e-5", () => {
  const x2 = compile("x^2");
  if (x2.ok) {
    const r = integrateExpression(x2.value, "x", 0, 1);
    assert.equal(r.ok, true);
    if (r.ok) assert.ok(Math.abs(r.value.estimate - 1 / 3) < 1e-5, String(r.value.estimate));
  }
  const sin = compile("sin(x)");
  if (sin.ok) {
    const r = integrateExpression(sin.value, "x", 0, Math.PI);
    assert.equal(r.ok, true);
    if (r.ok) assert.ok(Math.abs(r.value.estimate - 2) < 1e-5);
  }
});

test("swapped integration bounds negate the result", () => {
  const c = compile("x");
  if (!c.ok) return;
  const forward = integrateExpression(c.value, "x", 0, 2);
  const backward = integrateExpression(c.value, "x", 2, 0);
  assert.equal(forward.ok, true);
  assert.equal(backward.ok, true);
  if (forward.ok && backward.ok) assert.ok(Math.abs(forward.value.estimate + backward.value.estimate) < 1e-9);
});

test("integrals across singularities do not report success", () => {
  const c = compile("1/x");
  if (!c.ok) return;
  const r = integrateExpression(c.value, "x", -1, 1);
  if (r.ok) {
    // Either a hard failure or a clearly flagged non-converged estimate.
    assert.ok(!r.value.converged || Math.abs(r.value.estimate) > 1e3, "divergent integral must not look converged");
  } else {
    assert.ok(r.diagnostics.some((d) => ["integral_discontinuity", "integral_not_converged", "integral_endpoint_singular"].includes(d.code)));
  }
  const div = compile("1/x^2");
  if (div.ok) {
    const r2 = integrateExpression(div.value, "x", 0.5, 4);
    assert.equal(r2.ok, true);
    if (r2.ok) assert.ok(Math.abs(r2.value.estimate - 1.75) < 1e-4); // converges away from 0
  }
});

test("symbolic antiderivative covers verified rules only", () => {
  const c = compile("x^2");
  if (c.ok) {
    const r = symbolicAntiderivative(c.value.ast, "x");
    assert.equal(r.ok, true);
    if (r.ok) {
      const v = evaluateExpression(r.value, { x: 2 }, EMPTY_SYMBOLS);
      if (v.ok) assert.ok(Math.abs(v.value - 8 / 3) < 1e-12);
    }
  }
  const unsupported = compile("sin(x)*ln(x)");
  if (unsupported.ok) {
    const r2 = symbolicAntiderivative(unsupported.value.ast, "x");
    assert.equal(r2.ok, false);
    if (!r2.ok) assert.equal(r2.diagnostics[0]?.code, "symbolic_unsupported");
  }
  const sinLinear = compile("sin(2*x+1)");
  if (sinLinear.ok) {
    const r3 = symbolicAntiderivative(sinLinear.value.ast, "x");
    assert.equal(r3.ok, true);
    if (r3.ok) {
      // F(3) − F(0) must equal the definite integral of sin(2x+1) on [0,3].
      const at3 = evaluateExpression(r3.value, { x: 3 }, EMPTY_SYMBOLS);
      const at0 = evaluateExpression(r3.value, { x: 0 }, EMPTY_SYMBOLS);
      if (at3.ok && at0.ok) {
        const expected = (Math.cos(1) - Math.cos(7)) / 2;
        assert.ok(Math.abs(at3.value - at0.value - expected) < 1e-12);
      }
    }
  }
});

test("derivativeAtPoint uses compiled expressions", () => {
  const c = compile("x^3");
  if (!c.ok) return;
  const table: SymbolTable = { variables: ["x"], functions: new Map() };
  const r = derivativeAtPoint(c.value, "x", 2, table);
  assert.equal(r.ok, true);
  if (r.ok) assert.ok(Math.abs(r.value.value - 12) < 1e-4);
});

test("differentiateCompiled repackages results for cache keys", () => {
  const c = compile("3*x^2");
  if (!c.ok) return;
  const d = differentiateCompiled(c.value, "x");
  assert.equal(d.ok, true);
  if (d.ok) {
    const v = evaluateExpression(d.value, { x: 4 }, EMPTY_SYMBOLS);
    if (v.ok) assert.equal(v.value, 24);
  }
});
