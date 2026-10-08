"use client";

/**
 * The single lab scene: one SVG world group (camera transform, environment,
 * equipment, tubing, ghost, candidate highlights — all visuals, none of them
 * take pointers) plus one DOM overlay whose ObjectHitTargets are the *only*
 * pointer entry points, projected from the same world group's CTM into CSS
 * pixels. Slot buttons appear solely for target selection (click/keyboard
 * move path); during a drag the drop is resolved geometrically in world
 * space — never from `event.target`.
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { makePageT } from "@/lib/i18n-page";
import { STRINGS } from "@/app/(workspace)/tools/lab/chemistry/strings";
import type { ChemLabObjectRef, OperationDraft } from "../interaction";
import {
  affineToCssTransform,
  boundCamera,
  focusCameraOn,
  OVERVIEW_CAMERA,
  type CameraPose,
} from "./scene-geometry.ts";
import type { DropCandidate, SceneModel, SceneNode } from "./scene-model";
import { useProjectionVersion, worldRectToCssRect, type CssRect } from "./scene-projection.ts";
import { useLabInteraction } from "./useLabInteraction.ts";
import { LabEnvironment } from "./LabEnvironment";
import { LabEquipment } from "./LabEquipment";

export interface LabSceneProps {
  scene: SceneModel;
  language: string;
  selectedId: string | null;
  /** Commands locked (conflict/offline) or history read-only. */
  locked: boolean;
  readOnly: boolean;
  sessionId: string | null;
  /** `${sessionId}:${revision}` — deterministic phenomena seed base. */
  seedBase: string;
  heatingVessels: ReadonlySet<string>;
  hotplateDevices: ReadonlySet<string>;
  onSelect: (ref: ChemLabObjectRef) => void;
  onMoveObject: (objectId: string, slotId: string) => void;
  onDragOperation: (draft: OperationDraft) => void;
  onInstrumentTap: (equipmentId: string, anchor: HTMLElement) => void;
  onBackgroundClick: () => void;
}

interface HitBox {
  node: SceneNode;
  rect: CssRect;
}

const CANDIDATE_KEYS: Record<string, string> = {
  move: "drop.move",
  operation: "drop.operation",
  none: "drop.none",
  occupied: "drop.occupied",
  unsupported: "drop.unsupported",
  locked: "drop.locked",
  out_of_bounds: "drop.out_of_bounds",
};

type PageTranslator = ReturnType<typeof makePageT>;

function candidateText(candidate: DropCandidate | null, tr: PageTranslator): string {
  if (!candidate) return "";
  const key = CANDIDATE_KEYS[candidate.type === "invalid" ? candidate.reason : candidate.type];
  return key ? tr(key) : "";
}

function formatMl(volumeUL: number): string {
  const ml = volumeUL / 1000;
  return ml >= 100 ? String(Math.round(ml)) : ml >= 10 ? ml.toFixed(1).replace(/\.0$/, "") : ml.toFixed(1);
}

function nodeSummary(node: SceneNode, tr: PageTranslator, separator: string): string {
  const parts: string[] = [];
  if (node.vessel && node.vessel.volumeUL > 0) parts.push(`${formatMl(node.vessel.volumeUL)} mL`);
  if (node.vessel?.liquidLabel && node.vessel.fillRatio > 0) parts.push(node.vessel.liquidLabel);
  if (node.vessel?.precipitate) parts.push(tr("hasPrecipitate"));
  if (node.vessel?.bubbles) parts.push(tr("hasBubbles"));
  if ((node.vessel?.steam ?? 0) > 0.05) parts.push(tr("sum.steam"));
  if (node.vessel?.temperatureBand === "hot") parts.push(tr("sum.hot"));
  if (node.vessel?.temperatureBand === "warm") parts.push(tr("sum.warm"));
  if (node.instrument?.reading) parts.push(`${node.instrument.reading} ${node.instrument.unit}`.trim());
  return parts.join(separator);
}

function highlightChip(node: SceneNode, tr: PageTranslator): { text: string; danger: boolean } | null {
  const highlight = node.vessel?.highlight ?? null;
  if (!highlight) return null;
  if (highlight.startsWith("safety")) {
    return { text: tr("chip.safety"), danger: true };
  }
  if (highlight === "precipitate_formed") {
    return { text: tr("chip.precipitate"), danger: false };
  }
  if (highlight === "gas_released") {
    return { text: tr("chip.gas"), danger: false };
  }
  if (highlight === "crystal_formed") {
    return { text: tr("chip.crystal"), danger: false };
  }
  return { text: highlight, danger: false };
}

export function LabScene({
  scene, language, selectedId, locked, readOnly, sessionId, seedBase,
  heatingVessels, hotplateDevices,
  onSelect, onMoveObject, onDragOperation, onInstrumentTap, onBackgroundClick,
}: LabSceneProps) {
  const tr = useMemo(
    () => makePageT(language === "en" ? "en" : "zh", STRINGS),
    [language],
  );
  const listSeparator = language === "en" ? ", " : "，";
  const viewportRef = useRef<HTMLDivElement>(null);
  const [viewportEl, setViewportEl] = useState<HTMLDivElement | null>(null);
  const [overlayEl, setOverlayEl] = useState<HTMLDivElement | null>(null);
  const worldGroupRef = useRef<SVGGElement | null>(null);
  const [worldGroupEl, setWorldGroupEl] = useState<SVGGElement | null>(null);
  // Keep the stable ref (used at event time for CTM reads) in sync with the
  // state-held element (used during render for hit-box projection).
  useEffect(() => {
    worldGroupRef.current = worldGroupEl;
  }, [worldGroupEl]);

  const frame = useMemo(
    () => ({
      x: -56,
      y: -220,
      width: scene.bounds.width + 112,
      height: scene.bounds.height + 300,
    }),
    [scene.bounds],
  );
  const frameViewport = useMemo(
    () => ({ x: frame.width, y: frame.height }),
    [frame.width, frame.height],
  );

  const [camera, setCamera] = useState<CameraPose>(OVERVIEW_CAMERA);
  const [cameraMode, setCameraMode] = useState<"overview" | "focus">("overview");
  const [lastFocusedId, setLastFocusedId] = useState<string | null>(null);

  const interaction = useLabInteraction({
    scene,
    viewportRef,
    sceneGroupRef: worldGroupRef,
    busy: false,
    locked: locked || readOnly,
    sessionId,
    onSelect,
    onMoveObject,
    onDragOperation,
  });
  const {
    gestureView, targetSelectionFor, startObjectPointer, pointerMove, pointerUp,
    pointerCancel, lostCapture, chooseTargetByKeyboard, requestTargetSelection,
    shouldSuppressClick,
  } = interaction;

  // Selection drives the camera (render-time derived-state adjustment — the
  // sanctioned pattern instead of a setState-in-effect). Liquid/revision
  // updates never retrigger it: only an actual selection change does.
  if (lastFocusedId !== selectedId && !gestureView) {
    setLastFocusedId(selectedId);
    if (!selectedId) {
      setCamera(OVERVIEW_CAMERA);
      setCameraMode("overview");
    } else {
      const node = scene.nodes.find((item) => item.ref.id === selectedId);
      if (node) {
        const center = {
          x: node.world.x + node.world.width / 2,
          y: node.world.y + node.world.height / 2,
        };
        setCamera(boundCamera(focusCameraOn(center, frameViewport, node.ref.id), frame, frameViewport));
        setCameraMode("focus");
      }
    }
  }

  const cameraKey = `${camera.mode}:${camera.scale}:${camera.translateX}:${camera.translateY}`;
  const { version: projectionVersion } = useProjectionVersion(
    viewportEl,
    `${cameraKey}|${seedBase}|${scene.nodes.length}|${targetSelectionFor ?? ""}`,
  );

  // CSS-pixel hit boxes for the DOM overlay — recomputed only on version bumps.
  const hitBoxes = useMemo<HitBox[]>(() => {
    void projectionVersion;
    if (!viewportEl || !worldGroupEl || !overlayEl) return [];
    return scene.nodes.map((node) => {
      const rect = worldRectToCssRect(node.world, worldGroupEl, overlayEl);
      return { node, rect: rect ?? { left: 0, top: 0, width: 0, height: 0 } };
    });
  }, [scene.nodes, viewportEl, worldGroupEl, overlayEl, projectionVersion]);

  const slotBoxes = useMemo(() => {
    void projectionVersion;
    if (!viewportEl || !worldGroupEl || !overlayEl) return [];
    return scene.slots.map((slot) => ({
      slot,
      rect: worldRectToCssRect(
        { x: slot.rect.x, y: slot.rect.y, width: slot.rect.w, height: slot.rect.h },
        worldGroupEl,
        overlayEl,
      ) ?? { left: 0, top: 0, width: 0, height: 0 },
    }));
  }, [scene.slots, viewportEl, worldGroupEl, overlayEl, projectionVersion]);

  const expandedRect = useCallback((rect: CssRect): CssRect => {
    const minSide = 44;
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const width = Math.max(minSide, rect.width);
    const height = Math.max(minSide, rect.height);
    return { left: cx - width / 2, top: cy - height / 2, width, height };
  }, []);

  const candidateNode = useMemo<SceneNode | null>(() => {
    if (gestureView?.candidate.type === "operation") {
      const targetId = gestureView.candidate.draft.targetId ?? gestureView.candidate.draft.vesselId;
      return scene.nodes.find((node) => node.ref.id === targetId) ?? null;
    }
    return null;
  }, [gestureView, scene.nodes]);

  const candidateSlotId = gestureView?.candidate.type === "move" ? gestureView.candidate.slotId : null;

  const statusText = candidateText(gestureView?.candidate ?? null, tr)
    || (targetSelectionFor ? tr("chooseTarget") : "");

  const rearBand = useMemo(() => {
    const rows = scene.slots.filter((slot) => slot.depth === "rear");
    if (!rows.length) return null;
    return {
      top: Math.min(...rows.map((slot) => slot.rect.y)),
      bottom: Math.max(...rows.map((slot) => slot.rect.y + slot.rect.h)),
    };
  }, [scene.slots]);
  const frontBand = useMemo(() => {
    const rows = scene.slots.filter((slot) => slot.depth === "front");
    if (!rows.length) return null;
    return {
      top: Math.min(...rows.map((slot) => slot.rect.y)),
      bottom: Math.max(...rows.map((slot) => slot.rect.y + slot.rect.h)),
    };
  }, [scene.slots]);

  const onViewportClick = (event: React.MouseEvent<HTMLDivElement>) => {
    if (shouldSuppressClick()) return;
    const target = event.target as Element;
    if (target.closest("[data-chem-lab-hot]")) return;
    if (target.closest("button")) return;
    onBackgroundClick();
  };

  return (
    <div
      ref={(el) => {
        viewportRef.current = el;
        setViewportEl(el);
      }}
      className="lab-scene relative h-full min-h-[320px] w-full select-none overflow-hidden rounded-[14px] border border-border-light bg-bg"
      style={{ touchAction: "none" }}
      data-testid="chem-lab-scene"
      role="application"
      aria-label={tr("sceneAria")}
      onPointerMove={pointerMove}
      onPointerUp={pointerUp}
      onPointerCancel={pointerCancel}
      onLostPointerCapture={lostCapture}
      onClick={onViewportClick}
    >
      <svg
        className="absolute inset-0 h-full w-full"
        viewBox={`${frame.x} ${frame.y} ${frame.width} ${frame.height}`}
        preserveAspectRatio="xMidYMid meet"
        aria-hidden="true"
      >
        <g
          ref={(el) => {
            setWorldGroupEl(el);
          }}
          className={`lab-scene__world${gestureView ? " lab-scene__world--frozen" : ""}`}
          style={{ transform: affineToCssTransformFromPose(camera) }}
        >
          <LabEnvironment frame={frame} rearBand={rearBand} frontBand={frontBand} bounds={scene.bounds} />

          {/* slot placement mats — visual anchors, never interactive */}
          {scene.slots.map((slot) => (
            <rect
              key={slot.id}
              x={slot.rect.x}
              y={slot.rect.y}
              width={slot.rect.w}
              height={slot.rect.h}
              rx={12}
              fill="rgba(120, 130, 120, 0.05)"
              stroke="rgba(120, 130, 120, 0.16)"
              strokeWidth={1}
              strokeDasharray="3 6"
            />
          ))}

          {/* tubing links between real equipment ports */}
          {scene.links.map((link) => {
            const from = scene.nodes.find((node) => node.ref.id === link.fromId);
            const to = scene.nodes.find((node) => node.ref.id === link.toId);
            const tube = scene.nodes.find((node) => node.ref.id === link.equipmentId);
            if (!from || !to) return null;
            const x1 = from.world.x + from.world.width / 2;
            const y1 = from.world.y + 6;
            const x2 = to.world.x + to.world.width / 2;
            const y2 = to.world.y + 6;
            const midX = (x1 + x2) / 2;
            const gasFlowing = Boolean(to.vessel?.bubbles ?? from.vessel?.bubbles);
            return (
              <g key={link.equipmentId}>
                <path
                  d={`M${x1} ${y1} C${midX} ${Math.min(y1, y2) - (tube?.world.height ?? 40)}, ${midX} ${Math.min(y1, y2) - (tube?.world.height ?? 40)}, ${x2} ${y2}`}
                  fill="none"
                  stroke="var(--lab-steel-dark)"
                  strokeWidth={5.5}
                  strokeLinecap="round"
                  opacity={0.75}
                />
                {gasFlowing && (
                  <path
                    className="lab-scene__gasdot"
                    d={`M${x1} ${y1} C${midX} ${Math.min(y1, y2) - 40}, ${midX} ${Math.min(y1, y2) - 40}, ${x2} ${y2}`}
                    fill="none"
                    stroke="rgba(255,255,255,0.85)"
                    strokeWidth={2}
                    strokeDasharray="4 18"
                    strokeLinecap="round"
                  />
                )}
              </g>
            );
          })}

          {/* equipment, ordered by zOrder (rear first) */}
          {scene.nodes.map((node) => (
            <g
              key={node.ref.id}
              data-object-id={node.ref.id}
              transform={`translate(${node.world.x} ${node.world.y})`}
            >
              <LabEquipment
                node={node}
                selected={selectedId === node.ref.id}
                held={scene.heldId === node.ref.id}
                dimmed={gestureView?.objectId === node.ref.id}
                heating={heatingVessels.has(node.ref.id)}
                hotplateActive={hotplateDevices.has(node.ref.id)}
                seed={`${seedBase}:${node.ref.id}`}
              />
              {!node.known && (
                <text
                  x={node.world.width / 2}
                  y={node.world.height + 14}
                  textAnchor="middle"
                  fontSize={11}
                  fill="var(--lab-steel-dark)"
                >
                  {tr("unknownKind").replace("%s", node.kind)}
                </text>
              )}
            </g>
          ))}

          {/* drop-candidate feedback in world space */}
          {candidateSlotId && (() => {
            const slot = scene.slots.find((view) => view.id === candidateSlotId);
            if (!slot) return null;
            return (
              <rect
                data-testid="chem-lab-drop-candidate"
                x={slot.rect.x - 3}
                y={slot.rect.y - 3}
                width={slot.rect.w + 6}
                height={slot.rect.h + 6}
                rx={14}
                fill="rgba(63, 143, 107, 0.12)"
                stroke="var(--lab-drop-ok)"
                strokeWidth={2.4}
                strokeDasharray="7 5"
                vectorEffect="non-scaling-stroke"
              />
            );
          })()}
          {candidateNode && (
            <rect
              data-testid="chem-lab-drop-candidate"
              x={candidateNode.world.x - 4}
              y={candidateNode.world.y - 4}
              width={candidateNode.world.width + 8}
              height={candidateNode.world.height + 8}
              rx={12}
              fill="none"
              stroke="var(--color-accent, #2f6f6a)"
              strokeWidth={2.6}
              strokeDasharray="6 5"
              vectorEffect="non-scaling-stroke"
            />
          )}

          {/* drag ghost — grab offset preserved, rendered above everything */}
          {gestureView && (() => {
            const node = scene.nodes.find((item) => item.ref.id === gestureView.objectId);
            if (!node) return null;
            return (
              <g
                className="lab-scene__ghost"
                data-testid="chem-lab-drag-ghost"
                transform={`translate(${gestureView.worldX} ${gestureView.worldY})`}
                opacity={0.85}
                pointerEvents="none"
              >
                <LabEquipment
                  node={node}
                  selected={false}
                  held={false}
                  dimmed={false}
                  heating={heatingVessels.has(node.ref.id)}
                  hotplateActive={hotplateDevices.has(node.ref.id)}
                  seed={`${seedBase}:${node.ref.id}`}
                />
              </g>
            );
          })()}
        </g>
      </svg>

      {/* camera controls */}
      <div className="pointer-events-none absolute right-2 top-2 z-10 flex gap-1">
        {selectedId && !readOnly && !locked && (
          <>
            <button
              type="button"
              className="pointer-events-auto rounded-[8px] border border-border-light bg-surface/90 px-2 py-1 text-[10px] font-medium text-fg-secondary shadow-sm lab-scene__hit focus-visible:outline-2 focus-visible:outline-accent"
              data-testid="chem-lab-move-button"
              aria-pressed={targetSelectionFor === selectedId}
              onClick={() => requestTargetSelection(selectedId)}
            >
              {tr("sceneMove")}
            </button>
            <button
              type="button"
              className="pointer-events-auto rounded-[8px] border border-border-light bg-surface/90 px-2 py-1 text-[10px] font-medium text-fg-secondary shadow-sm lab-scene__hit focus-visible:outline-2 focus-visible:outline-accent"
              onClick={() => {
                requestTargetSelection(null);
                onBackgroundClick();
              }}
            >
              {tr("deselect")}
            </button>
          </>
        )}
        <button
          type="button"
          className="pointer-events-auto rounded-[8px] border border-border-light bg-surface/90 px-2 py-1 text-[10px] font-medium text-fg-secondary shadow-sm lab-scene__hit focus-visible:outline-2 focus-visible:outline-accent"
          data-testid="chem-lab-camera-overview"
          aria-pressed={cameraMode === "overview"}
          onClick={() => {
            setCamera(OVERVIEW_CAMERA);
            setCameraMode("overview");
          }}
        >
          {tr("cameraOverview")}
        </button>
        <button
          type="button"
          className="pointer-events-auto rounded-[8px] border border-border-light bg-surface/90 px-2 py-1 text-[10px] font-medium text-fg-secondary shadow-sm lab-scene__hit focus-visible:outline-2 focus-visible:outline-accent"
          data-testid="chem-lab-camera-focus"
          aria-pressed={cameraMode === "focus"}
          onClick={() => {
            const node = scene.nodes.find((item) => item.ref.id === (selectedId ?? scene.nodes[0]?.ref.id));
            if (!node) return;
            const center = {
              x: node.world.x + node.world.width / 2,
              y: node.world.y + node.world.height / 2,
            };
            setCamera(boundCamera(focusCameraOn(center, frameViewport, node.ref.id), frame, frameViewport));
            setCameraMode("focus");
          }}
        >
          {tr("cameraFocus")}
        </button>
      </div>

      {/* gesture / move status (visual + aria-live) */}
      {(statusText || gestureView) && (
        <p
          className="pointer-events-none absolute left-1/2 top-2 z-10 -translate-x-1/2 rounded-full border border-border-light bg-surface/95 px-3 py-1 text-[11px] font-medium text-fg-secondary shadow-sm"
          data-testid="chem-lab-move-status"
          role="status"
          aria-live="polite"
        >
          {statusText}
        </p>
      )}

      {/* DOM overlay: the only pointer entry points */}
      <div
        ref={(el) => {
          setOverlayEl(el);
        }}
        className="pointer-events-none absolute inset-0 z-[5]"
      >
        {hitBoxes.map(({ node, rect }) => {
          const box = expandedRect(rect);
          if (box.width <= 0 || box.height <= 0) return null;
          const summary = nodeSummary(node, tr, listSeparator);
          const chip = highlightChip(node, tr);
          const disabled = locked || readOnly;
          return (
            <button
              key={node.ref.id}
              type="button"
              data-chem-lab-hot="object"
              data-testid={`chem-lab-object-${node.ref.id}`}
              disabled={disabled}
              aria-pressed={selectedId === node.ref.id}
              aria-label={summary ? `${node.label}，${summary}` : node.label}
              className="lab-scene__hit pointer-events-auto absolute flex cursor-grab flex-col items-center justify-end rounded-[10px] p-0.5 focus-visible:outline-2 focus-visible:outline-accent active:cursor-grabbing"
              style={{
                left: `${box.left}px`,
                top: `${box.top}px`,
                width: `${box.width}px`,
                height: `${box.height}px`,
              }}
              onPointerDown={(event) => startObjectPointer(node.ref, event)}
              onDragStart={(event) => event.preventDefault()}
              onClick={(event) => {
                if (shouldSuppressClick()) return;
                onSelect(node.ref);
                if (node.ref.type === "equipment") onInstrumentTap(node.ref.id, event.currentTarget);
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  // keyboard activation == click path (select + instrument tap)
                  event.preventDefault();
                  onSelect(node.ref);
                  if (node.ref.type === "equipment") {
                    onInstrumentTap(node.ref.id, event.currentTarget);
                  }
                }
              }}
            >
              <span className="pointer-events-none max-w-[120px] truncate rounded-full bg-surface/90 px-2 py-0.5 text-[10px] font-medium text-fg-secondary shadow-sm">
                {node.label}
              </span>
              {node.instrument?.reading && (
                <span className="tnum pointer-events-none mt-0.5 rounded-full bg-accent-soft px-2 py-0.5 text-[10px] font-semibold text-accent-strong">
                  {node.instrument.reading} {node.instrument.unit}
                </span>
              )}
              {chip && (
                <span
                  className={`pointer-events-none mt-0.5 rounded-full px-2 py-0.5 text-[10px] font-medium ${
                    chip.danger ? "bg-danger/10 text-danger" : "bg-accent-soft/80 text-accent-strong"
                  }`}
                >
                  {chip.text}
                </span>
              )}
            </button>
          );
        })}

        {/* slot targets: click/keyboard move path and held placement */}
        {(targetSelectionFor || (readOnly ? false : targetSelectionFor)) && slotBoxes.map(({ slot, rect }) => {
          const box = expandedRect(rect);
          const occupiedByOther = slot.occupiedBy !== null && slot.occupiedBy !== targetSelectionFor;
          return (
            <button
              key={`slot-${slot.id}`}
              type="button"
              data-chem-lab-hot="slot"
              data-testid={`chem-lab-move-target-${slot.id}`}
              disabled={occupiedByOther}
              aria-label={
                occupiedByOther
                  ? tr("spotOccupied").replace("%s", slot.id).replace("%o", String(slot.occupiedBy ?? ""))
                  : tr("moveToSpot").replace("%s", slot.id)
              }
              className="lab-scene__hit pointer-events-auto absolute cursor-pointer rounded-[12px] border-2 border-dashed border-accent/60 bg-accent-soft/25 focus-visible:outline-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:border-border-light disabled:bg-surface/20 disabled:opacity-70"
              style={{
                left: `${box.left}px`,
                top: `${box.top}px`,
                width: `${box.width}px`,
                height: `${box.height}px`,
              }}
              onClick={() => {
                if (targetSelectionFor) chooseTargetByKeyboard(targetSelectionFor, { kind: "slot", id: slot.id });
              }}
            />
          );
        })}
      </div>
    </div>
  );
}

function affineToCssTransformFromPose(camera: CameraPose): string {
  return affineToCssTransform({
    a: camera.scale,
    b: 0,
    c: 0,
    d: camera.scale,
    e: camera.translateX,
    f: camera.translateY,
  });
}
