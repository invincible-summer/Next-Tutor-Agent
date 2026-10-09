import { createElectricalComponent, createElectricalLabDocument, type ElectricalComponentKind, type ElectricalLabDocument, type ElectricalWire } from "./model.ts";

export interface ElectricalExperiment { id: string; title: { zh: string; en: string }; description: { zh: string; en: string }; formula: string; build: () => ElectricalLabDocument }
function wire(id: string, from: string, to: string, color = "#57c9c0"): ElectricalWire { return { id, from, to, color, bends: [] }; }
function base(id: string, name: string): ElectricalLabDocument { const doc = createElectricalLabDocument(id); doc.name = name; doc.experimentId = id; doc.instruments.supply.enabled = true; doc.instruments.supply.voltage = 5; return doc; }
function add(doc: ElectricalLabDocument, kind: ElectricalComponentKind, id: string, x: number, y: number, label: string, value?: number) { const c = createElectricalComponent(kind, id, x, y, label); if (value !== undefined) c.value = value; doc.components.push(c); return c; }

export const ELECTRICAL_EXPERIMENTS: ElectricalExperiment[] = [
  {
    id: "ohms-law", title: { zh: "欧姆定律", en: "Ohm's law" }, description: { zh: "改变电阻，观察电压、电流与功率的关系。", en: "Change resistance and observe voltage, current and power." }, formula: "V = IR",
    build: () => { const d = base("ohms-law", "Ohm's law"); add(d, "resistor", "r1", 420, 280, "R1", 1000); add(d, "ground", "gnd", 420, 430, "GND"); d.wires = [wire("w1", "supply:p", "r1:p"), wire("w2", "r1:n", "gnd:p"), wire("w3", "supply:n", "gnd:p"), wire("w4", "meter:p", "r1:p", "#e9b854"), wire("w5", "meter:n", "r1:n", "#e9b854")]; return d; },
  },
  {
    id: "voltage-divider", title: { zh: "分压与串并联", en: "Divider and series-parallel" }, description: { zh: "用两个电阻和电位器搭出可调分压器。", en: "Build an adjustable divider with two resistors and a potentiometer." }, formula: "Vout = Vin · R₂/(R₁+R₂)",
    build: () => { const d = base("voltage-divider", "Voltage divider"); add(d, "resistor", "r1", 360, 230, "R1", 1000); add(d, "potentiometer", "vr1", 520, 350, "VR1", 10000); add(d, "ground", "gnd", 520, 500, "GND"); d.wires = [wire("w1", "supply:p", "r1:p"), wire("w2", "r1:n", "vr1:p"), wire("w3", "vr1:n", "gnd:p"), wire("w4", "supply:n", "gnd:p"), wire("w5", "meter:p", "vr1:w", "#e9b854"), wire("w6", "meter:n", "gnd:p", "#e9b854")]; return d; },
  },
  {
    id: "rc-scope", title: { zh: "RC 充放电", en: "RC charge and discharge" }, description: { zh: "用示波器观察电容两端的指数变化。", en: "Watch the capacitor's exponential response on the oscilloscope." }, formula: "V(t) = V₀(1 − e⁻ᵗ⧸ᴿᶜ)",
    build: () => { const d = base("rc-scope", "RC charge and discharge"); d.instruments.supply.voltage = 5; d.instruments.scope.timePerDiv = .02; d.instruments.scope.voltsPerDivA = 1; add(d, "resistor", "r1", 350, 240, "R1", 10000); add(d, "capacitor", "c1", 540, 380, "C1", 100e-6); add(d, "switch", "sw1", 350, 380, "S1"); add(d, "ground", "gnd", 540, 520, "GND"); d.wires = [wire("w1", "supply:p", "sw1:p"), wire("w2", "sw1:n", "r1:p"), wire("w3", "r1:n", "c1:p"), wire("w4", "c1:n", "gnd:p"), wire("w5", "supply:n", "gnd:p"), wire("w6", "scope:a", "c1:p", "#e96951"), wire("w7", "scope:g", "gnd:p", "#8996a8")]; return d; },
  },
  {
    id: "diode-led", title: { zh: "二极管与 LED", en: "Diode and LED" }, description: { zh: "比较整流方向、正向压降与 LED 亮度。", en: "Compare rectification, forward drop and LED brightness." }, formula: "I ≈ (V − V_F)/R",
    build: () => { const d = base("diode-led", "Diode and LED"); d.instruments.supply.voltage = 9; add(d, "resistor", "r1", 300, 270, "R1", 470); add(d, "led", "led1", 500, 270, "LED1", 1.8); add(d, "ground", "gnd", 500, 430, "GND"); d.wires = [wire("w1", "supply:p", "r1:p"), wire("w2", "r1:n", "led1:p"), wire("w3", "led1:n", "gnd:p"), wire("w4", "supply:n", "gnd:p"), wire("w5", "meter:p", "led1:p", "#e9b854"), wire("w6", "meter:n", "led1:n", "#e9b854")]; return d; },
  },
  {
    id: "transistor-switch", title: { zh: "三极管开关", en: "Transistor switch" }, description: { zh: "用 NPN 三极管控制灯泡，观察截止和饱和。", en: "Use an NPN transistor to control a lamp and see cutoff and saturation." }, formula: "I_C ≈ βI_B",
    build: () => { const d = base("transistor-switch", "Transistor switch"); d.instruments.supply.voltage = 6; add(d, "resistor", "rbase", 290, 340, "RB", 10000); add(d, "npn", "q1", 470, 340, "Q1", 100); add(d, "bulb", "lamp1", 470, 210, "LP1", 1); add(d, "switch", "sw1", 290, 210, "S1"); add(d, "ground", "gnd", 470, 500, "GND"); d.wires = [wire("w1", "supply:p", "sw1:p"), wire("w2", "sw1:n", "lamp1:p"), wire("w3", "lamp1:n", "q1:c"), wire("w4", "supply:p", "rbase:p"), wire("w5", "rbase:n", "q1:b"), wire("w6", "q1:e", "gnd:p"), wire("w7", "supply:n", "gnd:p")]; return d; },
  },
  {
    id: "conductance-bridge", title: { zh: "电导与电桥", en: "Conductance bridge" }, description: { zh: "把电阻换成电导，平衡一个简单电桥。", en: "Use conductance values to balance a simple bridge." }, formula: "G = 1/R",
    build: () => { const d = base("conductance-bridge", "Conductance bridge"); add(d, "conductance", "g1", 320, 240, "G1", .001); add(d, "conductance", "g2", 520, 240, "G2", .001); add(d, "resistor", "r1", 320, 380, "R1", 1000); add(d, "resistor", "r2", 520, 380, "R2", 1000); add(d, "ground", "gnd", 420, 520, "GND"); d.wires = [wire("w1", "supply:p", "g1:p"), wire("w2", "supply:p", "g2:p"), wire("w3", "g1:n", "r1:p"), wire("w4", "g2:n", "r2:p"), wire("w5", "r1:n", "gnd:p"), wire("w6", "r2:n", "gnd:p"), wire("w7", "supply:n", "gnd:p"), wire("w8", "meter:p", "g1:n", "#e9b854"), wire("w9", "meter:n", "g2:n", "#e9b854")]; return d; },
  },
];
export function experimentById(id: string | null): ElectricalExperiment | undefined { return ELECTRICAL_EXPERIMENTS.find(experiment => experiment.id === id); }
