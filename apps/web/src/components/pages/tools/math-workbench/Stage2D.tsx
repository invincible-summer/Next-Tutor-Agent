"use client";

/**
 * 2D SVG stage: coordinate paper, sampled function curves, constructed
 * geometry and pointer interactions (pan/zoom/select/drag/tools). All math
 * happens in world coordinates; only rendering uses CSS-pixel sizes so
 * labels and hit areas stay readable at any zoom (plan F6).
 *
 * The stage renders per-mode content (ADR-0022): `functions2d` shows axes,
 * ticks and curves (plus free points and annotations); `geometry2d` shows
 * grid and constructed geometry only — no axes, no curves.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  DEFAULT_VIEW_2D, buildEvaluationContext, compileExpression, resolveGeometry2D,
  sampleExplicit, sampleInverse, sampleParametric2D, samplePolar, implicitContour,
  newDefinitionId, type MathCommand, type MathWorkbenchDocument, type Pt2,
} from "./workbench-types.ts";
import { formatNumber } from "./panel-shared.ts";

interface StageSize { width: number; height: number }

/** MathResult → plain polylines; failed sampling simply draws nothing. */
function unwrap(result: { ok: true; value: { polylines: Pt2[][] } } | { ok: false }): { polylines: Pt2[][] } | null {
  return result.ok ? { polylines: result.value.polylines } : null;
}

function niceStep(target: number): number {
  const power = Math.pow(10, Math.floor(Math.log10(target)));
  const normalized = target / power;
  if (normalized >= 5) return 5 * power;
  if (normalized >= 2) return 2 * power;
  return power;
}

export interface ViewControls {
  zoomIn: () => void;
  zoomOut: () => void;
  resetView: () => void;
  fitView: () => void;
}

export function Stage2D({
  document, tool, selection, onSelect, dispatch, tr, readOnly, onViewControls,
}: {
  document: MathWorkbenchDocument;
  tool: string;
  selection: string | null;
  onSelect: (id: string | null) => void;
  dispatch: (command: MathCommand, label: string) => void;
  tr: (key: string) => string;
  readOnly: boolean;
  onViewControls?: (controls: ViewControls | null) => void;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [size, setSize] = useState<StageSize>({ width: 800, height: 600 });
  const [pending, setPending] = useState<{ tool: string; docId: string; points: Pt2[]; hitIds?: Array<string | null> } | null>(null);
  const [hoverPoint, setHoverPoint] = useState<string | null>(null);
  const [pendingCursor, setPendingCursor] = useState<Pt2 | null>(null);
  const downScreenRef = useRef<{ x: number; y: number } | null>(null);
  // A construction only lives inside its tool AND its document; anything
  // left over from a previous tool/document is inert, never reset by an
  // effect (avoids cascading renders).
  const activePending = pending !== null && pending.tool === tool && pending.docId === document.id ? pending : null;
  const constructionActive = tool === "point" || tool === "line" || tool === "circle" || tool === "measure" || activePending !== null;
  const [drag, setDrag] = useState<{ id: string; startWorld: Pt2; origin: Pt2 } | null>(null);
  const [dragPreview, setDragPreview] = useState<{ id: string; x: number; y: number } | null>(null);
  const [pan, setPan] = useState<{ startX: number; startY: number; centerX: number; centerY: number } | null>(null);
  const [cursor, setCursor] = useState<Pt2>({ x: 0, y: 0 });
  const [textEditing, setTextEditing] = useState<{ id: string | null; world: Pt2; text: string } | null>(null);
  const isFunctionsMode = document.activeMode === "functions2d";

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (rect) setSize({ width: Math.max(80, rect.width), height: Math.max(80, rect.height) });
    });
    observer.observe(host);
    return () => observer.disconnect();
  }, []);

  const view = document.view2d;
  const toScreen = useCallback((p: Pt2): Pt2 => ({
    x: (p.x - view.centerX) / view.scale + size.width / 2,
    y: size.height / 2 - (p.y - view.centerY) / view.scale,
  }), [view, size]);
  const toWorld = useCallback((p: Pt2): Pt2 => ({
    x: (p.x - size.width / 2) * view.scale + view.centerX,
    y: (size.height / 2 - p.y) * view.scale + view.centerY,
  }), [view, size]);

  const viewport = useMemo(() => {
    const halfW = (size.width / 2) * view.scale;
    const halfH = (size.height / 2) * view.scale;
    return { minX: view.centerX - halfW, maxX: view.centerX + halfW, minY: view.centerY - halfH, maxY: view.centerY + halfH };
  }, [size, view]);

  const setView = useCallback((next: Partial<typeof view>) => {
    dispatch({ kind: "setView2D", view: { ...document.view2d, ...next } }, "view");
  }, [dispatch, document.view2d]);

  /* --------------------------------- curves --------------------------------- */
  // Context only depends on named functions/parameters; other document edits
  // (views, geometry) must not invalidate compiled expressions.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const ctx = useMemo(() => buildEvaluationContext(document), [document.functions, document.parameters]);
  const curves = useMemo(() => {
    if (!ctx.ok || !isFunctionsMode) return [];
    const out: Array<{ id: string; color: string; width: number; dashed: boolean; polylines: Pt2[][] }> = [];
    for (const plot of document.plots2d) {
      if (!plot.visible) continue;
      const variables = [...axisVarsOf(plot), ...Object.keys(ctx.value.parameters)];
      const symbols = { variables, functions: ctx.value.functions };
      let result: { polylines: Pt2[][] } | null = null;
      if (plot.kind === "explicit") {
        const compiled = compileExpression(plot.expression, symbols);
        if (compiled.ok) result = unwrap(sampleExplicit(compiled.value, symbols, viewport, plot.xDomain));
      } else if (plot.kind === "inverse") {
        const compiled = compileExpression(plot.expression, symbols);
        if (compiled.ok) result = unwrap(sampleInverse(compiled.value, symbols, viewport, plot.yDomain));
      } else if (plot.kind === "parametric") {
        const x = compileExpression(plot.xExpression, symbols);
        const y = compileExpression(plot.yExpression, symbols);
        if (x.ok && y.ok) result = unwrap(sampleParametric2D(x.value, y.value, symbols, plot.tDomain));
      } else if (plot.kind === "polar") {
        const r = compileExpression(plot.rExpression, symbols);
        if (r.ok) result = unwrap(samplePolar(r.value, symbols, plot.thetaDomain));
      } else {
        const compiled = compileExpression(plot.expression, symbols);
        if (compiled.ok) result = unwrap(implicitContour(compiled.value, symbols, plot.level, plot.domain, 96));
      }
      if (result && result.polylines.length > 0) {
        out.push({ id: plot.id, color: plot.style.color, width: plot.style.width, dashed: plot.style.dashed, polylines: result.polylines });
      }
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [document.plots2d, ctx.ok, isFunctionsMode, viewport.minX, viewport.maxX, viewport.minY, viewport.maxY]);

  /* -------------------------------- geometry -------------------------------- */
  const resolved = useMemo(() => resolveGeometry2D(document.objects2d), [document.objects2d]);
  // During a point drag the whole construction re-resolves live against the
  // previewed position; the commit still lands once, on pointer-up.
  const dragObjects = useMemo(() => {
    if (!dragPreview) return null;
    return document.objects2d.map((obj) => (
      obj.kind2d === "point" && obj.id === dragPreview.id && obj.construction.kind === "free"
        ? { ...obj, construction: { kind: "free" as const, x: dragPreview.x, y: dragPreview.y } }
        : obj
    ));
  }, [document.objects2d, dragPreview]);
  const shown = useMemo(() => (dragObjects ? resolveGeometry2D(dragObjects) : resolved), [dragObjects, resolved]);

  // Geometry-mode objects render fully; function mode keeps only free points
  // (e.g. calculator root markers) plus annotations.
  const visiblePoints = useMemo(() => Array.from(shown.points.values()).filter((point) =>
    point && point.definition.visible && (isFunctionsMode ? point.definition.kind2d === "point" : true),
  ), [shown, isFunctionsMode]);
  const showGeometry = !isFunctionsMode;

  const pointAt = useCallback((world: Pt2, toleranceWorld: number): string | null => {
    let best: { id: string; dist: number } | null = null;
    for (const point of visiblePoints) {
      const dist = Math.hypot(point.position.x - world.x, point.position.y - world.y);
      if (dist <= toleranceWorld && (!best || dist < best.dist)) best = { id: point.id, dist };
    }
    return best?.id ?? null;
  }, [visiblePoints]);

  const snap = useCallback((world: Pt2): Pt2 => {
    if (!document.view2d.snapToGrid) return world;
    const step = document.view2d.snapSize;
    return { x: Math.round(world.x / step) * step, y: Math.round(world.y / step) * step };
  }, [document.view2d.snapToGrid, document.view2d.snapSize]);

  /** A free-point add command plus its fresh id (for batch construction). */
  const newPointCommand = useCallback((world: Pt2, label: string): { id: string; command: MathCommand } => {
    const id = newDefinitionId("pt");
    return {
      id,
      command: {
        kind: "add2D",
        object: {
          kind2d: "point", id, label, visible: true, locked: false,
          style: { color: "#2f3e4f", width: 2, opacity: 1, dashed: false },
          construction: { kind: "free", x: world.x, y: world.y },
        },
      },
    };
  }, []);

  /**
   * Construction click resolution: an existing point within a slightly
   * generous tolerance is reused (never duplicated); otherwise grid snap
   * decides where a new point would land.
   */
  const resolveClick = useCallback((world: Pt2): { hitId: string | null; point: Pt2 } => {
    const hitId = pointAt(world, 14 * view.scale);
    if (hitId) {
      const existing = shown.points.get(hitId);
      if (existing) return { hitId, point: existing.position };
    }
    return { hitId: null, point: snap(world) };
  }, [pointAt, shown, snap, view.scale]);

  /**
   * Finish a two-click construction (segment / circle / measure). Reused
   * anchors keep pointing at the same point ids, new points and the shape
   * land as ONE batch → one undo entry. Degenerate gestures (same anchor
   * twice, zero length) are ignored so nothing invisible is created.
   */
  const finishTwoPoint = useCallback((endWorld: Pt2) => {
    if (!activePending || activePending.points.length < 1) return;
    const startHitId = activePending.hitIds?.[0] ?? null;
    const startPoint = (startHitId ? shown.points.get(startHitId)?.position : undefined) ?? activePending.points[0] as Pt2;
    const end = resolveClick(endWorld);
    if (startHitId && startHitId === end.hitId) return;
    if (Math.hypot(end.point.x - startPoint.x, end.point.y - startPoint.y) < 4 * view.scale) return;
    const commands: MathCommand[] = [];
    const count = document.objects2d.filter((o) => o.kind2d === "point").length;
    const ensure = (hitId: string | null, point: Pt2, label: string): string => {
      if (hitId) return hitId;
      const created = newPointCommand(point, label);
      commands.push(created.command);
      return created.id;
    };
    if (activePending.tool === "circle") {
      const centerId = ensure(startHitId, startPoint, "O");
      const pointId = ensure(end.hitId, end.point, "P");
      commands.push({
        kind: "add2D",
        object: {
          kind2d: "circle", id: newDefinitionId("ci"), label: "c", visible: true, locked: false,
          style: { color: "#2f7d6e", width: 2, opacity: 1, dashed: false },
          construction: { kind: "centerPoint", centerId, pointId },
        },
      });
      dispatch({ kind: "batch", commands }, "add-circle");
    } else if (activePending.tool === "measure") {
      dispatch({
        kind: "addAnnotation",
        annotation: {
          id: newDefinitionId("an"), text: `d = ${formatNumber(Math.hypot(end.point.x - startPoint.x, end.point.y - startPoint.y))}`,
          x: (startPoint.x + end.point.x) / 2, y: (startPoint.y + end.point.y) / 2, visible: true, space: "2d",
        },
      }, "measure");
    } else {
      const aId = ensure(startHitId, startPoint, nextLabel(count));
      const bId = ensure(end.hitId, end.point, nextLabel(count + 1));
      // Domain validation rejects empty labels — lines need one too.
      const lineCount = document.objects2d.filter((o) => o.kind2d === "line").length;
      commands.push({
        kind: "add2D",
        object: {
          kind2d: "line", id: newDefinitionId("ln"), label: `L${lineCount + 1}`, visible: true, locked: false,
          style: { color: "#2f7d6e", width: 2, opacity: 1, dashed: false },
          lineKind: "segment", aId, bId,
        },
      });
      dispatch({ kind: "batch", commands }, "add-line");
    }
    setPending(null);
    setPendingCursor(null);
  }, [activePending, shown, resolveClick, view.scale, document.objects2d, newPointCommand, dispatch]);

  /* ------------------------------- view controls ------------------------------- */
  const contentBounds = useCallback((): { minX: number; minY: number; maxX: number; maxY: number } | null => {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    const add = (x: number, y: number) => {
      if (!Number.isFinite(x) || !Number.isFinite(y)) return;
      if (x < minX) minX = x;
      if (y < minY) minY = y;
      if (x > maxX) maxX = x;
      if (y > maxY) maxY = y;
    };
    for (const curve of curves) for (const line of curve.polylines) for (const p of line) add(p.x, p.y);
    for (const point of visiblePoints) add(point.position.x, point.position.y);
    for (const circle of shown.circles.values()) {
      if (!circle) continue;
      add(circle.center.x - circle.radius, circle.center.y - circle.radius);
      add(circle.center.x + circle.radius, circle.center.y + circle.radius);
    }
    for (const line of [...shown.lines.values(), ...shown.constructedLines.values()]) {
      if (!line) continue;
      add(line.point.x, line.point.y);
      add(line.point.x + line.direction.x * (line.length ?? 1), line.point.y + line.direction.y * (line.length ?? 1));
    }
    if (minX === Infinity || maxX === -Infinity) return null;
    return { minX, minY, maxX, maxY };
  }, [curves, visiblePoints, shown]);

  const zoomAt = useCallback((factor: number, anchorScreen?: Pt2) => {
    const anchor = anchorScreen ?? { x: size.width / 2, y: size.height / 2 };
    const world = toWorld(anchor);
    const scale = Math.min(100, Math.max(0.0005, view.scale * factor));
    if (scale === view.scale) return;
    setView({
      scale,
      centerX: world.x - (anchor.x - size.width / 2) * scale,
      centerY: world.y + (anchor.y - size.height / 2) * scale,
    });
  }, [size, toWorld, view.scale, setView]);

  const controls = useMemo<ViewControls>(() => ({
    zoomIn: () => zoomAt(1 / 1.25),
    zoomOut: () => zoomAt(1.25),
    resetView: () => setView({ ...DEFAULT_VIEW_2D, showGrid: document.view2d.showGrid, snapToGrid: document.view2d.snapToGrid, snapSize: document.view2d.snapSize }),
    fitView: () => {
      const bounds = contentBounds();
      if (!bounds) { setView({ centerX: 0, centerY: 0, scale: DEFAULT_VIEW_2D.scale }); return; }
      const width = Math.max(bounds.maxX - bounds.minX, 1e-6);
      const height = Math.max(bounds.maxY - bounds.minY, 1e-6);
      const scale = Math.min(100, Math.max(0.0005, Math.max(width / (size.width * 0.82), height / (size.height * 0.82))));
      setView({ scale, centerX: (bounds.minX + bounds.maxX) / 2, centerY: (bounds.minY + bounds.maxY) / 2 });
    },
  }), [zoomAt, setView, contentBounds, size.width, size.height, document.view2d.showGrid, document.view2d.snapToGrid, document.view2d.snapSize]);

  useEffect(() => { onViewControls?.(controls); return () => onViewControls?.(null); }, [controls, onViewControls]);

  /* ------------------------------- interactions ------------------------------- */
  const onPointerDown = (event: React.PointerEvent<SVGSVGElement>) => {
    if (event.button !== 0) return;
    const svg = svgRef.current;
    const rect = svg?.getBoundingClientRect();
    if (!svg || !rect) return;
    const screen = { x: event.clientX - rect.left, y: event.clientY - rect.top };
    const world = toWorld(screen);
    downScreenRef.current = { x: event.clientX, y: event.clientY };
    svg.setPointerCapture(event.pointerId);
    if (tool === "select" || readOnly) {
      const hit = readOnly ? null : pointAt(world, 12 * view.scale);
      onSelect(hit);
      if (hit) {
        const point = shown.points.get(hit);
        if (point && point.definition.construction.kind === "free" && !point.definition.locked) {
          setDrag({ id: hit, startWorld: world, origin: { x: point.definition.construction.x, y: point.definition.construction.y } });
        }
      } else {
        setPan({ startX: event.clientX, startY: event.clientY, centerX: view.centerX, centerY: view.centerY });
      }
      return;
    }
    if (readOnly) return;
    // Construction tools: click-click or drag both work; clicking on an
    // existing point reuses it as an anchor instead of creating a duplicate.
    if (tool === "point") {
      const click = resolveClick(world);
      if (click.hitId) { onSelect(click.hitId); return; }
      dispatch(newPointCommand(click.point, nextLabel(document.objects2d.filter((o) => o.kind2d === "point").length)).command, "add-point");
      return;
    }
    if (tool === "line" || tool === "circle" || tool === "measure") {
      if (activePending && activePending.points.length >= 1) {
        finishTwoPoint(world);
      } else {
        const click = resolveClick(world);
        setPending({ tool, docId: document.id, points: [click.point], hitIds: [click.hitId] });
      }
      return;
    }
    if (tool === "text") {
      setTextEditing({ id: null, world: snap(world), text: "" });
    }
  };

  const onPointerMove = (event: React.PointerEvent<SVGSVGElement>) => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return;
    const screen = { x: event.clientX - rect.left, y: event.clientY - rect.top };
    const world = toWorld(screen);
    setCursor(world);
    if (drag) {
      const dx = world.x - drag.startWorld.x;
      const dy = world.y - drag.startWorld.y;
      const moved = snap({ x: drag.origin.x + dx, y: drag.origin.y + dy });
      setDragPreview({ id: drag.id, x: moved.x, y: moved.y });
      return;
    }
    if (pan) {
      const dx = (event.clientX - pan.startX) * view.scale;
      const dy = (pan.startY - event.clientY) * view.scale;
      setView({ centerX: pan.centerX - dx, centerY: pan.centerY - dy });
      return;
    }
    // Construction feedback: a magnetic ring over the reusable point and a
    // rubber band from the pending anchor to the (snapped) cursor.
    if (!readOnly && constructionActive) {
      const hitId = pointAt(world, 14 * view.scale);
      setHoverPoint(hitId);
      if (activePending && activePending.points.length >= 1) {
        const anchor = (hitId ? shown.points.get(hitId)?.position : undefined) ?? snap(world);
        setPendingCursor(anchor);
      }
    } else if (hoverPoint || pendingCursor) {
      setHoverPoint(null);
      setPendingCursor(null);
    }
  };

  const onPointerUp = (event: React.PointerEvent<SVGSVGElement>) => {
    if (drag && dragPreview) {
      const { id, x, y } = dragPreview;
      if (shown.points.has(id)) {
        dispatch({
          kind: "update2D", id,
          patch: { construction: { kind: "free", x, y } },
        }, "move-point");
      }
    }
    if (drag || pan) (event.currentTarget as SVGSVGElement).releasePointerCapture(event.pointerId);
    // Drag-to-draw: releasing far from the press point completes the shape
    // in one gesture; a plain click just leaves the pending anchor waiting.
    if (!drag && !pan && activePending && activePending.points.length >= 1 && downScreenRef.current) {
      const dx = event.clientX - downScreenRef.current.x;
      const dy = event.clientY - downScreenRef.current.y;
      if (Math.hypot(dx, dy) > 8) {
        const rect = svgRef.current?.getBoundingClientRect();
        if (rect) {
          finishTwoPoint(toWorld({ x: event.clientX - rect.left, y: event.clientY - rect.top }));
        }
      }
    }
    setDrag(null);
    setDragPreview(null);
    setPan(null);
  };

  /* --------------------- keyboard: cancel / delete selection --------------------- */
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.isComposing || event.defaultPrevented) return;
      const target = event.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable)) return;
      if (event.key === "Escape") {
        if (activePending) {
          setPending(null);
          setPendingCursor(null);
        } else {
          onSelect(null);
        }
        return;
      }
      if ((event.key === "Delete" || event.key === "Backspace") && !readOnly && selection) {
        if (document.objects2d.some((o) => o.id === selection)) {
          event.preventDefault();
          dispatch({ kind: "remove2D", id: selection, cascade: true }, "remove-object");
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [activePending, selection, readOnly, document.objects2d, dispatch, onSelect]);

  /* ----------------------- wheel zoom (native, non-passive) ----------------------- */
  const stateRef = useRef({ view, size, toWorld });
  useEffect(() => {
    stateRef.current = { view, size, toWorld };
  });
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const { view: current, size: currentSize, toWorld: currentToWorld } = stateRef.current;
      const rect = svg.getBoundingClientRect();
      const screen = { x: event.clientX - rect.left, y: event.clientY - rect.top };
      const world = currentToWorld(screen);
      const factor = Math.exp(event.deltaY * 0.0012);
      const scale = Math.min(100, Math.max(0.0005, current.scale * factor));
      // Keep the world point under the cursor fixed.
      setView({
        scale,
        centerX: world.x - (screen.x - currentSize.width / 2) * scale,
        centerY: world.y + (screen.y - currentSize.height / 2) * scale,
      });
    };
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, [setView]);

  /* ------------------------------ export (2D SVG) ------------------------------ */
  useEffect(() => {
    const handler = () => {
      const svg = svgRef.current?.cloneNode(true) as SVGSVGElement | null;
      if (!svg) return;
      svg.querySelectorAll("[data-no-export]").forEach((node) => node.remove());
      svg.setAttribute("xmlns", "http://www.w3.org/2000/svg");
      const blob = new Blob([new XMLSerializer().serializeToString(svg)], { type: "image/svg+xml" });
      const url = URL.createObjectURL(blob);
      const anchor = window.document.createElement("a");
      anchor.href = url;
      anchor.download = `${(document.name.trim() || "drawing").replace(/[\\/:*?"<>|]/g, "_")}.svg`;
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 4000);
    };
    window.addEventListener("math-workbench:export-canvas", handler);
    return () => window.removeEventListener("math-workbench:export-canvas", handler);
  }, [document.name]);

  /* ------------------------------ text editing ------------------------------ */
  const commitTextEditing = () => {
    if (!textEditing) return;
    const text = textEditing.text.trim();
    if (text.length > 0) {
      if (textEditing.id) {
        dispatch({ kind: "updateAnnotation", id: textEditing.id, patch: { text } }, "edit-text");
      } else {
        dispatch({
          kind: "addAnnotation",
          annotation: { id: newDefinitionId("an"), text, x: textEditing.world.x, y: textEditing.world.y, visible: true, space: "2d" },
        }, "add-text");
      }
    }
    setTextEditing(null);
  };

  /* --------------------------------- rendering --------------------------------- */
  const step = niceStep(view.scale * 80);
  const gridLines: Array<{ x1: number; y1: number; x2: number; y2: number; major: boolean }> = [];
  if (view.showGrid) {
    const startX = Math.ceil(viewport.minX / step) * step;
    const startY = Math.ceil(viewport.minY / step) * step;
    for (let x = startX; x <= viewport.maxX; x += step) {
      const a = toScreen({ x, y: viewport.minY });
      const b = toScreen({ x, y: viewport.maxY });
      gridLines.push({ x1: a.x, y1: a.y, x2: b.x, y2: b.y, major: Math.abs(x % (step * 5)) < step * 0.01 });
    }
    for (let y = startY; y <= viewport.maxY; y += step) {
      const a = toScreen({ x: viewport.minX, y });
      const b = toScreen({ x: viewport.maxX, y });
      gridLines.push({ x1: a.x, y1: a.y, x2: b.x, y2: b.y, major: Math.abs(y % (step * 5)) < step * 0.01 });
    }
  }
  // Plane geometry needs no explicit axes — the grid is the paper (ADR-0022).
  const axisEndX = toScreen({ x: viewport.maxX, y: 0 });
  const axisStartX = toScreen({ x: viewport.minX, y: 0 });
  const axisEndY = toScreen({ x: 0, y: viewport.maxY });
  const axisStartY = toScreen({ x: 0, y: viewport.minY });

  const ticks: Array<{ screen: Pt2; label: string; vertical: boolean }> = [];
  if (isFunctionsMode) {
    const startX = Math.ceil(viewport.minX / step) * step;
    for (let x = startX; x <= viewport.maxX; x += step) {
      if (Math.abs(x) < step / 2) continue;
      const screen = toScreen({ x, y: 0 });
      if (screen.x > axisEndX.x - 34) continue; // keep the last ticks clear of the "x" label
      ticks.push({ screen, label: formatNumber(x), vertical: false });
    }
    const startY = Math.ceil(viewport.minY / step) * step;
    for (let y = startY; y <= viewport.maxY; y += step) {
      if (Math.abs(y) < step / 2) continue;
      const screen = toScreen({ x: 0, y });
      if (screen.y < axisEndY.y + 26) continue; // keep the top ticks clear of the "y" label
      ticks.push({ screen, label: formatNumber(y), vertical: true });
    }
  }

  const pathFor = (line: Pt2[]): string => line.map((p, i) => {
    const s = toScreen(p);
    return `${i === 0 ? "M" : "L"}${s.x.toFixed(2)},${s.y.toFixed(2)}`;
  }).join(" ");

  const editingScreen = textEditing ? toScreen(textEditing.world) : null;

  return (
    <div ref={hostRef} className="stage2d-host">
      <svg
        ref={svgRef}
        className="stage2d-svg"
        width={size.width}
        height={size.height}
        viewBox={`0 0 ${size.width} ${size.height}`}
        data-testid="geometry-stage-2d"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onDoubleClick={(event) => {
          if (readOnly) return;
          const rect = svgRef.current?.getBoundingClientRect();
          if (!rect) return;
          const world = toWorld({ x: event.clientX - rect.left, y: event.clientY - rect.top });
          for (const annotation of document.annotations) {
            const s = toScreen({ x: annotation.x, y: annotation.y });
            const dist = Math.hypot(s.x - (event.clientX - rect.left), s.y - (event.clientY - rect.top));
            if (dist < 40) {
              setTextEditing({ id: annotation.id, world: { x: annotation.x, y: annotation.y }, text: annotation.text });
              return;
            }
          }
          void world;
        }}
        style={{ touchAction: "none" }}
      >
        <rect x={0} y={0} width={size.width} height={size.height} className="stage2d-bg" />
        {view.showGrid && gridLines.map((line, index) => (
          <line key={`g${index}`} x1={line.x1} y1={line.y1} x2={line.x2} y2={line.y2} className={line.major ? "stage2d-grid-major" : "stage2d-grid"} />
        ))}
        {isFunctionsMode && (
          <g>
            <line x1={axisStartX.x} y1={axisStartX.y} x2={axisEndX.x} y2={axisEndX.y} className="stage2d-axis" />
            <line x1={axisStartY.x} y1={axisStartY.y} x2={axisEndY.x} y2={axisEndY.y} className="stage2d-axis" />
            <polygon points={`${axisEndX.x},${axisEndX.y} ${axisEndX.x - 10},${axisEndX.y - 4} ${axisEndX.x - 10},${axisEndX.y + 4}`} className="stage2d-axis-head" />
            <polygon points={`${axisEndY.x},${axisEndY.y} ${axisEndY.x - 4},${axisEndY.y + 10} ${axisEndY.x + 4},${axisEndY.y + 10}`} className="stage2d-axis-head" />
            <text x={axisEndX.x - 14} y={axisEndX.y - 8} className="stage2d-axis-label">x</text>
            <text x={axisEndY.x + 8} y={axisEndY.y + 14} className="stage2d-axis-label">y</text>
            {ticks.map((tick, index) => (
              <text
                key={`t${index}`}
                x={tick.vertical ? tick.screen.x + 6 : tick.screen.x}
                y={tick.vertical ? tick.screen.y : tick.screen.y - 6}
                className="stage2d-tick"
                textAnchor={tick.vertical ? "start" : "middle"}
              >{tick.label}</text>
            ))}
          </g>
        )}

        {curves.map((curve) => curve.polylines.map((line, index) => (
          <path
            key={`${curve.id}-${index}`}
            d={pathFor(line)}
            fill="none"
            stroke={curve.color}
            strokeWidth={curve.width}
            strokeDasharray={curve.dashed ? "6 4" : undefined}
            strokeLinecap="round"
          />
        )))}

        {showGeometry && Array.from(shown.circles.values()).map((circle) => {
          if (!circle) return null;
          const c = toScreen(circle.center);
          const r = circle.radius / view.scale;
          return <circle key={circle.id} cx={c.x} cy={c.y} r={r} className={`stage2d-shape ${selection === circle.id ? "is-selected" : ""}`} />;
        })}
        {showGeometry && Array.from(shown.arcs.values()).map((arc) => {
          if (!arc) return null;
          const r = arc.radius / view.scale;
          let sweep = arc.endAngle - arc.startAngle;
          while (sweep <= 0) sweep += Math.PI * 2;
          const end = toScreen({ x: arc.center.x + arc.radius * Math.cos(arc.startAngle + sweep), y: arc.center.y + arc.radius * Math.sin(arc.startAngle + sweep) });
          const start = toScreen({ x: arc.center.x + arc.radius * Math.cos(arc.startAngle), y: arc.center.y + arc.radius * Math.sin(arc.startAngle) });
          return <path key={arc.id} d={`M${start.x},${start.y} A${r},${r} 0 ${sweep > Math.PI ? 1 : 0} 1 ${end.x},${end.y}`} fill="none" className={`stage2d-shape ${selection === arc.id ? "is-selected" : ""}`} />;
        })}
        {showGeometry && Array.from(shown.polygons.values()).map((polygon) => {
          if (!polygon) return null;
          const points = polygon.points.map((p) => { const s = toScreen(p); return `${s.x},${s.y}`; }).join(" ");
          return <polygon key={polygon.id} points={points} className={`stage2d-shape-fill ${selection === polygon.id ? "is-selected" : ""}`} />;
        })}
        {showGeometry && Array.from(shown.lines.values()).concat(Array.from(shown.constructedLines.values())).map((line) => {
          if (!line) return null;
          let a: Pt2;
          let b: Pt2;
          if (line.kind === "segment") { a = line.point; b = { x: line.point.x + line.direction.x * (line.length ?? 1), y: line.point.y + line.direction.y * (line.length ?? 1) }; }
          else if (line.kind === "ray") {
            const reach = ((size.width + size.height) * view.scale);
            a = line.point; b = { x: line.point.x + line.direction.x * reach, y: line.point.y + line.direction.y * reach };
          } else {
            const reach = ((size.width + size.height) * view.scale) / 2;
            a = { x: line.point.x - line.direction.x * reach, y: line.point.y - line.direction.y * reach };
            b = { x: line.point.x + line.direction.x * reach, y: line.point.y + line.direction.y * reach };
          }
          const sa = toScreen(a);
          const sb = toScreen(b);
          return <line key={line.id} x1={sa.x} y1={sa.y} x2={sb.x} y2={sb.y} className={`stage2d-shape ${selection === line.id ? "is-selected" : ""}`} />;
        })}

        {visiblePoints.map((point) => {
          if (!point) return null;
          const s = toScreen(point.position);
          return (
            <g key={point.id}>
              <circle cx={s.x} cy={s.y} r={5} className={`stage2d-point ${selection === point.id ? "is-selected" : ""} ${dragPreview?.id === point.id ? "is-dragging" : ""}`} />
              <text x={s.x + 8} y={s.y - 8} className="stage2d-label">{point.label}</text>
            </g>
          );
        })}

        {document.annotations.filter((a) => a.visible && a.space === "2d").map((annotation) => {
          const s = toScreen({ x: annotation.x, y: annotation.y });
          return <text key={annotation.id} x={s.x} y={s.y} className={`stage2d-annotation ${textEditing?.id === annotation.id ? "is-editing" : ""}`}>{annotation.text}</text>;
        })}

        {activePending && activePending.points.map((p, index) => {
          const s = toScreen(p);
          return <circle key={`p${index}`} cx={s.x} cy={s.y} r={4} className="stage2d-pending" data-no-export />;
        })}
        {pendingCursor && activePending && activePending.points.length >= 1 && (() => {
          const start = activePending.points[0] as Pt2;
          const a = toScreen(start);
          const b = toScreen(pendingCursor);
          if (activePending.tool === "circle") {
            const radiusWorld = Math.hypot(pendingCursor.x - start.x, pendingCursor.y - start.y);
            return <circle cx={a.x} cy={a.y} r={radiusWorld / view.scale} className="stage2d-preview" data-no-export />;
          }
          return <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} className="stage2d-preview" data-no-export />;
        })()}
        {constructionActive && hoverPoint && (() => {
          const point = shown.points.get(hoverPoint);
          if (!point) return null;
          const s = toScreen(point.position);
          return <circle cx={s.x} cy={s.y} r={10} className="stage2d-snap-ring" data-no-export />;
        })()}
      </svg>
      {textEditing && editingScreen && (
        <input
          className="stage2d-text-input"
          style={{ left: editingScreen.x, top: editingScreen.y - 14 }}
          value={textEditing.text}
          autoFocus
          maxLength={200}
          onChange={(event) => setTextEditing((prev) => (prev ? { ...prev, text: event.target.value } : prev))}
          onKeyDown={(event) => {
            if (event.nativeEvent.isComposing) return;
            if (event.key === "Enter") commitTextEditing();
            if (event.key === "Escape") setTextEditing(null);
          }}
          onBlur={commitTextEditing}
          aria-label={tr("toolText")}
        />
      )}
      <div className="stage2d-cursor" data-testid="geometry-cursor" aria-hidden>
        {tr("cursor")} ({formatNumber(cursor.x)}, {formatNumber(cursor.y)})
        {(() => {
          if (activePending) return <span className="stage2d-hint">{tr("hintPending")}</span>;
          if (tool === "line" || tool === "circle" || tool === "measure") return <span className="stage2d-hint">{tr("hintToolStart")}</span>;
          return null;
        })()}
      </div>
    </div>
  );
}

function axisVarsOf(plot: { kind: string }): string[] {
  switch (plot.kind) {
    case "explicit": return ["x"];
    case "inverse": return ["y"];
    case "parametric": return ["t"];
    case "polar": return ["theta"];
    default: return ["x", "y"];
  }
}

function nextLabel(count: number): string {
  const letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  return letters[count % 26] + (count >= 26 ? String(Math.floor(count / 26) + 1) : "");
}
