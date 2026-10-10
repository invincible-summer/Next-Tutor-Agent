"use client";

/**
 * 化学实验台共享模型资源层（plan §3.1/§3.4）：ref-count 几何/材质/程序贴图缓存、
 * 统一玻璃/金属/液体材质库、旋转体与接管等通用构件。全部资源程序化生成（原创），
 * 不加载任何外部模型/贴图/HDR；尺寸事实源是 domain equipment.ts 的规格表。
 */
import * as THREE from "three";

// ── ref-count 资源注册表 ────────────────────────────────────────────────────
// 同 kind 器材共享 immutable geometry/material；引用归零时释放 GL 资源，
// 保证反复取用/删除器材不泄漏、不误删仍被共享的资源。

interface RefCounted<T> { value: T; refs: number }

const geometryCache = new Map<string, RefCounted<THREE.BufferGeometry>>();
const materialCache = new Map<string, RefCounted<THREE.Material>>();
const textureCache = new Map<string, RefCounted<THREE.Texture>>();

export function acquireGeometry(key: string, build: () => THREE.BufferGeometry): THREE.BufferGeometry {
  let entry = geometryCache.get(key);
  if (!entry) {
    entry = { value: build(), refs: 0 };
    geometryCache.set(key, entry);
  }
  entry.refs += 1;
  return entry.value;
}

export function releaseGeometry(key: string): void {
  const entry = geometryCache.get(key);
  if (!entry) return;
  entry.refs -= 1;
  if (entry.refs <= 0) {
    entry.value.dispose();
    geometryCache.delete(key);
  }
}

export function acquireMaterial(key: string, build: () => THREE.Material): THREE.Material {
  let entry = materialCache.get(key);
  if (!entry) {
    entry = { value: build(), refs: 0 };
    materialCache.set(key, entry);
  }
  entry.refs += 1;
  return entry.value;
}

export function releaseMaterial(key: string): void {
  const entry = materialCache.get(key);
  if (!entry) return;
  entry.refs -= 1;
  if (entry.refs <= 0) {
    entry.value.dispose();
    materialCache.delete(key);
  }
}

/** 程序自绘贴图（刻度/标签），缓存并计数；源码即资产（ASSETS.md 登记口径）。 */
export function acquireTexture(key: string, build: () => THREE.Texture): THREE.Texture {
  let entry = textureCache.get(key);
  if (!entry) {
    entry = { value: build(), refs: 0 };
    textureCache.set(key, entry);
  }
  entry.refs += 1;
  return entry.value;
}

export function releaseTexture(key: string): void {
  const entry = textureCache.get(key);
  if (!entry) return;
  entry.refs -= 1;
  if (entry.refs <= 0) {
    entry.value.dispose();
    textureCache.delete(key);
  }
}

export function disposeAllModelCaches(): void {
  for (const entry of geometryCache.values()) entry.value.dispose();
  geometryCache.clear();
  for (const entry of materialCache.values()) entry.value.dispose();
  materialCache.clear();
  for (const entry of textureCache.values()) entry.value.dispose();
  textureCache.clear();
}

// ── 材质键（全部经 acquireMaterial 使用）────────────────────────────────────

export const MAT = {
  glassHero: "chem:glass-hero",       // 近景主体玻璃：transmission 折射
  glassPlain: "chem:glass-plain",     // 远景/多件玻璃：双层透明近似
  glassMilky: "chem:glass-milky",     // 酒精灯乳白玻璃
  iron: "chem:metal-iron",
  steel: "chem:metal-steel",
  brass: "chem:metal-brass",
  rubber: "chem:rubber-dark",
  plastic: "chem:plastic-mist",
  plasticDark: "chem:plastic-dark",
  ceramic: "chem:ceramic-cream",
  oak: "chem:wood-oak",
  hose: "chem:hose",
  wick: "chem:fabric-wick",
  portRing: "chem:port-ring",
  pickProxy: "chem:pick-proxy",
  led: "chem:led",
} as const;

function buildGlassHero(): THREE.Material {
  const mat = new THREE.MeshPhysicalMaterial({
    color: 0xf4f9f7,
    metalness: 0,
    roughness: 0.05,
    transmission: 0.93,
    thickness: 0.2,
    ior: 1.45,
    clearcoat: 0.5,
    clearcoatRoughness: 0.22,
    specularIntensity: 1,
    envMapIntensity: 0.65,
    side: THREE.DoubleSide,
  });
  return mat;
}

function buildGlassPlain(): THREE.Material {
  const mat = new THREE.MeshPhysicalMaterial({
    color: 0xeef4f2,
    metalness: 0,
    roughness: 0.1,
    transparent: true,
    opacity: 0.34,
    clearcoat: 0.35,
    clearcoatRoughness: 0.25,
    depthWrite: false,
    side: THREE.DoubleSide,
  });
  return mat;
}

const MATERIAL_BUILDERS: Record<string, () => THREE.Material> = {
  [MAT.glassHero]: buildGlassHero,
  [MAT.glassPlain]: buildGlassPlain,
  [MAT.glassMilky]: () => new THREE.MeshPhysicalMaterial({
    color: 0xfdf3e2, roughness: 0.38, metalness: 0, transparent: true, opacity: 0.85,
    clearcoat: 0.2, side: THREE.DoubleSide,
  }),
  [MAT.iron]: () => new THREE.MeshStandardMaterial({ color: 0x343a40, metalness: 0.78, roughness: 0.38 }),
  [MAT.steel]: () => new THREE.MeshStandardMaterial({ color: 0x9aa2ab, metalness: 0.82, roughness: 0.3 }),
  [MAT.brass]: () => new THREE.MeshStandardMaterial({ color: 0xc8a04c, metalness: 0.85, roughness: 0.32 }),
  [MAT.rubber]: () => new THREE.MeshStandardMaterial({ color: 0x2b2a28, roughness: 0.92, metalness: 0 }),
  [MAT.plastic]: () => new THREE.MeshStandardMaterial({ color: 0xc7cbce, roughness: 0.5, metalness: 0.05 }),
  [MAT.plasticDark]: () => new THREE.MeshStandardMaterial({ color: 0x3c4147, roughness: 0.55, metalness: 0.1 }),
  [MAT.ceramic]: () => new THREE.MeshStandardMaterial({ color: 0xb9b3a7, roughness: 0.42, metalness: 0.02 }),
  [MAT.oak]: () => new THREE.MeshStandardMaterial({ color: 0xa8916f, roughness: 0.68, metalness: 0 }),
  [MAT.hose]: () => new THREE.MeshPhysicalMaterial({
    color: 0xa8c4cc, roughness: 0.28, metalness: 0, transparent: true, opacity: 0.62,
    clearcoat: 0.3, depthWrite: false, side: THREE.DoubleSide,
  }),
  [MAT.wick]: () => new THREE.MeshStandardMaterial({ color: 0x4a3f33, roughness: 0.95, metalness: 0 }),
  [MAT.portRing]: () => new THREE.MeshBasicMaterial({ color: 0x6fc5b6, transparent: true, opacity: 0.55 }),
  [MAT.pickProxy]: () => new THREE.MeshBasicMaterial({ visible: false }),
  [MAT.led]: () => new THREE.MeshStandardMaterial({ color: 0x2fa39a, emissive: 0x2fa39a, emissiveIntensity: 0.9, roughness: 0.4 }),
};

export function sharedMaterial(key: string): THREE.Material {
  return acquireMaterial(key, MATERIAL_BUILDERS[key] ?? (() => new THREE.MeshStandardMaterial({ color: 0x888888 })));
}

// ── 演示液色库（虚构物料色，非真实试剂）────────────────────────────────────

export const LIQUID_COLORS: Record<string, number> = {
  teal: 0x2fa39a,
  sky: 0x58a8dd,
  amber: 0xd6983c,
  violet: 0x8f6fc8,
  magenta: 0xc85a9e,
  pale: 0xd9e9e3,
};

export function liquidColorHex(colorId?: string): number {
  if (colorId && LIQUID_COLORS[colorId] !== undefined) return LIQUID_COLORS[colorId]!;
  return LIQUID_COLORS.pale!;
}

/** 液体材质按色缓存（透明、轻高光；cloudiness 通过 opacity/roughness 微调）。 */
export function liquidMaterial(colorId?: string, cloudy = false): THREE.Material {
  const hex = liquidColorHex(colorId);
  const key = `chem:liquid:${colorId ?? "pale"}:${cloudy ? "c" : "n"}`;
  return acquireMaterial(key, () => new THREE.MeshPhysicalMaterial({
    color: hex,
    roughness: cloudy ? 0.55 : 0.14,
    metalness: 0,
    transparent: true,
    opacity: cloudy ? 0.62 : 0.82,
    clearcoat: 0.3,
    depthWrite: false,
  }));
}

// ── 通用构件 ───────────────────────────────────────────────────────────────

export type ProfilePoint = [radius: number, y: number];

/** 旋转体：profile 从底到口沿；与器材规格端口坐标同一局部系（原点=底面中心）。 */
export function latheOf(key: string, profile: ProfilePoint[], segments = 56): THREE.BufferGeometry {
  return acquireGeometry(key, () => new THREE.LatheGeometry(profile.map(([r, y]) => new THREE.Vector2(r, y)), segments));
}

/** 圆角矩形拉伸体（台面/底座/机壳用），中心在原点、高度沿 Y。 */
export function roundedBoxOf(key: string, width: number, depth: number, height: number, radius: number): THREE.BufferGeometry {
  return acquireGeometry(key, () => {
    const shape = new THREE.Shape();
    const w = width / 2 - radius;
    const d = depth / 2 - radius;
    shape.moveTo(-w - radius, -d);
    shape.lineTo(w, -d);
    shape.quadraticCurveTo(w + radius, -d, w + radius, -d + radius);
    shape.lineTo(w + radius, d);
    shape.quadraticCurveTo(w + radius, d + radius, w, d + radius);
    shape.lineTo(-w, d + radius);
    shape.quadraticCurveTo(-w - radius, d + radius, -w - radius, d);
    shape.lineTo(-w - radius, -d + radius);
    shape.quadraticCurveTo(-w - radius, -d, -w, -d);
    const geo = new THREE.ExtrudeGeometry(shape, { depth: height, bevelEnabled: true, bevelThickness: height * 0.18, bevelSize: radius * 0.35, bevelSegments: 2, curveSegments: 6 });
    geo.rotateX(-Math.PI / 2);
    geo.translate(0, height / 2, 0); // Extrude 沿 +Z 深度 → 旋转后居中于原点
    return geo;
  });
}

/** 直玻璃接管（开放圆柱 + 口沿小卷边）。 */
export function glassTubeOf(key: string, radius: number, length: number): THREE.BufferGeometry {
  return acquireGeometry(key, () => {
    const geo = new THREE.CylinderGeometry(radius, radius, length, 24, 1, true);
    return geo;
  });
}

/** 端口提示环：小、克制，仅标记“这里可以插管”（不渲染在 liquid 口沿）。 */
export function portRingMesh(portId: string, visualRadius: number): THREE.Mesh {
  const geo = acquireGeometry(`chem:port-ring:${visualRadius.toFixed(2)}`,
    () => new THREE.TorusGeometry(1, 0.09, 8, 24));
  const mesh = new THREE.Mesh(geo, sharedMaterial(MAT.portRing));
  mesh.scale.setScalar(visualRadius * 1.5);
  mesh.name = `port-ring:${portId}`;
  mesh.renderOrder = 3;
  return mesh;
}

/**
 * 程序绘制的刻度/标签贴图。绘制逻辑即“资产来源”，不依赖任何图像文件。
 * 内容是演示性刻度（非科学测量承诺）与自创短名。
 */
export function scaleTexture(key: string, draw: (ctx: CanvasRenderingContext2D, w: number, h: number) => void): THREE.CanvasTexture {
  return acquireTexture(key, () => {
    const canvas = document.createElement("canvas");
    canvas.width = 128;
    canvas.height = 512;
    const ctx = canvas.getContext("2d");
    if (ctx) draw(ctx, canvas.width, canvas.height);
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.anisotropy = 4;
    return texture;
  }) as THREE.CanvasTexture;
}

/** 量筒刻度条：白底细刻线 + 少量数字（演示刻度，非精确量具）。 */
export function graduatedScaleTexture(): THREE.CanvasTexture {
  return scaleTexture("chem:tex:graduated", (ctx, w, h) => {
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "rgba(248,250,248,0.85)";
    ctx.fillRect(0, 0, w, h);
    ctx.strokeStyle = "rgba(60,70,75,0.85)";
    ctx.fillStyle = "rgba(45,55,60,0.95)";
    ctx.lineWidth = 2;
    const marks = 12;
    for (let i = 0; i <= marks; i++) {
      const y = 18 + ((h - 36) * i) / marks;
      const long = i % 2 === 0;
      ctx.beginPath();
      ctx.moveTo(10, y);
      ctx.lineTo(long ? 46 : 30, y);
      ctx.stroke();
      if (long && i % 4 === 0) {
        ctx.font = "600 22px system-ui, sans-serif";
        ctx.fillText(String(marks - i), 54, y + 8);
      }
    }
  });
}

/** 试剂瓶标签：色带 + 自创演示短名（虚构物料，无真实配方）。 */
export function reagentLabelTexture(colorId: string, zh: string, en: string): THREE.CanvasTexture {
  const hex = `#${liquidColorHex(colorId).toString(16).padStart(6, "0")}`;
  return scaleTexture(`chem:tex:reagent:${colorId}`, (ctx, w, h) => {
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "rgba(250,251,250,0.94)";
    ctx.fillRect(0, h * 0.3, w, h * 0.4);
    ctx.fillStyle = hex;
    ctx.fillRect(0, h * 0.26, w, h * 0.05);
    ctx.fillRect(0, h * 0.69, w, h * 0.05);
    ctx.fillStyle = "rgba(40,50,55,0.95)";
    ctx.textAlign = "center";
    ctx.font = "600 30px system-ui, sans-serif";
    ctx.fillText(zh, w / 2, h * 0.45);
    ctx.font = "500 20px system-ui, sans-serif";
    ctx.fillText(en, w / 2, h * 0.56);
  });
}

// ── 液体层 ─────────────────────────────────────────────────────────────────

/**
 * 按容器内轮廓生成“装到 fill 高度”的封闭液体旋转体（含轻微弯月面）。
 * innerProfile(y) 返回高度 y 处的内半径；liquidTop 为可用液面上限（局部 Y）。
 * 每个实例在液量变化时重建（离散操作触发，非每帧）。
 */
export function buildLiquidLathe(
  spec: { bottom: number; liquidTop: number; innerR: (y: number) => number },
  fill: number,
): THREE.LatheGeometry {
  const clamped = Math.min(Math.max(fill, 0.02), 1);
  const y0 = spec.bottom + 0.02;
  const y1 = spec.bottom + (spec.liquidTop - spec.bottom) * clamped;
  const steps = 18;
  const profile: THREE.Vector2[] = [];
  profile.push(new THREE.Vector2(0.015, y0));
  for (let i = 0; i <= steps; i++) {
    const y = y0 + ((y1 - y0) * i) / steps;
    profile.push(new THREE.Vector2(Math.max(spec.innerR(y), 0.02), y));
  }
  const rTop = Math.max(spec.innerR(y1), 0.02);
  // 弯月面：边缘微升再收拢到中心。
  profile.push(new THREE.Vector2(rTop * 0.9, y1 + 0.018));
  profile.push(new THREE.Vector2(rTop * 0.45, y1 + 0.026));
  profile.push(new THREE.Vector2(0.015, y1 + 0.012));
  return new THREE.LatheGeometry(profile, 36);
}
