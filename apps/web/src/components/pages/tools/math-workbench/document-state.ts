"use client";

/**
 * Math workbench document state: the working document, semantic dirty
 * tracking, expression drafts, undo/redo history and selection. View state
 * (camera/pan/zoom) lives in the document but never counts as a semantic
 * change and never enters undo history — see `semanticFingerprint` in the
 * domain package. Since ADR-0022 every drawing mode owns an independent
 * bundle here (document + history + drafts + baseline).
 */

import { useCallback, useMemo, useState } from "react";
import {
  applyMathCommand, createMathDocument, semanticFingerprint,
  type DrawingMode, type MathCommand, type MathWorkbenchDocument,
} from "./workbench-types.ts";

export type RightDockTab = "functions" | "objects" | "inspector" | "calculator";

export interface ExpressionDraft {
  /** Raw text per plot field, keyed `${plotId}:${fieldName}`. */
  [plotField: string]: string;
}

/**
 * A function row the user added but has not committed yet (plan D5: "添加函数"
 * creates a draft row — an empty row is not an error and never touches the
 * document until the expressions validate).
 */
export interface PendingPlotDraft {
  key: string;
  kind: string;
  color: string;
  expressions: Record<string, string>;
  domains: Record<string, string>;
}

export interface SavedBaseline {
  documentId: string;
  fingerprint: string;
  savedAt: number;
}

export interface HistoryEntry {
  document: MathWorkbenchDocument;
  label: string;
}

export interface DirtyState {
  dirty: boolean;
  reason: "new" | "document_changed" | "uncommitted_formula" | null;
}

export interface WorkbenchState {
  document: MathWorkbenchDocument;
  /** Not dirty until the first save creates a baseline (D6 state machine). */
  baseline: SavedBaseline | null;
  drafts: ExpressionDraft;
  pendingPlots: PendingPlotDraft[];
  selection: string | null;
  history: { past: HistoryEntry[]; future: HistoryEntry[] };
  /** Last command failure surfaced to the UI (cleared by the next success). */
  lastError: { code: string; at: number } | null;
}

export const HISTORY_LIMIT = 100;

export function freshWorkbenchState(name = "", mode: DrawingMode = "functions2d"): WorkbenchState {
  const document = createMathDocument(name, mode);
  return { document, baseline: null, drafts: {}, pendingPlots: [], selection: null, history: { past: [], future: [] }, lastError: null };
}

/** View commands mutate transient state only — they never enter undo history. */
export function isHistoryCommand(command: MathCommand): boolean {
  if (command.kind === "setView2D" || command.kind === "setCamera3D") return false;
  if (command.kind === "batch") return command.commands.some(isHistoryCommand);
  return true;
}

/**
 * Dirty = no saved baseline, or the semantic fingerprint drifted, or any
 * plot expression field differs from its in-editor draft text, or a pending
 * (not-yet-added) function row has content.
 */
export function computeDirty(state: WorkbenchState): DirtyState {
  if (!state.baseline) return { dirty: true, reason: "new" };
  const committedChanged = semanticFingerprint(state.document) !== state.baseline.fingerprint;
  const draftChanged = Object.entries(state.drafts).some(([key, text]) => {
    const [plotId, field] = key.split(":");
    const plot = [...state.document.plots2d, ...state.document.plots3d].find((p) => p.id === plotId);
    if (!plot) return text.trim().length > 0;
    const committed = String((plot as unknown as Record<string, unknown>)[field ?? ""] ?? "");
    return text.trim() !== committed.trim();
  });
  const pendingChanged = state.pendingPlots.some((row) =>
    Object.values(row.expressions).some((text) => text.trim().length > 0),
  );
  if (committedChanged) return { dirty: true, reason: "document_changed" };
  if (draftChanged || pendingChanged) return { dirty: true, reason: "uncommitted_formula" };
  return { dirty: false, reason: null };
}

export function useWorkbenchState(initial?: WorkbenchState) {
  const [state, setState] = useState<WorkbenchState>(initial ?? freshWorkbenchState());

  const dispatch = useCallback((command: MathCommand, label: string) => {
    setState((prev) => {
      const result = applyMathCommand(prev.document, command);
      if (!result.ok) {
        return { ...prev, lastError: { code: result.diagnostics[0]?.code ?? "command_failed", at: Date.now() } };
      }
      if (!isHistoryCommand(command)) {
        return { ...prev, document: result.value, lastError: null };
      }
      const nextHistory = {
        past: [...prev.history.past, { document: prev.document, label }].slice(-HISTORY_LIMIT),
        future: [],
      };
      return { ...prev, document: result.value, history: nextHistory, lastError: null };
    });
  }, []);

  /** Replace the whole working document (open/import/new/recovery). */
  const replaceDocument = useCallback((document: MathWorkbenchDocument, baseline: SavedBaseline | null) => {
    setState({ document, baseline, drafts: {}, pendingPlots: [], selection: null, history: { past: [], future: [] }, lastError: null });
  }, []);

  const undo = useCallback(() => {
    setState((prev) => {
      const last = prev.history.past[prev.history.past.length - 1];
      if (!last) return prev;
      // View state is transient: keep what the user currently sees instead of
      // rewinding pan/zoom/camera with an edit undo.
      const restored: MathWorkbenchDocument = {
        ...last.document,
        view2d: prev.document.view2d,
        camera3d: prev.document.camera3d,
      };
      return {
        ...prev,
        document: restored,
        history: {
          past: prev.history.past.slice(0, -1),
          future: [{ document: last.document, label: last.label }, ...prev.history.future].slice(0, HISTORY_LIMIT),
        },
      };
    });
  }, []);

  const redo = useCallback(() => {
    setState((prev) => {
      const next = prev.history.future[0];
      if (!next) return prev;
      const restored: MathWorkbenchDocument = {
        ...next.document,
        view2d: prev.document.view2d,
        camera3d: prev.document.camera3d,
      };
      return {
        ...prev,
        document: restored,
        history: {
          past: [...prev.history.past, { document: prev.document, label: next.label }].slice(0, HISTORY_LIMIT),
          future: prev.history.future.slice(1),
        },
      };
    });
  }, []);

  const setDraft = useCallback((plotId: string, text: string) => {
    setState((prev) => ({ ...prev, drafts: { ...prev.drafts, [plotId]: text } }));
  }, []);

  const clearDraft = useCallback((plotId: string) => {
    setState((prev) => {
      if (!(plotId in prev.drafts)) return prev;
      const drafts = { ...prev.drafts };
      delete drafts[plotId];
      return { ...prev, drafts };
    });
  }, []);

  const setPendingPlots = useCallback((updater: (rows: PendingPlotDraft[]) => PendingPlotDraft[]) => {
    setState((prev) => ({ ...prev, pendingPlots: updater(prev.pendingPlots) }));
  }, []);

  const select = useCallback((id: string | null) => {
    setState((prev) => (prev.selection === id ? prev : { ...prev, selection: id }));
  }, []);

  const markSaved = useCallback((baseline: SavedBaseline) => {
    setState((prev) => ({ ...prev, baseline }));
  }, []);

  const clearError = useCallback(() => {
    setState((prev) => (prev.lastError ? { ...prev, lastError: null } : prev));
  }, []);

  const dirty = useMemo(() => computeDirty(state), [state]);

  return {
    state, dirty,
    dispatch, replaceDocument, undo, redo, setDraft, clearDraft, setPendingPlots, select, markSaved, clearError,
    canUndo: state.history.past.length > 0,
    canRedo: state.history.future.length > 0,
  };
}

export type WorkbenchBundle = ReturnType<typeof useWorkbenchState>;

/**
 * A never-saved document with no content at all: nothing to lose when
 * switching canvases (ADR-0023) — mode switches skip the save prompt.
 * File intents (new/leave/…) keep the stricter D7 dirty check.
 */
export function isPristineState(state: WorkbenchState): boolean {
  if (state.baseline) return false;
  const document = state.document;
  return document.name.trim().length === 0
    && document.plots2d.length === 0
    && document.plots3d.length === 0
    && document.objects2d.length === 0
    && document.annotations.length === 0
    && document.parameters.length === 0
    && document.functions.length === 0
    && Object.keys(state.drafts).length === 0
    && state.pendingPlots.every((row) => Object.values(row.expressions).every((text) => text.trim().length === 0));
}

/**
 * One independent bundle per drawing mode (ADR-0022): modes never share a
 * canvas, a document or an undo stack. The hook list is a fixed tuple.
 */
export function useModeWorkbenchStates(): Record<DrawingMode, WorkbenchBundle> {
  const functions2d = useWorkbenchState(freshWorkbenchState("", "functions2d"));
  const geometry2d = useWorkbenchState(freshWorkbenchState("", "geometry2d"));
  const functions3d = useWorkbenchState(freshWorkbenchState("", "functions3d"));
  return { functions2d, geometry2d, functions3d };
}

export type { DrawingMode, MathWorkbenchDocument };
