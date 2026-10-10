/**
 * Math workbench expression engine: hand-written tokenizer, Pratt parser and
 * bounded evaluator. This is the ONLY path user math input ever takes — no
 * eval/newFunction/dynamic import, no member access, no strings, no
 * assignment. Grammar (first release requires explicit `*`):
 *
 *   expr    := unary (('+'|'-'|'*'|'/') unary)* | unary '^' expr
 *   unary   := ('-'|'+')* primary
 *   primary := number | constant | variable | name '(' args ')' | '(' expr ')'
 *
 * Aliases (π→pi, θ→theta) are normalized by the tokenizer only. Implicit
 * multiplication is NOT inferred from juxtaposition except through the
 * separate `normalizeJuxtaposition` helper which is only applied to
 * digit-followed-by-name/paren patterns, never `xy` (ambiguous).
 */

import { MATH_LIMITS, err, type EvalBudget, type MathDiagnostic, type MathResult, mathFail, mathOk, freshBudget } from "./diagnostics.ts";

export type ExprNode =
  | { kind: "num"; value: number; start: number; end: number }
  | { kind: "var"; name: string; start: number; end: number }
  | { kind: "neg"; arg: ExprNode; start: number; end: number }
  | { kind: "bin"; op: "+" | "-" | "*" | "/" | "^"; lhs: ExprNode; rhs: ExprNode; start: number; end: number }
  | { kind: "call"; name: string; args: readonly ExprNode[]; start: number; end: number };

export interface Span { start: number; end: number }

type Token =
  | { type: "num"; value: number; start: number; end: number }
  | { type: "name"; value: string; start: number; end: number }
  | { type: "op"; value: "+" | "-" | "*" | "/" | "^"; start: number; end: number }
  | { type: "lparen"; start: number; end: number }
  | { type: "rparen"; start: number; end: number }
  | { type: "comma"; start: number; end: number };

export const CONSTANTS: Readonly<Record<string, number>> = {
  pi: Math.PI,
  tau: Math.PI * 2,
  e: Math.E,
};

/** Trigonometry is radians everywhere; `deg` is an explicit conversion helper. */
interface BuiltinFn { arity: number | readonly number[]; fn: (...args: number[]) => number }

export const BUILTIN_FUNCTIONS: Readonly<Record<string, BuiltinFn>> = {
  sin: { arity: 1, fn: Math.sin },
  cos: { arity: 1, fn: Math.cos },
  tan: { arity: 1, fn: Math.tan },
  asin: { arity: 1, fn: Math.asin },
  acos: { arity: 1, fn: Math.acos },
  atan: { arity: 1, fn: Math.atan },
  atan2: { arity: 2, fn: Math.atan2 },
  sinh: { arity: 1, fn: Math.sinh },
  cosh: { arity: 1, fn: Math.cosh },
  tanh: { arity: 1, fn: Math.tanh },
  exp: { arity: 1, fn: Math.exp },
  ln: { arity: 1, fn: Math.log },
  log: { arity: 1, fn: Math.log10 },
  sqrt: { arity: 1, fn: Math.sqrt },
  abs: { arity: 1, fn: Math.abs },
  min: { arity: [2, 4], fn: Math.min },
  max: { arity: [2, 4], fn: Math.max },
  floor: { arity: 1, fn: Math.floor },
  ceil: { arity: 1, fn: Math.ceil },
  round: { arity: 1, fn: Math.round },
  deg: { arity: 1, fn: (x) => (x * Math.PI) / 180 },
};

export const BUILTIN_NAMES: readonly string[] = Object.keys(BUILTIN_FUNCTIONS);

/** Reserved single letters that user parameters must not shadow. */
export const RESERVED_VARIABLES: readonly string[] = ["x", "y", "z", "t", "u", "v", "r", "theta", "s", "w", "n", "k"];

export interface UserFunctionEntry {
  arity: number;
  params: readonly string[];
  body: ExprNode;
}

export interface SymbolTable {
  /** Free variables (parameters + plotting variable) the expression may use. */
  variables: readonly string[];
  /** Named user functions callable inside the expression. */
  functions: ReadonlyMap<string, UserFunctionEntry>;
}

export const EMPTY_SYMBOLS: SymbolTable = { variables: [], functions: new Map() };

export interface CompiledExpression {
  source: string;
  ast: ExprNode;
}

function span(start: number, end: number): Span { return { start, end }; }

export function normalizeMathInput(source: string): string {
  let out = "";
  for (const ch of source) {
    if (ch === "π") out += "pi";
    else if (ch === "θ" || ch === "ϑ") out += "theta";
    else if (ch === "×") out += "*";
    else if (ch === "÷") out += "/";
    else if (ch === "−") out += "-";
    else out += ch;
  }
  // `**` is accepted as an alias for `^` (single pass, no overlaps).
  let joined = out.replaceAll("**", "^");
  // Limited verifiable juxtaposition: a digit directly followed by a name or
  // '(' means multiplication (2x, 3sin(x), 2(x+1)). Name-name (xy) stays an
  // error so single-vs-product stays unambiguous.
  joined = joined.replace(/(\d)([A-Za-z(])/g, "$1*$2");
  return joined;
}

function tokenize(source: string): MathResult<Token[]> {
  const tokens: Token[] = [];
  let i = 0;
  const push = (t: Token) => {
    if (tokens.length >= MATH_LIMITS.maxTokens) throw new ParseError("expression_too_complex", i, i);
    tokens.push(t);
  };
  const isDigit = (c: string) => c >= "0" && c <= "9";
  const isNameStart = (c: string) => (c >= "a" && c <= "z") || (c >= "A" && c <= "Z") || c === "_";
  const isNamePart = (c: string) => isNameStart(c) || isDigit(c);
  while (i < source.length) {
    const c = source[i] as string;
    if (c === " " || c === "\t" || c === "\n" || c === "\r") { i++; continue; }
    if (isDigit(c) || (c === "." && isDigit(source[i + 1] ?? ""))) {
      const start = i;
      let sawDot = false;
      while (i < source.length) {
        const d = source[i] as string;
        if (isDigit(d)) { i++; continue; }
        if (d === "." && !sawDot) { sawDot = true; i++; continue; }
        break;
      }
      // Scientific notation only when e/E is directly followed by digits
      // (optionally signed); `2e` stays `2 * e` to be resolved by the parser.
      if ((source[i] === "e" || source[i] === "E")) {
        const j = i + 1;
        const sign = source[j] === "+" || source[j] === "-" ? 1 : 0;
        const digitStart = j + (sign ? 1 : 0);
        if (isDigit(source[digitStart] ?? "")) {
          i = digitStart;
          while (isDigit(source[i] ?? "")) i++;
        }
      }
      const text = source.slice(start, i);
      const value = Number(text);
      if (!Number.isFinite(value)) throw new ParseError("malformed_number", start, i);
      push({ type: "num", value, start, end: i });
      continue;
    }
    if (isNameStart(c)) {
      const start = i;
      i++;
      while (isNamePart(source[i] ?? "")) i++;
      push({ type: "name", value: source.slice(start, i), start, end: i });
      continue;
    }
    if (c === "+" || c === "-" || c === "*" || c === "/" || c === "^") {
      push({ type: "op", value: c, start: i, end: i + 1 });
      i++;
      continue;
    }
    if (c === "(") { push({ type: "lparen", start: i, end: i + 1 }); i++; continue; }
    if (c === ")") { push({ type: "rparen", start: i, end: i + 1 }); i++; continue; }
    if (c === ",") { push({ type: "comma", start: i, end: i + 1 }); i++; continue; }
    throw new ParseError("unexpected_character", i, i + 1);
  }
  return mathOk(tokens);
}

class ParseError extends Error {
  readonly code: string;
  readonly start: number;
  readonly end: number;
  constructor(code: string, start: number, end: number) {
    super(code);
    this.code = code;
    this.start = start;
    this.end = end;
  }
}

const PREC: Record<string, number> = { "+": 1, "-": 1, "*": 2, "/": 2, "^": 4 };
const UNARY_PREC = 3;

class Parser {
  private pos = 0;
  private nodes = 0;
  private depth = 0;
  private readonly tokens: readonly Token[];
  constructor(tokens: readonly Token[]) { this.tokens = tokens; }

  peek(): Token | undefined { return this.tokens[this.pos]; }
  private next(): Token | undefined { return this.tokens[this.pos++]; }

  private posTokStart(t: Token | undefined): number { return t ? t.start : (this.tokens[this.tokens.length - 1]?.end ?? 0); }
  private posTokEnd(t: Token | undefined): number { return t ? t.end : (this.tokens[this.tokens.length - 1]?.end ?? 0); }

  private enter() {
    this.depth++;
    if (this.depth > MATH_LIMITS.maxAstDepth) throw new ParseError("expression_too_deep", this.posTokStart(this.peek()), this.posTokEnd(this.peek()));
  }
  private exit() { this.depth--; }

  private make<T extends ExprNode>(node: T): T {
    this.nodes++;
    if (this.nodes > MATH_LIMITS.maxAstNodes) throw new ParseError("expression_too_complex", node.start, node.end);
    return node;
  }

  parseExpression(minPrec = 0): ExprNode {
    this.enter();
    try {
      let left = this.parseUnary();
      for (;;) {
        const t = this.peek();
        if (!t || t.type !== "op") break;
        const prec = PREC[t.value];
        if (prec === undefined || prec < minPrec) break;
        this.next();
        const rightMin = t.value === "^" ? prec : prec + 1; // ^ is right-associative
        const right = this.parseExpression(rightMin);
        const end = right.end;
        left = this.make({ kind: "bin", op: t.value, lhs: left, rhs: right, start: left.start, end });
      }
      return left;
    } finally { this.exit(); }
  }

  private parseUnary(): ExprNode {
    const t = this.peek();
    if (t && t.type === "op" && (t.value === "-" || t.value === "+")) {
      this.next();
      const arg = this.parseExpression(UNARY_PREC);
      if (t.value === "+") return arg;
      return this.make({ kind: "neg", arg, start: t.start, end: arg.end });
    }
    return this.parsePrimary();
  }

  private parsePrimary(): ExprNode {
    const t = this.next();
    if (!t) throw new ParseError("unexpected_end", this.posTokStart(undefined), this.posTokEnd(undefined));
    if (t.type === "num") return this.make({ kind: "num", value: t.value, start: t.start, end: t.end });
    if (t.type === "name") {
      const after = this.peek();
      if (after && after.type === "lparen") {
        this.next();
        const args: ExprNode[] = [];
        if (this.peek()?.type === "rparen") {
          const close = this.next() as Token;
          return this.make({ kind: "call", name: t.value, args, start: t.start, end: close.end });
        }
        for (;;) {
          args.push(this.parseExpression(0));
          if (args.length > MATH_LIMITS.maxCallArgs) throw new ParseError("too_many_arguments", t.start, t.end);
          const sep = this.peek();
          if (sep && sep.type === "comma") { this.next(); continue; }
          if (sep && sep.type === "rparen") { const close = this.next() as Token; return this.make({ kind: "call", name: t.value, args, start: t.start, end: close.end }); }
          throw new ParseError(sep ? "expected_comma_or_rparen" : "expected_rparen", this.posTokStart(sep), this.posTokEnd(sep));
        }
      }
      return this.make({ kind: "var", name: t.value, start: t.start, end: t.end });
    }
    if (t.type === "lparen") {
      const inner = this.parseExpression(0);
      const close = this.peek();
      if (!close || close.type !== "rparen") throw new ParseError("expected_rparen", this.posTokStart(close), this.posTokEnd(close));
      this.next();
      return { ...inner, start: t.start, end: close.end };
    }
    throw new ParseError("unexpected_token", t.start, t.end);
  }
}

/** Parse raw user text (after normalization) into a validated AST. */
export function parseExpression(source: string): MathResult<ExprNode> {
  if (source.length > MATH_LIMITS.maxExpressionChars) {
    return mathFail([err("expression_too_long", { range: span(0, source.length) })]);
  }
  try {
    const toks = tokenize(source);
    if (!toks.ok) return toks;
    if (toks.value.length === 0) return mathFail([err("empty_expression", { range: span(0, 0) })]);
    const parser = new Parser(toks.value);
    const ast = parser.parseExpression(0);
    const tail = parser.peek();
    // Expose the leftover position via a fresh parse error.
    if (tail) {
      const leftover = tail.start;
      return mathFail([err("unexpected_token", { range: span(leftover, source.length) })]);
    }
    return mathOk(ast);
  } catch (e) {
    if (e instanceof ParseError) return mathFail([err(e.code, { range: span(e.start, Math.max(e.end, e.start + 1)) })]);
    return mathFail([err("internal_error")]);
  }
}

/** Validate identifiers against the symbol table; returns sorted free variables. */
export function validateSymbols(ast: ExprNode, symbols: SymbolTable): MathResult<readonly string[]> {
  const free = new Set<string>();
  const walk = (node: ExprNode, depth: number): MathDiagnostic[] => {
    if (depth > MATH_LIMITS.maxAstDepth) return [err("expression_too_deep", { range: span(node.start, node.end) })];
    switch (node.kind) {
      case "num": return [];
      case "var": {
        if (Object.prototype.hasOwnProperty.call(CONSTANTS, node.name)) return [];
        if (symbols.variables.includes(node.name)) { free.add(node.name); return []; }
        return [err("unknown_symbol", { range: span(node.start, node.end), args: { name: node.name } })];
      }
      case "neg": return walk(node.arg, depth + 1);
      case "bin": return [...walk(node.lhs, depth + 1), ...walk(node.rhs, depth + 1)];
      case "call": {
        const builtin = BUILTIN_FUNCTIONS[node.name];
        if (builtin) {
          const arity = builtin.arity;
          const okArity = typeof arity === "number" ? node.args.length === arity : node.args.length >= (arity[0] as number) && node.args.length <= (arity[1] as number);
          if (!okArity) return [err("wrong_argument_count", { range: span(node.start, node.end), args: { name: node.name } })];
        } else {
          const user = symbols.functions.get(node.name);
          if (!user) return [err("unknown_function", { range: span(node.start, node.end), args: { name: node.name } })];
          if (node.args.length !== user.arity) return [err("wrong_argument_count", { range: span(node.start, node.end), args: { name: node.name } })];
        }
        const diags: MathDiagnostic[] = [];
        for (const arg of node.args) diags.push(...walk(arg, depth + 1));
        return diags;
      }
    }
  };
  const diags = walk(ast, 0);
  if (diags.some((d) => d.severity === "error")) return mathFail(diags);
  return mathOk([...free].sort(), diags);
}

export interface CompiledFunction {
  source: string;
  ast: ExprNode;
  params: readonly string[];
  freeVariables: readonly string[];
}

/** Full compile pipeline: normalize → parse → symbol-validate. */
export function compileExpression(source: string, symbols: SymbolTable): MathResult<CompiledFunction> {
  const normalized = normalizeMathInput(source.trim());
  if (normalized.length === 0) return mathFail([err("empty_expression")]);
  const parsed = parseExpression(normalized);
  if (!parsed.ok) return parsed;
  const checked = validateSymbols(parsed.value, symbols);
  if (!checked.ok) return checked;
  return mathOk({ source: normalized, ast: parsed.value, params: [], freeVariables: checked.value });
}

function applyBuiltin(name: string, args: readonly number[], start: number, end: number): MathResult<number> {
  const entry = BUILTIN_FUNCTIONS[name];
  if (!entry) return mathFail([err("unknown_function", { range: span(start, end), args: { name } })]);
  const value = entry.fn(...args);
  if (!Number.isFinite(value)) return mathFail([err("value_not_finite", { range: span(start, end) })]);
  return mathOk(value);
}

function evalNode(
  node: ExprNode,
  vars: Readonly<Record<string, number>>,
  symbols: SymbolTable,
  budget: EvalBudget,
  callDepth: number,
): MathResult<number> {
  if (--budget.nodeVisits < 0) return mathFail([err("evaluation_budget_exceeded", { range: span(node.start, node.end) })]);
  switch (node.kind) {
    case "num": return mathOk(node.value);
    case "var": {
      // hasOwnProperty guard: `CONSTANTS["constructor"]` would return the
      // inherited Object constructor, not a number.
      if (Object.prototype.hasOwnProperty.call(CONSTANTS, node.name)) return mathOk(CONSTANTS[node.name] as number);
      const value = vars[node.name];
      if (value === undefined) return mathFail([err("unbound_variable", { range: span(node.start, node.end), args: { name: node.name } })]);
      if (!Number.isFinite(value)) return mathFail([err("value_not_finite", { range: span(node.start, node.end), args: { name: node.name } })]);
      return mathOk(value);
    }
    case "neg": {
      const v = evalNode(node.arg, vars, symbols, budget, callDepth);
      if (!v.ok) return v;
      return mathOk(-v.value);
    }
    case "bin": {
      const l = evalNode(node.lhs, vars, symbols, budget, callDepth);
      if (!l.ok) return l;
      const r = evalNode(node.rhs, vars, symbols, budget, callDepth);
      if (!r.ok) return r;
      let value: number;
      switch (node.op) {
        case "+": value = l.value + r.value; break;
        case "-": value = l.value - r.value; break;
        case "*": value = l.value * r.value; break;
        case "/": value = l.value / r.value; break;
        case "^": {
          if (l.value < 0 && !Number.isInteger(r.value)) {
            return mathFail([err("domain_error", { range: span(node.start, node.end), args: { detail: "negative_base_fractional_exponent" } })]);
          }
          value = Math.pow(l.value, r.value);
          break;
        }
      }
      if (!Number.isFinite(value)) return mathFail([err("value_not_finite", { range: span(node.start, node.end) })]);
      return mathOk(value);
    }
    case "call": {
      const builtin = BUILTIN_FUNCTIONS[node.name];
      const args: number[] = [];
      for (const argNode of node.args) {
        const v = evalNode(argNode, vars, symbols, budget, callDepth);
        if (!v.ok) return v;
        args.push(v.value);
      }
      if (builtin) return applyBuiltin(node.name, args, node.start, node.end);
      const user = symbols.functions.get(node.name);
      if (!user) return mathFail([err("unknown_function", { range: span(node.start, node.end), args: { name: node.name } })]);
      if (callDepth + 1 > budget.callDepth) return mathFail([err("call_depth_exceeded", { range: span(node.start, node.end) })]);
      const bound: Record<string, number> = { ...vars };
      user.params.forEach((p, idx) => { bound[p] = args[idx] as number; });
      return evalNode(user.body, bound, symbols, budget, callDepth + 1);
    }
  }
}

/** Bounded evaluation against concrete variable values. */
export function evaluateExpression(
  compiled: CompiledExpression | CompiledFunction | ExprNode,
  vars: Readonly<Record<string, number>>,
  symbols: SymbolTable = EMPTY_SYMBOLS,
  budget?: EvalBudget,
): MathResult<number> {
  const ast = "ast" in compiled ? compiled.ast : compiled;
  return evalNode(ast, vars, symbols, budget ?? freshBudget(), 0);
}

/** Collect every variable/function name an AST references (for DAG edges). */
export function collectReferences(node: ExprNode, out: { variables: Set<string>; functions: Set<string> }): void {
  switch (node.kind) {
    case "num": return;
    case "var":
      if (!Object.prototype.hasOwnProperty.call(CONSTANTS, node.name)) out.variables.add(node.name);
      return;
    case "neg": collectReferences(node.arg, out); return;
    case "bin": collectReferences(node.lhs, out); collectReferences(node.rhs, out); return;
    case "call":
      if (!Object.prototype.hasOwnProperty.call(BUILTIN_FUNCTIONS, node.name)) out.functions.add(node.name);
      for (const a of node.args) collectReferences(a, out);
      return;
  }
}

/** Compact canonical source form used in cache keys and fingerprints. */
export function canonicalSource(node: ExprNode): string {
  switch (node.kind) {
    case "num": return numberKey(node.value);
    case "var": return node.name;
    case "neg": return `(-${canonicalSource(node.arg)})`;
    case "bin": return `(${canonicalSource(node.lhs)}${node.op}${canonicalSource(node.rhs)})`;
    case "call": return `${node.name}(${node.args.map(canonicalSource).join(",")})`;
  }
}

export function numberKey(v: number): string {
  if (Number.isInteger(v) && Math.abs(v) < 1e15) return String(v);
  return v.toPrecision(15);
}

/** Parse a restricted `left = right` equation into two ASTs (never JS assignment). */
export function parseEquation(source: string): MathResult<{ lhs: ExprNode; rhs: ExprNode }> {
  const eq = source.indexOf("=");
  if (eq < 0) return mathFail([err("equation_missing_equals", { range: span(0, source.length) })]);
  if (source.indexOf("=", eq + 1) >= 0) return mathFail([err("equation_multiple_equals", { range: span(eq, source.length) })]);
  const left = parseExpression(normalizeMathInput(source.slice(0, eq).trim()));
  if (!left.ok) return left;
  const right = parseExpression(normalizeMathInput(source.slice(eq + 1).trim()));
  if (!right.ok) return right;
  return mathOk({ lhs: left.value, rhs: right.value });
}
