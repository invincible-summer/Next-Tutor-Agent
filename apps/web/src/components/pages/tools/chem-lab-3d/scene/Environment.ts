"use client";

/**
 * 实验室舞台（plan §3.2）：有厚度的暖灰陶瓷台面 + 柜体、后方三层器材架（真实
 * 3D 小物件）、浅雾灰墙面/地面、暖主光 + 冷边光 + 环境光。亮/暗主题只调整
 * 灯光基准与背景色，不改 design token。静态资源在实例内创建并随 dispose 释放。
 */
import * as THREE from "three";
import { sharedMaterial, MAT, releaseGeometry, roundedBoxOf } from "./EquipmentModels.ts";
import type { QualityTier } from "./SceneController.ts";

interface ThemeConfig {
  background: number;
  fogNear: number;
  fogFar: number;
  keyIntensity: number;
  keyColor: number;
  hemiIntensity: number;
  hemiSky: number;
  hemiGround: number;
  rimIntensity: number;
  rimColor: number;
  ledIntensity: number;
  wallColor: number;
  floorColor: number;
  benchColor: number;
}

const THEMES: Record<"light" | "dark", ThemeConfig> = {
  light: {
    background: 0xdfe3e0, fogNear: 22, fogFar: 72,
    keyIntensity: 1.7, keyColor: 0xffe9cf,
    hemiIntensity: 0.55, hemiSky: 0xfff6ea, hemiGround: 0x8b8578,
    rimIntensity: 0.5, rimColor: 0xcfe4ea,
    ledIntensity: 0.7,
    wallColor: 0xd3d7d5, floorColor: 0x8f8a80, benchColor: 0xada69a,
  },
  dark: {
    background: 0x141a20, fogNear: 18, fogFar: 60,
    keyIntensity: 1.25, keyColor: 0xffdfc0,
    hemiIntensity: 0.24, hemiSky: 0xcfd8e2, hemiGround: 0x3a3d42,
    rimIntensity: 0.42, rimColor: 0x9fc4d4,
    ledIntensity: 1.4,
    wallColor: 0x272d34, floorColor: 0x22262b, benchColor: 0x6e6963,
  },
};

/** 确定性小伪随机（架上物件布局稳定，不闪烁）。 */
function seeded(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 0x100000000;
  };
}

export class LabEnvironment {
  readonly group: THREE.Group;
  private readonly tier: QualityTier;
  private readonly ownedGeometries: THREE.BufferGeometry[] = [];
  private readonly ownedMaterials: THREE.Material[] = [];
  private readonly key: THREE.DirectionalLight;
  private readonly hemi: THREE.HemisphereLight;
  private readonly rim: THREE.DirectionalLight;
  private readonly ledMaterials: THREE.MeshStandardMaterial[] = [];
  private readonly wallMaterial: THREE.MeshStandardMaterial;
  private readonly floorMaterial: THREE.MeshStandardMaterial;
  private readonly benchMaterial: THREE.MeshStandardMaterial;
  private scene: THREE.Scene | null = null;
  private pendingBackground: THREE.Color | null = null;
  private pendingFog: THREE.Fog | null = null;
  private readonly notes: string[] = [];

  constructor(tier: QualityTier) {
    this.tier = tier;
    this.group = new THREE.Group();
    this.group.name = "lab-environment";

    const mkMat = (params: THREE.MeshStandardMaterialParameters): THREE.MeshStandardMaterial => {
      const mat = new THREE.MeshStandardMaterial(params);
      this.ownedMaterials.push(mat);
      return mat;
    };
    const mkGeo = <T extends THREE.BufferGeometry>(geo: T): T => {
      this.ownedGeometries.push(geo);
      return geo;
    };

    // ── 台面（0.45 厚、圆角、金属包边、防滑条）───────────────────────────
    this.benchMaterial = mkMat({ color: 0xb9b3a8, roughness: 0.5, metalness: 0.04 });
    const benchTop = new THREE.Mesh(roundedBoxOf("env:bench-top", 17, 9, 0.45, 0.24), this.benchMaterial);
    this.notes.push("env:bench-top");
    benchTop.position.y = -0.225;
    benchTop.receiveShadow = true;
    this.group.add(benchTop);
    // 前沿金属包边
    const trimMat = sharedMaterial(MAT.steel);
    this.notes.push(MAT.steel);
    const trim = new THREE.Mesh(mkGeo(new THREE.BoxGeometry(17, 0.07, 0.08)), trimMat);
    trim.position.set(0, -0.1, 4.5);
    this.group.add(trim);
    // 墨绿金属防滑条（台前区，克制的两条）
    const stripMat = mkMat({ color: 0x2f4a44, roughness: 0.7, metalness: 0.35 });
    for (const z of [3.15, 3.55]) {
      const strip = new THREE.Mesh(mkGeo(new THREE.BoxGeometry(15.6, 0.018, 0.1)), stripMat);
      strip.position.set(0, 0.012, z);
      strip.receiveShadow = true;
      this.group.add(strip);
    }

    // ── 柜体（台面下方：门板、把手、脚轮的局部可见）──────────────────────
    const cabinetMat = mkMat({ color: 0x77716a, roughness: 0.62, metalness: 0.06 });
    const cabinet = new THREE.Mesh(roundedBoxOf("env:cabinet", 16.2, 8.2, 2.3, 0.08), cabinetMat);
    this.notes.push("env:cabinet");
    cabinet.position.y = -0.45 - 1.15;
    cabinet.receiveShadow = true;
    cabinet.castShadow = true;
    this.group.add(cabinet);
    const doorMat = mkMat({ color: 0x6b665f, roughness: 0.66, metalness: 0.05 });
    const handleGeo = mkGeo(new THREE.CylinderGeometry(0.028, 0.028, 0.34, 10));
    handleGeo.rotateZ(Math.PI / 2);
    for (let i = 0; i < 4; i++) {
      const door = new THREE.Mesh(mkGeo(new THREE.BoxGeometry(3.7, 1.9, 0.05)), doorMat);
      door.position.set(-6.05 + i * 4.03, -1.6, 4.14);
      this.group.add(door);
      const handle = new THREE.Mesh(handleGeo, trimMat);
      handle.position.set(-6.05 + i * 4.03 + (i < 2 ? 1.5 : -1.5), -1.35, 4.2);
      this.group.add(handle);
    }
    const casterGeo = mkGeo(new THREE.CylinderGeometry(0.09, 0.09, 0.12, 12));
    for (const x of [-7.6, -2.6, 2.6, 7.6]) {
      for (const z of [-3.4, 3.4]) {
        const caster = new THREE.Mesh(casterGeo, trimMat);
        caster.position.set(x, -2.9, z);
        this.group.add(caster);
      }
    }

    // ── 地面与墙面（浅雾灰；不搭建完整房间）──────────────────────────────
    this.floorMaterial = mkMat({ color: 0x8f8a80, roughness: 0.94, metalness: 0 });
    const floor = new THREE.Mesh(mkGeo(new THREE.PlaneGeometry(70, 46)), this.floorMaterial);
    floor.rotation.x = -Math.PI / 2;
    floor.position.y = -2.96;
    floor.receiveShadow = true;
    this.group.add(floor);
    this.wallMaterial = mkMat({ color: 0xd3d7d5, roughness: 0.96, metalness: 0 });
    const wall = new THREE.Mesh(mkGeo(new THREE.PlaneGeometry(70, 26)), this.wallMaterial);
    wall.position.set(0, 6, -9);
    wall.receiveShadow = true;
    this.group.add(wall);
    // 模糊的非互动浅色储物柜剪影 + 高处软光板
    const lockerMat = mkMat({ color: 0xb9bebc, roughness: 0.9, metalness: 0.05 });
    const seamGeo = mkGeo(new THREE.BoxGeometry(0.06, 7.5, 0.06));
    const lockerHandleGeo = mkGeo(new THREE.CylinderGeometry(0.035, 0.035, 0.5, 10));
    for (const x of [-6.2, 6.2]) {
      const locker = new THREE.Mesh(mkGeo(new THREE.BoxGeometry(5.6, 7.5, 0.4)), lockerMat);
      locker.position.set(x, 3.2, -8.7);
      this.group.add(locker);
      // 双开门缝与竖把手，让剪影有“柜”的识别度。
      const seam = new THREE.Mesh(seamGeo, lockerMat);
      seam.position.set(x, 3.2, -8.49);
      this.group.add(seam);
      for (const side of [-1, 1]) {
        const handle = new THREE.Mesh(lockerHandleGeo, trimMat);
        handle.position.set(x + side * 0.5, 3.2, -8.46);
        this.group.add(handle);
      }
    }
    const panelMat = mkMat({ color: 0xf2f5f4, emissive: 0xeef3f2, emissiveIntensity: 0.55, roughness: 1 });
    for (const x of [-4.5, 4.5]) {
      const panel = new THREE.Mesh(mkGeo(new THREE.PlaneGeometry(3.4, 1.7)), panelMat);
      panel.position.set(x, 7.6, -8.9);
      this.group.add(panel);
    }

    this.buildShelfRack(mkGeo, mkMat, trimMat);

    // ── 灯光 ─────────────────────────────────────────────────────────────
    this.hemi = new THREE.HemisphereLight(0xfff6ea, 0x8b8578, 0.55);
    this.group.add(this.hemi);
    this.key = new THREE.DirectionalLight(0xffe9cf, 1.55);
    this.key.position.set(7, 13, 6);
    this.key.castShadow = tier !== "low";
    const shadowCam = this.key.shadow.camera;
    shadowCam.left = -12; shadowCam.right = 12;
    shadowCam.top = 10; shadowCam.bottom = -6;
    shadowCam.near = 2; shadowCam.far = 40;
    const mapSize = tier === "high" ? 2048 : 1024;
    this.key.shadow.mapSize.set(mapSize, mapSize);
    this.key.shadow.bias = -0.0008;
    this.group.add(this.key, this.key.target);
    this.key.target.position.set(0, 0, -0.8);
    this.rim = new THREE.DirectionalLight(0xcfe4ea, 0.5);
    this.rim.position.set(-9, 7, -7);
    this.group.add(this.rim);

    this.applyTheme(false);
  }

  /** 三层器材架：拉丝铝立柱、浅木层板、灯带与真实 3D 小物件（可观赏，Phase C 挂热点）。 */
  private buildShelfRack(
    mkGeo: <T extends THREE.BufferGeometry>(geo: T) => T,
    mkMat: (params: THREE.MeshStandardMaterialParameters) => THREE.MeshStandardMaterial,
    trimMat: THREE.Material,
  ): void {
    const rackZ = -5.7;
    const columnMat = sharedMaterial(MAT.steel);
    this.notes.push(MAT.steel);
    const columnGeo = mkGeo(new THREE.CylinderGeometry(0.06, 0.06, 6.4, 12));
    for (const x of [-7.1, -2.5, 2.5, 7.1]) {
      for (const dz of [-0.5, 0.5]) {
        const column = new THREE.Mesh(columnGeo, columnMat);
        column.position.set(x, 3.2, rackZ + dz);
        column.castShadow = true;
        this.group.add(column);
      }
    }
    const boardMat = mkMat({ color: 0xa8916f, roughness: 0.68, metalness: 0 });
    const boardGeo = mkGeo(new THREE.BoxGeometry(14.6, 0.09, 1.15));
    const shelfYs = [1.35, 2.95, 4.55];
    for (const y of shelfYs) {
      const board = new THREE.Mesh(boardGeo, boardMat);
      board.position.set(0, y, rackZ);
      board.castShadow = true;
      board.receiveShadow = true;
      this.group.add(board);
      // 层板下沿灯带
      const ledMat = mkMat({ color: 0xf6f2e8, emissive: 0xf3ead6, emissiveIntensity: 0.7, roughness: 1 });
      this.ledMaterials.push(ledMat);
      const led = new THREE.Mesh(mkGeo(new THREE.BoxGeometry(14.2, 0.025, 0.03)), ledMat);
      led.position.set(0, y - 0.07, rackZ + 0.56);
      this.group.add(led);
    }
    this.populateShelves(mkGeo, mkMat, trimMat, rackZ, shelfYs);
  }

  /** 架上陈列：小试剂瓶、试管格栅、软管卷、接头盒、备用烧瓶（确定性布局）。 */
  private populateShelves(
    mkGeo: <T extends THREE.BufferGeometry>(geo: T) => T,
    mkMat: (params: THREE.MeshStandardMaterialParameters) => THREE.MeshStandardMaterial,
    _trimMat: THREE.Material,
    rackZ: number,
    shelfYs: number[],
  ): void {
    const glassMat = sharedMaterial(MAT.glassPlain);
    this.notes.push(MAT.glassPlain);
    const rand = seeded(20261010);
    const top = (y: number) => y + 0.045;

    // 小试剂瓶排（一、二层左段）。
    const bottleGeo = mkGeo(new THREE.CylinderGeometry(0.19, 0.21, 0.92, 14));
    const capGeo = mkGeo(new THREE.CylinderGeometry(0.115, 0.115, 0.1, 12));
    const bandColors = [0x2fa39a, 0x58a8dd, 0xd6983c, 0x8f6fc8, 0xc85a9e];
    const bandGeo = mkGeo(new THREE.CylinderGeometry(0.216, 0.216, 0.1, 14));
    for (const level of [0, 1]) {
      for (let i = 0; i < 5; i++) {
        const x = -6.7 + i * 0.56 + level * 2.1;
        const bottle = new THREE.Mesh(bottleGeo, glassMat);
        bottle.position.set(x, top(shelfYs[level]!) + 0.46, rackZ - 0.12);
        bottle.renderOrder = 2;
        this.group.add(bottle);
        const cap = new THREE.Mesh(capGeo, sharedMaterial(MAT.plasticDark));
        cap.position.set(x, top(shelfYs[level]!) + 0.97, rackZ - 0.12);
        this.group.add(cap);
        const band = new THREE.Mesh(bandGeo, mkMat({ color: bandColors[(i + level) % bandColors.length]!, roughness: 0.5 }));
        band.position.set(x, top(shelfYs[level]!) + 0.2, rackZ - 0.12);
        this.group.add(band);
      }
    }
    // 试管格栅（一层右段）。
    const rackBox = new THREE.Mesh(mkGeo(new THREE.BoxGeometry(1.5, 0.12, 0.55)), sharedMaterial(MAT.oak));
    this.notes.push(MAT.oak);
    rackBox.position.set(4.6, top(shelfYs[0]!) + 0.05, rackZ + 0.05);
    this.group.add(rackBox);
    const tubeGeo = mkGeo(new THREE.CylinderGeometry(0.055, 0.055, 1.1, 10));
    for (let i = 0; i < 6; i++) {
      const tube = new THREE.Mesh(tubeGeo, glassMat);
      tube.position.set(4.1 + (i % 3) * 0.5, top(shelfYs[0]!) + 0.65, rackZ + (i < 3 ? -0.08 : 0.14));
      tube.renderOrder = 2;
      this.group.add(tube);
    }
    // 软管卷（二层右段）：三圈堆叠的半透明卷。
    const hoseMat = sharedMaterial(MAT.hose);
    this.notes.push(MAT.hose);
    const coilGeo = mkGeo(new THREE.TorusGeometry(0.42, 0.085, 10, 30));
    for (let i = 0; i < 3; i++) {
      const coil = new THREE.Mesh(coilGeo, hoseMat);
      coil.rotation.x = Math.PI / 2;
      coil.position.set(4.9 + rand() * 0.15, top(shelfYs[1]!) + 0.1 + i * 0.19, rackZ);
      coil.renderOrder = 2;
      this.group.add(coil);
    }
    // 接头盒（三层左段）+ 备用圆底烧瓶（三层中段）。
    const tray = new THREE.Mesh(mkGeo(new THREE.BoxGeometry(1.3, 0.07, 0.7)), sharedMaterial(MAT.plastic));
    this.notes.push(MAT.plastic);
    tray.position.set(-6.2, top(shelfYs[2]!) + 0.035, rackZ + 0.05);
    this.group.add(tray);
    const stubGeo = mkGeo(new THREE.CylinderGeometry(0.05, 0.05, 0.16, 10));
    for (let i = 0; i < 5; i++) {
      const stub = new THREE.Mesh(stubGeo, sharedMaterial(MAT.brass));
      this.notes.push(MAT.brass);
      stub.position.set(-6.6 + i * 0.22, top(shelfYs[2]!) + 0.15, rackZ + (i % 2 === 0 ? -0.1 : 0.12));
      this.group.add(stub);
    }
    const bulbGeo = mkGeo(new THREE.SphereGeometry(0.34, 20, 14));
    const neckGeo = mkGeo(new THREE.CylinderGeometry(0.09, 0.12, 0.34, 14));
    for (const x of [-1.4, 0.6]) {
      const bulb = new THREE.Mesh(bulbGeo, glassMat);
      bulb.position.set(x, top(shelfYs[2]!) + 0.34, rackZ - 0.05);
      bulb.renderOrder = 2;
      this.group.add(bulb);
      const neck = new THREE.Mesh(neckGeo, glassMat);
      neck.position.set(x, top(shelfYs[2]!) + 0.64, rackZ - 0.05);
      neck.renderOrder = 2;
      this.group.add(neck);
    }
  }

  applyTheme(dark: boolean): void {
    const theme = dark ? THEMES.dark : THEMES.light;
    const bg = new THREE.Color(theme.background);
    if (this.scene) this.scene.background = bg;
    else this.pendingBackground = bg;
    if (this.scene) this.scene.fog = new THREE.Fog(theme.background, theme.fogNear, theme.fogFar);
    else this.pendingFog = new THREE.Fog(theme.background, theme.fogNear, theme.fogFar);
    this.key.intensity = theme.keyIntensity;
    this.key.color.setHex(theme.keyColor);
    this.hemi.intensity = theme.hemiIntensity;
    this.hemi.color.setHex(theme.hemiSky);
    this.hemi.groundColor.setHex(theme.hemiGround);
    this.rim.intensity = theme.rimIntensity;
    this.rim.color.setHex(theme.rimColor);
    this.wallMaterial.color.setHex(theme.wallColor);
    this.floorMaterial.color.setHex(theme.floorColor);
    this.benchMaterial.color.setHex(theme.benchColor);
    for (const led of this.ledMaterials) led.emissiveIntensity = theme.ledIntensity;
  }

  /** 挂载到场景后调用（背景/雾属于 Scene，由环境统一配置）。 */
  attach(scene: THREE.Scene): void {
    this.scene = scene;
    scene.add(this.group);
    if (this.pendingBackground) {
      scene.background = this.pendingBackground;
      this.pendingBackground = null;
    }
    if (this.pendingFog) {
      scene.fog = this.pendingFog;
      this.pendingFog = null;
    } else {
      const theme = THEMES.light;
      scene.fog = new THREE.Fog(theme.background, theme.fogNear, theme.fogFar);
    }
  }

  dispose(): void {
    this.group.removeFromParent();
    for (const key of this.notes) releaseGeometry(key);
    for (const geo of this.ownedGeometries) geo.dispose();
    for (const mat of this.ownedMaterials) mat.dispose();
  }
}
