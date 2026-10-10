"use client";

/**
 * 演出特效引擎（chem-lab architecture）：CueTrigger 与文档 diff → 三层视觉 cue。
 * L1 主体几何（火焰锥/弯月面波纹/沉积床/雾团/液流柱）、L2 共享实例化粒子
 * （气泡/晶粒/蒸汽点/流动点/微晶片）、L3 局部光与微演出（火焰点光/热板辉光/
 * 爆发闪与可选微抖）。时序统一 off→rampUp→steady→rampDown（连续）或
 * idle→burst→dissipate（单次）；对象池化，零每帧 new；质量档与
 * prefers-reduced-motion 双重降级；不可见时随 controller rAF 停摆自然暂停。
 *
 * 事实源边界：只读文档与 SceneSync 锚点，永不回写文档/不进撤销历史；
 * 连接图（reachableCueEdges）只决定流动动画沿哪些管线播放。
 */
import * as THREE from "three";
import {
  getEquipmentSpec, pumpOutletPorts, reachableCueEdges,
  type CueTrigger, type EquipmentInstance, type LabDocument, type PortRef, type StageDefinition,
} from "@next-tutor/domain";
import type { ChemSceneController, FrameContext, QualityTier } from "./SceneController.ts";
import type { SceneSync } from "./SceneSync.ts";
import { liquidColorHex } from "./EquipmentModels.ts";

const SCRATCH_V1 = new THREE.Vector3();
const SCRATCH_V2 = new THREE.Vector3();
const SCRATCH_V3 = new THREE.Vector3();
const SCRATCH_V4 = new THREE.Vector3();
const SCRATCH_V5 = new THREE.Vector3();
const SCRATCH_COLOR = new THREE.Color();
const portKey = (r: PortRef) => `${r.equipmentId}:${r.portId}`;

// ── 共享几何（模块级、应用生命周期，特效层独有；不进工厂 ref-count） ──────
const UNIT_SPHERE = new THREE.SphereGeometry(1, 10, 8);
const UNIT_CONE = new THREE.ConeGeometry(1, 1, 10, 3, true);
const UNIT_CYL = new THREE.CylinderGeometry(1, 1, 1, 10, 1, true);
const UNIT_RING = new THREE.TorusGeometry(1, 0.05, 6, 30);
const UNIT_DISC = new THREE.CircleGeometry(1, 26);
const CRYSTAL_GEO = new THREE.OctahedronGeometry(1, 0);
const BED_GEO = new THREE.IcosahedronGeometry(1, 0);

const UP = new THREE.Vector3(0, 1, 0);

// ── 通用材质 ────────────────────────────────────────────────────────────────
function additiveMat(color: number, opacity: number): THREE.MeshBasicMaterial {
  return new THREE.MeshBasicMaterial({
    color, transparent: true, opacity, depthWrite: false, blending: THREE.AdditiveBlending,
  });
}
function translucentMat(color: number, opacity: number): THREE.MeshBasicMaterial {
  return new THREE.MeshBasicMaterial({ color, transparent: true, opacity, depthWrite: false });
}

/** 共享固定透明度材质（淡出用缩放而非 opacity，避免多效果耦合）。 */
const SHARED = {
  bubble: translucentMat(0xe8f6f2, 0.4),
  drop: translucentMat(0xd7ecf4, 0.66),
  particle: additiveMat(0xffffff, 0.9),
  flow: additiveMat(0xffffff, 0.92),
};

/** 沸腾容器种类 → 蒸汽源端口（无端口的容器只冒汽丝不接管）。 */
const VAPOR_PORTS: Record<string, string> = {
  "flask-round": "neck",
  "flask-three-neck": "neckA",
  "flask-erlenmeyer": "neck",
  beaker: "rim",
  "graduated-cylinder": "rim",
  reservoir: "return",
};
/** 洗气瓶进气内管口局部坐标（EquipmentFactory buildWashingBottle 同源）。 */
const WASH_DIP_TIP = new THREE.Vector3(-0.24, 1.12, 0);

// ── 池化粒子（火焰余烬/蒸汽丝/断口雾羽/爆发微片/水滴） ──────────────────────

interface SpawnOptions { gravity?: number; drag?: number; grow?: number }

class ParticlePool {
  readonly mesh: THREE.InstancedMesh;
  private readonly cap: number;
  private readonly px: Float32Array;
  private readonly py: Float32Array;
  private readonly pz: Float32Array;
  private readonly vx: Float32Array;
  private readonly vy: Float32Array;
  private readonly vz: Float32Array;
  private readonly age: Float32Array;
  private readonly life: Float32Array;
  private readonly size: Float32Array;
  private readonly grow: Float32Array;
  private readonly grav: Float32Array;
  private readonly drag: Float32Array;
  private readonly alive: Uint8Array;
  private readonly free: number[] = [];
  private active = 0;
  private readonly scratchMat = new THREE.Matrix4();
  private readonly scratchQuat = new THREE.Quaternion();
  private readonly scratchPos = new THREE.Vector3();
  private readonly scratchScale = new THREE.Vector3();
  private readonly scratchColor = new THREE.Color();

  constructor(parent: THREE.Object3D, cap: number) {
    this.cap = cap;
    this.mesh = new THREE.InstancedMesh(UNIT_SPHERE, SHARED.particle, cap);
    this.mesh.frustumCulled = false;
    this.mesh.name = "cue-particles";
    this.mesh.count = cap;
    parent.add(this.mesh);
    this.px = new Float32Array(cap); this.py = new Float32Array(cap); this.pz = new Float32Array(cap);
    this.vx = new Float32Array(cap); this.vy = new Float32Array(cap); this.vz = new Float32Array(cap);
    this.age = new Float32Array(cap); this.life = new Float32Array(cap); this.size = new Float32Array(cap);
    this.grow = new Float32Array(cap); this.grav = new Float32Array(cap); this.drag = new Float32Array(cap);
    this.alive = new Uint8Array(cap);
    for (let i = cap - 1; i >= 0; i--) {
      this.free.push(i);
      this.mesh.setMatrixAt(i, this.scratchMat.makeScale(0, 0, 0));
      this.mesh.setColorAt(i, this.scratchColor.set(0xffffff));
    }
  }

  get activeCount(): number { return this.active; }

  spawn(pos: THREE.Vector3, vel: THREE.Vector3, life: number, size: number, color: number, opts: SpawnOptions = {}): void {
    const i = this.free.pop();
    if (i === undefined) return;
    this.px[i] = pos.x; this.py[i] = pos.y; this.pz[i] = pos.z;
    this.vx[i] = vel.x; this.vy[i] = vel.y; this.vz[i] = vel.z;
    this.age[i] = 0; this.life[i] = life; this.size[i] = size;
    this.grow[i] = opts.grow ?? 0;
    this.grav[i] = opts.gravity ?? 0;
    this.drag[i] = opts.drag ?? 0;
    this.alive[i] = 1;
    this.active += 1;
    this.mesh.setColorAt(i, this.scratchColor.set(color));
    this.mesh.instanceColor!.needsUpdate = true;
  }

  update(dt: number): void {
    if (this.active === 0) return;
    for (let i = 0; i < this.cap; i++) {
      if (!this.alive[i]) continue;
      this.age[i] += dt;
      if (this.age[i] >= this.life[i]) {
        this.alive[i] = 0;
        this.active -= 1;
        this.free.push(i);
        this.mesh.setMatrixAt(i, this.scratchMat.makeScale(0, 0, 0));
        continue;
      }
      const d = this.drag[i];
      if (d > 0) {
        const k = Math.max(0, 1 - d * dt);
        this.vx[i] *= k; this.vz[i] *= k; this.vy[i] *= k;
      }
      this.vy[i] -= this.grav[i] * dt;
      this.px[i] += this.vx[i] * dt;
      this.py[i] += this.vy[i] * dt;
      this.pz[i] += this.vz[i] * dt;
      const t = this.age[i] / this.life[i];
      const popIn = Math.min(1, this.age[i] * 14);
      const fade = this.grow[i] > 0 ? 1 : 1 - 0.7 * t;
      const growth = this.grow[i] > 0 ? 0.5 + this.grow[i] * t : 1;
      const s = Math.max(0, this.size[i] * popIn * fade * growth);
      this.scratchPos.set(this.px[i]!, this.py[i]!, this.pz[i]!);
      this.scratchScale.set(s, s, s);
      this.mesh.setMatrixAt(i, this.scratchMat.compose(this.scratchPos, this.scratchQuat, this.scratchScale));
    }
    this.mesh.instanceMatrix.needsUpdate = true;
  }

  dispose(): void {
    this.mesh.removeFromParent();
    this.mesh.dispose();
  }
}

// ── 通用小工具 ──────────────────────────────────────────────────────────────

interface VesselHints {
  surfaceLocalY: number;
  bedLocalY: number;
  innerR: number;
  anchorNode: THREE.Object3D | null;
}

function vesselHints(sync: SceneSync, id: string, kind: string): VesselHints | null {
  const item = sync.item(id);
  const spec = getEquipmentSpec(kind);
  if (!item || !spec) return null;
  const surface = spec.anchors.liquidSurface;
  if (!surface) return null;
  return {
    surfaceLocalY: surface.y,
    bedLocalY: spec.anchors.sedimentBed?.y ?? Math.max(0.08, surface.y - 0.55),
    innerR: Math.min(spec.bounds.width, spec.bounds.depth) * 0.22,
    anchorNode: item.handle.anchorNodes.get("liquidSurface") ?? null,
  };
}

function anchorWorld(sync: SceneSync, id: string, anchor: string, out: THREE.Vector3): boolean {
  const node = sync.item(id)?.handle.anchorNodes.get(anchor);
  if (!node) return false;
  node.getWorldPosition(out);
  return true;
}

function mixHex(a: number, b: number, out: THREE.Color): THREE.Color {
  return out.setHex(a).lerp(new THREE.Color(b), 0.5);
}

// ── 火焰（L1 三层锥 + L2 余烬 + L3 点光） ──────────────────────────────────

interface FlameLayer { mesh: THREE.Mesh; mat: THREE.MeshBasicMaterial; baseR: number; baseH: number; y: number }

class FlameEffect {
  private readonly layers: FlameLayer[] = [];
  private readonly group = new THREE.Group();
  private readonly light: THREE.PointLight | null;
  private level = 0;
  private target = 1;
  private readonly seed: number;
  private emberAcc = 0;
  private readonly runtime: CueRuntime;
  private readonly baseWorld = new THREE.Vector3();

  constructor(runtime: CueRuntime, lampId: string) {
    this.runtime = runtime;
    this.seed = Math.random() * 10;
    const lamp = runtime.sync.item(lampId);
    const spec = getEquipmentSpec("alcohol-lamp");
    const wick = spec?.anchors.wick ?? { x: 0, y: 0.92, z: 0 };
    const flameH = (spec?.visualTuning?.flameH ?? 0.85) * 0.8;
    const defs = [
      { r: 0.052, h: 0.26, color: 0x9fd4ff, opacity: 0.85 },
      { r: 0.105, h: 0.5, color: 0xffb347, opacity: 0.5 },
      { r: 0.155, h: 0.72 * flameH / 0.68, color: 0xff7a2a, opacity: 0.24 },
    ];
    for (const d of defs) {
      const mat = additiveMat(d.color, d.opacity);
      const mesh = new THREE.Mesh(UNIT_CONE, mat);
      mesh.scale.set(d.r, d.h, d.r);
      mesh.position.y = d.h / 2;
      this.group.add(mesh);
      this.layers.push({ mesh, mat, baseR: d.r, baseH: d.h, y: d.h / 2 });
    }
    this.group.position.set(wick.x, wick.y + 0.16, wick.z);
    this.group.name = "flame";
    lamp?.handle.group.add(this.group);
    this.light = runtime.acquireFlameLight();
    if (this.light) {
      this.light.position.set(wick.x, wick.y + 0.5, wick.z);
      lamp?.handle.group.add(this.light);
      this.light.intensity = 0;
    }
    runtime.markMatrixDirty();
  }

  extinguish(): void { this.target = 0; }

  /** 返回 false 时可回收。 */
  update(dt: number, t: number): boolean {
    // 线性包络：点火 0.2s 长起、熄灭 0.25s 收口（chem-lab architecture: flame timing）。
    this.level = this.target > this.level
      ? Math.min(this.target, this.level + dt / 0.2)
      : Math.max(this.target, this.level - dt / 0.25);
    if (this.target === 0 && this.level < 0.02) return false;
    const calm = this.runtime.reducedMotion ? 0.3 : 1;
    const n = Math.sin(t * 3.1 + this.seed) * 0.5 + Math.sin(t * 5.7 + this.seed * 2.1) * 0.3 + Math.sin(t * 8.3 + this.seed) * 0.2;
    const hK = this.level * (1 + 0.13 * n * calm);
    for (const layer of this.layers) {
      const h = layer.baseH * hK;
      layer.mesh.scale.set(layer.baseR * (1 + 0.07 * n * calm), Math.max(0.001, h), layer.baseR * (1 - 0.05 * n * calm));
      layer.mesh.position.y = h / 2;
      layer.mesh.rotation.y += dt * 0.8;
    }
    if (this.light) this.light.intensity = this.level * (2.1 + 0.5 * n * calm);
    if (this.level > 0.6 && !this.runtime.reducedMotion) {
      this.emberAcc += dt;
      if (this.emberAcc > 0.13) {
        this.emberAcc = 0;
        this.group.getWorldPosition(this.baseWorld);
        this.baseWorld.x += (Math.random() - 0.5) * 0.07;
        this.baseWorld.z += (Math.random() - 0.5) * 0.07;
        this.baseWorld.y += 0.2 + Math.random() * 0.2;
        this.runtime.pool.spawn(this.baseWorld, SCRATCH_V1.set((Math.random() - 0.5) * 0.1, 0.5, (Math.random() - 0.5) * 0.1), 0.5, 0.017, 0xffb066, { gravity: -0.15, drag: 0.4 });
      }
    }
    return true;
  }

  dispose(): void {
    this.group.removeFromParent();
    this.light?.removeFromParent();
    this.runtime.releaseFlameLight(this.light);
    for (const layer of this.layers) layer.mat.dispose();
  }
}

// ── 热板辉光（L3 局部暖色盘面） ─────────────────────────────────────────────

class PlateGlow {
  private readonly mat = additiveMat(0xff8a3c, 0);
  private readonly disc: THREE.Mesh;
  private level = 0;
  private target = 1;
  private readonly seed = Math.random() * 8;

  constructor(parent: THREE.Object3D) {
    this.disc = new THREE.Mesh(UNIT_DISC, this.mat);
    this.disc.rotation.x = -Math.PI / 2;
    this.disc.position.set(0, 0.335, 0);
    this.disc.scale.setScalar(0.52);
    parent.add(this.disc);
  }

  setActive(on: boolean): void { this.target = on ? 1 : 0; }

  update(dt: number, t: number): boolean {
    this.level = this.target > this.level
      ? Math.min(this.target, this.level + dt / 0.3)
      : Math.max(this.target, this.level - dt / 0.3);
    if (this.target === 0 && this.level < 0.02) return false;
    this.mat.opacity = this.level * (0.3 + 0.08 * Math.sin(t * 2.4 + this.seed));
    return true;
  }

  dispose(): void {
    this.disc.removeFromParent();
    this.mat.dispose();
  }
}

// ── 沸腾/进气泡（L1 波纹环 + L2 气泡池 + 蒸汽丝发射） ─────────────────────

interface BubbleState { x: number; z: number; y: number; vy: number; phase: number; r: number }

class BoilEffect {
  private readonly group = new THREE.Group();
  private readonly bubbles: BubbleState[] = [];
  private readonly mesh: THREE.InstancedMesh;
  private readonly ripples: THREE.Mesh[] = [];
  private readonly rippleMats: THREE.MeshBasicMaterial[] = [];
  private level = 0;
  private target = 1;
  private spawnAcc = 0;
  private steamAcc = 0;
  private readonly runtime: CueRuntime;
  private readonly hints: VesselHints;
  private readonly cap: number;
  private readonly seed = Math.random() * 9;
  private readonly scratchMat = new THREE.Matrix4();
  private readonly scratchQuat = new THREE.Quaternion();
  private readonly scratchEuler = new THREE.Euler();
  mode: "heated" | "gasfed";

  constructor(runtime: CueRuntime, vesselId: string, mode: "heated" | "gasfed") {
    this.runtime = runtime;
    this.mode = mode;
    const sync = runtime.sync;
    const vessel = sync.item(vesselId);
    const hints = vesselHints(sync, vesselId, vessel?.kind ?? "");
    if (!vessel || !hints) throw new Error("boil target missing anchors");
    this.hints = hints;
    this.cap = runtime.bubbleCap;
    this.mesh = new THREE.InstancedMesh(UNIT_SPHERE, SHARED.bubble, this.cap);
    this.mesh.frustumCulled = false;
    this.mesh.name = "bubbles";
    this.group.add(this.mesh);
    // 液面双波纹环（相位交错扩散）。
    for (let i = 0; i < 2; i++) {
      const mat = translucentMat(0xffffff, 0);
      const ring = new THREE.Mesh(UNIT_RING, mat);
      ring.rotation.x = -Math.PI / 2;
      ring.position.y = hints.surfaceLocalY + 0.01;
      ring.scale.setScalar(0.001);
      this.group.add(ring);
      this.ripples.push(ring);
      this.rippleMats.push(mat);
    }
    this.group.name = "boil";
    vessel.handle.group.add(this.group);
    for (let i = 0; i < this.cap; i++) {
      this.mesh.setMatrixAt(i, this.scratchMat.makeScale(0, 0, 0));
      this.bubbles.push({ x: 0, z: 0, y: 0, vy: 0, phase: 0, r: 0 });
    }
  }

  setActive(on: boolean): void { this.target = on ? 1 : 0; }

  update(dt: number, t: number): boolean {
    // 线性包络：起沸 0.4s、停沸 0.45s（气泡量随液位缓起缓落，不瞬变）。
    this.level = this.target > this.level
      ? Math.min(this.target, this.level + dt / 0.4)
      : Math.max(this.target, this.level - dt / 0.45);
    if (this.target === 0 && this.level < 0.02) return false;
    const hints = this.hints;
    // 生成：加热→底部随机点；进气→内管口。
    this.spawnAcc += dt;
    const interval = (this.runtime.reducedMotion ? 0.3 : 0.13) / Math.max(0.15, this.level);
    while (this.spawnAcc > interval) {
      this.spawnAcc -= interval;
      const slot = this.bubbles.find(b => b.vy === 0);
      if (!slot) break;
      const spawnR = this.mode === "gasfed" ? 0.05 : hints.innerR;
      const baseX = this.mode === "gasfed" ? WASH_DIP_TIP.x : 0;
      const baseY = this.mode === "gasfed" ? WASH_DIP_TIP.y : Math.max(0.12, hints.bedLocalY * 0.4);
      slot.x = baseX + (Math.random() - 0.5) * 2 * spawnR;
      slot.z = (Math.random() - 0.5) * 2 * spawnR;
      slot.y = baseY + Math.random() * 0.08;
      slot.vy = 0.4 + Math.random() * 0.35;
      slot.phase = Math.random() * Math.PI * 2;
      slot.r = (this.mode === "gasfed" ? 0.028 : 0.035) + Math.random() * 0.03;
    }
    // 上浮 + 液面破裂。
    for (const b of this.bubbles) {
      if (b.vy === 0) continue;
      b.y += b.vy * dt;
      if (b.y >= hints.surfaceLocalY) {
        b.vy = 0;
        const idx = this.bubbles.indexOf(b);
        this.mesh.setMatrixAt(idx, this.scratchMat.makeScale(0, 0, 0));
        continue;
      }
      const popNear = hints.surfaceLocalY - b.y < 0.1;
      const r = popNear ? b.r * Math.max(0.2, (hints.surfaceLocalY - b.y) / 0.1) : b.r;
      const sway = Math.sin(t * 3 + b.phase) * 0.018;
      this.scratchPos.set(b.x + sway, b.y, b.z + Math.cos(t * 2.6 + b.phase) * 0.018);
      this.scratchScale.set(r, r * (1 + 0.22 * Math.sin(t * 6 + b.phase)), r);
      this.scratchQuat.setFromEuler(this.scratchEuler.set(0, b.phase, 0));
      const idx = this.bubbles.indexOf(b);
      this.mesh.setMatrixAt(idx, this.scratchMat.compose(this.scratchPos, this.scratchQuat, this.scratchScale));
    }
    this.mesh.instanceMatrix.needsUpdate = true;
    // 波纹。
    for (let i = 0; i < this.ripples.length; i++) {
      const phase = (t * 0.8 + i * 0.5 + this.seed) % 1;
      const s = (0.2 + 0.8 * phase) * Math.max(0.12, hints.innerR);
      this.ripples[i]!.scale.set(s, s, 1);
      this.rippleMats[i]!.opacity = this.level * 0.28 * (1 - phase);
    }
    // 蒸汽丝（加热模式且液量足够）。
    if (this.mode === "heated" && this.level > 0.4) {
      this.steamAcc += dt;
      const rate = this.runtime.reducedMotion ? 1.1 : 2.4;
      if (this.steamAcc > 1 / rate) {
        this.steamAcc = 0;
        if (this.hints.anchorNode) {
          this.hints.anchorNode.getWorldPosition(SCRATCH_V1);
          SCRATCH_V1.x += (Math.random() - 0.5) * 0.16;
          SCRATCH_V1.z += (Math.random() - 0.5) * 0.16;
          SCRATCH_V1.y += 0.12;
          this.runtime.pool.spawn(SCRATCH_V1, SCRATCH_V2.set((Math.random() - 0.5) * 0.08, 0.5 + Math.random() * 0.2, (Math.random() - 0.5) * 0.08), 1.5, 0.06, 0xe9f6f3, { grow: 2.2, drag: 0.5, gravity: -0.06 });
        }
      }
    }
    return true;
  }

  dispose(): void {
    this.group.removeFromParent();
    this.mesh.dispose();
    for (const mat of this.rippleMats) mat.dispose();
  }

  private readonly scratchPos = new THREE.Vector3();
  private readonly scratchScale = new THREE.Vector3();
}

// ── 冷凝内芯水珠 + 出口滴液 ────────────────────────────────────────────────

class CondenserEffect {
  private readonly drops: THREE.Mesh[] = [];
  private readonly group = new THREE.Group();
  private level = 0;
  private target = 1;
  private dripAcc = 0;
  private readonly seed = Math.random() * 7;
  private readonly isCoil: boolean;
  private readonly equipmentId: string;
  private readonly runtime: CueRuntime;

  constructor(runtime: CueRuntime, equipmentId: string, kind: string) {
    this.runtime = runtime;
    this.equipmentId = equipmentId;
    this.isCoil = kind === "condenser-coil";
    const count = runtime.reducedMotion ? 3 : 5;
    for (let i = 0; i < count; i++) {
      const drop = new THREE.Mesh(UNIT_SPHERE, SHARED.drop);
      drop.scale.setScalar(0.03);
      drop.userData.t = i / count;
      this.group.add(drop);
      this.drops.push(drop);
    }
    this.group.name = "condensate";
    runtime.sync.item(equipmentId)?.handle.group.add(this.group);
  }

  setActive(on: boolean): void { this.target = on ? 1 : 0; }

  private pathPoint(t: number, out: THREE.Vector3): void {
    if (this.isCoil) {
      const turns = 7;
      const a = turns * Math.PI * 2 * t;
      out.set(-1.42 + 2.84 * t, 0.23 * Math.sin(a), 0.23 * Math.cos(a));
    } else {
      out.set(-1.5 + 3.0 * t, 0, 0);
    }
  }

  update(dt: number): boolean {
    this.level = this.target > this.level
      ? Math.min(this.target, this.level + dt / 0.3)
      : Math.max(this.target, this.level - dt / 0.35);
    if (this.target === 0 && this.level < 0.02) return false;
    for (const drop of this.drops) {
      drop.userData.t = (drop.userData.t as number + dt * 0.42) % 1;
      this.pathPoint(drop.userData.t as number, SCRATCH_V1);
      drop.position.copy(SCRATCH_V1);
      const edge = Math.min(drop.userData.t as number, 1 - (drop.userData.t as number));
      const s = 0.03 * this.level * Math.min(1, edge * 8);
      drop.scale.setScalar(Math.max(0.001, s));
    }
    // 接收口逐滴。
    this.dripAcc += dt;
    if (this.dripAcc > 0.85 && this.level > 0.5) {
      this.dripAcc = 0;
      const anchor = this.runtime.sync.worldPortAnchor(this.equipmentId, "condensateOut");
      if (anchor) {
        SCRATCH_V2.set((Math.random() - 0.5) * 0.03, -0.15, (Math.random() - 0.5) * 0.03);
        this.runtime.pool.spawn(anchor.position, SCRATCH_V2, 0.5, 0.032, 0xbfe0ea, { gravity: 2.4 });
      }
    }
    return true;
  }

  dispose(): void { this.group.removeFromParent(); }
}

// ── 管线流动点（弧长采样沿 tube 曲线） ─────────────────────────────────────

interface FlowEdgeSpec { forward: boolean; colorHex: number; speed: number; len: number }
interface FlowEntry { level: number; target: boolean; spec: FlowEdgeSpec; dots: number; phase: number }

export interface FlowVisual {
  /** connId → 可视流动（forward=沿 a→b 弧长方向；len 为近似长度，速度归一用）。 */
  edges: Map<string, FlowEdgeSpec>;
  reachedPorts: Set<string>;
}

class FlowSystem {
  private readonly mesh: THREE.InstancedMesh;
  private readonly entries = new Map<string, FlowEntry>();
  private readonly cap: number;
  private readonly dotsPerEdge: number;
  private readonly colorScratch = new THREE.Color();
  private readonly matScratch = new THREE.Matrix4();
  private readonly quatIdentity = new THREE.Quaternion();
  private active = false;

  constructor(parent: THREE.Object3D, tier: QualityTier, reduced: boolean) {
    this.dotsPerEdge = (tier === "low" ? 3 : tier === "balanced" ? 5 : 7) * (reduced ? 0.6 : 1) | 0;
    this.cap = Math.max(24, this.dotsPerEdge * 22);
    this.mesh = new THREE.InstancedMesh(UNIT_SPHERE, SHARED.flow, this.cap);
    this.mesh.frustumCulled = false;
    this.mesh.name = "flow-dots";
    this.mesh.count = this.cap;
    parent.add(this.mesh);
    for (let i = 0; i < this.cap; i++) {
      this.mesh.setMatrixAt(i, this.matScratch.makeScale(0, 0, 0));
      this.mesh.setColorAt(i, this.colorScratch.set(0xffffff));
    }
  }

  get hasVisual(): boolean {
    for (const entry of this.entries.values()) if (entry.level > 0.01 || entry.target) return true;
    return false;
  }

  setEdges(sync: SceneSync, visual: FlowVisual): void {
    for (const [id, spec] of visual.edges) {
      const existing = this.entries.get(id);
      if (existing) { existing.target = true; existing.spec = spec; continue; }
      this.entries.set(id, { level: 0, target: true, spec, dots: this.dotsPerEdge, phase: Math.random() });
    }
    for (const [id, entry] of this.entries) {
      if (!visual.edges.has(id)) entry.target = false;
    }
    void sync;
    this.assignSlots();
  }

  private slots = new Map<string, number[]>();

  private assignSlots(): void {
    this.slots.clear();
    let slot = 0;
    for (const [id, entry] of this.entries) {
      const list: number[] = [];
      for (let i = 0; i < entry.dots && slot < this.cap; i++, slot++) list.push(slot);
      this.slots.set(id, list);
      for (let i = 0; i < list.length; i++) {
        this.mesh.setColorAt(list[i]!, this.colorScratch.setHex(entry.spec.colorHex));
      }
    }
    // 超出容量的槽位清零。
    for (let i = slot; i < this.cap; i++) this.mesh.setMatrixAt(i, this.matScratch.makeScale(0, 0, 0));
    this.mesh.instanceColor!.needsUpdate = true;
  }

  update(sync: SceneSync, dt: number, t: number): void {
    let any = false;
    for (const [id, entry] of this.entries) {
      entry.level += ((entry.target ? 1 : 0) - entry.level) * Math.min(1, dt * (entry.target ? 6 : 8));
      if (!entry.target && entry.level < 0.02) {
        for (const slot of this.slots.get(id) ?? []) this.mesh.setMatrixAt(slot, this.matScratch.makeScale(0, 0, 0));
        this.slots.delete(id);
        this.entries.delete(id);
        continue;
      }
      any = true;
      entry.phase += (dt * entry.spec.speed) / Math.max(0.8, entry.spec.len);
      const list = this.slots.get(id) ?? [];
      for (let i = 0; i < list.length; i++) {
        const slot = list[i]!;
        let u = (entry.phase + i / list.length) % 1;
        if (u < 0) u += 1;
        if (!entry.spec.forward) u = 1 - u;
        if (sync.tubes.sampleTube(id, u, SCRATCH_V1)) {
          const s = (0.042 + 0.014 * Math.sin(t * 7 + i)) * entry.level;
          this.matScratch.compose(SCRATCH_V1, this.quatIdentity, SCRATCH_V2.set(s, s, s));
          this.mesh.setMatrixAt(slot, this.matScratch);
        } else {
          this.mesh.setMatrixAt(slot, this.matScratch.makeScale(0, 0, 0));
        }
      }
    }
    if (any || this.active) this.mesh.instanceMatrix.needsUpdate = true;
    this.active = any;
  }

  clear(): void {
    for (const entry of this.entries.values()) entry.target = false;
  }

  dispose(): void {
    this.mesh.removeFromParent();
    this.mesh.dispose();
  }
}

// ── 倒液（细流 + 触点波纹 + 色云扩散 + 水滴） ──────────────────────────────

class PourEffect {
  private readonly stream: THREE.Mesh;
  private readonly streamMat: THREE.MeshBasicMaterial;
  private readonly rippleMats: THREE.MeshBasicMaterial[] = [];
  private readonly ripples: THREE.Mesh[] = [];
  private readonly clouds: { mesh: THREE.Mesh; mat: THREE.MeshBasicMaterial; spin: number }[] = [];
  private readonly worldGroup = new THREE.Group();
  private age = 0;
  private readonly total = 1.15;
  private readonly runtime: CueRuntime;
  private readonly toId: string;
  private readonly fromId: string | null;
  private readonly colorHex: number;
  private splashAcc = 0;
  readonly done = false;

  constructor(runtime: CueRuntime, toId: string, fromId: string | null, colorId: string | undefined) {
    this.runtime = runtime;
    this.toId = toId;
    this.fromId = fromId;
    this.colorHex = liquidColorHex(colorId);
    this.streamMat = translucentMat(this.colorHex, 0.78);
    this.stream = new THREE.Mesh(UNIT_CYL, this.streamMat);
    this.stream.renderOrder = 3;
    this.worldGroup.add(this.stream);
    runtime.group.add(this.worldGroup);
    const cloudCount = runtime.reducedMotion ? 2 : 3;
    const target = runtime.sync.item(toId)?.handle.group ?? null;
    for (let i = 0; i < cloudCount; i++) {
      const mat = translucentMat(this.colorHex, 0.3);
      const mesh = new THREE.Mesh(UNIT_SPHERE, mat);
      mesh.renderOrder = 1;
      mesh.scale.set(0.2 + Math.random() * 0.08, 0.07, 0.2 + Math.random() * 0.08);
      target?.add(mesh);
      this.clouds.push({ mesh, mat, spin: (Math.random() - 0.5) * 0.8 });
    }
    for (let i = 0; i < 2; i++) {
      const mat = translucentMat(0xffffff, 0);
      const ring = new THREE.Mesh(UNIT_RING, mat);
      ring.rotation.x = -Math.PI / 2;
      ring.userData.phase = i * 0.4;
      this.worldGroup.add(ring);
      this.ripples.push(ring);
      this.rippleMats.push(mat);
    }
  }

  update(dt: number): boolean {
    this.age += dt;
    if (this.age >= this.total) return false;
    const sync = this.runtime.sync;
    const hints = vesselHints(sync, this.toId, sync.item(this.toId)?.kind ?? "");
    if (!hints) return false;
    // 每帧重读锚点：目标容器被拖走时液流与接收点实时跟随。
    const rimOk = anchorWorld(sync, this.toId, "rim", SCRATCH_V1)
      || (hints.anchorNode?.getWorldPosition(SCRATCH_V1) ?? false);
    if (!rimOk) return false;
    const surface = hints.anchorNode ? hints.anchorNode.getWorldPosition(SCRATCH_V2) : null;
    if (!surface) return false;
    const top = SCRATCH_V3.copy(SCRATCH_V1);
    top.y += 0.82;
    if (this.fromId) {
      const fromGroup = sync.equipmentGroup(this.fromId);
      if (fromGroup) {
        SCRATCH_V4.set(fromGroup.position.x - top.x, 0, fromGroup.position.z - top.z);
        if (SCRATCH_V4.lengthSq() > 1e-6) {
          SCRATCH_V4.normalize().multiplyScalar(0.42);
          top.add(SCRATCH_V4);
        }
      }
    }
    const bottom = SCRATCH_V5.copy(surface);
    bottom.y += 0.02;
    const k = this.age / this.total;
    const envelope = k < 0.08 ? k / 0.08 : k > 0.78 ? Math.max(0, (1 - k) / 0.22) : 1;
    const dir = SCRATCH_V4.copy(top).sub(bottom);
    const len = Math.max(0.05, dir.length());
    this.stream.scale.set(0.045 * envelope + 0.004, len, 0.045 * envelope + 0.004);
    this.stream.position.copy(top).addScaledVector(dir, -0.5);
    this.stream.quaternion.setFromUnitVectors(UP, dir.normalize());
    // 触点波纹。
    for (let i = 0; i < this.ripples.length; i++) {
      const phase = (k * 2.2 + this.ripples[i]!.userData.phase) % 1;
      const s = (0.08 + 0.3 * phase) * Math.max(0.1, hints.innerR) * 2;
      this.ripples[i]!.position.copy(bottom);
      this.ripples[i]!.scale.set(s, s, 1);
      this.rippleMats[i]!.opacity = envelope * 0.35 * (1 - phase);
    }
    // 色云：丝带状在液体内层扩散后淡出（非实心单色盘）。
    for (const cloud of this.clouds) {
      cloud.mesh.visible = k < 0.9;
      cloud.mat.opacity = 0.34 * envelope;
      cloud.mesh.rotation.y += cloud.spin * dt;
      const sink = Math.min(1, k * 1.4);
      cloud.mesh.position.y = (hints.surfaceLocalY ?? 0.6) - 0.06 - sink * 0.16;
    }
    // 触点水滴。
    this.splashAcc += dt;
    if (this.splashAcc > 0.09 && envelope > 0.5) {
      this.splashAcc = 0;
      SCRATCH_V4.set((Math.random() - 0.5) * 0.16, 0.35, (Math.random() - 0.5) * 0.16);
      this.runtime.pool.spawn(bottom, SCRATCH_V4, 0.4, 0.02, this.colorHex, { gravity: 2.2, drag: 0.4 });
    }
    return true;
  }

  dispose(): void {
    this.worldGroup.removeFromParent();
    this.streamMat.dispose();
    for (const cloud of this.clouds) { cloud.mesh.removeFromParent(); cloud.mat.dispose(); }
    for (const mat of this.rippleMats) mat.dispose();
  }
}

// ── 沉淀/晶雨（雾团 + 晶粒池 + 沉积床，容器局部坐标随动） ─────────────────

interface CrystalState { x: number; z: number; y: number; vy: number; landed: boolean; phase: number }

class SedimentEffect {
  private readonly group = new THREE.Group();
  private readonly crystals: CrystalState[] = [];
  private readonly mesh: THREE.InstancedMesh;
  private readonly crystalMat: THREE.MeshBasicMaterial;
  private readonly mists: { mesh: THREE.Mesh; mat: THREE.MeshBasicMaterial; spin: number }[] = [];
  private readonly beds: { mesh: THREE.Mesh; mat: THREE.MeshBasicMaterial; scale: number }[] = [];
  private bedLevel = 0;
  private bedTarget = 0;
  private activity = 0;
  private activityTarget = 0;
  private readonly hints: VesselHints;
  private readonly cap: number;
  private readonly seed = Math.random() * 11;
  private readonly runtime: CueRuntime;
  private readonly scratchMat = new THREE.Matrix4();
  private readonly scratchQuat = new THREE.Quaternion();
  private readonly scratchEuler = new THREE.Euler();
  private readonly scratchPos = new THREE.Vector3();
  private readonly scratchScale = new THREE.Vector3();

  constructor(runtime: CueRuntime, vesselId: string, mixColor: THREE.Color) {
    this.runtime = runtime;
    const sync = runtime.sync;
    const hints = vesselHints(sync, vesselId, sync.item(vesselId)?.kind ?? "");
    if (!hints) throw new Error("sediment target missing anchors");
    this.hints = hints;
    this.cap = runtime.reducedMotion ? 7 : 12;
    this.crystalMat = new THREE.MeshBasicMaterial({ color: mixColor.clone().lerp(new THREE.Color(0xffffff), 0.45), transparent: true, opacity: 0.95 });
    this.mesh = new THREE.InstancedMesh(CRYSTAL_GEO, this.crystalMat, this.cap);
    this.mesh.frustumCulled = false;
    this.mesh.name = "crystal-rain";
    this.group.add(this.mesh);
    for (let i = 0; i < 2; i++) {
      const mat = translucentMat(mixColor.getHex(), 0);
      const mesh = new THREE.Mesh(UNIT_SPHERE, mat);
      mesh.renderOrder = 1;
      this.group.add(mesh);
      this.mists.push({ mesh, mat, spin: (Math.random() - 0.5) * 0.5 });
    }
    for (let i = 0; i < 2; i++) {
      const mat = translucentMat(mixColor.clone().multiplyScalar(0.7).getHex(), 0);
      const mesh = new THREE.Mesh(BED_GEO, mat);
      this.group.add(mesh);
      this.beds.push({ mesh, mat, scale: 0.1 + i * 0.06 });
    }
    this.group.name = "sediment";
    sync.item(vesselId)?.handle.group.add(this.group);
    for (let i = 0; i < this.cap; i++) {
      this.mesh.setMatrixAt(i, this.scratchMat.makeScale(0, 0, 0));
      this.crystals.push({ x: 0, z: 0, y: 0, vy: 0, landed: false, phase: 0 });
    }
  }

  /** 双色相遇 → 下一轮晶雨；bedTarget 累积封顶。 */
  trigger(): void {
    this.bedTarget = Math.min(1, this.bedTarget + 0.45);
    this.activityTarget = 1;
    for (const c of this.crystals) this.respawn(c, true);
  }

  /** 扰动（容器移动）：已落床晶粒重新悬浮再沉降，床面保留。 */
  disturb(): void {
    this.activityTarget = 1;
    for (const c of this.crystals) this.respawn(c, false);
  }

  private respawn(c: CrystalState, fresh: boolean): void {
    const h = this.hints;
    c.x = (Math.random() - 0.5) * 2 * h.innerR;
    c.z = (Math.random() - 0.5) * 2 * h.innerR;
    const fromY = fresh ? h.surfaceLocalY - 0.05 - Math.random() * 0.2 : h.bedLocalY + 0.1 + Math.random() * (h.surfaceLocalY - h.bedLocalY) * 0.7;
    c.y = Math.max(h.bedLocalY + 0.05, fromY);
    c.vy = 0.1 + Math.random() * 0.1;
    c.landed = false;
    c.phase = Math.random() * Math.PI * 2;
  }

  /** 是否仍需要每帧更新（落定且云雾散尽后只剩静态床面 → 释放连续渲染）。 */
  get busy(): boolean {
    return this.activityTarget > 0 || this.activity > 0.02 || this.crystals.some(c => !c.landed);
  }

  update(dt: number, t: number): boolean {
    this.activity += (this.activityTarget - this.activity) * Math.min(1, dt * 2.2);
    this.bedLevel += (this.bedTarget - this.bedLevel) * Math.min(1, dt * 0.5);
    const h = this.hints;
    // 晶粒：悬浮微旋 → 缓沉 → 落床。
    for (let i = 0; i < this.crystals.length; i++) {
      const c = this.crystals[i]!;
      if (!c.landed) {
        c.y -= c.vy * dt;
        if (c.y <= h.bedLocalY + 0.02) {
          c.y = h.bedLocalY + 0.02;
          c.landed = true;
          this.bedTarget = Math.min(1, this.bedTarget + 0.5 / this.cap);
        }
      }
      const s = c.landed ? 0.02 : 0.026 + 0.006 * Math.sin(t * 3 + c.phase);
      const sway = c.landed ? 0 : Math.sin(t * 1.8 + c.phase) * 0.012;
      this.scratchPos.set(c.x + sway, c.y, c.z);
      this.scratchScale.setScalar(s);
      this.scratchQuat.setFromEuler(this.scratchEuler.set(c.phase, t * 0.6 + c.phase, c.phase * 0.5));
      this.mesh.setMatrixAt(i, this.scratchMat.compose(this.scratchPos, this.scratchQuat, this.scratchScale));
    }
    this.mesh.instanceMatrix.needsUpdate = true;
    // 全部落定 → 云雾散去（床面保留，fill=0 时由 runtime 清理）。
    if (this.crystals.every(c => c.landed)) this.activityTarget = 0;
    // 雾团。
    for (let i = 0; i < this.mists.length; i++) {
      const mist = this.mists[i]!;
      mist.mat.opacity = this.activity * 0.2;
      mist.mesh.visible = this.activity > 0.02;
      mist.mesh.rotation.y += mist.spin * dt;
      const bob = Math.sin(t * 0.9 + this.seed + i) * 0.05;
      mist.mesh.position.set((i - 0.5) * 0.16, (h.surfaceLocalY + h.bedLocalY) / 2 + bob, (0.5 - i) * 0.12);
      const s = 0.16 + 0.1 * this.activity;
      mist.mesh.scale.set(s * 1.5, s * 0.8, s * 1.5);
    }
    // 沉积床。
    for (let i = 0; i < this.beds.length; i++) {
      const bed = this.beds[i]!;
      bed.mat.opacity = this.bedLevel * 0.72;
      bed.mesh.visible = this.bedLevel > 0.02;
      const s = bed.scale * (0.4 + 0.9 * this.bedLevel);
      bed.mesh.scale.set(s * 1.4, s * 0.3, s * 1.1);
      bed.mesh.position.set((i - 0.5) * 0.14, h.bedLocalY + 0.015, (0.5 - i) * 0.1);
      bed.mesh.rotation.y = this.seed + i * 2;
    }
    return true;
  }

  dispose(): void {
    this.group.removeFromParent();
    this.mesh.dispose();
    this.crystalMat.dispose();
    for (const mist of this.mists) mist.mat.dispose();
    for (const bed of this.beds) bed.mat.dispose();
  }
}

// ── 短促爆发（脉冲剧场专用：收缩→闪光→球面波→微片→自动复位） ─────────────

class BurstEffect {
  private readonly group = new THREE.Group();
  private readonly core: THREE.Mesh;
  private readonly coreMat: THREE.MeshBasicMaterial;
  private readonly shell: THREE.Mesh;
  private readonly shellMat: THREE.MeshBasicMaterial;
  private readonly ring: THREE.Mesh;
  private readonly ringMat: THREE.MeshBasicMaterial;
  private age = 0;
  private readonly total = 1.5;
  private readonly colorHex: number;
  private readonly runtime: CueRuntime;
  private shardsAcc = 0;

  constructor(runtime: CueRuntime, sphereId: string) {
    this.runtime = runtime;
    this.colorHex = liquidColorHex(runtime.stage.cueMap.burstColor ?? "magenta");
    this.coreMat = additiveMat(0xfff6e8, 0);
    this.core = new THREE.Mesh(UNIT_SPHERE, this.coreMat);
    this.shellMat = additiveMat(this.colorHex, 0);
    this.shell = new THREE.Mesh(UNIT_SPHERE, this.shellMat);
    this.ringMat = additiveMat(this.colorHex, 0);
    this.ring = new THREE.Mesh(UNIT_RING, this.ringMat);
    this.ring.rotation.set(Math.PI / 2 + (Math.random() - 0.5) * 0.6, Math.random() * Math.PI, 0);
    this.group.add(this.core, this.shell, this.ring);
    const core = getEquipmentSpec("observation-sphere")?.anchors.core ?? { x: 0, y: 1.15, z: 0 };
    this.group.position.set(core.x, core.y, core.z);
    this.group.name = "burst";
    runtime.sync.item(sphereId)?.handle.group.add(this.group);
    if (!runtime.reducedMotion) runtime.requestShake(0.045, 0.32);
  }

  update(dt: number): boolean {
    this.age += dt;
    if (this.age >= this.total) return false;
    const t = this.age;
    // A 0–0.18 蓄能收缩 → B 0.18–0.32 中心闪光 → C 0.2–1.1 球面波圈扩张淡出。
    if (t < 0.18) {
      const k = t / 0.18;
      this.coreMat.opacity = 0.35 * k;
      this.core.scale.setScalar(0.3 * (1 - 0.5 * k));
    } else if (t < 0.34) {
      const k = (t - 0.18) / 0.16;
      this.coreMat.opacity = 0.9 * (1 - k * 0.7);
      this.core.scale.setScalar(0.15 + 0.5 * k);
    } else {
      const k = Math.min(1, (t - 0.34) / 0.3);
      this.coreMat.opacity = 0.25 * (1 - k);
      this.core.scale.setScalar(0.65 * (1 - k * 0.4));
    }
    if (t > 0.2) {
      const k = Math.min(1, (t - 0.2) / 0.9);
      const r = 0.2 + 1.15 * k;
      this.shell.scale.setScalar(r);
      this.shellMat.opacity = 0.3 * (1 - k) * (1 - k);
      this.ring.scale.setScalar(r * 0.9);
      this.ringMat.opacity = 0.5 * (1 - k);
    } else {
      this.shellMat.opacity = 0;
      this.ringMat.opacity = 0;
    }
    // 微片（reduced-motion 关闭；柔和扩张环已覆盖语义）。
    if (!this.runtime.reducedMotion && t > 0.22 && t < 0.5) {
      this.shardsAcc += dt;
      while (this.shardsAcc > 0.02) {
        this.shardsAcc -= 0.02;
        this.group.getWorldPosition(SCRATCH_V1);
        SCRATCH_V2.set(Math.random() - 0.5, Math.random() - 0.5, Math.random() - 0.5).normalize().multiplyScalar(1.3 + Math.random() * 0.8);
        this.runtime.pool.spawn(SCRATCH_V1, SCRATCH_V2, 0.7, 0.03, this.colorHex, { gravity: 0.5, drag: 1.6 });
      }
    }
    return true;
  }

  dispose(): void {
    this.group.removeFromParent();
    this.coreMat.dispose();
    this.shellMat.dispose();
    this.ringMat.dispose();
  }
}

// ── 一次性瞬态（pour/burst 的统一宿主） ────────────────────────────────────

interface TransientEffect { update(dt: number): boolean; dispose(): void }

// ── CueRuntime ─────────────────────────────────────────────────────────────

interface SnapshotEntry {
  kind: string;
  lit: boolean;
  power: boolean;
  open: boolean;
  dial: number;
  fill: number;
  colorId: string | null;
  posKey: string;
}

export interface CueRuntimeOptions {
  getDocument: () => LabDocument;
  stage: StageDefinition;
}

export class CueRuntime {
  readonly group = new THREE.Group();
  readonly pool: ParticlePool;
  private readonly controller: ChemSceneController;
  readonly sync: SceneSync;
  readonly stage: StageDefinition;
  private readonly getDocument: () => LabDocument;
  private readonly tier: QualityTier;

  private readonly flames = new Map<string, FlameEffect>();
  private readonly glows = new Map<string, PlateGlow>();
  private readonly boils = new Map<string, BoilEffect>();
  private readonly condensers = new Map<string, CondenserEffect>();
  private readonly sediments = new Map<string, SedimentEffect>();
  private readonly transients: TransientEffect[] = [];
  private readonly flow: FlowSystem;
  private readonly mists: { equipmentId: string; acc: number }[] = [];
  private readonly impellers: { id: string; speed: number }[] = [];
  private flameLights = 0;
  private readonly maxFlameLights: number;
  private readonly snapshot = new Map<string, SnapshotEntry>();
  private readonly connSnapshot = new Map<string, { a: PortRef; b: PortRef }>();
  private readonly reached = new Set<string>();
  private readonly flowEdges = new Map<string, FlowEdgeSpec>();

  reducedMotion = false;
  readonly bubbleCap: number;
  private readonly mediaQuery: MediaQueryList | null;
  private readonly mediaListener = () => { this.reducedMotion = this.mediaQuery?.matches ?? false; };
  private readonly frameHandler = (ctx: FrameContext) => this.update(ctx);
  private readonly shake = { time: 0, dur: 1, amp: 0 };
  private effectsActivity = false;
  private disposed = false;

  constructor(controller: ChemSceneController, sync: SceneSync, opts: CueRuntimeOptions) {
    this.controller = controller;
    this.sync = sync;
    this.stage = opts.stage;
    this.getDocument = opts.getDocument;
    this.tier = controller.tier;
    this.group.name = "cue-effects";
    controller.root.add(this.group);
    // 先确定 reduced-motion 与质量档，再建依赖它们的粒子/流动池。
    this.mediaQuery = typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : null;
    this.reducedMotion = this.mediaQuery?.matches ?? false;
    this.mediaQuery?.addEventListener("change", this.mediaListener);
    const capScale = this.tier === "low" ? 0.4 : this.tier === "balanced" ? 0.7 : 1;
    this.pool = new ParticlePool(this.group, Math.round(300 * capScale));
    this.flow = new FlowSystem(this.group, this.tier, this.reducedMotion);
    this.bubbleCap = this.tier === "low" ? 6 : this.tier === "balanced" ? 10 : 14;
    this.maxFlameLights = this.tier === "high" ? 3 : this.tier === "balanced" ? 2 : 0;
    controller.addFrameListener(this.frameHandler);
    controller.invalidate();
  }

  // ── 瞬时 cue（交互层 onCue） ─────────────────────────────────────────────

  cue(trigger: CueTrigger): void {
    if (this.disposed) return;
    switch (trigger.kind) {
      case "liquid-poured":
        this.transients.push(new PourEffect(this, trigger.toEquipmentId, trigger.fromEquipmentId, trigger.colorId));
        break;
      case "liquid-added":
        this.transients.push(new PourEffect(this, trigger.equipmentId, null, trigger.colorId));
        break;
      case "pulse-activated": {
        const kind = this.sync.item(trigger.equipmentId)?.kind;
        if (kind === "observation-sphere") {
          this.transients.push(new BurstEffect(this, trigger.equipmentId));
        } else if (kind === "expansion-ball") {
          if (anchorWorld(this.sync, trigger.equipmentId, "core", SCRATCH_V1)) {
            for (let i = 0; i < 6; i++) {
              SCRATCH_V2.set(Math.random() - 0.5, Math.random() * 0.6, Math.random() - 0.5).multiplyScalar(0.9);
              this.pool.spawn(SCRATCH_V1, SCRATCH_V2, 0.5, 0.05, 0xd9e9e3, { gravity: -0.1, drag: 1.4, grow: 1.6 });
            }
          }
        }
        break;
      }
      case "pipe-disconnected": {
        const at = SCRATCH_V1.set(trigger.at.position.x, trigger.at.position.y, trigger.at.position.z);
        for (let i = 0; i < (this.reducedMotion ? 4 : 9); i++) {
          SCRATCH_V2.set((Math.random() - 0.5) * 0.5, Math.random() * 0.5 + 0.15, (Math.random() - 0.5) * 0.5);
          this.pool.spawn(at, SCRATCH_V2, 0.45, 0.035, 0xcfe8ef, { gravity: -0.05, drag: 1.2, grow: 1.5 });
        }
        break;
      }
      default:
        break; // heat/pump/valve/vessel 类经 observe() 差分驱动。
    }
    this.ensureActivity();
  }

  // ── 文档差分：连续状态 + 拓扑重算 ────────────────────────────────────────

  observe(doc: LabDocument): void {
    if (this.disposed) return;
    const prev = this.snapshot;
    const prevConns = this.connSnapshot;

    const seen = new Set<string>();
    for (const eq of doc.equipment) {
      seen.add(eq.id);
      const before = prev.get(eq.id);
      const spec = getEquipmentSpec(eq.kind);
      if (!spec) continue;
      const contents = eq.visualContents;
      const entry: SnapshotEntry = {
        kind: eq.kind,
        lit: eq.controls.lit === true,
        power: eq.controls.power === true,
        open: eq.controls.open === true,
        dial: typeof eq.controls.speed === "number" ? eq.controls.speed
          : typeof eq.controls.level === "number" ? eq.controls.level : 1,
        fill: contents?.fill ?? 0,
        colorId: contents?.colorId ?? null,
        posKey: posKeyOf(eq),
      };
      if (before) {
        // 火焰/热板/泵/阀开关迁移。
        if (entry.lit !== before.lit && eq.kind === "alcohol-lamp") {
          if (entry.lit) this.flames.set(eq.id, new FlameEffect(this, eq.id));
          else this.flames.get(eq.id)?.extinguish();
        }
        if (entry.power !== before.power && eq.kind === "hot-plate") {
          this.ensureGlow(eq.id, entry.power);
        }
        // 液体事件：双色相遇 → 晶雨；清空 → 收纳沉淀。
        if (entry.fill > before.fill + 0.01 && before.fill > 0.02 && before.colorId && entry.colorId && before.colorId !== entry.colorId) {
          this.trySediment(eq.id);
        }
        if (before.fill > 0.02 && entry.fill <= 0.02) this.clearSediment(eq.id);
        // 移动扰动 → 晶粒重新悬浮。
        if (entry.posKey !== before.posKey) this.sediments.get(eq.id)?.disturb();
      } else if (eq.kind === "alcohol-lamp" && entry.lit) {
        this.flames.set(eq.id, new FlameEffect(this, eq.id));
      } else if (eq.kind === "hot-plate" && entry.power) {
        this.ensureGlow(eq.id, true);
      }
      prev.set(eq.id, entry);
    }
    // 已删除器材：特效随之回收（挂在模型组上的节点已被移出场景树）。
    for (const id of [...prev.keys()]) {
      if (seen.has(id)) continue;
      prev.delete(id);
      this.disposeEffectFor(id);
    }
    // 连接增删：运行中的管被拆 → 断口雾羽。
    for (const conn of doc.connections) {
      prevConns.set(conn.id, { a: conn.a, b: conn.b });
    }
    for (const [id, ports] of [...prevConns.entries()]) {
      if (doc.connections.some(c => c.id === id)) continue;
      prevConns.delete(id);
      if (this.flowEdges.has(id)) this.puffAt(ports.a, ports.b);
    }

    this.recompute(doc);
    this.ensureActivity();
  }

  /** 热源邻接、沸腾集、流动通路、冷凝/雾团/叶轮 的统一重算（observe 每轮全量）。 */
  private recompute(doc: LabDocument): void {
    // 1) 加热判定：火焰/热板 锚点水平贴近且位于容器之下。
    const heatSources: { x: number; z: number; y: number }[] = [];
    for (const eq of doc.equipment) {
      if (eq.kind === "alcohol-lamp" && eq.controls.lit === true) {
        if (anchorWorld(this.sync, eq.id, "flame", SCRATCH_V1)) heatSources.push({ x: SCRATCH_V1.x, z: SCRATCH_V1.z, y: SCRATCH_V1.y });
      } else if (eq.kind === "hot-plate" && eq.controls.power === true) {
        if (anchorWorld(this.sync, eq.id, "heatZone", SCRATCH_V1)) heatSources.push({ x: SCRATCH_V1.x, z: SCRATCH_V1.z, y: SCRATCH_V1.y });
      }
    }
    const heated = new Set<string>();
    for (const eq of doc.equipment) {
      const spec = getEquipmentSpec(eq.kind);
      if (!spec?.anchors.liquidSurface || eq.kind === "u-tube") continue;
      if ((eq.visualContents?.fill ?? 0) < 0.08) continue;
      // 加热探测点：有 heatZone 锚点的容器用其底部锚点，否则取底面中心。
      let px: number;
      let py: number;
      let pz: number;
      if (spec.anchors.heatZone) {
        if (!anchorWorld(this.sync, eq.id, "heatZone", SCRATCH_V1)) continue;
        px = SCRATCH_V1.x; py = SCRATCH_V1.y; pz = SCRATCH_V1.z;
      } else {
        const group = this.sync.equipmentGroup(eq.id);
        if (!group) continue;
        px = group.position.x; pz = group.position.z; py = group.position.y + 0.1;
      }
      for (const src of heatSources) {
        if (Math.hypot(src.x - px, src.z - pz) < 0.95 && py >= src.y - 0.25) { heated.add(eq.id); break; }
      }
    }
    // 2) 流动源：泵出口（flowColor）+ 沸腾容器蒸汽口（pale）。
    const sources: { refs: PortRef[]; colorHex: number; speed: number }[] = [];
    for (const eq of doc.equipment) {
      if (eq.kind === "pump" && eq.controls.power === true) {
        const speed = typeof eq.controls.speed === "number" ? eq.controls.speed : 0.7;
        sources.push({ refs: pumpOutletPorts(doc).filter(r => r.equipmentId === eq.id), colorHex: liquidColorHex(this.stage.cueMap.flowColor), speed: 0.85 * speed });
      }
    }
    for (const id of heated) {
      const eq = doc.equipment.find(e => e.id === id);
      const portId = eq ? VAPOR_PORTS[eq.kind] : undefined;
      if (!eq || !portId) continue;
      sources.push({ refs: [{ equipmentId: id, portId }], colorHex: liquidColorHex("pale"), speed: 0.5 });
    }
    // 3) 可视通路（BFS；方向 = 流入端 → 流出端）。
    const merged = new Map<string, FlowEdgeSpec>();
    this.reached.clear();
    const flowVisual: FlowVisual = { edges: merged, reachedPorts: this.reached };
    for (const src of sources) {
      const { edges, directions } = reachableCueEdges(doc, src.refs);
      for (const id of edges) {
        if (merged.has(id)) continue;
        const ports = this.connSnapshot.get(id);
        const dir = directions.get(id);
        const forward = dir && ports ? portKey(dir.from) === portKey(ports.a) : true;
        // 近似长度：端点直线距 × 松弛系数（视觉速度归一化，无需精确弧长）。
        let len = 2;
        if (ports) {
          const anchorA = this.sync.worldPortAnchor(ports.a.equipmentId, ports.a.portId);
          const anchorB = this.sync.worldPortAnchor(ports.b.equipmentId, ports.b.portId);
          if (anchorA && anchorB) len = Math.max(0.8, anchorA.position.distanceTo(anchorB.position) * 1.3);
        }
        merged.set(id, { forward, colorHex: src.colorHex, speed: src.speed, len });
      }
      for (const r of src.refs) this.reached.add(portKey(r));
    }
    for (const id of merged.keys()) {
      const ports = this.connSnapshot.get(id);
      if (ports) { this.reached.add(portKey(ports.a)); this.reached.add(portKey(ports.b)); }
    }
    this.flowEdges.clear();
    for (const [id, spec] of merged) this.flowEdges.set(id, spec);
    this.flow.setEdges(this.sync, flowVisual);
    // 4) 沸腾集合：加热 ∪ 进气（洗气瓶 gasIn 通气且装液）。
    for (const eq of doc.equipment) {
      if (eq.kind !== "washing-bottle") continue;
      if ((eq.visualContents?.fill ?? 0) < 0.05) continue;
      if (this.reached.has(`${eq.id}:gasIn`)) heated.add(eq.id);
    }
    this.reconcileBoils(doc, heated);
    // 5) 冷凝内芯 + 集气瓶雾 + 泵叶轮。
    for (const eq of doc.equipment) {
      if (eq.kind === "condenser-coil" || eq.kind === "condenser-straight") {
        this.ensureCondenser(eq.id, eq.kind, this.reached.has(`${eq.id}:vaporIn`));
      }
    }
    this.mists.length = 0;
    for (const eq of doc.equipment) {
      if (eq.kind === "gas-collecting-bottle" && this.reached.has(`${eq.id}:in`)) {
        this.mists.push({ equipmentId: eq.id, acc: Math.random() });
      }
    }
    this.impellers.length = 0;
    for (const eq of doc.equipment) {
      if (eq.kind === "pump" && eq.controls.power === true) {
        this.impellers.push({ id: eq.id, speed: typeof eq.controls.speed === "number" ? eq.controls.speed : 0.7 });
      }
    }
  }

  private reconcileBoils(doc: LabDocument, active: Set<string>): void {
    for (const id of active) {
      const existing = this.boils.get(id);
      if (existing) { existing.setActive(true); continue; }
      const kind = doc.equipment.find(e => e.id === id)?.kind;
      if (!kind) continue;
      try {
        this.boils.set(id, new BoilEffect(this, id, kind === "washing-bottle" ? "gasfed" : "heated"));
      } catch { /* 锚点缺失的容器跳过（模型未建好） */ }
    }
    for (const [id, boil] of this.boils) {
      if (!active.has(id)) boil.setActive(false);
    }
  }

  private ensureCondenser(id: string, kind: string, active: boolean): void {
    const existing = this.condensers.get(id);
    if (existing) { existing.setActive(active); return; }
    if (!active) return;
    this.condensers.set(id, new CondenserEffect(this, id, kind));
  }

  private ensureGlow(id: string, on: boolean): void {
    const existing = this.glows.get(id);
    if (existing) { existing.setActive(on); return; }
    if (!on) return;
    const group = this.sync.equipmentGroup(id);
    if (group) this.glows.set(id, new PlateGlow(group));
  }

  private ensureSediment(id: string): SedimentEffect {
    const existing = this.sediments.get(id);
    if (existing) return existing;
    const eq = this.getDocument().equipment.find(e => e.id === id);
    const created = new SedimentEffect(this, id, mixHex(
      liquidColorHex(this.snapshot.get(id)?.colorId ?? "teal"),
      liquidColorHex(eq?.visualContents?.colorId),
      SCRATCH_COLOR,
    ));
    this.sediments.set(id, created);
    return created;
  }

  /** 混色触发入口（锚点缺失时静默跳过，不影响文档流）。 */
  private trySediment(id: string): void {
    try { this.ensureSediment(id).trigger(); } catch { /* 模型未就绪 */ }
  }

  private clearSediment(id: string): void {
    this.sediments.get(id)?.dispose();
    this.sediments.delete(id);
  }

  private disposeEffectFor(id: string): void {
    this.flames.get(id)?.dispose();
    this.flames.delete(id);
    this.glows.get(id)?.dispose();
    this.glows.delete(id);
    this.boils.get(id)?.dispose();
    this.boils.delete(id);
    this.condensers.get(id)?.dispose();
    this.condensers.delete(id);
    this.clearSediment(id);
  }

  private puffAt(a: PortRef, b: PortRef): void {
    const anchor = this.sync.worldPortAnchor(a.equipmentId, a.portId) ?? this.sync.worldPortAnchor(b.equipmentId, b.portId);
    if (!anchor) return;
    for (let i = 0; i < (this.reducedMotion ? 4 : 9); i++) {
      SCRATCH_V2.set((Math.random() - 0.5) * 0.5, Math.random() * 0.5 + 0.15, (Math.random() - 0.5) * 0.5);
      this.pool.spawn(anchor.position, SCRATCH_V2, 0.45, 0.035, 0xcfe8ef, { gravity: -0.05, drag: 1.2, grow: 1.5 });
    }
  }

  // ── 火焰点光预算 ─────────────────────────────────────────────────────────

  acquireFlameLight(): THREE.PointLight | null {
    if (this.flameLights >= this.maxFlameLights || this.tier === "low") return null;
    this.flameLights += 1;
    return new THREE.PointLight(0xffa347, 0, 3.4, 2);
  }

  releaseFlameLight(light: THREE.PointLight | null): void {
    if (light) this.flameLights = Math.max(0, this.flameLights - 1);
  }

  requestShake(amp: number, dur: number): void {
    this.shake.amp = amp;
    this.shake.dur = dur;
    this.shake.time = dur;
  }

  /** 排序标记（flame 构造时提醒渲染一帧，避免首帧空白）。 */
  markMatrixDirty(): void { this.controller.invalidate(); }

  // ── 帧更新（controller 唯一 rAF 内） ─────────────────────────────────────

  private update(ctx: FrameContext): void {
    if (this.disposed) return;
    const { dt } = ctx;
    const t = ctx.elapsed;
    for (const [id, flame] of this.flames) {
      if (!flame.update(dt, t)) { flame.dispose(); this.flames.delete(id); }
    }
    for (const [id, glow] of this.glows) {
      if (!glow.update(dt, t)) { glow.dispose(); this.glows.delete(id); }
    }
    for (const [id, boil] of this.boils) {
      if (!boil.update(dt, t)) { boil.dispose(); this.boils.delete(id); }
    }
    for (const [id, cond] of this.condensers) {
      if (!cond.update(dt)) { cond.dispose(); this.condensers.delete(id); }
    }
    for (const [, sed] of this.sediments) sed.update(dt, t);
    for (let i = this.transients.length - 1; i >= 0; i--) {
      if (!this.transients[i]!.update(dt)) {
        this.transients[i]!.dispose();
        this.transients.splice(i, 1);
      }
    }
    this.flow.update(this.sync, dt, t);
    this.pool.update(dt);
    // 集气瓶雾团发射。
    const mistColor = liquidColorHex(this.stage.cueMap.flowColor);
    for (const mist of this.mists) {
      mist.acc += dt;
      const rate = this.reducedMotion ? 0.8 : 1.8;
      if (mist.acc > 1 / rate && anchorWorld(this.sync, mist.equipmentId, "mistZone", SCRATCH_V1)) {
        mist.acc = 0;
        SCRATCH_V1.x += (Math.random() - 0.5) * 0.3;
        SCRATCH_V1.z += (Math.random() - 0.5) * 0.3;
        SCRATCH_V2.set((Math.random() - 0.5) * 0.06, 0.12 + Math.random() * 0.12, (Math.random() - 0.5) * 0.06);
        this.pool.spawn(SCRATCH_V1, SCRATCH_V2, 1.6, 0.05, mistColor, { grow: 2, drag: 0.6 });
      }
    }
    // 泵叶轮旋转（L1 微动）。
    for (const pump of this.impellers) {
      const impeller = this.sync.equipmentGroup(pump.id)?.getObjectByName("impeller");
      if (impeller) impeller.rotation.y += dt * pump.speed * (this.reducedMotion ? 8 : 22);
    }
    // 相机微抖（可关闭；每帧叠加偏移，OrbitControls 下一帧自恢复）。
    if (this.shake.time > 0 && !this.reducedMotion) {
      this.shake.time = Math.max(0, this.shake.time - dt);
      const k = (this.shake.time / this.shake.dur) * this.shake.amp;
      this.controller.camera.position.x += (Math.random() - 0.5) * k;
      this.controller.camera.position.y += (Math.random() - 0.5) * k;
      this.controller.camera.position.z += (Math.random() - 0.5) * k;
    }
    this.ensureActivity();
  }

  private ensureActivity(): void {
    let sedimentBusy = false;
    for (const sed of this.sediments.values()) {
      if (sed.busy) { sedimentBusy = true; break; }
    }
    const needs = this.flames.size > 0 || this.glows.size > 0 || this.boils.size > 0
      || this.condensers.size > 0 || sedimentBusy || this.transients.length > 0
      || this.flow.hasVisual || this.mists.length > 0 || this.pool.activeCount > 0
      || this.shake.time > 0;
    if (needs && !this.effectsActivity) {
      this.effectsActivity = true;
      this.controller.beginActivity("effects");
    } else if (!needs && this.effectsActivity) {
      this.effectsActivity = false;
      this.controller.endActivity("effects");
    }
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.controller.removeFrameListener(this.frameHandler);
    this.mediaQuery?.removeEventListener("change", this.mediaListener);
    for (const id of [...this.flames.keys()]) this.disposeEffectFor(id);
    for (const id of [...this.glows.keys()]) { this.glows.get(id)?.dispose(); this.glows.delete(id); }
    for (const id of [...this.boils.keys()]) { this.boils.get(id)?.dispose(); this.boils.delete(id); }
    for (const id of [...this.condensers.keys()]) { this.condensers.get(id)?.dispose(); this.condensers.delete(id); }
    for (const id of [...this.sediments.keys()]) this.clearSediment(id);
    for (const transient of this.transients) transient.dispose();
    this.transients.length = 0;
    this.flow.dispose();
    this.pool.dispose();
    if (this.effectsActivity) this.controller.endActivity("effects");
    this.group.removeFromParent();
  }
}

function posKeyOf(eq: EquipmentInstance): string {
  const pose = eq.parentMountId ? eq.localPose ?? eq.pose : eq.pose;
  return `${pose.position.x.toFixed(2)},${pose.position.y.toFixed(2)},${pose.position.z.toFixed(2)}`;
}
