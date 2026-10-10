"use client";

/**
 * Right dock. Each drawing mode owns its own tab set (per ADR-0023 UX
 * revision): functions2d = 函数 + 数学工具, geometry2d = 对象 + 属性,
 * functions3d = 函数 only — nothing useless travels across modes. Function
 * rows are single-line with a details drawer; "添加函数" creates draft rows
 * where an empty row is not an error. The math tools panel stays mounted
 * (hidden) so switching dock tabs preserves its inputs and results.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { DEMO_MODE, demoReadOnly } from "@/lib/demo";
import {
  axisVariables, buildEvaluationContext, compileExpression, newDefinitionId,
  type DrawingMode, type EvaluationContext, type MathCommand, type MathWorkbenchDocument,
  type PlotDefinition2D, type PlotDefinition3D,
} from "./workbench-types.ts";
import { WORKBENCH_PRESETS } from "@next-tutor/domain";
import type { RightDockTab, PendingPlotDraft } from "./document-state.ts";
import { FormulaInput } from "./FormulaInput.tsx";
import { AnalysisPanel } from "./AnalysisPanel.tsx";
import { InspectorPanel, ObjectsPanel } from "./GeometryInspector.tsx";
import { formatNumber, PALETTE, stripLhsPrefix, TWO_PI } from "./panel-shared.ts";

/** Which dock tabs each mode offers; order is the display order. */
const MODE_TABS: Record<DrawingMode, readonly RightDockTab[]> = {
  functions2d: ["functions", "calculator"],
  geometry2d: ["objects", "inspector"],
  functions3d: ["functions"],
};

const TAB_LABEL_KEYS: Record<RightDockTab, string> = {
  functions: "functionsTab",
  objects: "objectsTab",
  inspector: "inspectorTab",
  calculator: "calculatorTab",
};

export function WorkbenchPanels({
  tr, lang, document, drafts, pendingPlots, selection, tab, open, onToggle, onTab, onSelect, dispatch, setDraft, clearDraft, setPendingPlots, readOnly,
}: {
  tr: (key: string) => string;
  lang: string;
  document: MathWorkbenchDocument;
  drafts: Record<string, string>;
  pendingPlots: PendingPlotDraft[];
  selection: string | null;
  tab: RightDockTab;
  open: boolean;
  onToggle: () => void;
  onTab: (tab: RightDockTab) => void;
  onSelect: (id: string | null) => void;
  dispatch: (command: MathCommand, label: string) => void;
  setDraft: (key: string, text: string) => void;
  clearDraft: (key: string) => void;
  setPendingPlots: (updater: (rows: PendingPlotDraft[]) => PendingPlotDraft[]) => void;
  readOnly: boolean;
}) {
  const mode: DrawingMode = document.activeMode;
  const tabs = MODE_TABS[mode];
  // A tab left over from another mode falls back to the mode's first tab.
  const effectiveTab = tabs.includes(tab) ? tab : tabs[0];
  const tabLabel = (id: RightDockTab) => tr(TAB_LABEL_KEYS[id]);
  const analysisActive = mode === "functions2d" && effectiveTab === "calculator";
  if (!open) {
    return (
      <button className="right-panel-collapsed" onClick={onToggle} aria-label={tabLabel(effectiveTab)}>
        <span className="collapsed-glyph" aria-hidden>‹</span>
        <span className="collapsed-label" aria-hidden>{tabLabel(effectiveTab)}</span>
      </button>
    );
  }
  return (
    <aside className="right-panel" data-testid="geometry-right-panel" data-testid-panel={effectiveTab}>
      <div className="right-panel-head">
        {tabs.length > 1 ? (
          <div className="right-tabs" role="tablist">
            {tabs.map((id) => (
              <button
                key={id}
                role="tab"
                aria-selected={effectiveTab === id}
                className={`right-tab ${effectiveTab === id ? "is-active" : ""}`}
                onClick={() => onTab(id)}
              >{tabLabel(id)}</button>
            ))}
          </div>
        ) : (
          <div className="right-panel-title">{tabLabel(tabs[0] as RightDockTab)}</div>
        )}
        <button className="right-collapse" onClick={onToggle} aria-label={tr("cancel")}>›</button>
      </div>
      <div className="right-panel-scroll">
        {effectiveTab === "functions" && mode === "functions2d" && (
          <Functions2DPanel tr={tr} lang={lang} document={document} drafts={drafts} pendingPlots={pendingPlots} dispatch={dispatch} setDraft={setDraft} clearDraft={clearDraft} setPendingPlots={setPendingPlots} readOnly={readOnly} />
        )}
        {effectiveTab === "functions" && mode === "functions3d" && (
          <Functions3DPanel tr={tr} lang={lang} document={document} drafts={drafts} pendingPlots={pendingPlots} dispatch={dispatch} setDraft={setDraft} clearDraft={clearDraft} setPendingPlots={setPendingPlots} readOnly={readOnly} />
        )}
        {effectiveTab === "objects" && <ObjectsPanel tr={tr} document={document} selection={selection} onSelect={onSelect} dispatch={dispatch} readOnly={readOnly} />}
        {effectiveTab === "inspector" && <InspectorPanel tr={tr} document={document} selection={selection} dispatch={dispatch} readOnly={readOnly} />}
        {/* Kept mounted (hidden) so dock-tab switches preserve tool state. */}
        <div hidden={!analysisActive}>
          <AnalysisPanel tr={tr} document={document} dispatch={dispatch} readOnly={readOnly} active={analysisActive} />
        </div>
      </div>
    </aside>
  );
}

/* ------------------------------- input helpers ------------------------------- */

interface FieldSpec { field: string; prefix: string; placeholder: string }

const FIELDS_2D: Record<PlotDefinition2D["kind"], readonly FieldSpec[]> = {
  explicit: [{ field: "expression", prefix: "y =", placeholder: "sin(x)" }],
  inverse: [{ field: "expression", prefix: "x =", placeholder: "sin(y)" }],
  parametric: [
    { field: "xExpression", prefix: "x(t) =", placeholder: "cos(t)" },
    { field: "yExpression", prefix: "y(t) =", placeholder: "sin(t)" },
  ],
  polar: [{ field: "rExpression", prefix: "r(θ) =", placeholder: "theta" }],
  implicit: [{ field: "expression", prefix: "F(x, y) =", placeholder: "x^2 + y^2 - 4" }],
};

const FIELDS_3D: Record<PlotDefinition3D["kind"], readonly FieldSpec[]> = {
  explicitSurface: [{ field: "expression", prefix: "z =", placeholder: "sin(x) * cos(y)" }],
  parametricSurface: [
    { field: "xExpression", prefix: "x(u, v) =", placeholder: "cos(u) * (2 + cos(v))" },
    { field: "yExpression", prefix: "y(u, v) =", placeholder: "sin(u) * (2 + cos(v))" },
    { field: "zExpression", prefix: "z(u, v) =", placeholder: "sin(v)" },
  ],
  implicitSurface: [{ field: "expression", prefix: "F(x, y, z) =", placeholder: "x^2 + y^2 + z^2" }],
  parametricCurve: [
    { field: "xExpression", prefix: "x(t) =", placeholder: "cos(t)" },
    { field: "yExpression", prefix: "y(t) =", placeholder: "sin(t)" },
    { field: "zExpression", prefix: "z(t) =", placeholder: "t / 4" },
  ],
};

/** `F(x, y) = c` style input splits into an expression and a level value. */
function splitImplicitEquation(text: string): { expression: string; level: number } | null {
  const trimmed = text.trim();
  if (!trimmed.includes("=")) return null;
  const parts = trimmed.split("=");
  if (parts.length !== 2) return null;
  const lhs = (parts[0] ?? "").trim();
  const rhs = (parts[1] ?? "").trim();
  const level = Number(rhs);
  if (!lhs || !Number.isFinite(level)) return null;
  return { expression: lhs, level };
}

/** Compile and return the normalized source (π→pi, ×→* …), or null. */
function normalizeSource(text: string, variables: readonly string[], ctx: EvaluationContext | null): string | null {
  if (!ctx || text.trim().length === 0) return null;
  const compiled = compileExpression(text, { variables: [...variables, ...Object.keys(ctx.parameters)], functions: ctx.functions });
  return compiled.ok ? compiled.value.source : null;
}

function compileError(text: string, variables: readonly string[], ctx: EvaluationContext | null): string | null {
  if (!ctx || text.trim().length === 0) return null;
  const compiled = compileExpression(text, { variables: [...variables, ...Object.keys(ctx.parameters)], functions: ctx.functions });
  return compiled.ok ? null : (compiled.diagnostics[0]?.code ?? "invalid_expression");
}

function parseDomainNumber(text: string | undefined): number | null {
  if (text === undefined || text.trim().length === 0) return null;
  const value = Number(text.trim());
  return Number.isFinite(value) ? value : null;
}

function plotText(plot: PlotDefinition2D | PlotDefinition3D, field: string): string {
  return String((plot as unknown as Record<string, unknown>)[field] ?? "");
}

let draftKeyCounter = 0;
function newPendingRow(kind: string, colorIndex: number): PendingPlotDraft {
  draftKeyCounter += 1;
  return { key: `draft${draftKeyCounter.toString(36)}`, kind, color: PALETTE[colorIndex % PALETTE.length] ?? "#2f7d6e", expressions: {}, domains: {} };
}

/** Details-drawer toggle: a drawn chevron, not a text ellipsis. */
function DetailsChevron({ expanded }: { expanded: boolean }) {
  return (
    <svg viewBox="0 0 16 16" aria-hidden className="plot-more-glyph" style={expanded ? { transform: "rotate(90deg)" } : undefined}>
      <path d="M6 3.5 L10.5 8 L6 12.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/* ------------------------------- functions (2D) ------------------------------- */

function Functions2DPanel({ tr, lang, document, drafts, pendingPlots, dispatch, setDraft, clearDraft, setPendingPlots, readOnly }: {
  tr: (key: string) => string; lang: string; document: MathWorkbenchDocument; drafts: Record<string, string>; pendingPlots: PendingPlotDraft[];
  dispatch: (command: MathCommand, label: string) => void; setDraft: (key: string, text: string) => void; clearDraft: (key: string) => void;
  setPendingPlots: (updater: (rows: PendingPlotDraft[]) => PendingPlotDraft[]) => void; readOnly: boolean;
}) {
  void lang;
  const ctx = useMemo(() => buildEvaluationContext(document), [document]);
  const ctxValue = ctx.ok ? ctx.value : null;
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  // First open guarantees one empty draft row per fresh document (plan D5).
  const autoDraftDoc = useRef<string | null>(null);
  useEffect(() => {
    if (readOnly) return;
    if (autoDraftDoc.current === document.id) return;
    autoDraftDoc.current = document.id;
    if (document.plots2d.length === 0 && pendingPlots.length === 0) {
      setPendingPlots((rows) => (rows.length === 0 ? [newPendingRow("explicit", 0)] : rows));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [document.id, document.plots2d.length, readOnly]);

  const plots = document.plots2d;
  const typeLabel: Record<PlotDefinition2D["kind"], string> = {
    explicit: tr("expressionTypeExplicit"), inverse: tr("expressionTypeInverse"), parametric: tr("expressionTypeParametric"),
    polar: tr("expressionTypePolar"), implicit: tr("expressionTypeImplicit"),
  };
  const examples = ["sin(x)", "x^2", "1/x", "sqrt(x)"];

  return (
    <div className="panel-stack">
      {plots.length === 0 && pendingPlots.length === 0 && (
        <div className="panel-empty">
          <p className="panel-hint">{tr("functionsHint")}</p>
          <div className="example-chips" aria-label={tr("examples")}>
            {examples.map((example) => (
              <button
                key={example}
                type="button"
                className="example-chip"
                disabled={readOnly}
                onClick={() => setPendingPlots((rows) => [...rows, { ...newPendingRow("explicit", rows.length), expressions: { expression: example } }])}
              >{example}</button>
            ))}
          </div>
        </div>
      )}
      {plots.map((plot) => (
        <PlotRow2D
          key={plot.id}
          tr={tr}
          plot={plot}
          ctx={ctxValue}
          drafts={drafts}
          dispatch={dispatch}
          setDraft={setDraft}
          clearDraft={clearDraft}
          readOnly={readOnly}
          expanded={expanded[plot.id] ?? false}
          onToggleExpand={() => setExpanded((prev) => ({ ...prev, [plot.id]: !(prev[plot.id] ?? false) }))}
          typeLabel={typeLabel}
        />
      ))}
      {pendingPlots.map((row, index) => (
        <PendingRow2D
          key={row.key}
          tr={tr}
          row={row}
          index={index}
          document={document}
          ctx={ctxValue}
          dispatch={dispatch}
          setPendingPlots={setPendingPlots}
          readOnly={readOnly}
          typeLabel={typeLabel}
        />
      ))}
      <Button
        variant="outline"
        size="sm"
        className="add-function-btn"
        disabled={readOnly}
        onClick={() => { if (DEMO_MODE) { demoReadOnly(); return; }
          setPendingPlots((rows) => [...rows, newPendingRow("explicit", plots.length + rows.length)]);
        }}
      >+ {tr("addFunction")}</Button>
      <ParametersSection tr={tr} document={document} dispatch={dispatch} readOnly={readOnly} />
    </div>
  );
}

function PlotRow2D({ tr, plot, ctx, drafts, dispatch, setDraft, clearDraft, readOnly, expanded, onToggleExpand, typeLabel }: {
  tr: (key: string) => string;
  plot: PlotDefinition2D;
  ctx: EvaluationContext | null;
  drafts: Record<string, string>;
  dispatch: (command: MathCommand, label: string) => void;
  setDraft: (key: string, text: string) => void;
  clearDraft: (key: string) => void;
  readOnly: boolean;
  expanded: boolean;
  onToggleExpand: () => void;
  typeLabel: Record<PlotDefinition2D["kind"], string>;
}) {
  const fields = FIELDS_2D[plot.kind];
  const axes = axisVariables(plot);
  const firstError = fields.find((spec) => {
    const text = drafts[`${plot.id}:${spec.field}`] ?? plotText(plot, spec.field);
    return compileError(stripLhsPrefix(text, plot.kind), axes, ctx) !== null;
  });

  const commitField = (field: string, raw: string) => {
    const text = stripLhsPrefix(raw, plot.kind);
    const normalized = normalizeSource(text, axes, ctx);
    if (normalized === null) return; // invalid stays as a draft, never clobbers
    if (normalized !== plotText(plot, field)) {
      dispatch({ kind: "setPlot2D", id: plot.id, patch: { [field]: normalized } }, "edit-plot");
    }
    clearDraft(`${plot.id}:${field}`);
  };

  return (
    <div className={`plot-row ${firstError ? "has-error" : ""}`}>
      <div className="plot-row-main">
        <button
          className="plot-eye"
          aria-label={tr("showFunction")}
          aria-pressed={plot.visible}
          disabled={readOnly}
          onClick={() => dispatch({ kind: "setVisibility", target: { collection: "plots2d", id: plot.id }, visible: !plot.visible }, "toggle-visibility")}
        >{plot.visible ? "◉" : "○"}</button>
        <span className="plot-color-dot" aria-hidden style={{ background: plot.style.color }} />
        <div className="plot-fields">
          {fields.map((spec) => (
            <label key={spec.field} className="plot-field">
              <span className="plot-field-prefix">{spec.prefix}</span>
              <FormulaInput
                compact
                value={drafts[`${plot.id}:${spec.field}`] ?? plotText(plot, spec.field)}
                placeholder={spec.placeholder}
                invalid={firstError?.field === spec.field}
                disabled={readOnly}
                testid="geometry-formula-editor"
                ariaLabel={`${spec.prefix} ${plot.label}`}
                previewVariables={axes}
                ctx={ctx}
                onChange={(text) => setDraft(`${plot.id}:${spec.field}`, text)}
                onCommit={() => commitField(spec.field, drafts[`${plot.id}:${spec.field}`] ?? plotText(plot, spec.field))}
                onEscape={() => clearDraft(`${plot.id}:${spec.field}`)}
              />
            </label>
          ))}
        </div>
        <button className="plot-more" aria-label={tr("details")} aria-expanded={expanded} onClick={onToggleExpand}><DetailsChevron expanded={expanded} /></button>
        <button className="plot-delete" aria-label={tr("deleteSelected")} disabled={readOnly}
          onClick={() => dispatch({ kind: "removePlot2D", id: plot.id }, "remove-plot")}>×</button>
      </div>
      {firstError && (
        <div className="plot-error" role="alert">{tr("invalidExpression")} · {compileError(stripLhsPrefix(drafts[`${plot.id}:${firstError.field}`] ?? plotText(plot, firstError.field), plot.kind), axes, ctx)}</div>
      )}
      {expanded && (
        <div className="plot-details">
          <div className="detail-row">
            <span className="detail-label">{tr("plotType")}</span>
            <select
              value={plot.kind}
              disabled={readOnly}
              aria-label={tr("plotType")}
              onChange={(event) => {
                const kind = event.target.value as PlotDefinition2D["kind"];
                dispatch({ kind: "setPlot2D", id: plot.id, patch: retarget2D(plot, kind) }, "plot-kind");
              }}
            >
              {(Object.keys(FIELDS_2D) as Array<PlotDefinition2D["kind"]>).map((kind) => (
                <option key={kind} value={kind}>{typeLabel[kind]}</option>
              ))}
            </select>
          </div>
          <DomainEditor2D tr={tr} plot={plot} dispatch={dispatch} readOnly={readOnly} />
          <div className="detail-row">
            <span className="detail-label">{tr("color")}</span>
            <div className="palette-row">
              {PALETTE.map((color) => (
                <button key={color} type="button" className={`swatch ${plot.style.color === color ? "is-active" : ""}`} style={{ background: color }} aria-label={color} disabled={readOnly}
                  onClick={() => dispatch({ kind: "setStyle", target: { collection: "plots2d", id: plot.id }, patch: { color } }, "style")} />
              ))}
            </div>
          </div>
          <div className="detail-row">
            <span className="detail-label">{tr("lineWidth")}</span>
            <input type="range" min={0.5} max={6} step={0.5} value={plot.style.width} disabled={readOnly}
              aria-label={tr("lineWidth")}
              onChange={(event) => dispatch({ kind: "setStyle", target: { collection: "plots2d", id: plot.id }, patch: { width: Number(event.target.value) } }, "style")} />
            <span className="detail-value" aria-hidden>{plot.style.width}</span>
          </div>
        </div>
      )}
    </div>
  );
}

/** Neutral conversion seeds: only what the new kind strictly requires. */
function retarget2D(plot: PlotDefinition2D, kind: PlotDefinition2D["kind"]): Partial<PlotDefinition2D> {
  const single = "expression" in plot ? plot.expression : "rExpression" in plot ? plot.rExpression : plot.xExpression ?? "";
  switch (kind) {
    case "explicit": return { kind, expression: single } as Partial<PlotDefinition2D>;
    case "inverse": return { kind, expression: single } as Partial<PlotDefinition2D>;
    case "parametric": return { kind, xExpression: "t", yExpression: single || "t", tDomain: { min: 0, max: TWO_PI } } as Partial<PlotDefinition2D>;
    case "polar": return { kind, rExpression: single || "theta", thetaDomain: { min: 0, max: TWO_PI } } as Partial<PlotDefinition2D>;
    case "implicit": return { kind, expression: single || "x", level: 0, domain: { x: { min: -6, max: 6 }, y: { min: -6, max: 6 } } } as Partial<PlotDefinition2D>;
  }
}

function DomainInputs({ tr, label, value, fallback, optional, onCommit, disabled }: {
  tr: (key: string) => string;
  label: string;
  value: { min: number; max: number } | undefined;
  fallback: { min: number; max: number };
  optional: boolean;
  onCommit: (next: { min: number; max: number } | undefined) => void;
  disabled?: boolean;
}) {
  // Local text state resets through the parent's `key` when the committed
  // domain changes (commit / kind switch / undo) — no state-sync effect.
  const [minText, setMinText] = useState(value ? String(value.min) : "");
  const [maxText, setMaxText] = useState(value ? String(value.max) : "");
  const [invalid, setInvalid] = useState(false);
  const commit = () => {
    if (minText.trim().length === 0 && maxText.trim().length === 0) {
      setInvalid(false);
      if (!optional) onCommit(fallback);
      else onCommit(undefined);
      return;
    }
    const min = parseDomainNumber(minText) ?? fallback.min;
    const max = parseDomainNumber(maxText) ?? fallback.max;
    if (min === null || max === null || !(min < max)) { setInvalid(true); return; }
    setInvalid(false);
    onCommit({ min, max });
  };
  return (
    <div className={`domain-inputs ${invalid ? "has-error" : ""}`}>
      <span className="detail-label">{label}</span>
      <input className="domain-number" value={minText} disabled={disabled} inputMode="decimal"
        aria-label={`${label} ${tr("paramMin")}`} placeholder={optional ? tr("paramMin") : formatNumber(fallback.min)}
        onChange={(event) => setMinText(event.target.value)} onBlur={commit}
        onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }} />
      <span className="domain-sep" aria-hidden>–</span>
      <input className="domain-number" value={maxText} disabled={disabled} inputMode="decimal"
        aria-label={`${label} ${tr("paramMax")}`} placeholder={optional ? tr("paramMax") : formatNumber(fallback.max)}
        onChange={(event) => setMaxText(event.target.value)} onBlur={commit}
        onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }} />
      {invalid && <span className="plot-error" role="alert">{tr("invalidDomain")}</span>}
    </div>
  );
}

function DomainEditor2D({ tr, plot, dispatch, readOnly }: {
  tr: (key: string) => string;
  plot: PlotDefinition2D;
  dispatch: (command: MathCommand, label: string) => void;
  readOnly: boolean;
}) {
  const setPatch = (patch: Record<string, unknown>) => dispatch({ kind: "setPlot2D", id: plot.id, patch }, "plot-domain");
  if (plot.kind === "explicit") {
    return <DomainInputs key={`x-${plot.xDomain ? `${plot.xDomain.min},${plot.xDomain.max}` : "none"}`} tr={tr} label="x" optional value={plot.xDomain} fallback={{ min: -10, max: 10 }} disabled={readOnly} onCommit={(next) => setPatch({ xDomain: next })} />;
  }
  if (plot.kind === "inverse") {
    return <DomainInputs key={`y-${plot.yDomain ? `${plot.yDomain.min},${plot.yDomain.max}` : "none"}`} tr={tr} label="y" optional value={plot.yDomain} fallback={{ min: -10, max: 10 }} disabled={readOnly} onCommit={(next) => setPatch({ yDomain: next })} />;
  }
  if (plot.kind === "parametric") {
    return <DomainInputs key={`t-${plot.tDomain ? `${plot.tDomain.min},${plot.tDomain.max}` : "none"}`} tr={tr} label="t" optional={false} value={plot.tDomain} fallback={{ min: 0, max: TWO_PI }} disabled={readOnly} onCommit={(next) => setPatch({ tDomain: next ?? { min: 0, max: TWO_PI } })} />;
  }
  if (plot.kind === "polar") {
    return <DomainInputs key={`θ-${plot.thetaDomain ? `${plot.thetaDomain.min},${plot.thetaDomain.max}` : "none"}`} tr={tr} label="θ" optional={false} value={plot.thetaDomain} fallback={{ min: 0, max: TWO_PI }} disabled={readOnly} onCommit={(next) => setPatch({ thetaDomain: next ?? { min: 0, max: TWO_PI } })} />;
  }
  return (
    <div className="detail-group">
      <DomainInputs key={`x-${plot.domain.x ? `${plot.domain.x.min},${plot.domain.x.max}` : "none"}`} tr={tr} label="x" optional={false} value={plot.domain.x} fallback={{ min: -6, max: 6 }} disabled={readOnly}
        onCommit={(next) => setPatch({ domain: { ...plot.domain, x: next ?? { min: -6, max: 6 } } })} />
      <DomainInputs key={`y-${plot.domain.y ? `${plot.domain.y.min},${plot.domain.y.max}` : "none"}`} tr={tr} label="y" optional={false} value={plot.domain.y} fallback={{ min: -6, max: 6 }} disabled={readOnly}
        onCommit={(next) => setPatch({ domain: { ...plot.domain, y: next ?? { min: -6, max: 6 } } })} />
      <label className="detail-row">
        <span className="detail-label">{tr("levelLabel")}</span>
        <input className="domain-number" defaultValue={String(plot.level)} disabled={readOnly} inputMode="decimal"
          onBlur={(event) => { const value = Number(event.target.value); if (Number.isFinite(value) && value !== plot.level) setPatch({ level: value }); }} />
      </label>
    </div>
  );
}

function PendingRow2D({ tr, row, index, document, ctx, dispatch, setPendingPlots, readOnly, typeLabel }: {
  tr: (key: string) => string;
  row: PendingPlotDraft;
  index: number;
  document: MathWorkbenchDocument;
  ctx: EvaluationContext | null;
  dispatch: (command: MathCommand, label: string) => void;
  setPendingPlots: (updater: (rows: PendingPlotDraft[]) => PendingPlotDraft[]) => void;
  readOnly: boolean;
  typeLabel: Record<PlotDefinition2D["kind"], string>;
}) {
  const kind = row.kind as PlotDefinition2D["kind"];
  const fields = FIELDS_2D[kind];
  const axes = axisVariables({ kind });
  const [error, setError] = useState<string | null>(null);
  const update = (patch: Partial<PendingPlotDraft>) => setPendingPlots((rows) => rows.map((r) => (r.key === row.key ? { ...r, ...patch } : r)));

  const commit = () => {
    const texts = fields.map((spec) => stripLhsPrefix(row.expressions[spec.field] ?? "", kind));
    if (texts.every((text) => text.trim().length === 0)) return; // empty row is not an error
    for (let i = 0; i < fields.length; i++) {
      const code = compileError(texts[i] as string, axes, ctx);
      if (code) { setError(code); return; }
    }
    const normalized = texts.map((text) => normalizeSource(text, axes, ctx) ?? "");
    const id = newDefinitionId("plot");
    const base = { id, label: `f${document.plots2d.length + 1}`, visible: true, style: { color: row.color, width: 2, opacity: 1, dashed: false } };
    let plot: PlotDefinition2D;
    if (kind === "explicit" || kind === "inverse") {
      plot = { ...base, kind, expression: normalized[0] as string } as PlotDefinition2D;
    } else if (kind === "parametric") {
      plot = { ...base, kind, xExpression: normalized[0] as string, yExpression: normalized[1] as string, tDomain: { min: 0, max: TWO_PI } };
    } else if (kind === "polar") {
      plot = { ...base, kind, rExpression: normalized[0] as string, thetaDomain: { min: 0, max: TWO_PI } };
    } else {
      const raw = row.expressions.expression ?? "";
      const split = splitImplicitEquation(raw);
      plot = { ...base, kind: "implicit", expression: split ? split.expression : (normalized[0] as string), level: split ? split.level : 0, domain: { x: { min: -6, max: 6 }, y: { min: -6, max: 6 } } };
    }
    dispatch({ kind: "addPlot2D", plot }, "add-plot");
    setError(null);
    setPendingPlots((rows) => rows.filter((r) => r.key !== row.key));
  };

  return (
    <div className={`plot-row is-draft ${error ? "has-error" : ""}`} data-testid="geometry-pending-plot">
      <div className="plot-row-main">
        <select
          className="plot-kind-select"
          value={kind}
          disabled={readOnly}
          aria-label={tr("plotType")}
          onChange={(event) => update({ kind: event.target.value })}
        >
          {(Object.keys(FIELDS_2D) as Array<PlotDefinition2D["kind"]>).map((k) => (
            <option key={k} value={k}>{typeLabel[k]}</option>
          ))}
        </select>
        <div className="plot-fields">
          {fields.map((spec) => (
            <label key={spec.field} className="plot-field">
              <span className="plot-field-prefix">{spec.prefix}</span>
              <FormulaInput
                compact
                value={row.expressions[spec.field] ?? ""}
                placeholder={spec.placeholder}
                disabled={readOnly}
                testid="geometry-formula-editor"
                ariaLabel={`${spec.prefix} ${index + 1}`}
                previewVariables={axes}
                ctx={ctx}
                onChange={(text) => update({ expressions: { ...row.expressions, [spec.field]: text } })}
                onCommit={commit}
              />
            </label>
          ))}
        </div>
        <span className="pending-add">
          <Button variant="primary" size="sm" disabled={readOnly} onClick={commit}>{tr("addFunctionCommit")}</Button>
        </span>
        <button className="plot-delete" aria-label={tr("removeFunction")} disabled={readOnly}
          onClick={() => setPendingPlots((rows) => rows.filter((r) => r.key !== row.key))}>×</button>
      </div>
      {error && <div className="plot-error" role="alert">{tr("invalidExpression")} · {error}</div>}
    </div>
  );
}

/* ------------------------------- functions (3D) ------------------------------- */

function Functions3DPanel({ tr, lang, document, drafts, pendingPlots, dispatch, setDraft, clearDraft, setPendingPlots, readOnly }: {
  tr: (key: string) => string; lang: string; document: MathWorkbenchDocument; drafts: Record<string, string>; pendingPlots: PendingPlotDraft[];
  dispatch: (command: MathCommand, label: string) => void; setDraft: (key: string, text: string) => void; clearDraft: (key: string) => void;
  setPendingPlots: (updater: (rows: PendingPlotDraft[]) => PendingPlotDraft[]) => void; readOnly: boolean;
}) {
  const ctx = useMemo(() => buildEvaluationContext(document), [document]);
  const ctxValue = ctx.ok ? ctx.value : null;
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [template, setTemplate] = useState<PlotDefinition3D["kind"]>("explicitSurface");
  void lang;
  const templates: Array<{ id: PlotDefinition3D["kind"]; label: string }> = [
    { id: "explicitSurface", label: tr("surfaceExplicit") },
    { id: "parametricSurface", label: tr("surfaceParametric") },
    { id: "implicitSurface", label: tr("surfaceImplicit") },
    { id: "parametricCurve", label: tr("curve3d") },
  ];

  return (
    <div className="panel-stack">
      {document.plots3d.map((plot) => (
        <PlotRow3D
          key={plot.id}
          tr={tr}
          plot={plot}
          ctx={ctxValue}
          drafts={drafts}
          dispatch={dispatch}
          setDraft={setDraft}
          clearDraft={clearDraft}
          readOnly={readOnly}
          expanded={expanded[plot.id] ?? false}
          onToggleExpand={() => setExpanded((prev) => ({ ...prev, [plot.id]: !(prev[plot.id] ?? false) }))}
        />
      ))}
      {pendingPlots.map((row, index) => (
        <PendingRow3D
          key={row.key}
          tr={tr}
          row={row}
          index={index}
          document={document}
          ctx={ctxValue}
          dispatch={dispatch}
          setPendingPlots={setPendingPlots}
          readOnly={readOnly}
        />
      ))}
      <div className="add-function-row">
        <select value={template} disabled={readOnly} aria-label={tr("plotType")} onChange={(event) => setTemplate(event.target.value as PlotDefinition3D["kind"])}>
          {templates.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
        </select>
        <Button variant="outline" size="sm" disabled={readOnly} onClick={() => { if (DEMO_MODE) { demoReadOnly(); return; }
          setPendingPlots((rows) => [...rows, newPendingRow(template, document.plots3d.length + rows.length)]);
        }}>+ {tr("addFunction")}</Button>
      </div>
      {document.plots3d.length === 0 && pendingPlots.length === 0 && (
        <div className="preset-list" aria-label={tr("presets")}>
          {WORKBENCH_PRESETS.map((preset) => (
            <button
              key={preset.id}
              type="button"
              className="preset-item"
              disabled={readOnly}
              onClick={() => {
                const applied = preset.apply(document);
                applied.plots3d.forEach((plot) => dispatch({ kind: "addPlot3D", plot }, "preset"));
              }}
            >
              <span>{lang === "zh" ? preset.nameZh : preset.nameEn}</span>
              <span className="preset-desc">{lang === "zh" ? preset.descriptionZh : preset.descriptionEn}</span>
            </button>
          ))}
        </div>
      )}
      <ParametersSection tr={tr} document={document} dispatch={dispatch} readOnly={readOnly} />
    </div>
  );
}

function PlotRow3D({ tr, plot, ctx, drafts, dispatch, setDraft, clearDraft, readOnly, expanded, onToggleExpand }: {
  tr: (key: string) => string;
  plot: PlotDefinition3D;
  ctx: EvaluationContext | null;
  drafts: Record<string, string>;
  dispatch: (command: MathCommand, label: string) => void;
  setDraft: (key: string, text: string) => void;
  clearDraft: (key: string) => void;
  readOnly: boolean;
  expanded: boolean;
  onToggleExpand: () => void;
}) {
  const fields = FIELDS_3D[plot.kind];
  const axes = axisVariables(plot);
  const kindLabel = plot.kind === "explicitSurface" ? tr("surfaceExplicit")
    : plot.kind === "parametricSurface" ? tr("surfaceParametric")
      : plot.kind === "implicitSurface" ? tr("surfaceImplicit") : tr("curve3d");
  const firstError = fields.find((spec) => compileError(stripLhsPrefix(drafts[`${plot.id}:${spec.field}`] ?? plotText(plot, spec.field), plot.kind), axes, ctx) !== null);

  const commitField = (field: string, raw: string) => {
    const text = stripLhsPrefix(raw, plot.kind);
    const normalized = normalizeSource(text, axes, ctx);
    if (normalized === null) return;
    if (normalized !== plotText(plot, field)) {
      dispatch({ kind: "setPlot3D", id: plot.id, patch: { [field]: normalized } }, "edit-plot3d");
    }
    clearDraft(`${plot.id}:${field}`);
  };

  return (
    <div className={`plot-row ${firstError ? "has-error" : ""}`}>
      <div className="plot-row-main">
        <button
          className="plot-eye"
          aria-label={tr("showFunction")}
          aria-pressed={plot.visible}
          disabled={readOnly}
          onClick={() => dispatch({ kind: "setVisibility", target: { collection: "plots3d", id: plot.id }, visible: !plot.visible }, "toggle-visibility")}
        >{plot.visible ? "◉" : "○"}</button>
        <span className="plot-color-dot" aria-hidden style={{ background: plot.style.color }} />
        <div className="plot-fields">
          {fields.map((spec) => (
            <label key={spec.field} className="plot-field">
              <span className="plot-field-prefix">{spec.prefix}</span>
              <FormulaInput
                compact
                value={drafts[`${plot.id}:${spec.field}`] ?? plotText(plot, spec.field)}
                placeholder={spec.placeholder}
                invalid={firstError?.field === spec.field}
                disabled={readOnly}
                testid="geometry-formula-editor"
                ariaLabel={`${spec.prefix} ${plot.label}`}
                previewVariables={axes}
                ctx={ctx}
                onChange={(text) => setDraft(`${plot.id}:${spec.field}`, text)}
                onCommit={() => commitField(spec.field, drafts[`${plot.id}:${spec.field}`] ?? plotText(plot, spec.field))}
                onEscape={() => clearDraft(`${plot.id}:${spec.field}`)}
              />
            </label>
          ))}
        </div>
        <button className="plot-more" aria-label={tr("details")} aria-expanded={expanded} onClick={onToggleExpand}><DetailsChevron expanded={expanded} /></button>
        <button className="plot-delete" aria-label={tr("deleteSelected")} disabled={readOnly}
          onClick={() => dispatch({ kind: "removePlot3D", id: plot.id }, "remove-plot3d")}>×</button>
      </div>
      {firstError && (
        <div className="plot-error" role="alert">{tr("invalidExpression")} · {compileError(stripLhsPrefix(drafts[`${plot.id}:${firstError.field}`] ?? plotText(plot, firstError.field), plot.kind), axes, ctx)}</div>
      )}
      {expanded && (
        <div className="plot-details">
          <div className="detail-row">
            <span className="detail-label">{tr("plotType")}</span>
            <span className="inspector-type">{kindLabel}</span>
          </div>
          <DomainEditor3D tr={tr} plot={plot} dispatch={dispatch} readOnly={readOnly} />
          {"quality" in plot && (
            <div className="detail-row">
              <span className="detail-label">{tr("quality")}</span>
              <select value={plot.quality} disabled={readOnly} aria-label={tr("quality")}
                onChange={(event) => dispatch({ kind: "setPlot3D", id: plot.id, patch: { quality: event.target.value } }, "quality")}>
                <option value="low">{tr("low")}</option>
                <option value="normal">{tr("normal")}</option>
                <option value="high">{tr("high")}</option>
              </select>
            </div>
          )}
          {"opacity" in plot && (
            <label className="detail-row">
              <span className="detail-label">{tr("opacity")}</span>
              <input type="range" min={0.1} max={1} step={0.05} value={plot.opacity} disabled={readOnly}
                onChange={(event) => dispatch({ kind: "setPlot3D", id: plot.id, patch: { opacity: Number(event.target.value) } }, "opacity")} />
              <span className="detail-value" aria-hidden>{plot.opacity.toFixed(2)}</span>
            </label>
          )}
          {"showGrid" in plot && (
            <label className="detail-row">
              <span className="detail-label">{tr("showGrid")}</span>
              <input type="checkbox" checked={plot.showGrid} disabled={readOnly}
                onChange={(event) => dispatch({ kind: "setPlot3D", id: plot.id, patch: { showGrid: event.target.checked } }, "showGrid")} />
            </label>
          )}
          <div className="detail-row">
            <span className="detail-label">{tr("color")}</span>
            <div className="palette-row">
              {PALETTE.map((color) => (
                <button key={color} type="button" className={`swatch ${plot.style.color === color ? "is-active" : ""}`} style={{ background: color }} aria-label={color} disabled={readOnly}
                  onClick={() => dispatch({ kind: "setStyle", target: { collection: "plots3d", id: plot.id }, patch: { color } }, "style")} />
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function DomainEditor3D({ tr, plot, dispatch, readOnly }: {
  tr: (key: string) => string;
  plot: PlotDefinition3D;
  dispatch: (command: MathCommand, label: string) => void;
  readOnly: boolean;
}) {
  const setPatch = (patch: Record<string, unknown>) => dispatch({ kind: "setPlot3D", id: plot.id, patch }, "plot-domain");
  if (plot.kind === "explicitSurface") {
    return (
      <div className="detail-group">
        <DomainInputs key={`x-${plot.xDomain ? `${plot.xDomain.min},${plot.xDomain.max}` : "none"}`} tr={tr} label="x" optional={false} value={plot.xDomain} fallback={{ min: -5, max: 5 }} disabled={readOnly}
          onCommit={(next) => setPatch({ xDomain: next ?? { min: -5, max: 5 } })} />
        <DomainInputs key={`y-${plot.yDomain ? `${plot.yDomain.min},${plot.yDomain.max}` : "none"}`} tr={tr} label="y" optional={false} value={plot.yDomain} fallback={{ min: -5, max: 5 }} disabled={readOnly}
          onCommit={(next) => setPatch({ yDomain: next ?? { min: -5, max: 5 } })} />
      </div>
    );
  }
  if (plot.kind === "parametricSurface") {
    return (
      <div className="detail-group">
        <DomainInputs key={`u-${plot.uDomain ? `${plot.uDomain.min},${plot.uDomain.max}` : "none"}`} tr={tr} label="u" optional={false} value={plot.uDomain} fallback={{ min: 0, max: TWO_PI }} disabled={readOnly}
          onCommit={(next) => setPatch({ uDomain: next ?? { min: 0, max: TWO_PI } })} />
        <DomainInputs key={`v-${plot.vDomain ? `${plot.vDomain.min},${plot.vDomain.max}` : "none"}`} tr={tr} label="v" optional={false} value={plot.vDomain} fallback={{ min: 0, max: TWO_PI }} disabled={readOnly}
          onCommit={(next) => setPatch({ vDomain: next ?? { min: 0, max: TWO_PI } })} />
      </div>
    );
  }
  if (plot.kind === "implicitSurface") {
    return (
      <div className="detail-group">
        <DomainInputs key={`x-${plot.box.x ? `${plot.box.x.min},${plot.box.x.max}` : "none"}`} tr={tr} label="x" optional={false} value={plot.box.x} fallback={{ min: -1.5, max: 1.5 }} disabled={readOnly}
          onCommit={(next) => setPatch({ box: { ...plot.box, x: next ?? { min: -1.5, max: 1.5 } } })} />
        <DomainInputs key={`y-${plot.box.y ? `${plot.box.y.min},${plot.box.y.max}` : "none"}`} tr={tr} label="y" optional={false} value={plot.box.y} fallback={{ min: -1.5, max: 1.5 }} disabled={readOnly}
          onCommit={(next) => setPatch({ box: { ...plot.box, y: next ?? { min: -1.5, max: 1.5 } } })} />
        <DomainInputs key={`z-${plot.box.z ? `${plot.box.z.min},${plot.box.z.max}` : "none"}`} tr={tr} label="z" optional={false} value={plot.box.z} fallback={{ min: -1.5, max: 1.5 }} disabled={readOnly}
          onCommit={(next) => setPatch({ box: { ...plot.box, z: next ?? { min: -1.5, max: 1.5 } } })} />
        <label className="detail-row">
          <span className="detail-label">{tr("levelLabel")}</span>
          <input className="domain-number" defaultValue={String(plot.iso)} disabled={readOnly} inputMode="decimal"
            onBlur={(event) => { const value = Number(event.target.value); if (Number.isFinite(value) && value !== plot.iso) setPatch({ iso: value }); }} />
        </label>
        <label className="detail-row">
          <span className="detail-label">{tr("resolutionLabel")}</span>
          <input className="domain-number" defaultValue={String(plot.resolution)} disabled={readOnly} inputMode="numeric"
            onBlur={(event) => { const value = Number(event.target.value); if (Number.isInteger(value) && value >= 6 && value <= 48 && value !== plot.resolution) setPatch({ resolution: value }); }} />
        </label>
      </div>
    );
  }
  return (
    <div className="detail-group">
      <DomainInputs key={`t-${plot.tDomain ? `${plot.tDomain.min},${plot.tDomain.max}` : "none"}`} tr={tr} label="t" optional={false} value={plot.tDomain} fallback={{ min: 0, max: TWO_PI * 2 }} disabled={readOnly}
        onCommit={(next) => setPatch({ tDomain: next ?? { min: 0, max: TWO_PI * 2 } })} />
      <label className="detail-row">
        <span className="detail-label">{tr("samplesLabel")}</span>
        <input className="domain-number" defaultValue={String(plot.samples)} disabled={readOnly} inputMode="numeric"
          onBlur={(event) => { const value = Number(event.target.value); if (Number.isInteger(value) && value >= 16 && value !== plot.samples) setPatch({ samples: value }); }} />
      </label>
    </div>
  );
}

function PendingRow3D({ tr, row, index, document, ctx, dispatch, setPendingPlots, readOnly }: {
  tr: (key: string) => string;
  row: PendingPlotDraft;
  index: number;
  document: MathWorkbenchDocument;
  ctx: EvaluationContext | null;
  dispatch: (command: MathCommand, label: string) => void;
  setPendingPlots: (updater: (rows: PendingPlotDraft[]) => PendingPlotDraft[]) => void;
  readOnly: boolean;
}) {
  const kind = row.kind as PlotDefinition3D["kind"];
  const fields = FIELDS_3D[kind];
  const axes = axisVariables({ kind });
  const [error, setError] = useState<string | null>(null);
  const templates: Array<{ id: PlotDefinition3D["kind"]; label: string }> = [
    { id: "explicitSurface", label: tr("surfaceExplicit") },
    { id: "parametricSurface", label: tr("surfaceParametric") },
    { id: "implicitSurface", label: tr("surfaceImplicit") },
    { id: "parametricCurve", label: tr("curve3d") },
  ];
  const update = (patch: Partial<PendingPlotDraft>) => setPendingPlots((rows) => rows.map((r) => (r.key === row.key ? { ...r, ...patch } : r)));

  const commit = () => {
    const texts = fields.map((spec) => stripLhsPrefix(row.expressions[spec.field] ?? "", kind));
    if (texts.every((text) => text.trim().length === 0)) return;
    for (let i = 0; i < fields.length; i++) {
      const code = compileError(texts[i] as string, axes, ctx);
      if (code) { setError(code); return; }
    }
    const normalized = texts.map((text) => normalizeSource(text, axes, ctx) ?? "");
    const readPair = (minKey: string, maxKey: string, fallback: { min: number; max: number }): { min: number; max: number } => {
      const min = parseDomainNumber(row.domains[minKey]);
      const max = parseDomainNumber(row.domains[maxKey]);
      if (min !== null && max !== null && min < max) return { min, max };
      return fallback;
    };
    const id = newDefinitionId("plot");
    const base = { id, label: `s${document.plots3d.length + 1}`, visible: true, style: { color: row.color, width: 2, opacity: 1, dashed: false } };
    let plot: PlotDefinition3D;
    if (kind === "explicitSurface") {
      plot = { ...base, kind, expression: normalized[0] as string, xDomain: readPair("xMin", "xMax", { min: -5, max: 5 }), yDomain: readPair("yMin", "yMax", { min: -5, max: 5 }), quality: "normal", opacity: 1, showGrid: true };
    } else if (kind === "parametricSurface") {
      plot = { ...base, kind, xExpression: normalized[0] as string, yExpression: normalized[1] as string, zExpression: normalized[2] as string, uDomain: readPair("uMin", "uMax", { min: 0, max: TWO_PI }), vDomain: readPair("vMin", "vMax", { min: 0, max: TWO_PI }), wrapU: true, wrapV: true, quality: "normal", opacity: 1, showGrid: true };
    } else if (kind === "implicitSurface") {
      const raw = row.expressions.expression ?? "";
      const split = splitImplicitEquation(raw);
      plot = { ...base, kind, expression: split ? split.expression : (normalized[0] as string), iso: split ? split.level : (parseDomainNumber(row.domains.iso) ?? 1), box: { x: readPair("xMin", "xMax", { min: -1.5, max: 1.5 }), y: readPair("yMin", "yMax", { min: -1.5, max: 1.5 }), z: readPair("zMin", "zMax", { min: -1.5, max: 1.5 }) }, resolution: 24, opacity: 0.9 };
    } else {
      plot = { ...base, kind, xExpression: normalized[0] as string, yExpression: normalized[1] as string, zExpression: normalized[2] as string, tDomain: readPair("tMin", "tMax", { min: 0, max: TWO_PI * 2 }), samples: 400 };
    }
    dispatch({ kind: "addPlot3D", plot }, "add-plot3d");
    setError(null);
    setPendingPlots((rows) => rows.filter((r) => r.key !== row.key));
  };

  return (
    <div className={`plot-row is-draft ${error ? "has-error" : ""}`} data-testid="geometry-pending-plot">
      <div className="plot-row-main">
        <select
          className="plot-kind-select"
          value={kind}
          disabled={readOnly}
          aria-label={tr("plotType")}
          onChange={(event) => update({ kind: event.target.value })}
        >
          {templates.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
        </select>
        <div className="plot-fields">
          {fields.map((spec) => (
            <label key={spec.field} className="plot-field">
              <span className="plot-field-prefix">{spec.prefix}</span>
              <FormulaInput
                compact
                value={row.expressions[spec.field] ?? ""}
                placeholder={spec.placeholder}
                disabled={readOnly}
                testid="geometry-formula-editor"
                ariaLabel={`${spec.prefix} ${index + 1}`}
                previewVariables={axes}
                ctx={ctx}
                onChange={(text) => update({ expressions: { ...row.expressions, [spec.field]: text } })}
                onCommit={commit}
              />
            </label>
          ))}
        </div>
        <span className="pending-add">
          <Button variant="primary" size="sm" disabled={readOnly} onClick={commit}>{tr("addFunctionCommit")}</Button>
        </span>
        <button className="plot-delete" aria-label={tr("removeFunction")} disabled={readOnly}
          onClick={() => setPendingPlots((rows) => rows.filter((r) => r.key !== row.key))}>×</button>
      </div>
      {error && <div className="plot-error" role="alert">{tr("invalidExpression")} · {error}</div>}
    </div>
  );
}

/* -------------------------------- parameters -------------------------------- */

function ParametersSection({ tr, document, dispatch, readOnly }: {
  tr: (key: string) => string; document: MathWorkbenchDocument; dispatch: (command: MathCommand, label: string) => void; readOnly: boolean;
}) {
  return (
    <section className="parameters-section">
      <div className="section-title">{tr("parameters")}</div>
      {document.parameters.map((param) => (
        <div key={param.id} className="param-row">
          <span className="param-name">{param.name}</span>
          <input
            type="range" min={param.min} max={param.max} step={param.step} value={param.value}
            disabled={readOnly}
            aria-label={`${tr("paramName")} ${param.name}`}
            onChange={(event) => dispatch({ kind: "setParameter", parameter: { ...param, value: Number(event.target.value) } }, "param")}
          />
          <span className="param-value">{formatNumber(param.value)}</span>
          <button className="param-remove" aria-label={tr("removeFunction")} disabled={readOnly}
            onClick={() => dispatch({ kind: "removeParameter", id: param.id }, "remove-param")}>×</button>
        </div>
      ))}
      <Button variant="ghost" size="sm" disabled={readOnly} onClick={() => {
        const used = new Set(document.parameters.map((p) => p.name));
        let letter = "a";
        for (const candidate of "abcdefghkmnpqrstuvw") {
          if (!used.has(candidate)) { letter = candidate; break; }
        }
        dispatch({ kind: "setParameter", parameter: { id: newDefinitionId("par"), name: letter, value: 1, min: -5, max: 5, step: 0.1 } }, "add-param");
      }}>+ {tr("addParameter")}</Button>
    </section>
  );
}
