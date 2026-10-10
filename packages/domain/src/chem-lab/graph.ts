/**
 * 端口连接图：管理“哪个口接哪个口”与演出流向的可视传播。
 * 节点是 PortRef；边 = 外部软管连接 + 器材内部通路。方向只用于流动 cue 的显示，
 * 不做任何流量/压力/化学输送能力判断。
 */
import { getEquipmentSpec, portsCompatible } from "./equipment.ts";
import type { Connection, LabDocument, PortRef, PortSpec } from "./types.ts";

export function findEquipment(doc: LabDocument, equipmentId: string) {
  return doc.equipment.find(e => e.id === equipmentId) ?? null;
}

export function getPortSpec(doc: LabDocument, ref: PortRef): PortSpec | null {
  const eq = findEquipment(doc, ref.equipmentId);
  if (!eq) return null;
  return getEquipmentSpec(eq.kind)?.ports.find(p => p.id === ref.portId) ?? null;
}

export function portExists(doc: LabDocument, ref: PortRef): boolean {
  return getPortSpec(doc, ref) !== null;
}

export function connectionsAtPort(doc: LabDocument, ref: PortRef): Connection[] {
  return doc.connections.filter(c =>
    (c.a.equipmentId === ref.equipmentId && c.a.portId === ref.portId)
    || (c.b.equipmentId === ref.equipmentId && c.b.portId === ref.portId));
}

export function portOccupied(doc: LabDocument, ref: PortRef): boolean {
  const spec = getPortSpec(doc, ref);
  if (!spec) return true; // 端口不存在按占用处理，防止接上幽灵口
  return connectionsAtPort(doc, ref).length >= spec.maxLinks;
}

export function sameRef(a: PortRef, b: PortRef): boolean {
  return a.equipmentId === b.equipmentId && a.portId === b.portId;
}

/** 连接原因检查（仅技术性：存在、兼容、未占用、不接自己同一个口）。 */
export function connectionBlockReason(doc: LabDocument, a: PortRef, b: PortRef): string | null {
  if (sameRef(a, b)) return "same_port";
  if (!portExists(doc, a) || !portExists(doc, b)) return "port_missing";
  if (portOccupied(doc, a)) return "port_a_busy";
  if (portOccupied(doc, b)) return "port_b_busy";
  const aSpec = getPortSpec(doc, a)!;
  const bSpec = getPortSpec(doc, b)!;
  if (!portsCompatible(aSpec, bSpec)) return "not_connectable";
  return null;
}

function controlIsOn(eq: { controls: Record<string, number | boolean> }, controlId?: string): boolean {
  if (!controlId) return true;
  const value = eq.controls[controlId];
  return value === true || (typeof value === "number" && value > 0);
}

/** 当前生效的内部有向边（含闭合的阀门/停机泵被排除后的）。 */
function activeInternalEdges(doc: LabDocument): { from: PortRef; to: PortRef }[] {
  const edges: { from: PortRef; to: PortRef }[] = [];
  for (const eq of doc.equipment) {
    const spec = getEquipmentSpec(eq.kind);
    if (!spec) continue;
    for (const edge of spec.internalEdges) {
      if (!controlIsOn(eq, edge.requiresControlOn)) continue;
      if (!spec.ports.some(p => p.id === edge.from) || !spec.ports.some(p => p.id === edge.to)) continue;
      edges.push({ from: { equipmentId: eq.id, portId: edge.from }, to: { equipmentId: eq.id, portId: edge.to } });
    }
  }
  return edges;
}

const refKey = (r: PortRef) => `${r.equipmentId}:${r.portId}`;

/**
 * 从源端口出发，沿“内部有向边 + 外部软管（可反向穿越）”广度优先传播，
 * 得到应播放流动 cue 的外部连接集合与每根管的可视流向。
 * visited 防止闭环死循环；控制状态改变只影响通路，不影响文档事实。
 */
export function reachableCueEdges(
  doc: LabDocument,
  sources: PortRef[],
): { edges: Set<string>; directions: Map<string, { from: PortRef; to: PortRef }> } {
  const edges = new Set<string>();
  const directions = new Map<string, { from: PortRef; to: PortRef }>();
  const visited = new Set<string>();
  const queue: PortRef[] = [];
  for (const s of sources) {
    if (!portExists(doc, s)) continue;
    if (!visited.has(refKey(s))) { visited.add(refKey(s)); queue.push(s); }
  }
  const internals = activeInternalEdges(doc);
  while (queue.length > 0) {
    const current = queue.shift()!;
    // 内部有向边：器材内穿行
    for (const edge of internals) {
      if (refKey(edge.from) !== refKey(current)) continue;
      const key = refKey(edge.to);
      if (visited.has(key)) continue;
      visited.add(key);
      queue.push(edge.to);
    }
    // 外部软管：双向可视穿越，记录该管的流动方向
    for (const conn of doc.connections) {
      let other: PortRef | null = null;
      if (refKey(conn.a) === refKey(current)) other = conn.b;
      else if (refKey(conn.b) === refKey(current)) other = conn.a;
      if (!other) continue;
      edges.add(conn.id);
      if (!directions.has(conn.id)) directions.set(conn.id, { from: current, to: other });
      const key = refKey(other);
      if (!visited.has(key)) { visited.add(key); queue.push(other); }
    }
  }
  return { edges, directions };
}

/** 正在运行的“驱动源”端口：开着电源的泵出口、点燃的酒精灯所在容器等由演出层给出；这里汇集辅助查询。 */
export function pumpOutletPorts(doc: LabDocument): PortRef[] {
  const out: PortRef[] = [];
  for (const eq of doc.equipment) {
    const spec = getEquipmentSpec(eq.kind);
    if (!spec || eq.kind !== "pump") continue;
    if (!controlIsOn(eq, "power")) continue;
    const outPort = spec.ports.find(p => p.id === "out");
    if (outPort) out.push({ equipmentId: eq.id, portId: outPort.id });
  }
  return out;
}
