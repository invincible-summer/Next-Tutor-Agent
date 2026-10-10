/**
 * 文档操作入口：装配/交互动作的唯一轻量状态层。
 * 所有 apply* 返回新文档（失败保留原文档）；不联网、不做化学评价。
 */
import { defaultControls, equipmentBenchY, getEquipmentSpec } from "./equipment.ts";
import { connectionBlockReason, findEquipment } from "./graph.ts";
import {
  IDENTITY_QUAT, LIMITS, type EquipmentInstance, type LabAction, type LabDocument,
  type Pose3D, type Quat, type StageDefinition, type StageScene, type Vec3,
} from "./types.ts";

export class LabError extends Error {
  code: string;
  constructor(code: string, message?: string) { super(message ?? code); this.name = "LabError"; this.code = code; }
}

// ── 小型四元数工具（仅用于文档内父子姿态合成；端口世界锚点一律由渲染层用 Three 变换求出） ──
function quatNormalize(q: Quat): Quat {
  const l = Math.hypot(q[0], q[1], q[2], q[3]) || 1;
  return [q[0] / l, q[1] / l, q[2] / l, q[3] / l];
}
function quatMultiply(a: Quat, b: Quat): Quat {
  const [ax, ay, az, aw] = a, [bx, by, bz, bw] = b;
  return quatNormalize([
    aw * bx + ax * bw + ay * bz - az * by,
    aw * by - ax * bz + ay * bw + az * bx,
    aw * bz + ax * by - ay * bx + az * bw,
    aw * bw - ax * bx - ay * by - az * bz,
  ]);
}
function quatApply(q: Quat, v: Vec3): Vec3 {
  const [x, y, z, w] = q;
  const tx = 2 * (y * v.z - z * v.y), ty = 2 * (z * v.x - x * v.z), tz = 2 * (x * v.y - y * v.x);
  return {
    x: v.x + w * tx + (y * tz - z * ty),
    y: v.y + w * ty + (z * tx - x * tz),
    z: v.z + w * tz + (x * ty - y * tx),
  };
}
export function composePose(parent: Pose3D, local: Pose3D): Pose3D {
  return {
    position: {
      x: parent.position.x + quatApply(parent.rotation, local.position).x,
      y: parent.position.y + quatApply(parent.rotation, local.position).y,
      z: parent.position.z + quatApply(parent.rotation, local.position).z,
    },
    rotation: quatMultiply(parent.rotation, local.rotation),
  };
}

export function instanceWorldPose(doc: LabDocument, id: string): Pose3D | null {
  const eq = findEquipment(doc, id);
  if (!eq) return null;
  if (!eq.parentMountId) return eq.pose;
  const parent = findEquipment(doc, eq.parentMountId);
  if (!parent) return eq.pose; // 父缺失时不悬空：按顶层姿态兜底
  const parentWorld = instanceWorldPose(doc, parent.id);
  if (!parentWorld) return eq.pose;
  return composePose(parentWorld, eq.localPose ?? { position: { x: 0, y: 0, z: 0 }, rotation: IDENTITY_QUAT });
}

function isMountChain(doc: LabDocument, childId: string, ancestorId: string): boolean {
  let current = findEquipment(doc, childId);
  const seen = new Set<string>();
  while (current?.parentMountId) {
    if (seen.has(current.id)) return false;
    seen.add(current.id);
    if (current.parentMountId === ancestorId) return true;
    current = findEquipment(doc, current.parentMountId);
  }
  return false;
}

// ── 实例规范化与 ID ────────────────────────────────────────────────────────
export function normalizeInstance(raw: Omit<EquipmentInstance, "controls"> & { controls?: Record<string, number | boolean> }): EquipmentInstance {
  const spec = getEquipmentSpec(raw.kind);
  const controls = { ...(spec ? defaultControls(raw.kind) : {}), ...(raw.controls ?? {}) };
  const instance: EquipmentInstance = { id: raw.id, kind: raw.kind, pose: raw.pose, controls };
  if (raw.parentMountId !== undefined) instance.parentMountId = raw.parentMountId;
  if (raw.localPose !== undefined) instance.localPose = raw.localPose;
  if (raw.visualContents !== undefined) {
    const contents = sanitizeVisualContents(raw.visualContents);
    if (contents) instance.visualContents = contents;
  }
  return instance;
}

function nextSeqId(doc: LabDocument, prefix: string): string {
  let max = 0;
  const re = new RegExp(`^${prefix}-(\\d+)$`);
  for (const id of idsIn(doc)) { const m = re.exec(id); if (m) max = Math.max(max, Number(m[1])); }
  return `${prefix}-${max + 1}`;
}
function* idsIn(doc: LabDocument): Generator<string> {
  yield* doc.equipment.map(e => e.id);
  yield* doc.connections.map(c => c.id);
}

function clone<T>(value: T): T { return JSON.parse(JSON.stringify(value)) as T; }

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function clamp01(value: unknown, fallback = 0): number {
  return finite(value) ? Math.min(1, Math.max(0, value)) : fallback;
}

function sanitizeVisualContents(value: EquipmentInstance["visualContents"] | undefined): EquipmentInstance["visualContents"] | undefined {
  if (!value) return undefined;
  const next: NonNullable<EquipmentInstance["visualContents"]> = { fill: clamp01(value.fill) };
  if (typeof value.colorId === "string" && value.colorId.length <= 32) next.colorId = value.colorId;
  if (value.cloudiness !== undefined) next.cloudiness = clamp01(value.cloudiness);
  if (value.sediment !== undefined) next.sediment = clamp01(value.sediment);
  return next;
}

// ── 文档创建 ───────────────────────────────────────────────────────────────
export function createStageDocument(stage: StageDefinition, name?: string): LabDocument {
  const scene = instantiateScene(stage.starterScene);
  return {
    schemaVersion: 1,
    stageId: stage.id,
    name: name ?? stage.title.zh,
    equipment: scene.equipment,
    connections: scene.connections,
    revision: 1,
    lastEditedAt: Date.now(),
  };
}

/** 深拷贝场景并补全控制默认值（模板中的 controls 是唯一显式状态）。 */
export function instantiateScene(scene: StageScene): StageScene {
  const equipment = scene.equipment.map(e => normalizeInstance(clone(e)));
  const connections = scene.connections.map(c => clone(c));
  return { ...scene, equipment, connections };
}

// ── 动作应用 ───────────────────────────────────────────────────────────────
export function applyLabAction(doc: LabDocument, action: LabAction, stage: StageDefinition): { ok: boolean; document: LabDocument; hint?: string } {
  const next = applyActionInner(doc, action, stage);
  if (next) return { ok: true, document: next };
  const hint = hintFor(action);
  return hint === undefined ? { ok: false, document: doc } : { ok: false, document: doc, hint };
}

function applyActionInner(doc: LabDocument, action: LabAction, stage: StageDefinition): LabDocument | null {
  const bumped = (): LabDocument => ({ ...doc, revision: doc.revision + 1, lastEditedAt: Date.now() });
  switch (action.type) {
    case "add": {
      if (!getEquipmentSpec(action.kind)) return null;
      if (doc.equipment.length >= LIMITS.maxEquipment) return null;
      const id = action.id ?? nextSeqId(doc, "eq");
      if (findEquipment(doc, id)) return null;
      return { ...bumped(), equipment: [...doc.equipment, normalizeInstance({ id, kind: action.kind, pose: action.pose })] };
    }
    case "move": {
      const eq = findEquipment(doc, action.id);
      if (!eq) return null;
      if (eq.parentMountId) {
        // 挂载件移动存为相对父的局部姿态（world→local 由交互层换算后传入 local 语义）
        return { ...bumped(), equipment: doc.equipment.map(e => e.id === action.id ? { ...e, localPose: action.pose } : e) };
      }
      return { ...bumped(), equipment: doc.equipment.map(e => e.id === action.id ? { ...e, pose: action.pose } : e) };
    }
    case "rotate": {
      const eq = findEquipment(doc, action.id);
      if (!eq) return null;
      return { ...bumped(), equipment: doc.equipment.map(e => e.id === action.id
        ? (e.parentMountId ? { ...e, localPose: { position: e.localPose?.position ?? { x: 0, y: 0, z: 0 }, rotation: action.rotation } } : { ...e, pose: { position: e.pose.position, rotation: action.rotation } })
        : e) };
    }
    case "attach": {
      const child = findEquipment(doc, action.id);
      const parent = findEquipment(doc, action.parentMountId);
      if (!child || !parent) return null;
      if (child.id === parent.id || isMountChain(doc, parent.id, child.id)) return null; // 防引用环
      const parentSpec = getEquipmentSpec(parent.kind);
      if (!parentSpec || parentSpec.family !== "stand") return null;
      const childSpec = getEquipmentSpec(child.kind);
      if (!childSpec?.clampable) return null;
      return {
        ...bumped(),
        equipment: doc.equipment.map(e => e.id === action.id
          ? { ...e, parentMountId: action.parentMountId, localPose: action.localPose, pose: instanceWorldPose(doc, e.id) ?? e.pose }
          : e),
      };
    }
    case "detach": {
      const eq = findEquipment(doc, action.id);
      if (!eq?.parentMountId) return null;
      return {
        ...bumped(),
        equipment: doc.equipment.map(e => {
          if (e.id !== action.id) return e;
          const { parentMountId: _p, localPose: _l, ...rest } = e;
          return { ...rest, pose: action.pose };
        }),
      };
    }
    case "remove": {
      const eq = findEquipment(doc, action.id);
      if (!eq) return null;
      // 挂载子件脱挂落台（近似世界姿态落地），不让结构凭空悬空
      const children = doc.equipment.filter(e => e.parentMountId === action.id);
      const landed = children.map(child => {
        const world = instanceWorldPose(doc, child.id) ?? child.pose;
        const spec = getEquipmentSpec(child.kind);
        const benchY = spec ? equipmentBenchY(spec) : 0;
        const { parentMountId: _p, localPose: _l, ...rest } = child;
        return { ...rest, pose: { position: { x: world.position.x, y: benchY, z: world.position.z }, rotation: world.rotation } };
      });
      return {
        ...bumped(),
        equipment: [...doc.equipment.filter(e => e.id !== action.id && e.parentMountId !== action.id), ...landed],
        connections: doc.connections.filter(c => c.a.equipmentId !== action.id && c.b.equipmentId !== action.id),
      };
    }
    case "connect": {
      if (doc.connections.length >= LIMITS.maxConnections) return null;
      const reason = connectionBlockReason(doc, action.a, action.b);
      if (reason) return null;
      return { ...bumped(), connections: [...doc.connections, { id: nextSeqId(doc, "cn"), a: action.a, b: action.b, style: action.style ?? "tube", slack: 0.5 }] };
    }
    case "disconnect": {
      if (!doc.connections.some(c => c.id === action.connectionId)) return null;
      return { ...bumped(), connections: doc.connections.filter(c => c.id !== action.connectionId) };
    }
    case "setControl": {
      const eq = findEquipment(doc, action.id);
      if (!eq) return null;
      const spec = getEquipmentSpec(eq.kind);
      if (!spec?.controls.some(c => c.id === action.controlId)) return null;
      return { ...bumped(), equipment: doc.equipment.map(e => e.id === action.id ? { ...e, controls: { ...e.controls, [action.controlId]: action.value } } : e) };
    }
    case "setVisualContents": {
      const eq = findEquipment(doc, action.id);
      if (!eq) return null;
      const merged = sanitizeVisualContents({ ...(eq.visualContents ?? { fill: 0 }), ...action.contents });
      if (!merged) return null;
      return { ...bumped(), equipment: doc.equipment.map(e => e.id === action.id ? { ...e, visualContents: merged } : e) };
    }
    case "replaceAuto": return applyAuto(doc, stage);
    case "reset": {
      const scene = instantiateScene(stage.starterScene);
      return { ...bumped(), equipment: scene.equipment, connections: scene.connections };
    }
  }
}

function hintFor(action: LabAction): string | undefined {
  switch (action.type) {
    case "connect": return "port_busy_or_missing";
    case "add": return "limit_reached";
    case "attach": return "mount_not_allowed";
    default: return undefined;
  }
}

// ── AUTO：一次性原子载入预组装模板 ─────────────────────────────────────────
/** 模板技术性完整性校验：引用/端点/坐标有效、初始全静止（不校验化学正确性）。 */
export function validateStageScene(scene: StageScene, opts: { requireIdle?: boolean } = {}): string | null {
  if (scene.equipment.length > LIMITS.maxEquipment) return "too_many_equipment";
  if (scene.connections.length > LIMITS.maxConnections) return "too_many_connections";
  const ids = new Set<string>();
  for (const eq of scene.equipment) {
    if (ids.has(eq.id)) return `duplicate_id:${eq.id}`;
    ids.add(eq.id);
    if (!getEquipmentSpec(eq.kind)) return `unknown_kind:${eq.kind}`;
    const spec = getEquipmentSpec(eq.kind)!;
    const poseValues = [...Object.values(eq.pose.position), ...eq.pose.rotation];
    if (poseValues.some(value => !finite(value))) return `bad_pose:${eq.id}`;
    for (const port of spec.ports) {
      const values = [...Object.values(port.localPosition), ...Object.values(port.localDirection), port.visualRadius, port.maxLinks];
      if (values.some(value => !finite(value))) return `bad_port:${eq.id}:${port.id}`;
    }
    if (eq.localPose) {
      const localValues = [...Object.values(eq.localPose.position), ...eq.localPose.rotation];
      if (localValues.some(value => !finite(value))) return `bad_local_pose:${eq.id}`;
    }
    if (eq.parentMountId) {
      if (!ids.has(eq.parentMountId) && !scene.equipment.some(e => e.id === eq.parentMountId)) return `missing_parent:${eq.id}`;
      const parent = scene.equipment.find(e => e.id === eq.parentMountId);
      if (parent && getEquipmentSpec(parent.kind)?.family !== "stand") return `parent_not_stand:${eq.id}`;
      if (eq.parentMountId === eq.id) return `self_parent:${eq.id}`;
      // A malformed imported template must not create a recursive world-pose walk.
      let ancestor = eq.parentMountId;
      const chain = new Set<string>([eq.id]);
      while (ancestor) {
        if (chain.has(ancestor)) return `mount_cycle:${eq.id}`;
        chain.add(ancestor);
        ancestor = scene.equipment.find(e => e.id === ancestor)?.parentMountId ?? "";
      }
    }
    if (opts.requireIdle) {
      for (const control of spec.controls) {
        if (control.kind === "toggle" && eq.controls[control.id] === true) return `not_idle:${eq.id}:${control.id}`;
      }
      if ((eq.visualContents?.fill ?? 0) > 0.001 && eq.kind !== "reagent-bottle") return `not_empty:${eq.id}`;
    }
  }
  const probe: LabDocument = {
    schemaVersion: 1,
    stageId: "template",
    name: "template",
    equipment: scene.equipment.map(e => normalizeInstance(clone(e))),
    connections: [],
    revision: 0,
    lastEditedAt: 0,
  };
  const connectionIds = new Set<string>();
  for (const conn of scene.connections) {
    if (connectionIds.has(conn.id) || ids.has(conn.id)) return `duplicate_connection_id:${conn.id}`;
    connectionIds.add(conn.id);
    if (conn.slack !== undefined && (!finite(conn.slack) || conn.slack < 0 || conn.slack > 1)) return `bad_slack:${conn.id}`;
    if (!ids.has(conn.a.equipmentId) || !ids.has(conn.b.equipmentId)) return `ghost_endpoint:${conn.id}`;
    const specA = getEquipmentSpec(scene.equipment.find(e => e.id === conn.a.equipmentId)!.kind);
    const specB = getEquipmentSpec(scene.equipment.find(e => e.id === conn.b.equipmentId)!.kind);
    if (!specA?.ports.some(p => p.id === conn.a.portId)) return `ghost_port:${conn.id}:a`;
    if (!specB?.ports.some(p => p.id === conn.b.portId)) return `ghost_port:${conn.id}:b`;
    const block = connectionBlockReason(probe, conn.a, conn.b);
    if (block) return `connection_${block}:${conn.id}`;
    probe.connections.push(clone(conn));
  }
  return null;
}

/** 同步、原子地把文档替换为关卡预组装快照；模板校验失败抛 LabError 且原文档不变。 */
export function applyAuto(doc: LabDocument, stage: StageDefinition): LabDocument {
  const problem = validateStageScene(stage.assembledTemplate, { requireIdle: true });
  if (problem) throw new LabError("auto_template_invalid", problem);
  const scene = instantiateScene(stage.assembledTemplate);
  return { ...doc, equipment: scene.equipment, connections: scene.connections, revision: doc.revision + 1, lastEditedAt: Date.now() };
}

// ── 轻量快照撤销（≤20 步，无事件日志/时间轴/分叉） ─────────────────────────
export interface LabHistory { past: LabDocument[] }
const HISTORY_CAP = 20;

export function createHistory(): LabHistory { return { past: [] }; }

export function pushHistory(history: LabHistory, document: LabDocument): LabHistory {
  return { past: [...history.past, document].slice(-HISTORY_CAP) };
}

export function undoHistory(history: LabHistory, current: LabDocument): { history: LabHistory; document: LabDocument } {
  if (history.past.length === 0) return { history, document: current };
  const past = [...history.past];
  const document = past.pop()!;
  return { history: { past }, document };
}
