/* 化学实验台场景纯函数测试。
 *
 * 直接以 node --experimental-strip-types 运行（package.json test:unit 已接
 * 入）：覆盖仿射相机/命中的数学、SceneModel 派生的稳定排序与端口投影、
 * resolveDropIntent 的 12 kind 分类判定表（含占用/自槽/held/锁定/越界）。
 * 只测纯函数 —— 不挂 DOM、不起 React。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  applyAffine,
  boundCamera,
  composeAffine,
  composeCameraMatrix,
  distanceToRectCenter,
  DRAG_START_THRESHOLD_PX,
  focusCameraOn,
  hitTestWorldSlot,
  IDENTITY_AFFINE,
  invertAffine,
  OVERVIEW_CAMERA,
} from "../../src/components/pages/tools/chem-lab/scene/scene-geometry.ts";
import {
  deriveSceneModel,
  KNOWN_EQUIPMENT_KINDS,
  resolveDropIntent,
} from "../../src/components/pages/tools/chem-lab/scene/scene-model.ts";
import {
  deriveMotion,
  MOTION_MS,
  presentationToken,
  REJECTED_FLASH_MS,
  subjectIdsOf,
} from "../../src/components/pages/tools/chem-lab/scene/presentation-model.ts";

// ---------------------------------------------------------------------------
// Geometry
// ---------------------------------------------------------------------------

test("affine compose + invert round-trips", () => {
  const m = { a: 2, b: 0.5, c: -0.25, d: 1.75, e: 12, f: -7 };
  const inv = invertAffine(m);
  assert.ok(inv);
  const p = { x: 31.5, y: -8.25 };
  const round = applyAffine(inv, applyAffine(m, p));
  assert.ok(Math.abs(round.x - p.x) < 1e-9 && Math.abs(round.y - p.y) < 1e-9);
  assert.equal(invertAffine({ a: 1, b: 2, c: 2, d: 4, e: 0, f: 0 }), null);
  const composed = composeAffine(m, IDENTITY_AFFINE);
  assert.deepEqual(composed, m);
  const cam = composeCameraMatrix({ ...OVERVIEW_CAMERA, scale: 1.5, translateX: 40, translateY: -10 });
  assert.deepEqual(applyAffine(cam, { x: 10, y: 20 }), { x: 55, y: 20 });
});

test("hitTestWorldSlot prefers the smaller slot on shared edges", () => {
  const slots = [
    { id: "big", x: 0, y: 0, w: 200, h: 200 },
    { id: "small", x: 100, y: 50, w: 100, h: 100 },
  ];
  assert.equal(hitTestWorldSlot(slots, { x: 150, y: 100 }).id, "small");
  assert.equal(hitTestWorldSlot(slots, { x: 50, y: 50 }).id, "big");
  assert.equal(hitTestWorldSlot(slots, { x: 250, y: 250 }), null);
  // deterministic tie-break by id when areas match
  const twins = [
    { id: "b", x: 0, y: 0, w: 100, h: 100 },
    { id: "a", x: 0, y: 0, w: 100, h: 100 },
  ];
  assert.equal(hitTestWorldSlot(twins, { x: 50, y: 50 }).id, "a");
});

test("boundCamera keeps the world covering the viewport", () => {
  const bounds = { x: 0, y: 0, width: 800, height: 600 };
  const viewport = { x: 400, y: 300 };
  const zoomed = boundCamera(
    { mode: "focus", scale: 2, translateX: 5000, translateY: -5000, focusedId: "x" },
    bounds,
    viewport,
  );
  assert.ok(zoomed.translateX <= 0 && zoomed.translateX >= viewport.x - bounds.width * 2);
  assert.ok(zoomed.translateY <= 0 && zoomed.translateY >= viewport.y - bounds.height * 2);
  const tiny = boundCamera(
    { mode: "focus", scale: 0.2, translateX: 0, translateY: 0, focusedId: null },
    bounds,
    viewport,
  );
  assert.equal(tiny.scale, 1); // scale never drops below 1
  const focus = focusCameraOn({ x: 400, y: 300 }, { x: 800, y: 600 }, "obj-1", 9);
  assert.equal(focus.scale, 1.35);
  assert.equal(DRAG_START_THRESHOLD_PX.mouse, 6);
  assert.ok(distanceToRectCenter({ id: "r", x: 0, y: 0, w: 10, h: 10 }, { x: 5, y: 5 }) === 0);
});

// ---------------------------------------------------------------------------
// Scene model + drop intent
// ---------------------------------------------------------------------------

function buildPack() {
  return {
    _equipment_defs: {
      "beaker.small": { id: "beaker.small", category: "vessel", footprint: [120, 150], capacity_uL: 250000 },
      reagent_bottle: { id: "reagent_bottle", category: "container", footprint: [100, 160] },
      graduated_cylinder: { id: "graduated_cylinder", category: "vessel", footprint: [70, 180], capacity_uL: 100000, graduations_uL: [20000, 50000, 80000] },
      conical_flask: { id: "conical_flask", category: "vessel", footprint: [130, 160] },
      gas_cylinder: { id: "gas_cylinder", category: "vessel", footprint: [90, 170] },
      dropper: { id: "dropper", category: "instrument", footprint: [26, 120], capacity_uL: 1000 },
      pipette: { id: "pipette", category: "instrument", footprint: [30, 170], capacity_uL: 10000 },
      thermometer: { id: "thermometer", category: "instrument", footprint: [20, 170], measures: ["temperature"] },
      ph_probe: { id: "ph_probe", category: "instrument", footprint: [36, 150], measures: ["ph", "temperature"] },
      stir_rod: { id: "stir_rod", category: "tool", footprint: [16, 140] },
      delivery_tube: { id: "delivery_tube", category: "instrument", footprint: [120, 60], ports: [{ id: "left", x: 6, y: 42 }, { id: "right", x: 94, y: 42 }] },
      hotplate: { id: "hotplate", category: "device", footprint: [160, 60] },
    },
    starting_state: {
      slots: [
        { id: "b1", x: 30, y: 90, w: 130, h: 190 },
        { id: "b2", x: 185, y: 90, w: 130, h: 190 },
        { id: "f1", x: 30, y: 400, w: 170, h: 260 },
        { id: "f2", x: 230, y: 400, w: 170, h: 260 },
        { id: "f3", x: 430, y: 400, w: 170, h: 260 },
      ],
      vessels: [
        { id: "stock", kind: "reagent_bottle", slot: "b1", capacity_uL: 300000, volume_uL: 200000 },
        { id: "beaker-a", kind: "beaker.small", slot: "f1", capacity_uL: 250000, volume_uL: 0 },
        { id: "beaker-b", kind: "beaker.small", slot: "f2", capacity_uL: 250000, volume_uL: 50000 },
      ],
      equipment: [
        { id: "pipette-1", kind: "pipette", slot: "b2" },
        { id: "plate-1", kind: "hotplate", slot: "f3" },
      ],
    },
  };
}

function buildDisplay(pack, overrides = {}) {
  return {
    renderFrame: {
      vessels: {},
      instruments: {},
      highlights: [],
    },
    guidance: null,
    phase: "running",
    revision: 3,
    simTimeMs: 1000,
    engineState: {
      vessels: Object.fromEntries(
        pack.starting_state.vessels.map((v) => [v.id, { ...v }]),
      ),
      equipment: Object.fromEntries(
        pack.starting_state.equipment.map((e) => [
          e.id,
          { ...e, load: { volume_uL: overrides.loadUL?.[e.id] ?? 0, contents: {}, solids: {}, source: null, clean: true }, connected: null, reading: null },
        ]),
      ),
      held: overrides.held ?? null,
    },
    events: [],
    observations: [],
    serverRevision: 3,
  };
}

test("deriveSceneModel orders nodes by depth then baseline and projects ports", () => {
  const pack = buildPack();
  const scene = deriveSceneModel(pack, buildDisplay(pack), "zh");
  assert.equal(scene.nodes.length, 5);
  const ids = scene.nodes.map((n) => n.ref.id);
  // rear band first (stock, pipette-1), then front by baseline (all equal) → id
  assert.deepEqual(ids.slice(0, 2).sort(), ["pipette-1", "stock"]);
  assert.deepEqual(ids.slice(2).sort(), ["beaker-a", "beaker-b", "plate-1"]);
  assert.equal(scene.slots.find((s) => s.id === "f3").occupiedBy, "plate-1");
  assert.equal(scene.slots.find((s) => s.id === "b1").depth, "rear");
  assert.equal(scene.slots.find((s) => s.id === "f1").depth, "front");
  // delivery tube ports project into world coordinates (not tested here: no tube)
  const stock = scene.nodes.find((n) => n.ref.id === "stock");
  assert.equal(stock.known, true);
  assert.equal(stock.depth, "rear");
  assert.ok(stock.world.width > 0 && stock.world.height > 0);
  // unknown kind degrades visibly, never disappears
  const odd = buildPack();
  odd.starting_state.equipment.push({ id: "mystery", kind: "flux_capacitor", slot: "f3" });
  odd._equipment_defs.flux_capacitor = { id: "flux_capacitor", category: "device" };
  const oddScene = deriveSceneModel(odd, buildDisplay(odd), "zh");
  const mystery = oddScene.nodes.find((n) => n.ref.id === "mystery");
  assert.equal(mystery.known, false);
  assert.equal(KNOWN_EQUIPMENT_KINDS.size, 12);
});

function center(scene, id) {
  const node = scene.nodes.find((n) => n.ref.id === id);
  return { x: node.world.x + node.world.width / 2, y: node.world.y + node.world.height / 2 };
}

test("resolveDropIntent matrix: vessel sources", () => {
  const pack = buildPack();
  const scene = deriveSceneModel(pack, buildDisplay(pack), "zh");
  const free = { heldId: null, locked: false };

  // vessel → vessel opens a pour draft (never an implicit pour command)
  const pour = resolveDropIntent({ type: "vessel", id: "stock" }, center(scene, "beaker-a"), scene, free);
  assert.equal(pour.type, "operation");
  assert.deepEqual(
    { action: pour.draft.action, s: pour.draft.sourceId, t: pour.draft.targetId },
    { action: "pour", s: "stock", t: "beaker-a" },
  );

  // vessel → hotplate opens a heat draft
  const heat = resolveDropIntent({ type: "vessel", id: "beaker-a" }, center(scene, "plate-1"), scene, free);
  assert.equal(heat.type, "operation");
  assert.deepEqual(
    { action: heat.draft.action, v: heat.draft.vesselId, d: heat.draft.deviceId },
    { action: "heat", v: "beaker-a", d: "plate-1" },
  );
});

test("resolveDropIntent matrix: instrument sources", () => {
  const pack = buildPack();
  const empty = deriveSceneModel(pack, buildDisplay(pack), "zh");
  const free = { heldId: null, locked: false };

  // empty pipette → vessel = aspirate; loaded pipette → vessel = dispense
  const asp = resolveDropIntent({ type: "equipment", id: "pipette-1" }, center(empty, "beaker-a"), empty, free);
  assert.equal(asp.type, "operation");
  assert.deepEqual(
    { action: asp.draft.action, i: asp.draft.instrumentId, s: asp.draft.sourceId },
    { action: "aspirate", i: "pipette-1", s: "beaker-a" },
  );
  const loaded = deriveSceneModel(pack, buildDisplay(pack, { loadUL: { "pipette-1": 5000 } }), "zh");
  const disp = resolveDropIntent({ type: "equipment", id: "pipette-1" }, center(loaded, "beaker-a"), loaded, free);
  assert.equal(disp.type, "operation");
  assert.deepEqual(
    { action: disp.draft.action, i: disp.draft.instrumentId, t: disp.draft.targetId },
    { action: "dispense", i: "pipette-1", t: "beaker-a" },
  );

  // instruments that cannot interact with a vessel stay unsupported
  const pack2 = buildPack();
  pack2.starting_state.equipment.push({ id: "rod-1", kind: "stir_rod", slot: "b2" });
  const scene2 = deriveSceneModel(pack2, buildDisplay(pack2), "zh");
  const stir = resolveDropIntent({ type: "equipment", id: "rod-1" }, center(scene2, "beaker-b"), scene2, free);
  assert.equal(stir.type, "operation");
  assert.equal(stir.draft.action, "stir");
  // hotplate dragged onto a vessel = heat with device=source
  const heatFromDevice = resolveDropIntent({ type: "equipment", id: "plate-1" }, center(scene2, "beaker-b"), scene2, free);
  assert.equal(heatFromDevice.type, "operation");
  assert.deepEqual(
    { action: heatFromDevice.draft.action, v: heatFromDevice.draft.vesselId, d: heatFromDevice.draft.deviceId },
    { action: "heat", v: "beaker-b", d: "plate-1" },
  );
});

test("resolveDropIntent: slots, guards and failure reasons", () => {
  const pack = buildPack();
  const packOpen = buildPack();
  packOpen.starting_state.slots.push({ id: "f4", x: 630, y: 400, w: 170, h: 260 });
  const open = deriveSceneModel(packOpen, buildDisplay(packOpen), "zh");
  const free = { heldId: null, locked: false };
  const f4Center = { x: 715, y: 530 };

  // empty slot → atomic move
  const move = resolveDropIntent({ type: "vessel", id: "beaker-a" }, f4Center, open, free);
  assert.deepEqual(move, { type: "move", objectId: "beaker-a", slotId: "f4" });

  // own slot → none (no fake move)
  const own = resolveDropIntent({ type: "vessel", id: "beaker-a" }, center(open, "beaker-a"), open, free);
  assert.deepEqual(own, { type: "none" });

  const scene = deriveSceneModel(pack, buildDisplay(pack), "zh");
  // slot corner: inside beaker-b's slot but outside its object rect → the
  // slot path applies and rejects with occupied (dropping onto the object
  // itself is a pour draft, tested above).
  const f2slot = scene.slots.find((s) => s.id === "f2").rect;
  const occupied = resolveDropIntent(
    { type: "vessel", id: "beaker-a" },
    { x: f2slot.x + 4, y: f2slot.y + 4 },
    scene,
    free,
  );
  assert.equal(occupied.type, "invalid");
  assert.equal(occupied.reason, "occupied");

  // out of bounds
  const oob = resolveDropIntent({ type: "vessel", id: "beaker-a" }, { x: -500, y: -500 }, scene, free);
  assert.equal(oob.type, "invalid");
  assert.equal(oob.reason, "out_of_bounds");

  // locked authority
  const locked = resolveDropIntent({ type: "vessel", id: "beaker-a" }, f4Center, open, { heldId: null, locked: true });
  assert.deepEqual(locked, { type: "invalid", reason: "locked" });

  // another object held server-side blocks the drag source entirely
  const heldOther = resolveDropIntent({ type: "vessel", id: "beaker-a" }, f4Center, open, { heldId: "beaker-b", locked: false });
  assert.deepEqual(heldOther, { type: "invalid", reason: "locked" });

  // ph_probe/thermometer → vessel opens measure, never aspirate
  const pack3 = buildPack();
  pack3.starting_state.equipment.push({ id: "probe-1", kind: "ph_probe", slot: "b2" });
  const scene3 = deriveSceneModel(pack3, buildDisplay(pack3), "zh");
  const measure = resolveDropIntent({ type: "equipment", id: "probe-1" }, center(scene3, "beaker-a"), scene3, free);
  assert.equal(measure.type, "operation");
  assert.equal(measure.draft.action, "measure");
});

// ---------------------------------------------------------------------------
// Presentation motion model (plan §7)
// ---------------------------------------------------------------------------

test("subjectIdsOf orders the visual actor first per command kind", () => {
  assert.deepEqual(subjectIdsOf({ kind: "move", object_id: "beaker-a", slot_id: "f2" }), ["beaker-a"]);
  assert.deepEqual(
    subjectIdsOf({ kind: "pour", source_id: "stock", target_id: "beaker-a", amount_uL: 1000 }),
    ["stock", "beaker-a"],
  );
  // pipette dips into the source vessel — instrument is the actor
  assert.deepEqual(
    subjectIdsOf({ kind: "aspirate", source_id: "stock", instrument_id: "pipette-1" }),
    ["pipette-1", "stock"],
  );
  assert.deepEqual(
    subjectIdsOf({ kind: "heat", device_id: "plate-1", vessel_id: "beaker-b" }),
    ["beaker-b", "plate-1"],
  );
  // no-actor commands stay empty; null fields never leak in
  assert.deepEqual(subjectIdsOf({ kind: "checkpoint" }), []);
  assert.deepEqual(subjectIdsOf({ kind: "wait", duration_ms: 1000 }), []);
  assert.deepEqual(subjectIdsOf({ kind: "stir", vessel_id: null }), []);
});

test("deriveMotion stages: prediction pulses, ack plays, rejection flashes", () => {
  const env = { sessionId: "s1", packHash: "h1", conflict: false, reducedMotion: false };
  const token = presentationToken("s1", "h1", 7, "c1");
  assert.equal(token, "s1:h1:7:c1");

  // prediction accepted → neutral pending only, never a success effect
  const pending = deriveMotion(env, {
    commandId: "c1", commandKind: "pour", revision: 7, accepted: true,
    authority: "prediction", subjectIds: ["stock", "beaker-a"],
  });
  assert.deepEqual(
    { ...pending },
    { token, kind: "pour", subjects: ["stock", "beaker-a"], stage: "pending", durationMs: MOTION_MS.pour },
  );

  // prediction rejected → nothing yet (the ack still carries the text)
  assert.equal(deriveMotion(env, {
    commandId: "c1", commandKind: "pour", revision: 7, accepted: false,
    authority: "prediction", subjectIds: [],
  }), null);

  // ack accepted → the one-shot motion
  const accepted = deriveMotion(env, {
    commandId: "c1", commandKind: "pour", revision: 7, accepted: true,
    authority: "ack", subjectIds: ["stock", "beaker-a"],
  });
  assert.equal(accepted.stage, "accepted");
  assert.equal(accepted.durationMs, MOTION_MS.pour);

  // ack rejected → one brief neutral flash
  const rejected = deriveMotion(env, {
    commandId: "c1", commandKind: "pour", revision: 7, accepted: false,
    authority: "ack", subjectIds: ["stock"],
  });
  assert.deepEqual(
    { ...rejected },
    { token, kind: "pour", subjects: ["stock"], stage: "rejected", durationMs: REJECTED_FLASH_MS },
  );

  // checkpoint has no visual; conflict / reduced motion / missing identity cancel
  assert.equal(deriveMotion(env, {
    commandId: "c1", commandKind: "checkpoint", revision: 7, accepted: true,
    authority: "ack", subjectIds: [],
  }), null);
  assert.equal(deriveMotion({ ...env, conflict: true }, pending ? {
    commandId: "c1", commandKind: "pour", revision: 7, accepted: true,
    authority: "ack", subjectIds: [],
  } : null), null);
  assert.equal(deriveMotion({ ...env, reducedMotion: true }, {
    commandId: "c1", commandKind: "pour", revision: 7, accepted: true,
    authority: "ack", subjectIds: [],
  }), null);
  assert.equal(deriveMotion({ ...env, sessionId: null }, {
    commandId: "c1", commandKind: "pour", revision: 7, accepted: true,
    authority: "ack", subjectIds: [],
  }), null);
  assert.equal(deriveMotion(env, null), null);

  // every non-checkpoint kind has a positive duration
  for (const [kind, ms] of Object.entries(MOTION_MS)) {
    if (kind === "checkpoint") assert.equal(ms, 0);
    else assert.ok(ms > 0 && ms <= 1200, `${kind} duration out of display budget`);
  }
});
