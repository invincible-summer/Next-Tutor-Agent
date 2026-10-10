"use client";

/**
 * Plane-geometry dock tabs (geometry2d only): an objects list with inline
 * label editing and resolved summaries (coordinates / length / radius), and
 * a properties inspector for the current selection — free-point coordinates
 * are editable, derived constructions show their inputs read-only, plus
 * style, lock and cascade delete. Both read truth from `resolveGeometry2D`.
 */

import { useMemo } from "react";
import { Button } from "@/components/ui/Button";
import { Field, INPUT_CLS } from "@/components/ui/Input";
import {
  resolveGeometry2D,
  type GeometryDefinition2D, type MathCommand, type MathWorkbenchDocument, type ResolvedGeometry2D,
} from "./workbench-types.ts";
import { formatNumber, PALETTE } from "./panel-shared.ts";

const KIND_LABEL_KEYS: Record<string, string> = {
  point: "objKindPoint",
  line: "objKindLine",
  constructedLine: "objKindConstructedLine",
  circle: "objKindCircle",
  arc: "objKindArc",
  polygon: "objKindPolygon",
  regularPolygon: "objKindRegularPolygon",
};

/** Language-neutral summary under an object (math text + labels only). */
function objectSummary(object: GeometryDefinition2D, resolved: ResolvedGeometry2D): string | null {
  switch (object.kind2d) {
    case "point": {
      const point = resolved.points.get(object.id);
      return point ? `(${formatNumber(point.position.x)}, ${formatNumber(point.position.y)})` : null;
    }
    case "line": {
      const line = resolved.lines.get(object.id);
      return line && line.kind === "segment" && line.length !== null ? `L = ${formatNumber(line.length)}` : null;
    }
    case "circle": {
      const circle = resolved.circles.get(object.id);
      return circle ? `r = ${formatNumber(circle.radius)}` : null;
    }
    default:
      return null;
  }
}

export function ObjectsPanel({ tr, document, selection, onSelect, dispatch, readOnly }: {
  tr: (key: string) => string;
  document: MathWorkbenchDocument;
  selection: string | null;
  onSelect: (id: string | null) => void;
  dispatch: (command: MathCommand, label: string) => void;
  readOnly: boolean;
}) {
  const objects = document.objects2d;
  const resolved = useMemo(() => resolveGeometry2D(objects), [objects]);
  const labelOf = (id: string): string => {
    const def = objects.find((obj) => obj.id === id);
    return def?.label || def?.id || "?";
  };
  return (
    <div className="panel-stack">
      {objects.length === 0 && <p className="panel-hint">{tr("objectsEmpty")}</p>}
      {objects.map((obj) => (
        <div
          key={obj.id}
          className={`object-row ${selection === obj.id ? "is-selected" : ""}`}
          data-testid="geometry-object-row"
          onClick={() => onSelect(obj.id)}
        >
          <button
            className="plot-eye"
            aria-label={tr("showFunction")}
            aria-pressed={obj.visible}
            disabled={readOnly}
            onClick={(event) => { event.stopPropagation(); dispatch({ kind: "setVisibility", target: { collection: "objects2d", id: obj.id }, visible: !obj.visible }, "toggle-visibility"); }}
          >{obj.visible ? "◉" : "○"}</button>
          <span className="object-kind">{tr(KIND_LABEL_KEYS[obj.kind2d] ?? "objKindPoint")}</span>
          <input
            className="object-label-input"
            value={obj.label}
            disabled={readOnly}
            maxLength={12}
            aria-label={tr("label")}
            onClick={(event) => event.stopPropagation()}
            onChange={(event) => dispatch({ kind: "update2D", id: obj.id, patch: { label: event.target.value } }, "rename-object")}
          />
          <span className="object-summary">
            {obj.kind2d === "line" ? `${labelOf(obj.aId)} – ${labelOf(obj.bId)}` : objectSummary(obj, resolved)}
          </span>
          <button
            className="param-remove"
            aria-label={tr("deleteSelected")}
            disabled={readOnly}
            onClick={(event) => { event.stopPropagation(); dispatch({ kind: "remove2D", id: obj.id, cascade: true }, "remove-object"); }}
          >×</button>
        </div>
      ))}
    </div>
  );
}

/** Construction inputs summary: math labels only, no prose to translate. */
function constructionDetail(object: GeometryDefinition2D, labelOf: (id: string) => string): string | null {
  switch (object.kind2d) {
    case "point":
      if (object.construction.kind === "midpoint") return `(${labelOf(object.construction.aId)}, ${labelOf(object.construction.bId)})`;
      if (object.construction.kind === "intersection") return `(${labelOf(object.construction.aId)}, ${labelOf(object.construction.bId)})`;
      return null;
    case "line":
      return `${labelOf(object.aId)} – ${labelOf(object.bId)}`;
    case "circle":
      if (object.construction.kind === "centerRadius") return `${labelOf(object.construction.centerId)} · r = ${formatNumber(object.construction.radius)}`;
      if (object.construction.kind === "centerPoint") return `${labelOf(object.construction.centerId)} · ${labelOf(object.construction.pointId)}`;
      return `${labelOf(object.construction.aId)} · ${labelOf(object.construction.bId)} · ${labelOf(object.construction.cId)}`;
    case "arc":
      return `${labelOf(object.centerId)} · ${labelOf(object.fromId)} → ${labelOf(object.toId)}`;
    case "polygon":
      return object.pointIds.map(labelOf).join(" – ");
    case "regularPolygon":
      return `${labelOf(object.centerId)} · ${labelOf(object.vertexId)} · n = ${object.sides}`;
    default:
      return null;
  }
}

export function InspectorPanel({ tr, document, selection, dispatch, readOnly }: {
  tr: (key: string) => string;
  document: MathWorkbenchDocument;
  selection: string | null;
  dispatch: (command: MathCommand, label: string) => void;
  readOnly: boolean;
}) {
  const objects = document.objects2d;
  const object = objects.find((obj) => obj.id === selection);
  const resolved = useMemo(() => resolveGeometry2D(objects), [objects]);
  if (!object) {
    return (
      <div className="panel-stack">
        <p className="panel-hint">{tr("selectObjectHint")}</p>
      </div>
    );
  }
  const kindLabel = tr(KIND_LABEL_KEYS[object.kind2d] ?? "objKindPoint");
  const detail = constructionDetail(object, (id) => objects.find((obj) => obj.id === id)?.label || "?");
  const isFreePoint = object.kind2d === "point" && object.construction.kind === "free";
  const setCoords = (axis: "x" | "y", text: string) => {
    if (object.kind2d !== "point" || object.construction.kind !== "free") return;
    const value = Number(text.trim());
    if (!Number.isFinite(value) || value === object.construction[axis]) return;
    dispatch({
      kind: "update2D",
      id: object.id,
      patch: { construction: { kind: "free", x: axis === "x" ? value : object.construction.x, y: axis === "y" ? value : object.construction.y } },
    }, "move-point");
  };
  const line = object.kind2d === "line" ? resolved.lines.get(object.id) : null;
  const circle = object.kind2d === "circle" ? resolved.circles.get(object.id) : null;
  const commitLabel = (event: React.FocusEvent<HTMLInputElement>) => {
    const value = event.target.value.trim();
    if (value !== object.label) dispatch({ kind: "update2D", id: object.id, patch: { label: value } }, "rename-object");
  };
  return (
    <div className="panel-stack" data-testid="geometry-inspector">
      <Field label={tr("label")}>
        <input
          className={INPUT_CLS}
          key={`label-${object.id}-${object.label}`}
          defaultValue={object.label}
          disabled={readOnly}
          maxLength={12}
          onBlur={commitLabel}
          onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }}
        />
      </Field>
      <div className="detail-row">
        <span className="detail-label">{tr("plotType")}</span>
        <span className="inspector-type">{kindLabel}{detail ? ` · ${detail}` : ""}</span>
      </div>
      {isFreePoint && object.construction.kind === "free" && (
        <div className="inspector-coords">
          <label className="inspector-coord">
            <span aria-hidden>x =</span>
            <input
              className="domain-number"
              key={`x-${object.id}-${object.construction.x}`}
              defaultValue={formatNumber(object.construction.x)}
              disabled={readOnly}
              inputMode="decimal"
              aria-label="x"
              onBlur={(event) => setCoords("x", event.target.value)}
              onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }}
            />
          </label>
          <label className="inspector-coord">
            <span aria-hidden>y =</span>
            <input
              className="domain-number"
              key={`y-${object.id}-${object.construction.y}`}
              defaultValue={formatNumber(object.construction.y)}
              disabled={readOnly}
              inputMode="decimal"
              aria-label="y"
              onBlur={(event) => setCoords("y", event.target.value)}
              onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }}
            />
          </label>
        </div>
      )}
      {line && line.kind === "segment" && line.length !== null && (
        <div className="detail-row">
          <span className="detail-label">{tr("lengthLabel")}</span>
          <span className="inspector-value">{formatNumber(line.length)}</span>
        </div>
      )}
      {circle && (
        <div className="detail-row">
          <span className="detail-label">{tr("radiusLabel")}</span>
          <span className="inspector-value">{formatNumber(circle.radius)}</span>
        </div>
      )}
      <Field label={tr("color")}>
        <div className="palette-row">
          {PALETTE.map((color) => (
            <button
              key={color}
              type="button"
              className={`swatch ${object.style.color === color ? "is-active" : ""}`}
              style={{ background: color }}
              aria-label={color}
              disabled={readOnly}
              onClick={() => dispatch({ kind: "setStyle", target: { collection: "objects2d", id: object.id }, patch: { color } }, "style")}
            />
          ))}
        </div>
      </Field>
      <Field label={tr("lineWidth")}>
        <input
          type="range"
          min={0.5}
          max={6}
          step={0.5}
          value={object.style.width}
          disabled={readOnly}
          onChange={(event) => dispatch({ kind: "setStyle", target: { collection: "objects2d", id: object.id }, patch: { width: Number(event.target.value) } }, "style")}
        />
      </Field>
      <label className="detail-row">
        <span className="detail-label">{tr("lockedLabel")}</span>
        <input
          type="checkbox"
          checked={object.locked}
          disabled={readOnly}
          onChange={(event) => dispatch({ kind: "update2D", id: object.id, patch: { locked: event.target.checked } }, "lock-object")}
        />
      </label>
      <Button variant="danger" size="sm" disabled={readOnly} onClick={() => dispatch({ kind: "remove2D", id: object.id, cascade: true }, "remove-object")}>
        {tr("deleteSelected")}
      </Button>
    </div>
  );
}
