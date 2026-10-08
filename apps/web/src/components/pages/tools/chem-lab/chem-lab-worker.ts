/// <reference lib="webworker" />
/**
 * Chem-lab prediction worker: runs the shared engine runner off the main
 * thread so the bench stays responsive. Protocol: init / predict / replay /
 * reset / dispose. The worker is never an authority — the server re-runs
 * every command and its ACK replaces any divergent prediction.
 */
import type { AnyDict } from "@next-tutor/domain";
import { createChemLabEngineRunner, type EngineRunResult } from "./chem-lab-engine";

export interface ChemLabWorkerInit {
  type: "init";
  pack: AnyDict;
  state: AnyDict;
}
export interface ChemLabWorkerPredict {
  type: "predict";
  predictionId: number;
  command: AnyDict;
  commandId: string;
}
export interface ChemLabWorkerReplay {
  type: "replay";
  predictionId: number;
  commands: Array<{ command: AnyDict; commandId: string }>;
}
export interface ChemLabWorkerReset {
  type: "reset";
  state: AnyDict;
}
export interface ChemLabWorkerDispose {
  type: "dispose";
}
export type ChemLabWorkerRequest =
  | ChemLabWorkerInit
  | ChemLabWorkerPredict
  | ChemLabWorkerReplay
  | ChemLabWorkerReset
  | ChemLabWorkerDispose;

export interface ChemLabPrediction extends EngineRunResult {
  type: "prediction";
  predictionId: number;
  commandId: string;
}
export interface ChemLabWorkerReady {
  type: "ready";
  requestType: "init" | "reset";
}
export interface ChemLabWorkerError {
  type: "error";
  predictionId?: number;
  message: string;
}
export type ChemLabWorkerResponse = ChemLabPrediction | ChemLabWorkerReady | ChemLabWorkerError;

const runner = createChemLabEngineRunner();

/** Minimal structural worker scope (avoids pulling in lib.webworker). */
interface WorkerScope {
  onmessage: ((event: MessageEvent<ChemLabWorkerRequest>) => void) | null;
  postMessage(message: ChemLabWorkerResponse): void;
  close(): void;
}
const ctx = self as unknown as WorkerScope;

ctx.onmessage = (event: MessageEvent<ChemLabWorkerRequest>) => {
  const message = event.data;
  try {
    switch (message.type) {
      case "init":
        runner.init(message.pack, message.state);
        ctx.postMessage({ type: "ready", requestType: "init" } satisfies ChemLabWorkerReady);
        return;
      case "reset":
        runner.reset(message.state);
        ctx.postMessage({ type: "ready", requestType: "reset" } satisfies ChemLabWorkerReady);
        return;
      case "dispose":
        ctx.close();
        return;
      case "predict":
        ctx.postMessage({
          type: "prediction",
          predictionId: message.predictionId,
          commandId: message.commandId,
          ...runner.runOne(message.command, message.commandId),
        } satisfies ChemLabPrediction);
        return;
      case "replay":
        ctx.postMessage({
          type: "prediction",
          predictionId: message.predictionId,
          commandId: message.commands.at(-1)?.commandId ?? "",
          ...runner.runAll(message.commands),
        } satisfies ChemLabPrediction);
        return;
    }
  } catch (error) {
    ctx.postMessage({
      type: "error",
      predictionId: "predictionId" in message ? message.predictionId : undefined,
      message: error instanceof Error ? error.message : String(error),
    } satisfies ChemLabWorkerError);
  }
};
