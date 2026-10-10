"use client";

/**
 * 文档 → 场景同步（plan §7.4）：LabDocument 的器材实例映射为 EquipmentFactory
 * 模型，按 domain 递归 world pose 摆放；端口 world anchor 供软管与交互使用；
 * 铁架夹具高度随挂载器材联动。渲染层不回写文档——显示层预览由交互层提交。
 */
import * as THREE from "three";
import {
  getEquipmentSpec, instanceWorldPose, type EquipmentInstance, type LabDocument,
} from "@next-tutor/domain";
import { createEquipmentModel, type EquipmentModelHandle } from "./EquipmentFactory.ts";
import type { QualityTier } from "./SceneController.ts";
import { TubeSystem, type TubePortAnchor } from "./TubeSystem.ts";

const UP = new THREE.Vector3(0, 1, 0);

export interface SyncedItem {
  instanceId: string;
  kind: string;
  handle: EquipmentModelHandle;
}

export class SceneSync {
  readonly root = new THREE.Group();
  readonly tubes = new TubeSystem();
  private readonly items = new Map<string, SyncedItem>();
  private readonly tier: QualityTier;

  constructor(tier: QualityTier) {
    this.tier = tier;
    this.root.name = "equipment";
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
        part.userData.pickRef = { ...part.userData.pickRef, instanceId: instance.id };
      }
      this.root.add(handle.group);
      this.items.set(instance.id, { instanceId: instance.id, kind: instance.kind, handle });
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

  pickMeshes(): THREE.Mesh[] {
    const meshes: THREE.Mesh[] = [];
    for (const item of this.items.values()) {
      for (const part of item.handle.pickParts) meshes.push(part);
    }
    return meshes;
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
      if (clawY !== null) item.handle.setClawHeight(clawY);
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
    this.root.removeFromParent();
  }
}
