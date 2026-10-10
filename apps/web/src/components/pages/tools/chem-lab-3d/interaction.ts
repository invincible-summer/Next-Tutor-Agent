"use client";

/**
 * 交互层（chem-lab architecture）：整个工作台唯一的 PointerEvents + Raycaster 状态机。
 *
 * - 命中优先级（chem-lab architecture，固定）：DOM 气泡 > 仪器端口 > 控件/夹爪 > 软管 >
 *   选中仪器 > 其他仪器 > 台面/空场景相机。
 * - 相机互斥：对象手势在 host 捕获阶段先于 OrbitControls（其监听在 canvas
 *   冒泡阶段）识别，stopPropagation 并临时 controls.enabled=false，松开/取消
 *   后恢复；第二指到达时取消单指对象拖动，交给双指相机。
 * - 几何细则（chem-lab architecture）：NDC 经 getBoundingClientRect 正确换算；ray-plane +
 *   grabOffset 保证抓取点不瞬移；挂载件沿杆（mountAxis）约束；抓铁架时挂载
 *   件随动；磁吸/倾倒阈值以 CSS 像素衡量；pointermove 只更新轻量预览，
 *   松手一次性提交领域动作（每次释放最多一次提交）。
 * - 取消路径（pointercancel/lostpointercapture/Esc/blur/contextlost/unmount）
 *   全部归零：恢复原 pose、回收预览线与磁吸环、恢复 OrbitControls。
 */
import * as THREE from "three";
import {
  connectionBlockReason, equipmentBenchY, equipmentCenterY, getEquipmentSpec, type ControlSpec, type CueTrigger,
  type LabAction, type LabDocument, type PortRef,
} from "@next-tutor/domain";
import type { ChemSceneController } from "./scene/SceneController.ts";
import { standGripLocal, type PickKind, type PickRef, type SceneSync } from "./scene/SceneSync.ts";
import { tubeCurveFor } from "./scene/TubeSystem.ts";

export interface LabSelection { type: "equipment" | "connection"; id: string }

export interface InteractionHooks {
  getDocument: () => LabDocument;
  getSelection: () => LabSelection | null;
  setSelection: (selection: LabSelection | null) => void;
  /** 每次释放最多一次领域提交（chem-lab architecture）。 */
  commit: (action: LabAction) => boolean;
  /** 瞬时 UI 提示（“这个口已占用”等原因，不评价实验对错）。 */
  hint: (key: string) => void;
  /** 拖动态起落（气泡/端口环随拖动隐藏与恢复）。 */
  onDragStateChange?: (dragging: boolean) => void;
  /** 演出触发（Phase D CueRuntime 消费；不进文档与撤销历史）。 */
  onCue?: (trigger: CueTrigger) => void;
}

type Axis = "free" | "rail";

interface Follower {
  id: string;
  basePos: THREE.Vector3;
  baseQuat: THREE.Quaternion;
}

interface PressedObject {
  kind: "pressed-object";
  pointerId: number;
  startX: number;
  startY: number;
  threshold: number;
  instanceId: string;
  /** 按下时的射线命中点与抓取偏移（保持抓取点在原模型位置，不瞬移）。 */
  grabOffset: THREE.Vector3;
  basePos: THREE.Vector3;
  baseQuat: THREE.Quaternion;
  /** 夹爪壳按下：沿杆拖动挂载子件（rail 轴）。 */
  axis: Axis;
  standId?: string;
  baseClawHeight: number | null;
}

interface DragObject {
  kind: "drag-object";
  pointerId: number;
  instanceId: string;
  axis: Axis;
  standId?: string;
  grabOffset: THREE.Vector3;
  planeY: number;
  basePos: THREE.Vector3;
  baseQuat: THREE.Quaternion;
  baseClawHeight: number | null;
  /** 抓铁架时随动的挂载子件（松手一次 move 提交，子件 localPose 不变）。 */
  followers: Follower[];
  /** 磁吸目标（挂到铁架）/倾倒目标（试剂瓶入容器）。 */
  snapStandId: string | null;
  pourTargetId: string | null;
  dropY: number;
}

interface PressedPort {
  kind: "pressed-port";
  pointerId: number;
  startX: number;
  startY: number;
  threshold: number;
  from: PortRef;
  fromPos: THREE.Vector3;
  fromDir: THREE.Vector3;
}

interface RackGestureBase {
  pointerId: number;
  startX: number;
  startY: number;
  threshold: number;
  rackKind: string;
  rackSlot: string;
}

interface PressedRack extends RackGestureBase {
  kind: "pressed-rack";
}

interface DragRack extends RackGestureBase {
  kind: "drag-rack";
}

interface DragTube {
  kind: "drag-tube";
  pointerId: number;
  from: PortRef;
  fromPos: THREE.Vector3;
  fromDir: THREE.Vector3;
  target: PortRef | null;
}

interface OperateControl {
  kind: "operate-control";
  pointerId: number;
  equipmentId: string;
  control: ControlSpec;
  startValue: number | boolean;
  startY: number;
  startIndex: number;
  dialIndex: number;
}

interface PortPending {
  kind: "port-pending";
  from: PortRef;
}

interface TwoFinger {
  kind: "two-finger";
  pointers: Set<number>;
}

type Gesture =
  | { kind: "none" }
  | PressedObject
  | DragObject
  | PressedPort
  | PressedRack
  | DragRack
  | DragTube
  | OperateControl
  | PortPending
  | TwoFinger;

const DRAG_PX_MOUSE = 5;
const DRAG_PX_TOUCH = 9;
const SNAP_PX = 26;
const POUR_PX = 34;
const DETACH_PX = 80;
const RAIL_MIN = 0.5;
const RAIL_MAX = 4.25;
const BENCH_X = 7.6;
const BENCH_Z = 3.6;
const POUR_STEP = 0.28;
const RING_POOL = 12;
const RING_COLOR = 0x2fa08f;

const UP = new THREE.Vector3(0, 1, 0);

/** 挂载局部姿态规则（与 stages.ts 一致）：夹持点落在 grip 局部位置。 */
function mountLocalPose(clamp: { x: number; y: number; z: number }, grip: THREE.Vector3, h: number): THREE.Vector3 {
  return new THREE.Vector3(grip.x, h - clamp.y, grip.z - clamp.z);
}

function pickRefKey(ref: PickRef): string {
  return [
    ref.kind,
    ref.instanceId ?? ref.equipmentId ?? ref.connectionId ?? ref.rackSlot ?? "",
    ref.portId ?? ref.controlId ?? ref.rackKind ?? "",
  ].join(":");
}

export class InteractionController {
  private readonly controller: ChemSceneController;
  private readonly sync: SceneSync;
  private readonly hooks: InteractionHooks;
  private readonly host: HTMLElement;
  private readonly canvas: HTMLCanvasElement;
  private readonly raycaster = new THREE.Raycaster();
  private readonly ndc = new THREE.Vector2();
  private readonly tmpV = new THREE.Vector3();
  private readonly tmpV2 = new THREE.Vector3();
  private readonly benchPlane = new THREE.Plane(UP, 0);
  private readonly dragPlane = new THREE.Plane();
  private readonly tmpQuat = new THREE.Quaternion();
  /** 指针屏幕点/投影点各自独立暂存，允许同帧并存比较。 */
  private readonly pointerPt = { x: 0, y: 0 };
  private readonly projectPt = { x: 0, y: 0 };

  private gesture: Gesture = { kind: "none" };
  private disposed = false;

  // 复用视觉资源：磁吸/悬停环池、预览软管线、选中轮廓（零每帧 new）。
  private readonly rings: THREE.Mesh[] = [];
  private readonly ringMat = new THREE.MeshBasicMaterial({ color: RING_COLOR, transparent: true, opacity: 0.95, depthTest: false });
  private readonly previewMat = new THREE.LineBasicMaterial({ color: RING_COLOR, transparent: true, opacity: 0.9, depthTest: false });
  private readonly previewLine: THREE.Line;
  private readonly selectionBox: THREE.LineSegments;
  private readonly selectionMat = new THREE.LineBasicMaterial({ color: RING_COLOR, transparent: true, opacity: 0.85, depthTest: false });

  private hoverScheduled = false;
  private lastHoverEvent: PointerEvent | null = null;
  private lastHoverRef: string | null = null;

  constructor(controller: ChemSceneController, sync: SceneSync, hooks: InteractionHooks) {
    this.controller = controller;
    this.sync = sync;
    this.hooks = hooks;
    this.host = controller.renderer.domElement.parentElement ?? controller.renderer.domElement;
    this.canvas = controller.renderer.domElement;

    const ringGeo = new THREE.TorusGeometry(1, 0.06, 8, 28);
    for (let i = 0; i < RING_POOL; i++) {
      const ring = new THREE.Mesh(ringGeo, this.ringMat);
      ring.renderOrder = 10;
      ring.visible = false;
      ring.scale.setScalar(0.3);
      controller.root.add(ring);
      this.rings.push(ring);
    }
    const lineGeo = new THREE.BufferGeometry();
    lineGeo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(3 * 33), 3));
    this.previewLine = new THREE.Line(lineGeo, this.previewMat);
    this.previewLine.renderOrder = 10;
    this.previewLine.visible = false;
    this.previewLine.frustumCulled = false;
    controller.root.add(this.previewLine);

    this.selectionBox = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1)), this.selectionMat);
    this.selectionBox.renderOrder = 10;
    this.selectionBox.visible = false;
    controller.root.add(this.selectionBox);

    this.host.addEventListener("pointerdown", this.onPointerDown, { capture: true });
    this.host.addEventListener("pointermove", this.onHoverProbe, { capture: true, passive: true });
    this.host.addEventListener("dblclick", this.onDoubleClick, { capture: true });
    window.addEventListener("pointermove", this.onPointerMove, { capture: true });
    window.addEventListener("pointerup", this.onPointerUp, { capture: true });
    window.addEventListener("pointercancel", this.onPointerCancel, { capture: true });
    window.addEventListener("blur", this.onCancelAll, { capture: true });
    window.addEventListener("keydown", this.onKeyDown, { capture: true });
  }

  // ── 公共入口 ─────────────────────────────────────────────────────────────

  /** 选中可视化（组合根在选择变化/提交后调用；拖动期间隐藏）。 */
  updateSelectionVisual(): void {
    const selection = this.hooks.getSelection();
    const item = selection?.type === "equipment" ? this.sync.item(selection.id) : null;
    if (!selection || !item) {
      this.selectionBox.visible = false;
      return;
    }
    const group = item.handle.group;
    const spec = getEquipmentSpec(item.kind);
    const { width, depth, height } = spec?.bounds ?? { width: 1, depth: 1, height: 1 };
    this.tmpV.set(0, spec ? equipmentCenterY(spec) : height / 2, 0)
      .applyQuaternion(group.quaternion).add(group.position);
    this.selectionBox.position.copy(this.tmpV);
    this.selectionBox.quaternion.copy(group.quaternion);
    this.selectionBox.scale.set(width + 0.14, height + 0.14, depth + 0.14);
    this.selectionBox.visible = true;
    this.controller.invalidate();
  }

  /** 一切手势与等待态归零（Esc/blur/contextlost/unmount/切关）。 */
  cancelAll(): void {
    if (this.gesture.kind === "drag-object") this.restoreFromDrag(this.gesture);
    if (this.gesture.kind === "drag-rack") this.sync.endRackPreview();
    this.resetGesture();
  }

  dispose(): void {
    this.disposed = true;
    this.cancelAll();
    this.host.removeEventListener("pointerdown", this.onPointerDown, { capture: true } as EventListenerOptions);
    this.host.removeEventListener("pointermove", this.onHoverProbe, { capture: true } as EventListenerOptions);
    this.host.removeEventListener("dblclick", this.onDoubleClick, { capture: true } as EventListenerOptions);
    window.removeEventListener("pointermove", this.onPointerMove, { capture: true } as EventListenerOptions);
    window.removeEventListener("pointerup", this.onPointerUp, { capture: true } as EventListenerOptions);
    window.removeEventListener("pointercancel", this.onPointerCancel, { capture: true } as EventListenerOptions);
    window.removeEventListener("blur", this.onCancelAll, { capture: true } as EventListenerOptions);
    window.removeEventListener("keydown", this.onKeyDown, { capture: true } as EventListenerOptions);
    for (const ring of this.rings) ring.removeFromParent();
    if (this.rings[0]) this.rings[0].geometry.dispose();
    this.ringMat.dispose();
    this.previewLine.removeFromParent();
    this.previewLine.geometry.dispose();
    this.previewMat.dispose();
    this.selectionBox.removeFromParent();
    this.selectionBox.geometry.dispose();
    this.selectionMat.dispose();
  }

  // ── 事件处理 ─────────────────────────────────────────────────────────────

  private onDoubleClick = (event: MouseEvent): void => {
    if (this.disposed || this.gesture.kind !== "none") return;
    const hit = this.pickAt(event as PointerEvent, ["equipment"]);
    if (!hit?.instanceId) return;
    const group = this.sync.equipmentGroup(hit.instanceId);
    if (!group) return;
    const spec = getEquipmentSpec(hit.instanceId ? this.sync.item(hit.instanceId)?.kind ?? "" : "");
    const center = group.localToWorld(new THREE.Vector3(0, spec ? equipmentCenterY(spec) : 0.5, 0));
    this.controller.focusOn(center, Math.max(5.5, Math.max(spec?.bounds.width ?? 1, spec?.bounds.height ?? 1) * 3.2));
    event.stopPropagation();
  };

  private onPointerDown = (event: PointerEvent): void => {
    if (this.disposed || event.button !== 0) return;
    // 点接第二击（chem-lab architecture）：pending 口存在时优先消费端口命中。
    if (this.gesture.kind === "port-pending") {
      const hit = this.pickAt(event, ["port"]);
      if (hit?.kind === "port" && hit.equipmentId && hit.portId) {
        event.stopPropagation();
        this.tryConnect(this.gesture.from, { equipmentId: hit.equipmentId, portId: hit.portId });
        this.resetGesture();
        return;
      }
      this.resetGesture(); // 空白/其他命中：取消等待，继续正常处理
    }
    if (this.gesture.kind === "two-finger") {
      this.gesture.pointers.add(event.pointerId);
      event.stopPropagation();
      return;
    }
    if (this.gesture.kind !== "none") {
      // 已有单指对象手势时第二指到达：取消对象拖动，交给双指相机。
      if (this.gesture.kind === "drag-object") this.restoreFromDrag(this.gesture);
      if (this.gesture.kind === "drag-rack") this.sync.endRackPreview();
      this.gesture = { kind: "two-finger", pointers: new Set([this.gesturePointer(), event.pointerId]) };
      this.controller.controls.enabled = true;
      this.hooks.onDragStateChange?.(false);
      this.controller.endActivity("drag");
      return;
    }

    const hit = this.pickAt(event, ["port", "control", "claw", "tube", "rack", "equipment"]);
    if (!hit) return; // 空台面/空场景：放行给 OrbitControls

    event.stopPropagation();
    try { this.host.setPointerCapture(event.pointerId); } catch { /* 捕获失败不阻断手势 */ }
    this.controller.controls.enabled = false;
    this.lastHoverRef = null;
    this.canvas.style.cursor = "grabbing";

    const threshold = event.pointerType === "touch" ? DRAG_PX_TOUCH : DRAG_PX_MOUSE;
    const doc = this.hooks.getDocument();
    if (hit.kind === "port" && hit.equipmentId && hit.portId) {
      const anchor = this.sync.worldPortAnchor(hit.equipmentId, hit.portId);
      if (!anchor) { this.finishGesture(); return; }
      this.gesture = {
        kind: "pressed-port",
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        threshold,
        from: { equipmentId: hit.equipmentId, portId: hit.portId },
        fromPos: anchor.position.clone(),
        fromDir: anchor.direction.clone(),
      };
      return;
    }
    if (hit.kind === "control" && hit.equipmentId && hit.controlId) {
      const eq = doc.equipment.find(e => e.id === hit.equipmentId);
      const control = getEquipmentSpec(eq?.kind ?? "")?.controls.find(c => c.id === hit.controlId);
      if (!eq || !control) { this.finishGesture(); return; }
      const value = eq.controls[control.id];
      const steps = control.kind === "dial" ? control.steps ?? [1] : null;
      const startIndex = steps ? Math.max(0, steps.findIndex(s => s === value)) : -1;
      this.hooks.setSelection({ type: "equipment", id: eq.id });
      this.gesture = {
        kind: "operate-control",
        pointerId: event.pointerId,
        equipmentId: eq.id,
        control,
        startValue: value,
        startY: event.clientY,
        startIndex,
        dialIndex: startIndex,
      };
      return;
    }
    if (hit.kind === "claw" && hit.equipmentId) {
      const child = this.lowestMountedChild(doc, hit.equipmentId);
      const group = child ? this.sync.equipmentGroup(child.id) : null;
      if (child && group) {
        this.gesture = {
          kind: "pressed-object",
          pointerId: event.pointerId,
          startX: event.clientX,
          startY: event.clientY,
          threshold,
          instanceId: child.id,
          grabOffset: new THREE.Vector3(),
          basePos: group.position.clone(),
          baseQuat: group.quaternion.clone(),
          axis: "rail",
          standId: hit.equipmentId,
          baseClawHeight: this.sync.clawVisualHeight(hit.equipmentId),
        };
        return;
      }
      this.finishGesture();
      return;
    }
    if (hit.kind === "tube" && hit.connectionId) {
      // 点软管中段：只选中该管（气泡提供“拆开”），不误触背后玻璃瓶/相机。
      this.controller.controls.enabled = true;
      this.releaseCaptureSafe(event.pointerId);
      this.hooks.setSelection({ type: "connection", id: hit.connectionId });
      this.updateSelectionVisual();
      return;
    }
    if (hit.kind === "rack" && hit.rackKind && hit.rackSlot) {
      this.gesture = {
        kind: "pressed-rack",
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        threshold,
        rackKind: hit.rackKind,
        rackSlot: hit.rackSlot,
      };
      return;
    }
    if (hit.kind === "equipment" && hit.instanceId) {
      const group = this.sync.equipmentGroup(hit.instanceId);
      if (!group) { this.finishGesture(); return; }
      const hitWorld = this.rayHitBench(event);
      const grabOffset = hitWorld ? hitWorld.clone().sub(group.position) : new THREE.Vector3();
      this.gesture = {
        kind: "pressed-object",
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        threshold,
        instanceId: hit.instanceId,
        grabOffset,
        basePos: group.position.clone(),
        baseQuat: group.quaternion.clone(),
        axis: "free",
        baseClawHeight: null,
      };
      return;
    }
    // A stale/unknown pick must never leave OrbitControls disabled or a pointer
    // capture held by the canvas.
    this.finishGesture();
  };

  private onPointerMove = (event: PointerEvent): void => {
    if (this.disposed) return;
    const gesture = this.gesture;
    if (gesture.kind === "none" || gesture.kind === "port-pending" || gesture.kind === "two-finger") return;
    if ("pointerId" in gesture && gesture.pointerId !== event.pointerId) return;

    if (gesture.kind === "pressed-object"
      && Math.hypot(event.clientX - gesture.startX, event.clientY - gesture.startY) >= gesture.threshold) {
      this.promoteToDrag(gesture);
    }
    if (gesture.kind === "pressed-rack"
      && Math.hypot(event.clientX - gesture.startX, event.clientY - gesture.startY) >= gesture.threshold) {
      this.promoteRackToDrag(gesture);
    }
    if (this.gesture.kind === "drag-object") this.moveDrag(this.gesture, event);
    else if (this.gesture.kind === "pressed-port") this.promoteToTubeDrag(this.gesture);
    if (this.gesture.kind === "drag-rack") this.moveRack(this.gesture, event);
    if (this.gesture.kind === "drag-tube") this.moveTubeDrag(this.gesture, event);
    if (this.gesture.kind === "operate-control") this.moveOperateControl(this.gesture, event);
  };

  private onPointerUp = (event: PointerEvent): void => {
    if (this.disposed) return;
    const gesture = this.gesture;
    if (gesture.kind === "two-finger") {
      gesture.pointers.delete(event.pointerId);
      if (gesture.pointers.size === 0) this.gesture = { kind: "none" };
      return;
    }
    if (gesture.kind === "none" || gesture.kind === "port-pending") return;
    if ("pointerId" in gesture && gesture.pointerId !== event.pointerId) return;

    switch (gesture.kind) {
      case "pressed-object":
        // 短按 = 选中（<100ms 轮廓出现；不动相机、不提交移动）。
        this.hooks.setSelection({ type: "equipment", id: gesture.instanceId });
        this.updateSelectionVisual();
        this.finishGesture();
        break;
      case "pressed-rack": {
        this.placeRack(gesture, event);
        this.finishGesture();
        break;
      }
      case "drag-rack":
        this.placeRack(gesture, event);
        this.finishGesture();
        break;
      case "drag-object":
        this.releaseDrag(gesture);
        this.finishGesture();
        break;
      case "pressed-port":
        // 未拖动：进入“点接”等待（点第二个口连接；Esc/空白取消）。
        this.gesture = { kind: "port-pending", from: gesture.from };
        this.showPortPendingRings(gesture.from);
        this.controller.controls.enabled = true;
        this.releaseCaptureSafe(event.pointerId);
        this.canvas.style.cursor = "";
        this.hooks.onDragStateChange?.(false);
        this.hooks.setSelection(null);
        this.updateSelectionVisual();
        return;
      case "drag-tube": {
        if (gesture.target) {
          this.tryConnect(gesture.from, gesture.target);
        } else {
          const hit = this.pickAt(event, ["port"]);
          if (hit?.kind === "port") this.hooks.hint("chem3d.hint.portNotConnectable");
        }
        this.finishGesture();
        break;
      }
      case "operate-control":
        this.commitOperateControl(gesture);
        this.finishGesture();
        break;
    }
  };

  private onPointerCancel = (event: PointerEvent): void => {
    const gesture = this.gesture;
    if (gesture.kind === "two-finger") {
      gesture.pointers.delete(event.pointerId);
      if (gesture.pointers.size === 0) this.gesture = { kind: "none" };
      return;
    }
    if (gesture.kind === "none" || gesture.kind === "port-pending") return;
    if ("pointerId" in gesture && gesture.pointerId !== event.pointerId) return;
    if (gesture.kind === "drag-object") this.restoreFromDrag(gesture);
    if (gesture.kind === "drag-rack") this.sync.endRackPreview();
    this.finishGesture();
  };

  private onCancelAll = (event?: Event): void => {
    // 只响应真正的窗口失焦（切窗/最小化）；DOM 元素失焦（如 HUD 按钮
    // 被画布点击夺走焦点）会沿捕获链传到这里，不构成取消条件。
    if (event && event.target !== window) return;
    this.cancelAll();
  };

  private onKeyDown = (event: KeyboardEvent): void => {
    if (event.key !== "Escape" || this.gesture.kind === "none") return;
    if (this.gesture.kind === "drag-object") this.restoreFromDrag(this.gesture);
    if (this.gesture.kind === "drag-rack") this.sync.endRackPreview();
    this.resetGesture();
    event.stopPropagation();
  };

  // ── 悬停（<80ms 反馈；rAF 合并） ──────────────────────────────────────────

  private onHoverProbe = (event: PointerEvent): void => {
    if (this.disposed || this.gesture.kind !== "none" || event.buttons !== 0) return;
    this.lastHoverEvent = event;
    if (this.hoverScheduled) return;
    this.hoverScheduled = true;
    requestAnimationFrame(() => {
      this.hoverScheduled = false;
      if (this.disposed || this.gesture.kind !== "none" || !this.lastHoverEvent) return;
      this.runHover(this.lastHoverEvent);
    });
  };

  private runHover(event: PointerEvent): void {
    const hit = this.pickAt(event, ["port", "control", "claw", "tube", "rack", "equipment"]);
    const ref = hit ? pickRefKey(hit) : null;
    if (ref === this.lastHoverRef) return;
    this.lastHoverRef = ref;
    this.canvas.style.cursor = hit?.kind === "port" || hit?.kind === "control" || hit?.kind === "claw"
      ? "pointer"
      : hit?.kind === "tube" || hit?.kind === "equipment" || hit?.kind === "rack" ? "grab" : "";
    if (hit?.kind === "port" && hit.equipmentId && hit.portId) {
      const anchor = this.sync.worldPortAnchor(hit.equipmentId, hit.portId);
      if (anchor) this.showRings([{ position: anchor.position, radius: 0.2 }]);
    } else {
      this.showRings([]);
    }
    this.controller.invalidate();
  }

  // ── 器材拖动（ray-plane + offset；挂载沿杆约束；铁架整体随动） ───────────

  private promoteToDrag(gesture: PressedObject): void {
    const doc = this.hooks.getDocument();
    const eq = doc.equipment.find(e => e.id === gesture.instanceId);
    const followers: Follower[] = [];
    if (eq && getEquipmentSpec(eq.kind)?.family === "stand") {
      for (const child of doc.equipment) {
        if (child.parentMountId !== eq.id) continue;
        const group = this.sync.equipmentGroup(child.id);
        if (group) followers.push({ id: child.id, basePos: group.position.clone(), baseQuat: group.quaternion.clone() });
      }
    }
    this.gesture = {
      kind: "drag-object",
      pointerId: gesture.pointerId,
      instanceId: gesture.instanceId,
      axis: gesture.axis,
      standId: gesture.standId,
      grabOffset: gesture.grabOffset,
      planeY: gesture.axis === "rail" ? 0 : gesture.basePos.y,
      basePos: gesture.basePos,
      baseQuat: gesture.baseQuat,
      baseClawHeight: gesture.baseClawHeight,
      followers,
      snapStandId: null,
      pourTargetId: null,
      dropY: gesture.basePos.y,
    };
    this.controller.beginActivity("drag");
    this.hooks.onDragStateChange?.(true);
    this.hooks.setSelection(null);
    this.selectionBox.visible = false;
    this.showRings([]);
    this.previewLine.visible = false;
  }

  private promoteRackToDrag(gesture: PressedRack): void {
    if (!this.sync.beginRackPreview(gesture.rackSlot)) {
      this.finishGesture();
      return;
    }
    this.gesture = { ...gesture, kind: "drag-rack" };
    this.controller.beginActivity("drag");
    this.hooks.onDragStateChange?.(true);
    this.hooks.setSelection(null);
    this.selectionBox.visible = false;
    this.showRings([]);
  }

  private moveRack(gesture: DragRack, event: PointerEvent): void {
    const hit = this.rayHitBench(event);
    if (!hit) return;
    hit.x = THREE.MathUtils.clamp(hit.x, -BENCH_X, BENCH_X);
    hit.z = THREE.MathUtils.clamp(hit.z, -BENCH_Z, BENCH_Z);
    this.sync.updateRackPreview(gesture.rackSlot, hit);
    this.showRings([{ position: hit, radius: 0.42 }]);
    this.controller.invalidate();
  }

  private placeRack(gesture: PressedRack | DragRack, event: PointerEvent): void {
    const hit = this.rayHitBench(event);
    const position = hit ?? new THREE.Vector3(0, 0, 0);
    position.x = THREE.MathUtils.clamp(position.x, -BENCH_X, BENCH_X);
    position.z = THREE.MathUtils.clamp(position.z, -BENCH_Z, BENCH_Z);
    this.sync.endRackPreview();
    const doc = this.hooks.getDocument();
    const stem = `rack-${gesture.rackKind}`;
    let id = `${stem}-${Date.now().toString(36)}`;
    let suffix = 1;
    while (doc.equipment.some(eq => eq.id === id)) id = `${stem}-${Date.now().toString(36)}-${suffix++}`;
    const accepted = this.hooks.commit({
      type: "add",
      id,
      kind: gesture.rackKind,
      pose: { position: { x: position.x, y: this.benchYFor(gesture.rackKind), z: position.z }, rotation: [0, 0, 0, 1] },
    });
    if (accepted) this.hooks.setSelection({ type: "equipment", id });
  }

  /** 落台目标高度：底面原点器材贴台面，中心原点器材（卧式冷凝器等）抬半高。 */
  private benchYFor(kind: string): number {
    const spec = getEquipmentSpec(kind);
    return spec ? equipmentBenchY(spec) : 0;
  }

  private moveDrag(gesture: DragObject, event: PointerEvent): void {
    const doc = this.hooks.getDocument();
    const eq = doc.equipment.find(e => e.id === gesture.instanceId);
    if (!eq) return;

    if (gesture.axis === "rail" && gesture.standId) {
      // 沿立杆上下：射线与过杆、面向相机的竖直平面相交（不压回台面 Y=0）。
      const standGroup = this.sync.equipmentGroup(gesture.standId);
      if (!standGroup) return;
      this.tmpV.copy(this.controller.camera.position).sub(standGroup.position).setY(0);
      if (this.tmpV.lengthSq() < 0.0001) this.tmpV.set(0, 0, 1);
      this.tmpV.normalize();
      this.dragPlane.setFromNormalAndCoplanarPoint(this.tmpV, standGroup.position);
      const hit = this.rayHitPlane(event, this.dragPlane);
      if (!hit) return;
      this.previewRailHeight(gesture, THREE.MathUtils.clamp(hit.y, RAIL_MIN, RAIL_MAX));
      return;
    }

    // 自由面拖动：保持抓取偏移；Y 平滑贴向目标高度（离架后落到台面）。
    this.dragPlane.copy(this.benchPlane).constant = -gesture.planeY;
    const hit = this.rayHitPlane(event, this.dragPlane);
    if (!hit) return;
    this.tmpV.copy(hit).sub(gesture.grabOffset);
    this.tmpV.x = THREE.MathUtils.clamp(this.tmpV.x, -BENCH_X, BENCH_X);
    this.tmpV.z = THREE.MathUtils.clamp(this.tmpV.z, -BENCH_Z, BENCH_Z);
    const benchY = this.benchYFor(eq.kind);
    const targetY = eq.parentMountId ? this.detachDropY(gesture, event, benchY) : benchY;
    // Once the pointer clears the clamp, make the detach intent explicit so a
    // quick release cannot leave a half-detached object in an invalid pose.
    const detachIntended = eq.parentMountId && targetY <= benchY + 0.001;
    gesture.dropY = detachIntended
      ? benchY
      : gesture.dropY + (targetY - gesture.dropY) * 0.25;
    this.tmpV.y = gesture.dropY;

    // 磁吸（不强迫）：可夹器材靠近任一铁架夹持点 → 半透明贴合预览。
    gesture.snapStandId = null;
    gesture.pourTargetId = null;
    if (!eq.parentMountId && getEquipmentSpec(eq.kind)?.clampable) {
      gesture.snapStandId = this.findSnapStand(event, eq.id);
    }
    if (gesture.snapStandId) {
      this.previewSnapToStand(gesture, gesture.snapStandId);
    } else {
      this.sync.applyPreviewWorldPose(gesture.instanceId, this.tmpV, gesture.baseQuat);
      // 抓铁架时挂载子件整体随动（子件 localPose 不变，一次 move 提交）。
      this.tmpV2.copy(this.tmpV).sub(gesture.basePos);
      for (const follower of gesture.followers) {
        this.tmpV.copy(follower.basePos).add(this.tmpV2);
        this.sync.applyPreviewWorldPose(follower.id, this.tmpV, follower.baseQuat);
      }
      if (eq.kind === "reagent-bottle") gesture.pourTargetId = this.findPourTarget(event, eq.id);
    }
    this.updateDragRings(gesture);
    this.sync.refreshTubesFor(doc, gesture.instanceId, true);
    for (const follower of gesture.followers) this.sync.refreshTubesFor(doc, follower.id, true);
    this.controller.invalidate();
  }

  /** 挂架件横向拖离支架：超过屏幕阈值后目标高度落到台面（按规格落台高度）。 */
  private detachDropY(gesture: DragObject, event: PointerEvent, benchY: number): number {
    if (!gesture.standId) return benchY;
    const standGroup = this.sync.equipmentGroup(gesture.standId);
    if (!standGroup) return benchY;
    const h = this.sync.clawVisualHeight(gesture.standId) ?? 2.6;
    this.tmpV.copy(standGripLocal()).setY(h).applyQuaternion(standGroup.quaternion).add(standGroup.position);
    const grip = this.projectToScreen(this.tmpV);
    const pointer = this.pointerScreen(event);
    if (!grip || !pointer) return gesture.basePos.y;
    return Math.hypot(grip.x - pointer.x, grip.y - pointer.y) > DETACH_PX ? benchY : gesture.basePos.y;
  }

  private previewRailHeight(gesture: DragObject, h: number): void {
    if (!gesture.standId) return;
    const doc = this.hooks.getDocument();
    const standGroup = this.sync.equipmentGroup(gesture.standId);
    const child = doc.equipment.find(e => e.id === gesture.instanceId);
    const clamp = getEquipmentSpec(child?.kind ?? "")?.anchors.clampPoint;
    if (!standGroup || !child || !clamp) return;
    const localPos = mountLocalPose(clamp, standGripLocal(), h);
    this.tmpV.copy(localPos).applyQuaternion(standGroup.quaternion).add(standGroup.position);
    this.sync.applyPreviewWorldPose(child.id, this.tmpV, gesture.baseQuat);
    this.sync.previewClawHeight(gesture.standId, h);
    this.sync.refreshTubesFor(doc, child.id, true);
    this.controller.invalidate();
  }

  private previewSnapToStand(gesture: DragObject, standId: string): void {
    const doc = this.hooks.getDocument();
    const eq = doc.equipment.find(e => e.id === gesture.instanceId);
    const standGroup = this.sync.equipmentGroup(standId);
    const clamp = getEquipmentSpec(eq?.kind ?? "")?.anchors.clampPoint;
    if (!eq || !standGroup || !clamp) return;
    const h = this.sync.clawVisualHeight(standId) ?? 2.6;
    const localPos = mountLocalPose(clamp, standGripLocal(), h);
    this.tmpV.copy(localPos).applyQuaternion(standGroup.quaternion).add(standGroup.position);
    this.sync.applyPreviewWorldPose(gesture.instanceId, this.tmpV, this.tmpQuat.identity());
    this.sync.refreshTubesFor(doc, gesture.instanceId, true);
  }

  private updateDragRings(gesture: DragObject): void {
    const marks: { position: THREE.Vector3; radius: number }[] = [];
    if (gesture.snapStandId && this.standGripWorld(gesture.snapStandId, this.tmpV)) {
      marks.push({ position: this.tmpV.clone(), radius: 0.3 });
    }
    if (gesture.pourTargetId && this.rimWorld(gesture.pourTargetId, this.tmpV2)) {
      marks.push({ position: this.tmpV2.clone(), radius: 0.26 });
    }
    this.showRings(marks);
  }

  private releaseDrag(gesture: DragObject): void {
    const doc = this.hooks.getDocument();
    const eq = doc.equipment.find(e => e.id === gesture.instanceId);
    if (!eq) { this.restoreFromDrag(gesture); return; }

    // 试剂瓶倾倒：目标是容器时只提交演示液（瓶原件回位，架上样品不消耗）。
    if (gesture.pourTargetId) {
      const target = doc.equipment.find(e => e.id === gesture.pourTargetId);
      const bottle = getEquipmentSpec(eq.kind)?.defaultContents;
      if (target && bottle) {
        this.sync.applyPreviewWorldPose(eq.id, gesture.basePos, gesture.baseQuat);
        this.sync.refreshTubesFor(doc, eq.id, false);
        const fill = Math.min(1, (target.visualContents?.fill ?? 0) + POUR_STEP);
        this.hooks.commit({ type: "setVisualContents", id: target.id, contents: { fill, colorId: bottle.colorId } });
        this.hooks.onCue?.({ kind: "liquid-poured", fromEquipmentId: eq.id, toEquipmentId: target.id, colorId: bottle.colorId });
        return;
      }
    }

    if (gesture.axis === "rail" && gesture.standId) {
      // 沿杆拖动提交：新的局部挂载姿态（move 对挂载件存 localPose）。
      const standGroup = this.sync.equipmentGroup(gesture.standId);
      const clamp = getEquipmentSpec(eq.kind)?.anchors.clampPoint;
      const current = this.sync.equipmentGroup(eq.id);
      if (standGroup && clamp && current) {
        const h = this.sync.clawVisualHeight(gesture.standId) ?? gesture.baseClawHeight ?? 2.6;
        const localPos = mountLocalPose(clamp, standGripLocal(), h);
        const localQuat = current.quaternion.clone().premultiply(standGroup.quaternion.clone().invert()).normalize();
        this.hooks.commit({
          type: "move",
          id: eq.id,
          pose: { position: { x: localPos.x, y: localPos.y, z: localPos.z }, rotation: [localQuat.x, localQuat.y, localQuat.z, localQuat.w] },
        });
        return;
      }
      this.restoreFromDrag(gesture);
      return;
    }

    if (gesture.snapStandId) {
      const standGroup = this.sync.equipmentGroup(gesture.snapStandId);
      const clamp = getEquipmentSpec(eq.kind)?.anchors.clampPoint;
      if (standGroup && clamp && !eq.parentMountId) {
        const h = this.sync.clawVisualHeight(gesture.snapStandId) ?? 2.6;
        const localPos = mountLocalPose(clamp, standGripLocal(), h);
        this.hooks.commit({
          type: "attach",
          id: eq.id,
          parentMountId: gesture.snapStandId,
          localPose: { position: { x: localPos.x, y: localPos.y, z: localPos.z }, rotation: [0, 0, 0, 1] },
        });
        return;
      }
    }

    if (eq.parentMountId) {
      // 仍在夹具附近：把预览还原到挂载姿态。只有明确拖离夹具才脱挂。
      if (gesture.dropY > this.benchYFor(eq.kind) + 0.05) {
        this.restoreFromDrag(gesture);
        return;
      }
      // 已挂架件被拖离支架：脱挂落台（保留朝向）。
      const group = this.sync.equipmentGroup(eq.id);
      if (group) {
        this.hooks.commit({
          type: "detach",
          id: eq.id,
          pose: {
            position: { x: group.position.x, y: this.benchYFor(eq.kind), z: group.position.z },
            rotation: [gesture.baseQuat.x, gesture.baseQuat.y, gesture.baseQuat.z, gesture.baseQuat.w],
          },
        });
        return;
      }
    }

    const group = this.sync.equipmentGroup(eq.id);
    if (!group) { this.restoreFromDrag(gesture); return; }
    this.hooks.commit({
      type: "move",
      id: eq.id,
      pose: {
        position: { x: group.position.x, y: group.position.y, z: group.position.z },
        rotation: [gesture.baseQuat.x, gesture.baseQuat.y, gesture.baseQuat.z, gesture.baseQuat.w],
      },
    });
  }

  /** 取消路径：显示层恢复文档姿态（原文档未变，无需提交）。 */
  private restoreFromDrag(gesture: DragObject): void {
    this.sync.applyPreviewWorldPose(gesture.instanceId, gesture.basePos, gesture.baseQuat);
    for (const follower of gesture.followers) {
      this.sync.applyPreviewWorldPose(follower.id, follower.basePos, follower.baseQuat);
    }
    if (gesture.standId && gesture.baseClawHeight !== null) this.sync.previewClawHeight(gesture.standId, gesture.baseClawHeight);
    const doc = this.hooks.getDocument();
    this.sync.refreshTubesFor(doc, gesture.instanceId, false);
    for (const follower of gesture.followers) this.sync.refreshTubesFor(doc, follower.id, false);
    this.controller.invalidate();
  }

  // ── 端口 → 软管拖接（磁吸 20–28 CSS px） ────────────────────────────────

  private promoteToTubeDrag(gesture: PressedPort): void {
    this.gesture = {
      kind: "drag-tube",
      pointerId: gesture.pointerId,
      from: gesture.from,
      fromPos: gesture.fromPos,
      fromDir: gesture.fromDir,
      target: null,
    };
    this.controller.beginActivity("drag");
    this.hooks.onDragStateChange?.(true);
    this.hooks.setSelection(null);
    this.selectionBox.visible = false;
    this.previewLine.visible = true;
  }

  private moveTubeDrag(gesture: DragTube, event: PointerEvent): void {
    const doc = this.hooks.getDocument();
    gesture.target = this.findSnapPort(event, doc, gesture.from);
    const end = this.tmpV2;
    const targetAnchor = gesture.target
      ? this.sync.worldPortAnchor(gesture.target.equipmentId, gesture.target.portId)
      : null;
    if (targetAnchor) {
      end.copy(targetAnchor.position);
    } else {
      const hit = this.rayHitBench(event);
      if (!hit) return;
      end.copy(hit);
    }
    const curve = tubeCurveFor(
      { position: gesture.fromPos, direction: gesture.fromDir },
      { position: end.clone(), direction: UP },
      0.5,
    );
    const attr = this.previewLine.geometry.getAttribute("position") as THREE.BufferAttribute;
    for (let i = 0; i < 33; i++) {
      const p = curve.getPoint(i / 32, this.tmpV);
      attr.setXYZ(i, p.x, p.y, p.z);
    }
    attr.needsUpdate = true;
    this.previewLine.geometry.computeBoundingSphere();

    if (targetAnchor) this.showRings([{ position: targetAnchor.position, radius: 0.24 }]);
    else this.showRings([]);
    this.controller.invalidate();
  }

  /** 屏幕距离最近的兼容端口（磁吸；只做类型/占用检查，不判实验对错）。 */
  private findSnapPort(event: PointerEvent, doc: LabDocument, from: PortRef): PortRef | null {
    const pointer = this.pointerScreen(event);
    if (!pointer) return null;
    let best: PortRef | null = null;
    let bestDist = SNAP_PX;
    for (const shell of this.sync.portShells()) {
      const ref = shell.userData.pickRef as PickRef | undefined;
      if (!ref || ref.kind !== "port" || !ref.equipmentId || !ref.portId) continue;
      const candidate: PortRef = { equipmentId: ref.equipmentId, portId: ref.portId };
      if (connectionBlockReason(doc, from, candidate) !== null) continue;
      const anchor = this.sync.worldPortAnchor(ref.equipmentId, ref.portId);
      if (!anchor) continue;
      const screen = this.projectToScreen(anchor.position);
      if (!screen) continue;
      const dist = Math.hypot(screen.x - pointer.x, screen.y - pointer.y);
      if (dist < bestDist) { bestDist = dist; best = candidate; }
    }
    return best;
  }

  private tryConnect(from: PortRef, to: PortRef): void {
    const doc = this.hooks.getDocument();
    const reason = connectionBlockReason(doc, from, to);
    if (reason) {
      this.hooks.hint(
        reason === "port_a_busy" || reason === "port_b_busy" ? "chem3d.hint.portBusy" : "chem3d.hint.portNotConnectable",
      );
      return;
    }
    this.hooks.commit({ type: "connect", a: from, b: to });
  }

  private showPortPendingRings(from: PortRef): void {
    const doc = this.hooks.getDocument();
    const marks: { position: THREE.Vector3; radius: number }[] = [];
    const fromAnchor = this.sync.worldPortAnchor(from.equipmentId, from.portId);
    if (fromAnchor) marks.push({ position: fromAnchor.position.clone(), radius: 0.26 });
    for (const shell of this.sync.portShells()) {
      const ref = shell.userData.pickRef as PickRef | undefined;
      if (!ref || ref.kind !== "port" || !ref.equipmentId || !ref.portId) continue;
      const candidate: PortRef = { equipmentId: ref.equipmentId, portId: ref.portId };
      if (connectionBlockReason(doc, from, candidate) !== null) continue;
      const anchor = this.sync.worldPortAnchor(ref.equipmentId, ref.portId);
      if (anchor) marks.push({ position: anchor.position.clone(), radius: 0.18 });
      if (marks.length >= RING_POOL - 1) break;
    }
    this.showRings(marks);
    this.controller.invalidate();
  }

  // ── 控件：短按开关 / dial 拖动 ──────────────────────────────────────────

  private moveOperateControl(gesture: OperateControl, event: PointerEvent): void {
    if (gesture.control.kind !== "dial" || !gesture.control.steps) return;
    // 短按 = 原档；上拖约 55px 升一档、下拖降一档（离散档位，不连续求解）。
    const stepsMoved = Math.round((gesture.startY - event.clientY) / 55);
    gesture.dialIndex = THREE.MathUtils.clamp(gesture.startIndex + stepsMoved, 0, gesture.control.steps.length - 1);
  }

  private commitOperateControl(gesture: OperateControl): void {
    if (gesture.control.kind === "toggle") {
      this.hooks.commit({ type: "setControl", id: gesture.equipmentId, controlId: gesture.control.id, value: !gesture.startValue });
      return;
    }
    const steps = gesture.control.steps ?? [1];
    const value = steps[THREE.MathUtils.clamp(gesture.dialIndex, 0, steps.length - 1)] ?? steps[0]!;
    this.hooks.commit({ type: "setControl", id: gesture.equipmentId, controlId: gesture.control.id, value });
  }

  // ── 拾取与坐标换算（NDC 经 getBoundingClientRect，chem-lab architecture） ───────────

  private pickAt(event: PointerEvent, kinds: PickKind[]): PickRef | null {
    this.updateNdc(event);
    this.raycaster.setFromCamera(this.ndc, this.controller.camera);
    const groups: THREE.Mesh[][] = [];
    if (kinds.includes("port")) groups.push(this.sync.portShells());
    if (kinds.includes("control")) groups.push(this.sync.controlShells());
    if (kinds.includes("claw")) groups.push(this.sync.clawShells());
    if (kinds.includes("tube")) groups.push(this.sync.tubes.tubeMeshes());
    if (kinds.includes("rack")) groups.push(this.sync.rackPickMeshes());
    if (kinds.includes("equipment")) groups.push(this.sync.pickMeshes());
    for (const group of groups) {
      if (group.length === 0) continue;
      const hits = this.raycaster.intersectObjects(group, false);
      if (hits.length === 0) continue;
      const first = hits[0]!.object.userData.pickRef as PickRef | undefined;
      if (!first) continue;
      if (first.kind === "port") {
        // 透过玻璃的端口：按屏幕距离对候选排序，不信任透明表面最近交点。
        const chosen = this.nearestPortByScreen(hits, event);
        if (chosen) return chosen;
        continue;
      }
      return first;
    }
    return null;
  }

  private nearestPortByScreen(hits: THREE.Intersection[], event: PointerEvent): PickRef | null {
    const pointer = this.pointerScreen(event);
    if (!pointer) return null;
    let best: PickRef | null = null;
    let bestDist = Infinity;
    for (const hit of hits) {
      const candidate = hit.object.userData.pickRef as PickRef | undefined;
      if (!candidate || candidate.kind !== "port" || !candidate.equipmentId || !candidate.portId) continue;
      const anchor = this.sync.worldPortAnchor(candidate.equipmentId, candidate.portId);
      if (!anchor) continue;
      const screen = this.projectToScreen(anchor.position);
      if (!screen) continue;
      const dist = Math.hypot(screen.x - pointer.x, screen.y - pointer.y);
      if (dist < bestDist) { bestDist = dist; best = candidate; }
    }
    return best;
  }

  private updateNdc(event: PointerEvent): void {
    const rect = this.canvas.getBoundingClientRect();
    this.ndc.x = ((event.clientX - rect.left) / Math.max(1, rect.width)) * 2 - 1;
    this.ndc.y = -((event.clientY - rect.top) / Math.max(1, rect.height)) * 2 + 1;
  }

  private rayHitBench(event: PointerEvent): THREE.Vector3 | null {
    this.updateNdc(event);
    this.raycaster.setFromCamera(this.ndc, this.controller.camera);
    return this.raycaster.ray.intersectPlane(this.benchPlane, new THREE.Vector3());
  }

  private rayHitPlane(event: PointerEvent, plane: THREE.Plane): THREE.Vector3 | null {
    this.updateNdc(event);
    this.raycaster.setFromCamera(this.ndc, this.controller.camera);
    return this.raycaster.ray.intersectPlane(plane, new THREE.Vector3());
  }

  private pointerScreen(event: PointerEvent): { x: number; y: number } | null {
    const rect = this.canvas.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    this.pointerPt.x = event.clientX - rect.left;
    this.pointerPt.y = event.clientY - rect.top;
    return this.pointerPt;
  }

  private projectToScreen(world: THREE.Vector3): { x: number; y: number } | null {
    const rect = this.canvas.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    const p = this.tmpV2.copy(world).project(this.controller.camera);
    this.projectPt.x = ((p.x + 1) / 2) * rect.width;
    this.projectPt.y = ((1 - p.y) / 2) * rect.height;
    return this.projectPt;
  }

  // ── 辅助查询 ────────────────────────────────────────────────────────────

  private lowestMountedChild(doc: LabDocument, standId: string) {
    let lowest: { id: string; y: number } | null = null;
    for (const child of doc.equipment) {
      if (child.parentMountId !== standId || !child.localPose) continue;
      const clamp = getEquipmentSpec(child.kind)?.anchors.clampPoint;
      if (!clamp) continue;
      const y = child.localPose.position.y + clamp.y;
      if (!lowest || y < lowest.y) lowest = { id: child.id, y };
    }
    return lowest ? doc.equipment.find(e => e.id === lowest!.id) ?? null : null;
  }

  private findSnapStand(event: PointerEvent, excludeId: string): string | null {
    const doc = this.hooks.getDocument();
    const pointer = this.pointerScreen(event);
    if (!pointer) return null;
    let best: string | null = null;
    let bestDist = SNAP_PX + 10;
    for (const stand of doc.equipment) {
      if (stand.id === excludeId || getEquipmentSpec(stand.kind)?.family !== "stand") continue;
      if (!this.standGripWorld(stand.id, this.tmpV)) continue;
      const screen = this.projectToScreen(this.tmpV);
      if (!screen) continue;
      const dist = Math.hypot(screen.x - pointer.x, screen.y - pointer.y);
      if (dist < bestDist) { bestDist = dist; best = stand.id; }
    }
    return best;
  }

  private findPourTarget(event: PointerEvent, bottleId: string): string | null {
    const doc = this.hooks.getDocument();
    const pointer = this.pointerScreen(event);
    if (!pointer) return null;
    let best: string | null = null;
    let bestDist = POUR_PX;
    for (const eq of doc.equipment) {
      if (eq.id === bottleId) continue;
      const spec = getEquipmentSpec(eq.kind);
      if (spec?.family !== "glassware" || eq.parentMountId) continue;
      if (!this.rimWorld(eq.id, this.tmpV)) continue;
      const screen = this.projectToScreen(this.tmpV);
      if (!screen) continue;
      const dist = Math.hypot(screen.x - pointer.x, screen.y - pointer.y);
      if (dist < bestDist) { bestDist = dist; best = eq.id; }
    }
    return best;
  }

  private standGripWorld(standId: string, out: THREE.Vector3): THREE.Vector3 | null {
    const group = this.sync.equipmentGroup(standId);
    if (!group) return null;
    const h = this.sync.clawVisualHeight(standId) ?? 2.6;
    return out.copy(standGripLocal()).setY(h).applyQuaternion(group.quaternion).add(group.position);
  }

  private rimWorld(equipmentId: string, out: THREE.Vector3): THREE.Vector3 | null {
    const item = this.sync.item(equipmentId);
    const rim = item ? getEquipmentSpec(item.kind)?.anchors.rim : null;
    if (!item || !rim) return null;
    return out.set(rim.x, rim.y, rim.z).applyQuaternion(item.handle.group.quaternion).add(item.handle.group.position);
  }

  // ── 复用视觉资源 ────────────────────────────────────────────────────────

  private showRings(marks: { position: THREE.Vector3; radius: number }[]): void {
    for (let i = 0; i < this.rings.length; i++) {
      const ring = this.rings[i]!;
      const mark = marks[i];
      if (!mark) { ring.visible = false; continue; }
      ring.visible = true;
      ring.position.copy(mark.position);
      ring.scale.setScalar(mark.radius);
      ring.quaternion.copy(this.controller.camera.quaternion);
    }
  }

  private gesturePointer(): number {
    const g = this.gesture;
    return "pointerId" in g ? g.pointerId : -1;
  }

  private finishGesture(): void {
    this.releaseCaptureSafe(this.gesturePointer());
    this.resetGesture();
  }

  private resetGesture(): void {
    this.gesture = { kind: "none" };
    this.controller.controls.enabled = true;
    this.controller.endActivity("drag");
    this.canvas.style.cursor = "";
    this.showRings([]);
    this.previewLine.visible = false;
    this.hooks.onDragStateChange?.(false);
    this.controller.invalidate();
  }

  private releaseCaptureSafe(pointerId: number): void {
    if (pointerId < 0) return;
    try { if (this.host.hasPointerCapture(pointerId)) this.host.releasePointerCapture(pointerId); } catch { /* 已释放 */ }
  }
}
