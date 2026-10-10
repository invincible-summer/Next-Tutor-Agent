/**
 * 3D math kernel: vectors, rays, planes, standard solid measures and the
 * right-handed math-space → Three.js space adapter. Domain math is ALWAYS
 * right-handed (x,y,z) with z up; the Web renderer converts once at the
 * boundary via `mathToThree`/`threeToMath` (plan G1, determinant +1).
 * Solid-geometry construction objects were removed with the geometry3d mode
 * (ADR-0022); the pure measures below stay part of the math kernel surface.
 */

import { nearlyEqual } from "./diagnostics.ts";
import type { Pt3 } from "./model.ts";

export type { Pt3 } from "./model.ts";

/* ---------------------------------- vec3 ---------------------------------- */

export function v3(x: number, y: number, z: number): Pt3 { return { x, y, z }; }
export function vadd(a: Pt3, b: Pt3): Pt3 { return { x: a.x + b.x, y: a.y + b.y, z: a.z + b.z }; }
export function vsub(a: Pt3, b: Pt3): Pt3 { return { x: a.x - b.x, y: a.y - b.y, z: a.z - b.z }; }
export function vscale(a: Pt3, k: number): Pt3 { return { x: a.x * k, y: a.y * k, z: a.z * k }; }
export function vdot(a: Pt3, b: Pt3): number { return a.x * b.x + a.y * b.y + a.z * b.z; }
export function vcross(a: Pt3, b: Pt3): Pt3 {
  return { x: a.y * b.z - a.z * b.y, y: a.z * b.x - a.x * b.z, z: a.x * b.y - a.y * b.x };
}
export function vlen(a: Pt3): number { return Math.hypot(a.x, a.y, a.z); }
export function vdist(a: Pt3, b: Pt3): number { return vlen(vsub(a, b)); }
export function vnormalize(a: Pt3): Pt3 {
  const len = vlen(a);
  if (len < 1e-15) return { x: 0, y: 0, z: 0 };
  return vscale(a, 1 / len);
}
export function vnearlyEqual(a: Pt3, b: Pt3, tol = 1e-9): boolean {
  return nearlyEqual(a.x, b.x, tol) && nearlyEqual(a.y, b.y, tol) && nearlyEqual(a.z, b.z, tol);
}

/* ---------------------------- space adapter (G1) ---------------------------- */

/** math(x,y,z) → three(X,Y,Z) = (x, z, -y); determinant +1, handedness kept. */
export function mathToThree(p: Pt3): { x: number; y: number; z: number } {
  return { x: p.x, y: p.z, z: -p.y };
}

/** three(X,Y,Z) → math(x,y,z) = (X, -Z, Y). */
export function threeToMath(p: Pt3): { x: number; y: number; z: number } {
  return { x: p.x, y: -p.z, z: p.y };
}

/* ---------------------------------- rays ---------------------------------- */

export interface Ray3 { origin: Pt3; direction: Pt3 }

export function rayPoint(ray: Ray3, t: number): Pt3 { return vadd(ray.origin, vscale(ray.direction, t)); }

/** Nearest t on the ray/line to p (unclamped). */
export function closestPointOnLine(ray: Ray3, p: Pt3): { t: number; point: Pt3 } {
  const dir2 = vdot(ray.direction, ray.direction);
  if (dir2 < 1e-30) return { t: 0, point: ray.origin };
  const t = vdot(vsub(p, ray.origin), ray.direction) / dir2;
  return { t, point: rayPoint(ray, t) };
}

/** Ray–plane intersection; null when parallel or (for rays) behind. */
export function intersectRayPlane(ray: Ray3, plane: { point: Pt3; normal: Pt3 }, allowNegativeT = true): Pt3 | null {
  const denom = vdot(plane.normal, ray.direction);
  if (Math.abs(denom) < 1e-12) return null;
  const t = vdot(vsub(plane.point, ray.origin), plane.normal) / denom;
  if (!allowNegativeT && t < 0) return null;
  return rayPoint(ray, t);
}

/** Ray–sphere: nearest positive hit or null. */
export function intersectRaySphere(ray: Ray3, center: Pt3, radius: number): Pt3 | null {
  const oc = vsub(ray.origin, center);
  const b = 2 * vdot(oc, ray.direction);
  const c = vdot(oc, oc) - radius * radius;
  const disc = b * b - 4 * c;
  if (disc < 0) return null;
  const root = Math.sqrt(disc);
  const t1 = (-b - root) / 2;
  const t2 = (-b + root) / 2;
  const t = t1 >= 0 ? t1 : t2 >= 0 ? t2 : null;
  return t === null ? null : rayPoint(ray, t);
}

/* --------------------------------- planes --------------------------------- */

/** Plane through three points; null when collinear. */
export function planeFromPoints(a: Pt3, b: Pt3, c: Pt3): { point: Pt3; normal: Pt3 } | null {
  const normal = vcross(vsub(b, a), vsub(c, a));
  if (vlen(normal) < 1e-12) return null;
  return { point: a, normal: vnormalize(normal) };
}

export function pointToPlaneDistance(p: Pt3, plane: { point: Pt3; normal: Pt3 }): number {
  return Math.abs(vdot(vsub(p, plane.point), plane.normal));
}

/** Line–plane intersection; null when parallel. */
export function intersectLinePlane(a: Pt3, b: Pt3, plane: { point: Pt3; normal: Pt3 }): Pt3 | null {
  const dir = vsub(b, a);
  const denom = vdot(plane.normal, dir);
  if (Math.abs(denom) < 1e-12) return null;
  const t = vdot(vsub(plane.point, a), plane.normal) / denom;
  return vadd(a, vscale(dir, t));
}

/** Plane–plane intersection line; null when parallel/coincident. */
export function intersectPlanePlane(p1: { point: Pt3; normal: Pt3 }, p2: { point: Pt3; normal: Pt3 }): Ray3 | null {
  const dir = vcross(p1.normal, p2.normal);
  if (vlen(dir) < 1e-12) return null;
  const n1n2 = vdot(p1.normal, p2.normal);
  const det = 1 - n1n2 * n1n2;
  if (Math.abs(det) < 1e-12) return null;
  const d1 = vdot(p1.normal, p1.point);
  const d2 = vdot(p2.normal, p2.point);
  const c1 = (d1 - n1n2 * d2) / det;
  const c2 = (d2 - n1n2 * d1) / det;
  const point = vadd(vscale(p1.normal, c1), vscale(p2.normal, c2));
  return { origin: point, direction: vnormalize(dir) };
}

export function angleBetweenVectors(a: Pt3, b: Pt3): number {
  const cos = vdot(a, b) / (vlen(a) * vlen(b));
  return Math.acos(Math.min(1, Math.max(-1, cos)));
}

/* --------------------------- standard solid measures --------------------------- */

export function boxMeasures(dx: number, dy: number, dz: number): { volume: number; surface: number } {
  return { volume: Math.abs(dx * dy * dz), surface: 2 * (Math.abs(dx * dy) + Math.abs(dx * dz) + Math.abs(dy * dz)) };
}
export function sphereMeasures(r: number): { volume: number; surface: number } {
  return { volume: (4 / 3) * Math.PI * r ** 3, surface: 4 * Math.PI * r * r };
}
export function cylinderMeasures(r: number, h: number): { volume: number; surface: number } {
  return { volume: Math.PI * r * r * Math.abs(h), surface: 2 * Math.PI * r * Math.abs(h) + 2 * Math.PI * r * r };
}
export function coneMeasures(r: number, h: number): { volume: number; surface: number } {
  const slant = Math.hypot(r, h);
  return { volume: (Math.PI * r * r * Math.abs(h)) / 3, surface: Math.PI * r * slant + Math.PI * r * r };
}
/** Right-prism measures: volume = base area × h, surface = 2×base + lateral wall. */
export function prismMeasures(baseArea: number, perimeter: number, h: number): { volume: number; surface: number } {
  return { volume: baseArea * Math.abs(h), surface: 2 * baseArea + perimeter * Math.abs(h) };
}
