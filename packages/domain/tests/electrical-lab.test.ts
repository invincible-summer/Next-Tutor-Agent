import test from "node:test";
import assert from "node:assert/strict";
import { createElectricalComponent, createElectricalLabDocument, deserializeElectricalLabDocument, formatElectrical, parseElectrical, projectOnWirePath, routeOrthogonal, solveCircuit, spliceElectricalWire, validateElectricalLabDocument } from "../src/electrical-lab/index.ts";
import { experimentById } from "../src/electrical-lab/experiments.ts";

test("SI values parse and format without locale ambiguity", () => {
  assert.equal(parseElectrical("4.7 kΩ", "Ω"), 4700);
  assert.equal(parseElectrical("10 μF", "F"), 10e-6);
  assert.equal(parseElectrical("4 V", "Ω"), null);
  assert.equal(formatElectrical(.001, "A"), "1 mA");
});
test("document validation rejects unknown components and oversized positions", () => {
  const doc = createElectricalLabDocument();
  doc.components.push(createElectricalComponent("resistor", "r1", 20, 20));
  assert.equal(validateElectricalLabDocument(doc).components[0]?.id, "r1");
  assert.throws(() => validateElectricalLabDocument({ ...doc, components: [{ ...doc.components[0], kind: "magic" }] }), /invalid_value/);
  assert.throws(() => validateElectricalLabDocument({ ...doc, components: [{ ...doc.components[0], x: 9000 }] }), /invalid_value/);
});
test("empty bench stays finite and reports no fake measurements", () => {
  const frame = solveCircuit(createElectricalLabDocument());
  assert.equal(frame.valid, true);
  assert.equal(frame.meter.status, "open");
  assert.ok(frame.diagnostics.every(d => d.code !== "singular"));
});
test("Ohm's law preset produces a finite resistor current", () => {
  const doc = experimentById("ohms-law")!.build();
  const frame = solveCircuit(doc);
  const current = frame.branches.r1?.current ?? 0;
  assert.ok(Math.abs(current - .005) < .0008, `current=${current}`);
  assert.ok(Math.abs((frame.branches.r1?.power ?? 0) - .025) < .01);
});
test("LED and transistor presets remain bounded and observable", () => {
  const led = solveCircuit(experimentById("diode-led")!.build());
  assert.equal(led.valid, true);
  assert.ok((led.branches.led1?.brightness ?? 0) > .25);
  const transistor = solveCircuit(experimentById("transistor-switch")!.build());
  assert.equal(transistor.valid, true);
  assert.ok(Number.isFinite(transistor.branches.q1?.current ?? NaN));
});
test("powered ohmmeter reports a protection state", () => {
  const doc = experimentById("ohms-law")!.build();
  doc.instruments.meter.mode = "resistance";
  const frame = solveCircuit(doc);
  assert.equal(frame.meter.status, "powered");
  assert.ok(frame.diagnostics.some(diagnostic => diagnostic.code === "powered_ohmmeter"));
});
test("RC preset emits transient samples and capacitor state changes", () => {
  const doc = experimentById("rc-scope")!.build();
  const frame = solveCircuit(doc, { mode: "transient", dt: .002, steps: 40 });
  assert.equal(frame.samples.length, 40);
  assert.ok((frame.samples.at(-1)?.a ?? 0) > (frame.samples[0]?.a ?? 0));
});
test("serialization is versioned and round trips", () => {
  const doc = experimentById("diode-led")!.build();
  const restored = deserializeElectricalLabDocument(JSON.stringify(doc));
  assert.deepEqual(restored.components.map(c => c.id), doc.components.map(c => c.id));
  assert.throws(() => deserializeElectricalLabDocument(JSON.stringify({ ...doc, schemaVersion: 99 })), /unsupported_version/);
});
test("orthogonal wiring can split a live wire at a junction", () => {
  const doc = createElectricalLabDocument();
  doc.instruments.supply.enabled = true;
  doc.wires.push({ id: "w1", from: "supply:p", to: "meter:p", color: "#57c9c0", bends: [] });
  doc.wires.push({ id: "w2", from: "supply:n", to: "meter:n", color: "#e96951", bends: [] });
  const reading = solveCircuit(doc).meter.value;
  const path = routeOrthogonal({ x: 94, y: 118 }, { x: 466, y: 118 });
  assert.ok(path.every((point, index) => index === 0 || point.x === path[index - 1]!.x || point.y === path[index - 1]!.y));
  const node = createElectricalComponent("junction", "junction_1", 280, 118, "J1");
  const split = spliceElectricalWire(doc, "w1", node, path);
  assert.equal(split.components[0]?.id, "junction_1");
  assert.equal(split.wires.length, 3);
  assert.equal(split.wires.filter(wire => wire.from.includes("junction_1") || wire.to.includes("junction_1")).length, 2);
  assert.ok(reading != null);
  assert.equal(solveCircuit(split).meter.value, reading);
  assert.throws(() => spliceElectricalWire(doc, "w1", { ...node, y: 121 }, path), /invalid_junction/);
});
test("routing keeps bends axial and a branch stays on an off-grid parent wire", () => {
  const path = routeOrthogonal({ x: 462, y: 340 }, { x: 810, y: 208 }, [{ x: 643, y: 317 }], { x: -1, y: 0 }, { x: 0, y: 1 });
  assert.ok(path.slice(1).every((point, index) => point.x === path[index]!.x || point.y === path[index]!.y));
  const hit = projectOnWirePath([{ x: 462, y: 340 }, { x: 462, y: 200 }], { x: 459, y: 273 });
  assert.deepEqual(hit?.point, { x: 462, y: 270 });
});
