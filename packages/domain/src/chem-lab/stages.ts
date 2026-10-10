/**
 * 五个多阶演示关卡。关卡 = 场景主题 + 器材组合 + AUTO 预组装模板；
 * 没有目标、评分、步骤或正确性判定。所有模板初始状态：热源熄灭、泵停、容器为空。
 * 布局遵守统一世界尺度（台面 Y=0，X∈[-8,8]，Z∈[-4,4]，后方为 -Z），按关卡构图
 * 前后错落、不同高度，避免把设备排成一条直线。
 */
import type { Connection, EquipmentInstance, Quat, StageDefinition, Vec3 } from "./types.ts";

const v = (x: number, y: number, z: number): Vec3 => ({ x, y, z });
const qYaw = (a: number): Quat => [0, Math.sin(a / 2), 0, Math.cos(a / 2)];
const qTiltZ = (a: number): Quat => [0, 0, Math.sin(a / 2), Math.cos(a / 2)];

/** 台面独立器材（底面中心落台）。 */
function eq(id: string, kind: string, x: number, z: number, yaw = 0): EquipmentInstance {
  return { id, kind, pose: { position: v(x, 0, z), rotation: qYaw(yaw) }, controls: {} };
}
/**
 * 挂载到铁架台的器材：localPose 相对支架原点（支架底面中心）。
 * 铁架立杆在支架局部 (-0.52, ·, -0.3)，夹爪朝 +X 伸出 0.75 让玻璃体让开立杆，夹持点即 (+0.23, ·, -0.3)；
 * 因此 localPose.position = (0.23, 夹持高度 h − clampPoint.y, -0.3 − clampPoint.z …)，
 * 即夹持点对齐器材的 clampPoint 锚点，瓶体悬在杆前方而不是与立杆相交。
 */
function mounted(id: string, kind: string, parent: string, x: number, y: number, z: number, tiltZ = 0): EquipmentInstance {
  const rotation = qTiltZ(tiltZ);
  const localPose = { position: v(x, y, z), rotation };
  return { id, kind, pose: localPose, parentMountId: parent, localPose, controls: {} };
}
function cn(id: string, aE: string, aP: string, bE: string, bP: string, style: Connection["style"] = "tube"): Connection {
  return { id, a: { equipmentId: aE, portId: aP }, b: { equipmentId: bE, portId: bP }, style, slack: 0.5 };
}

const UNIVERSAL_KINDS = ["beaker", "graduated-cylinder", "stand", "valve", "tee", "stopper-2port", "reagent-bottle", "alcohol-lamp", "hot-plate"] as const;

// ── 01 气泡接力 · 多瓶串联 ─────────────────────────────────────────────────
const bubbleRelay: StageDefinition = {
  id: "bubble-relay",
  title: { zh: "气泡接力 · 多瓶串联", en: "Bubble Relay" },
  description: { zh: "一瓶气泡液，牵着两级洗气瓶、U 形管一路冒泡到集气瓶。", en: "One bubbler flask, two washing bottles and a U-tube relaying bubbles into a collector." },
  thumbnail: { hue: 178, seed: 11, motif: "relay" },
  availableEquipmentKinds: [...UNIVERSAL_KINDS, "flask-round", "washing-bottle", "u-tube", "gas-collecting-bottle", "pump"],
  starterScene: {
    equipment: [
      eq("stand-1", "stand", -5.8, 0.9),
      eq("flask-1", "flask-round", -4.2, 0.6),
      eq("wash-1", "washing-bottle", -1.6, -0.8),
      eq("u-1", "u-tube", 2.0, 1.0),
    ],
    connections: [],
  },
  assembledTemplate: {
    equipment: [
      eq("stand-1", "stand", -6.0, 0.9),
      mounted("flask-1", "flask-round", "stand-1", 0.23, 1.43, -0.3),
      eq("wash-1", "washing-bottle", -2.9, -1.5),
      eq("stand-2", "stand", -0.5, -2.3),
      mounted("wash-2", "washing-bottle", "stand-2", 0.23, 1.05, -0.3),
      eq("valve-1", "valve", 1.3, 0.1),
      eq("u-1", "u-tube", 2.7, 1.2),
      eq("collect-1", "gas-collecting-bottle", 4.7, -0.5),
      eq("beaker-1", "beaker", 5.9, 1.6),
    ],
    connections: [
      cn("cn-1", "flask-1", "neck", "wash-1", "gasIn"),
      cn("cn-2", "wash-1", "gasOut", "wash-2", "gasIn"),
      cn("cn-3", "wash-2", "gasOut", "valve-1", "a"),
      cn("cn-4", "valve-1", "b", "u-1", "in"),
      cn("cn-5", "u-1", "out", "collect-1", "in"),
    ],
    camera: { yaw: 0.55, pitch: 0.66, distance: 15.5, target: v(-0.4, 1.0, -0.3) },
  },
  camera: { yaw: 0.55, pitch: 0.66, distance: 16.5, target: v(-0.4, 1.0, -0.3) },
  cueMap: { liquidColors: ["teal", "sky"], flowColor: "sky" },
};

// ── 02 银色蛇管 · 多路冷凝 ─────────────────────────────────────────────────
const silverCondenser: StageDefinition = {
  id: "silver-condenser",
  title: { zh: "银色蛇管 · 多路冷凝", en: "Silver Coil Condenser" },
  description: { zh: "点亮酒精灯，看蒸汽爬上蛇形冷凝器，一滴一滴落进两个接收瓶。", en: "Light the lamp, watch vapor climb the coil condenser and drip into two receivers." },
  thumbnail: { hue: 200, seed: 27, motif: "coil" },
  availableEquipmentKinds: [...UNIVERSAL_KINDS, "flask-round", "condenser-coil", "condenser-straight", "pump"],
  starterScene: {
    equipment: [
      eq("stand-1", "stand", -4.6, 0.6),
      eq("flask-1", "flask-round", -3.2, 1.6),
      eq("lamp-1", "alcohol-lamp", -3.0, 1.4),
      eq("cond-1", "condenser-coil", 0.8, -0.6, -0.25),
      eq("beaker-1", "beaker", 3.4, 0.8),
    ],
    connections: [],
  },
  assembledTemplate: {
    equipment: [
      eq("stand-1", "stand", -5.7, 0.5),
      mounted("flask-1", "flask-round", "stand-1", 0.23, 1.48, -0.3),
      eq("lamp-1", "alcohol-lamp", -5.8, 0.2),
      eq("stand-2", "stand", -2.1, -1.5),
      mounted("cond-1", "condenser-coil", "stand-2", 0.23, 2.1, -0.3, -0.32),
      eq("valve-1", "valve", 1.0, -0.35, 0.2),
      eq("tee-1", "tee", 2.3, -0.3),
      eq("beaker-a", "beaker", 3.2, 0.55),
      eq("beaker-b", "beaker", 3.4, -1.95),
      eq("pump-1", "pump", -4.8, -3.2, 0.9),
      eq("beaker-c", "beaker", -2.6, -3.4),
    ],
    connections: [
      cn("cn-1", "flask-1", "neck", "cond-1", "vaporIn", "glass"),
      cn("cn-2", "cond-1", "condensateOut", "valve-1", "a"),
      cn("cn-3", "valve-1", "b", "tee-1", "a"),
      cn("cn-4", "tee-1", "b", "beaker-a", "rim"),
      cn("cn-5", "tee-1", "c", "beaker-b", "rim"),
      cn("cn-6", "pump-1", "out", "cond-1", "waterIn"),
      cn("cn-7", "cond-1", "waterOut", "beaker-c", "rim"),
    ],
    camera: { yaw: 0.62, pitch: 0.62, distance: 15.0, target: v(-1.2, 1.2, 0.0) },
  },
  camera: { yaw: 0.62, pitch: 0.62, distance: 16.0, target: v(-1.2, 1.2, 0.0) },
  cueMap: { liquidColors: ["amber", "teal"], flowColor: "pale" },
};

// ── 03 彩雾回环 · 泵驱循环 ─────────────────────────────────────────────────
const chromaticLoop: StageDefinition = {
  id: "chromatic-loop",
  title: { zh: "彩雾回环 · 泵驱循环", en: "Chromatic Loop" },
  description: { zh: "倒进两种颜色，开泵拨阀，看彩雾绕着观察球转圈圈。", en: "Pour in colors, start the pump and steer the hue cloud around the loop." },
  thumbnail: { hue: 286, seed: 43, motif: "loop" },
  availableEquipmentKinds: [...UNIVERSAL_KINDS, "reservoir", "pump", "condenser-straight", "observation-sphere", "flask-erlenmeyer", "cross", "y-mixer"],
  starterScene: {
    equipment: [
      eq("res-1", "reservoir", -5.2, 1.8),
      eq("pump-1", "pump", -3.2, 2.3),
      eq("sphere-1", "observation-sphere", 1.4, 1.6),
      eq("flask-1", "flask-erlenmeyer", 5.2, 0.4),
    ],
    connections: [],
  },
  assembledTemplate: {
    equipment: [
      eq("res-1", "reservoir", -5.5, 1.7),
      eq("pump-1", "pump", -3.5, 2.4),
      eq("tee-1", "tee", -1.5, 1.1),
      eq("valve-a", "valve", -0.3, 2.2),
      eq("stand-1", "stand", 1.6, 3.3),
      mounted("sphere-1", "observation-sphere", "stand-1", 0.23, 1.9, -0.3),
      eq("valve-b", "valve", 0.1, -0.7),
      eq("coil-1", "condenser-straight", 2.1, -1.9, 1.57),
      eq("cross-1", "cross", 4.1, 0.2),
      eq("flask-1", "flask-erlenmeyer", 5.8, 0.8, -0.6),
    ],
    connections: [
      cn("cn-1", "res-1", "drain", "pump-1", "in"),
      cn("cn-2", "pump-1", "out", "tee-1", "a"),
      cn("cn-3", "tee-1", "b", "valve-a", "a"),
      cn("cn-4", "valve-a", "b", "sphere-1", "in"),
      cn("cn-5", "sphere-1", "out", "cross-1", "a"),
      cn("cn-6", "tee-1", "c", "valve-b", "a"),
      cn("cn-7", "valve-b", "b", "coil-1", "vaporIn"),
      cn("cn-8", "coil-1", "condensateOut", "cross-1", "b"),
      cn("cn-9", "cross-1", "c", "flask-1", "neck"),
      cn("cn-10", "cross-1", "d", "res-1", "return"),
    ],
    camera: { yaw: 0.5, pitch: 0.7, distance: 15.5, target: v(0.2, 0.9, 0.2) },
  },
  camera: { yaw: 0.5, pitch: 0.7, distance: 16.5, target: v(0.2, 0.9, 0.2) },
  cueMap: { liquidColors: ["violet", "teal", "amber"], flowColor: "violet" },
};

// ── 04 云雾晶雨 · 双路混合与沉降 ───────────────────────────────────────────
const crystalRain: StageDefinition = {
  id: "crystal-rain",
  title: { zh: "云雾晶雨 · 双路混合与沉降", en: "Crystal Rain" },
  description: { zh: "两种演示液在 Y 口相遇，雾团翻涌，晶粒像雨一样落进瓶底。", en: "Two demo liquids meet at the Y-junction; mist billows and crystal rain settles below." },
  thumbnail: { hue: 46, seed: 59, motif: "rain" },
  availableEquipmentKinds: [...UNIVERSAL_KINDS, "reservoir", "settling-bottle", "y-mixer", "flask-three-neck"],
  starterScene: {
    equipment: [
      eq("res-a", "reservoir", -3.2, -1.2),
      eq("res-b", "reservoir", 2.8, -1.2),
      eq("settle-1", "settling-bottle", 0.6, 1.2),
      eq("beaker-1", "beaker", 3.0, 2.2),
    ],
    connections: [],
  },
  assembledTemplate: {
    equipment: [
      eq("stand-a", "stand", -3.6, -1.9),
      mounted("res-a", "reservoir", "stand-a", 0.23, 2.95, 0.12),
      eq("stand-b", "stand", 3.1, -1.9),
      mounted("res-b", "reservoir", "stand-b", 0.23, 2.95, 0.12),
      eq("valve-a", "valve", -2.0, 0.4),
      eq("valve-b", "valve", 1.7, 0.4),
      eq("stand-c", "stand", 0, -0.9),
      mounted("mix-1", "y-mixer", "stand-c", 0.23, 2.95, -0.3),
      eq("settle-1", "settling-bottle", 0.7, 1.6),
      eq("cup-top", "beaker", 3.2, 1.7),
      eq("tray-low", "beaker", -1.5, 1.8),
    ],
    connections: [
      cn("cn-1", "res-a", "drain", "valve-a", "a"),
      cn("cn-2", "valve-a", "b", "mix-1", "a"),
      cn("cn-3", "res-b", "drain", "valve-b", "a"),
      cn("cn-4", "valve-b", "b", "mix-1", "b"),
      cn("cn-5", "mix-1", "c", "settle-1", "topIn"),
      cn("cn-6", "settle-1", "topOut", "cup-top", "rim"),
      cn("cn-7", "settle-1", "bottomOut", "tray-low", "rim"),
    ],
    camera: { yaw: 0.42, pitch: 0.64, distance: 15.0, target: v(0.2, 1.3, -0.2) },
  },
  camera: { yaw: 0.42, pitch: 0.64, distance: 16.0, target: v(0.2, 1.3, -0.2) },
  cueMap: { liquidColors: ["sky", "amber"], flowColor: "pale" },
};

// ── 05 脉冲剧场 · 多级气压秀 ───────────────────────────────────────────────
const pulseTheatre: StageDefinition = {
  id: "pulse-theatre",
  title: { zh: "脉冲剧场 · 多级气压秀", en: "Pulse Theatre" },
  description: { zh: "开泵蓄压，按下脉冲按钮，看观察球里绽开一圈短促的光雾爆发。", en: "Charge with the pump, hit pulse and enjoy a brief harmless burst inside the sphere." },
  thumbnail: { hue: 322, seed: 73, motif: "pulse" },
  availableEquipmentKinds: [...UNIVERSAL_KINDS, "washing-bottle", "pump", "observation-sphere", "expansion-ball", "gas-collecting-bottle", "u-tube"],
  starterScene: {
    equipment: [
      eq("wash-1", "washing-bottle", -5.6, -0.6),
      eq("pump-1", "pump", -3.8, 0.8),
      eq("sphere-1", "observation-sphere", 0.8, 0.2),
      eq("expand-1", "expansion-ball", 3.0, -1.0),
    ],
    connections: [],
  },
  assembledTemplate: {
    equipment: [
      eq("wash-1", "washing-bottle", -6.1, -0.9),
      eq("pump-1", "pump", -4.1, 0.7),
      eq("tee-1", "tee", -2.5, -0.1),
      eq("valve-m", "valve", -1.3, 1.0),
      eq("stand-1", "stand", 0.7, 2.2),
      mounted("sphere-1", "observation-sphere", "stand-1", 0.23, 1.9, -0.3),
      eq("expand-1", "expansion-ball", 3.1, -1.3),
      eq("collect-1", "gas-collecting-bottle", 5.2, 0.3),
      eq("valve-t", "valve", -1.7, -1.9),
      eq("u-1", "u-tube", 0.9, -2.7),
    ],
    connections: [
      cn("cn-1", "wash-1", "gasOut", "pump-1", "in"),
      cn("cn-2", "pump-1", "out", "tee-1", "a"),
      cn("cn-3", "tee-1", "b", "valve-m", "a"),
      cn("cn-4", "valve-m", "b", "sphere-1", "in"),
      cn("cn-5", "sphere-1", "out", "expand-1", "in"),
      cn("cn-6", "expand-1", "out", "collect-1", "in"),
      cn("cn-7", "tee-1", "c", "valve-t", "a"),
      cn("cn-8", "valve-t", "b", "u-1", "in"),
      cn("cn-9", "u-1", "out", "collect-1", "vent"),
    ],
    camera: { yaw: 0.48, pitch: 0.62, distance: 15.5, target: v(0.1, 1.1, -0.3) },
  },
  camera: { yaw: 0.48, pitch: 0.62, distance: 16.5, target: v(0.1, 1.1, -0.3) },
  cueMap: { liquidColors: ["violet", "teal"], flowColor: "violet", burstColor: "magenta" },
};

export const STAGES: readonly StageDefinition[] = [bubbleRelay, silverCondenser, chromaticLoop, crystalRain, pulseTheatre];

const STAGE_INDEX = new Map(STAGES.map(s => [s.id, s]));

export interface StageSummary { id: string; title: { zh: string; en: string }; description: { zh: string; en: string }; thumbnail: StageDefinition["thumbnail"] }

export function listStages(): StageSummary[] {
  return STAGES.map(s => ({ id: s.id, title: s.title, description: s.description, thumbnail: s.thumbnail }));
}

export function getStage(id: string): StageDefinition | null { return STAGE_INDEX.get(id) ?? null; }
