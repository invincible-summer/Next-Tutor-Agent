"use client";

/**
 * Presentation lifecycle for one lab motion at a time (see the 演出层 notes in
 * docs/architecture/chem-lab.md): derives
 * the motion from the session's read-only `lastTransition` at render time
 * (the sanctioned derived-state pattern — no setState-in-effect), expires
 * finished one-shots via a timeout, and cancels on tab hide. Idle sessions
 * hold no timers and do zero React updates per frame.
 */
import { useEffect, useState } from "react";
import type { LabDisplayTransition } from "../useChemLabSession";
import { deriveMotion, type LabMotion } from "./presentation-model";

export interface LabPresentationInput {
  sessionId: string | null;
  packHash: string | null;
  transition: LabDisplayTransition | null;
  /** Conflict / offline states cancel any in-flight decoration. */
  conflict: boolean;
}

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return reduced;
}

export function useLabPresentation(input: LabPresentationInput): LabMotion | null {
  const reducedMotion = usePrefersReducedMotion();
  const { sessionId, packHash, transition, conflict } = input;
  const [expiredToken, setExpiredToken] = useState<string | null>(null);

  // Derived at render: the current motion, unless its one-shot already expired.
  const derived = deriveMotion({ sessionId, packHash, conflict, reducedMotion }, transition);
  const motion = derived && derived.token !== expiredToken ? derived : null;

  // Accepted/rejected one-shots self-clear; a pending motion has no expiry —
  // its own ACK (same token, new stage) or a cancel above replaces it.
  const token = motion?.token ?? null;
  const stage = motion?.stage ?? null;
  const durationMs = motion?.durationMs ?? 0;
  useEffect(() => {
    if (!token || stage === "pending") return;
    const id = window.setTimeout(() => setExpiredToken(token), durationMs + 120);
    return () => window.clearTimeout(id);
  }, [token, stage, durationMs]);

  // Hidden tab: stop decorating, realign to the latest frame when visible.
  useEffect(() => {
    const onVisibility = () => {
      if (document.visibilityState === "hidden") setExpiredToken(token);
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, [token]);

  return motion;
}
