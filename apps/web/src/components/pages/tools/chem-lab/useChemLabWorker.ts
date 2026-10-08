"use client";

/**
 * Worker lifecycle for chem-lab predictions. Creates the Web Worker lazily,
 * serialises every request through one promise chain (predictions are cheap;
 * ordering matters more than parallelism), and transparently falls back to
 * the same engine on the main thread when Worker construction or the worker
 * itself fails — predictions stay local previews either way, the server ACK
 * remains the only authority.
 */
import { useCallback, useEffect, useRef } from "react";
import type { AnyDict } from "@next-tutor/domain";
import { createChemLabEngineRunner } from "./chem-lab-engine";
import type {
  ChemLabPrediction,
  ChemLabWorkerRequest,
  ChemLabWorkerResponse,
} from "./chem-lab-worker";

export interface ChemLabWorkerHandle {
  /** True when predictions run on the main thread (Worker unavailable). */
  readonly degraded: boolean;
  init(pack: AnyDict, state: AnyDict): Promise<void>;
  predict(command: AnyDict, commandId: string): Promise<ChemLabPrediction>;
  replay(commands: Array<{ command: AnyDict; commandId: string }>): Promise<ChemLabPrediction>;
  reset(state: AnyDict): Promise<void>;
  dispose(): void;
}

function createHandle(): ChemLabWorkerHandle {
  let worker: Worker | null = null;
  let broken = false;
  let disposed = false;
  let counter = 0;
  const fallback = createChemLabEngineRunner();
  let fallbackReady = false;
  const pending = new Map<number, { resolve: (p: ChemLabPrediction) => void; reject: (e: Error) => void }>();
  let controlWaiter: { resolve: () => void; reject: (e: Error) => void } | null = null;
  let chain: Promise<unknown> = Promise.resolve();

  try {
    worker = new Worker(new URL("./chem-lab-worker.ts", import.meta.url), { type: "module" });
  } catch {
    worker = null;
    broken = true;
  }

  const failPending = (error: Error) => {
    for (const entry of pending.values()) entry.reject(error);
    pending.clear();
    controlWaiter?.reject(error);
    controlWaiter = null;
  };

  if (worker) {
    worker.onmessage = (event: MessageEvent<ChemLabWorkerResponse>) => {
      const message = event.data;
      if (message.type === "prediction") {
        const waiter = pending.get(message.predictionId);
        if (waiter) {
          pending.delete(message.predictionId);
          waiter.resolve(message);
        }
        return;
      }
      if (message.type === "ready") {
        const waiter = controlWaiter;
        controlWaiter = null;
        waiter?.resolve();
        return;
      }
      const error = new Error(message.message);
      if (message.predictionId !== undefined && pending.has(message.predictionId)) {
        const waiter = pending.get(message.predictionId);
        pending.delete(message.predictionId);
        waiter?.reject(error);
      } else {
        controlWaiter?.reject(error);
        controlWaiter = null;
      }
    };
    worker.onerror = () => {
      broken = true;
      failPending(new Error("chem-lab worker failed"));
      worker?.terminate();
      worker = null;
    };
  }

  const enqueue = <T,>(fn: () => Promise<T>): Promise<T> => {
    const run = chain.then(fn);
    chain = run.catch(() => undefined);
    return run;
  };

  const postControl = (message: ChemLabWorkerRequest): Promise<void> =>
    new Promise<void>((resolve, reject) => {
      if (!worker) {
        reject(new Error("worker unavailable"));
        return;
      }
      controlWaiter = { resolve, reject };
      worker.postMessage(message);
    });

  const postPredict = (message: ChemLabWorkerRequest & { predictionId: number }): Promise<ChemLabPrediction> =>
    new Promise<ChemLabPrediction>((resolve, reject) => {
      if (!worker) {
        reject(new Error("worker unavailable"));
        return;
      }
      pending.set(message.predictionId, { resolve, reject });
      worker.postMessage(message);
    });

  const handle: ChemLabWorkerHandle = {
    get degraded() {
      return broken || !worker;
    },
    init(pack, state) {
      return enqueue(async () => {
        if (disposed) throw new Error("worker disposed");
        fallback.init(pack, state);
        fallbackReady = true;
        if (worker && !broken) await postControl({ type: "init", pack, state });
      });
    },
    reset(state) {
      return enqueue(async () => {
        if (disposed) throw new Error("worker disposed");
        if (fallbackReady) fallback.reset(state);
        if (worker && !broken) await postControl({ type: "reset", state });
      });
    },
    predict(command, commandId) {
      return enqueue(async () => {
        if (disposed) throw new Error("worker disposed");
        if (!worker || broken) {
          if (!fallbackReady) throw new Error("engine not initialised");
          const result = fallback.runOne(command, commandId);
          return { type: "prediction", predictionId: ++counter, commandId, ...result };
        }
        const predictionId = ++counter;
        return postPredict({ type: "predict", predictionId, command, commandId });
      });
    },
    replay(commands) {
      return enqueue(async () => {
        if (disposed) throw new Error("worker disposed");
        if (!worker || broken) {
          if (!fallbackReady) throw new Error("engine not initialised");
          const result = fallback.runAll(commands);
          return { type: "prediction", predictionId: ++counter, commandId: commands.at(-1)?.commandId ?? "", ...result };
        }
        const predictionId = ++counter;
        return postPredict({ type: "replay", predictionId, commands });
      });
    },
    dispose() {
      disposed = true;
      failPending(new Error("worker disposed"));
      if (worker) {
        try {
          worker.postMessage({ type: "dispose" } satisfies ChemLabWorkerRequest);
        } catch { /* already gone */ }
        worker.terminate();
        worker = null;
      }
    },
  };
  return handle;
}

/** Returns a getter; the worker is created on first use and terminated on unmount. */
export function useChemLabWorker(): () => ChemLabWorkerHandle {
  const ref = useRef<ChemLabWorkerHandle | null>(null);
  useEffect(
    () => () => {
      ref.current?.dispose();
      ref.current = null;
    },
    [],
  );
  return useCallback(() => {
    if (!ref.current) ref.current = createHandle();
    return ref.current;
  }, []);
}
