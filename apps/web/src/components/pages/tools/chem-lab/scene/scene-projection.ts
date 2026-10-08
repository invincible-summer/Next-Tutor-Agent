"use client";

/**
 * Scene projection: the single bridge between SVG world coordinates and CSS
 * pixels. Every DOM hit target, ghost anchor and popover position derives
 * from the *same* world group's screen CTM — never from
 * getBoundingClientRect()/viewBox ratio arithmetic, which breaks under
 * `preserveAspectRatio` letterboxing, browser zoom and camera transforms.
 *
 * If the CTM is missing or non-invertible the projection degrades to null
 * and callers must disable dropping (and say so), never guess.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  invertAffine,
  type Affine2D,
  type Point,
  type WorldRect,
} from "./scene-geometry.ts";

export interface CssRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** client px → world units through the group's current screen CTM. */
export function clientToWorld(
  clientX: number,
  clientY: number,
  group: SVGGraphicsElement | null,
): Point | null {
  const ctm = group?.getScreenCTM();
  if (!ctm) return null;
  try {
    const inverse = ctm.inverse();
    const point = new DOMPoint(clientX, clientY).matrixTransform(inverse);
    if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) return null;
    return { x: point.x, y: point.y };
  } catch {
    return null;
  }
}

/** world units → client px (same CTM, forward direction). */
export function worldToClient(point: Point, group: SVGGraphicsElement | null): Point | null {
  const ctm = group?.getScreenCTM();
  if (!ctm) return null;
  const mapped = new DOMPoint(point.x, point.y).matrixTransform(ctm);
  if (!Number.isFinite(mapped.x) || !Number.isFinite(mapped.y)) return null;
  return { x: mapped.x, y: mapped.y };
}

/**
 * Project a world rectangle into a CSS-pixel rect relative to the overlay
 * container. The four corners are projected individually so non-uniform
 * transforms (letterboxed meet, camera zoom) still bound the visual object.
 */
export function worldRectToCssRect(
  rect: WorldRect,
  group: SVGGraphicsElement | null,
  overlay: HTMLElement | null,
): CssRect | null {
  if (!group || !overlay) return null;
  const corners: Point[] = [
    { x: rect.x, y: rect.y },
    { x: rect.x + rect.width, y: rect.y },
    { x: rect.x, y: rect.y + rect.height },
    { x: rect.x + rect.width, y: rect.y + rect.height },
  ];
  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  const base = overlay.getBoundingClientRect();
  for (const corner of corners) {
    const mapped = worldToClient(corner, group);
    if (!mapped) return null;
    minX = Math.min(minX, mapped.x - base.left);
    minY = Math.min(minY, mapped.y - base.top);
    maxX = Math.max(maxX, mapped.x - base.left);
    maxY = Math.max(maxY, mapped.y - base.top);
  }
  return { left: minX, top: minY, width: maxX - minX, height: maxY - minY };
}

/** Convert a CSS-px length to world units along the CTM's x axis. */
export function cssPxToWorldX(px: number, group: SVGGraphicsElement | null): number {
  const ctm = group?.getScreenCTM();
  if (!ctm) return px;
  const scale = Math.hypot(ctm.a, ctm.b);
  return scale > 0 ? px / scale : px;
}

// ---------------------------------------------------------------------------
// Re-render ticker
// ---------------------------------------------------------------------------

/**
 * Bumps a version whenever anything that can move the CTM happens: container
 * resize (ResizeObserver), window resize/zoom, camera or scene change, or an
 * explicit tick request (e.g. after fonts settle). Updates are rAF-batched
 * so pointer-driven work never thrashes React commits per event.
 */
export function useProjectionVersion(
  watched: HTMLElement | null,
  changeKey: string,
): { version: number; requestTick: () => void } {
  const [version, setVersion] = useState(0);
  const scheduledRef = useRef(false);

  const requestTick = useCallback(() => {
    if (scheduledRef.current) return;
    scheduledRef.current = true;
    requestAnimationFrame(() => {
      scheduledRef.current = false;
      setVersion((v) => v + 1);
    });
  }, []);

  useEffect(() => {
    if (!watched) return;
    const observer = new ResizeObserver(() => requestTick());
    observer.observe(watched);
    const onWindowResize = () => requestTick();
    window.addEventListener("resize", onWindowResize);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", onWindowResize);
    };
  }, [watched, requestTick]);

  useEffect(() => {
    requestTick();
  }, [changeKey, requestTick]);

  return { version, requestTick };
}

/** Exposed for tests: compose a DOMMatrix-compatible affine from an CTM. */
export function affineFromCtm(ctm: DOMMatrix): Affine2D {
  return { a: ctm.a, b: ctm.b, c: ctm.c, d: ctm.d, e: ctm.e, f: ctm.f };
}

export { invertAffine };
