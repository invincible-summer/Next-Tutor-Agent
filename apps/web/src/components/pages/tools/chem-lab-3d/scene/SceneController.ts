"use client";

/**
 * 化学实验台 3D 场景唯一生命周期持有者（chem-lab architecture）：单例 WebGLRenderer /
 * Scene / PerspectiveCamera / OrbitControls / rAF。渲染调度采用“按需 invalidate +
 * 活跃源连续帧”：静止且无相机阻尼时不再渲染；拖动、演出效果、相机活动期间自动
 * 追加下一帧。内容（器材/管线/特效）由各自模块挂到 root 并自行释放，
 * controller 只负责帧调度与 WebGL 上下文。
 */
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import type { CameraPose } from "@next-tutor/domain";

/** 自动质量档（不暴露给用户选择；DPR/阴影/transmission 由各模块按档取用）。 */
export type QualityTier = "high" | "balanced" | "low";

export function tierPixelRatioCap(tier: QualityTier): number {
  return tier === "high" ? 2 : tier === "balanced" ? 1.75 : 1.25;
}

function detectTier(): QualityTier {
  if (typeof navigator === "undefined") return "balanced";
  // 开发调试用隐藏覆盖（无 UI 菜单；产品决策：质量自动分级不暴露选项）。
  try {
    const override = localStorage.getItem("chem-lab.tier");
    if (override === "high" || override === "balanced" || override === "low") return override;
  } catch { /* localStorage 不可用时忽略 */ }
  const cores = navigator.hardwareConcurrency ?? 4;
  try {
    const canvas = document.createElement("canvas");
    const gl = canvas.getContext("webgl2");
    // 软件渲染（无 GPU 的 CI/虚拟机）直接最低档，保证可交互。
    const renderer = gl ? (gl.getParameter(gl.RENDERER) as string) : "";
    if (/swiftshader|llvmpipe|software/i.test(renderer)) return "low";
  } catch { /* 检测失败按常规处理 */ }
  if (cores <= 2) return "low";
  if (cores >= 8) return "high";
  return "balanced";
}

export interface FrameContext {
  /** 帧间隔（秒），封顶 50ms 防切后台后突变。 */
  dt: number;
  /** 演出时钟累计（秒）。 */
  elapsed: number;
}

export interface SceneControllerHooks {
  /** 相机被用户/程序改动后回调（用于持久化视角）。 */
  onCameraChange?: (pose: CameraPose) => void;
  /** 每个实际渲染的帧调用（特效/管线跟随更新）。 */
  onFrame?: (ctx: FrameContext) => void;
  onContextLost?: () => void;
  onContextRestored?: () => void;
}

interface CameraTween {
  fromPosition: THREE.Vector3;
  fromTarget: THREE.Vector3;
  toPosition: THREE.Vector3;
  toTarget: THREE.Vector3;
  startedAt: number;
  duration: number;
}

const ACTIVITY = { drag: "drag", camera: "camera", effects: "effects", tween: "tween" } as const;
export type ActivityName = (typeof ACTIVITY)[keyof typeof ACTIVITY];

export class ChemSceneController {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene: THREE.Scene;
  readonly camera: THREE.PerspectiveCamera;
  readonly controls: OrbitControls;
  /** 所有实验室内容的根节点（环境/器材/管线/特效都挂在这里）。 */
  readonly root: THREE.Group;
  readonly tier: QualityTier;

  private readonly host: HTMLElement;
  private readonly hooks: SceneControllerHooks;
  private readonly resizeObserver: ResizeObserver;
  private readonly frame = { requested: false };
  private readonly activities = new Set<ActivityName>();
  private readonly frameListeners = new Set<(ctx: FrameContext) => void>();
  private readonly envTexture: THREE.Texture;
  private lastFrameTime = 0;
  private elapsed = 0;
  private slowFrameWindow: number[] = [];
  private tierDowngraded = false;
  private cameraTween: CameraTween | null = null;
  private disposed = false;

  static webgl2Available(): boolean {
    try {
      const canvas = document.createElement("canvas");
      return Boolean(canvas.getContext("webgl2"));
    } catch {
      return false;
    }
  }

  constructor(host: HTMLElement, hooks: SceneControllerHooks = {}) {
    this.host = host;
    this.hooks = hooks;
    this.tier = detectTier();

    this.renderer = new THREE.WebGLRenderer({ antialias: this.tier !== "low", alpha: false, powerPreference: "high-performance" });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, tierPixelRatioCap(this.tier)));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;
    this.renderer.shadowMap.enabled = this.tier !== "low";
    // Three 0.186 removed PCFSoftShadowMap; PCFShadowMap is the supported
    // filtered path and keeps the same stable shadow behavior across tiers.
    this.renderer.shadowMap.type = THREE.PCFShadowMap;
    const canvas = this.renderer.domElement;
    canvas.classList.add("chem-scene-canvas");
    canvas.style.touchAction = "none"; // PointerEvents 手势由交互层接管
    host.appendChild(canvas);

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(38, 1, 0.1, 120);
    this.root = new THREE.Group();
    this.root.name = "lab-root";
    this.scene.add(this.root);

    // 程序化室内环境贴图（three 自带 RoomEnvironment 渲到 PMREM）：
    // 给玻璃 transmission/金属反射提供高光基准；无任何外部 HDR 资产。
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    const roomScene = new RoomEnvironment();
    this.envTexture = pmrem.fromScene(roomScene, 0.04).texture;
    this.scene.environment = this.envTexture;
    this.scene.environmentIntensity = 0.5;
    roomScene.dispose?.();
    pmrem.dispose();

    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    // 俯仰/距离夹取：永远从台面上方看，不允许钻到台底或拉到无限远。
    this.controls.minPolarAngle = 0.12;
    this.controls.maxPolarAngle = 1.45;
    this.controls.minDistance = 4;
    this.controls.maxDistance = 34;
    this.controls.target.set(0, 1, 0);
    this.controls.addEventListener("change", () => {
      this.invalidate();
      this.hooks.onCameraChange?.(this.cameraPose());
    });
    this.controls.addEventListener("start", () => this.beginActivity(ACTIVITY.camera));
    this.controls.addEventListener("end", () => this.endActivity(ACTIVITY.camera));

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(host);
    this.resize();

    canvas.addEventListener("webglcontextlost", (event) => {
      event.preventDefault();
      this.hooks.onContextLost?.();
    });
    canvas.addEventListener("webglcontextrestored", () => {
      this.invalidate();
      this.hooks.onContextRestored?.();
    });

    this.invalidate();
  }

  // ── 渲染调度 ─────────────────────────────────────────────────────────────

  invalidate(): void {
    if (this.disposed || this.frame.requested) return;
    this.frame.requested = true;
    requestAnimationFrame(() => {
      this.frame.requested = false;
      if (this.disposed) return;
      const now = performance.now();
      const dt = Math.min((now - (this.lastFrameTime || now)) / 1000, 0.05);
      this.lastFrameTime = now;
      this.elapsed += dt;
      this.updateCameraTween(now);
      // 阻尼未收敛时 controls.update() 触发 change → 自续帧直至静止。
      if (this.controls.enableDamping) this.controls.update();
      const ctx = { dt, elapsed: this.elapsed };
      this.hooks.onFrame?.(ctx);
      // 后挂模块（特效等）共享同一 rAF：controller 仍是唯一帧调度者。
      for (const listener of this.frameListeners) listener(ctx);
      this.renderer.render(this.scene, this.camera);
      if (this.activities.size > 0) this.invalidate();
      this.noteFrameTime(dt);
    });
  }

  /** 声明一段持续活动（拖动/相机/演出），活动期间自动连帧渲染。 */
  beginActivity(name: ActivityName): void {
    const size = this.activities.size;
    this.activities.add(name);
    if (this.activities.size !== size) this.invalidate();
  }

  endActivity(name: ActivityName): void {
    this.activities.delete(name);
  }

  get activeActivities(): readonly ActivityName[] { return [...this.activities]; }

  /** 后挂模块订阅每帧回调（同一 rAF 内、controls.update 之后、render 之前）。 */
  addFrameListener(listener: (ctx: FrameContext) => void): void {
    this.frameListeners.add(listener);
  }

  removeFrameListener(listener: (ctx: FrameContext) => void): void {
    this.frameListeners.delete(listener);
  }

  /** 供 PNG 导出等显式渲染（无 preserveDrawingBuffer）。 */
  renderOnce(): void {
    this.renderer.render(this.scene, this.camera);
  }

  // ── 相机 ─────────────────────────────────────────────────────────────────

  /** domain CameraPose → OrbitControls（yaw 绕 Y，pitch 为俯角，distance 为半径）。 */
  applyCameraPose(pose: CameraPose): void {
    const { target, yaw, pitch, distance } = pose;
    this.controls.target.set(target.x, target.y, target.z);
    const horizontal = distance * Math.cos(pitch);
    this.camera.position.set(
      target.x + horizontal * Math.sin(yaw),
      target.y + distance * Math.sin(pitch),
      target.z + horizontal * Math.cos(yaw),
    );
    this.camera.lookAt(this.controls.target);
    this.controls.update();
    this.invalidate();
  }

  cameraPose(): CameraPose {
    const offset = this.camera.position.clone().sub(this.controls.target);
    const distance = offset.length();
    const horizontal = Math.hypot(offset.x, offset.z);
    return {
      yaw: Math.atan2(offset.x, offset.z),
      pitch: Math.atan2(offset.y, horizontal),
      distance,
      target: { x: this.controls.target.x, y: this.controls.target.y, z: this.controls.target.z },
    };
  }

  /** 视角恢复默认（不重置实验、不计 dirty）：带一个短促的活动期让阻尼平滑落位。 */
  resetCamera(pose: CameraPose): void {
    this.applyCameraPose(pose);
  }

  /** 双击器材后的轻量聚焦：只移动相机和目标，不改变文档或选择状态。 */
  focusOn(world: THREE.Vector3, distance = 8): void {
    const currentOffset = this.camera.position.clone().sub(this.controls.target);
    const direction = currentOffset.lengthSq() > 0.001
      ? currentOffset.normalize()
      : new THREE.Vector3(0.35, 0.55, 0.75).normalize();
    const toTarget = world.clone();
    const toPosition = world.clone().add(direction.multiplyScalar(THREE.MathUtils.clamp(distance, 4.5, 18)));
    this.cameraTween = {
      fromPosition: this.camera.position.clone(),
      fromTarget: this.controls.target.clone(),
      toPosition,
      toTarget,
      startedAt: performance.now(),
      duration: 280,
    };
    this.beginActivity(ACTIVITY.tween);
    this.invalidate();
  }

  private updateCameraTween(now: number): void {
    const tween = this.cameraTween;
    if (!tween) return;
    const raw = Math.min(1, Math.max(0, (now - tween.startedAt) / tween.duration));
    const eased = raw < 0.5 ? 2 * raw * raw : 1 - ((-2 * raw + 2) ** 2) / 2;
    this.camera.position.lerpVectors(tween.fromPosition, tween.toPosition, eased);
    this.controls.target.lerpVectors(tween.fromTarget, tween.toTarget, eased);
    this.camera.lookAt(this.controls.target);
    if (raw >= 1) {
      this.cameraTween = null;
      this.endActivity(ACTIVITY.tween);
    }
  }

  // ── 尺寸 / 性能 ─────────────────────────────────────────────────────────

  resize(): void {
    const rect = this.host.getBoundingClientRect();
    const width = Math.max(16, rect.width);
    const height = Math.max(16, rect.height);
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.invalidate();
  }

  /** 世界坐标 → 视口 CSS 像素（ObjectPopover 每帧投影用，不缓存）。 */
  worldToScreen(world: THREE.Vector3, out: { x: number; y: number; visible: boolean }): void {
    const rect = this.renderer.domElement.getBoundingClientRect();
    const projected = world.clone().project(this.camera);
    out.x = rect.left + ((projected.x + 1) / 2) * rect.width;
    out.y = rect.top + ((1 - projected.y) / 2) * rect.height;
    out.visible = projected.z < 1 && projected.z > -1;
  }

  /** 持续掉帧时一次性降档（DPR 压低）；交互能力不受影响。 */
  private noteFrameTime(dt: number): void {
    if (this.tierDowngraded || this.tier === "low" || this.activities.size === 0) return;
    this.slowFrameWindow.push(dt);
    if (this.slowFrameWindow.length < 120) return;
    const samples = this.slowFrameWindow;
    this.slowFrameWindow = [];
    const slow = samples.filter((d) => d > 0.04).length;
    if (slow > samples.length * 0.25) {
      this.tierDowngraded = true;
      this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.25));
    }
  }

  dispose(): void {
    this.disposed = true;
    this.activities.clear();
    this.resizeObserver.disconnect();
    this.controls.dispose();
    // 内容资源（共享几何/材质/管线/粒子）由各自 owner 释放；这里只回收
    // WebGL 上下文与 DOM。renderer.dispose 会释放其分配的 GL 资源。
    this.scene.environment = null;
    this.cameraTween = null;
    this.envTexture.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }
}
