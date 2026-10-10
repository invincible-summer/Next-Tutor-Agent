"use client";

/**
 * 软管系统（chem-lab architecture/§4.3）：以端口 world anchor 为事实源生成确定性曲线——
 * 端点 + 沿端口法线的控制点 + 重力垂弧中点；静态期一次 TubeGeometry 精修。
 * 拖动期的低段数动态更新在交互层（Phase C）扩展，本层负责几何所有权与随动。
 */
import * as THREE from "three";
import { acquireGeometry, releaseGeometry, releaseMaterial, sharedMaterial, MAT } from "./EquipmentModels.ts";

export interface TubePortAnchor {
  position: THREE.Vector3;
  /** 插接方向（世界坐标，指向管子离开端口的方向）。 */
  direction: THREE.Vector3;
}

export interface TubeConnectionSpec {
  id: string;
  a: TubePortAnchor;
  b: TubePortAnchor;
  style: "tube" | "glass";
  slack: number;
}

const UP = new THREE.Vector3(0, 1, 0);

/** 软管采样点的最低高度：台面 Y=0 上方留出管径（0.072）与样条过冲余量，防止切进台面。 */
const TUBE_MIN_Y = 0.14;

/** 端点曲线：p0 出发沿法线抬出、p4 沿对向法线进入，中段受重力下垂。 */
export function tubeCurveFor(a: TubePortAnchor, b: TubePortAnchor, slack: number): THREE.CatmullRomCurve3 {
  const lead = 0.34 + slack * 0.3;
  const p0 = a.position.clone();
  const p1 = p0.clone().addScaledVector(a.direction, lead);
  const p4 = b.position.clone();
  const p3 = p4.clone().addScaledVector(b.direction, lead);
  const span = p1.distanceTo(p3);
  const sag = Math.min(0.5 + span * 0.07 + slack * 0.9, 1.8);
  const mid = p1.clone().add(p3).multiplyScalar(0.5);
  mid.y -= sag;
  // 低端口之间/下向端口（如沉降瓶下嘴）引出的控制点可能低于台面，且样条在
  // 控制点之间还会轻微过冲：重采样整条曲线并把每点钳到台面上方一个管径处，
  // 保证软管全程不穿台。端口 y 均 ≥ 0.1，钳位不会移动接管端点。
  const rough = new THREE.CatmullRomCurve3([p0, p1, mid, p3, p4], false, "catmullrom", 0.6);
  const pts = rough.getPoints(64);
  for (const pt of pts) pt.y = Math.max(TUBE_MIN_Y, pt.y);
  return new THREE.CatmullRomCurve3(pts, false, "catmullrom", 0.5);
}

interface TubeEntry {
  mesh: THREE.Mesh;
  sleeveA: THREE.Mesh;
  sleeveB: THREE.Mesh;
  curve: THREE.CatmullRomCurve3;
  radius: number;
}

export class TubeSystem {
  readonly group = new THREE.Group();
  private readonly tubes = new Map<string, TubeEntry>();
  private readonly matNotes = new Map<string, number>();
  private readonly geoNotes = new Map<string, number>();

  constructor() {
    this.group.name = "tubes";
  }

  /** 全量重建（AUTO/载入/删除走这里，数量 ≤80；器材拖动随动走 updateTube）。 */
  setConnections(specs: TubeConnectionSpec[]): void {
    const keep = new Set(specs.map((s) => s.id));
    for (const id of [...this.tubes.keys()]) {
      if (!keep.has(id)) this.removeConnection(id);
    }
    for (const spec of specs) {
      if (this.tubes.has(spec.id)) this.updateTube(spec.id, spec.a, spec.b, spec.slack);
      else this.addConnection(spec);
    }
  }

  addConnection(spec: TubeConnectionSpec): void {
    if (this.tubes.has(spec.id)) return;
    const glass = spec.style === "glass";
    const radius = glass ? 0.05 : 0.072;
    const curve = tubeCurveFor(spec.a, spec.b, spec.slack);
    const geometry = new THREE.TubeGeometry(curve, 36, radius, 10, false);
    const matKey = glass ? MAT.glassPlain : MAT.hose;
    const material = sharedMaterial(matKey);
    this.note(this.matNotes, matKey);
    const mesh = new THREE.Mesh(geometry, material);
    mesh.renderOrder = 2;
    mesh.name = `tube:${spec.id}`;
    mesh.userData.pickRef = { kind: "tube", connectionId: spec.id };
    // 端头连接套：可见的插接过渡（软管粗塑料套 / 玻璃接管细亮环）。
    const sleeveGeoKey = glass ? "chem:geo:tube-sleeve-glass" : "chem:geo:tube-sleeve";
    const sleeveMatKey = glass ? MAT.steel : MAT.plasticDark;
    const sleeveGeo = acquireGeometry(sleeveGeoKey, () => new THREE.CylinderGeometry(glass ? 0.068 : 0.095, glass ? 0.068 : 0.095, 0.22, 14, 1, true));
    this.note(this.geoNotes, sleeveGeoKey);
    const sleeveMat = sharedMaterial(sleeveMatKey);
    this.note(this.matNotes, sleeveMatKey);
    const sleeveA = new THREE.Mesh(sleeveGeo, sleeveMat);
    const sleeveB = new THREE.Mesh(sleeveGeo, sleeveMat);
    this.placeSleeve(sleeveA, spec.a);
    this.placeSleeve(sleeveB, spec.b);
    this.group.add(mesh, sleeveA, sleeveB);
    this.tubes.set(spec.id, { mesh, sleeveA, sleeveB, curve, radius });
  }

  /** 端点随动（器材拖动/挂载调整）：只重建该管几何并释放旧独有几何。
   *  拖动期 preview=true 用 16 段低成本更新，松手后以 36 段精修（chem-lab architecture）。 */
  updateTube(id: string, a: TubePortAnchor, b: TubePortAnchor, slack: number, preview = false): void {
    const entry = this.tubes.get(id);
    if (!entry) return;
    const curve = tubeCurveFor(a, b, slack);
    const geometry = new THREE.TubeGeometry(curve, preview ? 16 : 36, entry.radius, 10, false);
    entry.mesh.geometry.dispose();
    entry.mesh.geometry = geometry;
    entry.curve = curve;
    this.placeSleeve(entry.sleeveA, a);
    this.placeSleeve(entry.sleeveB, b);
  }

  removeConnection(id: string): void {
    const entry = this.tubes.get(id);
    if (!entry) return;
    this.group.remove(entry.mesh, entry.sleeveA, entry.sleeveB);
    entry.mesh.geometry.dispose();
    this.tubes.delete(id);
  }

  /** 管身 mesh（交互层拾取/高亮用）。 */
  tubeMeshes(): THREE.Mesh[] {
    return [...this.tubes.values()].map((entry) => entry.mesh);
  }

  /** 管路径采样点（流动粒子弧长采样用）。 */
  sampleTube(id: string, t: number, out: THREE.Vector3): boolean {
    const entry = this.tubes.get(id);
    if (!entry) return false;
    entry.curve.getPoint(t, out);
    return true;
  }

  private placeSleeve(sleeve: THREE.Mesh, anchor: TubePortAnchor): void {
    sleeve.position.copy(anchor.position).addScaledVector(anchor.direction, 0.12);
    sleeve.quaternion.setFromUnitVectors(UP, anchor.direction.clone().normalize());
  }

  private note(map: Map<string, number>, key: string): void {
    map.set(key, (map.get(key) ?? 0) + 1);
  }

  dispose(): void {
    for (const id of [...this.tubes.keys()]) this.removeConnection(id);
    for (const [key, n] of this.geoNotes) for (let i = 0; i < n; i++) releaseGeometry(key);
    for (const [key, n] of this.matNotes) for (let i = 0; i < n; i++) releaseMaterial(key);
    this.geoNotes.clear();
    this.matNotes.clear();
    this.group.removeFromParent();
  }
}
