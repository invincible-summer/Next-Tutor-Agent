"use client";

/**
 * 3D stage React boundary: mounts the Three scene controller lazily (this
 * module itself is dynamically imported by MathWorkbench so the Three chunk
 * only loads in 3D modes), rebuilds objects on document changes, picks
 * meshes by raycast and exports PNG through an explicit offscreen render.
 * The camera is applied from the document once, on mount; afterwards the
 * OrbitControls own it live and gesture settles are persisted back through
 * `setCamera3D` (non-semantic, never in undo history — ADR-0022 fixes).
 */

import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { ThreeSceneController } from "./three/scene-controller.ts";
import { buildEnvironment, buildSceneObjects, meshFromPayload } from "./three/scene-objects.ts";
import { MathWorkerScheduler } from "./worker/scheduler.ts";
import type { TaskResponse } from "./worker/protocol.ts";
import { DEFAULT_CAMERA_3D, type Camera3DSettings, type MathCommand, type MathWorkbenchDocument } from "./workbench-types.ts";

export interface ViewControls3D {
  resetView: () => void;
  fitView: () => void;
}

export function Stage3D({
  document, selection, onSelect, tr, dispatch, onViewControls,
}: {
  document: MathWorkbenchDocument;
  selection: string | null;
  onSelect: (id: string | null) => void;
  tr: (key: string) => string;
  dispatch: (command: MathCommand, label: string) => void;
  onViewControls?: (controls: ViewControls3D | null) => void;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const controllerRef = useRef<ThreeSceneController | null>(null);
  const contentRef = useRef<THREE.Group | null>(null);
  const schedulerRef = useRef<MathWorkerScheduler | null>(null);
  const resultHandlerRef = useRef<((response: TaskResponse) => void) | null>(null);
  const workerHealthyRef = useRef(true);
  const cameraFlushRef = useRef<number | null>(null);
  const dispatchRef = useRef(dispatch);
  useEffect(() => { dispatchRef.current = dispatch; }, [dispatch]);
  const [webglOk, setWebglOk] = useState(true);
  const [resolution, setResolution] = useState({ width: 800, height: 600 });
  const revision = document.revision;
  void tr;

  /** Persist the camera after the gesture settles (not per frame). */
  const scheduleCameraFlush = useRef(() => {
    if (cameraFlushRef.current !== null) return;
    cameraFlushRef.current = window.setTimeout(() => {
      cameraFlushRef.current = null;
      const controller = controllerRef.current;
      if (!controller) return;
      dispatchRef.current({ kind: "setCamera3D", camera: controller.cameraSettings() }, "camera");
    }, 350);
  });

  useEffect(() => {
    if (!ThreeSceneController.webgl2Available()) {
      const timer = window.setTimeout(() => setWebglOk(false), 0);
      return () => window.clearTimeout(timer);
    }
    const host = hostRef.current;
    if (!host) return;
    const controller = new ThreeSceneController(host, {
      onCameraChange: () => scheduleCameraFlush.current(),
    });
    // The stored camera is authoritative exactly once per mount; afterwards
    // document edits must never yank the orbit back to a stale position.
    controller.applyCamera(document.camera3d);
    controllerRef.current = controller;
    const scheduler = new MathWorkerScheduler({
      onResult: (response) => resultHandlerRef.current?.(response),
      onFallback: () => {
        // Worker died or unavailable: compute inline from now on and repaint.
        workerHealthyRef.current = false;
        controllerRef.current?.invalidate();
      },
    });
    schedulerRef.current = scheduler;
    const observer = new ResizeObserver(() => {
      const rect = host.getBoundingClientRect();
      setResolution({ width: Math.max(16, rect.width), height: Math.max(16, rect.height) });
    });
    observer.observe(host);
    return () => {
      observer.disconnect();
      scheduler.dispose();
      controller.dispose();
      controllerRef.current = null;
      schedulerRef.current = null;
      if (cameraFlushRef.current !== null) {
        window.clearTimeout(cameraFlushRef.current);
        cameraFlushRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Rebuild scene contents whenever the document semantics or size change.
  useEffect(() => {
    const controller = controllerRef.current;
    if (!controller) return;
    if (contentRef.current) {
      controller.root.remove(contentRef.current);
      contentRef.current.traverse((node) => {
        const mesh = node as THREE.Mesh;
        mesh.geometry?.dispose?.();
        const material = mesh.material as THREE.Material | THREE.Material[] | undefined;
        if (Array.isArray(material)) material.forEach((m) => m.dispose());
        else material?.dispose();
      });
    }
    const environment = buildEnvironment(document.render3d.showGridXY, document.render3d.showAxes);
    const built = buildSceneObjects(document, resolution, { deferImplicit: workerHealthyRef.current });
    const container = new THREE.Group();
    container.add(environment, built.group);
    contentRef.current = container;
    controller.root.add(container);
    controller.invalidate();
    // Heavy implicit isosurfaces go through the worker; fresh results swap
    // their mesh in, stale revisions are dropped (plan I1/I2).
    if (workerHealthyRef.current && schedulerRef.current) {
      const parameters = Object.fromEntries(document.parameters.map((p) => [p.name, p.value]));
      const functions = document.functions.map((f) => ({ name: f.name, params: [...f.params], expression: f.expression }));
      for (const plot of document.plots3d) {
        if (plot.kind !== "implicitSurface" || !plot.visible) continue;
        void schedulerRef.current.submit(plot.id, document.id, revision, "surfaceImplicit", { plot, parameters, functions });
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revision, resolution.width, resolution.height, document.render3d.showGridXY, document.render3d.showAxes]);

  // Worker mesh arrival: revision guard, then swap the plot's mesh in place.
  useEffect(() => {
    resultHandlerRef.current = (response) => {
      if (!response.ok || response.revision !== document.revision) return; // stale guard
      const content = contentRef.current;
      const controller = controllerRef.current;
      if (!content || !controller) return;
      const plot = document.plots3d.find((p) => p.id === response.id);
      if (!plot || plot.kind !== "implicitSurface" || !plot.visible) return;
      const previous = content.children.find((child) => child.userData?.id === plot.id);
      if (previous) {
        content.remove(previous);
        const mesh = previous as THREE.Mesh;
        mesh.geometry?.dispose();
        (mesh.material as THREE.Material)?.dispose();
      }
      const mesh = meshFromPayload(response.result, plot.style.color, plot.opacity);
      mesh.userData.id = plot.id;
      content.add(mesh);
      controller.invalidate();
    };
    return () => { resultHandlerRef.current = null; };
  }, [document.revision, document.plots3d]);

  // Selection highlight pass (no geometry rebuilds).
  useEffect(() => {
    const content = contentRef.current;
    if (!content) return;
    content.traverse((node) => {
      const id = node.userData?.id as string | undefined;
      if (id) (node as THREE.Mesh).scale.setScalar(id === selection ? 1.35 : 1);
    });
    controllerRef.current?.invalidate();
  }, [selection]);

  /* ------------------------------- view controls ------------------------------- */
  const applyCamera = (camera: Camera3DSettings) => {
    controllerRef.current?.applyCamera(camera);
    dispatchRef.current({ kind: "setCamera3D", camera }, "camera");
  };
  const controls = useMemo<ViewControls3D>(() => ({
    resetView: () => applyCamera({ ...DEFAULT_CAMERA_3D, target: { ...DEFAULT_CAMERA_3D.target } }),
    fitView: () => {
      const content = contentRef.current;
      if (!content) return;
      const box = new THREE.Box3().setFromObject(content);
      if (box.isEmpty()) { applyCamera({ ...DEFAULT_CAMERA_3D, target: { ...DEFAULT_CAMERA_3D.target } }); return; }
      controllerRef.current?.fit(box);
      scheduleCameraFlush.current();
    },
  }), []);
  const controlsRef = useRef(controls);
  useEffect(() => { controlsRef.current = controls; }, [controls]);
  useEffect(() => {
    const current = controlsRef.current;
    onViewControls?.(current);
    return () => onViewControls?.(null);
  }, [onViewControls]);

  // Pointer picking: raycast against plot meshes (orbit stays owned by
  // OrbitControls; a click that moved the camera is ignored).
  useEffect(() => {
    const controller = controllerRef.current;
    const dom = controller?.renderer.domElement;
    if (!controller || !dom) return;
    let downAt: { x: number; y: number } | null = null;
    const onPointerDown = (event: PointerEvent) => {
      downAt = { x: event.clientX, y: event.clientY };
    };
    const onPointerUp = (event: PointerEvent) => {
      if (!downAt) return;
      const moved = Math.hypot(event.clientX - downAt.x, event.clientY - downAt.y);
      downAt = null;
      if (moved > 5) return; // that was an orbit gesture, not a pick
      const rect = dom.getBoundingClientRect();
      const ndc = new THREE.Vector2(
        ((event.clientX - rect.left) / rect.width) * 2 - 1,
        -((event.clientY - rect.top) / rect.height) * 2 + 1,
      );
      const raycaster = new THREE.Raycaster();
      raycaster.setFromCamera(ndc, controller.camera);
      const meshes: THREE.Object3D[] = [];
      contentRef.current?.traverse((node) => { if (node.userData?.id) meshes.push(node); });
      const hits = raycaster.intersectObjects(meshes, false);
      if (hits.length > 0) {
        const hit = hits[0] as THREE.Intersection;
        onSelect(hit.object.userData?.id ?? null);
      } else {
        onSelect(null);
      }
    };
    dom.addEventListener("pointerdown", onPointerDown);
    dom.addEventListener("pointerup", onPointerUp);
    return () => {
      dom.removeEventListener("pointerdown", onPointerDown);
      dom.removeEventListener("pointerup", onPointerUp);
    };
  }, [onSelect, revision]);

  // PNG export via explicit render (H6): no preserveDrawingBuffer needed.
  useEffect(() => {
    const handler = () => {
      const controller = controllerRef.current;
      if (!controller) return;
      controller.renderOnce();
      controller.renderer.domElement.toBlob((blob) => {
        if (!blob) return;
        const url = URL.createObjectURL(blob);
        const anchor = window.document.createElement("a");
        anchor.href = url;
        anchor.download = `${(document.name.trim() || "drawing").replace(/[\\/:*?"<>|]/g, "_")}.png`;
        anchor.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 4000);
      }, "image/png");
    };
    window.addEventListener("math-workbench:export-canvas", handler);
    return () => window.removeEventListener("math-workbench:export-canvas", handler);
  }, [document.name]);

  if (!webglOk) {
    return (
      <div className="stage3d-unavailable" role="status">
        <p>{tr("webglUnavailable")}</p>
      </div>
    );
  }
  return <div ref={hostRef} className="stage3d-host" data-testid="geometry-stage-3d" />;
}
