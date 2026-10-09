/** Original bounded MNA solver: pivoted linear algebra, damped Newton and BE. */
import { allTerminals, emptySimulationState, type BranchReading, type CircuitDiagnostic, type ElectricalLabDocument, type MeterReading, type SimulationFrame, type SimulationSettings, type SimulationState } from "./model.ts";

type ElementKind = "r" | "c" | "l" | "v" | "i" | "diode" | "bjt" | "bulb" | "supply";
interface Element { id: string; kind: ElementKind; p: string; n: string; b: string; value: number; aux: number; resistance: number; led: boolean; polarity: number }
interface Netlist { elements: Element[]; indices: Map<string, number>; net: Map<string, string>; islands: Map<string, string>; size: number; diagnostics: CircuitDiagnostic[]; connected: Set<string> }

class Union {
  parents = new Map<string, string>();
  find(key: string): string { const parent = this.parents.get(key); if (!parent) { this.parents.set(key, key); return key; } if (parent === key) return key; const root = this.find(parent); this.parents.set(key, root); return root; }
  join(a: string, b: string): void { const ra = this.find(a); const rb = this.find(b); if (ra !== rb) this.parents.set(ra > rb ? ra : rb, ra > rb ? rb : ra); }
}
function diagnostic(code: CircuitDiagnostic["code"], severity: CircuitDiagnostic["severity"], componentId: string | null = null): CircuitDiagnostic { return { code, severity, componentId }; }

function compile(doc: ElectricalLabDocument): Netlist {
  const union = new Union(); const connected = new Set<string>();
  for (const t of allTerminals(doc)) union.find(t);
  const grounds = doc.components.filter(c => c.kind === "ground").map(c => `${c.id}:p`);
  for (const g of grounds) union.join(g, grounds[0]!);
  for (const w of doc.wires) { union.join(w.from, w.to); connected.add(w.from); connected.add(w.to); }
  const net = new Map([...union.parents.keys()].map(t => [t, union.find(t)]));
  const elements: Element[] = [];
  const add = (id: string, kind: ElementKind, p: string, n: string, value: number, opts: Partial<Element> = {}) => {
    elements.push({ id, kind, p: net.get(p)!, n: net.get(n)!, b: "", value, aux: -1, resistance: 0, led: false, polarity: 1, ...opts });
  };
  for (const c of doc.components) {
    const p = `${c.id}:p`; const n = `${c.id}:n`;
    switch (c.kind) {
      case "resistor": add(c.id, "r", p, n, c.value); break;
      case "conductance": add(c.id, "r", p, n, 1 / c.value); break;
      case "switch": add(c.id, "r", p, n, c.closed ? c.value : 1e12); break;
      case "potentiometer": add(`${c.id}_upper`, "r", p, `${c.id}:w`, Math.max(.01, c.value * c.position)); add(`${c.id}_lower`, "r", `${c.id}:w`, n, Math.max(.01, c.value * (1 - c.position))); break;
      case "capacitor": add(c.id, "c", p, n, c.value); break;
      case "inductor": add(c.id, "l", p, n, c.value); break;
      case "voltage": add(c.id, "v", p, n, c.value); break;
      case "current": add(c.id, "i", p, n, c.value); break;
      case "diode": add(c.id, "diode", p, n, c.value); break;
      case "led": add(c.id, "diode", p, n, c.value, { led: true }); break;
      case "bulb": add(c.id, "bulb", p, n, c.value); break;
      case "npn": case "pnp": add(c.id, "bjt", `${c.id}:c`, `${c.id}:e`, c.value, { b: net.get(`${c.id}:b`)!, polarity: c.kind === "npn" ? 1 : -1 }); break;
    }
  }
  const wired = (a: string, b: string) => connected.has(a) && connected.has(b);
  if (wired("supply:p", "supply:n")) {
    if (doc.instruments.supply.enabled) add("supply", "supply", "supply:p", "supply:n", doc.instruments.supply.voltage);
    else add("supply", "r", "supply:p", "supply:n", 1e12);
  }
  if (wired("generator:p", "generator:n")) {
    if (doc.instruments.generator.enabled) add("generator", "v", "generator:p", "generator:n", 0, { resistance: 50 });
    else add("generator", "r", "generator:p", "generator:n", 1e12);
  }
  if (wired("meter:p", "meter:n")) {
    const mode = doc.instruments.meter.mode;
    add("meter", "r", "meter:p", "meter:n", mode === "current" ? .1 : 1e7);
  }
  for (const ch of ["a", "b"]) if (wired(`scope:${ch}`, "scope:g")) add(`scope_${ch}`, "r", `scope:${ch}`, "scope:g", 1e6);

  // Reference every connected island separately; warn when it lacks ground.
  // Gmin stabilizes isolated pins, but never masks a contradictory ideal source.
  const graph = new Union();
  for (const el of elements) { graph.join(el.p, el.n); if (el.b) graph.join(el.b, el.n); }
  const groundNets = new Set(grounds.map(g => net.get(g)!));
  const groups = new Map<string, string[]>();
  for (const n of graph.parents.keys()) { const island = graph.find(n); groups.set(island, [...(groups.get(island) ?? []), n]); }
  const refs = new Set<string>(); const diagnostics: CircuitDiagnostic[] = [];
  for (const ns of groups.values()) {
    const explicit = ns.find(n => groundNets.has(n));
    const implicit = ["supply:n", "generator:n", "meter:n"].map(p => net.get(p)!).find(n => ns.includes(n));
    refs.add(explicit ?? implicit ?? ns[0]!);
    if (!explicit && !implicit && ns.some(n => doc.wires.some(w => net.get(w.from) === n))) diagnostics.push(diagnostic("floating", "warning"));
  }
  const indices = new Map<string, number>();
  for (const n of graph.parents.keys()) if (!refs.has(n)) indices.set(n, indices.size);
  let size = indices.size;
  for (const el of elements) if (el.kind === "v" || el.kind === "l") el.aux = size++;
  const islands = new Map([...graph.parents.keys()].map(n => [n, graph.find(n)]));
  return { elements, indices, net, size, diagnostics, islands, connected };
}

/** Gaussian elimination with scaled partial pivoting, no external math library. */
export function solveElectricalMatrix(a: Float64Array[], b: Float64Array): Float64Array | null {
  const n = b.length; const scale = a.map(row => Math.max(...row.map(Math.abs)));
  for (let k = 0; k < n; k++) {
    let pivot = k;
    for (let i = k + 1; i < n; i++) if (Math.abs(a[i]![k]!) / (scale[i] || 1) > Math.abs(a[pivot]![k]!) / (scale[pivot] || 1)) pivot = i;
    if (Math.abs(a[pivot]![k]!) < 1e-20 || !Number.isFinite(a[pivot]![k]!)) return null;
    [a[k], a[pivot]] = [a[pivot]!, a[k]!]; [b[k], b[pivot]] = [b[pivot]!, b[k]!]; [scale[k], scale[pivot]] = [scale[pivot]!, scale[k]!];
    for (let i = k + 1; i < n; i++) {
      const f = a[i]![k]! / a[k]![k]!; if (!f) continue;
      for (let j = k + 1; j < n; j++) a[i]![j] = a[i]![j]! - f * a[k]![j]!;
      b[i] = b[i]! - f * b[k]!;
    }
  }
  const x = new Float64Array(n);
  for (let i = n - 1; i >= 0; i--) {
    let sum = b[i]!; for (let j = i + 1; j < n; j++) sum -= a[i]![j]! * x[j]!;
    x[i] = sum / a[i]![i]!; if (!Number.isFinite(x[i]!)) return null;
  }
  return x;
}

export function sourceWaveform(doc: ElectricalLabDocument, time: number): number {
  const g = doc.instruments.generator;
  const phase = ((time * g.frequency) % 1 + 1) % 1;
  const signal = g.waveform === "sine" ? Math.sin(phase * 2 * Math.PI) : g.waveform === "square" ? (phase < g.duty ? 1 : -1) : 1 - 4 * Math.abs(phase - .5);
  return g.offset + g.amplitude * signal;
}
// Linear continuation outside the exponential window avoids overflow and
// retains a derivative instead of an inconsistent clipped Newton Jacobian.
function junction(v: number, saturation: number, vt: number): [number, number] {
  const z = v / vt; const clipped = Math.max(-40, Math.min(32, z));
  const exp = Math.exp(clipped); const slope = saturation * exp / vt;
  return [saturation * (exp - 1) + (z > 32 ? slope * (v - 32 * vt) : 0), z < -40 ? 0 : slope];
}
function diode(el: Element, v: number): [number, number] {
  const vt = el.led ? .052 : .026 * el.value;
  const saturation = el.led ? .01 / Math.exp(el.value / vt) : 1e-12;
  return junction(v, saturation, vt);
}
function transistor(el: Element, vc: number, vb: number, ve: number): { currents: number[]; jacobian: number[][] } {
  const sign = el.polarity; const alpha = el.value / (el.value + 1); const reverseAlpha = .5;
  const [f, gf] = junction(sign * (vb - ve), 1e-14, .026);
  const [r, gr] = junction(sign * (vb - vc), 1e-14, .026);
  const ic = sign * (alpha * f - r); const ie = sign * (-f + reverseAlpha * r);
  // Rows and columns are collector, base, emitter. Rows sum to KCL zero.
  return { currents: [ic, -ic - ie, ie], jacobian: [
    [gr, alpha * gf - gr, -alpha * gf],
    [-(1 - reverseAlpha) * gr, (1 - alpha) * gf + (1 - reverseAlpha) * gr, -(1 - alpha) * gf],
    [-reverseAlpha * gr, -gf + reverseAlpha * gr, gf],
  ] };
}
interface SolveResult { x: Float64Array; success: boolean; diagnostic: CircuitDiagnostic | null }
function solveStep(net: Netlist, doc: ElectricalLabDocument, state: SimulationState, dt: number, transient: boolean, testCurrent = 0): SolveResult {
  let x = new Float64Array(net.size);
  for (const [node, i] of net.indices) x[i] = state.guess[node] ?? 0;
  for (const el of net.elements) if (el.aux >= 0) x[el.aux] = state.inductorCurrents[el.id] ?? 0;
  const index = (n: string) => net.indices.get(n) ?? -1;
  const value = (n: string) => { const i = index(n); return i < 0 ? 0 : x[i]!; };
  const nonlinear = net.elements.some(el => ["diode", "bjt", "bulb"].includes(el.kind));
  for (let iteration = 0; iteration < (nonlinear ? 100 : 2); iteration++) {
    const a = Array.from({ length: net.size }, () => new Float64Array(net.size)); const b = new Float64Array(net.size);
    const matrix = (i: number, j: number, v: number) => { if (i >= 0 && j >= 0) a[i]![j] = a[i]![j]! + v; };
    const rhs = (i: number, v: number) => { if (i >= 0) b[i] = b[i]! + v; };
    const stamp = (p: string, n: string, g: number, i = 0) => {
      const pi = index(p); const ni = index(n); matrix(pi, pi, g); matrix(pi, ni, -g); matrix(ni, pi, -g); matrix(ni, ni, g); rhs(pi, -i); rhs(ni, i);
    };
    for (let i = 0; i < net.indices.size; i++) matrix(i, i, 1e-12);
    for (const el of net.elements) {
      const v = value(el.p) - value(el.n);
      switch (el.kind) {
        case "r": stamp(el.p, el.n, 1 / el.value); break;
        case "i": stamp(el.p, el.n, 0, testCurrent ? 0 : el.value); break;
        case "c": if (transient) { const g = el.value / dt; stamp(el.p, el.n, g, -g * (state.capacitorVoltages[el.id] ?? 0)); } break;
        case "l": case "v": {
          const pi = index(el.p); const ni = index(el.n); const aux = el.aux;
          matrix(pi, aux, 1); matrix(ni, aux, -1); matrix(aux, pi, 1); matrix(aux, ni, -1);
          const r = el.kind === "l" ? (transient ? el.value / dt : 0) : el.resistance;
          matrix(aux, aux, -r);
          const voltage = el.id === "generator" ? sourceWaveform(doc, state.time + (transient ? dt : 0)) : el.value;
          rhs(aux, el.kind === "l" ? -r * (state.inductorCurrents[el.id] ?? 0) : (testCurrent ? 0 : voltage)); break;
        }
        case "supply": stamp(el.p, el.n, 20, -doc.instruments.supply.voltage * 20); break;
        case "diode": { const [i, g] = diode(el, v); stamp(el.p, el.n, g + 1e-12, i - g * v); break; }
        case "bjt": {
          const terminals = [el.p, el.b, el.n]; const values = terminals.map(value); const model = transistor(el, values[0]!, values[1]!, values[2]!);
          for (let r = 0; r < 3; r++) {
            let offset = -model.currents[r]!;
            for (let c = 0; c < 3; c++) { const g = model.jacobian[r]![c]!; matrix(index(terminals[r]!), index(terminals[c]!), g); offset += g * values[c]!; }
            rhs(index(terminals[r]!), offset);
          }
          break;
        }
        case "bulb": {
          // 6 V rated tungsten lamp: cold resistance is one tenth of rated.
          const hotR = 36 / el.value; const heat = transient ? state.bulbTemperatures[el.id] ?? 0 : Math.min(1, (v / 6) ** 2);
          const r = hotR * (.1 + .9 * heat); const g = 1 / r;
          // The steady thermal relation is nonlinear; differentiate the I/V law.
          const dg = !transient && Math.abs(v) < 6 ? -(1.8 * v / 36) / (hotR * (.1 + .9 * heat) ** 2) : 0;
          const slope = Math.max(1 / hotR / 10, g + v * dg);
          stamp(el.p, el.n, slope, v * g - slope * v); break;
        }
      }
    }
    if (testCurrent) stamp(net.net.get("meter:p")!, net.net.get("meter:n")!, 0, -testCurrent);
    const next = solveElectricalMatrix(a, b);
    if (!next) return { x, success: false, diagnostic: diagnostic("singular", "error") };
    let difference = 0; let voltageDelta = 0;
    for (let i = 0; i < next.length; i++) { difference = Math.max(difference, Math.abs(next[i]! - x[i]!) / (1 + Math.abs(next[i]!))); if (i < net.indices.size) voltageDelta = Math.max(voltageDelta, Math.abs(next[i]! - x[i]!)); }
    if (!nonlinear || difference < 1e-8) return { x: next, success: true, diagnostic: null };
    // Limit Newton's voltage jump for semiconductor junctions. Linear sources
    // remain exact at convergence; damping is numerical, not a physical clamp.
    const damping = Math.min(1, .4 / (voltageDelta || 1));
    for (let i = 0; i < next.length; i++) next[i] = x[i]! + damping * (next[i]! - x[i]!);
    x = new Float64Array(next);
  }
  return { x, success: false, diagnostic: diagnostic("non_convergence", "error") };
}

function project(net: Netlist, doc: ElectricalLabDocument, result: SolveResult, prev: SimulationState, dt: number, transient: boolean): SimulationFrame {
  const volt = (n: string) => { const i = net.indices.get(n); return i === undefined ? 0 : result.x[i]!; };
  const branches: Record<string, BranchReading> = {}; const nodes: Record<string, number> = {};
  const state: SimulationState = { time: prev.time + (transient ? dt : 0), capacitorVoltages: { ...prev.capacitorVoltages }, inductorCurrents: { ...prev.inductorCurrents }, bulbTemperatures: { ...prev.bulbTemperatures }, guess: {} };
  const diagnostics = [...net.diagnostics]; if (result.diagnostic) diagnostics.push(result.diagnostic);
  for (const [node] of net.indices) state.guess[node] = volt(node);
  for (const [terminal, node] of net.net) nodes[terminal] = volt(node);
  for (const el of net.elements) {
    const voltage = volt(el.p) - volt(el.n); let current = 0; let brightness = 0; const terminalCurrents: Record<string, number> = {};
    switch (el.kind) {
      case "r": current = voltage / el.value; break;
      case "i": current = el.value; break;
      case "v": case "l": current = result.x[el.aux] ?? 0; if (el.kind === "l") state.inductorCurrents[el.id] = current; break;
      case "c": current = transient ? el.value / dt * (voltage - (prev.capacitorVoltages[el.id] ?? 0)) : 0; state.capacitorVoltages[el.id] = voltage; break;
      case "supply": {
        current = Math.max(-doc.instruments.supply.currentLimit, Math.min(doc.instruments.supply.currentLimit, (voltage - doc.instruments.supply.voltage) / .05));
        if (Math.abs(current) >= doc.instruments.supply.currentLimit * .999) diagnostics.push(diagnostic("current_limit", "warning", "supply"));
        if (el.p === el.n) diagnostics.push(diagnostic("short_circuit", "warning", "supply"));
        break;
      }
      case "diode": current = diode(el, voltage)[0]; brightness = el.led ? Math.max(0, Math.min(1, current / .02)) : 0; break;
      case "bjt": { const model = transistor(el, volt(el.p), volt(el.b), volt(el.n)); current = model.currents[0]!; terminalCurrents.c = current; terminalCurrents.b = model.currents[1]!; terminalCurrents.e = model.currents[2]!; break; }
      case "bulb": {
        const heat = transient ? prev.bulbTemperatures[el.id] ?? 0 : Math.min(1, (voltage / 6) ** 2);
        current = voltage / (36 / el.value * (.1 + .9 * heat));
        const target = Math.min(1.5, Math.max(0, voltage * current / el.value));
        state.bulbTemperatures[el.id] = transient ? heat + (target - heat) * (1 - Math.exp(-dt / .12)) : target;
        brightness = Math.min(1, state.bulbTemperatures[el.id]!); break;
      }
    }
    if (Math.abs(current) > 3.01 || (el.led && current > .035) || (el.kind === "bjt" && Math.abs(current) > .2)) diagnostics.push(diagnostic("overload", "warning", el.id));
    branches[el.id] = { voltage, current, power: voltage * current, conductance: Math.abs(voltage) > 1e-10 ? current / voltage : el.kind === "r" ? 1 / el.value : 0, brightness, terminalCurrents };
  }
  const differential = (a: string, b: string): number | null => {
    if (!net.connected.has(a) || !net.connected.has(b)) return null;
    const na = net.net.get(a)!; const nb = net.net.get(b)!;
    if (na !== nb && net.islands.get(na) !== net.islands.get(nb)) return null;
    return (nodes[a] ?? 0) - (nodes[b] ?? 0);
  };
  let meter: MeterReading = { value: null, unit: "V", status: "open", continuity: false };
  const mode = doc.instruments.meter.mode; const mv = differential("meter:p", "meter:n");
  if (mv !== null && result.success) {
    if (mode === "voltage" || mode === "current") {
      const value = mode === "voltage" ? mv : branches.meter?.current ?? 0;
      const range = doc.instruments.meter.range || (mode === "current" ? 3 : 600);
      meter = { value: Math.abs(value) > range ? null : value, unit: mode === "current" ? "A" : "V", status: Math.abs(value) > range ? "overload" : "ready", continuity: false };
    } else {
      const powered = net.elements.some(el => (el.kind === "supply" && doc.instruments.supply.enabled) || (el.kind === "v" && (el.id === "generator" ? doc.instruments.generator.enabled : el.value !== 0)) || (el.kind === "i" && el.value !== 0));
      if (powered) { meter = { value: null, unit: "Ω", status: "powered", continuity: false }; diagnostics.push(diagnostic("powered_ohmmeter", "warning", "meter")); }
      else {
        // The ohmmeter is an actual small test-current circuit. Remove its
        // voltage input load during the test to measure the external network.
        const testNet = { ...net, elements: net.elements.filter(el => el.id !== "meter") };
        const injection = mode === "diode" ? .001 : 1e-6;
        const test = solveStep(testNet, doc, emptySimulationState(), dt, false, injection);
        const probe = (terminal: string) => { const i = testNet.indices.get(testNet.net.get(terminal)!); return i === undefined ? 0 : test.x[i]!; };
        const voltage = probe("meter:p") - probe("meter:n"); const resistance = voltage / injection;
        const isOpen = !test.success || resistance > 1e8 || voltage > (mode === "diode" ? 3 : 100);
        meter = { value: isOpen ? null : mode === "diode" ? voltage : mode === "conductance" ? (resistance > 1e-10 ? 1 / resistance : null) : Math.max(0, resistance), unit: mode === "diode" ? "V" : mode === "conductance" ? "S" : "Ω", status: isOpen ? "open" : "ready", continuity: !isOpen && resistance < 50 };
      }
    }
  }
  const scope = { a: result.success ? differential("scope:a", "scope:g") : null, b: result.success ? differential("scope:b", "scope:g") : null };
  if ((net.connected.has("scope:a") || net.connected.has("scope:b")) && !net.connected.has("scope:g")) diagnostics.push(diagnostic("probe_missing", "info", "scope"));
  return { time: state.time, valid: result.success, nodes: result.success ? nodes : {}, branches: result.success ? branches : {}, diagnostics, meter, scope, samples: [], state: result.success ? state : prev };
}

/** Transient state is supplied explicitly: rendering cannot affect simulation. */
export function solveCircuit(doc: ElectricalLabDocument, settings: SimulationSettings = { mode: "dc", dt: .0001, steps: 1 }, previous = emptySimulationState()): SimulationFrame {
  const net = compile(doc); const dt = Math.max(1e-8, Math.min(1, settings.dt)); const transient = settings.mode === "transient";
  const steps = transient ? Math.max(1, Math.min(4096, Math.floor(settings.steps))) : 1;
  let state = previous;
  let frame = project(net, doc, { x: new Float64Array(net.size), success: true, diagnostic: null }, state, dt, false);
  const samples: SimulationFrame["samples"] = [];
  for (let step = 0; step < steps; step++) {
    const result = solveStep(net, doc, state, dt, transient);
    frame = project(net, doc, result, state, dt, transient);
    if (!frame.valid) break;
    state = frame.state;
    if (transient) samples.push({ time: frame.time, ...frame.scope });
  }
  frame.samples = samples;
  if (transient && doc.instruments.generator.enabled && dt * doc.instruments.generator.frequency > .025) frame.diagnostics.push(diagnostic("undersampled", "warning", "generator"));
  return frame;
}
