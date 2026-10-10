/**
 * 化学模拟实验台（3D 娱乐玩具）文档模型。纯数据层：不 import React/Three/DOM/网络。
 * 这里没有任何目标判定、评分、安全锁或化学正确性概念——关卡只是场景主题与器材组合。
 */

/** 世界尺度约定：1 unit ≈ 10cm 展示比例，台面 Y=0，可用区 X∈[-8,8]、Z∈[-4,4]。 */
export interface Vec3 { x: number; y: number; z: number }

/** 单位四元数分量顺序 [x, y, z, w]；文档中只允许有限值。 */
export type Quat = [number, number, number, number];

export interface Pose3D { position: Vec3; rotation: Quat }

export const IDENTITY_QUAT: Quat = [0, 0, 0, 1];

export function pose(position: Vec3, rotation: Quat = IDENTITY_QUAT): Pose3D { return { position, rotation }; }

/**
 * 端口插座类别——只是“外观上能不能插接”的标签，不是化学安全判断。
 * tube/glassJoint/gas/water 之间可由软管互通；liquid 是容器口（接收倾倒），不可接管。
 */
export type SocketClass = "tube" | "glassJoint" | "gas" | "water" | "liquid";

export interface PortSpec {
  id: string;
  localPosition: Vec3;
  /** 插接方向（局部坐标单位向量），软管曲线的起始法线。 */
  localDirection: Vec3;
  socketClass: SocketClass;
  maxLinks: number;
  /** 可视端口半径（world unit），渲染与命中壳放大都从它派生。 */
  visualRadius: number;
}

export type ControlValue = number | boolean;

export interface ControlSpec {
  id: string;
  kind: "toggle" | "dial";
  /** dial 的离散档位（如泵速 慢/中/快）；toggle 无档位。 */
  steps?: number[];
  default: ControlValue;
}

/** 器材内部的通路（如洗气瓶进气→出气、泵 in→out），仅用于演出流向传播。 */
export interface InternalEdge {
  from: string;
  to: string;
  /** 需要某开关处于开启状态才通路（如阀门、泵电源）。 */
  requiresControlOn?: string;
}

export type EquipmentFamily =
  | "glassware"    // 透明玻璃容器
  | "connector"    // 塞/三通/四通/阀等接插件
  | "stand"        // 支架挂载
  | "heat"         // 热源
  | "machine"      // 泵等小机械
  | "reagent";     // 虚拟试剂瓶

/** 器材在近物体气泡中可玩的动作标签。它不表达步骤、目标或评分。 */
export type EquipmentInteraction = "pour" | "pulse" | "rotate";

export interface EquipmentSpec {
  kind: string;
  family: EquipmentFamily;
  /** 中英短名（气泡/端口提示用），不含操作指导文案。 */
  label: { zh: string; en: string };
  /** 近似放置包围盒（局部坐标）。默认底面中心为原点；横放冷凝管等器材可声明以几何中心为原点。 */
  bounds: { width: number; depth: number; height: number };
  boundsOrigin?: "base" | "center";
  ports: PortSpec[];
  /** 交互/特效锚点（局部坐标）：rim、pourLip、heatZone、wick、flame、liquidSurface、clampPoint 等。 */
  anchors: Record<string, Vec3>;
  /** 能否被铁架台夹持（沿 rail 挂载）。 */
  clampable: boolean;
  controls: ControlSpec[];
  internalEdges: InternalEdge[];
  /** 初始演示内容（一般为空；试剂瓶按色液预置）。 */
  defaultContents?: VisualContents;
  /** 气泡与键盘入口读取的能力声明；controls/anchors 提供具体参数。 */
  interactions?: readonly EquipmentInteraction[];
  /** 视觉微调参数（渲染层消费；如蛇形螺距、火焰高度）。 */
  visualTuning?: Record<string, number>;
}

export interface VisualContents {
  /** 液位 0..1（相对容器内腔）。 */
  fill: number;
  colorId?: string;
  cloudiness?: number;
  sediment?: number;
}

export interface EquipmentInstance {
  id: string;
  kind: string;
  /** 顶层（未被挂载时）的世界姿态。 */
  pose: Pose3D;
  /** 挂载父器材（铁架台）的实例 id；挂载时 world 姿态由 parent + localPose 递归求出。 */
  parentMountId?: string;
  /** 相对挂载父原点的局部姿态（挂载时的唯一权威）。 */
  localPose?: Pose3D;
  controls: Record<string, ControlValue>;
  visualContents?: VisualContents;
}

export interface PortRef { equipmentId: string; portId: string }

export interface Connection {
  id: string;
  a: PortRef;
  b: PortRef;
  style: "tube" | "glass";
  /** 软管松弛度 0..1（影响垂弧深度）；缺省 0.5。 */
  slack?: number;
}

export interface CameraPose {
  /** 弧度；yaw 绕 Y、pitch 俯角（正值从上往下看）。 */
  yaw: number;
  pitch: number;
  distance: number;
  target: Vec3;
}

/** 关卡内的一份可加载场景（初始摆放或 AUTO 预组装模板）。 */
export interface StageScene {
  equipment: EquipmentInstance[];
  connections: Connection[];
  camera?: CameraPose;
}

/** 演出绑定：哪个器材族/控制变化时在哪些锚点播放什么强度的 cue（纯视觉参数）。 */
export interface StageCueMap {
  /** 该关可选演示液颜色（虚构物料，如 teal/amber/violet）。 */
  liquidColors: string[];
  /** 主流动粒子颜色。 */
  flowColor?: string;
  /** 爆发/脉冲特效颜色（仅 pulse-theatre 使用）。 */
  burstColor?: string;
}

export interface StageDefinition {
  id: string;
  title: { zh: string; en: string };
  description: { zh: string; en: string };
  /** 目录缩略图的原创生成参数（色调/构图种子），不是外链图片。 */
  thumbnail: { hue: number; seed: number; motif: string };
  availableEquipmentKinds: string[];
  starterScene: StageScene;
  assembledTemplate: StageScene;
  camera: CameraPose;
  cueMap: StageCueMap;
}

export interface LabDocument {
  schemaVersion: 1;
  stageId: string;
  name: string;
  equipment: EquipmentInstance[];
  connections: Connection[];
  revision: number;
  lastEditedAt: number;
}

export type LabAction =
  | { type: "add"; kind: string; pose: Pose3D; id?: string }
  | { type: "move"; id: string; pose: Pose3D }
  | { type: "rotate"; id: string; rotation: Quat }
  | { type: "attach"; id: string; parentMountId: string; localPose: Pose3D }
  | { type: "detach"; id: string; pose: Pose3D }
  | { type: "remove"; id: string }
  | { type: "connect"; a: PortRef; b: PortRef; style?: "tube" | "glass" }
  | { type: "disconnect"; connectionId: string }
  | { type: "setControl"; id: string; controlId: string; value: ControlValue }
  | { type: "setVisualContents"; id: string; contents: Partial<VisualContents> }
  | { type: "replaceAuto" }
  | { type: "reset" };

export interface ActionResult {
  ok: boolean;
  document: LabDocument;
  /** 仅说明 UI 原因（如“这个接口已被占用”），永不评价实验对错。 */
  hint?: string;
}

export interface WorldAnchor {
  equipmentId: string;
  portId: string;
  position: Vec3;
  direction: Vec3;
}

/** 所有客户端共用的最小器材能力投影。 */
export interface EquipmentCapabilities {
  canPour: boolean;
  canPulse: boolean;
  canRotate: boolean;
}

/** 演出触发事件（Effects 消费；不进入文档、不进入撤销历史）。 */
export type CueTrigger =
  | { kind: "liquid-added"; equipmentId: string; colorId?: string }
  | { kind: "liquid-poured"; fromEquipmentId: string; toEquipmentId: string; colorId?: string }
  | { kind: "heat-on"; equipmentId: string }
  | { kind: "heat-off"; equipmentId: string }
  | { kind: "pump-on"; equipmentId: string }
  | { kind: "pump-off"; equipmentId: string }
  | { kind: "valve-open"; equipmentId: string }
  | { kind: "valve-close"; equipmentId: string }
  | { kind: "pipe-connected"; connectionId: string }
  | { kind: "pipe-disconnected"; at: WorldAnchor }
  | { kind: "pulse-activated"; equipmentId: string }
  | { kind: "vessel-moved"; equipmentId: string };

/** 性能护栏（渲染实例与 JSON 防护），不是实验规范判定。 */
export const LIMITS = {
  maxEquipment: 50,
  maxConnections: 80,
  maxNameLength: 60,
  maxDocumentBytes: 150_000,
} as const;
