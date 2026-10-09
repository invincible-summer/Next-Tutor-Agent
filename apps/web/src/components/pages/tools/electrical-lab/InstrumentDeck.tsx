"use client";

import { useRef } from "react";
import { Power, Radio, Gauge, Activity, Play, Pause, CircleStop, Volume2, X } from "lucide-react";
import { formatElectrical, type ElectricalInstrumentState, type MeterMode, type SimulationFrame } from "@next-tutor/domain";
import { ScopeDisplay } from "./ScopeDisplay";
import { PartGlyph } from "./PartGlyph";

export type InstrumentPanel = "meter" | "scope" | "sources";
interface Props {
  panel: InstrumentPanel;
  instruments: ElectricalInstrumentState;
  frame: SimulationFrame;
  labels: Record<string, string>;
  onChange: (next: ElectricalInstrumentState) => void;
  running: boolean;
  onRun: () => void;
  onPause: () => void;
  onSingle: () => void;
  onClear: () => void;
  wireStart: string | null;
  onTerminal: (terminal: string) => void;
}
const update = <T extends keyof ElectricalInstrumentState>(state: ElectricalInstrumentState, key: T, patch: Partial<ElectricalInstrumentState[T]>): ElectricalInstrumentState => ({ ...state, [key]: { ...state[key], ...patch } });
const bounded = (raw: string, min: number, max: number, previous: number) => raw.trim() && Number.isFinite(Number(raw)) ? Math.max(min, Math.min(max, Number(raw))) : previous;
const METER_MODES: MeterMode[] = ["voltage", "current", "resistance", "conductance", "continuity", "diode"];
const METER_SYMBOLS = { voltage: "V", current: "A", resistance: "Ω", conductance: "S", continuity: <Volume2 size={18} />, diode: <PartGlyph kind="diode" /> };

function InstrumentPorts({ refs, wireStart, onTerminal }: { refs: string[]; wireStart: string | null; onTerminal: (terminal: string) => void }) {
  const pressed = useRef<{ from: string; started: boolean } | null>(null);
  return <div className="instrument-ports">{refs.map(ref => <button type="button" key={ref} data-terminal={ref} className={wireStart === ref ? "active" : ""} aria-label={`Terminal ${ref}`} title={ref}
    onPointerDown={event => { event.stopPropagation(); pressed.current = { from: ref, started: !wireStart }; onTerminal(ref); event.currentTarget.setPointerCapture(event.pointerId); }}
    onPointerUp={event => {
      event.stopPropagation();
      const target = window.document.elementFromPoint(event.clientX, event.clientY)?.closest("[data-terminal]")?.getAttribute("data-terminal");
      if (pressed.current?.started && target && target !== pressed.current.from) onTerminal(target);
      pressed.current = null;
      if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    }} onPointerCancel={() => { pressed.current = null; }}
    onClick={event => { if (event.detail === 0) onTerminal(ref); }} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onTerminal(ref); } }}>
    <span className={`instrument-port-ring port-${ref.split(":")[1]}`} />{({ p: "+", n: "−", a: "A", b: "B", g: "G" } as Record<string, string>)[ref.split(":")[1]!]}
  </button>)}</div>;
}

export function InstrumentDeck({ panel, instruments, frame, labels, onChange, running, onRun, onPause, onSingle, onClear, wireStart, onTerminal }: Props) {
  const { supply, generator, meter, scope } = instruments;
  return <div className="electrical-instruments">
    {panel === "sources" && <>
      <section className="electrical-instrument instrument-supply">
        <header><Power size={16} /><h3>{labels.supply}</h3><span className={`instrument-led ${supply.enabled ? "on" : ""}`} /></header>
        <div className="instrument-display"><span>DC OUTPUT</span><strong>{supply.enabled ? `${supply.voltage.toFixed(2)} V` : "OFF"}</strong></div>
        <div className="instrument-controls">
          <label>{labels.voltage}<input type="range" min="0" max="30" step=".1" value={supply.voltage} onChange={event => onChange(update(instruments, "supply", { voltage: Number(event.target.value) }))} /><output>{formatElectrical(supply.voltage, "V")}</output></label>
          <label>{labels.currentLimit}<input type="range" min=".01" max="3" step=".01" value={supply.currentLimit} onChange={event => onChange(update(instruments, "supply", { currentLimit: Number(event.target.value) }))} /><output>{formatElectrical(supply.currentLimit, "A")}</output></label>
        </div>
        <footer className="instrument-connectors"><InstrumentPorts refs={["supply:p", "supply:n"]} wireStart={wireStart} onTerminal={onTerminal} /><button type="button" className={`instrument-toggle ${supply.enabled ? "active" : ""}`} aria-pressed={supply.enabled} onClick={() => onChange(update(instruments, "supply", { enabled: !supply.enabled }))}><Power size={14} />{labels.power}</button></footer>
      </section>
      <section className="electrical-instrument instrument-generator">
        <header><Radio size={16} /><h3>{labels.generator}</h3><span className={`instrument-led ${generator.enabled ? "on" : ""}`} /></header>
        <div className="instrument-display"><span>FUNCTION</span><strong>{generator.enabled ? formatElectrical(generator.frequency, "Hz") : "OFF"}</strong></div>
        <div className="generator-fields">
          <label>{labels.waveform}<select value={generator.waveform} onChange={event => onChange(update(instruments, "generator", { waveform: event.target.value as typeof generator.waveform }))}><option value="sine">{labels.sine}</option><option value="square">{labels.square}</option><option value="triangle">{labels.triangle}</option></select></label>
          <label>{labels.frequency} · Hz<input type="number" min=".1" max="10000" value={generator.frequency} onChange={event => onChange(update(instruments, "generator", { frequency: bounded(event.target.value, .1, 10000, generator.frequency) }))} /></label>
          <label>{labels.amplitude} · V<input type="number" min="0" max="15" step=".1" value={generator.amplitude} onChange={event => onChange(update(instruments, "generator", { amplitude: bounded(event.target.value, 0, 15, generator.amplitude) }))} /></label>
          <label>{labels.offset} · V<input type="number" min="-15" max="15" step=".1" value={generator.offset} onChange={event => onChange(update(instruments, "generator", { offset: bounded(event.target.value, -15, 15, generator.offset) }))} /></label>
          {generator.waveform === "square" && <label>{labels.duty}<input type="number" min=".05" max=".95" step=".05" value={generator.duty} onChange={event => onChange(update(instruments, "generator", { duty: bounded(event.target.value, .05, .95, generator.duty) }))} /></label>}
        </div>
        <footer className="instrument-connectors"><InstrumentPorts refs={["generator:p", "generator:n"]} wireStart={wireStart} onTerminal={onTerminal} /><button type="button" className={`instrument-toggle ${generator.enabled ? "active" : ""}`} aria-pressed={generator.enabled} onClick={() => onChange(update(instruments, "generator", { enabled: !generator.enabled }))}><Power size={14} />{labels.power}</button></footer>
      </section>
    </>}
    {panel === "meter" && <section className="electrical-instrument instrument-meter">
      <header><Gauge size={16} /><h3>{labels.meter}</h3><span className={`meter-status ${frame.meter.status}`}>{frame.meter.status === "ready" ? labels.connected : frame.meter.status === "powered" ? labels.powered : frame.meter.status === "overload" ? labels.overload : labels.open}</span></header>
      <div className="instrument-display meter-display"><span>{labels[meter.mode]}</span><strong>{frame.meter.value == null ? "—" : formatElectrical(frame.meter.value, frame.meter.unit)}</strong></div>
      <div className="meter-modes" role="group" aria-label={labels.mode}>{METER_MODES.map(mode => <button type="button" key={mode} aria-pressed={meter.mode === mode} title={labels[mode]} onClick={() => onChange(update(instruments, "meter", { mode }))}><span>{METER_SYMBOLS[mode]}</span><small>{labels[mode]}</small></button>)}</div>
      <footer className="instrument-connectors"><InstrumentPorts refs={["meter:p", "meter:n"]} wireStart={wireStart} onTerminal={onTerminal} /><span className="instrument-port-caption">V / Ω / A</span></footer>
    </section>}
    {panel === "scope" && <section className="electrical-instrument instrument-scope">
      <header><Activity size={16} /><h3>{labels.scope}</h3><span className={`instrument-led ${running ? "on" : ""}`} /></header>
      <ScopeDisplay frame={frame} labels={labels} voltsA={scope.voltsPerDivA} voltsB={scope.voltsPerDivB} trigger={scope.triggerLevel} cursors={scope.cursors} />
      <div className="scope-actions"><button type="button" className={running ? "active" : ""} aria-label={running ? labels.pause : labels.run} onClick={running ? onPause : onRun}>{running ? <Pause size={15} /> : <Play size={15} />}{running ? labels.pause : labels.run}</button><button type="button" aria-label={labels.single} onClick={onSingle}><CircleStop size={15} />{labels.single}</button><button type="button" aria-label={labels.clear} onClick={onClear}><X size={15} /></button></div>
      <div className="scope-options">
        <label>{labels.timebase} · s<input type="number" min=".000001" max="10" step=".001" value={scope.timePerDiv} onChange={event => onChange(update(instruments, "scope", { timePerDiv: bounded(event.target.value, 1e-6, 10, scope.timePerDiv) }))} /></label>
        <label>{labels.voltsDivA} · V<input type="number" min=".001" max="100" step=".1" value={scope.voltsPerDivA} onChange={event => onChange(update(instruments, "scope", { voltsPerDivA: bounded(event.target.value, .001, 100, scope.voltsPerDivA) }))} /></label>
        <label>{labels.voltsDivB} · V<input type="number" min=".001" max="100" step=".1" value={scope.voltsPerDivB} onChange={event => onChange(update(instruments, "scope", { voltsPerDivB: bounded(event.target.value, .001, 100, scope.voltsPerDivB) }))} /></label>
      </div>
      <details className="instrument-advanced"><summary>{labels.scopeSettings}</summary><div className="scope-options">
        <label>{labels.trigger} · V<input type="number" min="-30" max="30" step=".1" value={scope.triggerLevel} onChange={event => onChange(update(instruments, "scope", { triggerLevel: bounded(event.target.value, -30, 30, scope.triggerLevel) }))} /></label>
        <label>{labels.triggerEdge}<select value={scope.triggerEdge} onChange={event => onChange(update(instruments, "scope", { triggerEdge: event.target.value as typeof scope.triggerEdge }))}><option value="rising">{labels.rising}</option><option value="falling">{labels.falling}</option></select></label>
        <label>{labels.coupling}<select value={scope.coupling} onChange={event => onChange(update(instruments, "scope", { coupling: event.target.value as typeof scope.coupling }))}><option value="dc">{labels.dc}</option><option value="ac">{labels.ac}</option></select></label>
        <label className="scope-check"><input type="checkbox" checked={scope.cursors} onChange={event => onChange(update(instruments, "scope", { cursors: event.target.checked }))} />{labels.cursors}</label>
      </div></details>
      <footer className="instrument-connectors"><InstrumentPorts refs={["scope:a", "scope:b", "scope:g"]} wireStart={wireStart} onTerminal={onTerminal} /><span className="instrument-port-caption">A / B / GND</span></footer>
    </section>}
  </div>;
}
