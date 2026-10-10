"use client";

/**
 * Math tools panel (functions2d only, replaces the old flat calculator):
 * evaluate / derivative / definite integral / equation solving each get a
 * dedicated structured result card — KaTeX typeset expressions, root lists
 * with simple/double badges, convergence metadata — instead of one flat
 * text line. An existing plot can be picked as the expression source, and
 * roots are marked on the canvas from the structured data (never re-parsed
 * display text). The parent keeps this panel mounted (hidden) so switching
 * dock tabs preserves inputs and results.
 */

import { useMemo, useState } from "react";
import katex from "katex";
import { Button } from "@/components/ui/Button";
import { Field, INPUT_CLS } from "@/components/ui/Input";
import {
  buildEvaluationContext, compileExpression, evaluateExpression, newDefinitionId, parseEquation,
  type MathCommand, type MathWorkbenchDocument, type PlotDefinition2D,
} from "./workbench-types.ts";
import {
  canonicalSource, derivativeAtPoint, differentiate, integrateExpression, solveEquationInterval, tryExactPolynomial,
} from "@next-tutor/domain";
import { FormulaInput } from "./FormulaInput.tsx";
import { astToLatex } from "./latex.ts";
import { formatNumber, PALETTE, stripLhsPrefix } from "./panel-shared.ts";

export type AnalysisTool = "evaluate" | "derivative" | "integral" | "solve";

type RootKind = "simple" | "double";

type AnalysisResult =
  | { kind: "evaluate"; latexHtml: string | null; value: number | null }
  | { kind: "derivative"; latexHtml: string | null; value: number | null; numericOnly: boolean; source: string }
  | { kind: "integral"; latexHtml: string | null; estimate: number | null; errorEstimate: number | null; evaluations: number | null; converged: boolean }
  | { kind: "solve"; latexHtml: string | null; roots: number[]; kinds: RootKind[]; method: "exact" | "numeric" };

function katexHtml(latex: string | null): string | null {
  if (!latex) return null;
  try {
    return katex.renderToString(latex, { throwOnError: false, output: "html" });
  } catch {
    return null;
  }
}

export function AnalysisPanel({ tr, document, dispatch, readOnly, active = true }: {
  tr: (key: string) => string;
  document: MathWorkbenchDocument;
  dispatch: (command: MathCommand, label: string) => void;
  readOnly: boolean;
  /** False while the dock shows another tab: keep state, skip the DOM
   * (the shared geometry-formula-editor testid must stay unique). */
  active?: boolean;
}) {
  const [tool, setTool] = useState<AnalysisTool>("evaluate");
  const [sourceId, setSourceId] = useState("");
  const [expression, setExpression] = useState("x^2");
  const [at, setAt] = useState("1");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [diagnostics, setDiagnostics] = useState<string[]>([]);
  const ctx = useMemo(() => buildEvaluationContext(document), [document]);

  const explicitPlots = useMemo(
    () => document.plots2d.filter((plot): plot is Extract<PlotDefinition2D, { kind: "explicit" }> => plot.kind === "explicit"),
    [document.plots2d],
  );
  const sourcePlot = sourceId ? explicitPlots.find((plot) => plot.id === sourceId) : undefined;
  const needsInterval = tool === "integral" || tool === "solve";

  const selectTool = (next: AnalysisTool) => {
    setTool(next);
    // Prefill the interval the first time an interval tool opens: the
    // source plot's domain when one is selected, otherwise a wide default.
    if ((next === "integral" || next === "solve") && from.trim() === "" && to.trim() === "") {
      setFrom(String(sourcePlot?.xDomain?.min ?? -10));
      setTo(String(sourcePlot?.xDomain?.max ?? 10));
    }
  };

  const readInterval = (): { a: number; b: number } | null => {
    const a = Number(from.trim());
    const b = Number(to.trim());
    if (!Number.isFinite(a) || !Number.isFinite(b) || a >= b) {
      setError(tr("invalidDomain"));
      return null;
    }
    return { a, b };
  };

  const run = () => {
    setError(null);
    setResult(null);
    setDiagnostics([]);
    if (!ctx.ok) {
      setError(tr("invalidExpression"));
      setDiagnostics(ctx.diagnostics.map((d) => d.code));
      return;
    }
    const symbols = { variables: ["x", ...Object.keys(ctx.value.parameters)], functions: ctx.value.functions };

    if (tool === "solve") {
      const raw = sourcePlot ? `${sourcePlot.expression} = 0` : (expression.includes("=") ? expression : `(${stripLhsPrefix(expression, "explicit")}) = 0`);
      const equation = parseEquation(raw);
      if (!equation.ok) {
        setError(tr("invalidExpression"));
        setDiagnostics([equation.diagnostics[0]?.code ?? "invalid_expression"]);
        return;
      }
      const interval = readInterval();
      if (!interval) return;
      const lhs = compileExpression(canonicalSource(equation.value.lhs), symbols);
      const rhs = compileExpression(canonicalSource(equation.value.rhs), symbols);
      if (!lhs.ok || !rhs.ok) {
        setError(tr("invalidExpression"));
        setDiagnostics([(lhs.ok ? rhs : lhs).diagnostics[0]?.code ?? "invalid_expression"]);
        return;
      }
      const latex = `${astToLatex(equation.value.lhs) ?? ""} = ${astToLatex(equation.value.rhs) ?? ""}`;
      const exact = tryExactPolynomial(equation.value.lhs, equation.value.rhs, "x");
      if (exact.exact && exact.result.ok && exact.result.value.roots.length > 0) {
        setResult({ kind: "solve", latexHtml: katexHtml(latex), roots: exact.result.value.roots, kinds: exact.result.value.kinds, method: "exact" });
        if (exact.result.value.kinds.includes("double")) setDiagnostics(["root_tangential"]);
        return;
      }
      const scan = solveEquationInterval(lhs.value, rhs.value, "x", [interval.a, interval.b], symbols);
      if (scan.ok) {
        setResult({ kind: "solve", latexHtml: katexHtml(latex), roots: scan.value.roots, kinds: scan.value.kinds, method: "numeric" });
        setDiagnostics(scan.diagnostics.map((d) => d.code));
      } else {
        setError(tr("noResult"));
        setDiagnostics(scan.diagnostics.map((d) => d.code));
      }
      return;
    }

    const exprText = sourcePlot ? sourcePlot.expression : stripLhsPrefix(expression, "explicit");
    const compiled = compileExpression(exprText, symbols);
    if (!compiled.ok) {
      setError(tr("invalidExpression"));
      setDiagnostics([compiled.diagnostics[0]?.code ?? "invalid_expression"]);
      return;
    }

    if (tool === "evaluate") {
      const x = Number(at.trim());
      if (!Number.isFinite(x)) { setError(tr("invalidExpression")); return; }
      const value = evaluateExpression(compiled.value, { x }, symbols);
      setResult({ kind: "evaluate", latexHtml: katexHtml(astToLatex(compiled.value.ast)), value: value.ok ? value.value : null });
      if (!value.ok) setDiagnostics(value.diagnostics.map((d) => d.code));
      return;
    }

    if (tool === "derivative") {
      const x = Number(at.trim());
      if (!Number.isFinite(x)) { setError(tr("invalidExpression")); return; }
      const symbolic = differentiate(compiled.value.ast, "x");
      const diag: string[] = [];
      let latexHtml: string | null = null;
      let value: number | null = null;
      let numericOnly = false;
      if (symbolic.ok) {
        const dLatex = astToLatex(symbolic.value);
        latexHtml = katexHtml(dLatex ? `f'(x) = ${dLatex}` : null);
        const atPoint = evaluateExpression(symbolic.value, { x }, symbols);
        if (atPoint.ok) value = atPoint.value;
      } else {
        diag.push(...symbolic.diagnostics.map((d) => d.code));
      }
      if (value === null) {
        // Symbolic failed or is undefined at the point: honest numeric value.
        const numeric = derivativeAtPoint(compiled.value, "x", x, symbols);
        if (numeric.ok) {
          value = numeric.value.value;
          numericOnly = true;
          diag.push(...numeric.diagnostics.map((d) => d.code));
        } else {
          setError(tr("noResult"));
          setDiagnostics([...diag, ...numeric.diagnostics.map((d) => d.code)]);
          return;
        }
      }
      setResult({ kind: "derivative", latexHtml, value, numericOnly, source: symbolic.ok ? canonicalSource(symbolic.value) : "" });
      setDiagnostics(diag);
      return;
    }

    // integral
    const interval = readInterval();
    if (!interval) return;
    const value = integrateExpression(compiled.value, "x", interval.a, interval.b, symbols);
    const exprLatex = astToLatex(compiled.value.ast);
    const latexHtml = katexHtml(exprLatex ? `\\int_{${formatNumber(interval.a)}}^{${formatNumber(interval.b)}} ${exprLatex} \\, dx` : null);
    if (value.ok) {
      setResult({ kind: "integral", latexHtml, estimate: value.value.estimate, errorEstimate: value.value.errorEstimate, evaluations: value.value.evaluations, converged: value.value.converged });
      setDiagnostics(value.diagnostics.map((d) => d.code));
    } else {
      setError(tr("noResult"));
      setDiagnostics(value.diagnostics.map((d) => d.code));
    }
  };

  const drawDerivative = () => {
    if (readOnly || !result || result.kind !== "derivative" || !result.source) return;
    const used = new Set(document.plots2d.map((plot) => plot.style.color));
    const color = PALETTE.find((c) => !used.has(c)) ?? PALETTE[document.plots2d.length % PALETTE.length] ?? "#2f7d6e";
    dispatch({
      kind: "addPlot2D",
      plot: {
        kind: "explicit", id: newDefinitionId("plot"), label: `f'${document.plots2d.length + 1}`, visible: true,
        style: { color, width: 2, opacity: 1, dashed: true },
        expression: result.source,
      },
    }, "plot-derivative");
  };

  const markRoots = (roots: number[]) => {
    if (readOnly) return;
    const commands: MathCommand[] = roots.map((x) => ({
      kind: "add2D",
      object: {
        kind2d: "point", id: newDefinitionId("pt"),
        label: formatNumber(x), visible: true, locked: false,
        style: { color: "#b0563c", width: 2, opacity: 1, dashed: false },
        construction: { kind: "free", x, y: 0 },
      },
    }));
    if (commands.length === 0) return;
    // One batch → one undo entry for the whole marking action.
    dispatch({ kind: "batch", commands }, "mark-roots");
  };

  const tools: Array<{ id: AnalysisTool; labelKey: string }> = [
    { id: "evaluate", labelKey: "calculatorEvaluate" },
    { id: "derivative", labelKey: "calculatorDerivative" },
    { id: "integral", labelKey: "calculatorIntegral" },
    { id: "solve", labelKey: "calculatorSolve" },
  ];
  const enterRun = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.nativeEvent.isComposing) return;
    if (event.key === "Enter") { event.preventDefault(); run(); }
  };

  return (
    <div className="panel-stack analysis-panel">
      <div className="analysis-tools" role="tablist" aria-label={tr("calculatorTab")}>
        {tools.map((entry) => (
          <button
            key={entry.id}
            type="button"
            role="tab"
            aria-selected={tool === entry.id}
            data-testid={`geometry-analysis-${entry.id}`}
            className={`analysis-tool ${tool === entry.id ? "is-active" : ""}`}
            onClick={() => selectTool(entry.id)}
          >{tr(entry.labelKey)}</button>
        ))}
      </div>

      {explicitPlots.length > 0 && (
        <label className="analysis-source">
          <span className="detail-label">{tr("analysisSource")}</span>
          <select
            value={sourceId}
            aria-label={tr("analysisSource")}
            onChange={(event) => setSourceId(event.target.value)}
          >
            <option value="">{tr("analysisManual")}</option>
            {explicitPlots.map((plot) => (
              <option key={plot.id} value={plot.id}>{plot.label ? `${plot.label}：y = ` : "y = "}{plot.expression}</option>
            ))}
          </select>
        </label>
      )}

      {active && !sourceId && (
        <FormulaInput
          value={expression}
          placeholder={tool === "solve" ? "x^2 - 4 = 0" : "x^2"}
          previewVariables={["x"]}
          ctx={ctx.ok ? ctx.value : null}
          disabled={readOnly}
          testid="geometry-formula-editor"
          onChange={setExpression}
          onCommit={run}
          commitOnBlur={false}
        />
      )}

      {(tool === "evaluate" || tool === "derivative") && (
        <Field label={tr("calculatorAt")}>
          <input className={INPUT_CLS} value={at} disabled={readOnly} inputMode="decimal" onChange={(event) => setAt(event.target.value)} onKeyDown={enterRun} />
        </Field>
      )}
      {needsInterval && (
        <div className="analysis-interval">
          <Field label={tr("calculatorFrom")}>
            <input className={INPUT_CLS} value={from} disabled={readOnly} inputMode="decimal" onChange={(event) => setFrom(event.target.value)} onKeyDown={enterRun} />
          </Field>
          <Field label={tr("calculatorTo")}>
            <input className={INPUT_CLS} value={to} disabled={readOnly} inputMode="decimal" onChange={(event) => setTo(event.target.value)} onKeyDown={enterRun} />
          </Field>
        </div>
      )}

      <span data-testid="geometry-calc-run" className="analysis-run-row">
        <Button variant="primary" size="sm" onClick={run} disabled={readOnly}>{tr("calculatorRun")}</Button>
      </span>

      {error && <div className="plot-error" role="alert">{error}</div>}

      {result && (
        <div className="analysis-card" data-testid="geometry-analysis-result">
          {result.kind === "evaluate" && (
            <>
              {result.latexHtml && <div className="analysis-latex" aria-hidden dangerouslySetInnerHTML={{ __html: result.latexHtml }} />}
              <output className="analysis-value">{result.value === null ? tr("noResult") : formatNumber(result.value)}</output>
            </>
          )}
          {result.kind === "derivative" && (
            <>
              {result.latexHtml
                ? <div className="analysis-latex" aria-hidden dangerouslySetInnerHTML={{ __html: result.latexHtml }} />
                : <p className="panel-hint">{tr("symbolicUnsupported")}</p>}
              {result.value !== null && (
                <output className="analysis-value analysis-value-sm">
                  f′({at.trim()}) {result.numericOnly ? "≈" : "="} {formatNumber(result.value)}
                </output>
              )}
              {result.source && !readOnly && (
                <Button variant="outline" size="sm" onClick={drawDerivative}>{tr("drawDerivative")}</Button>
              )}
            </>
          )}
          {result.kind === "integral" && (
            <>
              {result.latexHtml && <div className="analysis-latex" aria-hidden dangerouslySetInnerHTML={{ __html: result.latexHtml }} />}
              <output className="analysis-value">
                {result.estimate === null
                  ? tr("noResult")
                  : `${formatNumber(result.estimate)} ± ${formatNumber(result.errorEstimate ?? 0)}`}
              </output>
              <div className="analysis-badges">
                <span className={`analysis-badge ${result.converged ? "is-ok" : "is-warn"}`}>
                  {result.converged ? tr("integralConverged") : tr("integralNotConverged")}
                </span>
                {result.evaluations !== null && (
                  <span className="analysis-badge">{tr("integralEvaluations")} {result.evaluations}</span>
                )}
              </div>
            </>
          )}
          {result.kind === "solve" && (
            <>
              {result.latexHtml && <div className="analysis-latex" aria-hidden dangerouslySetInnerHTML={{ __html: result.latexHtml }} />}
              <div className="analysis-badges">
                <span className="analysis-badge">{result.method === "exact" ? tr("methodExact") : tr("methodNumeric")}</span>
                {result.roots.length > 0 && <span className="analysis-badge">{result.roots.length} {tr("rootsUnit")}</span>}
              </div>
              {result.roots.length === 0
                ? <p className="panel-hint">{tr("noRootsFound")}</p>
                : (
                  <ul className="analysis-roots">
                    {result.roots.map((x, index) => (
                      <li key={`${x}`} className="analysis-root" data-testid="geometry-analysis-root">
                        <span className="analysis-root-value">x = {formatNumber(x)}</span>
                        <span className={`analysis-badge ${result.kinds[index] === "double" ? "is-warn" : ""}`}>
                          {result.kinds[index] === "double" ? tr("rootDouble") : tr("rootSimple")}
                        </span>
                        {!readOnly && (
                          <button type="button" className="analysis-mark" onClick={() => markRoots([x])}>{tr("markSingle")}</button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              {result.roots.length > 0 && !readOnly && (
                <span data-testid="geometry-analysis-mark">
                  <Button variant="outline" size="sm" onClick={() => markRoots(result.roots)}>{tr("markToCanvas")}</Button>
                </span>
              )}
            </>
          )}
          {diagnostics.length > 0 && (
            <ul className="calc-diagnostics">
              {diagnostics.slice(0, 4).map((code, index) => <li key={index}>{code}</li>)}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
