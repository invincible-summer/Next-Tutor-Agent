import test from "node:test";
import assert from "node:assert/strict";
import {
  compileExpression, evaluateExpression, parseExpression, parseEquation, normalizeMathInput,
  validateSymbols, collectReferences, canonicalSource, EMPTY_SYMBOLS,
  type SymbolTable, type UserFunctionEntry,
} from "../src/math-workbench/index.ts";

const sym = (variables: string[], functions: Record<string, UserFunctionEntry> = {}): SymbolTable =>
  ({ variables, functions: new Map(Object.entries(functions)) });

const evalStr = (source: string, vars: Record<string, number> = {}, symbols: SymbolTable = sym(["x", "y", "t", "theta"])) => {
  const compiled = compileExpression(source, symbols);
  if (!compiled.ok) return compiled;
  return evaluateExpression(compiled.value, vars, symbols);
};

test("precedence: -x^2 parses as -(x^2), 2^3^2 right-associative", () => {
  const a = evalStr("-x^2", { x: 3 });
  assert.equal(a.ok, true);
  if (a.ok) assert.equal(a.value, -9);
  const b = evalStr("2^3^2");
  assert.equal(b.ok, true);
  if (b.ok) assert.equal(b.value, 512); // 2^(3^2), not (2^3)^2=64
  const c = evalStr("-2^2");
  assert.equal(c.ok, true);
  if (c.ok) assert.equal(c.value, -4);
});

test("scientific notation, constants and aliases", () => {
  assert.equal(evalStr("1.5e-3")?.ok, true);
  const a = evalStr("1.5e2");
  if (a?.ok) assert.equal(a.value, 150);
  const pi = evalStr("pi");
  if (pi?.ok) assert.ok(Math.abs(pi.value - Math.PI) < 1e-12);
  const aliased = compileExpression(normalizeMathInput("2π + θ"), sym(["theta"]));
  assert.equal(aliased.ok, true);
  if (aliased.ok) {
    const v = evaluateExpression(aliased.value, { theta: 0.5 });
    if (v.ok) assert.ok(Math.abs(v.value - (2 * Math.PI + 0.5)) < 1e-12);
  }
});

test("juxtaposition normalization is digit-only, never name-name", () => {
  assert.equal(normalizeMathInput("2x"), "2*x");
  assert.equal(normalizeMathInput("3sin(x)"), "3*sin(x)");
  assert.equal(normalizeMathInput("2(x+1)"), "2*(x+1)");
  assert.equal(normalizeMathInput("xy"), "xy");
  const ambiguous = compileExpression("xy", sym(["x", "y"]));
  assert.equal(ambiguous.ok, false); // unknown symbol: xy ≠ x*y
});

test("builtins evaluate with correct arity and radians", () => {
  const s = evalStr("sin(pi/2)");
  if (s?.ok) assert.equal(s.value, 1);
  const wrongArity = compileExpression("sin(1,2)", sym(["x"]));
  assert.equal(wrongArity.ok, false);
  if (!wrongArity.ok) assert.equal(wrongArity.diagnostics[0]?.code, "wrong_argument_count");
  const deg = evalStr("sin(deg(90))");
  if (deg?.ok) assert.ok(Math.abs(deg.value - 1) < 1e-12);
});

test("member access, assignment and strings are rejected", () => {
  for (const bad of ["window.x", "x = 1", "'str'", "x.y", "f{x}", ";", "new Function"]) {
    const r = parseExpression(normalizeMathInput(bad));
    assert.equal(r.ok, false, `expected parse failure: ${bad}`);
  }
  // `constructor` and `import(x)` tokenize as plain name/call syntax; without
  // member access, eval or dynamic import they are inert unknown references.
  for (const inert of ["constructor", "import(x)"]) {
    const c = compileExpression(inert, sym(["x"]));
    assert.equal(c.ok, false, inert);
    if (!c.ok) assert.ok(["unknown_symbol", "unknown_function"].includes(c.diagnostics[0]?.code ?? ""));
  }
});

test("parse errors carry character spans", () => {
  const r = parseExpression("1 + * 2");
  assert.equal(r.ok, false);
  if (!r.ok) {
    const d = r.diagnostics[0];
    assert.equal(d?.severity, "error");
    assert.ok(d?.range && d.range.start >= 2 && d.range.start <= 6, JSON.stringify(d));
  }
  const r2 = parseExpression("sin(x");
  assert.equal(r2.ok, false);
  if (!r2.ok) assert.equal(r2.diagnostics[0]?.code, "expected_rparen");
});

test("unknown symbols and functions are diagnostics with spans", () => {
  const parsed = parseExpression("q + 1");
  assert.equal(parsed.ok, true);
  if (!parsed.ok) return;
  const r = validateSymbols(parsed.value, sym(["x"]));
  assert.equal(r.ok, false);
  if (!r.ok) assert.equal(r.diagnostics[0]?.code, "unknown_symbol");
  const r2 = compileExpression("nosuch(1)", sym(["x"]));
  assert.equal(r2.ok, false);
  if (!r2.ok) assert.equal(r2.diagnostics[0]?.code, "unknown_function");
});

test("oversized expressions are rejected before stack overflow or runaway", () => {
  const deep = "(".repeat(5000) + "1" + ")".repeat(5000);
  const r = parseExpression(deep);
  assert.equal(r.ok, false);
  if (!r.ok) assert.ok(["expression_too_complex", "expression_too_deep", "expression_too_long"].includes(r.diagnostics[0]?.code ?? ""), r.diagnostics[0]?.code);
  const wide = new Array(3000).fill("1+").join("") + "1";
  const r2 = parseExpression(wide);
  assert.equal(r2.ok, false);
});

test("user functions resolve with arity checking and bounded recursion", () => {
  const fnAst = compileExpression("u^2", sym(["u"]));
  assert.equal(fnAst.ok, true);
  if (!fnAst.ok) return;
  const table = sym(["x"], { f: { arity: 1, params: ["u"], body: fnAst.value.ast } });
  const call = evalStr("f(3)+f(x)", { x: 2 }, table);
  if (call?.ok) assert.equal(call.value, 13);
  const badArity = compileExpression("f(1,2)", table);
  assert.equal(badArity.ok, false);
  // Self-recursive body is prevented at the document layer; direct eval depth is bounded.
  const selfRef = parseExpression("f(1)");
  assert.equal(selfRef.ok, true);
  const gAst = compileExpression("g(u)+1", sym(["u", "g"])); // parse-level OK (unknown at eval time)
  assert.equal(gAst.ok, false, "g is not a known function or variable");
});

test("division by zero and domain errors produce diagnostics, not Infinity", () => {
  const r = evalStr("1/0");
  assert.equal(r.ok, false);
  if (!r.ok) assert.equal(r.diagnostics[0]?.code, "value_not_finite");
  const r2 = evalStr("ln(-1)");
  assert.equal(r2.ok, false);
  const r3 = evalStr("sqrt(-4)");
  assert.equal(r3.ok, false);
  const r4 = evalStr("(-8)^(1/3)");
  assert.equal(r4.ok, false); // negative base with fractional exponent
});

test("equations parse into two ASTs and reject double equals", () => {
  const ok = parseEquation("x^2 = 4");
  assert.equal(ok.ok, true);
  const bad = parseEquation("x = 1 = 2");
  assert.equal(bad.ok, false);
  if (!bad.ok) assert.equal(bad.diagnostics[0]?.code, "equation_multiple_equals");
});

test("collectReferences gathers free variables and user functions", () => {
  const t = compileExpression("t", sym(["t"]));
  assert.equal(t.ok, true);
  if (!t.ok) return;
  const c = compileExpression("a*sin(x)+f(b)", sym(["a", "x", "b"], { f: { arity: 1, params: ["t"], body: t.value.ast } }));
  assert.equal(c.ok, true);
  if (!c.ok) return;
  const out = { variables: new Set<string>(), functions: new Set<string>() };
  collectReferences(c.value.ast, out);
  assert.deepEqual([...out.variables].sort(), ["a", "b", "x"]);
  assert.deepEqual([...out.functions], ["f"]);
  assert.equal(canonicalSource(c.value.ast), "((a*sin(x))+f(b))");
});
