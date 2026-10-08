"use client";

/**
 * Thin presenter: derives the read-only SceneModel from the pack + display
 * and hands it to LabScene, which owns the visual stack and the single
 * pointer entry. LabStage keeps its public props contract (onSelect /
 * onMoveObject / onDragOperation / onSlotTarget / onInstrumentTap) so the
 * workspace wiring stays stable; `onSlotTarget` is retained for compatibility
 * — the new move flow resolves through the gesture controller's atomic
 * `move` path (`onMoveObject`) for pointer, click and keyboard alike.
 */
import { useMemo } from "react";
import type { ChemLabDisplay } from "./useChemLabSession";
import type { ChemLabObjectRef, OperationDraft } from "./interaction";
import { deriveSceneModel } from "./scene/scene-model";
import type { LabMotion } from "./scene/presentation-model";
import { LabScene } from "./scene/LabScene";
import "./scene/lab-scene.css";

type AnyRecord = Record<string, unknown>;

export interface LabStageProps {
  pack: AnyRecord | null;
  display: ChemLabDisplay;
  language: string;
  selected: ChemLabObjectRef | null;
  busy: boolean;
  /** Read-only presentation (history view): locks every write path. */
  readOnly?: boolean;
  sessionId?: string | null;
  onSelect: (ref: ChemLabObjectRef) => void;
  /** Background / cancel-selection path (Escape and empty-space clicks). */
  onDeselect?: () => void;
  /** Atomic move intent — one `move` command, confirmed by the engine. */
  onMoveObject: (objectId: string, slotId: string) => void;
  /** Drop onto another object opens the shared operation panel. */
  onDragOperation: (draft: OperationDraft) => void;
  /** Kept for contract stability; the move flow now goes through
   * `onMoveObject` for every input path. */
  onSlotTarget?: (slotId: string) => void;
  onInstrumentTap: (equipmentId: string, anchor: HTMLElement) => void;
  /** Presentation-only one-shot motion (from useLabPresentation). */
  motion?: LabMotion | null;
}

export function LabStage({
  pack,
  display,
  language,
  selected,
  busy,
  readOnly = false,
  sessionId = null,
  onSelect,
  onDeselect,
  onMoveObject,
  onDragOperation,
  onInstrumentTap,
  motion = null,
}: LabStageProps) {
  const scene = useMemo(
    () => deriveSceneModel(pack, display, language),
    [pack, display, language],
  );

  const heating = useMemo(() => {
    const vessels = (display.engineState?.vessels ?? {}) as AnyRecord;
    const vesselIds = new Set<string>();
    const deviceIds = new Set<string>();
    for (const [id, vessel] of Object.entries(vessels)) {
      const heat = (vessel as AnyRecord).heat as AnyRecord | null | undefined;
      if (heat?.device_id) {
        vesselIds.add(id);
        deviceIds.add(String(heat.device_id));
      }
    }
    return { vesselIds, deviceIds };
  }, [display.engineState]);

  const selectedId = selected ? selected.id : null;
  const seedBase = `${sessionId ?? "session"}:${display.revision}`;

  return (
    <div className="h-full w-full" data-testid="chem-lab-stage">
      <LabScene
        scene={scene}
        language={language}
        selectedId={selectedId}
        locked={busy}
        readOnly={readOnly}
        sessionId={sessionId}
        seedBase={seedBase}
        heatingVessels={heating.vesselIds}
        hotplateDevices={heating.deviceIds}
        motion={motion}
        onSelect={onSelect}
        onMoveObject={onMoveObject}
        onDragOperation={onDragOperation}
        onInstrumentTap={onInstrumentTap}
        onBackgroundClick={() => onDeselect?.()}
      />
    </div>
  );
}
