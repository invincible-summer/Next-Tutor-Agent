import { COMPONENT_DEFAULTS, COMPONENT_KINDS, allTerminals, type ElectricalComponent, type ElectricalInstrumentState, type ElectricalLabCommand, type ElectricalLabDocument, type ElectricalWire, type Point } from "./model.ts";

export const ELECTRICAL_LIMITS = { components: 64, wires: 192, bends: 32, bytes: 250_000 } as const;
const idPattern = /^[a-zA-Z0-9_-]{1,64}$/;
const colors = ["#e96951", "#57c9c0", "#e9b854", "#8996a8", "#a38bdd"];
function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid_document");
  return value as Record<string, unknown>;
}
function number(value: unknown, min: number, max: number): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < min || value > max) throw new Error("invalid_value");
  return value;
}
function bool(value: unknown): boolean { if (typeof value !== "boolean") throw new Error("invalid_value"); return value; }
function oneOf<T extends string>(value: unknown, choices: readonly T[]): T { if (typeof value !== "string" || !choices.includes(value as T)) throw new Error("invalid_value"); return value as T; }
function id(value: unknown): string { if (typeof value !== "string" || !idPattern.test(value) || ["__proto__", "constructor", "prototype", "supply", "generator", "meter", "scope"].includes(value)) throw new Error("invalid_id"); return value; }
function name(value: unknown, max: number): string { if (typeof value !== "string" || value.length > max) throw new Error("invalid_name"); return value.replace(/[\u0000-\u001f]/g, "").trim() || "Untitled circuit"; }
function point(value: unknown): Point { const p = record(value); return { x: number(p.x, -4000, 4000), y: number(p.y, -4000, 4000) }; }
export function validateElectricalLabDocument(value: unknown): ElectricalLabDocument {
  const row = record(value);
  if (row.schemaVersion !== 1) throw new Error("unsupported_version");
  if (!Array.isArray(row.components) || !Array.isArray(row.wires) || row.components.length > ELECTRICAL_LIMITS.components || row.wires.length > ELECTRICAL_LIMITS.wires) throw new Error("circuit_too_large");
  const components: ElectricalComponent[] = row.components.map(value => {
    const c = record(value); const kind = oneOf(c.kind, COMPONENT_KINDS); const limits = COMPONENT_DEFAULTS[kind];
    return { ...point(c), id: id(c.id), kind, label: name(c.label, 32), rotation: number(c.rotation, 0, 270), value: number(c.value, limits.min, limits.max), position: number(c.position, 0, 1), closed: bool(c.closed) };
  });
  if (components.some(c => c.rotation % 90 !== 0) || new Set(components.map(c => c.id)).size !== components.length) throw new Error("invalid_id");
  const inst = record(row.instruments); const s = record(inst.supply); const g = record(inst.generator); const m = record(inst.meter); const o = record(inst.scope);
  const instruments: ElectricalInstrumentState = {
    supply: { enabled: bool(s.enabled), voltage: number(s.voltage, 0, 30), currentLimit: number(s.currentLimit, .001, 3) },
    generator: { enabled: bool(g.enabled), waveform: oneOf(g.waveform, ["sine", "square", "triangle"]), frequency: number(g.frequency, .1, 10000), amplitude: number(g.amplitude, 0, 15), offset: number(g.offset, -15, 15), duty: number(g.duty, .05, .95) },
    meter: { mode: oneOf(m.mode, ["voltage", "current", "resistance", "conductance", "continuity", "diode"]), range: number(m.range, 0, 1e9) },
    scope: { timePerDiv: number(o.timePerDiv, 1e-6, 10), voltsPerDivA: number(o.voltsPerDivA, .001, 100), voltsPerDivB: number(o.voltsPerDivB, .001, 100), coupling: oneOf(o.coupling, ["dc", "ac"]), triggerLevel: number(o.triggerLevel, -30, 30), triggerEdge: oneOf(o.triggerEdge, ["rising", "falling"]), cursors: bool(o.cursors) },
  };
  const view = record(row.view);
  const doc: ElectricalLabDocument = { schemaVersion: 1, id: id(row.id), name: name(row.name, 80), components, wires: [], instruments, view: { x: number(view.x, -4000, 4000), y: number(view.y, -4000, 4000), scale: number(view.scale, .35, 3) }, experimentId: row.experimentId === null ? null : id(row.experimentId) };
  const terminals = new Set(allTerminals(doc));
  const wires: ElectricalWire[] = row.wires.map(value => {
    const w = record(value);
    if (typeof w.from !== "string" || typeof w.to !== "string" || !terminals.has(w.from) || !terminals.has(w.to) || w.from === w.to || !Array.isArray(w.bends) || w.bends.length > ELECTRICAL_LIMITS.bends) throw new Error("invalid_wire");
    return { id: id(w.id), from: w.from, to: w.to, color: oneOf(w.color, colors), bends: w.bends.map(point) };
  });
  if (new Set(wires.map(w => w.id)).size !== wires.length || wires.some(w => components.some(c => c.id === w.id))) throw new Error("invalid_id");
  doc.wires = wires;
  return doc;
}
export function serializeElectricalLabDocument(doc: ElectricalLabDocument): string { return JSON.stringify(validateElectricalLabDocument(doc), null, 2); }
export function deserializeElectricalLabDocument(text: string): ElectricalLabDocument {
  if (text.length > ELECTRICAL_LIMITS.bytes || new TextEncoder().encode(text).byteLength > ELECTRICAL_LIMITS.bytes) throw new Error("circuit_too_large");
  return validateElectricalLabDocument(JSON.parse(text));
}
export function reduceElectricalLabCommand(doc: ElectricalLabDocument, command: ElectricalLabCommand): ElectricalLabDocument {
  let next = doc;
  switch (command.type) {
    case "add": next = { ...doc, components: [...doc.components, command.component] }; break;
    case "update": next = { ...doc, components: doc.components.map(c => c.id === command.id ? { ...c, ...command.patch } : c) }; break;
    case "remove": next = { ...doc, components: doc.components.filter(c => c.id !== command.id), wires: doc.wires.filter(w => w.id !== command.id && !w.from.startsWith(`${command.id}:`) && !w.to.startsWith(`${command.id}:`)) }; break;
    case "connect": if (doc.wires.some(w => (w.from === command.wire.from && w.to === command.wire.to) || (w.from === command.wire.to && w.to === command.wire.from))) return doc; next = { ...doc, wires: [...doc.wires, command.wire] }; break;
    case "network": next = { ...doc, components: command.components, wires: command.wires }; break;
    case "instrument": next = { ...doc, instruments: command.instruments }; break;
    case "rename": next = { ...doc, name: command.name }; break;
    case "view": next = { ...doc, view: command.view }; break;
  }
  return validateElectricalLabDocument(next);
}
