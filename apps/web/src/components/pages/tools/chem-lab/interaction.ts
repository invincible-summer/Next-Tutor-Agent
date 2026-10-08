/**
 * Interaction model for the chem-lab bench. UI produces InteractionIntents
 * and operation drafts only; the single command factory (OperationToolbar's
 * buildLabCommand) turns a confirmed draft into one closed LabCommand — the
 * click, keyboard and drag paths all funnel through it.
 */
import type { LabCommand } from "@/lib/api-chem-lab";

export type ChemLabObjectType = "vessel" | "equipment";

export interface ChemLabObjectRef {
  type: ChemLabObjectType;
  id: string;
}

export type ChemLabOperation =
  | "aspirate"
  | "dispense"
  | "pour"
  | "heat"
  | "stir"
  | "wait"
  | "measure"
  | "connect"
  | "filter"
  | "wash"
  | "dispose";

export interface OperationDraft {
  action: ChemLabOperation;
  vesselId?: string;
  sourceId?: string;
  targetId?: string;
  instrumentId?: string;
  deviceId?: string;
  apparatusId?: string;
}

/** Confirmed parameters collected by the operation panel. */
export interface OperationParams {
  amountUL?: number;
  rate?: string;
  powerPermille?: number;
  speedPermille?: number;
  durationMs?: number;
  quantity?: string;
  vesselId?: string;
  sourceId?: string;
  targetId?: string;
  instrumentId?: string;
  deviceId?: string;
  apparatusId?: string;
}

/** The one command factory — drag, click and keyboard paths all call this. */
export function buildLabCommand(draft: OperationDraft, params: OperationParams): LabCommand {
  const vesselId = params.vesselId ?? draft.vesselId ?? null;
  const sourceId = params.sourceId ?? draft.sourceId ?? null;
  const targetId = params.targetId ?? draft.targetId ?? null;
  const instrumentId = params.instrumentId ?? draft.instrumentId ?? null;
  switch (draft.action) {
    case "aspirate":
      return { kind: "aspirate", source_id: sourceId, instrument_id: instrumentId, amount_uL: params.amountUL ?? null };
    case "dispense":
      return { kind: "dispense", instrument_id: instrumentId, target_id: targetId, amount_uL: params.amountUL ?? null };
    case "pour":
      return { kind: "pour", source_id: sourceId, target_id: targetId, amount_uL: params.amountUL ?? null, rate: params.rate ?? "normal" };
    case "heat":
      return { kind: "heat", device_id: params.deviceId ?? draft.deviceId ?? null, vessel_id: vesselId, power_permille: params.powerPermille ?? null, duration_ms: params.durationMs ?? null };
    case "stir":
      return { kind: "stir", vessel_id: vesselId, speed_permille: params.speedPermille ?? null, duration_ms: params.durationMs ?? null };
    case "wait":
      return { kind: "wait", duration_ms: params.durationMs ?? null };
    case "measure":
      return { kind: "measure", instrument_id: instrumentId, vessel_id: vesselId, quantity: params.quantity ?? null };
    case "connect":
      return { kind: "connect", vessel_id: vesselId, instrument_id: instrumentId, target_id: targetId };
    case "filter":
      return { kind: "filter", vessel_id: vesselId, apparatus_id: params.apparatusId ?? draft.apparatusId ?? null };
    case "wash":
      return { kind: "wash", instrument_id: instrumentId };
    case "dispose":
      return { kind: "dispose", vessel_id: vesselId, amount_uL: params.amountUL ?? null };
  }
}
