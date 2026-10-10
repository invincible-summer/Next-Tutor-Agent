"use client";

/**
 * 器材工厂（chem-lab architecture）：domain equipment.ts 规格表 → 参数化 Three 模型。
 * 每个模型返回 {group, portNodes, anchorNodes, pickParts, update, dispose}；
 * 几何/材质尽量共享（EquipmentModels ref-count 缓存），液体层等实例独有资源
 * 由 handle.dispose 释放。原点是底面中心、Y 向上，与端口/锚点局部坐标一致。
 */
import * as THREE from "three";
import { equipmentCenterY, getEquipmentSpec, type EquipmentInstance } from "@next-tutor/domain";
import type { QualityTier } from "./SceneController.ts";
import {
  acquireGeometry, acquireMaterial, buildLiquidLathe, graduatedScaleTexture, liquidMaterial,
  MAT, portRingMesh, reagentLabelTexture, releaseGeometry, releaseMaterial, releaseTexture,
  roundedBoxOf, sharedMaterial, type ProfilePoint,
} from "./EquipmentModels.ts";

const UP = new THREE.Vector3(0, 1, 0);

/** 默认享受 transmission 近景玻璃的器材（每场景再按数量预算收敛）。 */
const HERO_KINDS = new Set(["flask-round", "washing-bottle", "condenser-coil", "observation-sphere"]);

export interface EquipmentModelHandle {
  kind: string;
  group: THREE.Group;
  portNodes: Map<string, THREE.Object3D>;
  anchorNodes: Map<string, THREE.Object3D>;
  pickParts: THREE.Mesh[];
  /** 由文档实例同步视觉状态（液量/颜色/阀门/指示灯）。 */
  update: (instance: EquipmentInstance) => void;
  setClawHeight?: (height: number) => void;
  getClawHeight?: () => number;
  dispose: () => void;
}

export interface CreateModelOptions {
  tier: QualityTier;
  /** 近景主体（transmission 玻璃）；低档自动降级为普通透明。 */
  hero?: boolean;
}

/** 采集共享资源键（key → 获取次数），dispose 时按次数归还。 */
class ResourceNotes {
  readonly geos = new Map<string, number>();
  readonly mats = new Map<string, number>();
  readonly textures = new Map<string, number>();
  noteGeo(key: string): void { this.geos.set(key, (this.geos.get(key) ?? 0) + 1); }
  noteMat(key: string): void { this.mats.set(key, (this.mats.get(key) ?? 0) + 1); }
  dropMat(key: string): void {
    const count = this.mats.get(key) ?? 0;
    if (count <= 1) this.mats.delete(key);
    else this.mats.set(key, count - 1);
    releaseMaterial(key);
  }
  noteTexture(key: string): void { this.textures.set(key, (this.textures.get(key) ?? 0) + 1); }
  releaseAll(): void {
    for (const [key, n] of this.geos) for (let i = 0; i < n; i++) releaseGeometry(key);
    for (const [key, n] of this.mats) for (let i = 0; i < n; i++) releaseMaterial(key);
    for (const [key, n] of this.textures) for (let i = 0; i < n; i++) releaseTexture(key);
    this.geos.clear();
    this.mats.clear();
    this.textures.clear();
  }
}

interface Ctx {
  group: THREE.Group;
  glass: THREE.Material;
  notes: ResourceNotes;
  ownedLiquid?: THREE.Mesh;
  liquidSpec?: { bottom: number; liquidTop: number; innerR: (y: number) => number };
  specialLiquid?: (fill: number, colorId?: string, cloudy?: number) => void;
  liquidMaterialKey?: string;
  currentLiquidMaterial?: THREE.Material;
  valveHandle?: THREE.Object3D;
  ledMesh?: THREE.Object3D;
  clawGroup?: THREE.Group;
}

function v3(p: { x: number; y: number; z: number }): THREE.Vector3 {
  return new THREE.Vector3(p.x, p.y, p.z);
}

function latheGeo(profile: ProfilePoint[], segments: number): THREE.LatheGeometry {
  return new THREE.LatheGeometry(profile.map(([r, y]) => new THREE.Vector2(r, y)), segments);
}

/** 竖直/斜向接管：单位圆柱缓存 + 网格缩放旋转（避免每管一个几何）。 */
function tubeBetween(ctx: Ctx, radiusKey: string, radius: number, from: THREE.Vector3, to: THREE.Vector3, material: THREE.Material): THREE.Mesh {
  const geoKey = `chem:geo:tube:${radiusKey}`;
  const geo = acquireGeometry(geoKey, () => new THREE.CylinderGeometry(1, 1, 1, 20, 1, true));
  ctx.notes.noteGeo(geoKey);
  const mesh = new THREE.Mesh(geo, material);
  const dir = to.clone().sub(from);
  const length = dir.length();
  mesh.scale.set(radius, length, radius);
  mesh.quaternion.setFromUnitVectors(UP, dir.normalize());
  mesh.position.copy(from).addScaledVector(dir, 0.5);
  return mesh;
}

function addMesh(ctx: Ctx, mesh: THREE.Mesh, opts: { castShadow?: boolean } = {}): THREE.Mesh {
  mesh.castShadow = opts.castShadow ?? mesh.castShadow;
  ctx.group.add(mesh);
  return mesh;
}

/** 共享几何玻璃件；renderOrder=2 让内部液体（order 1）先绘。 */
function glassPart(ctx: Ctx, geoKey: string, build: () => THREE.BufferGeometry): THREE.Mesh {
  const geo = acquireGeometry(geoKey, build);
  ctx.notes.noteGeo(geoKey);
  const mesh = new THREE.Mesh(geo, ctx.glass);
  mesh.renderOrder = 2;
  return mesh;
}

function solidPart(ctx: Ctx, geoKey: string, build: () => THREE.BufferGeometry, matKey: string): THREE.Mesh {
  const geo = acquireGeometry(geoKey, build);
  ctx.notes.noteGeo(geoKey);
  ctx.notes.noteMat(matKey);
  const mesh = new THREE.Mesh(geo, sharedMaterial(matKey));
  mesh.castShadow = true;
  return mesh;
}

/** 共享缓存但按需构造的贴图材质（刻度条/标签条）。 */
function basicTextureMaterial(ctx: Ctx, matKey: string): THREE.MeshBasicMaterial {
  ctx.notes.noteMat(matKey);
  return acquireMaterial(matKey, () => new THREE.MeshBasicMaterial({ transparent: true })) as THREE.MeshBasicMaterial;
}

function liquidMaterialKey(colorId?: string, cloudy = false): string {
  return `chem:liquid:${colorId ?? "pale"}:${cloudy ? "c" : "n"}`;
}

/** 每个模型只持有当前液体材质的一份缓存引用，颜色切换时立即归还旧引用。 */
function acquireLiquidMaterial(ctx: Ctx, colorId?: string, cloudy = false): THREE.Material {
  const key = liquidMaterialKey(colorId, cloudy);
  if (ctx.liquidMaterialKey === key && ctx.currentLiquidMaterial) return ctx.currentLiquidMaterial;
  if (ctx.liquidMaterialKey) ctx.notes.dropMat(ctx.liquidMaterialKey);
  const material = liquidMaterial(colorId, cloudy);
  ctx.notes.noteMat(key);
  ctx.liquidMaterialKey = key;
  ctx.currentLiquidMaterial = material;
  return material;
}

/** 容器液体层的公共更新路径（离散操作触发，非每帧）。 */
function updateLiquidMesh(ctx: Ctx, fill: number, colorId?: string, cloudy?: number): void {
  if (!ctx.liquidSpec) return;
  if (fill <= 0.015) {
    if (ctx.ownedLiquid) {
      ctx.group.remove(ctx.ownedLiquid);
      ctx.ownedLiquid.geometry.dispose();
      ctx.ownedLiquid = undefined;
    }
    if (ctx.liquidMaterialKey) {
      ctx.notes.dropMat(ctx.liquidMaterialKey);
      ctx.liquidMaterialKey = undefined;
      ctx.currentLiquidMaterial = undefined;
    }
    return;
  }
  const cloudyOn = (cloudy ?? 0) > 0.4;
  const geo = buildLiquidLathe(ctx.liquidSpec, fill);
  const mat = acquireLiquidMaterial(ctx, colorId, cloudyOn);
  if (ctx.ownedLiquid) {
    ctx.group.remove(ctx.ownedLiquid);
    ctx.ownedLiquid.geometry.dispose();
  }
  const mesh = new THREE.Mesh(geo, mat);
  mesh.renderOrder = 1;
  mesh.name = "liquid";
  ctx.group.add(mesh);
  ctx.ownedLiquid = mesh;
}

// ── 各器材造型 ─────────────────────────────────────────────────────────────

type Builder = (ctx: Ctx) => void;

const sphereInnerR = (center: number, radius: number) => (y: number) =>
  Math.sqrt(Math.max(0, radius * radius - (y - center) * (y - center)));

function buildFlaskRound(ctx: Ctx): void {
  const profile: ProfilePoint[] = [
    [0.02, 0], [0.3, 0.01], [0.5, 0.1], [0.6, 0.34], [0.62, 0.62], [0.575, 0.92],
    [0.42, 1.16], [0.2, 1.3], [0.155, 1.42], [0.15, 1.94], [0.178, 2.0],
    [0.166, 2.06], [0.142, 2.1], [0.126, 2.06],
  ];
  addMesh(ctx, glassPart(ctx, "chem:geo:flask-round", () => latheGeo(profile, 64)));
  ctx.liquidSpec = { bottom: 0.08, liquidTop: 1.14, innerR: sphereInnerR(0.62, 0.575) };
}

function buildBeaker(ctx: Ctx): void {
  const profile: ProfilePoint[] = [
    [0.03, 0], [0.5, 0], [0.505, 1.42], [0.462, 1.5], [0.5, 1.56], [0.53, 1.6], [0.5, 1.63],
  ];
  addMesh(ctx, glassPart(ctx, "chem:geo:beaker", () => latheGeo(profile, 56)));
  ctx.liquidSpec = { bottom: 0.06, liquidTop: 1.34, innerR: () => 0.46 };
}

function buildGraduatedCylinder(ctx: Ctx): void {
  const foot = glassPart(ctx, "chem:geo:cyl-foot", () => new THREE.CylinderGeometry(0.3, 0.32, 0.07, 32));
  foot.position.y = 0.035;
  addMesh(ctx, foot);
  const profile: ProfilePoint[] = [
    [0.02, 0], [0.2, 0.08], [0.208, 2.42], [0.185, 2.5], [0.215, 2.56], [0.2, 2.6],
  ];
  addMesh(ctx, glassPart(ctx, "chem:geo:cylinder", () => latheGeo(profile, 48)));
  // 前侧程序刻度条（演示刻度，非精确量具）。
  const stripMat = basicTextureMaterial(ctx, "chem:mat:cyl-scale");
  stripMat.map = graduatedScaleTexture();
  stripMat.needsUpdate = true;
  // The texture is shared through the material cache. Retain one reference per
  // model handle so removing the first cylinder cannot invalidate another map.
  ctx.notes.noteTexture("chem:tex:graduated");
  const strip = new THREE.Mesh(
    acquireGeometry("chem:geo:cyl-scale", () => new THREE.CylinderGeometry(0.213, 0.213, 2.2, 24, 1, true, -0.55, 1.1)),
    stripMat,
  );
  ctx.notes.noteGeo("chem:geo:cyl-scale");
  strip.renderOrder = 3;
  strip.name = "scale";
  // 刻度条几何以自身中心为原点（高 2.2），抬到筒身中段，否则下半没入台面。
  strip.position.y = 1.3;
  addMesh(ctx, strip);
  ctx.liquidSpec = { bottom: 0.12, liquidTop: 2.3, innerR: () => 0.18 };
}

function buildFlaskThreeNeck(ctx: Ctx): void {
  const profile: ProfilePoint[] = [
    [0.02, 0], [0.34, 0.02], [0.55, 0.14], [0.62, 0.55], [0.58, 1.0], [0.3, 1.26],
    [0.155, 1.42], [0.148, 2.42], [0.18, 2.47], [0.16, 2.53], [0.14, 2.5],
  ];
  addMesh(ctx, glassPart(ctx, "chem:geo:flask-3n", () => latheGeo(profile, 60)));
  for (const side of [-1, 1] as const) {
    const from = new THREE.Vector3(side * 0.24, 1.3, 0);
    const to = new THREE.Vector3(side * 0.82, 1.62, 0);
    addMesh(ctx, tubeBetween(ctx, "s7", 0.072, from, to, ctx.glass));
    const flare = glassPart(ctx, "chem:geo:flask-3n-flare", () => new THREE.CylinderGeometry(0.1, 0.072, 0.07, 20, 1, true));
    flare.position.copy(to);
    flare.quaternion.setFromUnitVectors(UP, to.clone().sub(from).normalize());
    addMesh(ctx, flare);
  }
  ctx.liquidSpec = { bottom: 0.1, liquidTop: 1.02, innerR: sphereInnerR(0.6, 0.575) };
}

function buildFlaskErlenmeyer(ctx: Ctx): void {
  const profile: ProfilePoint[] = [
    [0.02, 0], [0.58, 0.03], [0.6, 0.12], [0.52, 1.3], [0.17, 1.82], [0.15, 1.92],
    [0.18, 1.97], [0.16, 2.0], [0.14, 1.96],
  ];
  addMesh(ctx, glassPart(ctx, "chem:geo:erlenmeyer", () => latheGeo(profile, 56)));
  ctx.liquidSpec = {
    bottom: 0.08,
    liquidTop: 1.6,
    innerR: (y) => 0.14 + 0.36 * Math.min(1, Math.max(0, (1.78 - y) / 1.66)),
  };
}

function buildWashingBottle(ctx: Ctx): void {
  const profile: ProfilePoint[] = [
    [0.05, 0], [0.46, 0.03], [0.5, 0.1], [0.5, 2.32], [0.42, 2.6], [0.24, 2.84],
    [0.1, 2.95], [0.02, 3.0],
  ];
  addMesh(ctx, glassPart(ctx, "chem:geo:wash-body", () => latheGeo(profile, 60)));
  for (const side of [-1, 1] as const) {
    const x = side * 0.24;
    const collar = glassPart(ctx, "chem:geo:wash-collar", () => new THREE.CylinderGeometry(0.105, 0.12, 0.14, 24, 1, true));
    collar.position.set(x, 2.94, 0);
    addMesh(ctx, collar);
    // 内管：进气深入液下（dipDepth 1.9），出气只到罩顶下方。
    const depth = side < 0 ? 1.85 : 0.28;
    addMesh(ctx, tubeBetween(ctx, "w6", 0.062, new THREE.Vector3(x, 2.96, 0), new THREE.Vector3(x, 2.96 - depth, 0), ctx.glass));
    const tip = glassPart(ctx, "chem:geo:wash-tip", () => new THREE.CylinderGeometry(0.075, 0.062, 0.05, 18, 1, true));
    tip.position.set(x, 2.96 - depth, 0);
    addMesh(ctx, tip);
  }
  ctx.liquidSpec = { bottom: 0.1, liquidTop: 2.2, innerR: () => 0.45 };
}

function addCondenserWaterNozzles(ctx: Ctx): void {
  for (const side of [-1, 1] as const) {
    const x = side * 1.35;
    addMesh(ctx, tubeBetween(ctx, "w5", 0.05, new THREE.Vector3(x * 0.94, 0.34, 0), new THREE.Vector3(x, 0.6, 0), ctx.glass));
    const ring = solidPart(ctx, "chem:geo:nozzle-ring", () => new THREE.TorusGeometry(0.058, 0.016, 8, 18), MAT.steel);
    ring.position.set(x, 0.56, 0);
    ring.rotation.x = Math.PI / 2 - 0.42;
    addMesh(ctx, ring);
  }
}

function addCondenserClampBand(ctx: Ctx, radius: number): void {
  const band = solidPart(ctx, `chem:geo:clamp-band:${radius}`, () => {
    const geo = new THREE.TorusGeometry(radius + 0.015, 0.035, 10, 32);
    geo.rotateY(Math.PI / 2);
    return geo;
  }, MAT.steel);
  addMesh(ctx, band);
  const boss = solidPart(ctx, "chem:geo:clamp-boss", () => new THREE.BoxGeometry(0.1, 0.16, 0.12), MAT.iron);
  boss.position.y = radius + 0.05;
  addMesh(ctx, boss);
}

function buildCondenserCoil(ctx: Ctx): void {
  const jacket = glassPart(ctx, "chem:geo:coil-jacket", () => {
    const geo = new THREE.CylinderGeometry(0.4, 0.4, 3.2, 36, 1, true);
    geo.rotateZ(Math.PI / 2);
    return geo;
  });
  addMesh(ctx, jacket);
  for (const side of [-1, 1] as const) {
    const cap = glassPart(ctx, "chem:geo:coil-cap", () => new THREE.SphereGeometry(0.4, 28, 18));
    cap.position.x = side * 1.56;
    addMesh(ctx, cap);
    addMesh(ctx, tubeBetween(ctx, "g7", 0.07, new THREE.Vector3(side * 1.56, 0, 0), new THREE.Vector3(side * 2.0, 0, 0), ctx.glass));
  }
  // 螺旋内芯：7 匝、半径 0.23（夹套内壁余量）；与水路视觉独立。
  const coil = glassPart(ctx, "chem:geo:coil-inner", () => {
    const turns = 7;
    const points: THREE.Vector3[] = [];
    for (let i = 0; i <= 90; i++) {
      const t = i / 90;
      const a = turns * Math.PI * 2 * t;
      points.push(new THREE.Vector3(-1.42 + 2.84 * t, 0.23 * Math.sin(a), 0.23 * Math.cos(a)));
    }
    return new THREE.TubeGeometry(new THREE.CatmullRomCurve3(points), 140, 0.062, 10, false);
  });
  coil.name = "coil-inner";
  addMesh(ctx, coil);
  addCondenserWaterNozzles(ctx);
  addCondenserClampBand(ctx, 0.4);
}

function buildCondenserStraight(ctx: Ctx): void {
  const jacket = glassPart(ctx, "chem:geo:str-jacket", () => {
    const geo = new THREE.CylinderGeometry(0.34, 0.34, 2.5, 32, 1, true);
    geo.rotateZ(Math.PI / 2);
    return geo;
  });
  addMesh(ctx, jacket);
  for (const side of [-1, 1] as const) {
    const cap = glassPart(ctx, "chem:geo:str-cap", () => new THREE.SphereGeometry(0.34, 24, 16));
    cap.position.x = side * 1.22;
    addMesh(ctx, cap);
  }
  const core = tubeBetween(ctx, "g7", 0.07, new THREE.Vector3(-1.65, 0, 0), new THREE.Vector3(1.65, 0, 0), ctx.glass);
  core.name = "core-tube";
  addMesh(ctx, core);
  addCondenserWaterNozzles(ctx);
  addCondenserClampBand(ctx, 0.34);
}

function buildSettlingBottle(ctx: Ctx): void {
  const profile: ProfilePoint[] = [
    [0.06, 0.16], [0.07, 0.2], [0.12, 0.26], [0.3, 0.36], [0.45, 0.5], [0.46, 0.56],
    [0.46, 3.16], [0.4, 3.34], [0.24, 3.46], [0.09, 3.5], [0.02, 3.52],
  ];
  addMesh(ctx, glassPart(ctx, "chem:geo:settle-body", () => latheGeo(profile, 56)));
  for (const x of [-0.2, 0.22]) {
    const collar = glassPart(ctx, "chem:geo:settle-collar", () => new THREE.CylinderGeometry(0.09, 0.105, 0.14, 22, 1, true));
    collar.position.set(x, 3.44, 0);
    addMesh(ctx, collar);
  }
  // 下出口：漏斗尖 → 向前下引出的弯嘴（到 bottomOut 端口）。
  addMesh(ctx, tubeBetween(ctx, "w5", 0.05, new THREE.Vector3(0, 0.2, 0), new THREE.Vector3(0, 0.14, 0.44), ctx.glass));
  const tip = glassPart(ctx, "chem:geo:settle-tip", () => new THREE.CylinderGeometry(0.068, 0.05, 0.05, 16, 1, true));
  tip.position.set(0, 0.14, 0.44);
  tip.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), new THREE.Vector3(0, -0.3, 1).normalize());
  addMesh(ctx, tip);
  ctx.liquidSpec = { bottom: 0.62, liquidTop: 3.0, innerR: () => 0.42 };
}

function buildUTube(ctx: Ctx): void {
  const tube = glassPart(ctx, "chem:geo:utube", () => {
    const points: THREE.Vector3[] = [
      new THREE.Vector3(-0.42, 1.8, 0), new THREE.Vector3(-0.42, 0.9, 0), new THREE.Vector3(-0.42, 0.55, 0),
    ];
    for (let i = 0; i <= 20; i++) {
      const a = Math.PI - (Math.PI * i) / 20;
      points.push(new THREE.Vector3(0.42 * Math.cos(a), 0.42 + 0.42 * Math.sin(a), 0));
    }
    points.push(new THREE.Vector3(0.42, 0.55, 0), new THREE.Vector3(0.42, 0.9, 0), new THREE.Vector3(0.42, 1.8, 0));
    return new THREE.TubeGeometry(new THREE.CatmullRomCurve3(points), 90, 0.115, 14, false);
  });
  tube.name = "utube-glass";
  addMesh(ctx, tube);
  const clampBase = solidPart(ctx, "chem:geo:utube-base", () => roundedBoxOf("chem:geo:utube-base", 0.95, 0.3, 0.22, 0.05), MAT.plasticDark);
  clampBase.position.y = 0.11;
  addMesh(ctx, clampBase);
  const screw = solidPart(ctx, "chem:geo:utube-screw", () => new THREE.CylinderGeometry(0.045, 0.045, 0.1, 12), MAT.brass);
  screw.position.set(0, 0.26, 0);
  addMesh(ctx, screw);
  // 液柱：两臂独立、随 fill 升降。
  const arms: THREE.Mesh[] = [];
  for (const side of [-1, 1] as const) {
    const arm = new THREE.Mesh(
      acquireGeometry("chem:geo:utube-liquid", () => new THREE.CylinderGeometry(0.085, 0.085, 1, 18, 1)),
      acquireLiquidMaterial(ctx),
    );
    ctx.notes.noteGeo("chem:geo:utube-liquid");
    arm.position.set(side * 0.42, 0.62, 0);
    arm.scale.y = 0.001;
    arm.renderOrder = 1;
    arm.visible = false;
    arm.name = `utube-liquid:${side < 0 ? "a" : "b"}`;
    ctx.group.add(arm);
    arms.push(arm);
  }
  ctx.specialLiquid = (fill, colorId, cloudy) => {
    const mat = acquireLiquidMaterial(ctx, colorId, (cloudy ?? 0) > 0.4);
    for (const arm of arms) {
      arm.material = mat;
      if (fill > 0.02) {
        arm.visible = true;
        const h = 0.2 + 1.05 * Math.min(1, fill);
        arm.scale.y = h;
        arm.position.y = 0.5 + h / 2;
      } else {
        arm.visible = false;
      }
    }
  };
}

function buildGasCollectingBottle(ctx: Ctx): void {
  const profile: ProfilePoint[] = [
    [0.08, 0], [0.5, 0], [0.55, 0.12], [0.55, 1.9], [0.48, 2.08], [0.3, 2.22],
    [0.12, 2.3], [0.02, 2.33],
  ];
  addMesh(ctx, glassPart(ctx, "chem:geo:collect-body", () => latheGeo(profile, 56)));
  for (const x of [-0.22, 0.24]) {
    const collar = glassPart(ctx, "chem:geo:collect-collar", () => new THREE.CylinderGeometry(0.09, 0.105, 0.14, 22, 1, true));
    collar.position.set(x, 2.26, 0);
    addMesh(ctx, collar);
  }
  const ring = glassPart(ctx, "chem:geo:collect-base", () => new THREE.TorusGeometry(0.5, 0.03, 8, 32));
  ring.rotation.x = Math.PI / 2;
  ring.position.y = 0.05;
  addMesh(ctx, ring);
  ctx.liquidSpec = { bottom: 0.14, liquidTop: 1.8, innerR: () => 0.5 };
}

function buildObservationSphere(ctx: Ctx): void {
  const body = glassPart(ctx, "chem:geo:obs-sphere", () => new THREE.SphereGeometry(0.8, 40, 28));
  body.position.y = 1.15;
  body.name = "sphere-shell";
  addMesh(ctx, body);
  const seam = glassPart(ctx, "chem:geo:obs-seam", () => new THREE.TorusGeometry(0.8, 0.008, 6, 48));
  seam.position.y = 1.15;
  seam.renderOrder = 3;
  addMesh(ctx, seam);
  for (const side of [-1, 1] as const) {
    addMesh(ctx, tubeBetween(ctx, "g8", 0.085, new THREE.Vector3(side * 0.72, 1.15, 0), new THREE.Vector3(side * 0.98, 1.15, 0), ctx.glass));
    const flange = glassPart(ctx, "chem:geo:obs-flange", () => new THREE.CylinderGeometry(0.13, 0.13, 0.035, 22));
    flange.rotation.z = Math.PI / 2;
    flange.position.set(side * 0.99, 1.15, 0);
    addMesh(ctx, flange);
  }
  const bottomJoint = glassPart(ctx, "chem:geo:obs-bottom", () => new THREE.CylinderGeometry(0.12, 0.085, 0.24, 22));
  bottomJoint.position.y = 0.42;
  addMesh(ctx, bottomJoint);
  ctx.liquidSpec = { bottom: 0.5, liquidTop: 1.75, innerR: sphereInnerR(1.15, 0.76) };
}

function buildExpansionBall(ctx: Ctx): void {
  const body = glassPart(ctx, "chem:geo:expand-ball", () => new THREE.SphereGeometry(0.62, 36, 24));
  body.position.y = 0.95;
  addMesh(ctx, body);
  for (const side of [-1, 1] as const) {
    addMesh(ctx, tubeBetween(ctx, "g6", 0.072, new THREE.Vector3(side * 0.5, 0.95, 0), new THREE.Vector3(side * 0.62, 0.95, 0), ctx.glass));
  }
}

function buildReservoir(ctx: Ctx): void {
  const bottom = glassPart(ctx, "chem:geo:res-bottom", () => roundedBoxOf("chem:geo:res-bottom", 1.36, 0.96, 0.06, 0.03));
  bottom.position.y = 0.03;
  addMesh(ctx, bottom);
  const wallH = 1.16;
  for (const side of [-1, 1] as const) {
    const front = glassPart(ctx, "chem:geo:res-wallx", () => new THREE.BoxGeometry(1.4, wallH, 0.05));
    front.position.set(0, 0.06 + wallH / 2, side * 0.475);
    addMesh(ctx, front);
    const end = glassPart(ctx, "chem:geo:res-wallz", () => new THREE.BoxGeometry(0.05, wallH, 0.9));
    end.position.set(side * 0.675, 0.06 + wallH / 2, 0);
    addMesh(ctx, end);
    const rail = solidPart(ctx, "chem:geo:res-rail", () => new THREE.CylinderGeometry(0.018, 0.018, 1.4, 10), MAT.steel);
    rail.rotation.z = Math.PI / 2;
    rail.position.set(0, 1.24, side * 0.475);
    addMesh(ctx, rail);
  }
  addMesh(ctx, tubeBetween(ctx, "w6", 0.06, new THREE.Vector3(0.6, 0.4, 0), new THREE.Vector3(0.78, 0.4, 0), ctx.glass));
  addMesh(ctx, tubeBetween(ctx, "w6", 0.06, new THREE.Vector3(0, 1.14, -0.3), new THREE.Vector3(0, 1.32, -0.3), ctx.glass));
  // 液体：内腔盒随 fill 缩放。
  const liquid = new THREE.Mesh(
    acquireGeometry("chem:geo:res-liquid", () => new THREE.BoxGeometry(1.28, 1, 0.86)),
    acquireLiquidMaterial(ctx),
  );
  ctx.notes.noteGeo("chem:geo:res-liquid");
  liquid.renderOrder = 1;
  liquid.visible = false;
  liquid.name = "liquid";
  ctx.group.add(liquid);
  ctx.specialLiquid = (fill, colorId, cloudy) => {
    liquid.material = acquireLiquidMaterial(ctx, colorId, (cloudy ?? 0) > 0.4);
    if (fill > 0.02) {
      const h = 0.08 + 0.95 * Math.min(1, fill);
      liquid.scale.y = h;
      liquid.position.y = 0.09 + h / 2;
      liquid.visible = true;
    } else {
      liquid.visible = false;
    }
  };
}

function buildStopper(ctx: Ctx): void {
  const profile: ProfilePoint[] = [[0.24, 0], [0.235, 0.18], [0.2, 0.38], [0.165, 0.5], [0.155, 0.58], [0.16, 0.6]];
  addMesh(ctx, solidPart(ctx, "chem:geo:stopper", () => latheGeo(profile, 36), MAT.rubber));
  for (const side of [-1, 1] as const) {
    addMesh(ctx, tubeBetween(ctx, "g4", 0.05, new THREE.Vector3(side * 0.15, 0.08, 0), new THREE.Vector3(side * 0.15, 0.62, 0), ctx.glass));
    const collar = glassPart(ctx, "chem:geo:stopper-collar", () => new THREE.TorusGeometry(0.056, 0.014, 8, 16));
    collar.rotation.x = Math.PI / 2;
    collar.position.set(side * 0.15, 0.58, 0);
    addMesh(ctx, collar);
  }
}

// 三通/四通：水平管轴在 y=0.13（球体半径），底面触台，与规格端口坐标一致。
const CONNECTOR_AXIS_Y = 0.13;

function buildTee(ctx: Ctx): void {
  addMesh(ctx, glassPart(ctx, "chem:geo:tee-chamber", () => {
    const geo = new THREE.SphereGeometry(0.13, 24, 16);
    geo.translate(0, CONNECTOR_AXIS_Y, 0);
    return geo;
  }));
  const stubs: [THREE.Vector3, THREE.Vector3][] = [
    [new THREE.Vector3(-0.12, CONNECTOR_AXIS_Y, 0), new THREE.Vector3(-0.46, CONNECTOR_AXIS_Y, 0)],
    [new THREE.Vector3(0.12, CONNECTOR_AXIS_Y, 0), new THREE.Vector3(0.46, CONNECTOR_AXIS_Y, 0)],
    [new THREE.Vector3(0, CONNECTOR_AXIS_Y + 0.12, 0), new THREE.Vector3(0, CONNECTOR_AXIS_Y + 0.52, 0)],
  ];
  for (const [from, to] of stubs) addMesh(ctx, tubeBetween(ctx, "g5", 0.062, from, to, ctx.glass));
}

function buildCross(ctx: Ctx): void {
  addMesh(ctx, glassPart(ctx, "chem:geo:cross-chamber", () => {
    const geo = new THREE.SphereGeometry(0.13, 24, 16);
    geo.translate(0, CONNECTOR_AXIS_Y, 0);
    return geo;
  }));
  const stubs: [THREE.Vector3, THREE.Vector3][] = [
    [new THREE.Vector3(-0.12, CONNECTOR_AXIS_Y, 0), new THREE.Vector3(-0.46, CONNECTOR_AXIS_Y, 0)],
    [new THREE.Vector3(0.12, CONNECTOR_AXIS_Y, 0), new THREE.Vector3(0.46, CONNECTOR_AXIS_Y, 0)],
    [new THREE.Vector3(0, CONNECTOR_AXIS_Y + 0.12, 0), new THREE.Vector3(0, CONNECTOR_AXIS_Y + 0.52, 0)],
    [new THREE.Vector3(0, CONNECTOR_AXIS_Y, -0.12), new THREE.Vector3(0, CONNECTOR_AXIS_Y, -0.46)],
  ];
  for (const [from, to] of stubs) addMesh(ctx, tubeBetween(ctx, "g5", 0.062, from, to, ctx.glass));
}

function buildYMixer(ctx: Ctx): void {
  const chamber = glassPart(ctx, "chem:geo:ymix-chamber", () => new THREE.SphereGeometry(0.14, 24, 16));
  chamber.position.y = 0.3;
  addMesh(ctx, chamber);
  addMesh(ctx, tubeBetween(ctx, "g5", 0.058, new THREE.Vector3(-0.1, 0.44, 0), new THREE.Vector3(-0.29, 0.64, 0), ctx.glass));
  addMesh(ctx, tubeBetween(ctx, "g5", 0.058, new THREE.Vector3(0.1, 0.44, 0), new THREE.Vector3(0.29, 0.64, 0), ctx.glass));
  addMesh(ctx, tubeBetween(ctx, "g5", 0.058, new THREE.Vector3(0, 0.2, 0.05), new THREE.Vector3(0, 0.13, 0.26), ctx.glass));
}

function buildValve(ctx: Ctx): void {
  addMesh(ctx, tubeBetween(ctx, "g6", 0.085, new THREE.Vector3(-0.38, 0.1, 0), new THREE.Vector3(0.38, 0.1, 0), ctx.glass));
  const boss = solidPart(ctx, "chem:geo:valve-boss", () => new THREE.CylinderGeometry(0.115, 0.13, 0.18, 20), MAT.brass);
  boss.position.y = 0.14;
  addMesh(ctx, boss);
  const stem = solidPart(ctx, "chem:geo:valve-stem", () => new THREE.CylinderGeometry(0.042, 0.042, 0.3, 14), MAT.steel);
  stem.position.y = 0.28;
  addMesh(ctx, stem);
  // 手柄组：开启时与管轴平行（顺流），关闭时旋转 90° 横截。
  const handle = new THREE.Group();
  handle.name = "valve-handle";
  handle.position.y = 0.43;
  for (const side of [-1, 1] as const) {
    const blade = solidPart(ctx, "chem:geo:valve-blade", () => roundedBoxOf("chem:geo:valve-blade", 0.5, 0.1, 0.045, 0.02), MAT.brass);
    blade.position.x = side * 0.26;
    handle.add(blade);
  }
  const cap = solidPart(ctx, "chem:geo:valve-cap", () => new THREE.SphereGeometry(0.055, 16, 12), MAT.brass);
  handle.add(cap);
  ctx.group.add(handle);
  ctx.valveHandle = handle;
}

function buildStand(ctx: Ctx): void {
  const spec = getEquipmentSpec("stand");
  const baseH = spec?.visualTuning?.baseH ?? 0.16;
  const poleR = spec?.visualTuning?.poleR ?? 0.07;
  const rail = spec?.anchors.railBase ?? { x: -0.52, y: 0.22, z: -0.3 };
  // 低重心圆角铸铁底座 + 立杆（railBase→railTop）。
  const base = solidPart(ctx, "chem:geo:stand-base", () => roundedBoxOf("chem:geo:stand-base", 1.6, 0.95, baseH, 0.16), MAT.iron);
  base.position.y = baseH / 2;
  addMesh(ctx, base);
  const poleLen = 4.4 - rail.y;
  const pole = solidPart(ctx, "chem:geo:stand-pole", () => new THREE.CylinderGeometry(poleR, poleR, poleLen, 20), MAT.steel);
  pole.position.set(rail.x, rail.y + poleLen / 2, rail.z);
  addMesh(ctx, pole);
  const cap = solidPart(ctx, "chem:geo:stand-cap", () => new THREE.SphereGeometry(poleR + 0.008, 16, 12), MAT.steel);
  cap.position.set(rail.x, 4.4, rail.z);
  addMesh(ctx, cap);

  // 夹具组（默认高度 2.6；随挂载器材 clampPoint 高度联动）。
  const claw = new THREE.Group();
  claw.name = "claw";
  const gripX = rail.x + 0.75;
  const collar = solidPart(ctx, "chem:geo:claw-collar", () => new THREE.CylinderGeometry(0.105, 0.105, 0.22, 18), MAT.iron);
  collar.position.set(rail.x, 0, rail.z);
  claw.add(collar);
  const knob = solidPart(ctx, "chem:geo:claw-knob", () => new THREE.CylinderGeometry(0.055, 0.07, 0.13, 14), MAT.brass);
  knob.rotation.x = Math.PI / 2;
  knob.position.set(rail.x, 0, rail.z + 0.16);
  claw.add(knob);
  const arm = solidPart(ctx, "chem:geo:claw-arm", () => new THREE.CylinderGeometry(0.042, 0.042, 0.88, 12), MAT.steel);
  arm.rotation.z = Math.PI / 2;
  arm.position.set((rail.x + gripX) / 2, 0, rail.z);
  claw.add(arm);
  // 双爪：两段半环 + 内侧胶垫，环绕夹持点（gripX 处）。
  for (const side of [-1, 1] as const) {
    const finger = solidPart(ctx, "chem:geo:claw-finger", () => new THREE.TorusGeometry(0.16, 0.022, 8, 20, Math.PI * 1.15), MAT.steel);
    finger.position.set(gripX, 0, rail.z);
    finger.rotation.z = side < 0 ? Math.PI * -0.42 : Math.PI * 0.42;
    claw.add(finger);
    const pad = solidPart(ctx, "chem:geo:claw-pad", () => new THREE.BoxGeometry(0.05, 0.12, 0.06), MAT.rubber);
    pad.position.set(gripX + side * 0.13, 0, rail.z);
    claw.add(pad);
  }
  claw.position.y = 2.6;
  ctx.group.add(claw);
  ctx.clawGroup = claw;
}

function buildAlcoholLamp(ctx: Ctx): void {
  const bodyProfile: ProfilePoint[] = [
    [0.03, 0], [0.3, 0.02], [0.42, 0.12], [0.45, 0.3], [0.4, 0.48], [0.2, 0.56],
    [0.135, 0.62], [0.12, 0.74], [0.13, 0.8],
  ];
  const bodyMat = sharedMaterial(MAT.glassMilky);
  ctx.notes.noteMat(MAT.glassMilky);
  const body = new THREE.Mesh(
    acquireGeometry("chem:geo:lamp-body", () => latheGeo(bodyProfile, 48)),
    bodyMat,
  );
  ctx.notes.noteGeo("chem:geo:lamp-body");
  body.renderOrder = 2;
  addMesh(ctx, body);
  // 淡色“虚拟酒精”演示液面。
  ctx.liquidSpec = {
    bottom: 0.12,
    liquidTop: 0.4,
    innerR: (y) => 0.06 + 0.3 * Math.min(1, Math.max(0, (y - 0.08) / 0.34)),
  };
  const collar = solidPart(ctx, "chem:geo:lamp-collar", () => new THREE.CylinderGeometry(0.135, 0.145, 0.09, 18), MAT.brass);
  collar.position.y = 0.83;
  addMesh(ctx, collar);
  const holder = solidPart(ctx, "chem:geo:lamp-holder", () => new THREE.CylinderGeometry(0.085, 0.1, 0.12, 16), MAT.steel);
  holder.position.y = 0.92;
  addMesh(ctx, holder);
  const wick = solidPart(ctx, "chem:geo:lamp-wick", () => new THREE.CylinderGeometry(0.032, 0.032, 0.14, 10), MAT.wick);
  wick.position.y = 1.0;
  addMesh(ctx, wick);
  // 灯帽（备用熄灭罩）立在一侧，对应 cap 锚点。
  const capBody = new THREE.Mesh(
    acquireGeometry("chem:geo:lamp-cap", () => new THREE.CylinderGeometry(0.15, 0.15, 0.2, 18, 1, true)),
    bodyMat,
  );
  ctx.notes.noteGeo("chem:geo:lamp-cap");
  capBody.position.set(0.32, 0.88, 0);
  capBody.renderOrder = 2;
  addMesh(ctx, capBody);
  const capTop = solidPart(ctx, "chem:geo:lamp-captop", () => new THREE.CylinderGeometry(0.155, 0.15, 0.035, 18), MAT.brass);
  capTop.position.set(0.32, 0.99, 0);
  addMesh(ctx, capTop);
}

function buildHotPlate(ctx: Ctx): void {
  const body = solidPart(ctx, "chem:geo:plate-body", () => roundedBoxOf("chem:geo:plate-body", 1.2, 0.86, 0.24, 0.06), MAT.plasticDark);
  body.position.y = 0.12;
  addMesh(ctx, body);
  const top = solidPart(ctx, "chem:geo:plate-top", () => roundedBoxOf("chem:geo:plate-top", 1.14, 0.8, 0.05, 0.04), MAT.ceramic);
  top.position.y = 0.265;
  addMesh(ctx, top);
  const dial = solidPart(ctx, "chem:geo:plate-dial", () => new THREE.CylinderGeometry(0.09, 0.095, 0.06, 18), MAT.steel);
  dial.rotation.x = Math.PI / 2;
  dial.position.set(0.42, 0.2, 0.45);
  addMesh(ctx, dial);
  const indicator = solidPart(ctx, "chem:geo:plate-led", () => new THREE.SphereGeometry(0.028, 10, 8), MAT.led);
  indicator.position.set(-0.42, 0.22, 0.44);
  addMesh(ctx, indicator);
  ctx.ledMesh = indicator;
}

function buildPump(ctx: Ctx): void {
  const base = solidPart(ctx, "chem:geo:pump-base", () => roundedBoxOf("chem:geo:pump-base", 0.9, 0.66, 0.14, 0.05), MAT.plasticDark);
  base.position.y = 0.07;
  addMesh(ctx, base);
  const shell = solidPart(ctx, "chem:geo:pump-shell", () => roundedBoxOf("chem:geo:pump-shell", 0.8, 0.58, 0.34, 0.08), MAT.plastic);
  shell.position.y = 0.31;
  addMesh(ctx, shell);
  // 透明泵头 + 可见叶轮（特效层旋转）。
  const dome = glassPart(ctx, "chem:geo:pump-dome", () => new THREE.SphereGeometry(0.17, 22, 14, 0, Math.PI * 2, 0, Math.PI / 2));
  dome.position.set(0, 0.45, 0.1);
  addMesh(ctx, dome);
  const impeller = new THREE.Group();
  impeller.name = "impeller";
  impeller.position.set(0, 0.45, 0.1);
  for (let i = 0; i < 4; i++) {
    const blade = solidPart(ctx, "chem:geo:pump-blade", () => new THREE.BoxGeometry(0.11, 0.012, 0.035), MAT.plasticDark);
    blade.rotation.y = (Math.PI / 2) * i;
    blade.position.set(Math.cos((Math.PI / 2) * i) * 0.055, 0.02, Math.sin((Math.PI / 2) * i) * 0.055);
    impeller.add(blade);
  }
  ctx.group.add(impeller);
  for (const side of [-1, 1] as const) {
    const nozzle = solidPart(ctx, "chem:geo:pump-nozzle", () => new THREE.CylinderGeometry(0.05, 0.055, 0.16, 14), MAT.steel);
    nozzle.rotation.z = Math.PI / 2;
    nozzle.position.set(side * 0.48, 0.42, 0);
    addMesh(ctx, nozzle);
  }
}

const REAGENT_NAMES: Record<string, [string, string]> = {
  teal: ["演示液 A", "DEMO A"],
  sky: ["演示液 B", "DEMO B"],
  amber: ["琥珀演示液", "AMBER"],
  violet: ["紫雾演示液", "VIOLET"],
  magenta: ["彩雾微粒", "MIST"],
  pale: ["透明演示液", "CLEAR"],
};

function buildReagentBottle(ctx: Ctx): void {
  const profile: ProfilePoint[] = [
    [0.03, 0], [0.26, 0.02], [0.3, 0.16], [0.3, 0.82], [0.23, 0.95], [0.13, 1.02], [0.125, 1.18],
  ];
  addMesh(ctx, glassPart(ctx, "chem:geo:reagent-body", () => latheGeo(profile, 44)));
  ctx.liquidSpec = { bottom: 0.06, liquidTop: 0.92, innerR: () => 0.27 };
  const cap = solidPart(ctx, "chem:geo:reagent-cap", () => new THREE.CylinderGeometry(0.14, 0.14, 0.13, 18), MAT.plasticDark);
  cap.position.y = 1.24;
  addMesh(ctx, cap);
  const capTop = solidPart(ctx, "chem:geo:reagent-captop", () => new THREE.CylinderGeometry(0.175, 0.175, 0.045, 18), MAT.plasticDark);
  capTop.position.y = 1.32;
  addMesh(ctx, capTop);
  // 自创标签：色带 + 虚构演示短名（颜色即物料标识）。
  const colorId = getEquipmentSpec("reagent-bottle")?.defaultContents?.colorId ?? "teal";
  const labelMat = basicTextureMaterial(ctx, "chem:mat:reagent-label");
  const names = REAGENT_NAMES[colorId] ?? REAGENT_NAMES.teal!;
  labelMat.map = reagentLabelTexture(colorId, names[0], names[1]);
  labelMat.needsUpdate = true;
  ctx.notes.noteTexture(`chem:tex:reagent:${colorId}`);
  const label = new THREE.Mesh(
    acquireGeometry("chem:geo:reagent-label", () => new THREE.CylinderGeometry(0.308, 0.308, 0.5, 24, 1, true, -0.7, 1.4)),
    labelMat,
  );
  ctx.notes.noteGeo("chem:geo:reagent-label");
  label.position.y = 0.5;
  label.renderOrder = 3;
  addMesh(ctx, label);
}

const BUILDERS: Record<string, Builder> = {
  "beaker": buildBeaker,
  "graduated-cylinder": buildGraduatedCylinder,
  "flask-round": buildFlaskRound,
  "flask-three-neck": buildFlaskThreeNeck,
  "flask-erlenmeyer": buildFlaskErlenmeyer,
  "washing-bottle": buildWashingBottle,
  "condenser-coil": buildCondenserCoil,
  "condenser-straight": buildCondenserStraight,
  "settling-bottle": buildSettlingBottle,
  "u-tube": buildUTube,
  "gas-collecting-bottle": buildGasCollectingBottle,
  "observation-sphere": buildObservationSphere,
  "expansion-ball": buildExpansionBall,
  "reservoir": buildReservoir,
  "stopper-2port": buildStopper,
  "tee": buildTee,
  "cross": buildCross,
  "y-mixer": buildYMixer,
  "valve": buildValve,
  "stand": buildStand,
  "alcohol-lamp": buildAlcoholLamp,
  "hot-plate": buildHotPlate,
  "pump": buildPump,
  "reagent-bottle": buildReagentBottle,
};

// ── 工厂入口 ───────────────────────────────────────────────────────────────

export function createEquipmentModel(kind: string, opts: CreateModelOptions): EquipmentModelHandle | null {
  const spec = getEquipmentSpec(kind);
  const builder = BUILDERS[kind];
  if (!spec || !builder) return null;

  const hero = (opts.hero ?? HERO_KINDS.has(kind)) && opts.tier !== "low";
  const notes = new ResourceNotes();
  const glassMat = sharedMaterial(hero ? MAT.glassHero : MAT.glassPlain);
  notes.noteMat(hero ? MAT.glassHero : MAT.glassPlain);
  const ctx: Ctx = { group: new THREE.Group(), glass: glassMat, notes };
  ctx.group.name = `eq:${kind}`;

  builder(ctx);

  // 端口锚点节点：局部位置 + 朝向（quaternion 把局部 +Y 对准插接方向）。
  const portNodes = new Map<string, THREE.Object3D>();
  for (const p of spec.ports) {
    const node = new THREE.Object3D();
    node.name = `port:${p.id}`;
    node.position.copy(v3(p.localPosition));
    node.quaternion.setFromUnitVectors(UP, v3(p.localDirection).normalize());
    if (p.socketClass !== "liquid") {
      // 端口提示环外径 ≈ 1.09 × scale（环半径 1 + 管径 0.09）。底面原点器材的
      // 低端口把环外径钳进离台高度内，避免环下沿切进台面；中心原点器材
      // （卧式冷凝器，挂架/抬升半高）不做本地钳制。
      const ringScale = spec.boundsOrigin === "center"
        ? p.visualRadius * 1.5
        : Math.min(p.visualRadius * 1.5, Math.max(0.04, (p.localPosition.y - 0.03) / 1.09));
      node.add(portRingMesh(p.id, ringScale / 1.5));
      // portRingMesh uses the same ref-count registry as the rest of the model.
      ctx.notes.noteGeo(`chem:port-ring:${(ringScale / 1.5).toFixed(2)}`);
      ctx.notes.noteMat(MAT.portRing);
    }
    ctx.group.add(node);
    portNodes.set(p.id, node);
  }
  const anchorNodes = new Map<string, THREE.Object3D>();
  for (const [id, pos] of Object.entries(spec.anchors)) {
    const node = new THREE.Object3D();
    node.name = `anchor:${id}`;
    node.position.copy(v3(pos));
    ctx.group.add(node);
    anchorNodes.set(id, node);
  }

  // 拾取代理：按规格生成隐形命中体（与可视本体分离）。普通器材用
  // 紧缩包围盒；铁架台拆成底座 + 立杆，避免一整块空心盒子盖住后方的
  // 烧瓶/冷凝管。Raycaster 只看到这些代理，不会把透明玻璃当成遮挡层。
  const pickParts: THREE.Mesh[] = [];
  const pickMaterial = sharedMaterial(MAT.pickProxy);
  notes.noteMat(MAT.pickProxy);
  const addPick = (mesh: THREE.Mesh, name: string): void => {
    mesh.name = name;
    mesh.userData.pickRef = { kind: "equipment" };
    ctx.group.add(mesh);
    pickParts.push(mesh);
  };
  if (kind === "stand") {
    const baseH = spec.visualTuning?.baseH ?? 0.16;
    const rail = spec.anchors.railBase ?? { x: -0.52, y: 0.22, z: -0.3 };
    const baseKey = "chem:geo:pick:stand-base";
    const base = new THREE.Mesh(
      acquireGeometry(baseKey, () => new THREE.BoxGeometry(spec.bounds.width * 0.94, Math.max(0.22, baseH * 1.7), spec.bounds.depth * 0.94)),
      pickMaterial,
    );
    notes.noteGeo(baseKey);
    base.position.y = baseH * 0.85;
    addPick(base, "pick-base");
    const poleKey = "chem:geo:pick:stand-pole";
    const poleHeight = Math.max(0.8, (spec.anchors.railTop?.y ?? 4.4) - rail.y);
    const pole = new THREE.Mesh(
      acquireGeometry(poleKey, () => new THREE.CylinderGeometry(0.16, 0.16, poleHeight, 12)),
      pickMaterial,
    );
    notes.noteGeo(poleKey);
    pole.position.set(rail.x, rail.y + poleHeight / 2, rail.z);
    addPick(pole, "pick-pole");
  } else {
    const pickGeoKey = `chem:geo:pick:${kind}`;
    const { width, depth, height } = spec.bounds;
    const pick = new THREE.Mesh(
      acquireGeometry(pickGeoKey, () => new THREE.BoxGeometry(width * 0.86, height * 0.9, depth * 0.86)),
      pickMaterial,
    );
    notes.noteGeo(pickGeoKey);
    pick.position.y = equipmentCenterY(spec);
    addPick(pick, "pick-body");
  }

  const handle: EquipmentModelHandle = {
    kind,
    group: ctx.group,
    portNodes,
    anchorNodes,
    pickParts,
    update: (instance) => {
      const contents = instance.visualContents;
      if (ctx.specialLiquid) {
        ctx.specialLiquid(contents?.fill ?? 0, contents?.colorId, contents?.cloudiness);
      } else if (ctx.liquidSpec) {
        updateLiquidMesh(ctx, contents?.fill ?? 0, contents?.colorId, contents?.cloudiness);
      }
      if (ctx.valveHandle) {
        ctx.valveHandle.rotation.y = instance.controls.open ? 0 : Math.PI / 2;
      }
      if (ctx.ledMesh) {
        ctx.ledMesh.visible = Boolean(instance.controls.power);
      }
    },
    dispose: () => {
      if (ctx.ownedLiquid) ctx.ownedLiquid.geometry.dispose();
      notes.releaseAll();
    },
  };
  if (ctx.clawGroup) {
    const claw = ctx.clawGroup;
    handle.setClawHeight = (h: number) => {
      claw.position.y = Math.min(4.25, Math.max(0.5, h));
    };
    handle.getClawHeight = () => claw.position.y;
  }
  return handle;
}
