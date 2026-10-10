"use client";

/**
 * 文档 → 场景同步（plan §7.4）：LabDocument 的器材实例映射为 EquipmentFactory
 * 模型，按 domain 递归 world pose 摆放；端口 world anchor 供软管与交互使用；
 * 铁架夹具高度随挂载器材联动。渲染层不回写文档——显示层预览由交互层提交。
 *
 * 命中体系（plan §4.1）：每件模型额外挂三种“隐形放大命中壳”（材质
 * visible:false，Raycaster 不受 mesh 可见性影响）：端口壳（磁吸目标）、
 * 控件壳（旋钮/开关/灯芯直点区）、夹爪壳（沿杆高度拖动），与设备整体
 * pick 代理一起交给交互层按固定优先级取用。
 */
import * as THREE from "three";
import {
  getEquipmentSpec, instanceWorldPose, type EquipmentInstance, type LabDocument,
} from "@next-tutor/domain";
import { createEquipmentModel, type EquipmentModelHandle } from "./EquipmentFactory.ts";
import { MAT, sharedMaterial } from "./EquipmentModels.ts";
import type { QualityTier } from "./SceneController.ts";
import { TubeSystem, type TubePortAnchor } from "./TubeSystem.ts";

const UP = new THREE.Vector3(0, 1, 0);

export type PickKind = "equipment" | "tube" | "port" | "control" | "claw";

export interface PickRef {
  kind: PickKind;
  instanceId?: string;
  equipmentId?: string;
  portId?: string;
  controlId?: string;
  connectionId?: string;
}

/** 直点控件壳的局部位置/半径（dial 只给主开关壳，档位经气泡“转动”）。 */
const CONTROL_ZONES: Record<string, { controlId: string; at: THREE.Vector3; r: number }> = {
  valve: { controlId: "open", at: new THREE.Vector3(0, 0.42, 0), r: 0.3 },
  "alcohol-lamp": { controlId: "lit", at: new THREE.Vector3(0, 0.96, 0), r: 0.26 },
  pump: { controlId: "power", at: new THREE.Vector3(0, 0.5, 0.08), r: 0.28 },
  "hot-plate": { controlId: "power", at: new THREE.Vector3(0.42, 0.2, 0.45), r: 0.24 },
};

/** 铁架夹持点局部 X 偏移 = railBase.x + 0.75（与 stages.ts 挂载规则一致）。 */
export function standGripLocal(standKind = "stand"): THREE.Vector3 {
  const spec = getEquipmentSpec(standKind);
  const rail = spec?.anchors.railBase ?? { x: -0.52, y: 0.22, z: -0.3 };
  return new THREE.Vector3(rail.x + 0.75, 2.6, rail.z);
}

export interface SyncedItem {
  instanceId: string;
  kind: string;
  handle: EquipmentModelHandle;
  portShells: THREE.Mesh[];
  controlShells: THREE.Mesh[];
  clawShell: THREE.Mesh | null;
}

export class SceneSync {
  readonly root = new THREE.Group();
  readonly tubes = new TubeSystem();
  private readonly items = new Map<string, SyncedItem>();
  private readonly tier: QualityTier;
  private readonly hitSphereGeo: THREE.SphereGeometry;

  constructor(tier: QualityTier) {
    this.tier = tier;
    this.root.name = "equipment";
    this.hitSphereGeo = new THREE.SphereGeometry(1, 10, 8);
  }

  /** 差量同步：新增/移除/姿态与视觉状态更新，随后刷新管线锚点。 */
  syncDocument(doc: LabDocument): void {
    const seen = new Set<string>();
    for (const instance of doc.equipment) {
      seen.add(instance.id);
      const existing = this.items.get(instance.id);
      if (existing && existing.kind === instance.kind) {
        this.applyPose(existing.handle, doc, instance);
        existing.handle.update(instance);
        continue;
      }
      if (existing) this.removeItem(instance.id);
      const handle = createEquipmentModel(instance.kind, { tier: this.tier });
      if (!handle) continue;
      handle.group.userData.instanceId = instance.id;
      for (const part of handle.pickParts) {
        part.userData.pickRef = { ...part.userData.pickRef, instanceId: instance.id } as PickRef;
      }
      this.root.add(handle.group);
      const item: SyncedItem = {
        instanceId: instance.id,
        kind: instance.kind,
        handle,
        portShells: this.attachPortShells(handle, instance.id),
        controlShells: this.attachControlShells(handle, instance.id),
        clawShell: instance.kind === "stand" ? this.attachClawShell(handle, instance.id) : null,
      };
      this.items.set(instance.id, item);
      this.applyPose(handle, doc, instance);
      handle.update(instance);
    }
    for (const id of [...this.items.keys()]) {
      if (!seen.has(id)) this.removeItem(id);
    }
    this.root.updateMatrixWorld(true);
    this.syncClawHeights(doc);
    this.rebuildTubes(doc);
  }

  /** 器材被交互层直接移动后调用：只刷新矩阵/夹具/管线，不重建模型。 */
  refreshWorld(doc: LabDocument): void {
    for (const instance of doc.equipment) {
      const item = this.items.get(instance.id);
      if (item) this.applyPose(item.handle, doc, instance);
    }
    this.root.updateMatrixWorld(true);
    this.syncClawHeights(doc);
    this.rebuildTubes(doc);
  }

  /** 拖动期显示层预览（plan §4.2.3：不写文档，松手一次性提交）。 */
  applyPreviewWorldPose(id: string, position: THREE.Vector3, quaternion?: THREE.Quaternion): void {
    const item = this.items.get(id);
    if (!item) return;
    item.handle.group.position.copy(position);
    if (quaternion) item.handle.group.quaternion.copy(quaternion);
    item.handle.group.updateMatrixWorld(true);
  }

  /** 拖动期只重建触及该器材的管线（preview=true 走低段数几何）。 */
  refreshTubesFor(doc: LabDocument, equipmentId: string, preview = false): void {
    for (const connection of doc.connections) {
      if (connection.a.equipmentId !== equipmentId && connection.b.equipmentId !== equipmentId) continue;
      const a = this.worldPortAnchor(connection.a.equipmentId, connection.a.portId);
      const b = this.worldPortAnchor(connection.b.equipmentId, connection.b.portId);
      if (!a || !b) continue;
      this.tubes.updateTube(connection.id, a, b, connection.slack ?? 0.5, preview);
    }
  }

  /** 端口世界锚点（spec 端口 + 层级 world transform，唯一口径）。 */
  worldPortAnchor(equipmentId: string, portId: string): TubePortAnchor | null {
    const item = this.items.get(equipmentId);
    const node = item?.handle.portNodes.get(portId);
    if (!item || !node) return null;
    const position = node.getWorldPosition(new THREE.Vector3());
    const quaternion = node.getWorldQuaternion(new THREE.Quaternion());
    return { position, direction: UP.clone().applyQuaternion(quaternion) };
  }

  get syncedItems(): readonly SyncedItem[] {
    return [...this.items.values()];
  }

  item(id: string): SyncedItem | null { return this.items.get(id) ?? null; }

  equipmentGroup(id: string): THREE.Group | null {
    return this.items.get(id)?.handle.group ?? null;
  }

  pickMeshes(): THREE.Mesh[] {
    const meshes: THREE.Mesh[] = [];
    for (const item of this.items.values()) {
      for (const part of item.handle.pickParts) meshes.push(part);
    }
    return meshes;
  }

  portShells(): THREE.Mesh[] {
    const meshes: THREE.Mesh[] = [];
    for (const item of this.items.values()) meshes.push(...item.portShells);
    return meshes;
  }

  controlShells(): THREE.Mesh[] {
    const meshes: THREE.Mesh[] = [];
    for (const item of this.items.values()) meshes.push(...item.controlShells);
    return meshes;
  }

  clawShells(): THREE.Mesh[] {
    const meshes: THREE.Mesh[] = [];
    for (const item of this.items.values()) if (item.clawShell) meshes.push(item.clawShell);
    return meshes;
  }

  /** 夹爪当前视觉高度（拖动挂载/磁吸计算用）。 */
  clawVisualHeight(standId: string): number | null {
    return this.items.get(standId)?.handle.getClawHeight?.() ?? null;
  }

  /** 夹爪预览高度（拖杆时视觉联动；提交后由 syncClawHeights 定稿）。 */
  previewClawHeight(standId: string, height: number): void {
    const item = this.items.get(standId);
    if (!item) return;
    item.handle.setClawHeight?.(height);
    if (item.clawShell) item.clawShell.position.y = Math.min(4.25, Math.max(0.5, height));
  }

  /** 气泡锚点：器材取口沿上方一点；管线取中段。 */
  selectionAnchor(selection: { type: "equipment" | "connection"; id: string }): THREE.Vector3 | null {
    if (selection.type === "connection") {
      const out = new THREE.Vector3();
      return this.tubes.sampleTube(selection.id, 0.5, out) ? out : null;
    }
    const item = this.items.get(selection.id);
    if (!item) return null;
    const bounds = getEquipmentSpec(item.kind)?.bounds;
    const h = bounds?.height ?? 1;
    return item.handle.group.localToWorld(new THREE.Vector3(0, h + 0.3, 0));
  }

  private attachPortShells(handle: EquipmentModelHandle, instanceId: string): THREE.Mesh[] {
    const shells: THREE.Mesh[] = [];
    const material = sharedMaterial(MAT.pickProxy);
    for (const [portId, node] of handle.portNodes) {
      const spec = getEquipmentSpec(handle.kind)?.ports.find(p => p.id === portId);
      if (!spec) continue;
      const shell = new THREE.Mesh(this.hitSphereGeo, material);
      shell.scale.setScalar(THREE.MathUtils.clamp(spec.visualRadius * 3.2, 0.26, 0.46));
      shell.userData.pickRef = { kind: "port", equipmentId: instanceId, portId } satisfies PickRef;
      node.add(shell);
      shells.push(shell);
    }
    return shells;
  }

  private attachControlShells(handle: EquipmentModelHandle, instanceId: string): THREE.Mesh[] {
    const zone = CONTROL_ZONES[handle.kind];
    if (!zone) return [];
    const material = sharedMaterial(MAT.pickProxy);
    const shell = new THREE.Mesh(this.hitSphereGeo, material);
    shell.position.copy(zone.at);
    shell.scale.setScalar(zone.r);
    shell.userData.pickRef = { kind: "control", equipmentId: instanceId, controlId: zone.controlId } satisfies PickRef;
    handle.group.add(shell);
    return [shell];
  }

  private attachClawShell(handle: EquipmentModelHandle, instanceId: string): THREE.Mesh {
    const material = sharedMaterial(MAT.pickProxy);
    const shell = new THREE.Mesh(this.hitSphereGeo, material);
    const grip = standGripLocal(handle.kind);
    shell.position.copy(grip);
    shell.scale.setScalar(0.4);
    shell.userData.pickRef = { kind: "claw", equipmentId: instanceId } satisfies PickRef;
    handle.group.add(shell);
    return shell;
  }

  private applyPose(handle: EquipmentModelHandle, doc: LabDocument, instance: EquipmentInstance): void {
    const world = instanceWorldPose(doc, instance.id);
    if (!world) return;
    handle.group.position.set(world.position.x, world.position.y, world.position.z);
    handle.group.quaternion.set(world.rotation[0], world.rotation[1], world.rotation[2], world.rotation[3]);
  }

  /** 铁架夹具高度 = 挂载器材 clampPoint 的支架局部高度（多挂载件取最低）。 */
  private syncClawHeights(doc: LabDocument): void {
    for (const item of this.items.values()) {
      if (item.kind !== "stand" || !item.handle.setClawHeight) continue;
      let clawY: number | null = null;
      for (const child of doc.equipment) {
        if (child.parentMountId !== item.instanceId || !child.localPose) continue;
        const clamp = getEquipmentSpec(child.kind)?.anchors.clampPoint;
        if (!clamp) continue;
        const y = child.localPose.position.y + clamp.y;
        clawY = clawY === null ? y : Math.min(clawY, y);
      }
      if (clawY !== null) {
        item.handle.setClawHeight(clawY);
        if (item.clawShell) item.clawShell.position.y = Math.min(4.25, Math.max(0.5, clawY));
      }
    }
  }

  private rebuildTubes(doc: LabDocument): void {
    const specs = [];
    for (const connection of doc.connections) {
      const a = this.worldPortAnchor(connection.a.equipmentId, connection.a.portId);
      const b = this.worldPortAnchor(connection.b.equipmentId, connection.b.portId);
      if (!a || !b) continue;
      specs.push({
        id: connection.id,
        a,
        b,
        style: connection.style,
        slack: connection.slack ?? 0.5,
      });
    }
    this.tubes.setConnections(specs);
  }

  private removeItem(id: string): void {
    const item = this.items.get(id);
    if (!item) return;
    this.root.remove(item.handle.group);
    item.handle.dispose();
    this.items.delete(id);
  }

  dispose(): void {
    for (const id of [...this.items.keys()]) this.removeItem(id);
    this.tubes.dispose();
    this.hitSphereGeo.dispose();
    this.root.removeFromParent();
  }
}
