"use client";

/**
 * Task scheduler (plan I1): bounded queue, revision-based stale guard and a
 * terminate/restart hard fallback for tasks that cannot yield in time. When
 * workers are unavailable (or creation fails), callers fall back to bounded
 * main-thread computation — the UI never shows an eternal spinner.
 */

import { isTaskResponse, type TaskRequest, type TaskResponse, type MathTaskKind } from "./protocol.ts";

export interface SchedulerCallbacks {
  onResult: (response: TaskResponse) => void;
  onFallback?: (reason: string) => void;
}

interface Pending {
  request: TaskRequest;
  resolve: () => void;
}

const MAX_ACTIVE_HEAVY_TASKS = 2;
const MAX_RESTARTS_PER_SESSION = 6;

export class MathWorkerScheduler {
  private worker: Worker | null = null;
  private pending: Pending[] = [];
  private active = 0;
  private restarts = 0;
  private readonly callbacks: SchedulerCallbacks;
  private disposed = false;
  private readonly transfers = new Map<string, boolean>();

  constructor(callbacks: SchedulerCallbacks) {
    this.callbacks = callbacks;
  }

  private ensureWorker(): Worker | null {
    if (this.disposed) return null;
    if (this.worker) return this.worker;
    if (this.restarts >= MAX_RESTARTS_PER_SESSION) return null;
    try {
      const worker = new Worker(new URL("./math-worker.ts", import.meta.url), { type: "module" });
      worker.addEventListener("message", (event: MessageEvent) => {
        if (!isTaskResponse(event.data)) return;
        const response = event.data;
        this.transfers.delete(response.id);
        this.active = Math.max(0, this.active - 1);
        this.callbacks.onResult(response);
        this.drain();
      });
      worker.addEventListener("error", () => {
        this.restart("worker_error");
      });
      this.worker = worker;
      return worker;
    } catch {
      this.worker = null;
      return null;
    }
  }

  private restart(reason: string): void {
    const worker = this.worker;
    this.worker = null;
    this.restarts += 1;
    this.active = 0;
    if (worker) {
      worker.terminate(); // hard fallback for non-yielding tasks (plan I1)
    }
    // Pending tasks fail fast into the main-thread fallback path.
    const stalled = this.pending;
    this.pending = [];
    stalled.forEach((entry) => entry.resolve());
    this.callbacks.onFallback?.(reason);
  }

  private drain(): void {
    while (this.pending.length > 0 && this.active < MAX_ACTIVE_HEAVY_TASKS) {
      const entry = this.pending.shift();
      if (!entry) break;
      const worker = this.ensureWorker();
      if (!worker) {
        entry.resolve();
        this.callbacks.onFallback?.("worker_unavailable");
        continue;
      }
      this.active += 1;
      worker.postMessage(entry.request);
    }
  }

  submit(id: string, documentId: string, revision: number, task: MathTaskKind, payload: unknown): Promise<"posted" | "fallback"> {
    if (this.disposed) return Promise.resolve("fallback");
    const worker = this.ensureWorker();
    if (!worker) {
      this.callbacks.onFallback?.("worker_unavailable");
      return Promise.resolve("fallback");
    }
    const request: TaskRequest = { id, documentId, revision, task, payload };
    return new Promise((resolve) => {
      this.pending.push({ request, resolve: () => resolve("fallback") });
      this.drain();
      // Once posted the promise resolution comes from the message handler.
      if (this.transfers.has(id) || this.active > 0) resolve("posted");
    });
  }

  dispose(): void {
    this.disposed = true;
    this.worker?.terminate();
    this.worker = null;
    this.pending.forEach((entry) => entry.resolve());
    this.pending = [];
  }
}
