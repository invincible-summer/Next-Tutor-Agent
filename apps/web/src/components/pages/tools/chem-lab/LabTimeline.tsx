"use client";

/**
 * Lab timeline: sim clock, revision, sync status, event ticks and checkpoint
 * markers on one continuous (horizontally windowed) track, plus the session
 * actions — checkpoint, branch-from-checkpoint, reset, finish, compare.
 */
import { useMemo, useState } from "react";
import { Button } from "@/components/ui/Button";
import { FIELD_CLS } from "@/components/ui/Input";
import type { ChemLabCheckpoint, ChemLabEvent } from "@/lib/api-chem-lab";
import type { ChemLabSyncStatus } from "./useChemLabSession";

function simClock(ms: number): string {
  const total = Math.floor(ms / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

const TRACK_TICK_CAP = 160;

export interface LabTimelineProps {
  simTimeMs: number;
  revision: number;
  serverRevision: number;
  syncStatus: ChemLabSyncStatus;
  pendingCount: number;
  checkpoints: ChemLabCheckpoint[];
  events: ChemLabEvent[];
  finished: boolean;
  phase: string;
  busy: boolean;
  tr: (key: string, fallback?: string) => string;
  onCheckpoint: (label: string) => void;
  onFork: (checkpointId: string) => void;
  onReset: () => void;
  onFinish: () => void;
  onCompare: () => void;
}

export function LabTimeline({
  simTimeMs,
  revision,
  serverRevision,
  syncStatus,
  pendingCount,
  checkpoints,
  events,
  finished,
  phase,
  busy,
  tr,
  onCheckpoint,
  onFork,
  onReset,
  onFinish,
  onCompare,
}: LabTimelineProps) {
  const [naming, setNaming] = useState(false);
  const [label, setLabel] = useState("");

  const ticks = useMemo(() => events.slice(-TRACK_TICK_CAP), [events]);
  const maxSeq = useMemo(() => {
    let max = 1;
    for (const event of ticks) max = Math.max(max, event.seq);
    for (const checkpoint of checkpoints) max = Math.max(max, checkpoint.seq);
    return max;
  }, [ticks, checkpoints]);

  const statusText = (() => {
    switch (syncStatus) {
      case "synced":
        return tr("sync.synced");
      case "predicting":
        return tr("sync.predicting");
      case "pending_sync":
        return tr("sync.pending").replace("%n", String(pendingCount));
      case "conflict":
        return tr("sync.conflict");
      case "offline_preview":
        return tr("sync.offline");
      case "safety_locked":
        return tr("sync.safety");
      default:
        return "";
    }
  })();

  const submitCheckpoint = () => {
    const text = label.trim();
    if (!text) return;
    onCheckpoint(text.slice(0, 80));
    setLabel("");
    setNaming(false);
  };

  return (
    <div className="space-y-2" data-testid="chem-lab-timeline">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span className="tnum text-xs font-semibold text-fg" aria-label={tr("simTime")}>
          {simClock(simTimeMs)}
        </span>
        <span className="tnum text-[10px] text-muted">
          {tr("revisionLabel").replace("%n", String(revision))}
          {revision !== serverRevision ? ` · ${tr("serverRevision").replace("%n", String(serverRevision))}` : ""}
        </span>
        <span
          role="status"
          data-testid="chem-lab-sync-status"
          className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${
            syncStatus === "synced"
              ? "border-accent/30 bg-accent-soft text-accent-strong"
              : syncStatus === "conflict" || syncStatus === "safety_locked"
                ? "border-danger/40 bg-danger/10 text-danger"
                : syncStatus === "offline_preview"
                  ? "border-[#e0a23c]/40 bg-[#e0a23c]/10 text-[#8a6420]"
                  : "border-border-light text-muted"
          }`}
        >
          {statusText}
        </span>
        {phase === "completed" && (
          <span className="rounded-full bg-accent-soft px-2 py-0.5 text-[10px] font-medium text-accent-strong">
            {tr("phaseCompleted")}
          </span>
        )}
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          {naming ? (
            <form
              className="flex items-center gap-1.5"
              onSubmit={(event) => {
                event.preventDefault();
                submitCheckpoint();
              }}
            >
              <input
                autoFocus
                value={label}
                onChange={(event) => setLabel(event.target.value)}
                maxLength={80}
                placeholder={tr("checkpointLabel")}
                aria-label={tr("checkpointLabel")}
                className={`${FIELD_CLS} h-8 w-40 text-xs`}
                data-testid="chem-lab-checkpoint-input"
                onKeyDown={(event) => {
                  if (event.key === "Escape") setNaming(false);
                }}
              />
              <Button type="submit" size="sm" disabled={!label.trim() || busy}>{tr("save")}</Button>
              <Button type="button" size="sm" variant="ghost" onClick={() => setNaming(false)}>{tr("cancel")}</Button>
            </form>
          ) : (
            <Button size="sm" variant="outline" disabled={busy || pendingCount > 0 || finished}
              onClick={() => setNaming(true)} data-testid="chem-lab-checkpoint">
              {tr("checkpoint")}
            </Button>
          )}
          <Button size="sm" variant="outline" disabled={busy || pendingCount > 0} onClick={onReset}
            data-testid="chem-lab-reset">
            {tr("reset")}
          </Button>
          <Button size="sm" variant="outline" disabled={busy || pendingCount > 0} onClick={onCompare}
            data-testid="chem-lab-compare">
            {tr("compare")}
          </Button>
          <Button size="sm" disabled={busy || pendingCount > 0 || finished} onClick={onFinish}
            data-testid="chem-lab-finish">
            {tr("finish")}
          </Button>
        </div>
      </div>
      <div
        className="relative h-8 overflow-x-auto rounded-[8px] border border-border-light bg-surface"
        role="img"
        aria-label={tr("timelineTrack")}
      >
        <div className="relative h-full min-w-full" style={{ width: `${Math.max(100, maxSeq * 6)}px` }}>
          <div className="absolute inset-x-0 top-1/2 h-px bg-border" />
          {ticks.map((event) => (
            <span
              key={`tick-${event.seq}`}
              className={`absolute top-1/2 h-2.5 w-px -translate-y-1/2 ${
                event.kind === "command_rejected" ? "bg-[#e0a23c]" : "bg-muted/50"
              }`}
              style={{ left: `${(event.seq / maxSeq) * 100}%` }}
            />
          ))}
          {checkpoints.map((checkpoint) => (
            <button
              key={checkpoint.checkpoint_id}
              type="button"
              disabled={busy}
              onClick={() => onFork(checkpoint.checkpoint_id)}
              title={`${checkpoint.label} · ${simClock(checkpoint.sim_time_ms)} · ${tr("forkFromHere")}`}
              aria-label={`${tr("forkFromHere")}：${checkpoint.label}`}
              data-testid="chem-lab-checkpoint-marker"
              className="absolute top-1/2 flex h-6 w-6 -translate-x-1/2 -translate-y-1/2 cursor-pointer items-center justify-center rounded-full border border-accent/50 bg-accent-soft text-[9px] font-bold text-accent-strong transition-transform hover:scale-110 focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-50"
              style={{ left: `${(checkpoint.seq / maxSeq) * 100}%` }}
            >
              ◆
            </button>
          ))}
        </div>
      </div>
      {checkpoints.length > 0 && (
        <p className="text-[10px] leading-4 text-muted">{tr("checkpointHint")}</p>
      )}
    </div>
  );
}
