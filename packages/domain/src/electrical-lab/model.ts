/** Independent, platform-free circuit workbench model. All quantities use SI. */
export type ElectricalComponentKind = "resistor" | "conductance" | "potentiometer" | "switch" | "capacitor" | "inductor" | "bulb" | "diode" | "led" | "npn" | "pnp" | "ground" | "junction" | "voltage" | "current";
export type MeterMode = "voltage" | "current" | "resistance" | "conductance" | "continuity" | "diode";
export type Waveform = "sine" | "square" | "triangle";
export interface Point { x: number; y: number }
export interface ElectricalComponent extends Point {
  id: string;
  kind: ElectricalComponentKind;
  label: string;
  rotation: number;
  value: number;
  position: number;
  closed: boolean;
}
export interface ElectricalWire { id: string; from: string; to: string; color: string; bends: Point[] }
export interface ElectricalInstrumentState {
  supply: { enabled: boolean; voltage: number; currentLimit: number };
  generator: { enabled: boolean; waveform: Waveform; frequency: number; amplitude: number; offset: number; duty: number };
  meter: { mode: MeterMode; range: number };
  scope: { timePerDiv: number; voltsPerDivA: number; voltsPerDivB: number; coupling: "dc" | "ac"; triggerLevel: number; triggerEdge: "rising" | "falling"; cursors: boolean };
}
export interface ElectricalLabDocument {
  schemaVersion: 1;
  id: string;
  name: string;
  components: ElectricalComponent[];
  wires: ElectricalWire[];
  instruments: ElectricalInstrumentState;
  view: { x: number; y: number; scale: number };
  experimentId: string | null;
}
export interface SimulationSettings { mode: "dc" | "transient"; dt: number; steps: number }
export interface CircuitDiagnostic { code: "floating" | "short_circuit" | "current_limit" | "singular" | "non_convergence" | "overload" | "probe_missing" | "powered_ohmmeter" | "undersampled"; severity: "info" | "warning" | "error"; componentId: string | null }
export interface BranchReading { voltage: number; current: number; power: number; conductance: number; brightness: number; terminalCurrents: Record<string, number> }
export interface MeterReading { value: number | null; unit: string; status: "ready" | "open" | "overload" | "powered"; continuity: boolean }
export interface SimulationState { time: number; capacitorVoltages: Record<string, number>; inductorCurrents: Record<string, number>; bulbTemperatures: Record<string, number>; guess: Record<string, number> }
export interface SimulationFrame {
  time: number;
  valid: boolean;
  nodes: Record<string, number>;
  branches: Record<string, BranchReading>;
  diagnostics: CircuitDiagnostic[];
  meter: MeterReading;
  scope: { a: number | null; b: number | null };
  samples: { time: number; a: number | null; b: number | null }[];
  state: SimulationState;
}
export interface CalculationCard { title: string; formula: string; value: number; unit: string }
export type ElectricalLabCommand =
  | { type: "add"; component: ElectricalComponent }
  | { type: "update"; id: string; patch: Partial<Pick<ElectricalComponent, "x" | "y" | "rotation" | "value" | "position" | "closed" | "label">> }
  | { type: "remove"; id: string }
  | { type: "connect"; wire: ElectricalWire }
  | { type: "network"; components: ElectricalComponent[]; wires: ElectricalWire[] }
  | { type: "instrument"; instruments: ElectricalInstrumentState }
  | { type: "rename"; name: string }
  | { type: "view"; view: ElectricalLabDocument["view"] };

export const COMPONENT_KINDS: readonly ElectricalComponentKind[] = ["resistor", "conductance", "potentiometer", "switch", "capacitor", "inductor", "bulb", "diode", "led", "npn", "pnp", "ground", "junction", "voltage", "current"];
export const COMPONENT_DEFAULTS: Record<ElectricalComponentKind, { value: number; prefix: string; unit: string; min: number; max: number }> = {
  resistor: { value: 1000, prefix: "R", unit: "Ω", min: .01, max: 1e9 },
  conductance: { value: .001, prefix: "G", unit: "S", min: 1e-9, max: 100 },
  potentiometer: { value: 10000, prefix: "VR", unit: "Ω", min: 1, max: 1e8 },
  switch: { value: .01, prefix: "S", unit: "Ω", min: .001, max: 1 },
  capacitor: { value: 100e-6, prefix: "C", unit: "F", min: 1e-12, max: 1 },
  inductor: { value: .01, prefix: "L", unit: "H", min: 1e-6, max: 100 },
  bulb: { value: 1, prefix: "LP", unit: "W", min: .01, max: 100 },
  diode: { value: 1, prefix: "D", unit: "", min: .5, max: 2 },
  led: { value: 1.8, prefix: "LED", unit: "V", min: 1.2, max: 3.5 },
  npn: { value: 100, prefix: "Q", unit: "β", min: 5, max: 500 },
  pnp: { value: 100, prefix: "Q", unit: "β", min: 5, max: 500 },
  ground: { value: 0, prefix: "GND", unit: "", min: 0, max: 0 },
  junction: { value: 0, prefix: "J", unit: "", min: 0, max: 0 },
  voltage: { value: 5, prefix: "V", unit: "V", min: -30, max: 30 },
  current: { value: .01, prefix: "I", unit: "A", min: -3, max: 3 },
};
export const INSTRUMENT_TERMINALS: Record<string, readonly string[]> = { supply: ["p", "n"], generator: ["p", "n"], meter: ["p", "n"], scope: ["a", "b", "g"] };
export function componentTerminals(kind: ElectricalComponentKind): readonly string[] {
  if (kind === "ground" || kind === "junction") return ["p"];
  if (kind === "npn" || kind === "pnp") return ["c", "b", "e"];
  if (kind === "potentiometer") return ["p", "w", "n"];
  return ["p", "n"];
}
export function allTerminals(doc: ElectricalLabDocument): string[] {
  return [...Object.entries(INSTRUMENT_TERMINALS).flatMap(([id, pins]) => pins.map(p => `${id}:${p}`)), ...doc.components.flatMap(c => componentTerminals(c.kind).map(p => `${c.id}:${p}`))];
}
export function createElectricalLabDocument(id = "bench"): ElectricalLabDocument {
  return { schemaVersion: 1, id, name: "Untitled circuit", components: [], wires: [], experimentId: null, view: { x: 0, y: 0, scale: 1 }, instruments: {
    supply: { enabled: false, voltage: 5, currentLimit: .5 },
    generator: { enabled: false, waveform: "sine", frequency: 100, amplitude: 3, offset: 0, duty: .5 },
    meter: { mode: "voltage", range: 0 },
    scope: { timePerDiv: .001, voltsPerDivA: 2, voltsPerDivB: 2, coupling: "dc", triggerLevel: 0, triggerEdge: "rising", cursors: false },
  } };
}
export function createElectricalComponent(kind: ElectricalComponentKind, id: string, x: number, y: number, label = `${COMPONENT_DEFAULTS[kind].prefix}1`): ElectricalComponent {
  return { id, kind, x, y, label, rotation: 0, value: COMPONENT_DEFAULTS[kind].value, position: .5, closed: true };
}
export function emptySimulationState(): SimulationState { return { time: 0, capacitorVoltages: {}, inductorCurrents: {}, bulbTemperatures: {}, guess: {} }; }
