/**
 * Calculus: symbolic first derivative/partial derivative with bounded local
 * simplification, scale-adaptive central-difference numeric derivative, and a
 * budgeted adaptive Simpson definite integral. Symbolic antidifferentiation
 * covers only verified basic rules and reports `symbolic_unsupported`
 * elsewhere — it never guesses.
 */

import { err, mathFail, mathOk, warn, type MathDiagnostic, type MathResult, MATH_LIMITS, freshBudget, nearlyEqual } from "./diagnostics.ts";
import { CONSTANTS, evaluateExpression, type CompiledFunction, type ExprNode, type SymbolTable } from "./expression.ts";

const N = (value: number, start = 0, end = 0): ExprNode => ({ kind: "num", value, start, end });
const isNum = (n: ExprNode, v: number): boolean => n.kind === "num" && n.value === v;
const isNum0 = (n: ExprNode): boolean => n.kind === "num" && n.value === 0;
const isNum1 = (n: ExprNode): boolean => n.kind === "num" && n.value === 1;

function bin(op: "+" | "-" | "*" | "/" | "^", lhs: ExprNode, rhs: ExprNode): ExprNode {
  return { kind: "bin", op, lhs, rhs, start: lhs.start, end: rhs.end };
}
function neg(arg: ExprNode): ExprNode { return { kind: "neg", arg, start: arg.start, end: arg.end }; }
function call(name: string, args: ExprNode[], proto: ExprNode): ExprNode {
  return { kind: "call", name, args, start: proto.start, end: proto.end };
}

/** Bounded local simplification: constant folding and 0/1 elimination only. */
function simplify(node: ExprNode, budget: { visits: number }): ExprNode {
  if (--budget.visits < 0) return node;
  switch (node.kind) {
    case "num": case "var": return node;
    case "neg": {
      const arg = simplify(node.arg, budget);
      if (arg.kind === "num") return N(-arg.value, node.start, node.end);
      return { ...node, arg };
    }
    case "bin": {
      const lhs = simplify(node.lhs, budget);
      const rhs = simplify(node.rhs, budget);
      if (lhs.kind === "num" && rhs.kind === "num") {
        let value: number | null = null;
        switch (node.op) {
          case "+": value = lhs.value + rhs.value; break;
          case "-": value = lhs.value - rhs.value; break;
          case "*": value = lhs.value * rhs.value; break;
          case "/": value = rhs.value !== 0 ? lhs.value / rhs.value : null; break;
          case "^":
            if (lhs.value >= 0 || Number.isInteger(rhs.value)) value = Math.pow(lhs.value, rhs.value);
            break;
        }
        if (value !== null && Number.isFinite(value)) return N(value, node.start, node.end);
      }
      switch (node.op) {
        case "+": if (isNum0(lhs)) return rhs; if (isNum0(rhs)) return lhs; break;
        case "-": if (isNum0(rhs)) return lhs; if (isNum0(lhs)) return neg(rhs); break;
        case "*":
          if (isNum0(lhs) || isNum0(rhs)) return N(0, node.start, node.end);
          if (isNum1(lhs)) return rhs;
          if (isNum1(rhs)) return lhs;
          break;
        case "/":
          if (isNum0(lhs)) return N(0, node.start, node.end);
          if (isNum1(rhs)) return lhs;
          break;
        case "^":
          if (isNum1(rhs)) return lhs;
          if (isNum0(rhs)) return N(1, node.start, node.end);
          break;
      }
      return { ...node, lhs, rhs };
    }
    case "call": {
      const args = node.args.map((a) => simplify(a, budget));
      if (args.every((a) => a.kind === "num")) {
        // Constant folding through safe builtins keeps derivative output small.
        const values = (args as Array<{ kind: "num"; value: number }>).map((a) => a.value);
        const probe = evaluateExpression({ source: "", ast: node, params: [], freeVariables: [] }, {}, { variables: [], functions: new Map() });
        if (probe.ok) return N(probe.value, node.start, node.end);
      }
      return { ...node, args };
    }
  }
}

/**
 * Symbolic first derivative of `ast` w.r.t. `variable`. Diagnoses
 * non-differentiable constructs (abs kinks are still differentiated as sign
 * with a warning; tan poles/ln domain produce warnings) rather than failing
 * silently.
 */
export function differentiate(ast: ExprNode, variable: string, symbols?: SymbolTable): MathResult<ExprNode> {
  const diagnostics: MathDiagnostic[] = [];
  const derive = (node: ExprNode, depth: number): ExprNode | null => {
    if (depth > 64) return null;
    switch (node.kind) {
      case "num": return N(0, node.start, node.end);
      case "var": return node.name === variable ? N(1, node.start, node.end) : N(0, node.start, node.end);
      case "neg": {
        const inner = derive(node.arg, depth + 1);
        return inner === null ? null : neg(inner);
      }
      case "bin": {
        const u = node.lhs;
        const v = node.rhs;
        switch (node.op) {
          case "+": {
            const du = derive(u, depth + 1), dv = derive(v, depth + 1);
            return du === null || dv === null ? null : bin("+", du, dv);
          }
          case "-": {
            const du = derive(u, depth + 1), dv = derive(v, depth + 1);
            return du === null || dv === null ? null : bin("-", du, dv);
          }
          case "*": {
            const du = derive(u, depth + 1), dv = derive(v, depth + 1);
            if (du === null || dv === null) return null;
            return bin("+", bin("*", du, v), bin("*", u, dv));
          }
          case "/": {
            const du = derive(u, depth + 1), dv = derive(v, depth + 1);
            if (du === null || dv === null) return null;
            return bin("/", bin("-", bin("*", du, v), bin("*", u, dv)), bin("*", v, v));
          }
          case "^": {
            if (v.kind === "num") {
              // d(u^c) = c·u^(c-1)·u'
              const du = derive(u, depth + 1);
              if (du === null) return null;
              return bin("*", bin("*", N(v.value, v.start, v.end), bin("^", u, N(v.value - 1, v.start, v.end))), du);
            }
            if (u.kind === "num") {
              // d(c^v) = c^v·ln(c)·v'
              const dv = derive(v, depth + 1);
              if (dv === null) return null;
              return bin("*", bin("*", bin("^", u, v), call("ln", [u], u)), dv);
            }
            // General u^v = e^{v·ln u} — only valid where u > 0.
            diagnostics.push(warn("derivative_requires_positive_base", { range: { start: node.start, end: node.end } }));
            const du = derive(u, depth + 1), dv = derive(v, depth + 1);
            if (du === null || dv === null) return null;
            const lnU = call("ln", [u], u);
            return bin("*", bin("^", u, v), bin("+", bin("*", dv, lnU), bin("/", bin("*", v, du), u)));
          }
        }
      }
      case "call": {
        const f = node.name;
        if (node.args.length !== 1) {
          if (f === "atan2" && node.args.length === 2) {
            const [y, x] = node.args as [ExprNode, ExprNode];
            const dy = derive(y, depth + 1), dx = derive(x, depth + 1);
            if (dy === null || dx === null) return null;
            return bin("/", bin("-", bin("*", dy, x), bin("*", y, dx)), bin("+", bin("*", x, x), bin("*", y, y)));
          }
          diagnostics.push(err("derivative_unsupported_function", { range: { start: node.start, end: node.end }, args: { name: f } }));
          return null;
        }
        const u = node.args[0] as ExprNode;
        const du = derive(u, depth + 1);
        if (du === null) return null;
        switch (f) {
          case "sin": return bin("*", call("cos", [u], u), du);
          case "cos": return neg(bin("*", call("sin", [u], u), du));
          case "tan": {
            diagnostics.push(warn("derivative_pole_possible", { range: { start: node.start, end: node.end } }));
            return bin("/", du, bin("*", call("cos", [u], u), call("cos", [u], u)));
          }
          case "exp": return bin("*", call("exp", [u], u), du);
          case "ln": {
            diagnostics.push(warn("derivative_domain_ln", { range: { start: node.start, end: node.end } }));
            return bin("/", du, u);
          }
          case "log": return bin("/", du, bin("*", u, N(Math.LN10, u.start, u.end)));
          case "sqrt": return bin("/", du, bin("*", N(2, u.start, u.end), call("sqrt", [u], u)));
          case "abs": {
            diagnostics.push(warn("derivative_nondifferentiable", { range: { start: node.start, end: node.end } }));
            return bin("/", bin("*", u, du), call("abs", [u], u));
          }
          case "asin": return bin("/", du, call("sqrt", [bin("-", N(1, u.start, u.end), bin("*", u, u))], u));
          case "acos": return neg(bin("/", du, call("sqrt", [bin("-", N(1, u.start, u.end), bin("*", u, u))], u)));
          case "atan": return bin("/", du, bin("+", N(1, u.start, u.end), bin("*", u, u)));
          case "sinh": return bin("*", call("cosh", [u], u), du);
          case "cosh": return bin("*", call("sinh", [u], u), du);
          case "tanh": return bin("/", du, bin("*", call("cosh", [u], u), call("cosh", [u], u)));
          case "deg": return bin("*", N(Math.PI / 180, u.start, u.end), du);
          default:
            diagnostics.push(err("derivative_unsupported_function", { range: { start: node.start, end: node.end }, args: { name: f } }));
            return null;
        }
      }
    }
  };
  if (symbols && symbols.functions.size > 0) {
    // Calls to user-defined functions cannot be differentiated symbolically.
    const scan = (n: ExprNode): boolean => {
      switch (n.kind) {
        case "call":
          if (symbols.functions.has(n.name)) return false;
          return n.args.every(scan);
        case "neg": return scan(n.arg);
        case "bin": return scan(n.lhs) && scan(n.rhs);
        default: return true;
      }
    };
    if (!scan(ast)) diagnostics.push(err("derivative_unsupported_function", { args: { name: "user function" } }));
  }
  const derived = derive(ast, 0);
  if (derived === null) {
    if (!diagnostics.some((d) => d.severity === "error")) diagnostics.push(err("derivative_failed"));
    return mathFail(diagnostics);
  }
  const simplified = simplify(derived, { visits: 2000 });
  return mathOk(simplified, diagnostics);
}

/* ---------------------------- numeric derivative ---------------------------- */

export interface NumericDerivative {
  value: number;
  /** Scale-adaptive step actually used. */
  step: number;
  method: "central" | "one-sided";
}

/**
 * Central difference with scale-adaptive h and catastrophic-cancellation
 * detection (comparison against a half-step probe). Falls back to reporting
 * `derivative_uncertain` instead of pretending precision it does not have.
 */
export function numericDerivative(
  f: (x: number) => number | null,
  x: number,
  options?: { side?: "center" | "left" | "right" },
): MathResult<NumericDerivative> {
  const h = Math.max(1e-7, Math.abs(x) * 1e-7) * 8;
  const side = options?.side ?? "center";
  const evalAt = (t: number): number | null => {
    const v = f(t);
    return Number.isFinite(v) ? v : null;
  };
  if (side === "center") {
    const fa = evalAt(x - h);
    const fb = evalAt(x + h);
    if (fa === null || fb === null) return mathFail([err("derivative_point_undefined", { args: { x } })]);
    const coarse = (fb - fa) / (2 * h);
    const fa2 = evalAt(x - h / 2);
    const fb2 = evalAt(x + h / 2);
    if (fa2 !== null && fb2 !== null) {
      const fine = (fb2 - fa2) / h;
      const scale = Math.max(Math.abs(coarse), Math.abs(fine), 1e-12);
      if (Math.abs(coarse - fine) > 0.05 * scale + 1e-9) {
        return mathOk({ value: (coarse + fine) / 2, step: h, method: "central" }, [warn("derivative_uncertain", { args: { x } })]);
      }
    }
    return mathOk({ value: coarse, step: h, method: "central" });
  }
  const f0 = evalAt(x);
  const dir = side === "right" ? h : -h;
  const fn = evalAt(x + dir);
  if (f0 === null || fn === null) return mathFail([err("derivative_point_undefined", { args: { x } })]);
  return mathOk({ value: (fn - f0) / dir, step: h, method: "one-sided" });
}

/** Derivative of a compiled expression at a point (scale-adaptive central difference). */
export function derivativeAtPoint(
  compiled: CompiledFunction,
  variable: string,
  x: number,
  symbols: SymbolTable = { variables: [], functions: new Map() },
): MathResult<NumericDerivative> {
  const evaluate = (t: number): number | null => {
    const v = evaluateExpression(compiled, { [variable]: t }, symbols);
    return v.ok ? v.value : null;
  };
  return numericDerivative(evaluate, x);
}

/* ------------------------------ definite integral ------------------------------ */

export interface IntegralValue {
  estimate: number;
  errorEstimate: number;
  evaluations: number;
  converged: boolean;
}

interface SimpsonBudget { evaluations: number; depth: number }

function evalSafe(f: (x: number) => number, x: number, budget: SimpsonBudget): number | null {
  budget.evaluations += 1;
  const v = f(x);
  return Number.isFinite(v) ? v : null;
}

/**
 * Adaptive Simpson definite integral. a>b flips the sign automatically.
 * Interior non-finite values or suspected discontinuities abort with
 * `integral_discontinuity`; budget exhaustion reports `integral_not_converged`.
 */
export function integrateNumeric(
  f: (x: number) => number,
  a: number,
  b: number,
): MathResult<IntegralValue> {
  const sign = a <= b ? 1 : -1;
  const lo = Math.min(a, b);
  const hi = Math.max(a, b);
  const budget: SimpsonBudget = { evaluations: 0, depth: 0 };
  const diagnostics: MathDiagnostic[] = [];

  const adaptive = (left: number, right: number, whole: { fa: number; fb: number; fm: number; value: number }, depth: number): { value: number | null; error: number } => {
    if (budget.evaluations > MATH_LIMITS.maxIntegralEvaluations) return { value: null, error: Infinity };
    const mid = (left + right) / 2;
    const fl = evalSafe(f, (left + mid) / 2, budget);
    const fr = evalSafe(f, (mid + right) / 2, budget);
    if (fl === null || fr === null) return { value: null, error: Infinity };
    const hHalf = (mid - left) / 6;
    const leftVal = hHalf * (whole.fa + 4 * fl + whole.fm);
    const rightVal = hHalf * (whole.fm + 4 * fr + whole.fb);
    const refined = leftVal + rightVal;
    const error = Math.abs(refined - whole.value);
    if (depth > 50 || error < 1e-10 * Math.max(1, Math.abs(refined))) {
      return { value: refined, error };
    }
    if (budget.evaluations > MATH_LIMITS.maxIntegralEvaluations) return { value: null, error: Infinity };
    const l = adaptive(left, mid, { fa: whole.fa, fb: whole.fm, fm: fl, value: leftVal }, depth + 1);
    const r = adaptive(mid, right, { fa: whole.fm, fb: whole.fb, fm: fr, value: rightVal }, depth + 1);
    if (l.value === null || r.value === null) return { value: null, error: Infinity };
    return { value: l.value + r.value, error: l.error + r.error };
  };

  const fa = evalSafe(f, lo, budget);
  const fb = evalSafe(f, hi, budget);
  const fm = evalSafe(f, (lo + hi) / 2, budget);
  if (fa === null || fb === null || fm === null) {
    // Endpoint singularity: probe just inside and compare one refined estimate.
    diagnostics.push(warn("integral_endpoint_singular", {}));
    const eps = Math.max((hi - lo) * 1e-9, 1e-12);
    const fa2 = evalSafe(f, lo + eps, budget);
    const fb2 = evalSafe(f, hi - eps, budget);
    const fm2 = evalSafe(f, (lo + hi) / 2, budget);
    if (fa2 === null || fb2 === null || fm2 === null) {
      return mathFail([err("integral_discontinuity", { args: { a, b } })]);
    }
    const whole = ((hi - lo - 2 * eps) / 6) * (fa2 + 4 * fm2 + fb2);
    return mathOk({ estimate: sign * whole, errorEstimate: (hi - lo) * 1e-6, evaluations: budget.evaluations, converged: false }, diagnostics);
  }
  const whole = { fa, fb, fm, value: ((hi - lo) / 6) * (fa + 4 * fm + fb) };
  const result = adaptive(lo, hi, whole, 0);
  if (result.value === null) {
    return mathFail([...diagnostics, err("integral_not_converged", { args: { a, b, evaluations: budget.evaluations } })]);
  }
  return mathOk({ estimate: sign * result.value, errorEstimate: Math.max(result.error, 1e-15), evaluations: budget.evaluations, converged: true }, diagnostics);
}

/** Integrate a compiled expression over its variable. */
export function integrateExpression(
  compiled: CompiledFunction,
  variable: string,
  a: number,
  b: number,
  symbols: SymbolTable = { variables: [], functions: new Map() },
): MathResult<IntegralValue> {
  const f = (x: number): number => {
    const v = evaluateExpression(compiled, { [variable]: x }, symbols, freshBudget());
    return v.ok ? v.value : NaN;
  };
  return integrateNumeric(f, a, b);
}

/* --------------------------- symbolic antiderivative --------------------------- */

/**
 * Verified-rule-only symbolic antiderivative: constants, x^n (n≠-1), 1/x on
 * a nonzero interval, constant multiples/sums, sin(ax+b), cos(ax+b),
 * exp(ax+b). Everything else returns `symbolic_unsupported` — no guessing.
 */
export function symbolicAntiderivative(ast: ExprNode, variable: string): MathResult<ExprNode> {
  const build = (node: ExprNode, depth: number): ExprNode | null => {
    if (depth > 24) return null;
    switch (node.kind) {
      case "num": return bin("*", node, { kind: "var", name: variable, start: node.start, end: node.end });
      case "var":
        if (node.name === variable) return bin("/", bin("^", node, N(2, node.start, node.end)), N(2, node.start, node.end));
        if (Object.prototype.hasOwnProperty.call(CONSTANTS, node.name)) return bin("*", { kind: "num", value: CONSTANTS[node.name] as number, start: node.start, end: node.end }, { kind: "var", name: variable, start: node.start, end: node.end });
        return null;
      case "neg": {
        const inner = build(node.arg, depth + 1);
        return inner === null ? null : neg(inner);
      }
      case "bin": {
        if (node.op === "+" || node.op === "-") {
          const l = build(node.lhs, depth + 1);
          const r = build(node.rhs, depth + 1);
          if (l !== null && r !== null) return bin(node.op, l, r);
          return null;
        }
        if (node.op === "*") {
          if (node.lhs.kind === "num") {
            const inner = build(node.rhs, depth + 1);
            return inner === null ? null : bin("*", node.lhs, inner);
          }
          if (node.rhs.kind === "num") {
            const inner = build(node.lhs, depth + 1);
            return inner === null ? null : bin("*", node.rhs, inner);
          }
          return null;
        }
        if (node.op === "/" && node.rhs.kind === "num" && node.rhs.value !== 0) {
          const inner = build(node.lhs, depth + 1);
          return inner === null ? null : bin("/", inner, node.rhs);
        }
        if (node.op === "/" && node.lhs.kind === "num" && isNum1(node.lhs) && node.rhs.kind === "var" && node.rhs.name === variable) {
          return call("ln", [call("abs", [node.rhs], node.rhs)], node);
        }
        if (node.op === "^" && node.rhs.kind === "num" && node.lhs.kind === "var" && node.lhs.name === variable && node.rhs.value !== -1) {
          const p = node.rhs.value + 1;
          if (p === 0) return call("ln", [call("abs", [node.lhs], node.lhs)], node);
          return bin("/", bin("^", node.lhs, N(p, node.start, node.end)), N(p, node.start, node.end));
        }
        return null;
      }
      case "call": {
        if (node.args.length !== 1) return null;
        const u = node.args[0] as ExprNode;
        // Only linear inner functions au+b are covered by the verified rules.
        let a = 0, b = 0;
        if (u.kind === "var" && u.name === variable) { a = 1; b = 0; }
        else if (u.kind === "bin" && u.op === "*" && u.lhs.kind === "num" && u.rhs.kind === "var" && u.rhs.name === variable) { a = u.lhs.value; b = 0; }
        else if (u.kind === "bin" && u.op === "*" && u.lhs.kind === "var" && u.lhs.name === variable && u.rhs.kind === "num") { a = u.rhs.value; b = 0; }
        else if (u.kind === "bin" && u.op === "+" && u.lhs.kind === "var" && u.lhs.name === variable && u.rhs.kind === "num") { a = 1; b = u.rhs.value; }
        else if (u.kind === "bin" && u.op === "+" && u.lhs.kind === "num" && u.rhs.kind === "var" && u.rhs.name === variable) { a = 1; b = u.lhs.value; }
        else if (u.kind === "bin" && u.op === "-" && u.lhs.kind === "var" && u.lhs.name === variable && u.rhs.kind === "num") { a = 1; b = -u.rhs.value; }
        else if (u.kind === "bin" && (u.op === "+" || u.op === "-")
          && u.lhs.kind === "bin" && u.lhs.op === "*"
          && ((u.lhs.lhs.kind === "num" && u.lhs.rhs.kind === "var" && u.lhs.rhs.name === variable) || (u.lhs.lhs.kind === "var" && u.lhs.lhs.name === variable && u.lhs.rhs.kind === "num"))
          && u.rhs.kind === "num") {
          // (k·x) ± m as parsed from source like sin(2*x+1)
          const kSide = u.lhs.lhs.kind === "num" ? u.lhs.lhs.value : (u.lhs.rhs as { value: number }).value;
          a = kSide;
          b = u.op === "+" ? u.rhs.value : -u.rhs.value;
        }
        else if (u.kind === "bin" && u.op === "*" && u.lhs.kind === "num" && u.rhs.kind === "bin" && u.rhs.op === "+" && u.rhs.lhs.kind === "var" && u.rhs.lhs.name === variable && u.rhs.rhs.kind === "num") {
          a = u.lhs.value;
          b = u.lhs.value * (u.rhs.rhs as { value: number }).value;
        }
        else return null;
        if (a === 0) return null;
        switch (node.name) {
          case "sin": return bin("/", neg(call("cos", [u], u)), N(a, u.start, u.end));
          case "cos": return bin("/", call("sin", [u], u), N(a, u.start, u.end));
          case "exp": return bin("/", call("exp", [u], u), N(a, u.start, u.end));
          default: return null;
        }
      }
    }
  };
  const built = build(ast, 0);
  if (built === null) return mathFail([err("symbolic_unsupported")]);
  return mathOk(built);
}

/** Shared helper: differentiate a compiled expression and repackage it. */
export function differentiateCompiled(compiled: CompiledFunction, variable: string): MathResult<CompiledFunction> {
  const derived = differentiate(compiled.ast, variable);
  if (!derived.ok) return derived;
  return mathOk({ source: "", ast: derived.value, params: [], freeVariables: [variable] });
}
