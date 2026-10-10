import test from "node:test";
import assert from "node:assert/strict";
import {
  mathToThree, threeToMath, v3, vadd, vcross, vdot, vnormalize, vsub,
  intersectRayPlane, planeFromPoints, intersectLinePlane, intersectPlanePlane, angleBetweenVectors,
  boxMeasures, sphereMeasures, cylinderMeasures, coneMeasures, prismMeasures,
} from "../src/math-workbench/index.ts";

const near = (a: number, b: number, tol = 1e-9): boolean => Math.abs(a - b) <= tol;

test("Z-up ↔ Y-up adapter roundtrips and maps axes exactly", () => {
  // math(x,y,z) → three(x,z,-y)
  assert.deepEqual(mathToThree(v3(1, 2, 3)), { x: 1, y: 3, z: -2 });
  // three(X,Y,Z) → math(X,-Z,Y)
  assert.deepEqual(threeToMath(v3(1, 3, -2)), { x: 1, y: 2, z: 3 });
  for (const p of [v3(0.3, -1.2, 2.7), v3(-5, 0, 1), v3(1, 1, 1)]) {
    const round = threeToMath(mathToThree(p) as { x: number; y: number; z: number });
    assert.ok(near(round.x, p.x) && near(round.y, p.y) && near(round.z, p.z));
  }
  // math +Z (up) maps to three +Y, math +Y maps to three -Z: handedness kept.
  const zUp = mathToThree(v3(0, 0, 1));
  assert.ok(near(zUp.y, 1) && near(zUp.x, 0) && near(zUp.z, 0));
  const yMath = mathToThree(v3(0, 1, 0));
  assert.ok(near(yMath.z, -1));
  // Cross-product orientation survives the adapter (det +1).
  const ex = mathToThree(v3(1, 0, 0)), ey = mathToThree(v3(0, 1, 0)), ez = mathToThree(v3(0, 0, 1));
  const cross = vcross(ex, ey);
  assert.ok(near(cross.x, ez.x) && near(cross.y, ez.y) && near(cross.z, ez.z));
});

test("vector algebra basics", () => {
  const a = v3(1, 2, 3), b = v3(-4, 0.5, 2);
  assert.equal(vdot(a, b), -4 + 1 + 6);
  assert.deepEqual(vcross(v3(1, 0, 0), v3(0, 1, 0)), { x: 0, y: 0, z: 1 });
  assert.ok(near(Math.hypot(...Object.values(vnormalize(v3(3, 4, 0))) as number[]), 1));
  assert.deepEqual(vadd(v3(1, 1, 1), v3(1, 2, 3)), { x: 2, y: 3, z: 4 });
  assert.deepEqual(vsub(v3(5, 5, 5), v3(1, 2, 3)), { x: 4, y: 3, z: 2 });
});

test("ray-plane and plane-plane intersections", () => {
  const plane = { point: v3(0, 0, 2), normal: v3(0, 0, 1) };
  const hit = intersectRayPlane({ origin: v3(0, 0, 0), direction: v3(0, 0, 1) }, plane);
  assert.ok(hit && near(hit.z, 2));
  const miss = intersectRayPlane({ origin: v3(0, 0, 0), direction: v3(1, 0, 0) }, plane);
  assert.equal(miss, null);
  const lineHit = intersectLinePlane(v3(0, 0, 0), v3(1, 1, 4), plane);
  assert.ok(lineHit && near(lineHit.z, 2) && near(lineHit.x, 0.5));
  const twoPlanes = intersectPlanePlane(plane, { point: v3(0, 0, 0), normal: v3(1, 0, 0) });
  assert.ok(twoPlanes);
  if (twoPlanes) {
    const onBoth = twoPlanes.origin;
    assert.ok(near(onBoth.x, 0) && near(onBoth.z, 2));
    assert.ok(near(Math.hypot(...Object.values(twoPlanes.direction) as number[]), 1));
  }
  const collinear = planeFromPoints(v3(0, 0, 0), v3(1, 1, 1), v3(2, 2, 2));
  assert.equal(collinear, null);
  assert.ok(near(angleBetweenVectors(v3(1, 0, 0), v3(0, 0, 1)), Math.PI / 2));
});

test("solid measures match closed-form values", () => {
  const box = boxMeasures(2, 3, 4);
  assert.ok(near(box.volume, 24) && near(box.surface, 52));
  const sphere = sphereMeasures(2);
  assert.ok(near(sphere.volume, 32 / 3 * Math.PI) && near(sphere.surface, 16 * Math.PI));
  const cyl = cylinderMeasures(1, 5);
  assert.ok(near(cyl.volume, 5 * Math.PI) && near(cyl.surface, 12 * Math.PI));
  const cone = coneMeasures(3, 4);
  assert.ok(near(cone.volume, 12 * Math.PI) && near(cone.surface, 24 * Math.PI));
  const prism = prismMeasures(4, 8, 2); // square base side 2? area 4, perimeter 8
  assert.ok(near(prism.volume, 8) && near(prism.surface, 24));
});
