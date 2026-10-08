/**
 * Stage geometry: resolves the pack-declared slot grid into SVG coordinates.
 * All positions come from the experiment pack's `starting_state.slots` and the
 * equipment defs' `footprint`/`ports` — the renderer never hard-codes where a
 * beaker stands, so new experiments rearrange the bench from content alone.
 */

export interface SlotRect {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface StageViewport {
  viewBox: string;
  width: number;
  height: number;
}

const STAGE_PADDING = 24;
const STAGE_MIN_WIDTH = 640;
const STAGE_MIN_HEIGHT = 480;

export function readSlots(pack: Record<string, unknown> | null | undefined): SlotRect[] {
  const start = (pack?.starting_state ?? {}) as Record<string, unknown>;
  const slots = Array.isArray(start.slots) ? start.slots : [];
  return slots
    .map((slot) => {
      const row = slot as Record<string, unknown>;
      return {
        id: String(row.id ?? ""),
        x: Number(row.x ?? 0),
        y: Number(row.y ?? 0),
        w: Number(row.w ?? 0),
        h: Number(row.h ?? 0),
      };
    })
    .filter((slot) => slot.id && slot.w > 0 && slot.h > 0);
}

/** Bounding box of the declared slots + padding; stable aspect for the SVG. */
export function stageViewport(slots: SlotRect[]): StageViewport {
  let maxX = STAGE_MIN_WIDTH - STAGE_PADDING * 2;
  let maxY = STAGE_MIN_HEIGHT - STAGE_PADDING * 2;
  for (const slot of slots) {
    maxX = Math.max(maxX, slot.x + slot.w);
    maxY = Math.max(maxY, slot.y + slot.h);
  }
  const width = maxX + STAGE_PADDING * 2;
  const height = maxY + STAGE_PADDING * 2;
  return { viewBox: `0 0 ${width} ${height}`, width, height };
}

export function slotById(slots: SlotRect[], slotId: string | null | undefined): SlotRect | null {
  if (!slotId) return null;
  return slots.find((slot) => slot.id === slotId) ?? null;
}

/** Point (in viewBox coordinates) → slot id, for pointer drop targeting. */
export function hitTestSlot(slots: SlotRect[], x: number, y: number): SlotRect | null {
  return (
    slots.find((slot) => x >= slot.x && x <= slot.x + slot.w && y >= slot.y && y <= slot.y + slot.h) ??
    null
  );
}

export interface EquipmentPlacement {
  /** Top-left of the equipment footprint inside the stage viewBox. */
  x: number;
  y: number;
  w: number;
  h: number;
  /** Horizontal centre — labels and held ghosts anchor here. */
  cx: number;
  /** Bottom edge — vessels sit on the bench line. */
  bottom: number;
}

/**
 * Place an item inside its slot: equipment defs declare a footprint (w,h);
 * the item is centred horizontally and bottom-aligned inside the slot, which
 * keeps every bench composition tidy without per-experiment pixel work.
 */
export function placement(
  slot: SlotRect | null,
  footprint: [number, number],
): EquipmentPlacement {
  const [fw, fh] = footprint;
  const box = slot ?? { id: "", x: 0, y: 0, w: fw, h: fh };
  const w = Math.min(fw, box.w);
  const h = Math.min(fh, box.h);
  const x = box.x + (box.w - w) / 2;
  const y = box.y + box.h - h;
  return { x, y, w, h, cx: x + w / 2, bottom: y + h };
}

/** Equipment def footprint, tolerant of missing content. */
export function footprintOf(def: Record<string, unknown> | null | undefined): [number, number] {
  const raw = def?.footprint;
  if (Array.isArray(raw) && raw.length >= 2) {
    return [Number(raw[0]) || 80, Number(raw[1]) || 120];
  }
  return [80, 120];
}

/** Port position in stage coordinates (ports are footprint-relative). */
export function portPoint(
  place: EquipmentPlacement,
  def: Record<string, unknown> | null | undefined,
  portId: string,
): { x: number; y: number } | null {
  const ports = def?.ports;
  if (!Array.isArray(ports)) return null;
  for (const entry of ports) {
    const port = entry as Record<string, unknown>;
    if (String(port.id ?? "") !== portId) continue;
    const px = Number(port.x ?? 0);
    const py = Number(port.y ?? 0);
    return {
      x: place.x + (px / Math.max(1, footprintOf(def)[0])) * place.w,
      y: place.y + (py / Math.max(1, footprintOf(def)[1])) * place.h,
    };
  }
  return null;
}
