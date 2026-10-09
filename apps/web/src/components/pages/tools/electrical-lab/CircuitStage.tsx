"use client";
import { useEffect, useRef, useState } from "react";
import type { ElectricalComponent, ElectricalLabDocument, ElectricalWire, Point, SimulationFrame } from "@next-tutor/domain";
import { COMPONENT_KINDS, componentTerminals, projectOnWirePath, routeOrthogonal, type ElectricalComponentKind } from "@next-tutor/domain";

import { ComponentModel } from "./ComponentModel";

export const STAGE_WIDTH = 1000;
export const STAGE_HEIGHT = 680;
export type TerminalRef = string;
export type ToolMode = "select" | "wire" | "delete";
export const INSTRUMENT_POINTS: Record<string, Point> = {
  "supply:p": { x: 94, y: 118 }, "supply:n": { x: 150, y: 118 },
  "generator:p": { x: 260, y: 118 }, "generator:n": { x: 316, y: 118 },
  "meter:p": { x: 466, y: 118 }, "meter:n": { x: 522, y: 118 },
  "scope:a": { x: 710, y: 118 }, "scope:b": { x: 766, y: 118 }, "scope:g": { x: 822, y: 118 },
};

interface Props {
  document: ElectricalLabDocument;
  frame: SimulationFrame;
  selectedId: string | null;
  wireStart: TerminalRef | null;
  toolMode: ToolMode;
  onSelect: (id: string | null) => void;
  onDelete: (id: string) => void;
  onWireInsert: (wireId: string, point: Point) => void;
  onWireBlank: (point: Point) => void;
  onMove: (id: string, point: Point) => void;
  onMoveStart: () => void;
  onResize: (size: { width: number; height: number }) => void;
  onAdd: (kind: ElectricalComponentKind, point: Point) => void;
  onToggleSwitch: (id: string) => void;
  onView: (view: ElectricalLabDocument["view"]) => void;
  onTerminal: (terminal: TerminalRef) => void;
  onBackground: () => void;
  labels: Record<string, string>;
}
function rotate(point: Point, origin: Point, degrees: number): Point { const r = degrees * Math.PI / 180; const dx = point.x - origin.x; const dy = point.y - origin.y; return { x: origin.x + dx * Math.cos(r) - dy * Math.sin(r), y: origin.y + dx * Math.sin(r) + dy * Math.cos(r) }; }
export function terminalPoint(component: ElectricalComponent, terminal: string): Point {
  const base = { x: component.x, y: component.y };
  if (component.kind === "ground") return rotate({ x: component.x, y: component.y - 24 }, base, component.rotation);
  if (component.kind === "junction") return base;
  if (component.kind === "npn" || component.kind === "pnp") {
    const offset = terminal === "c" ? { x: 0, y: -30 } : terminal === "e" ? { x: 0, y: 30 } : { x: -32, y: 0 };
    return rotate({ x: component.x + offset.x, y: component.y + offset.y }, base, component.rotation);
  }
  if (component.kind === "potentiometer") {
    const offset = terminal === "p" ? { x: -38, y: 0 } : terminal === "n" ? { x: 38, y: 0 } : { x: 0, y: -28 };
    return rotate({ x: component.x + offset.x, y: component.y + offset.y }, base, component.rotation);
  }
  const offset = terminal === "p" ? { x: -38, y: 0 } : { x: 38, y: 0 };
  return rotate({ x: component.x + offset.x, y: component.y + offset.y }, base, component.rotation);
}
export function terminalPosition(document: ElectricalLabDocument, ref: TerminalRef): Point | null {
  const [id, terminal] = ref.split(":");
  if (!id || !terminal) return null;
  const component = document.components.find(item => item.id === id);
  return component ? terminalPoint(component, terminal) : INSTRUMENT_POINTS[ref] ?? null;
}
function instrumentTerminalLabel(ref: string): string {
  const [instrument, pin] = ref.split(":");
  const name = ({ supply: "PS", generator: "GEN", meter: "DMM", scope: "SCOPE" } as Record<string, string>)[instrument ?? ""] ?? instrument ?? "";
  const terminal = ({ p: "+", n: "−", a: "A", b: "B", g: "G" } as Record<string, string>)[pin ?? ""] ?? pin ?? "";
  return `${name} ${terminal}`;
}
function terminalDirection(document: ElectricalLabDocument, ref: TerminalRef): Point | null {
  const component = document.components.find(item => item.id === ref.split(":")[0]);
  if (!component) return INSTRUMENT_POINTS[ref] ? { x: 0, y: 1 } : null;
  if (component.kind === "junction") return null;
  const point = terminalPosition(document, ref)!;
  return { x: Math.abs(point.x - component.x) < .001 ? 0 : Math.sign(point.x - component.x), y: Math.abs(point.y - component.y) < .001 ? 0 : Math.sign(point.y - component.y) };
}
export function wirePoints(document: ElectricalLabDocument, wire: ElectricalWire): Point[] | null {
  const from = terminalPosition(document, wire.from); const to = terminalPosition(document, wire.to);
  return from && to ? routeOrthogonal(from, to, wire.bends, terminalDirection(document, wire.from), terminalDirection(document, wire.to)) : null;
}
function pathForWire(document: ElectricalLabDocument, wire: ElectricalWire): string {
  const points = wirePoints(document, wire);
  if (!points) return "";
  return points.map((point, index) => `${index ? "L" : "M"}${point.x} ${point.y}`).join(" ");
}
function currentForWire(frame: SimulationFrame, wire: ElectricalWire): number {
  const ids = [wire.from.split(":")[0], wire.to.split(":")[0]];
  return Math.max(...ids.map(id => Math.abs(frame.branches[id ?? ""]?.current ?? 0)), 0);
}
interface PointerAction {
  kind: "part" | "pan" | "wire";
  origin: Point;
  moved: boolean;
  id?: string;
  offset?: Point;
  view?: ElectricalLabDocument["view"];
}

/** View coordinates are offsets from the bench centre, with scale in CSS pixels per world unit. */
export function CircuitStage({ document, frame, selectedId, wireStart, toolMode, onSelect, onDelete, onWireInsert, onWireBlank, onMove, onMoveStart, onView, onResize, onAdd, onToggleSwitch, onTerminal, onBackground, labels }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const action = useRef<PointerAction | null>(null);
  const [size, setSize] = useState({ width: STAGE_WIDTH, height: STAGE_HEIGHT });
  const [busy, setBusy] = useState(false);
  const [cursor, setCursor] = useState<Point | null>(null);
  const [hoveredPin, setHoveredPin] = useState<string | null>(null);
  const baseScale = Math.max(.35, Math.min(3, document.view.scale));
  // Keep the complete instrument row in view when the dock leaves less than
  // the 1000-unit bench width. The document scale remains the user's zoom
  // setting; this factor only adapts the physical viewport.
  const viewportScale = size.width < 700 ? 1 : Math.min(1, Math.max(.25, size.width / STAGE_WIDTH));
  const scale = baseScale * viewportScale;
  const width = size.width / scale;
  const height = size.height / scale;
  const left = STAGE_WIDTH / 2 + document.view.x - width / 2;
  const top = STAGE_HEIGHT / 2 + document.view.y - height / 2;

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const observer = new ResizeObserver(entries => {
      const box = entries[0]?.contentRect;
      if (!box || box.width < 1 || box.height < 1) return;
      const next = { width: box.width, height: box.height };
      setSize(next);
      onResize(next);
    });
    observer.observe(svg);
    return () => observer.disconnect();
  }, [onResize]);

  const toWorld = (event: { clientX: number; clientY: number }): Point => {
    const svg = svgRef.current;
    const matrix = svg?.getScreenCTM();
    if (!svg || !matrix) return { x: 500, y: 340 };
    const p = svg.createSVGPoint();
    p.x = event.clientX;
    p.y = event.clientY;
    const world = p.matrixTransform(matrix.inverse());
    return { x: world.x, y: world.y };
  };
  const pins = [
    ...Object.entries(INSTRUMENT_POINTS),
    ...document.components.flatMap(c => componentTerminals(c.kind).map(pin => [`${c.id}:${pin}`, terminalPoint(c, pin)] as [string, Point])),
  ];
  const nearestPin = (point: Point): [string, Point] | null => {
    let nearest: [string, Point] | null = null;
    let distance = Math.max(17, 20 / scale);
    for (const [ref, position] of pins) {
      const d = Math.hypot(position.x - point.x, position.y - point.y);
      if (ref !== wireStart && d < distance) { nearest = [ref, position]; distance = d; }
    }
    return nearest;
  };
  const capture = (event: React.PointerEvent) => svgRef.current?.setPointerCapture(event.pointerId);
  const beginPan = (event: React.PointerEvent) => {
    if (event.button !== 0 && event.button !== 1) return;
    event.preventDefault();
    if (toolMode === "wire" && event.button === 0) { onWireBlank(toWorld(event)); return; }
    if (toolMode === "delete" && event.button === 0) { onBackground(); return; }
    if (wireStart) { onBackground(); return; }
    action.current = { kind: "pan", origin: { x: event.clientX, y: event.clientY }, moved: false, view: document.view };
    capture(event);
  };
  const beginPart = (event: React.PointerEvent, component: ElectricalComponent) => {
    event.stopPropagation();
    if (event.button !== 0) { beginPan(event); return; }
    if (toolMode === "delete") { onDelete(component.id); return; }
    if (toolMode === "wire") {
      const point = toWorld(event);
      const candidate = componentTerminals(component.kind).map(pin => [`${component.id}:${pin}`, terminalPoint(component, pin)] as [string, Point]).sort((a, b) => Math.hypot(a[1].x - point.x, a[1].y - point.y) - Math.hypot(b[1].x - point.x, b[1].y - point.y))[0];
      if (candidate) onTerminal(candidate[0]);
      return;
    }
    onSelect(component.id);
    if (wireStart) return;
    const point = toWorld(event);
    action.current = { kind: "part", id: component.id, origin: { x: event.clientX, y: event.clientY }, moved: false, offset: { x: point.x - component.x, y: point.y - component.y } };
    capture(event);
  };
  const beginWire = (event: React.PointerEvent, ref: string) => {
    event.preventDefault();
    event.stopPropagation();
    if (event.button !== 0) { beginPan(event); return; }
    if (toolMode === "delete") { onTerminal(ref); return; }
    // A second click completes the wire; a first press can also start a drag.
    if (wireStart) { onTerminal(ref); return; }
    onTerminal(ref);
    setCursor(toWorld(event));
    action.current = { kind: "wire", id: ref, origin: { x: event.clientX, y: event.clientY }, moved: false };
    capture(event);
  };
  const move = (event: React.PointerEvent<SVGSVGElement>) => {
    const point = toWorld(event);
    const active = action.current;
    if (wireStart || active?.kind === "wire") {
      const target = nearestPin(point);
      setHoveredPin(target?.[0] ?? null);
      setCursor(target?.[1] ?? point);
    }
    if (!active) return;
    if (!active.moved && Math.hypot(event.clientX - active.origin.x, event.clientY - active.origin.y) < 3) return;
    if (!active.moved && active.kind === "part") onMoveStart();
    active.moved = true;
    setBusy(active.kind !== "wire");
    if (active.kind === "part" && active.offset && active.id) {
      onMove(active.id, { x: Math.round((point.x - active.offset.x) / 10) * 10, y: Math.round((point.y - active.offset.y) / 10) * 10 });
    } else if (active.kind === "pan" && active.view) {
      onView({ ...active.view, x: active.view.x - (event.clientX - active.origin.x) / scale, y: active.view.y - (event.clientY - active.origin.y) / scale });
    }
  };
  const finish = (event: React.PointerEvent<SVGSVGElement>) => {
    const active = action.current;
    if (active?.kind === "wire" && active.moved) {
      const terminal = window.document.elementFromPoint(event.clientX, event.clientY)?.closest("[data-terminal]")?.getAttribute("data-terminal");
      const target = terminal ?? nearestPin(toWorld(event))?.[0];
      if (target && target !== active.id) onTerminal(target);
    } else if (!active && wireStart) {
      // A wire may have started at a terminal in the measurement dock.
      const target = nearestPin(toWorld(event));
      if (target) onTerminal(target[0]);
    } else if (active?.kind === "pan" && !active.moved) onBackground();
    action.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    setBusy(false);
    setHoveredPin(null);
    setCursor(null);
  };
  const zoom = (event: WheelEvent) => {
    event.preventDefault();
    const point = toWorld(event);
    const nextBaseScale = Math.max(.35, Math.min(3, baseScale * (event.deltaY < 0 ? 1.12 : 1 / 1.12)));
    const nextScale = nextBaseScale * viewportScale;
    const centre = { x: 500 + document.view.x, y: 340 + document.view.y };
    onView({ scale: nextBaseScale, x: point.x - (point.x - centre.x) * scale / nextScale - 500, y: point.y - (point.y - centre.y) * scale / nextScale - 340 });
  };
  // Use a non-passive listener so wheel gestures remain within the workbench.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    svg.addEventListener("wheel", zoom, { passive: false });
    return () => svg.removeEventListener("wheel", zoom);
  });

  const terminal = (ref: string, point: Point) => <g key={ref} data-terminal={ref} className={`electrical-terminal ${document.components.some(component => component.kind === "junction" && ref === `${component.id}:p`) ? "is-junction" : ""} ${wireStart === ref ? "is-start" : ""} ${hoveredPin === ref ? "is-target" : ""}`} role="button" tabIndex={0} aria-label={ref}
    onPointerDown={event => beginWire(event, ref)} onClick={event => event.stopPropagation()}
    onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); event.stopPropagation(); onTerminal(ref); } }}>
    <circle className="terminal-hit" cx={point.x} cy={point.y} r={Math.max(15, 18 / scale)} />
    <circle className="terminal-ring" cx={point.x} cy={point.y} r="7" />
    <circle className="terminal-core" cx={point.x} cy={point.y} r="2.5" />
  </g>;
  const start = wireStart ? terminalPosition(document, wireStart) : null;
  return <svg ref={svgRef} viewBox={`${left} ${top} ${width} ${height}`} className={`electrical-stage-svg tool-${toolMode} ${busy ? "is-dragging" : ""} ${wireStart ? "is-wiring" : ""}`} role="application" aria-label={labels.workbench}
    onPointerDown={beginPan} onPointerMove={move} onPointerUp={finish} onPointerCancel={() => { action.current = null; setBusy(false); setCursor(null); }}
    onPointerLeave={() => { if (!action.current) setCursor(null); }}
    onDragOver={event => { if (event.dataTransfer.types.includes("application/x-electrical-part")) { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; } }}
    onDrop={event => { event.preventDefault(); const kind = event.dataTransfer.getData("application/x-electrical-part"); if (COMPONENT_KINDS.includes(kind as ElectricalComponentKind)) onAdd(kind as ElectricalComponentKind, toWorld(event)); }}>
    <defs><pattern id="electrical-grid" width="20" height="20" patternUnits="userSpaceOnUse"><path d="M20 0H0V20" fill="none" stroke="var(--el-grid)" strokeOpacity=".13" strokeWidth="1" /></pattern><filter id="electrical-glow"><feGaussianBlur stdDeviation="5" result="blur" /><feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge></filter></defs>
    <rect x={left} y={top} width={width} height={height} fill="url(#electrical-grid)" />
    <path d="M35 176 H885" stroke="var(--el-line)" strokeWidth="1" opacity=".5" />
    {[
      { id: "supply", x: 122, label: labels.supply, reading: document.instruments.supply.enabled ? `${document.instruments.supply.voltage.toFixed(1)} V` : "OFF" },
      { id: "generator", x: 288, label: labels.generator, reading: document.instruments.generator.enabled ? `${document.instruments.generator.frequency} Hz` : "OFF" },
      { id: "meter", x: 494, label: labels.meter, reading: "V · A · Ω" },
      { id: "scope", x: 766, label: labels.scope, reading: "A / B" },
    ].map(instrument => <g key={instrument.id} pointerEvents="none"><text x={instrument.x} y="65" textAnchor="middle" className="electrical-port-title">{instrument.label}</text><text x={instrument.x} y="85" textAnchor="middle" className="electrical-terminal-label">{instrument.reading}</text></g>)}
    {document.wires.map(wire => {
      const path = pathForWire(document, wire);
      return <g key={wire.id} data-testid={`electrical-wire-${wire.id}`} className={`electrical-wire ${currentForWire(frame, wire) > 1e-6 ? "active" : ""}`} role="button" tabIndex={0} aria-label={`${labels.wire}: ${wire.from} → ${wire.to}`}
        onPointerDown={event => { event.stopPropagation(); event.preventDefault(); if (event.button !== 0) { beginPan(event); return; } const point = wirePoints(document, wire); const projected = point ? projectOnWirePath(point, toWorld(event))?.point : null; if (toolMode === "delete") onDelete(wire.id); else if (toolMode === "wire" && projected) onWireInsert(wire.id, projected); else onSelect(wire.id); }} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); if (toolMode === "delete") onDelete(wire.id); else if (toolMode === "wire") { const route = wirePoints(document, wire); if (route && route.length > 1) { const segments = route.slice(1).map((point, index) => ({ a: route[index]!, b: point })).sort((a, b) => Math.hypot(b.b.x - b.a.x, b.b.y - b.a.y) - Math.hypot(a.b.x - a.a.x, a.b.y - a.a.y)); const segment = segments[0]!; onWireInsert(wire.id, { x: (segment.a.x + segment.b.x) / 2, y: (segment.a.y + segment.b.y) / 2 }); } } else onSelect(wire.id); } }}>
        <path d={path} fill="none" stroke="transparent" strokeWidth="18" />
        <path d={path} fill="none" stroke={selectedId === wire.id ? "var(--el-yellow)" : "var(--el-panel)"} strokeWidth="3.6" strokeLinecap="round" strokeLinejoin="round" />
        <path d={path} fill="none" stroke={wire.color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </g>;
    })}
    {start && cursor && <path d={routeOrthogonal(start, cursor, [], terminalDirection(document, wireStart!), hoveredPin ? terminalDirection(document, hoveredPin) : null).map((point, index) => `${index ? "L" : "M"}${point.x} ${point.y}`).join(" ")} className="electrical-wire-preview" fill="none" stroke="var(--el-yellow)" strokeWidth="2" strokeDasharray="7 6" pointerEvents="none" />}
    {document.components.map(component => <g key={component.id} data-testid={`electrical-component-${component.id}`} className="electrical-component" onPointerDown={event => beginPart(event, component)} onDoubleClick={() => { if (toolMode === "select" && component.kind === "switch") onToggleSwitch(component.id); }} role="button" tabIndex={0} aria-label={`${component.label} ${component.kind}`} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); if (toolMode === "delete") onDelete(component.id); else if (toolMode === "wire") onTerminal(`${component.id}:${componentTerminals(component.kind)[0]}`); else onSelect(component.id); } }}>
      {frame.branches[component.id]?.brightness ? <circle cx={component.x} cy={component.y} r={20 + frame.branches[component.id]!.brightness * 12} fill="#f6c75b" opacity={frame.branches[component.id]!.brightness * .4} filter="url(#electrical-glow)" pointerEvents="none" /> : null}
      <ComponentModel component={component} selected={selectedId === component.id} labels={labels} />
    </g>)}
    {pins.map(([ref, point]) => <g key={ref}>{terminal(ref, point)}{INSTRUMENT_POINTS[ref] && <text x={point.x} y={point.y + 26} textAnchor="middle" className="electrical-terminal-label" pointerEvents="none">{instrumentTerminalLabel(ref)}</text>}</g>)}
  </svg>;
}
