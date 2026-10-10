"use client";

/**
 * Single owner of the WebGLRenderer/Scene/Camera/OrbitControls lifecycle
 * (plan H1): invalidate-on-demand rendering, resize/DPR handling, context
 * loss recovery and full disposal. Math space is right-handed z-up; every
 * position/direction crosses `mathToThree` exactly once at this boundary.
 */

import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { mathToThree, v3, type Camera3DSettings, type Pt3 } from "./stage-adapter.ts";

export type Invalidation = () => void;

export interface ControllerHooks {
  onCameraChange?: (camera: Camera3DSettings) => void;
  onPick?: (mathPoint: Pt3 | null, objectId: string | null) => void;
}

export class ThreeSceneController {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene: THREE.Scene;
  readonly camera: THREE.PerspectiveCamera;
  readonly controls: OrbitControls;
  readonly root: THREE.Group;
  private readonly frame = { requested: false };
  private readonly host: HTMLElement;
  private readonly hooks: ControllerHooks;
  private readonly resizeObserver: ResizeObserver;
  private disposed = false;

  static webgl2Available(): boolean {
    try {
      const canvas = document.createElement("canvas");
      return Boolean(canvas.getContext("webgl2"));
    } catch {
      return false;
    }
  }

  constructor(host: HTMLElement, hooks: ControllerHooks = {}) {
    this.host = host;
    this.hooks = hooks;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "default" });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.setClearColor(0x000000, 0);
    host.appendChild(this.renderer.domElement);
    this.renderer.domElement.classList.add("stage3d-canvas");

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(50, 1, 0.05, 500);
    this.root = new THREE.Group();
    this.scene.add(this.root);

    const hemi = new THREE.HemisphereLight(0xffffff, 0x8a8578, 0.85);
    const key = new THREE.DirectionalLight(0xfff4e0, 1.15);
    key.position.set(6, 10, 4);
    const fill = new THREE.DirectionalLight(0xd8e6e2, 0.35);
    fill.position.set(-8, 4, -6);
    this.scene.add(hemi, key, fill);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.addEventListener("change", () => {
      this.invalidate();
      this.hooks.onCameraChange?.(this.cameraSettings());
    });
    this.controls.addEventListener("start", () => this.hooks.onPick?.(null, null));

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(host);
    this.resize();

    this.renderer.domElement.addEventListener("webglcontextlost", (event) => {
      event.preventDefault();
    });
    this.renderer.domElement.addEventListener("webglcontextrestored", () => {
      this.invalidate();
    });
    this.invalidate();
  }

  /** Camera position from math-space spherical coordinates (azimuth/elev/dist). */
  applyCamera(settings: Camera3DSettings): void {
    const target = mathToThree(settings.target);
    this.controls.target.set(target.x, target.y, target.z);
    if (settings.kind === "orthographic") {
      this.camera.fov = 12; // narrow fov approximates orthographic look
    } else {
      this.camera.fov = 50;
    }
    const horizontal = settings.distance * Math.cos(settings.elevation);
    const mathPosition = v3(
      settings.target.x + horizontal * Math.cos(settings.azimuth),
      settings.target.y + horizontal * Math.sin(settings.azimuth),
      settings.target.z + settings.distance * Math.sin(settings.elevation),
    );
    const three = mathToThree(mathPosition);
    this.camera.position.set(three.x, three.y, three.z);
    this.camera.updateProjectionMatrix();
    this.controls.update();
    this.invalidate();
  }

  cameraSettings(): Camera3DSettings {
    const offset = this.camera.position.clone().sub(this.controls.target);
    const distance = offset.length();
    const threeTarget = { x: this.controls.target.x, y: this.controls.target.y, z: this.controls.target.z };
    // three → math
    const mathOffset = { x: offset.x, y: -offset.z, z: offset.y };
    const target = { x: threeTarget.x, y: -threeTarget.z, z: threeTarget.y };
    const horizontal = Math.hypot(mathOffset.x, mathOffset.y);
    return {
      kind: this.camera.fov <= 12 ? "orthographic" : "perspective",
      azimuth: Math.atan2(mathOffset.y, mathOffset.x),
      elevation: Math.atan2(mathOffset.z, horizontal),
      distance,
      target,
    };
  }

  fit(rootBox: THREE.Box3): void {
    if (rootBox.isEmpty()) return;
    const sphere = rootBox.getBoundingSphere(new THREE.Sphere());
    const three = mathToThree({ x: sphere.center.x, y: sphere.center.y, z: sphere.center.z });
    this.controls.target.set(three.x, three.y, three.z);
    const distance = Math.max(sphere.radius * 2.6, 2);
    const current = this.cameraSettings();
    this.applyCamera({ ...current, distance, target: { x: sphere.center.x, y: sphere.center.y, z: sphere.center.z } });
    void distance;
  }

  resize(): void {
    const rect = this.host.getBoundingClientRect();
    const width = Math.max(16, rect.width);
    const height = Math.max(16, rect.height);
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.invalidate();
  }

  invalidate(): void {
    if (this.disposed || this.frame.requested) return;
    this.frame.requested = true;
    requestAnimationFrame(() => {
      this.frame.requested = false;
      if (this.disposed) return;
      // Damped controls need continuous updates until they settle (H6).
      if (this.controls.enableDamping) this.controls.update();
      this.renderer.render(this.scene, this.camera);
    });
  }

  /** Explicit render used by PNG export (no preserveDrawingBuffer). */
  renderOnce(): void {
    this.renderer.render(this.scene, this.camera);
  }

  dispose(): void {
    this.disposed = true;
    this.resizeObserver.disconnect();
    this.controls.dispose();
    this.root.traverse((node) => {
      const mesh = node as THREE.Mesh;
      if (mesh.geometry) mesh.geometry.dispose();
      const material = mesh.material as THREE.Material | THREE.Material[] | undefined;
      if (Array.isArray(material)) material.forEach((m) => m.dispose());
      else material?.dispose();
    });
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }
}
