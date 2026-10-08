"use client";

/**
 * The single gesture controller for the lab scene. One pointer source, one
 * state machine, one commit point:
 *
 * - `idle → pressed` only records the grab point (no ghost, no POST);
 * - `pressed → dragging` after a per-pointer-type CSS-px threshold;
 * - during a drag the only work is ref updates + rAF-batched visual state
 *   (ghost transform + drop-candidate highlight) — zero server writes;
 * - `pointerup` from `dragging` resolves ONE DropCandidate from the latest
 *   world point and calls the same onMoveObject / onDragOperation the click
 *   and keyboard paths use; `pointerup` from `pressed` is a short press and
 *   commits selection directly (captured clicks never reach the hit button);
 * - pointercancel / lostpointercapture / Escape / blur / hidden tab /
 *   session switch / unmount all funnel into one cancel path that clears
 *   ghost + capture without any implicit placement.
 *
 * The ref is the truth; the React `gestureView` mirror exists only so the
 * ghost can render, and is updated at most once per animation frame.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { ChemLabObjectRef, OperationDraft } from "../interaction";
import {
  resolveDropIntent,
  type DropCandidate,
  type SceneModel,
} from "./scene-model.ts";
import {
  DRAG_START_THRESHOLD_PX,
  DROP_EDGE_TOLERANCE_PX,
  type LabPointerType,
} from "./scene-geometry.ts";
import { clientToWorld, cssPxToWorldX } from "./scene-projection.ts";

export type GestureCancelReason =
  | "escape"
  | "blur"
  | "scene_change"
  | "conflict"
  | "modal"
  | "unmount"
  | "pointercancel"
  | "lostpointercapture"
  | "invalid_matrix";

interface ActiveGesture {
  phase: "pressed" | "dragging";
  pointerId: number;
  pointerType: LabPointerType;
  objectId: string;
  /** The hit button the press started on — capture retargets every later
   * pointer event to the viewport, so the original element is kept for the
   * short-press select / instrument-tap commit. */
  hitElement: HTMLElement | null;
  sourceSessionId: string;
  startClient: { x: number; y: number };
  startWorld: { x: number; y: number };
  grabOffsetWorld: { x: number; y: number };
  sourceWorld: { x: number; y: number; width: number; height: number };
  currentClient: { x: number; y: number };
  candidate: DropCandidate | null;
  finished: boolean;
}

type GestureState = { phase: "idle" } | ({ phase: "pressed" | "dragging" } & ActiveGesture);

export interface GestureView {
  objectId: string;
  pointerType: LabPointerType;
  /** Ghost top-left in world units (grab offset preserved — no centre jump). */
  worldX: number;
  worldY: number;
  candidate: DropCandidate;
}

export interface KeyboardTarget {
  kind: "slot" | "object";
  id: string;
}

export interface UseLabInteractionParams {
  scene: SceneModel | null;
  viewportRef: React.RefObject<HTMLElement | null>;
  sceneGroupRef: React.RefObject<SVGGraphicsElement | null>;
  busy: boolean;
  /** Commands locked (conflict/offline) or read-only (history view). */
  locked: boolean;
  sessionId: string | null;
  onSelect: (ref: ChemLabObjectRef) => void;
  onMoveObject: (objectId: string, slotId: string) => void;
  onDragOperation: (draft: OperationDraft) => void;
  /** Instrument menu anchor — same contract as the LabScene prop. */
  onInstrumentTap: (equipmentId: string, anchor: HTMLElement) => void;
}

export function useLabInteraction(params: UseLabInteractionParams) {
  const { viewportRef, sceneGroupRef, busy, locked, sessionId } = params;
  const callbacksRef = useRef(params);
  // Latest-props mirror for event-time reads (never read during render).
  useEffect(() => {
    callbacksRef.current = params;
  });

  const gestureRef = useRef<GestureState>({ phase: "idle" });
  const rafRef = useRef<number | null>(null);
  const suppressClickUntilRef = useRef(0);
  const [gestureView, setGestureView] = useState<GestureView | null>(null);
  const [targetSelectionFor, setTargetSelectionFor] = useState<string | null>(null);

  const releaseRaf = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
  }, []);

  const clearCapture = useCallback((pointerId: number | null) => {
    const viewport = viewportRef.current;
    if (!viewport || pointerId === null) return;
    try {
      if (viewport.hasPointerCapture(pointerId)) viewport.releasePointerCapture(pointerId);
    } catch {
      // Element gone — capture died with it.
    }
  }, [viewportRef]);

  const cancelGesture = useCallback((reason: GestureCancelReason) => {
    const gesture = gestureRef.current;
    if (gesture.phase === "idle") return;
    releaseRaf();
    clearCapture(gesture.pointerId);
    gestureRef.current = { phase: "idle" };
    setGestureView(null);
    void reason;
  }, [clearCapture, releaseRaf]);

  // ---- lifecycle guards -----------------------------------------------------

  useEffect(() => () => cancelGesture("unmount"), [cancelGesture]);

  useEffect(() => {
    // Session/experiment switch or lock while a gesture is mid-flight.
    cancelGesture("scene_change");
  }, [sessionId, locked, busy, cancelGesture]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (gestureRef.current.phase !== "idle") {
        cancelGesture("escape");
      } else if (targetSelectionFor !== null) {
        setTargetSelectionFor(null);
      }
    };
    const onBlur = () => cancelGesture("blur");
    const onVisibility = () => {
      if (document.visibilityState === "hidden") cancelGesture("blur");
    };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("blur", onBlur);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("blur", onBlur);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [cancelGesture, targetSelectionFor]);

  // ---- rAF visual flush -------------------------------------------------------

  const flushView = useCallback(() => {
    rafRef.current = null;
    const gesture = gestureRef.current;
    if (gesture.phase === "idle") {
      setGestureView(null);
      return;
    }
    const group = sceneGroupRef.current;
    const world = clientToWorld(gesture.currentClient.x, gesture.currentClient.y, group);
    if (!world) {
      setGestureView((prev) =>
        prev
          ? { ...prev, candidate: { type: "invalid", reason: "out_of_bounds" } }
          : prev,
      );
      return;
    }
    const currentScene = callbacksRef.current.scene;
    if (!currentScene) return;
    const worldX = world.x - gesture.grabOffsetWorld.x;
    const worldY = world.y - gesture.grabOffsetWorld.y;
    const source = currentScene.nodes.find((node) => node.ref.id === gesture.objectId);
    if (!source) {
      cancelGesture("scene_change");
      return;
    }
    const candidate = resolveDropIntent(
      source.ref,
      world,
      currentScene,
      {
        heldId: currentScene.heldId,
        locked: callbacksRef.current.locked,
        pad: cssPxToWorldX(DROP_EDGE_TOLERANCE_PX, group),
      },
    );
    setGestureView({
      objectId: gesture.objectId,
      pointerType: gesture.pointerType,
      worldX,
      worldY,
      candidate,
    });
  }, [cancelGesture, sceneGroupRef]);

  const scheduleFlush = useCallback(() => {
    if (rafRef.current !== null) return;
    rafRef.current = requestAnimationFrame(flushView);
  }, [flushView]);

  // ---- pointer entry points ---------------------------------------------------

  const startObjectPointer = useCallback(
    (ref: ChemLabObjectRef, event: React.PointerEvent<HTMLElement>) => {
      const currentScene = callbacksRef.current.scene;
      const currentSession = callbacksRef.current.sessionId;
      if (!currentScene || !currentSession) return;
      if (gestureRef.current.phase !== "idle") return;
      if (!event.isPrimary) return;
      if (event.pointerType === "mouse" && event.button !== 0) return;
      if (callbacksRef.current.busy || callbacksRef.current.locked) return;
      const node = currentScene.nodes.find((item) => item.ref.id === ref.id);
      if (!node) return;
      // Another object is already held server-side: no drag until released.
      if (currentScene.heldId && currentScene.heldId !== ref.id) return;

      const group = sceneGroupRef.current;
      const world = clientToWorld(event.clientX, event.clientY, group);
      if (!world) return; // no CTM → no gesture, click path still selects

      const viewport = viewportRef.current;
      if (!viewport) return;
      try {
        viewport.setPointerCapture(event.pointerId);
      } catch {
        return; // capture is the stability guarantee; without it, no drag
      }

      gestureRef.current = {
        phase: "pressed",
        pointerId: event.pointerId,
        pointerType: event.pointerType as LabPointerType,
        objectId: ref.id,
        hitElement: event.currentTarget,
        sourceSessionId: currentSession,
        startClient: { x: event.clientX, y: event.clientY },
        startWorld: world,
        grabOffsetWorld: {
          x: world.x - node.world.x,
          y: world.y - node.world.y,
        },
        sourceWorld: {
          x: node.world.x,
          y: node.world.y,
          width: node.world.width,
          height: node.world.height,
        },
        currentClient: { x: event.clientX, y: event.clientY },
        candidate: null,
        finished: false,
      };
      event.preventDefault();
    },
    [sceneGroupRef, viewportRef],
  );

  const pointerMove = useCallback(
    (event: React.PointerEvent<HTMLElement>) => {
      const gesture = gestureRef.current;
      if (gesture.phase === "idle" || gesture.pointerId !== event.pointerId) return;
      gesture.currentClient = { x: event.clientX, y: event.clientY };
      if (gesture.phase === "pressed") {
        const dx = event.clientX - gesture.startClient.x;
        const dy = event.clientY - gesture.startClient.y;
        const threshold = DRAG_START_THRESHOLD_PX[gesture.pointerType] ?? 8;
        if (Math.hypot(dx, dy) < threshold) return;
        gesture.phase = "dragging";
        // Swallow the synthetic click that follows this pointer sequence;
        // keyboard/AT clicks (no drag) are never affected.
        suppressClickUntilRef.current = performance.now() + 500;
      }
      if (event.cancelable) event.preventDefault();
      scheduleFlush();
    },
    [scheduleFlush],
  );

  /** Shared resolution: pointerup, keyboard target choice, click-to-move. */
  const commitCandidate = useCallback(
    (objectId: string, worldPoint: { x: number; y: number }, pad: number) => {
      const currentScene = callbacksRef.current.scene;
      if (!currentScene) return;
      const node = currentScene.nodes.find((item) => item.ref.id === objectId);
      if (!node) return;
      const candidate = resolveDropIntent(node.ref, worldPoint, currentScene, {
        heldId: currentScene.heldId,
        locked: callbacksRef.current.locked,
        pad,
      });
      if (candidate.type === "move") {
        callbacksRef.current.onMoveObject(candidate.objectId, candidate.slotId);
      } else if (candidate.type === "operation") {
        callbacksRef.current.onDragOperation(candidate.draft);
      }
    },
    [],
  );

  const pointerUp = useCallback(
    (event: React.PointerEvent<HTMLElement>) => {
      const gesture = gestureRef.current;
      if (gesture.phase === "idle" || gesture.pointerId !== event.pointerId) return;
      // Mark finished first so the lostpointercapture that follows this
      // release never double-settles the gesture.
      gesture.finished = true;
      releaseRaf();
      clearCapture(gesture.pointerId);
      const phase = gesture.phase;
      gestureRef.current = { phase: "idle" };
      setGestureView(null);
      if (phase === "pressed") {
        // Short press commits selection HERE: viewport pointer capture
        // retargets the trailing click to a common ancestor, so the hit
        // button's onClick never fires for pointer-originated taps. The
        // suppression flag swallows that click wherever it lands; onClick
        // stays only as the fallback for gestures that never started
        // (busy/locked/no CTM), which set no flag.
        const currentScene = callbacksRef.current.scene;
        const node = currentScene?.nodes.find((item) => item.ref.id === gesture.objectId);
        if (node) {
          suppressClickUntilRef.current = performance.now() + 500;
          callbacksRef.current.onSelect(node.ref);
          if (node.ref.type === "equipment" && gesture.hitElement) {
            callbacksRef.current.onInstrumentTap(gesture.objectId, gesture.hitElement);
          }
        }
        return;
      }
      const world = clientToWorld(event.clientX, event.clientY, sceneGroupRef.current);
      if (!world) return; // invalid matrix at release → snap back, no command
      commitCandidate(
        gesture.objectId,
        world,
        cssPxToWorldX(DROP_EDGE_TOLERANCE_PX, sceneGroupRef.current),
      );
    },
    [clearCapture, commitCandidate, releaseRaf, sceneGroupRef],
  );

  const pointerCancel = useCallback(
    (event: React.PointerEvent<HTMLElement>) => {
      const gesture = gestureRef.current;
      if (gesture.phase === "idle" || gesture.pointerId !== event.pointerId) return;
      cancelGesture("pointercancel");
    },
    [cancelGesture],
  );

  const lostCapture = useCallback(
    (event: React.PointerEvent<HTMLElement>) => {
      const gesture = gestureRef.current;
      if (gesture.phase === "idle" || gesture.pointerId !== event.pointerId) return;
      if (gesture.finished) return; // expected release, already settled
      cancelGesture("lostpointercapture");
    },
    [cancelGesture],
  );

  // ---- keyboard / click-to-move path --------------------------------------------

  const chooseTargetByKeyboard = useCallback(
    (sourceId: string, target: KeyboardTarget) => {
      const currentScene = callbacksRef.current.scene;
      if (!currentScene) return;
      let point: { x: number; y: number } | null = null;
      if (target.kind === "slot") {
        const slot = currentScene.slots.find((view) => view.id === target.id);
        if (!slot) return;
        point = {
          x: slot.rect.x + slot.rect.w / 2,
          y: slot.rect.y + slot.rect.h / 2,
        };
      } else {
        const node = currentScene.nodes.find((item) => item.ref.id === target.id);
        if (!node || node.ref.id === sourceId) return;
        point = {
          x: node.world.x + node.world.width / 2,
          y: node.world.y + node.world.height / 2,
        };
      }
      commitCandidate(sourceId, point, 0);
      setTargetSelectionFor(null);
    },
    [commitCandidate],
  );

  const requestTargetSelection = useCallback((objectId: string | null) => {
    setTargetSelectionFor((prev) => (prev === objectId ? null : objectId));
  }, []);

  /** ObjectHitTarget onClick helper — true when this click must be swallowed
   * because it is the tail of a gesture already settled in pointerUp (drag
   * or short press). */
  const shouldSuppressClick = useCallback(() => {
    if (suppressClickUntilRef.current === 0) return false;
    const suppressed = performance.now() < suppressClickUntilRef.current;
    suppressClickUntilRef.current = 0;
    return suppressed;
  }, []);

  return {
    gestureView,
    gestureActive: gestureView !== null,
    targetSelectionFor,
    startObjectPointer,
    pointerMove,
    pointerUp,
    pointerCancel,
    lostCapture,
    cancel: cancelGesture,
    chooseTargetByKeyboard,
    requestTargetSelection,
    shouldSuppressClick,
  };
}

export type LabGestureController = ReturnType<typeof useLabInteraction>;
