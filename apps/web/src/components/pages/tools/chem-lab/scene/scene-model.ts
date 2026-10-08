/**
 * Pure read-only adapter: pack + display (+ language) → SceneModel.
 *
 * The scene never owns state: nodes/slots/links are derived per render from
 * the authoritative display (server snapshot or worker prediction) and the
 * pinned content pack. Object order (z) is computed here — depth band first,
 * then baseline, then stable id — so occlusion never depends on React
 * insertion order. `resolveDropIntent` normalises pointer/keyboard/touch
 * targeting into a UI-only DropCandidate; it never builds a LabCommand and
 * never mutates anything.
 *
 * Runtime imports use explicit .ts extensions (Node strip-types test runner);
 * alias imports are type-only and erased.
 */
import {
  footprintOf,
  placement,
  portPoint,
  readSlots,
  slotById,
  stageViewport,
  type EquipmentPlacement,
  type SlotRect,
} from "../chem-lab-geometry.ts";
import { vesselVisual, type InstrumentVisual, type VesselVisual } from "../chem-lab-renderer.ts";
import type { ChemLabObjectRef, OperationDraft } from "../interaction";
import type { ChemLabDisplay } from "../useChemLabSession";
import {
  distanceToRectCenter,
  hitTestWorldSlot,
  type HitRect,
  type Point,
} from "./scene-geometry.ts";

type AnyRecord = Record<string, unknown>;

export type LabDepth = "rear" | "front";

export interface SceneNode {
  ref: ChemLabObjectRef;
  kind: string;
  category: string;
  label: string;
  slotId: string;
  footprint: readonly [number, number];
  world: { x: number; y: number; width: number; height: number; baseline: number };
  depth: LabDepth;
  zOrder: number;
  ports: ReadonlyArray<{ id: string; x: number; y: number }>;
  vessel: VesselVisual | null;
  instrument: InstrumentVisual | null;
  /** Transfer-instrument load (µL) — feeds aspirate/dispense intent choice. */
  loadUL: number;
  /** Declared capacity (µL) from the equipment def, when present. */
  capacityUL: number;
  /** Quantities this node's def can measure (thermometer/ph_probe/cylinder…). */
  measures: readonly string[];
  known: boolean;
}

export interface SceneSlotView {
  id: string;
  rect: SlotRect;
  depth: LabDepth;
  occupiedBy: string | null;
}

export interface SceneLink {
  equipmentId: string;
  fromId: string;
  toId: string;
}

export interface SceneModel {
  nodes: readonly SceneNode[];
  slots: readonly SceneSlotView[];
  links: readonly SceneLink[];
  bounds: { width: number; height: number };
  heldId: string | null;
}

/** The twelve kinds the art dispatch draws natively (content-owned ids). */
export const KNOWN_EQUIPMENT_KINDS: ReadonlySet<string> = new Set([
  "beaker.small", "reagent_bottle", "graduated_cylinder", "conical_flask", "gas_cylinder",
  "dropper", "pipette", "thermometer", "ph_probe", "stir_rod", "delivery_tube", "hotplate",
]);

const CATEGORY_BY_KIND: Record<string, string> = {
  "beaker.small": "vessel",
  reagent_bottle: "container",
  graduated_cylinder: "vessel",
  conical_flask: "vessel",
  gas_cylinder: "vessel",
  dropper: "instrument",
  pipette: "instrument",
  thermometer: "instrument",
  ph_probe: "instrument",
  stir_rod: "tool",
  delivery_tube: "instrument",
  hotplate: "device",
};

function l10n(entry: unknown, language: string): string {
  if (entry && typeof entry === "object" && !Array.isArray(entry)) {
    const row = entry as AnyRecord;
    const value = row[language] ?? row.zh ?? row.en;
    if (typeof value === "string") return value;
  }
  return typeof entry === "string" ? entry : "";
}

/** Derive the read-only scene projection. Pure: same inputs → same model. */
export function deriveSceneModel(
  pack: AnyRecord | null,
  display: ChemLabDisplay,
  language: string,
): SceneModel {
  const slots = readSlots(pack);
  const viewport = stageViewport(slots);
  const equipmentDefs = (pack?._equipment_defs ?? pack?.equipment_defs ?? {}) as AnyRecord;
  const start = (pack?.starting_state ?? {}) as AnyRecord;
  const startingVessels = new Map<string, AnyRecord>();
  for (const row of Array.isArray(start.vessels) ? (start.vessels as AnyRecord[]) : []) {
    startingVessels.set(String(row.id ?? ""), row);
  }

  const state = (display.engineState ?? {}) as AnyRecord;
  const vessels = (state.vessels ?? {}) as AnyRecord;
  const equipment = (state.equipment ?? {}) as AnyRecord;
  const instrumentRows = instrumentMap(display);

  const midline = viewport.height * 0.5;
  const nodes: SceneNode[] = [];
  for (const id of Object.keys(vessels).sort()) {
    const vessel = vessels[id] as AnyRecord;
    nodes.push(
      nodeFrom({
        ref: { type: "vessel", id },
        kind: String(vessel.kind ?? ""),
        label: l10n(startingVessels.get(id)?.label, language) || id,
        slotId: String(vessel.slot ?? ""),
        slots,
        equipmentDefs,
        depthMidline: midline,
        vessel: vesselVisual(pack, display.renderFrame, state, id, language),
        instrument: instrumentRows.get(id) ?? null,
        loadUL: 0,
      }),
    );
  }
  for (const id of Object.keys(equipment).sort()) {
    const item = equipment[id] as AnyRecord;
    const kind = String(item.kind ?? "");
    const def = equipmentDefs[kind] as AnyRecord | undefined;
    nodes.push(
      nodeFrom({
        ref: { type: "equipment", id },
        kind,
        label: l10n(def?.name, language) || id,
        slotId: String(item.slot ?? ""),
        slots,
        equipmentDefs,
        depthMidline: midline,
        vessel: null,
        instrument: instrumentRows.get(id) ?? null,
        loadUL: Number(((item.load ?? {}) as AnyRecord).volume_uL ?? 0),
      }),
    );
  }

  // Stable occlusion order: rear band first, then baseline (lower = nearer),
  // then id. Assigning explicit zOrder keeps the DOM overlay deterministic.
  const depthRank: Record<LabDepth, number> = { rear: 0, front: 1 };
  nodes.sort((n1, n2) => {
    const band = depthRank[n1.depth] - depthRank[n2.depth];
    if (band !== 0) return band;
    if (n1.world.baseline !== n2.world.baseline) return n1.world.baseline - n2.world.baseline;
    return n1.ref.id < n2.ref.id ? -1 : n1.ref.id > n2.ref.id ? 1 : 0;
  });
  nodes.forEach((node, index) => {
    node.zOrder = index;
  });

  const occupied = new Map<string, string>();
  for (const node of nodes) occupied.set(node.slotId, node.ref.id);

  const slotViews: SceneSlotView[] = slots.map((rect) => ({
    id: rect.id,
    rect,
    depth: rect.y < midline ? "rear" : "front",
    occupiedBy: occupied.get(rect.id) ?? null,
  }));

  const links: SceneLink[] = [];
  for (const [id, item] of Object.entries(equipment)) {
    const connected = (item as AnyRecord).connected as AnyRecord | null;
    if (connected?.source && connected?.target) {
      links.push({ equipmentId: id, fromId: String(connected.source), toId: String(connected.target) });
    }
  }

  return {
    nodes,
    slots: slotViews,
    links,
    bounds: { width: viewport.width, height: viewport.height },
    heldId: typeof state.held === "string" && state.held ? state.held : null,
  };
}

function instrumentMap(display: ChemLabDisplay): Map<string, InstrumentVisual> {
  const map = new Map<string, InstrumentVisual>();
  const instruments = (display.renderFrame?.instruments ?? {}) as AnyRecord;
  for (const id of Object.keys(instruments).sort()) {
    const row = instruments[id] as AnyRecord;
    map.set(id, {
      equipmentId: id,
      reading: String(row.reading ?? ""),
      unit: String(row.unit ?? ""),
      status: String(row.status ?? "idle"),
    });
  }
  return map;
}

interface NodeSeed {
  ref: ChemLabObjectRef;
  kind: string;
  label: string;
  slotId: string;
  slots: SlotRect[];
  equipmentDefs: AnyRecord;
  depthMidline: number;
  vessel: VesselVisual | null;
  instrument: InstrumentVisual | null;
  loadUL: number;
}

function nodeFrom(seed: NodeSeed): SceneNode {
  const def = seed.equipmentDefs[seed.kind] as AnyRecord | undefined;
  const footprint = footprintOf(def);
  const slot = slotById(seed.slots, seed.slotId);
  const place: EquipmentPlacement = placement(slot, footprint);
  const ports: Array<{ id: string; x: number; y: number }> = [];
  if (Array.isArray(def?.ports)) {
    for (const entry of def.ports as AnyRecord[]) {
      const portId = String(entry.id ?? "");
      if (!portId) continue;
      const point = portPoint(place, def, portId);
      if (point) ports.push({ id: portId, x: point.x, y: point.y });
    }
  }
  return {
    ref: seed.ref,
    kind: seed.kind,
    category: String(def?.category ?? CATEGORY_BY_KIND[seed.kind] ?? ""),
    label: seed.label,
    slotId: seed.slotId,
    footprint,
    world: {
      x: place.x,
      y: place.y,
      width: place.w,
      height: place.h,
      baseline: place.bottom,
    },
    depth: (slot?.y ?? 0) < seed.depthMidline ? "rear" : "front",
    zOrder: 0,
    ports,
    vessel: seed.vessel,
    instrument: seed.instrument,
    loadUL: seed.loadUL,
    capacityUL: Number(def?.capacity_uL ?? 0) || 0,
    measures: Array.isArray(def?.measures) ? (def.measures as string[]) : [],
    known: KNOWN_EQUIPMENT_KINDS.has(seed.kind),
  };
}

// ---------------------------------------------------------------------------
// Drop intent (UI-only normalisation; commands still go through the panel)
// ---------------------------------------------------------------------------

export type DropInvalidReason =
  | "occupied"
  | "unsupported"
  | "locked"
  | "out_of_bounds"
  | "self";

export type DropCandidate =
  | { type: "move"; objectId: string; slotId: string }
  | { type: "operation"; draft: OperationDraft }
  | { type: "invalid"; reason: DropInvalidReason }
  | { type: "none" };

export interface DropAuthorityContext {
  /** Server-side held object id, or null. */
  heldId: string | null;
  /** Commands locked (conflict / offline / read-only). */
  locked: boolean;
  /** Extra world-space tolerance when matching objects (edge forgiveness). */
  pad?: number;
}

/**
 * Normalise a pointer release (or keyboard target choice) into a UI intent.
 * Decision order: target object (≠ source) → operation draft; empty slot →
 * atomic move; own slot → none; occupied/unknown/out-of-bounds → invalid.
 * This is a *pre-check only* — the engine stays the sole authority.
 */
export function resolveDropIntent(
  source: ChemLabObjectRef,
  worldPoint: Point,
  scene: SceneModel,
  authority: DropAuthorityContext,
): DropCandidate {
  if (authority.locked) return { type: "invalid", reason: "locked" };
  if (authority.heldId && authority.heldId !== source.id) {
    return { type: "invalid", reason: "locked" };
  }
  const sourceNode = scene.nodes.find((node) => node.ref.id === source.id);
  if (!sourceNode) return { type: "invalid", reason: "out_of_bounds" };

  const pad = authority.pad ?? 0;
  const target = pickTargetNode(source.id, worldPoint, scene, pad);
  if (target) {
    return operationIntent(sourceNode, target);
  }

  const slot = hitTestWorldSlot(
    scene.slots.map((view) => ({
      id: view.id,
      x: view.rect.x,
      y: view.rect.y,
      w: view.rect.w,
      h: view.rect.h,
    })) as HitRect[],
    worldPoint,
  );
  if (!slot) return { type: "invalid", reason: "out_of_bounds" };
  if (slot.id === sourceNode.slotId) return { type: "none" };
  const occupant = scene.slots.find((view) => view.id === slot.id)?.occupiedBy ?? null;
  if (occupant && occupant !== source.id) return { type: "invalid", reason: "occupied" };
  return { type: "move", objectId: source.id, slotId: slot.id };
}

function pickTargetNode(
  sourceId: string,
  worldPoint: Point,
  scene: SceneModel,
  pad: number,
): SceneNode | null {
  let best: SceneNode | null = null;
  let bestDistance = Number.POSITIVE_INFINITY;
  for (const node of scene.nodes) {
    if (node.ref.id === sourceId) continue;
    const rect: HitRect = {
      id: node.ref.id,
      x: node.world.x,
      y: node.world.y,
      w: node.world.width,
      h: node.world.height,
    };
    const inside =
      worldPoint.x >= rect.x - pad && worldPoint.x <= rect.x + rect.w + pad &&
      worldPoint.y >= rect.y - pad && worldPoint.y <= rect.y + rect.h + pad;
    if (!inside) continue;
    const distance = distanceToRectCenter(rect, worldPoint);
    // Highest z-order (nearest viewer) wins ties.
    if (!best || node.zOrder > best.zOrder || (node.zOrder === best.zOrder && distance < bestDistance)) {
      best = node;
      bestDistance = distance;
    }
  }
  return best;
}

function operationIntent(source: SceneNode, target: SceneNode): DropCandidate {
  const sourceKind = source.kind;
  const targetKind = target.kind;
  const targetIsVessel = target.ref.type === "vessel";
  if (source.ref.id === target.ref.id) return { type: "none" };

  // Vessel dragged onto something.
  if (source.ref.type === "vessel") {
    if (targetIsVessel) {
      // The panel always asks for the amount — never an implicit pour.
      return { type: "operation", draft: { action: "pour", sourceId: source.ref.id, targetId: target.ref.id } };
    }
    if (targetKind === "hotplate") {
      return { type: "operation", draft: { action: "heat", vesselId: source.ref.id, deviceId: target.ref.id } };
    }
    return { type: "invalid", reason: "unsupported" };
  }

  // Instruments / devices dragged onto vessels.
  if (targetIsVessel) {
    switch (sourceKind) {
      case "dropper":
      case "pipette":
        return source.loadUL > 0
          ? { type: "operation", draft: { action: "dispense", instrumentId: source.ref.id, targetId: target.ref.id } }
          : { type: "operation", draft: { action: "aspirate", instrumentId: source.ref.id, sourceId: target.ref.id } };
      case "thermometer":
        return {
          type: "operation",
          draft: {
            action: "measure",
            instrumentId: source.ref.id,
            vesselId: target.ref.id,
          },
        };
      case "ph_probe":
        return {
          type: "operation",
          draft: {
            action: "measure",
            instrumentId: source.ref.id,
            vesselId: target.ref.id,
          },
        };
      case "stir_rod":
        return { type: "operation", draft: { action: "stir", vesselId: target.ref.id } };
      case "delivery_tube":
        return {
          type: "operation",
          draft: { action: "connect", instrumentId: source.ref.id, targetId: target.ref.id },
        };
      case "hotplate":
        return { type: "operation", draft: { action: "heat", deviceId: source.ref.id, vesselId: target.ref.id } };
      default:
        return { type: "invalid", reason: "unsupported" };
    }
  }
  return { type: "invalid", reason: "unsupported" };
}
