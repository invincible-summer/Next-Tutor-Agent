/**
 * Pure scene math: affine cameras, slot→world mapping and hit tests for the
 * 2.5D lab bench. No DOM, no React — every function is deterministic and
 * covered by apps/web/tests/unit/test-chem-lab-scene.mjs (node
 * --experimental-strip-types). Runtime imports use explicit .ts extensions
 * so the same files load under the Node type-stripping runner.
 *
 * One coordinate system: the SVG viewBox is the single world space. Slots,
 * equipment, links, ghosts and drop candidates all live here; the camera is
 * an affine transform applied to one world group, and DOM hit targets are
 * projected through the same group's screen CTM (see scene-projection.ts).
 */

export interface Point {
  x: number;
  y: number;
}

export interface WorldRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** SVG-style affine matrix — column-vector form [a c e; b d f; 0 0 1]. */
export interface Affine2D {
  a: number;
  b: number;
  c: number;
  d: number;
  e: number;
  f: number;
}

export const IDENTITY_AFFINE: Affine2D = { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 };

export function applyAffine(m: Affine2D, p: Point): Point {
  return { x: m.a * p.x + m.c * p.y + m.e, y: m.b * p.x + m.d * p.y + m.f };
}

/** Compose two affines: `composeAffine(outer, inner)` applies inner first. */
export function composeAffine(outer: Affine2D, inner: Affine2D): Affine2D {
  return {
    a: outer.a * inner.a + outer.c * inner.b,
    b: outer.b * inner.a + outer.d * inner.b,
    c: outer.a * inner.c + outer.c * inner.d,
    d: outer.b * inner.c + outer.d * inner.d,
    e: outer.a * inner.e + outer.c * inner.f + outer.e,
    f: outer.b * inner.e + outer.d * inner.f + outer.f,
  };
}

/** Inverse of an affine, or null when it is degenerate (det ≈ 0). */
export function invertAffine(m: Affine2D): Affine2D | null {
  const det = m.a * m.d - m.b * m.c;
  if (!Number.isFinite(det) || Math.abs(det) < 1e-12) return null;
  return {
    a: m.d / det,
    b: -m.b / det,
    c: -m.c / det,
    d: m.a / det,
    e: (m.c * m.f - m.d * m.e) / det,
    f: (m.b * m.e - m.a * m.f) / det,
  };
}

export function affineToCssTransform(m: Affine2D): string {
  // CSS matrix() is comma-separated (SVG transform attributes are not).
  return `matrix(${m.a}, ${m.b}, ${m.c}, ${m.d}, ${m.e}, ${m.f})`;
}

// ---------------------------------------------------------------------------
// Camera
// ---------------------------------------------------------------------------

export type CameraMode = "overview" | "focus";

export interface CameraPose {
  mode: CameraMode;
  scale: number;
  translateX: number;
  translateY: number;
  focusedId: string | null;
}

export const OVERVIEW_CAMERA: CameraPose = {
  mode: "overview",
  scale: 1,
  translateX: 0,
  translateY: 0,
  focusedId: null,
};

export function composeCameraMatrix(camera: CameraPose): Affine2D {
  return {
    a: camera.scale,
    b: 0,
    c: 0,
    d: camera.scale,
    e: camera.translateX,
    f: camera.translateY,
  };
}

/**
 * Clamp a camera so the world rectangle (`bounds`) still covers the whole
 * viewport after scaling — no empty voids, no unreachably off-screen
 * content. Pure math; the caller supplies world bounds and the current
 * viewport size in world units at scale 1.
 */
export function boundCamera(camera: CameraPose, bounds: WorldRect, viewport: Point): CameraPose {
  const scale = clamp(camera.scale, 1, 2.5);
  if (!Number.isFinite(scale) || scale <= 0) return { ...OVERVIEW_CAMERA };
  const worldWidth = bounds.width;
  const worldHeight = bounds.height;
  const viewWidth = viewport.x;
  const viewHeight = viewport.y;
  // How much world is visible at this scale; translations must keep the
  // viewport inside [0, world - visible] on each axis when the world is
  // larger than the viewport, otherwise centre the world.
  let translateX = camera.translateX;
  let translateY = camera.translateY;
  const visibleW = viewWidth / scale;
  const visibleH = viewHeight / scale;
  if (visibleW >= worldWidth) {
    translateX = (viewWidth - worldWidth * scale) / 2;
  } else {
    translateX = clamp(translateX, viewWidth - worldWidth * scale, 0);
  }
  if (visibleH >= worldHeight) {
    translateY = (viewHeight - worldHeight * scale) / 2;
  } else {
    translateY = clamp(translateY, viewHeight - worldHeight * scale, 0);
  }
  return {
    mode: camera.mode,
    scale,
    translateX,
    translateY,
    focusedId: camera.focusedId ?? null,
  };
}

/** Focus pose for one node: centre it in the safe zone, gently zoomed in. */
export function focusCameraOn(
  nodeCenter: Point,
  viewport: Point,
  focusedId: string,
  scale = 1.22,
): CameraPose {
  const clampedScale = clamp(scale, 1.05, 1.35);
  return {
    mode: "focus",
    scale: clampedScale,
    translateX: viewport.x / 2 - nodeCenter.x * clampedScale,
    translateY: viewport.y * 0.42 - nodeCenter.y * clampedScale,
    focusedId,
  };
}

// ---------------------------------------------------------------------------
// Hit testing
// ---------------------------------------------------------------------------

export interface HitRect {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

export function pointInRect(rect: HitRect, p: Point, pad = 0): boolean {
  return (
    p.x >= rect.x - pad && p.x <= rect.x + rect.w + pad &&
    p.y >= rect.y - pad && p.y <= rect.y + rect.h + pad
  );
}

/**
 * Slot hit test with a stable tie-break: when point sits on a shared edge of
 * several slots the *smaller* slot wins (tighter containment), then the
 * lexicographically smaller id. Deterministic across renders and engines.
 */
export function hitTestWorldSlot(slots: HitRect[], p: Point): HitRect | null {
  const containing = slots.filter((slot) => pointInRect(slot, p));
  if (!containing.length) return null;
  containing.sort((s1, s2) => {
    const area = s1.w * s1.h - s2.w * s2.h;
    if (area !== 0) return area;
    return s1.id < s2.id ? -1 : s1.id > s2.id ? 1 : 0;
  });
  return containing[0] ?? null;
}

/** Distance from a point to a rect's centre — used for nearest-object wins. */
export function distanceToRectCenter(rect: HitRect, p: Point): number {
  const dx = rect.x + rect.w / 2 - p.x;
  const dy = rect.y + rect.h / 2 - p.y;
  return Math.sqrt(dx * dx + dy * dy);
}

// ---------------------------------------------------------------------------
// Pointer thresholds (CSS px; acceptance defaults, not physical constants)
// ---------------------------------------------------------------------------

export type LabPointerType = "mouse" | "touch" | "pen";

export const DRAG_START_THRESHOLD_PX: Record<LabPointerType, number> = {
  mouse: 6,
  touch: 10,
  pen: 7,
};

/** Screen-space edge tolerance for drop snapping (converted to world at use). */
export const DROP_EDGE_TOLERANCE_PX = 12;

// ---------------------------------------------------------------------------
// Small shared helpers
// ---------------------------------------------------------------------------

export function clamp(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.min(max, Math.max(min, value));
}

/** Deterministic 0..1 hash scatter — same idea as the retired assets.tsx. */
export function scatter(seed: string, index: number): number {
  let hash = 2166136261;
  const text = `${seed}:${index}`;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return ((hash >>> 0) % 1000) / 1000;
}
