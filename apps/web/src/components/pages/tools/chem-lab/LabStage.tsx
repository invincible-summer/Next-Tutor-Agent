"use client";

/**
 * The interactive bench: inline SVG renders slot grid, vessel/instrument art,
 * tubing links and frame-driven phenomena; a DOM overlay provides 44px+
 * accessible buttons, readings and drop slots. Pointer drag only moves a
 * ghost — no chemistry runs during a drag; pointerup over a slot produces a
 * single InteractionIntent (move, or open the operation panel), exactly like
 * the click and keyboard paths.
 */
import {
  useCallback,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import type { ChemLabRenderFrame } from "@/lib/api-chem-lab";
import {
  footprintOf,
  hitTestSlot,
  placement,
  readSlots,
  slotById,
  stageViewport,
  type EquipmentPlacement,
  type SlotRect,
} from "./chem-lab-geometry";
import { instrumentVisuals, vesselVisual, type VesselVisual } from "./chem-lab-renderer";
import { CHEM_LAB_STAGE_CSS, InstrumentArt, isVesselKind, VesselArt } from "./assets";
import type { ChemLabDisplay } from "./useChemLabSession";
import type { ChemLabObjectRef, OperationDraft } from "./interaction";

type AnyRecord = Record<string, unknown>;

interface StageObject {
  ref: ChemLabObjectRef;
  kind: string;
  name: string;
  place: EquipmentPlacement;
  slotId: string;
}

interface DragGhost {
  object: StageObject;
  x: number;
  y: number;
  pointerId: number;
}

export interface LabStageProps {
  pack: AnyRecord | null;
  display: ChemLabDisplay;
  language: string;
  selected: ChemLabObjectRef | null;
  busy: boolean;
  onSelect: (ref: ChemLabObjectRef) => void;
  /** Move intent: pick_up + place, confirmed through the command factory. */
  onMoveObject: (objectId: string, slotId: string) => void;
  /** Drop onto another object opens the shared operation panel. */
  onDragOperation: (draft: OperationDraft) => void;
  /** Held-object placement via slot click (keyboard / single-pointer path). */
  onSlotTarget: (slotId: string) => void;
  onInstrumentTap: (equipmentId: string, anchor: HTMLElement) => void;
}

function l10n(entry: unknown, language: string): string {
  if (entry && typeof entry === "object" && !Array.isArray(entry)) {
    const row = entry as AnyRecord;
    const value = row[language] ?? row.zh ?? row.en;
    if (typeof value === "string") return value;
  }
  return typeof entry === "string" ? entry : "";
}

function formatMl(volumeUL: number): string {
  const ml = volumeUL / 1000;
  return ml >= 100 ? String(Math.round(ml)) : ml >= 10 ? ml.toFixed(1).replace(/\.0$/, "") : ml.toFixed(1);
}

function vesselSummary(visual: VesselVisual | null, language: string): string {
  if (!visual) return "";
  const parts: string[] = [];
  if (visual.volumeUL > 0) parts.push(`${formatMl(visual.volumeUL)} mL`);
  if (visual.liquidLabel && visual.fillRatio > 0) parts.push(visual.liquidLabel);
  if (visual.precipitate) parts.push(language === "en" ? "precipitate" : "有沉淀");
  if (visual.bubbles) parts.push(language === "en" ? "bubbling" : "有气泡");
  if (visual.steam > 0.05) parts.push(language === "en" ? "steaming" : "有水汽");
  if (visual.temperatureBand === "hot") parts.push(language === "en" ? "hot" : "温度高");
  if (visual.temperatureBand === "warm") parts.push(language === "en" ? "warm" : "温热");
  return parts.join("，");
}

export function LabStage({
  pack,
  display,
  language,
  selected,
  busy,
  onSelect,
  onMoveObject,
  onDragOperation,
  onSlotTarget,
  onInstrumentTap,
}: LabStageProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [ghost, setGhost] = useState<DragGhost | null>(null);

  const slots = useMemo(() => readSlots(pack), [pack]);
  const viewport = useMemo(() => stageViewport(slots), [slots]);
  const equipmentDefs = useMemo(
    () => ((pack?._equipment_defs ?? pack?.equipment_defs ?? {}) as AnyRecord),
    [pack],
  );

  const startingVessels = useMemo(() => {
    const start = (pack?.starting_state ?? {}) as AnyRecord;
    const rows = Array.isArray(start.vessels) ? (start.vessels as AnyRecord[]) : [];
    const map = new Map<string, AnyRecord>();
    for (const row of rows) map.set(String(row.id ?? ""), row);
    return map;
  }, [pack]);

  const objects = useMemo<StageObject[]>(() => {
    const state = display.engineState ?? {};
    const out: StageObject[] = [];
    const vessels = (state.vessels ?? {}) as AnyRecord;
    for (const id of Object.keys(vessels).sort()) {
      const vessel = vessels[id] as AnyRecord;
      const kind = String(vessel.kind ?? "");
      const slot = slotById(slots, String(vessel.slot ?? ""));
      const label = l10n(startingVessels.get(id)?.label, language);
      out.push({
        ref: { type: "vessel", id },
        kind,
        name: label || id,
        place: placement(slot, footprintOf(equipmentDefs[kind] as AnyRecord)),
        slotId: String(vessel.slot ?? ""),
      });
    }
    const equipment = (state.equipment ?? {}) as AnyRecord;
    for (const id of Object.keys(equipment).sort()) {
      const item = equipment[id] as AnyRecord;
      const kind = String(item.kind ?? "");
      const slot = slotById(slots, String(item.slot ?? ""));
      const def = equipmentDefs[kind] as AnyRecord;
      const name = l10n(def?.name, language) || id;
      out.push({
        ref: { type: "equipment", id },
        kind,
        name,
        place: placement(slot, footprintOf(def)),
        slotId: String(item.slot ?? ""),
      });
    }
    return out;
  }, [display.engineState, slots, equipmentDefs, startingVessels, language]);

  const objectMap = useMemo(() => {
    const map = new Map<string, StageObject>();
    for (const object of objects) map.set(object.ref.id, object);
    return map;
  }, [objects]);

  const frame: ChemLabRenderFrame | null = display.renderFrame;
  const visuals = useMemo(() => {
    const map = new Map<string, VesselVisual>();
    for (const object of objects) {
      if (object.ref.type === "vessel") {
        map.set(object.ref.id, vesselVisual(pack, frame, display.engineState, object.ref.id, language));
      }
    }
    return map;
  }, [objects, pack, frame, display.engineState, language]);

  const instruments = useMemo(() => instrumentVisuals(frame), [frame]);
  const heldId = String(display.engineState?.held ?? "") || null;
  const heatVessels = useMemo(() => {
    const vessels = (display.engineState?.vessels ?? {}) as AnyRecord;
    const set = new Set<string>();
    for (const [id, vessel] of Object.entries(vessels)) {
      if ((vessel as AnyRecord).heat) set.add(id);
    }
    return set;
  }, [display.engineState]);

  const tubing = useMemo(() => {
    const equipment = (display.engineState?.equipment ?? {}) as AnyRecord;
    const links: Array<{ id: string; from: string; to: string }> = [];
    for (const [id, item] of Object.entries(equipment)) {
      const connected = (item as AnyRecord).connected as AnyRecord | null;
      if (connected?.source && connected?.target) {
        links.push({ id, from: String(connected.source), to: String(connected.target) });
      }
    }
    return links;
  }, [display.engineState]);

  const toStagePoint = useCallback((event: { clientX: number; clientY: number }) => {
    const svg = svgRef.current;
    if (!svg) return { x: 0, y: 0 };
    const rect = svg.getBoundingClientRect();
    return {
      x: ((event.clientX - rect.left) / Math.max(1, rect.width)) * viewport.width,
      y: ((event.clientY - rect.top) / Math.max(1, rect.height)) * viewport.height,
    };
  }, [viewport]);

  const beginDrag = useCallback(
    (event: ReactPointerEvent, object: StageObject) => {
      if (busy || event.button !== 0) return;
      event.preventDefault();
      (event.currentTarget as Element).setPointerCapture?.(event.pointerId);
      const point = toStagePoint(event);
      setGhost({ object, x: point.x, y: point.y, pointerId: event.pointerId });
      onSelect(object.ref);
    },
    [busy, onSelect, toStagePoint],
  );

  const moveDrag = useCallback(
    (event: ReactPointerEvent) => {
      setGhost((prev) => {
        if (!prev || prev.pointerId !== event.pointerId) return prev;
        const point = toStagePoint(event);
        return { ...prev, x: point.x, y: point.y };
      });
    },
    [toStagePoint],
  );

  const endDrag = useCallback(
    (event: ReactPointerEvent) => {
      const current = ghost;
      setGhost(null);
      if (!current || current.pointerId !== event.pointerId) return;
      const point = toStagePoint(event);
      const slot = hitTestSlot(slots, point.x, point.y);
      const dragged = current.object;
      if (!slot || slot.id === dragged.slotId) return;
      const occupant = objects.find(
        (object) => object.slotId === slot.id && object.ref.id !== dragged.ref.id && object.ref.type === "vessel",
      );
      if (occupant) {
        // Dropping onto another vessel opens the shared operation panel —
        // never an implicit whole-bottle pour.
        if (dragged.ref.type === "vessel") {
          onDragOperation({ action: "pour", sourceId: dragged.ref.id, targetId: occupant.ref.id });
        } else {
          onDragOperation({ action: "aspirate", instrumentId: dragged.ref.id, sourceId: occupant.ref.id });
        }
        return;
      }
      onMoveObject(dragged.ref.id, slot.id);
    },
    [ghost, slots, objects, toStagePoint, onMoveObject, onDragOperation],
  );

  const cancelDrag = useCallback(() => setGhost(null), []);

  const isSelected = (object: StageObject) =>
    selected?.type === object.ref.type && selected.id === object.ref.id;

  return (
    <div className="relative h-full min-h-[320px] w-full select-none" data-testid="chem-lab-stage">
      <style>{CHEM_LAB_STAGE_CSS}</style>
      <svg
        ref={svgRef}
        viewBox={viewport.viewBox}
        className="h-full w-full touch-none text-fg-secondary"
        role="img"
        aria-label={language === "en" ? "Virtual chemistry bench" : "虚拟化学实验台"}
        onPointerMove={moveDrag}
        onPointerUp={endDrag}
        onPointerCancel={cancelDrag}
      >
        <defs>
          <pattern id="chem-lab-grid" width="32" height="32" patternUnits="userSpaceOnUse">
            <circle cx="1" cy="1" r="1" fill="currentColor" opacity="0.10" />
          </pattern>
        </defs>
        <rect x={0} y={0} width={viewport.width} height={viewport.height} fill="url(#chem-lab-grid)" rx={18} />
        {slots.map((slot: SlotRect) => {
          const targeted = heldId !== null;
          return (
            <g key={slot.id}>
              <rect
                x={slot.x}
                y={slot.y}
                width={slot.w}
                height={slot.h}
                rx={10}
                fill={targeted ? "rgba(96, 140, 180, 0.10)" : "rgba(148, 178, 210, 0.06)"}
                stroke="currentColor"
                strokeOpacity={targeted ? 0.35 : 0.14}
                strokeDasharray="6 6"
              />
            </g>
          );
        })}
        {tubing.map((link) => {
          const from = objectMap.get(link.from);
          const to = objectMap.get(link.to);
          if (!from || !to) return null;
          const midX = (from.place.cx + to.place.cx) / 2;
          return (
            <path
              key={link.id}
              d={`M${from.place.cx} ${from.place.y + 4} C${midX} ${from.place.y - 30}, ${midX} ${to.place.y - 30}, ${to.place.cx} ${to.place.y + 4}`}
              fill="none"
              stroke="currentColor"
              strokeOpacity={0.55}
              strokeWidth={5}
              strokeLinecap="round"
            />
          );
        })}
        {objects.map((object) => {
          const { place } = object;
          const visual = visuals.get(object.ref.id) ?? null;
          const selectedCls = isSelected(object);
          const dimmed = ghost?.object.ref.id === object.ref.id;
          const held = heldId === object.ref.id;
          return (
            <g
              key={object.ref.id}
              transform={`translate(${place.x} ${place.y}) scale(${place.w / 100} ${place.h / 100})`}
              opacity={dimmed ? 0.35 : 1}
              onPointerDown={(event) => beginDrag(event, object)}
              style={{ cursor: busy ? "default" : "grab" }}
              data-object-id={object.ref.id}
            >
              {selectedCls && (
                <rect x={-6} y={-6} width={112} height={112} rx={12} fill="none"
                  stroke="var(--color-accent, #2f6f6a)" strokeWidth={2.4} strokeDasharray="4 5"
                  vectorEffect="non-scaling-stroke" />
              )}
              {held && (
                <rect x={-4} y={-4} width={108} height={108} rx={10} fill="rgba(96, 140, 180, 0.12)"
                  stroke="var(--color-accent, #2f6f6a)" strokeWidth={2} vectorEffect="non-scaling-stroke" />
              )}
              {isVesselKind(object.kind) ? (
                <VesselArt kind={object.kind} visual={visual} heating={heatVessels.has(object.ref.id)} />
              ) : (
                <InstrumentArt kind={object.kind} active={held} />
              )}
            </g>
          );
        })}
        {ghost && (
          <g
            transform={`translate(${ghost.x - ghost.object.place.w / 2} ${ghost.y - ghost.object.place.h / 2}) scale(${ghost.object.place.w / 100} ${ghost.object.place.h / 100})`}
            opacity={0.75}
            pointerEvents="none"
          >
            {isVesselKind(ghost.object.kind) ? (
              <VesselArt kind={ghost.object.kind} visual={visuals.get(ghost.object.ref.id) ?? null} />
            ) : (
              <InstrumentArt kind={ghost.object.kind} active />
            )}
          </g>
        )}
      </svg>

      {/* DOM overlay: accessible targets, readings, slot drop buttons. */}
      <div className="pointer-events-none absolute inset-0">
        {objects.map((object) => {
          const { place } = object;
          const visual = visuals.get(object.ref.id) ?? null;
          const style = {
            left: `${(place.cx / viewport.width) * 100}%`,
            top: `${(place.y / viewport.height) * 100}%`,
            width: `${Math.max(44, (place.w / viewport.width) * 100 * 1.0)}px`,
            minWidth: "44px",
            minHeight: "44px",
          };
          const reading = object.ref.type === "equipment"
            ? instruments.find((row) => row.equipmentId === object.ref.id)
            : undefined;
          const summary = object.ref.type === "vessel" ? vesselSummary(visual, language) : "";
          return (
            <div key={`overlay-${object.ref.id}`} className="absolute -translate-x-1/2" style={style}>
              <button
                type="button"
                disabled={busy}
                onClick={(event) => {
                  onSelect(object.ref);
                  if (object.ref.type === "equipment") onInstrumentTap(object.ref.id, event.currentTarget);
                }}
                aria-pressed={isSelected(object)}
                aria-label={summary ? `${object.name}，${summary}` : object.name}
                data-testid={`chem-lab-object-${object.ref.id}`}
                className="pointer-events-auto inline-flex min-h-[44px] min-w-[44px] cursor-pointer flex-col items-center justify-start rounded-[10px] px-1 pt-1 focus-visible:outline-2 focus-visible:outline-accent"
              >
                <span className="max-w-[120px] truncate rounded-full bg-surface/85 px-2 py-0.5 text-[10px] font-medium text-fg-secondary shadow-sm">
                  {object.name}
                </span>
                {reading?.reading && (
                  <span className="tnum mt-0.5 rounded-full bg-accent-soft px-2 py-0.5 text-[10px] font-semibold text-accent-strong">
                    {reading.reading} {reading.unit}
                  </span>
                )}
                {visual?.highlight && (
                  <span className="mt-0.5 rounded-full bg-danger/10 px-2 py-0.5 text-[10px] font-medium text-danger">
                    {language === "en" ? "check safety" : "注意安全"}
                  </span>
                )}
              </button>
            </div>
          );
        })}
        {heldId && (
          <div className="absolute inset-0">
            {slots.map((slot) => (
              <button
                key={`slot-${slot.id}`}
                type="button"
                onClick={() => onSlotTarget(slot.id)}
                aria-label={language === "en" ? `Place here (${slot.id})` : `放到这里（${slot.id}）`}
                className="pointer-events-auto absolute cursor-pointer rounded-[10px] border-2 border-dashed border-accent/50 bg-accent-soft/30 focus-visible:outline-2 focus-visible:outline-accent"
                style={{
                  left: `${(slot.x / viewport.width) * 100}%`,
                  top: `${(slot.y / viewport.height) * 100}%`,
                  width: `${(slot.w / viewport.width) * 100}%`,
                  height: `${(slot.h / viewport.height) * 100}%`,
                  minWidth: "44px",
                  minHeight: "44px",
                }}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
