"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Activity, ArrowLeft, Cable, Check, ChevronLeft, ChevronRight, Download, Eraser, Expand, FileUp, Gauge, Hand, LocateFixed, MousePointer2, PanelRightClose, Plus, Power, RotateCw, RotateCcw, Save, Trash2, Undo2, Redo2, X, Zap, ZoomIn, ZoomOut } from "lucide-react";
import { useAuthStore } from "@/lib/auth-store";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { COMPONENT_DEFAULTS, ELECTRICAL_EXPERIMENTS, ELECTRICAL_LIMITS, createElectricalComponent, createElectricalLabDocument, experimentById, formatElectrical, parseElectrical, reduceElectricalLabCommand, sameCircuitPoint, solveCircuit, spliceElectricalWire, type ElectricalComponent, type ElectricalComponentKind, type ElectricalLabCommand, type ElectricalLabDocument, type Point, type SimulationFrame, type SimulationState } from "@next-tutor/domain";
import { importElectricalLab, exportElectricalLab, loadElectricalLabs, persistElectricalLabs, type SavedElectricalLab } from "./storage";
import { CircuitStage, INSTRUMENT_POINTS, terminalPosition, wirePoints, type TerminalRef, type ToolMode } from "./CircuitStage";
import { InstrumentDeck, type InstrumentPanel } from "./InstrumentDeck";
import { PartGlyph } from "./PartGlyph";
import { STRINGS } from "./strings";
import "./electrical-lab.css";

const PARTS: ElectricalComponentKind[] = ["resistor", "conductance", "potentiometer", "switch", "capacitor", "inductor", "bulb", "diode", "led", "npn", "pnp", "ground", "voltage", "current"];
const QUICK_PARTS: ElectricalComponentKind[] = ["resistor", "switch", "bulb", "led", "capacitor", "ground"];
const PART_LABEL: Record<string, string> = { conductance: "conductancePart", diode: "diodePart", voltage: "voltagePart", current: "currentPart" };
const PANELS = [{ id: "meter", label: "readings", icon: Gauge }, { id: "scope", label: "scopeTab", icon: Activity }, { id: "sources", label: "sources", icon: Power }] as const;
const MODES: { id: ToolMode; label: string; icon: typeof MousePointer2 }[] = [{ id: "select", label: "selectMode", icon: MousePointer2 }, { id: "wire", label: "wireMode", icon: Cable }, { id: "delete", label: "deleteMode", icon: Eraser }];
const DIAGNOSTIC_LABEL: Record<string, string> = { floating: "diagFloating", short_circuit: "diagShortCircuit", current_limit: "diagCurrentLimit", singular: "diagSingular", non_convergence: "diagNonConvergence", overload: "diagOverload", probe_missing: "diagProbeMissing", powered_ohmmeter: "diagPoweredOhmmeter", undersampled: "diagUndersampled" };
const COMPONENT_FORMULA: Partial<Record<ElectricalComponentKind, string>> = { resistor: "V = I · R", conductance: "I = G · V", potentiometer: "Vout = Vin · R₂/(R₁ + R₂)", capacitor: "I = C · dV/dt", inductor: "V = L · dI/dt", bulb: "P = V · I", diode: "I ≈ (V − V_F)/R", led: "I ≈ (V − V_F)/R", npn: "I_C ≈ β · I_B", pnp: "I_C ≈ β · I_B" };
function clampName(value: string, fallback = "Untitled circuit"): string { return value.replace(/[\u0000-\u001f]/g, "").trim().slice(0, 80) || fallback; }
function initialDocument(name = "Untitled circuit"): ElectricalLabDocument { return { ...createElectricalLabDocument("bench"), name }; }
function narrowScreen(): boolean { return window.matchMedia("(max-width: 700px)").matches; }
function startPartDrag(event: React.DragEvent, kind: ElectricalComponentKind) { event.dataTransfer.setData("application/x-electrical-part", kind); event.dataTransfer.effectAllowed = "copy"; }

function AnalysisStrip({ frame, selected, formula, labels }: { frame: SimulationFrame; selected: ElectricalComponent | null; formula: string | null; labels: Record<string, string> }) {
  const branch = selected ? frame.branches[selected.id] : undefined;
  return <div className="electrical-analysis">
    <section className="electrical-analysis-card"><h2>{labels.results}{selected && <span>{selected.label}</span>}</h2>
      {branch ? <><dl className="electrical-readings">{[[labels.voltage, formatElectrical(branch.voltage, "V")], [labels.current, formatElectrical(branch.current, "A")], [labels.powerFormula, formatElectrical(branch.power, "W")], [labels.conductance, formatElectrical(branch.conductance, "S")]].map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl><p className="electrical-analysis-formula">{COMPONENT_FORMULA[selected!.kind] ?? formula}</p></> : <p className="electrical-analysis-formula">{formula ?? labels.noSelection}</p>}
    </section>
    <section className="electrical-analysis-card diagnostics"><h2>{labels.diagnostics}</h2>{frame.diagnostics.length ? frame.diagnostics.slice(0, 6).map((item, index) => <p key={`${item.code}-${index}`} className={`electrical-diagnostic ${item.severity === "error" ? "error" : ""}`}>{labels[DIAGNOSTIC_LABEL[item.code] ?? ""] ?? item.code}</p>) : <p className="electrical-diagnostic good">{labels.connected}</p>}</section>
  </div>;
}

function PartControls({ selected, labels, onPatch, onRemove }: { selected: ElectricalComponent; labels: Record<string, string>; onPatch: (patch: Partial<ElectricalComponent>) => void; onRemove: () => void }) {
  const defaults = COMPONENT_DEFAULTS[selected.kind];
  return <>
    <span className="electrical-selection-symbol"><PartGlyph kind={selected.kind} /></span>
    <input key={selected.id} className="electrical-part-name" aria-label={labels.componentName} maxLength={32} defaultValue={selected.label} onBlur={event => { const label = clampName(event.target.value, selected.label).slice(0, 32); if (label !== selected.label) onPatch({ label }); }} onKeyDown={event => { if (event.key === "Enter") event.currentTarget.blur(); }} />
    {selected.kind !== "ground" && selected.kind !== "junction" && selected.kind !== "switch" && <label className="electrical-value-field"><span>{labels.componentValue}</span><input key={`${selected.id}-${selected.value}`} aria-label={labels.componentValue} defaultValue={formatElectrical(selected.value, defaults.unit)} spellCheck={false} onBlur={event => {
      const value = parseElectrical(event.target.value, defaults.unit);
      if (value != null && value >= defaults.min && value <= defaults.max) { if (value !== selected.value) onPatch({ value }); }
      else event.target.value = formatElectrical(selected.value, defaults.unit);
    }} onKeyDown={event => { if (event.key === "Enter") event.currentTarget.blur(); if (event.key === "Escape") { event.currentTarget.value = formatElectrical(selected.value, defaults.unit); event.currentTarget.blur(); } }} /></label>}
    {selected.kind === "potentiometer" && <label className="electrical-position-control"><span>{labels.position}</span><input type="range" aria-label={labels.position} min="0" max="1" step=".01" value={selected.position} onChange={event => onPatch({ position: Number(event.target.value) })} /><output>{Math.round(selected.position * 100)}%</output></label>}
    <div className="electrical-selection-actions"><button type="button" onClick={() => onPatch({ rotation: (selected.rotation + 90) % 360 })} aria-label={labels.rotate} title={`${labels.rotate} · R`}><RotateCw size={16} /></button>
      {selected.kind === "switch" && <button type="button" className={selected.closed ? "active" : ""} aria-pressed={selected.closed} onClick={() => onPatch({ closed: !selected.closed })}>{selected.closed ? labels.closed : labels.openSwitch}</button>}
      <button type="button" onClick={onRemove} aria-label={labels.deleteComponent} title={`${labels.deleteComponent} · Delete`}><Trash2 size={15} /></button>
    </div>
  </>;
}

export function ElectricalLabWorkspace() {
  const owner = useAuthStore(s => s.user?.id ?? "guest");
  const lang = useUIStore(s => s.lang);
  const theme = useUIStore(s => s.theme);
  const tr = useMemo(() => makePageT(lang, STRINGS), [lang]);
  const blankNameRef = useRef(tr("blank"));
  const allLabels = useMemo(() => Object.fromEntries(Object.keys(STRINGS.zh).map(key => [key, tr(key)])), [tr]);
  const [doc, setDoc] = useState(() => initialDocument(tr("blank")));
  const [loaded, setLoaded] = useState(false);
  const [railOpen, setRailOpen] = useState(false);
  const [instrumentOpen, setInstrumentOpen] = useState(false);
  const [instrumentPanel, setInstrumentPanel] = useState<InstrumentPanel>("meter");
  const [toolMode, setToolMode] = useState<ToolMode>("select");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [wireStart, setWireStart] = useState<TerminalRef | null>(null);
  const [past, setPast] = useState<ElectricalLabDocument[]>([]);
  const [future, setFuture] = useState<ElectricalLabDocument[]>([]);
  const [savedItems, setSavedItems] = useState<SavedElectricalLab[]>([]);
  const [activeSavedId, setActiveSavedId] = useState("");
  const [dirty, setDirty] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [pendingExperiment, setPendingExperiment] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [running, setRunning] = useState(false);
  const [traceFrame, setTraceFrame] = useState<SimulationFrame | null>(null);
  const [canvasSize, setCanvasSize] = useState({ width: 1000, height: 680 });
  const simState = useRef<SimulationState | null>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const importRef = useRef<HTMLInputElement>(null);

  useEffect(() => { blankNameRef.current = tr("blank"); }, [tr]);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const store = loadElectricalLabs(owner);
      const active = store.items.find(item => item.id === store.activeId) ?? store.items[0];
      setSavedItems(store.items); setActiveSavedId(active?.id ?? ""); setDoc(active?.document ?? initialDocument(blankNameRef.current)); setLoaded(true);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [owner]);
  // Layout, zoom, names and selection do not reset a running experiment.
  const simulationDocument = useMemo(() => ({ ...createElectricalLabDocument("simulation"), components: doc.components, wires: doc.wires, instruments: doc.instruments }), [doc.components, doc.wires, doc.instruments]);
  const dcFrame = useMemo(() => solveCircuit(simulationDocument, { mode: "dc", dt: .001, steps: 1 }), [simulationDocument]);
  const frame = traceFrame ?? dcFrame;
  useEffect(() => {
    if (!loaded) return;
    const timer = window.setTimeout(() => { simState.current = null; setTraceFrame(null); setRunning(false); }, 0);
    return () => window.clearTimeout(timer);
  }, [simulationDocument, loaded]);
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => {
      const next = solveCircuit(simulationDocument, { mode: "transient", dt: Math.max(1e-6, simulationDocument.instruments.scope.timePerDiv / 100), steps: 16 }, simState.current ?? undefined);
      simState.current = next.state;
      setTraceFrame(current => ({ ...next, samples: [...(current?.samples ?? []), ...next.samples].slice(-720) }));
    }, 80);
    return () => window.clearInterval(timer);
  }, [simulationDocument, running]);
  useEffect(() => {
    if (!loaded || !activeSavedId) return;
    const timer = window.setTimeout(() => {
      setSavedItems(items => { const next = items.map(item => item.id === activeSavedId ? { ...item, name: doc.name, document: doc, updatedAt: Date.now() } : item); persistElectricalLabs(owner, { version: 1, activeId: activeSavedId, items: next }); return next; }); setDirty(false);
    }, 450);
    return () => window.clearTimeout(timer);
  }, [doc, owner, activeSavedId, loaded]);
  useEffect(() => { if (!notice) return; const timer = window.setTimeout(() => setNotice(""), 2200); return () => window.clearTimeout(timer); }, [notice]);

  const apply = useCallback((command: ElectricalLabCommand) => {
    setDoc(current => {
      const next = reduceElectricalLabCommand(current, command);
      if (next === current) return current;
      setPast(stack => [...stack, current].slice(-80)); setFuture([]); setDirty(true);
      return next;
    });
  }, []);
  const openParts = () => { setRailOpen(v => !v); if (window.matchMedia("(max-width: 1100px)").matches) setInstrumentOpen(false); };
  const openInstruments = (panel: InstrumentPanel = instrumentPanel) => { setInstrumentPanel(panel); setInstrumentOpen(true); if (window.matchMedia("(max-width: 1100px)").matches) setRailOpen(false); };
  const updateView = useCallback((view: ElectricalLabDocument["view"]) => { setDoc(current => ({ ...current, view: { x: Math.max(-3500, Math.min(3500, view.x)), y: Math.max(-3500, Math.min(3500, view.y)), scale: Math.max(.35, Math.min(3, view.scale)) } })); setDirty(true); }, []);
  const fitDocument = (target: ElectricalLabDocument) => {
    const connectedPorts = Object.entries(INSTRUMENT_POINTS).filter(([ref]) => target.wires.some(wire => wire.from === ref || wire.to === ref)).map(([, point]) => point);
    const points = [...target.components, ...connectedPorts];
    if (!points.length) return { x: 0, y: 0, scale: 1 };
    const x0 = Math.min(...points.map(p => p.x)) - 95, x1 = Math.max(...points.map(p => p.x)) + 95;
    const y0 = Math.min(...points.map(p => p.y)) - 95, y1 = Math.max(...points.map(p => p.y)) + 110;
    const viewportScale = canvasSize.width < 700 ? 1 : Math.min(1, Math.max(.25, canvasSize.width / 1000));
    return { x: (x0 + x1) / 2 - 500, y: (y0 + y1) / 2 - 340, scale: Math.max(.35, Math.min(1.3, canvasSize.width / (x1 - x0) / viewportScale, canvasSize.height / (y1 - y0) / viewportScale)) };
  };
  const replaceDocument = (next: ElectricalLabDocument, fit = false) => { setPast(stack => [...stack, doc].slice(-80)); setFuture([]); setDoc(fit ? { ...next, view: fitDocument(next) } : next); setSelectedId(null); setWireStart(null); setTraceFrame(null); setPendingExperiment(null); setDirty(true); if (narrowScreen()) setRailOpen(false); };
  const addPart = (kind: ElectricalComponentKind, point?: Point) => {
    if (doc.components.length >= ELECTRICAL_LIMITS.components) { setNotice(tr("partLimit")); return; }
    let index = 1;
    while (doc.components.some(part => part.id === `${kind}_${index}`)) index++;
    const centre = { x: 500 + doc.view.x, y: 340 + doc.view.y };
    let position = point ?? centre;
    if (!point) {
      const candidates = [centre, ...Array.from({ length: 7 }, (_, row) => Array.from({ length: 7 }, (_, col) => ({ x: centre.x + (col - 3) * 140, y: centre.y + (row - 3) * 140 }))).flat()].sort((a, b) => Math.hypot(a.x - centre.x, a.y - centre.y) - Math.hypot(b.x - centre.x, b.y - centre.y));
      position = candidates.find(p => Math.abs(p.x - centre.x) < Math.max(100, canvasSize.width / doc.view.scale / 2 - 65) && Math.abs(p.y - centre.y) < Math.max(100, canvasSize.height / doc.view.scale / 2 - 85) && doc.components.every(c => Math.abs(c.x - p.x) >= 120 || Math.abs(c.y - p.y) >= 130)) ?? { x: centre.x + 20, y: centre.y + 20 };
    }
    const component = createElectricalComponent(kind, `${kind}_${index}`, Math.max(-3900, Math.min(3900, Math.round(position.x / 10) * 10)), Math.max(-3900, Math.min(3900, Math.round(position.y / 10) * 10)), `${COMPONENT_DEFAULTS[kind].prefix}${index}`);
    apply({ type: "add", component }); setSelectedId(component.id); setWireStart(null); if (narrowScreen()) setRailOpen(false);
  };
  const selected = doc.components.find(component => component.id === selectedId) ?? null;
  const selectedWire = doc.wires.find(wire => wire.id === selectedId) ?? null;
  const removeById = (id: string) => { if (doc.components.some(component => component.id === id) || doc.wires.some(wire => wire.id === id)) { apply({ type: "remove", id }); setSelectedId(null); setWireStart(null); } };
  const removeSelected = () => { if (selectedId) removeById(selectedId); };
  const patchSelected = (patch: Partial<ElectricalComponent>) => { if (selected) apply({ type: "update", id: selected.id, patch }); };
  const toggleSwitch = (id: string) => { const c = doc.components.find(part => part.id === id); if (c?.kind === "switch") apply({ type: "update", id, patch: { closed: !c.closed } }); };
  const undo = () => { const previous = past.at(-1); if (!previous) return; setFuture(stack => [doc, ...stack]); setPast(stack => stack.slice(0, -1)); setDoc(previous); setSelectedId(null); setWireStart(null); setDirty(true); };
  const redo = () => { const next = future[0]; if (!next) return; setPast(stack => [...stack, doc]); setFuture(stack => stack.slice(1)); setDoc(next); setSelectedId(null); setWireStart(null); setDirty(true); };
  const terminal = (ref: TerminalRef) => {
    if (toolMode === "delete") {
      const id = ref.split(":")[0]!;
      if (doc.components.some(component => component.id === id)) removeById(id);
      else apply({ type: "network", components: doc.components, wires: doc.wires.filter(wire => wire.from !== ref && wire.to !== ref) });
      return;
    }
    setToolMode("wire");
    if (!wireStart) { setWireStart(ref); return; }
    if (wireStart === ref) { setWireStart(null); return; }
    if (doc.wires.some(wire => (wire.from === wireStart && wire.to === ref) || (wire.from === ref && wire.to === wireStart))) { setWireStart(null); setNotice(tr("wireDuplicate")); return; }
    if (doc.wires.length >= ELECTRICAL_LIMITS.wires) { setWireStart(null); setNotice(tr("wireLimit")); return; }
    apply({ type: "connect", wire: { id: `wire_${Date.now()}_${doc.wires.length}`, from: wireStart, to: ref, color: ["#57c9c0", "#e96951", "#a38bdd"][doc.wires.length % 3]!, bends: [] } }); setWireStart(null); setNotice(tr("wireDone"));
  };
  const chooseTool = (mode: ToolMode) => { setToolMode(mode); setSelectedId(null); if (mode !== "wire") setWireStart(null); };
  const junctionAt = (point: Point, grid = 10): ElectricalComponent => {
    let index = 1;
    while (doc.components.some(component => component.id === `junction_${index}`) || doc.wires.some(wire => wire.id === `junction_${index}`)) index++;
    return createElectricalComponent("junction", `junction_${index}`, Math.max(-3900, Math.min(3900, Math.round(point.x / grid) * grid)), Math.max(-3900, Math.min(3900, Math.round(point.y / grid) * grid)), `J${index}`);
  };
  const commitNodeDocument = (next: ElectricalLabDocument, node: ElectricalComponent, start: TerminalRef | null = `${node.id}:p`) => {
    apply({ type: "network", components: next.components, wires: next.wires });
    setSelectedId(node.id); setWireStart(start); setToolMode("wire");
  };
  const addWireNode = (point: Point) => {
    if (doc.components.length >= ELECTRICAL_LIMITS.components) { setNotice(tr("partLimit")); return; }
    if (wireStart && doc.wires.length >= ELECTRICAL_LIMITS.wires) { setNotice(tr("wireLimit")); return; }
    const node = junctionAt(point);
    const nextWires = wireStart ? [...doc.wires, { id: `wire_${Date.now()}_${doc.wires.length}`, from: wireStart, to: `${node.id}:p`, color: ["#57c9c0", "#e96951", "#a38bdd"][doc.wires.length % 3]!, bends: [] }] : doc.wires;
    commitNodeDocument({ ...doc, components: [...doc.components, node], wires: nextWires }, node);
    setNotice(tr("nodeAdded"));
  };
  const insertNodeOnWire = (wireId: string, point: Point) => {
    if (doc.components.length >= ELECTRICAL_LIMITS.components) { setNotice(tr("partLimit")); return; }
    const wire = doc.wires.find(item => item.id === wireId);
    if (!wire) return;
    const from = terminalPosition(doc, wire.from); const to = terminalPosition(doc, wire.to);
    if (!from || !to) return;
    if (sameCircuitPoint(point, from)) { terminal(wire.from); return; }
    if (sameCircuitPoint(point, to)) { terminal(wire.to); return; }
    if (doc.wires.length + (wireStart ? 2 : 1) > ELECTRICAL_LIMITS.wires) { setNotice(tr("wireLimit")); return; }
    const node = junctionAt(point, 1);
    const path = wirePoints(doc, wire);
    if (!path) return;
    try {
      const next = spliceElectricalWire(doc, wireId, node, path);
      if (wireStart) next.wires.push({ id: `wire_${Date.now()}_${next.wires.length}`, from: wireStart, to: `${node.id}:p`, color: "#57c9c0", bends: [] });
      commitNodeDocument(next, node, wireStart ? null : `${node.id}:p`); setNotice(tr(wireStart ? "wireDone" : "nodeAdded"));
    } catch { setNotice(tr("wireInvalid")); }
  };
  const movePart = (id: string, point: Point) => { setDoc(current => reduceElectricalLabCommand(current, { type: "update", id, patch: { x: Math.max(-3900, Math.min(3900, point.x)), y: Math.max(-3900, Math.min(3900, point.y)) } })); setDirty(true); };
  const save = () => {
    const now = Date.now(), id = activeSavedId || `lab_${now}`;
    const item: SavedElectricalLab = { id, name: doc.name, createdAt: savedItems.find(row => row.id === id)?.createdAt ?? now, updatedAt: now, document: { ...doc, id } };
    const items = [item, ...savedItems.filter(row => row.id !== id)].slice(0, 24);
    setSavedItems(items); setActiveSavedId(id); setDoc(item.document); persistElectricalLabs(owner, { version: 1, activeId: id, items }); setDirty(false); setNotice(tr("saved"));
  };
  const loadExperiment = (id: string, confirmed = false) => {
    const experiment = experimentById(id); if (!experiment) return;
    if (doc.components.length && !confirmed) { setPendingExperiment(id); return; }
    const next = experiment.build(); next.name = experiment.title[lang]; replaceDocument(next, true);
  };
  const loadSaved = (item: SavedElectricalLab) => { replaceDocument(item.document); setActiveSavedId(item.id); persistElectricalLabs(owner, { version: 1, activeId: item.id, items: savedItems }); };
  const rename = () => { apply({ type: "rename", name: clampName(nameDraft, tr("blank")) }); setRenameOpen(false); };
  const download = () => { try { const url = URL.createObjectURL(exportElectricalLab(doc)); const a = window.document.createElement("a"); a.href = url; a.download = `${doc.name.replace(/[^\w\-\u4e00-\u9fff]+/g, "-") || "circuit"}.json`; a.click(); URL.revokeObjectURL(url); } catch { setNotice(tr("exportFailed")); } };
  const readImport = async (event: React.ChangeEvent<HTMLInputElement>) => { const file = event.target.files?.[0]; event.target.value = ""; if (!file) return; try { const imported = await importElectricalLab(file); replaceDocument(imported, true); setNotice(tr("imported")); } catch { setNotice(tr("importFailed")); } };
  const toggleFullscreen = () => { if (document.fullscreenElement) void document.exitFullscreen(); else void stageRef.current?.requestFullscreen(); };
  const runSingle = () => { const next = solveCircuit(simulationDocument, { mode: "transient", dt: Math.max(1e-6, doc.instruments.scope.timePerDiv / 100), steps: 240 }); setTraceFrame(next); simState.current = next.state; };

  const onKeys = (event: React.KeyboardEvent) => {
    if ((event.target as HTMLElement).closest("input, textarea, select, [contenteditable=true]")) return;
    if (event.key === "Escape") { setWireStart(null); setRenameOpen(false); setSelectedId(null); }
    if (!event.ctrlKey && !event.metaKey && !event.altKey) {
      if (event.key.toLowerCase() === "v") { event.preventDefault(); chooseTool("select"); }
      if (event.key.toLowerCase() === "w") { event.preventDefault(); chooseTool("wire"); }
      if (event.key.toLowerCase() === "e") { event.preventDefault(); chooseTool("delete"); }
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") { event.preventDefault(); if (event.shiftKey) redo(); else undo(); }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "y") { event.preventDefault(); redo(); }
    if ((event.key === "Delete" || event.key === "Backspace") && selectedId) { event.preventDefault(); removeSelected(); }
    if (event.key.toLowerCase() === "r" && selected && !event.ctrlKey && !event.metaKey) { event.preventDefault(); patchSelected({ rotation: (selected.rotation + 90) % 360 }); }
  };
  return <div ref={stageRef} className={`electrical-lab ${theme === "light" ? "light" : ""} ${railOpen ? "has-parts" : ""} ${instrumentOpen ? "has-measurement" : ""}`} data-testid="electrical-lab" onKeyDown={onKeys}>
    <header className="electrical-topbar">
      <Link href="/tools" className="electrical-icon-btn" aria-label={tr("back")} title={tr("back")}><ArrowLeft size={17} /></Link>
      <Zap size={20} className="electrical-brand-mark" />
      <div className="electrical-title-block">{renameOpen ? <form className="electrical-inline-rename" onSubmit={event => { event.preventDefault(); rename(); }}><input autoFocus aria-label={tr("namePlaceholder")} value={nameDraft} maxLength={80} onChange={event => setNameDraft(event.target.value)} onKeyDown={event => { if (event.key === "Escape") setRenameOpen(false); }} /><button type="submit" aria-label={tr("save")}><Check size={16} /></button><button type="button" onClick={() => setRenameOpen(false)} aria-label={tr("cancel")}><X size={16} /></button></form> : <button type="button" className="electrical-title-button" aria-label={tr("rename")} title={tr("rename")} onClick={() => { setNameDraft(doc.name); setRenameOpen(true); }}><h1>{doc.name}</h1><span className="lab-caption">{tr("title")}</span></button>}</div>
      <div className="electrical-top-spacer" /><span className={`electrical-save-dot ${dirty ? "unsaved" : ""}`}>{dirty ? tr("unsaved") : tr("saved")}</span>
      <button type="button" className={`electrical-icon-btn ${railOpen ? "active" : ""}`} onClick={openParts} aria-label={tr("addPart")} aria-expanded={railOpen} title={tr("addPart")}><Plus size={17} /><span className="electrical-button-label">{tr("addPart")}</span></button>
      <button type="button" className={`electrical-icon-btn ${instrumentOpen ? "active" : ""}`} onClick={() => { if (instrumentOpen) setInstrumentOpen(false); else openInstruments(); }} aria-label={instrumentOpen ? tr("closeMeasure") : tr("openMeasure")} aria-expanded={instrumentOpen} title={tr("measure")}><Gauge size={17} /><span className="electrical-button-label">{tr("measure")}</span></button>
      <button type="button" className="electrical-icon-btn primary" onClick={save} aria-label={tr("save")} title={tr("save")}><Save size={16} /><span className="electrical-button-label">{tr("save")}</span></button>
      <button type="button" className="electrical-icon-btn electrical-secondary-action" onClick={download} aria-label={tr("export")} title={tr("export")}><Download size={16} /></button>
      <button type="button" className="electrical-icon-btn" onClick={toggleFullscreen} aria-label={tr("fullscreen")} title={tr("fullscreen")}><Expand size={16} /></button>
    </header>
    <div className="electrical-workbench">
      <aside className={`electrical-rail ${railOpen ? "" : "collapsed"}`} aria-label={tr("rail")}>
        <button type="button" className="electrical-rail-header" onClick={openParts} aria-label={railOpen ? tr("collapse") : tr("expand")} aria-expanded={railOpen} title={railOpen ? tr("collapse") : tr("expand")}><Plus size={18} /><span>{tr("rail")}</span>{railOpen ? <ChevronLeft size={16} /> : <ChevronRight size={16} />}</button>
        {railOpen && <div className="electrical-rail-body">
          <section className="electrical-rail-section"><h2>{tr("parts")}<span>{tr("clickOrDrag")}</span></h2><div className="electrical-part-grid">{PARTS.map(kind => <button type="button" className="electrical-part-button" key={kind} draggable onDragStart={event => startPartDrag(event, kind)} onClick={() => addPart(kind)}><PartGlyph kind={kind} /><strong>{tr(PART_LABEL[kind] ?? kind)}</strong><small>{COMPONENT_DEFAULTS[kind].prefix}</small></button>)}</div></section>
          <section className="electrical-rail-section"><h2>{tr("experiments")}</h2>{ELECTRICAL_EXPERIMENTS.map(experiment => <button type="button" key={experiment.id} className="electrical-experiment-button" onClick={() => loadExperiment(experiment.id)}><strong>{experiment.title[lang]}</strong><small>{experiment.description[lang]}</small></button>)}
            {pendingExperiment && <div className="electrical-replace-confirm"><p>{tr("replaceConfirm")}</p><button type="button" onClick={() => loadExperiment(pendingExperiment, true)}>{tr("yes")}</button><button type="button" onClick={() => setPendingExperiment(null)}>{tr("cancel")}</button></div>}
          </section>
          <section className="electrical-rail-section"><h2>{tr("savedLabs")}</h2><div className="electrical-library-actions"><button type="button" onClick={() => { replaceDocument(initialDocument(tr("blank"))); setActiveSavedId(""); }}>{tr("newExperiment")}</button><button type="button" onClick={() => importRef.current?.click()}><FileUp size={14} />{tr("import")}</button><button type="button" onClick={download} aria-label={tr("export")}><Download size={14} /></button></div>
            <input ref={importRef} type="file" accept="application/json,.json" className="electrical-import-input" onChange={readImport} />
            {savedItems.length ? savedItems.slice(0, 8).map(item => <div className="electrical-saved-row" key={item.id}><button type="button" onClick={() => loadSaved(item)}><strong>{item.name}</strong><small>{new Date(item.updatedAt).toLocaleString(lang === "zh" ? "zh-CN" : "en-US")}</small></button><button type="button" aria-label={tr("delete")} onClick={() => { const items = savedItems.filter(row => row.id !== item.id); setSavedItems(items); persistElectricalLabs(owner, { version: 1, activeId: activeSavedId === item.id ? "" : activeSavedId, items }); if (activeSavedId === item.id) setActiveSavedId(""); }}><Trash2 size={14} /></button></div>) : <p className="electrical-rail-note">{tr("emptySaved")}</p>}
          </section><p className="electrical-rail-note">{tr("savedLocal")}</p>
        </div>}
      </aside>
      <main className="electrical-stage-wrap">
        <div className="electrical-quick-tray" aria-label={tr("quickParts")}>{QUICK_PARTS.map(kind => <button type="button" key={kind} draggable onDragStart={event => startPartDrag(event, kind)} onClick={() => addPart(kind)} aria-label={tr(PART_LABEL[kind] ?? kind)} title={`${tr(PART_LABEL[kind] ?? kind)} · ${tr("clickOrDrag")}`}><PartGlyph kind={kind} /><span>{tr(PART_LABEL[kind] ?? kind)}</span></button>)}<button type="button" className="electrical-all-parts" onClick={openParts} aria-label={tr("allParts")}><Plus size={16} /><span>{tr("allParts")}</span></button></div>
        <div className="electrical-selection-bar" aria-label={tr("selection")}>
          <div className="electrical-mode-bar" role="toolbar" aria-label={tr("modeBar")}>{MODES.map(({ id, label, icon: Icon }) => <button type="button" key={id} className={toolMode === id ? "active" : ""} aria-pressed={toolMode === id} onClick={() => chooseTool(id)} title={`${tr(label)} · ${id === "select" ? "V" : id === "wire" ? "W" : "E"}`}><Icon size={15} /><span>{tr(label)}</span></button>)}</div>
          <span className="electrical-mode-divider" />
          {wireStart ? <><span className="electrical-wiring-dot" /><span className="electrical-selection-hint">{tr("chooseWire")}</span><button type="button" onClick={() => setWireStart(null)} aria-label={tr("stopWire")}><X size={16} /><span>{tr("cancel")}</span></button></> : selected ? <PartControls key={selected.id} selected={selected} labels={allLabels} onPatch={patchSelected} onRemove={removeSelected} /> : selectedWire ? <><span className="electrical-selection-hint">{tr("wire")} · {selectedWire.from} → {selectedWire.to}</span><button type="button" onClick={removeSelected} aria-label={tr("deleteWire")}><Trash2 size={16} /><span>{tr("delete")}</span></button></> : <><Hand size={15} /><span className="electrical-selection-hint">{toolMode === "wire" ? tr("wireHint") : toolMode === "delete" ? tr("deleteHint") : tr("operateHint")}</span></>}
        </div>
        <div className="electrical-stage"><CircuitStage document={doc} frame={frame} selectedId={selectedId} wireStart={wireStart} toolMode={toolMode} onSelect={setSelectedId} onDelete={removeById} onWireInsert={insertNodeOnWire} onWireBlank={addWireNode} onMove={movePart} onMoveStart={() => { setPast(stack => [...stack, doc].slice(-80)); setFuture([]); }} onView={updateView} onResize={setCanvasSize} onAdd={addPart} onToggleSwitch={toggleSwitch} onTerminal={terminal} onBackground={() => { setSelectedId(null); setWireStart(null); }} labels={allLabels} /></div>
        <footer className="electrical-statusbar"><div className="electrical-undo-bar"><button type="button" disabled={!past.length} onClick={undo} aria-label={tr("undo")} title={`${tr("undo")} · Ctrl Z`}><Undo2 size={16} /></button><button type="button" disabled={!future.length} onClick={redo} aria-label={tr("redo")} title={`${tr("redo")} · Ctrl Shift Z`}><Redo2 size={16} /></button><button type="button" onClick={() => replaceDocument(initialDocument(tr("blank")))} aria-label={tr("reset")} title={tr("reset")}><RotateCcw size={15} /></button></div>
          <span className="electrical-status-message" role="status" aria-live="polite">{notice || `${doc.components.length} ${tr("parts")} · ${doc.wires.length} ${tr("branches")}`}</span>
          <div className="electrical-view-controls"><button type="button" onClick={() => updateView({ ...doc.view, scale: doc.view.scale / 1.2 })} aria-label={tr("zoomOut")} title={tr("zoomOut")}><ZoomOut size={16} /></button><output>{Math.round(doc.view.scale * 100)}%</output><button type="button" onClick={() => updateView({ ...doc.view, scale: doc.view.scale * 1.2 })} aria-label={tr("zoomIn")} title={tr("zoomIn")}><ZoomIn size={16} /></button><button type="button" onClick={() => updateView(fitDocument(doc))} aria-label={tr("resetView")} title={tr("resetView")}><LocateFixed size={16} /></button></div>
        </footer>
      </main>
      <aside className={`electrical-instrument-dock ${instrumentOpen ? "open" : "collapsed"}`} aria-label={tr("instrumentDock")}>
        {instrumentOpen ? <><header className="electrical-dock-header"><Gauge size={17} /><h2>{tr("instrumentDock")}</h2><button type="button" onClick={() => setInstrumentOpen(false)} aria-label={tr("closeMeasure")} title={tr("closeMeasure")}><PanelRightClose size={18} /></button></header><div className="electrical-dock-tabs" role="tablist" aria-label={tr("instrumentDock")}>{PANELS.map(({ id, label, icon: Icon }) => <button type="button" key={id} role="tab" aria-selected={instrumentPanel === id} aria-controls={`electrical-panel-${id}`} id={`electrical-tab-${id}`} onClick={() => setInstrumentPanel(id)} onKeyDown={event => { if (event.key === "ArrowRight" || event.key === "ArrowLeft") { event.preventDefault(); const index = PANELS.findIndex(p => p.id === id); const next = PANELS[(index + (event.key === "ArrowRight" ? 1 : 2)) % 3]!; setInstrumentPanel(next.id); window.document.getElementById(`electrical-tab-${next.id}`)?.focus(); } }} tabIndex={instrumentPanel === id ? 0 : -1}><Icon size={15} />{tr(label)}</button>)}</div>
          <div className="electrical-dock-body" role="tabpanel" id={`electrical-panel-${instrumentPanel}`} aria-labelledby={`electrical-tab-${instrumentPanel}`}>
            <InstrumentDeck panel={instrumentPanel} instruments={doc.instruments} frame={frame} labels={allLabels} onChange={instruments => apply({ type: "instrument", instruments })} running={running} onRun={() => { simState.current = null; setTraceFrame(null); setRunning(true); }} onPause={() => setRunning(false)} onSingle={runSingle} onClear={() => { setTraceFrame(null); simState.current = null; setRunning(false); }} wireStart={wireStart} onTerminal={terminal} />
            {instrumentPanel === "meter" && <AnalysisStrip frame={frame} selected={selected} formula={experimentById(doc.experimentId)?.formula ?? null} labels={allLabels} />}
            <p className="electrical-probe-hint">{tr("probeHint")}</p>
          </div></> : <div className="electrical-dock-shortcuts">{PANELS.map(({ id, label, icon: Icon }) => <button type="button" key={id} onClick={() => openInstruments(id)} aria-label={tr(label)} title={tr(label)}><Icon size={19} /></button>)}<span>{tr("measure")}</span></div>}
      </aside>
    </div>
  </div>;
}
