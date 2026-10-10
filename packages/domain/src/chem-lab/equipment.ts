/**
 * 器材规格表：全部尺寸/端口/锚点/控制的唯一配置事实源。
 * 几何制造者（web EquipmentFactory）与交互命中体都从这张表生成，避免模型与端口图不同步。
 * 局部坐标约定：原点在底面中心，Y 向上；1 unit ≈ 10cm。
 */
import type { ControlSpec, ControlValue, EquipmentSpec, InternalEdge, PortSpec, Vec3 } from "./types.ts";

const v = (x: number, y: number, z: number): Vec3 => ({ x, y, z });

function port(id: string, position: Vec3, direction: Vec3, socketClass: PortSpec["socketClass"], visualRadius = 0.09, maxLinks = 1): PortSpec {
  return { id, localPosition: position, localDirection: direction, socketClass, maxLinks, visualRadius };
}

function toggle(id: string, def = false): ControlSpec { return { id, kind: "toggle", default: def }; }
function dial(id: string, steps: number[], def: number): ControlSpec { return { id, kind: "dial", steps, default: def }; }

/** 全互通小节点（三通/四通/双口塞）的内部边：任意两口互达。 */
function fullyLinked(ids: string[], requiresControlOn?: string): InternalEdge[] {
  const edges: InternalEdge[] = [];
  for (let i = 0; i < ids.length; i++) {
    for (let j = 0; j < ids.length; j++) {
      if (i === j) continue;
      const from = ids[i]!, to = ids[j]!;
      edges.push(requiresControlOn === undefined ? { from, to } : { from, to, requiresControlOn });
    }
  }
  return edges;
}

const SPECS: EquipmentSpec[] = [
  // ── 玻璃容器 ──────────────────────────────────────────────────────────────
  {
    kind: "beaker", family: "glassware", label: { zh: "烧杯", en: "Beaker" },
    bounds: { width: 1.0, depth: 1.0, height: 1.6 },
    ports: [port("rim", v(0.34, 1.58, 0), v(0, 1, 0), "liquid", 0.1)],
    anchors: { rim: v(0, 1.6, 0), pourLip: v(0.5, 1.5, 0.14), liquidSurface: v(0, 0.75, 0) },
    clampable: false, controls: [], internalEdges: [],
  },
  {
    kind: "graduated-cylinder", family: "glassware", label: { zh: "量筒", en: "Graduated cylinder" },
    bounds: { width: 0.62, depth: 0.62, height: 2.6 },
    ports: [port("rim", v(0.14, 2.58, 0), v(0, 1, 0), "liquid", 0.09)],
    anchors: { rim: v(0, 2.6, 0), pourLip: v(0.24, 2.5, 0.1), liquidSurface: v(0, 1.2, 0) },
    clampable: false, controls: [], internalEdges: [],
  },
  {
    kind: "flask-round", family: "glassware", label: { zh: "圆底烧瓶", en: "Round-bottom flask" },
    bounds: { width: 1.3, depth: 1.3, height: 2.1 },
    ports: [port("neck", v(0, 2.1, 0), v(0, 1, 0), "glassJoint", 0.11)],
    anchors: { rim: v(0, 2.1, 0), pourLip: v(0, 2.05, 0.12), clampPoint: v(0, 1.72, 0), heatZone: v(0, 0.12, 0), liquidSurface: v(0, 0.62, 0) },
    clampable: true, controls: [], internalEdges: [],
  },
  {
    kind: "flask-three-neck", family: "glassware", label: { zh: "三口烧瓶", en: "Three-neck flask" },
    bounds: { width: 1.9, depth: 1.6, height: 2.5 },
    ports: [
      port("neckA", v(0, 2.5, 0), v(0, 1, 0), "glassJoint", 0.11),
      port("neckB", v(-0.82, 1.62, 0), v(-0.62, 0.79, 0), "glassJoint", 0.09),
      port("neckC", v(0.82, 1.62, 0), v(0.62, 0.79, 0), "glassJoint", 0.09),
    ],
    anchors: { rim: v(0, 2.5, 0), clampPoint: v(0, 2.1, 0), heatZone: v(0, 0.14, 0), liquidSurface: v(0, 0.78, 0) },
    clampable: true, controls: [], internalEdges: [],
  },
  {
    kind: "flask-erlenmeyer", family: "glassware", label: { zh: "锥形瓶", en: "Erlenmeyer flask" },
    bounds: { width: 1.3, depth: 1.3, height: 2.0 },
    ports: [port("neck", v(0, 2.0, 0), v(0, 1, 0), "glassJoint", 0.1)],
    anchors: { rim: v(0, 2.0, 0), pourLip: v(0, 1.95, 0.12), liquidSurface: v(0, 0.55, 0) },
    clampable: false, controls: [], internalEdges: [],
  },
  {
    kind: "washing-bottle", family: "glassware", label: { zh: "洗气瓶", en: "Gas-washing bottle" },
    bounds: { width: 1.1, depth: 1.1, height: 3.0 },
    ports: [
      port("gasIn", v(-0.24, 3.0, 0), v(0, 1, 0), "gas", 0.09),
      port("gasOut", v(0.24, 3.0, 0), v(0, 1, 0), "gas", 0.09),
    ],
    anchors: { liquidSurface: v(0, 1.35, 0), fill: v(0, 1.5, 0), clampPoint: v(0, 2.3, 0) },
    clampable: true, controls: [],
    internalEdges: [{ from: "gasIn", to: "gasOut" }],
    visualTuning: { dipDepth: 1.9, innerTubeR: 0.09 },
  },
  {
    kind: "condenser-coil", family: "glassware", label: { zh: "蛇形冷凝器", en: "Coil condenser" },
    bounds: { width: 4.1, depth: 1.1, height: 1.0 },
    ports: [
      port("vaporIn", v(-2.0, 0, 0), v(-1, 0, 0), "glassJoint", 0.11),
      port("condensateOut", v(2.0, 0, 0), v(1, 0, 0), "glassJoint", 0.11),
      port("waterIn", v(-1.5, 0.44, 0), v(0, 1, 0), "water", 0.08),
      port("waterOut", v(1.5, 0.44, 0), v(0, 1, 0), "water", 0.08),
    ],
    anchors: { clampPoint: v(0, 0, 0) },
    clampable: true, controls: [],
    internalEdges: [{ from: "vaporIn", to: "condensateOut" }, { from: "waterIn", to: "waterOut" }],
    visualTuning: { coilTurns: 7, coilR: 0.34 },
  },
  {
    kind: "condenser-straight", family: "glassware", label: { zh: "直形冷凝管", en: "Straight condenser" },
    bounds: { width: 3.4, depth: 1.0, height: 0.95 },
    ports: [
      port("vaporIn", v(-1.65, 0, 0), v(-1, 0, 0), "glassJoint", 0.1),
      port("condensateOut", v(1.65, 0, 0), v(1, 0, 0), "glassJoint", 0.1),
      port("waterIn", v(-1.2, 0.42, 0), v(0, 1, 0), "water", 0.08),
      port("waterOut", v(1.2, 0.42, 0), v(0, 1, 0), "water", 0.08),
    ],
    anchors: { clampPoint: v(0, 0, 0) },
    clampable: true, controls: [],
    internalEdges: [{ from: "vaporIn", to: "condensateOut" }, { from: "waterIn", to: "waterOut" }],
  },
  {
    kind: "settling-bottle", family: "glassware", label: { zh: "沉降瓶", en: "Settling bottle" },
    bounds: { width: 1.0, depth: 1.0, height: 3.5 },
    ports: [
      port("topIn", v(-0.2, 3.5, 0), v(0, 1, 0), "tube", 0.09),
      port("topOut", v(0.22, 3.5, 0), v(0, 1, 0), "tube", 0.09),
      port("bottomOut", v(0, 0.14, 0.44), v(0, -0.3, 1), "tube", 0.08),
    ],
    anchors: { liquidSurface: v(0, 1.6, 0), sedimentBed: v(0, 0.3, 0), clampPoint: v(0, 2.7, 0) },
    clampable: true, controls: [],
    internalEdges: [{ from: "topIn", to: "topOut" }, { from: "topIn", to: "bottomOut" }],
  },
  {
    kind: "u-tube", family: "glassware", label: { zh: "U 形观察管", en: "U-tube" },
    bounds: { width: 1.1, depth: 0.55, height: 1.8 },
    ports: [
      port("in", v(-0.42, 1.8, 0), v(0, 1, 0), "tube", 0.08),
      port("out", v(0.42, 1.8, 0), v(0, 1, 0), "tube", 0.08),
    ],
    anchors: { liquidSurface: v(0, 0.85, 0) },
    clampable: false, controls: [],
    internalEdges: [{ from: "in", to: "out" }],
  },
  {
    kind: "gas-collecting-bottle", family: "glassware", label: { zh: "集气观察瓶", en: "Gas-collecting bottle" },
    bounds: { width: 1.2, depth: 1.2, height: 2.3 },
    ports: [
      port("in", v(-0.22, 2.3, 0), v(0, 1, 0), "gas", 0.09),
      port("vent", v(0.24, 2.3, 0), v(0, 1, 0), "gas", 0.09),
    ],
    anchors: { mistZone: v(0, 1.5, 0), liquidSurface: v(0, 0.4, 0) },
    clampable: false, controls: [],
    internalEdges: [{ from: "in", to: "vent" }],
  },
  {
    kind: "observation-sphere", family: "glassware", label: { zh: "观察球", en: "Observation sphere" },
    bounds: { width: 1.9, depth: 1.9, height: 2.3 },
    ports: [
      port("in", v(-0.95, 1.15, 0), v(-1, 0, 0), "tube", 0.1),
      port("out", v(0.95, 1.15, 0), v(1, 0, 0), "tube", 0.1),
    ],
    anchors: { core: v(0, 1.15, 0), burstZone: v(0, 1.15, 0), clampPoint: v(0, 0.45, 0) },
    clampable: true, controls: [],
    internalEdges: [{ from: "in", to: "out" }],
    visualTuning: { sphereR: 0.8 },
  },
  {
    kind: "expansion-ball", family: "glassware", label: { zh: "缓冲膨胀球", en: "Expansion ball" },
    bounds: { width: 1.5, depth: 1.5, height: 1.8 },
    ports: [
      port("in", v(-0.62, 0.95, 0), v(-1, 0, 0), "tube", 0.09),
      port("out", v(0.62, 0.95, 0), v(1, 0, 0), "tube", 0.09),
    ],
    anchors: { core: v(0, 0.95, 0) },
    clampable: false, controls: [],
    internalEdges: [{ from: "in", to: "out" }],
    visualTuning: { sphereR: 0.62 },
  },
  {
    kind: "reservoir", family: "glassware", label: { zh: "透明储液槽", en: "Clear reservoir" },
    bounds: { width: 1.5, depth: 1.1, height: 1.25 },
    ports: [
      port("drain", v(0.75, 0.4, 0), v(1, 0, 0), "water", 0.09),
      port("return", v(0, 1.25, -0.3), v(0, 1, 0), "water", 0.09),
    ],
    anchors: { rim: v(0, 1.25, 0.2), liquidSurface: v(0, 0.55, 0), clampPoint: v(0, 0.95, -0.42) },
    clampable: true, controls: [], internalEdges: [{ from: "drain", to: "return" }],
  },
  // ── 接插件 ────────────────────────────────────────────────────────────────
  {
    kind: "stopper-2port", family: "connector", label: { zh: "双口胶塞", en: "Two-port stopper" },
    bounds: { width: 0.5, depth: 0.5, height: 0.6 },
    ports: [
      port("a", v(-0.15, 0.6, 0), v(0, 1, 0), "glassJoint", 0.08),
      port("b", v(0.15, 0.6, 0), v(0, 1, 0), "glassJoint", 0.08),
    ],
    anchors: {}, clampable: false, controls: [],
    internalEdges: fullyLinked(["a", "b"]),
  },
  {
    kind: "tee", family: "connector", label: { zh: "三通", en: "Tee" },
    bounds: { width: 1.0, depth: 0.4, height: 0.55 },
    ports: [
      port("a", v(-0.45, 0, 0), v(-1, 0, 0), "tube", 0.085),
      port("b", v(0.45, 0, 0), v(1, 0, 0), "tube", 0.085),
      port("c", v(0, 0.5, 0), v(0, 1, 0), "tube", 0.085),
    ],
    anchors: {}, clampable: false, controls: [],
    internalEdges: fullyLinked(["a", "b", "c"]),
  },
  {
    kind: "cross", family: "connector", label: { zh: "四通", en: "Cross" },
    bounds: { width: 1.0, depth: 1.0, height: 0.55 },
    ports: [
      port("a", v(-0.45, 0, 0), v(-1, 0, 0), "tube", 0.085),
      port("b", v(0.45, 0, 0), v(1, 0, 0), "tube", 0.085),
      port("c", v(0, 0.5, 0), v(0, 1, 0), "tube", 0.085),
      port("d", v(0, 0, -0.45), v(0, 0, -1), "tube", 0.085),
    ],
    anchors: {}, clampable: false, controls: [],
    internalEdges: fullyLinked(["a", "b", "c", "d"]),
  },
  {
    kind: "y-mixer", family: "connector", label: { zh: "Y 型混合管", en: "Y mixer" },
    bounds: { width: 0.8, depth: 0.7, height: 0.7 },
    ports: [
      port("a", v(-0.28, 0.65, 0), v(0, 1, 0), "tube", 0.085),
      port("b", v(0.28, 0.65, 0), v(0, 1, 0), "tube", 0.085),
      port("c", v(0, 0.12, 0.26), v(0, -0.45, 1), "tube", 0.085),
    ],
    anchors: { mixZone: v(0, 0.3, 0) },
    clampable: false, controls: [],
    internalEdges: [{ from: "a", to: "c" }, { from: "b", to: "c" }, { from: "a", to: "b" }, { from: "b", to: "a" }],
  },
  {
    kind: "valve", family: "connector", label: { zh: "阀门", en: "Valve" },
    bounds: { width: 0.85, depth: 0.5, height: 0.7 },
    ports: [
      port("a", v(-0.38, 0.1, 0), v(-1, 0, 0), "tube", 0.085),
      port("b", v(0.38, 0.1, 0), v(1, 0, 0), "tube", 0.085),
    ],
    anchors: { handle: v(0, 0.42, 0) },
    clampable: false, controls: [toggle("open")],
    internalEdges: [{ from: "a", to: "b", requiresControlOn: "open" }, { from: "b", to: "a", requiresControlOn: "open" }],
  },
  // ── 支架 / 热源 / 机械 ───────────────────────────────────────────────────
  {
    kind: "stand", family: "stand", label: { zh: "铁架台", en: "Retort stand" },
    bounds: { width: 1.7, depth: 1.05, height: 4.6 },
    ports: [],
    anchors: { railBase: v(-0.52, 0.22, -0.3), railTop: v(-0.52, 4.4, -0.3), railAxis: v(-0.52, 2.3, -0.3) },
    clampable: false, controls: [], internalEdges: [],
    visualTuning: { poleR: 0.07, baseH: 0.16 },
  },
  {
    kind: "alcohol-lamp", family: "heat", label: { zh: "酒精灯（虚拟）", en: "Alcohol lamp (virtual)" },
    bounds: { width: 0.9, depth: 0.9, height: 1.05 },
    ports: [],
    anchors: { wick: v(0, 0.92, 0), flame: v(0, 1.3, 0), cap: v(0.3, 0.9, 0) },
    clampable: false, controls: [toggle("lit")], internalEdges: [],
    visualTuning: { flameH: 0.85 },
  },
  {
    kind: "hot-plate", family: "heat", label: { zh: "热板", en: "Hot plate" },
    bounds: { width: 1.25, depth: 0.95, height: 0.3 },
    ports: [],
    anchors: { heatZone: v(0, 0.32, 0) },
    clampable: false, controls: [toggle("power"), dial("level", [0.5, 1], 0.5)], internalEdges: [],
  },
  {
    kind: "pump", family: "machine", label: { zh: "演示泵", en: "Demo pump" },
    bounds: { width: 0.95, depth: 0.75, height: 0.85 },
    ports: [
      port("in", v(-0.48, 0.42, 0), v(-1, 0, 0), "water", 0.085),
      port("out", v(0.48, 0.42, 0), v(1, 0, 0), "water", 0.085),
    ],
    anchors: { impeller: v(0, 0.45, 0.1) },
    clampable: false, controls: [toggle("power"), dial("speed", [0.35, 0.7, 1], 0.7)],
    internalEdges: [{ from: "in", to: "out", requiresControlOn: "power" }],
  },
  // ── 虚拟试剂 ──────────────────────────────────────────────────────────────
  {
    kind: "reagent-bottle", family: "reagent", label: { zh: "演示试剂瓶", en: "Demo reagent bottle" },
    bounds: { width: 0.68, depth: 0.68, height: 1.2 },
    ports: [],
    anchors: { pourLip: v(0.3, 1.08, 0.1), cap: v(0, 1.2, 0) },
    clampable: false, controls: [], internalEdges: [],
    defaultContents: { fill: 0.8, colorId: "teal" },
  },
];

export const EQUIPMENT_SPECS: ReadonlyMap<string, EquipmentSpec> = new Map(SPECS.map(s => [s.kind, s]));

export const EQUIPMENT_KINDS: readonly string[] = SPECS.map(s => s.kind);

export function getEquipmentSpec(kind: string): EquipmentSpec | null { return EQUIPMENT_SPECS.get(kind) ?? null; }

export function equipmentLabel(kind: string, lang: "zh" | "en"): string {
  const spec = getEquipmentSpec(kind);
  return spec ? spec.label[lang] : kind;
}

export function defaultControls(kind: string): Record<string, ControlValue> {
  const spec = getEquipmentSpec(kind);
  if (!spec) return {};
  const out: Record<string, ControlValue> = {};
  for (const c of spec.controls) out[c.id] = c.default;
  return out;
}

/** 能盛演示液的容器（有 liquidSurface 锚点即视为可装液）。 */
export function isLiquidVessel(kind: string): boolean {
  return getEquipmentSpec(kind)?.anchors.liquidSurface !== undefined;
}

/**
 * 端口能否插接：仅外观结构判断——liquid 口是开口容器沿，只可作为软管/玻璃管的
 * “卸料端”接收一路来液，两个开口容器口不能互接；其余类别由万能软管互连。
 * 不做化学安全或危险组合推导。
 */
export function portsCompatible(aSpec: PortSpec, bSpec: PortSpec): boolean {
  return !(aSpec.socketClass === "liquid" && bSpec.socketClass === "liquid");
}
