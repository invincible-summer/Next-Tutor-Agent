"use client";

/**
 * Observation log: emitted observation facts (newest last), rejections, and
 * the student's own notes — handwritten notes are this-round learning records
 * only, never lab facts, and stay in memory. Clicking an observation opens
 * the evidence chain.
 */
import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/Button";
import { FIELD_CLS } from "@/components/ui/Input";
import type { ChemLabEvent, ChemLabObservation } from "@/lib/api-chem-lab";

export interface ObservationEntry {
  seq: number;
  key: string;
  vesselId: string;
  vesselName: string;
  simTimeMs: number;
  rejected?: boolean;
  note?: boolean;
  observation?: ChemLabObservation;
}

export interface ObservationLogProps {
  observations: ChemLabObservation[];
  events: ChemLabEvent[];
  vesselName: (vesselId: string) => string;
  tr: (key: string, fallback?: string) => string;
  onOpenEvidence: (observation: ChemLabObservation) => void;
}

function simClock(ms: number): string {
  const total = Math.floor(ms / 1000);
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export function ObservationLog({
  observations,
  events,
  vesselName,
  tr,
  onOpenEvidence,
}: ObservationLogProps) {
  const [notes, setNotes] = useState<Array<{ id: number; text: string }>>([]);
  const [draft, setDraft] = useState("");

  const entries: ObservationEntry[] = [
    ...observations.map((obs) => ({
      seq: obs.seq,
      key: obs.key,
      vesselId: obs.vessel_id ?? "",
      vesselName: vesselName(obs.vessel_id ?? ""),
      simTimeMs: obs.sim_time_ms,
      observation: obs,
    })),
    ...events
      .filter((event) => event.kind === "command_rejected")
      .map((event) => ({
        seq: event.seq,
        key: "command_rejected",
        vesselId: "",
        vesselName: "",
        simTimeMs: event.sim_time_ms,
        rejected: true,
      })),
  ].sort((a, b) => a.seq - b.seq);

  const addNote = (event: FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text) return;
    setNotes((prev) => [...prev, { id: Date.now(), text }].slice(-20));
    setDraft("");
  };

  return (
    <div data-testid="chem-lab-observation-log">
      <ul className="space-y-1.5" aria-live="polite">
        {entries.length === 0 && notes.length === 0 && (
          <li className="text-xs leading-5 text-muted">{tr("noObservations")}</li>
        )}
        {entries.map((entry, index) => (
          <li key={`${entry.seq}-${index}`}>
            {entry.rejected ? (
              <span className="block rounded-[10px] border border-[#e0a23c]/30 bg-[#e0a23c]/8 px-2.5 py-1.5 text-[11px] leading-4 text-fg-secondary">
                {tr("rejectedEntry")}
                <span className="tnum ml-1.5 text-muted">{simClock(entry.simTimeMs)}</span>
              </span>
            ) : (
              <button
                type="button"
                onClick={() => entry.observation && onOpenEvidence(entry.observation)}
                data-testid="chem-lab-observation"
                className="block w-full cursor-pointer rounded-[10px] border border-border-light bg-surface px-2.5 py-1.5 text-left transition-colors hover:border-accent/40 focus-visible:outline-2 focus-visible:outline-accent"
              >
                <span className="text-[11px] leading-4 text-fg">
                  {tr(`obs.${entry.key}`, entry.key)}
                  {entry.vesselName ? ` · ${entry.vesselName}` : ""}
                </span>
                <span className="tnum mt-0.5 block text-[10px] text-muted">
                  {tr("simTime")} {simClock(entry.simTimeMs)} · {tr("openEvidence")}
                </span>
              </button>
            )}
          </li>
        ))}
        {notes.map((note) => (
          <li key={note.id}
            className="rounded-[10px] border border-dashed border-accent/30 bg-accent-soft/40 px-2.5 py-1.5 text-[11px] leading-4 text-fg-secondary">
            <span className="mr-1.5 rounded bg-accent-soft px-1 text-[9px] font-semibold text-accent-strong">
              {tr("myNote")}
            </span>
            {note.text}
          </li>
        ))}
      </ul>
      <form onSubmit={addNote} className="mt-2 flex gap-1.5">
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          maxLength={200}
          placeholder={tr("notePlaceholder")}
          aria-label={tr("notePlaceholder")}
          className={`${FIELD_CLS} h-8 text-xs`}
          data-testid="chem-lab-note-input"
        />
        <Button type="submit" size="sm" variant="outline" disabled={!draft.trim()}>
          {tr("addNote")}
        </Button>
      </form>
    </div>
  );
}
