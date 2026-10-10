/**
 * ExprNode → LaTeX serialization for the live formula preview (plan D5 /
 * KaTeX display lives in the web layer only — the domain never parses
 * LaTeX). `a / b` renders as a stacked fraction when both operands are
 * atomic so common schoolbook formulas read naturally.
 */

import type { ExprNode } from "./workbench-types.ts";

const GREEK = new Map<string, string>([["theta", "\\theta"], ["pi", "\\pi"]]);

function isAtomic(node: ExprNode): boolean {
  return node.kind === "num" || node.kind === "var";
}

function numberLatex(value: number): string {
  if (!Number.isFinite(value)) return "\\infty";
  if (Number.isInteger(value) && Math.abs(value) < 1e15) return String(value);
  return String(value);
}

function precedence(node: ExprNode): number {
  if (node.kind === "bin") {
    if (node.op === "+" || node.op === "-") return 1;
    if (node.op === "*" || node.op === "/") return 2;
    return 3; // ^
  }
  if (node.kind === "neg") return 2.5;
  return 4; // atoms and calls bind tightest
}

function parenIf(text: string, needs: boolean): string {
  return needs ? `\\left(${text}\\right)` : text;
}

function render(node: ExprNode, parentPrecedence: number): string {
  switch (node.kind) {
    case "num":
      return numberLatex(node.value);
    case "var":
      return GREEK.get(node.name) ?? node.name;
    case "neg": {
      const inner = render(node.arg, precedence(node));
      const needs = precedence(node.arg) < precedence(node);
      return parenIf(`-${parenIf(inner, needs)}`, parentPrecedence > precedence(node));
    }
    case "call": {
      const known = ["sin", "cos", "tan", "asin", "acos", "atan", "sinh", "cosh", "tanh", "exp", "ln", "log", "sqrt", "abs", "min", "max", "floor", "ceil", "round", "deg", "atan2"];
      const name = known.includes(node.name) ? `\\${node.name}` : `\\mathrm{${node.name}}`;
      const args = node.args.map((arg: ExprNode): string => render(arg, 0)).join(",\\; ");
      return `${name}\\left(${args}\\right)`;
    }
    case "bin": {
      const { op, lhs, rhs } = node;
      if (op === "/") {
        // Stacked fraction when both sides are simple; otherwise divide inline.
        if (isAtomic(lhs) && isAtomic(rhs)) {
          return `\\frac{${render(lhs, 0)}}{${render(rhs, 0)}}`;
        }
        const l = parenIf(render(lhs, 2), precedence(lhs) < 2);
        const r = parenIf(render(rhs, 2), precedence(rhs) < 2);
        return `${l} / ${r}`;
      }
      if (op === "^") {
        const baseNeeds = precedence(lhs) < 4;
        const base = parenIf(render(lhs, 4), baseNeeds);
        return `${base}^{${render(rhs, 0)}}`;
      }
      if (op === "*") {
        const l = render(lhs, precedence(node));
        const r = render(rhs, precedence(node) + 1);
        const lText = parenIf(l, precedence(lhs) < precedence(node));
        const rText = parenIf(r, precedence(rhs) < precedence(node));
        return `${lText} \\cdot ${rText}`;
      }
      // + and -
      const l = parenIf(render(lhs, precedence(node)), precedence(lhs) < precedence(node));
      const r = parenIf(render(rhs, precedence(node) + 1), precedence(rhs) < precedence(node) + 1);
      return `${l} ${op} ${r}`;
    }
    default: {
      const exhaustive: never = node;
      void exhaustive;
      throw new Error("unknown expression node");
    }
  }
}

/** Serialize a compiled AST to a KaTeX-renderable string; null when unsafe. */
export function astToLatex(ast: ExprNode): string | null {
  try {
    return render(ast, 0);
  } catch {
    return null;
  }
}
