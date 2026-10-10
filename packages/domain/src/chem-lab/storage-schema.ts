/**
 * 本地存档序列化：只保存文档数据，不保存 WebGL 对象。
 * 解析严格拒绝非法输入（旧格式不兼容，不做转换 shim）。
 */
import { getEquipmentSpec } from "./equipment.ts";
import { validateStageScene } from "./document.ts";
import { LIMITS, type Connection, type EquipmentInstance, type LabDocument, type Quat, type StageScene, type Vec3 } from "./types.ts";

export class LabStorageError extends Error {
  code: string;
  constructor(code: string) { super(code); this.name = "LabStorageError"; this.code = code; }
}

const finite = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n);

function readVec3(value: unknown, what: string): Vec3 {
  if (typeof value !== "object" || value === null) throw new LabStorageError(`bad_vec3:${what}`);
  const { x, y, z } = value as Record<string, unknown>;
  if (!finite(x) || !finite(y) || !finite(z)) throw new LabStorageError(`bad_vec3:${what}`);
  return { x, y, z };
}

function readQuat(value: unknown, what: string): Quat {
  if (!Array.isArray(value) || value.length !== 4 || !value.every(finite)) throw new LabStorageError(`bad_quat:${what}`);
  const q = value as Quat;
  const length = Math.hypot(q[0], q[1], q[2], q[3]);
  if (length < 0.5 || length > 2) throw new LabStorageError(`bad_quat:${what}`);
  return q;
}

function readControls(value: unknown, what: string): Record<string, number | boolean> {
  if (typeof value !== "object" || value === null) throw new LabStorageError(`bad_controls:${what}`);
  const out: Record<string, number | boolean> = {};
  for (const [key, raw] of Object.entries(value as Record<string, unknown>)) {
    if (key.length > 24) throw new LabStorageError(`bad_control_key:${what}`);
    if (typeof raw === "boolean" || (typeof raw === "number" && Number.isFinite(raw))) out[key] = raw;
    else throw new LabStorageError(`bad_control_value:${what}`);
  }
  return out;
}

function readPose(value: unknown, what: string) {
  if (typeof value !== "object" || value === null) throw new LabStorageError(`bad_pose:${what}`);
  const row = value as Record<string, unknown>;
  return { position: readVec3(row.position, what), rotation: readQuat(row.rotation, what) };
}

function readInstance(value: unknown): EquipmentInstance {
  if (typeof value !== "object" || value === null) throw new LabStorageError("bad_equipment");
  const row = value as Record<string, unknown>;
  const id = row.id, kind = row.kind;
  if (typeof id !== "string" || id.length === 0 || id.length > 40) throw new LabStorageError("bad_equipment_id");
  if (typeof kind !== "string" || !getEquipmentSpec(kind)) throw new LabStorageError(`unknown_kind:${String(kind)}`);
  const pose = readPose(row.pose, id);
  const instance: EquipmentInstance = { id, kind, pose, controls: readControls(row.controls ?? {}, id) };
  if (row.parentMountId !== undefined) {
    if (typeof row.parentMountId !== "string") throw new LabStorageError(`bad_parent:${id}`);
    instance.parentMountId = row.parentMountId;
    instance.localPose = row.localPose === undefined ? pose : readPose(row.localPose, id);
  }
  if (row.visualContents !== undefined) {
    const vc = row.visualContents as Record<string, unknown>;
    const fill = vc.fill;
    if (!finite(fill) || fill < 0 || fill > 1) throw new LabStorageError(`bad_fill:${id}`);
    instance.visualContents = {
      fill,
      ...(typeof vc.colorId === "string" && vc.colorId.length <= 16 ? { colorId: vc.colorId } : {}),
      ...(finite(vc.cloudiness) ? { cloudiness: Math.min(1, Math.max(0, vc.cloudiness as number)) } : {}),
      ...(finite(vc.sediment) ? { sediment: Math.min(1, Math.max(0, vc.sediment as number)) } : {}),
    };
  }
  return instance;
}

function readConnection(value: unknown): Connection {
  if (typeof value !== "object" || value === null) throw new LabStorageError("bad_connection");
  const row = value as Record<string, unknown>;
  const id = row.id;
  if (typeof id !== "string" || id.length === 0 || id.length > 40) throw new LabStorageError("bad_connection_id");
  const readRef = (ref: unknown, side: string) => {
    if (typeof ref !== "object" || ref === null) throw new LabStorageError(`bad_ref:${id}:${side}`);
    const { equipmentId, portId } = ref as Record<string, unknown>;
    if (typeof equipmentId !== "string" || typeof portId !== "string") throw new LabStorageError(`bad_ref:${id}:${side}`);
    return { equipmentId, portId };
  };
  const style = row.style === "glass" ? "glass" : "tube";
  const slack = finite(row.slack) ? Math.min(1, Math.max(0, row.slack as number)) : 0.5;
  return { id, a: readRef(row.a, "a"), b: readRef(row.b, "b"), style, slack };
}

/** 严格解析：schemaVersion 必须为 1；拒绝幽灵端口、未知设备、父级环、数量/体积超限。 */
export function parseDocument(raw: string): LabDocument {
  if (typeof raw !== "string" || raw.length === 0) throw new LabStorageError("empty");
  if (raw.length > LIMITS.maxDocumentBytes) throw new LabStorageError("too_large");
  let value: unknown;
  try { value = JSON.parse(raw); } catch { throw new LabStorageError("bad_json"); }
  if (typeof value !== "object" || value === null) throw new LabStorageError("bad_root");
  const row = value as Record<string, unknown>;
  if (row.schemaVersion !== 1) throw new LabStorageError("unsupported_version");
  if (typeof row.stageId !== "string" || row.stageId.length === 0 || row.stageId.length > 40) throw new LabStorageError("bad_stage");
  if (typeof row.name !== "string" || row.name.length > LIMITS.maxNameLength) throw new LabStorageError("bad_name");
  if (!Array.isArray(row.equipment)) throw new LabStorageError("bad_equipment_list");
  if (row.equipment.length > LIMITS.maxEquipment) throw new LabStorageError("too_many_equipment");
  if (!Array.isArray(row.connections)) throw new LabStorageError("bad_connection_list");
  if (row.connections.length > LIMITS.maxConnections) throw new LabStorageError("too_many_connections");
  if (!finite(row.revision) || (row.revision as number) < 1) throw new LabStorageError("bad_revision");
  const scene: StageScene = {
    equipment: row.equipment.map(readInstance),
    connections: row.connections.map(readConnection),
  };
  const problem = validateStageScene(scene);
  if (problem) throw new LabStorageError(problem);
  return {
    schemaVersion: 1,
    stageId: row.stageId,
    name: row.name,
    equipment: scene.equipment,
    connections: scene.connections,
    revision: row.revision as number,
    lastEditedAt: finite(row.lastEditedAt) ? row.lastEditedAt as number : Date.now(),
  };
}

/** 序列化前先校验（NaN/Infinity 会自然变 null，先挡住非法结构）。 */
export function serializeDocument(doc: LabDocument): string {
  const problem = validateStageScene({ equipment: doc.equipment, connections: doc.connections });
  if (problem) throw new LabStorageError(problem);
  const out = JSON.stringify(doc);
  if (out.length > LIMITS.maxDocumentBytes) throw new LabStorageError("too_large");
  return out;
}
