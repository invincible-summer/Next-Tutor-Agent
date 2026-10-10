"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/lib/auth-store";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { useUnsavedChanges } from "@/lib/use-unsaved-changes";
import { DEMO_MODE } from "@/lib/demo";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { STRINGS } from "@/app/(workspace)/tools/geometry/strings";
import { WorkbenchHeader } from "./WorkbenchHeader.tsx";
import { ModeRail } from "./ModeRail.tsx";
import { WorkbenchPanels } from "./WorkbenchPanels.tsx";
import { Stage2D } from "./Stage2D.tsx";
import { UnsavedChangesDialog } from "./UnsavedChangesDialog.tsx";

// Client-boundary dynamic import keeps the Three chunk out of every non-3D
// route bundle; it loads only when a 3D mode actually mounts (H6).
import { FileDialogs } from "./FileDialogs.tsx";
import { freshWorkbenchState, isPristineState, useModeWorkbenchStates, type RightDockTab } from "./document-state.ts";
import type { FileIntent } from "./navigation-intents.ts";
import {
  clearRecoveryDraft, deleteDrawing, exportDrawingJson, importDrawingFile, loadDrawingStore, readRecoveryDraft,
  renameDrawing, saveAsDrawing, saveDrawing, setActiveDrawing, writeRecoveryDraft,
  type DrawingStore, type SaveResult,
} from "./storage.ts";
import { presetDocument, semanticFingerprint, type DrawingMode, type MathWorkbenchDocument } from "./workbench-types.ts";
import "./math-workbench.css";

const Stage3D = dynamic(() => import("./Stage3D.tsx").then((m) => ({ default: m.Stage3D })), {
  ssr: false,
  loading: () => <div className="stage3d-loading" aria-busy="true" />,
});

const MODES: readonly DrawingMode[] = ["functions2d", "geometry2d", "functions3d"];

function lastModeKey(owner: string): string {
  return `next-tutor.math-workbench.last-mode:${encodeURIComponent(owner || "guest")}`;
}

export function MathWorkbench() {
  const lang = useUIStore((s) => s.lang);
  const owner = useAuthStore((s) => s.user?.id ?? "guest");
  const router = useRouter();
  const tr = useMemo(() => makePageT(lang, STRINGS), [lang]);
  // One independent bundle per mode (ADR-0022): document, history, drafts,
  // dirty state and baseline never mix across modes.
  const bundles = useModeWorkbenchStates();
  const [activeMode, setActiveMode] = useState<DrawingMode>("functions2d");
  const wb = bundles[activeMode];
  const { state, dirty } = wb;

  const [store, setStore] = useState<DrawingStore>({ version: 2, activeIds: { functions2d: "", geometry2d: "", functions3d: "" }, items: [] });
  const [hydrated, setHydrated] = useState(false);
  const [modePanelOpen, setModePanelOpen] = useState(false);
  // Narrow screens start with the dock closed so the canvas owns the viewport;
  // the collapsed pill at the bottom-right reopens it.
  const [rightOpen, setRightOpen] = useState(() =>
    typeof window === "undefined" || window.matchMedia("(min-width: 600px)").matches);
  const [rightTab, setRightTab] = useState<RightDockTab>("functions");
  const [activeTool, setActiveTool] = useState("select");
  const [pendingIntent, setPendingIntent] = useState<FileIntent | null>(null);
  const [intentError, setIntentError] = useState<string | null>(null);
  const [fileListOpen, setFileListOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [saveAsOpen, setSaveAsOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  const [recoveryDoc, setRecoveryDoc] = useState<MathWorkbenchDocument | null>(null);
  const ownerRef = useRef(owner);
  useEffect(() => { ownerRef.current = owner; }, [owner]);

  /* -------------------------- archive load per owner -------------------------- */
  useEffect(() => {
    // Deferred past hydration (same pattern as the electrical lab workspace).
    const timer = window.setTimeout(() => {
      const loaded = loadDrawingStore(owner);
      setStore(loaded);
      setHydrated(true);
      // Reopen the mode the user left from (first visit = 2D functions).
      let mode: DrawingMode = "functions2d";
      try {
        const saved = window.localStorage.getItem(lastModeKey(owner)) as DrawingMode | null;
        if (saved && MODES.includes(saved)) mode = saved;
      } catch { /* ignore */ }
      setActiveMode(mode);
      setRightTab(mode === "geometry2d" ? "objects" : "functions");
      for (const m of MODES) {
        const id = loaded.activeIds[m];
        const item = id ? loaded.items.find((i) => i.id === id) : undefined;
        if (item) {
          bundles[m].replaceDocument(item.document, { documentId: item.id, fingerprint: semanticFingerprint(item.document), savedAt: item.updatedAt });
        } else {
          bundles[m].replaceDocument(freshWorkbenchState("", m).document, null);
        }
      }
    }, 0);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [owner]);

  /* ------------------------------ recovery drafts ------------------------------ */
  useEffect(() => {
    if (!hydrated || state.baseline) return;
    const timer = window.setTimeout(() => {
      const recovered = readRecoveryDraft(owner, state.document.id);
      if (recovered && semanticFingerprint(recovered) !== semanticFingerprint(state.document)) setRecoveryDoc(recovered);
      else if (recovered) clearRecoveryDraft(owner, state.document.id);
    }, 0);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hydrated, state.baseline, state.document.id]);

  // Debounced crash-recovery writes: only while dirty, never treated as saves.
  useEffect(() => {
    if (!hydrated || !dirty.dirty) return;
    const id = state.document.id;
    const timer = window.setTimeout(() => writeRecoveryDraft(owner, id, state.document), 1500);
    return () => window.clearTimeout(timer);
  }, [hydrated, dirty.dirty, owner, state.document]);

  useUnsavedChanges(dirty.dirty, tr("unsavedBody"));

  // Command failures surface honestly instead of vanishing (D5).
  useEffect(() => {
    if (!state.lastError) return;
    const error = state.lastError;
    const timer = window.setTimeout(() => {
      setToast(`${tr("invalidExpression")} · ${error.code}`);
      wb.clearError();
    }, 0);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.lastError]);

  /* --------------------------------- saving --------------------------------- */
  const saveCurrent = useCallback((): SaveResult => {
    const { result, store: next } = saveDrawing(ownerRef.current, store, state.document);
    if (result.ok) {
      setStore(next);
      wb.markSaved({ documentId: result.documentId, fingerprint: semanticFingerprint(state.document), savedAt: result.savedAt });
      clearRecoveryDraft(ownerRef.current, state.document.id);
    }
    return result;
    // store intentionally read fresh from state via closure; saveDrawing is pure
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [owner, store, state.document]);

  /* ------------------------------- mode & tools ------------------------------- */
  const selectMode = useCallback((mode: DrawingMode) => {
    // Modes are independent canvases (ADR-0022): switching swaps the whole
    // bundle — no undo entry, nothing destroyed (the dirty check happens in
    // requestDestructiveTransition, ADR-0023).
    setActiveMode(mode);
    try { window.localStorage.setItem(lastModeKey(owner), mode); } catch { /* ignore */ }
    setRightTab(mode === "geometry2d" ? "objects" : "functions");
    setActiveTool("select");
    setModePanelOpen(false);
  }, [owner]);

  /* ------------------------------- file intents ------------------------------- */
  const executeIntent = useCallback(async (intent: FileIntent) => {
    setIntentError(null);
    switch (intent.kind) {
      case "leave":
        router.push(intent.href);
        return;
      case "new": {
        wb.replaceDocument(freshWorkbenchState("", activeMode).document, null);
        setRightTab(activeMode === "geometry2d" ? "objects" : "functions");
        return;
      }
      case "open": {
        const item = store.items.find((i) => i.id === intent.documentId);
        if (!item) { setIntentError(tr("importFailed")); return; }
        const mode = item.document.activeMode;
        const nextStore = { ...store, activeIds: { ...store.activeIds, [mode]: item.id } };
        setStore(nextStore);
        setActiveDrawing(owner, nextStore, mode, item.id);
        bundles[mode].replaceDocument(item.document, { documentId: item.id, fingerprint: semanticFingerprint(item.document), savedAt: item.updatedAt });
        if (mode !== activeMode) {
          setActiveMode(mode);
          setRightTab(mode === "geometry2d" ? "objects" : "functions");
          setActiveTool("select");
        }
        return;
      }
      case "replace-import": {
        try {
          const imported = await importDrawingFile(intent.file);
          const mode = imported.activeMode;
          bundles[mode].replaceDocument(imported, null);
          if (mode !== activeMode) {
            setActiveMode(mode);
            setActiveTool("select");
          }
          setRightTab(mode === "geometry2d" ? "objects" : "functions");
          setToast(tr("importSuccess"));
        } catch {
          setIntentError(tr("importFailed"));
        }
        return;
      }
      case "replace-preset": {
        const doc = presetDocument(intent.presetId);
        if (!doc) { setIntentError(tr("importFailed")); return; }
        bundles[doc.activeMode].replaceDocument(doc, null);
        if (doc.activeMode !== activeMode) {
          setActiveMode(doc.activeMode);
          setActiveTool("select");
        }
        setRightTab("functions");
        return;
      }
      case "switch-mode": {
        // Same semantics as selectMode, reached through the dirty check
        // (ADR-0023): nothing is destroyed, the other bundle keeps waiting.
        selectMode(intent.mode);
        return;
      }
    }
  }, [owner, router, store, tr, wb, activeMode, bundles, selectMode]);

  const requestDestructiveTransition = useCallback((intent: FileIntent) => {
    if (DEMO_MODE) {
      // Read-only demo: non-destructive viewing actions still work.
      if (intent.kind === "replace-preset" || intent.kind === "switch-mode") { void executeIntent(intent); return; }
      if (intent.kind === "leave") { router.push(intent.href); return; }
      window.dispatchEvent(new Event("edu-demo-readonly"));
      return;
    }
    // Mode switches on a pristine canvas are zero-friction (ADR-0023):
    // there is nothing to save, so no prompt.
    if (!dirty.dirty || (intent.kind === "switch-mode" && isPristineState(state))) { void executeIntent(intent); return; }
    setIntentError(null);
    setPendingIntent(intent);
  }, [dirty.dirty, executeIntent, router, state]);

  const cancelPendingTransition = useCallback(() => {
    setPendingIntent(null);
    setIntentError(null);
  }, []);

  const confirmSaveThenContinue = useCallback(async () => {
    if (!pendingIntent) return;
    if (!state.document.name.trim() && !state.baseline) {
      // Unnamed first save needs a name before continuing (D7).
      setRenameOpen(true);
      return;
    }
    const result = saveCurrent();
    if (!result.ok) {
      setIntentError(result.code === "quota_exceeded" || result.code === "storage_unavailable" ? tr("saveFailed") : tr("invalidExpression"));
      return; // dialog stays open; no navigation happened
    }
    const intent = pendingIntent;
    setPendingIntent(null);
    await executeIntent(intent);
  }, [pendingIntent, state.document.name, state.baseline, saveCurrent, tr, executeIntent]);

  const confirmDiscardThenContinue = useCallback(() => {
    if (!pendingIntent) return;
    // switch-mode keeps the bundle in memory; its crash-recovery draft must
    // survive too (ADR-0023), so only truly destructive intents clear it.
    if (pendingIntent.kind !== "switch-mode") clearRecoveryDraft(owner, state.document.id);
    const intent = pendingIntent;
    setPendingIntent(null);
    void executeIntent(intent);
  }, [pendingIntent, owner, state.document.id, executeIntent]);

  /* ------------------------------ view controls ------------------------------ */
  const viewControlsRef = useRef<{ zoomIn?: () => void; zoomOut?: () => void; resetView?: () => void; fitView?: () => void } | null>(null);
  const registerViewControls = useCallback((controls: { zoomIn?: () => void; zoomOut?: () => void; resetView?: () => void; fitView?: () => void } | null) => {
    viewControlsRef.current = controls;
  }, []);

  /* -------------------------------- shortcuts -------------------------------- */
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.isComposing || event.defaultPrevented) return;
      const target = event.target as HTMLElement | null;
      const inField = !!target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable);
      const meta = event.ctrlKey || event.metaKey;
      if (event.altKey && !meta) {
        // Alt+1/2/3 jumps between the three canvases through the same
        // dirty check as the mode panel (ADR-0023).
        const index = ["1", "2", "3"].indexOf(event.key);
        if (index >= 0 && MODES[index] && !inField) {
          event.preventDefault();
          requestDestructiveTransition({ kind: "switch-mode", mode: MODES[index] as DrawingMode });
        }
        return;
      }
      if (!meta || inField) return;
      const key = event.key.toLowerCase();
      if (key === "s") { event.preventDefault(); if (!DEMO_MODE && dirty.dirty) saveCurrent(); }
      else if (key === "o") { event.preventDefault(); if (!DEMO_MODE) setFileListOpen(true); }
      else if (key === "n") { event.preventDefault(); requestDestructiveTransition({ kind: "new" }); }
      else if (key === "z" && !event.shiftKey) { event.preventDefault(); wb.undo(); }
      else if ((key === "z" && event.shiftKey) || key === "y") { event.preventDefault(); wb.redo(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dirty.dirty, saveCurrent, requestDestructiveTransition, wb]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 3200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const is3D = activeMode === "functions3d";

  return (
    <div className="math-workbench flex h-full min-h-0 flex-col bg-bg text-fg" data-testid="geometry-workbench">
      <WorkbenchHeader
        tr={tr}
        document={state.document}
        dirty={dirty}
        canUndo={wb.canUndo}
        canRedo={wb.canRedo}
        owner={owner}
        onUndo={wb.undo}
        onRedo={wb.redo}
        onBack={() => requestDestructiveTransition({ kind: "leave", href: "/tools" })}
        onNew={() => requestDestructiveTransition({ kind: "new" })}
        onOpen={() => setFileListOpen(true)}
        onSave={saveCurrent}
        onRename={() => setRenameOpen(true)}
        onSaveAs={() => setSaveAsOpen(true)}
        onImport={(file) => requestDestructiveTransition({ kind: "replace-import", file })}
        onExportJson={() => downloadBlob(exportDrawingJson(state.document), drawingFileName(state.document.name, "json"))}
        onExportCanvasRequest={() => window.dispatchEvent(new CustomEvent("math-workbench:export-canvas"))}
        onDeleteCurrent={state.baseline ? () => setDeleteTarget(state.baseline?.documentId ?? null) : undefined}
      />
      <div className="relative flex min-h-0 flex-1">
        <ModeRail
          tr={tr}
          mode={activeMode}
          tool={activeTool}
          modePanelOpen={modePanelOpen}
          onToggleModePanel={() => setModePanelOpen((v) => !v)}
          onCloseModePanel={() => setModePanelOpen(false)}
          onSelectMode={(mode) => requestDestructiveTransition({ kind: "switch-mode", mode })}
          onSelectTool={setActiveTool}
          onHelp={() => setHelpOpen(true)}
        />
        <main className="relative flex min-w-0 flex-1 flex-col" data-testid="geometry-stage">
          {recoveryDoc && (
            <div className="recovery-banner" role="status">
              <span>{tr("recoveryBanner")}</span>
              <span className="recovery-actions">
                <button
                  onClick={() => {
                    bundles[recoveryDoc.activeMode].replaceDocument(recoveryDoc, null);
                    if (recoveryDoc.activeMode !== activeMode) {
                      setActiveMode(recoveryDoc.activeMode);
                      setRightTab(recoveryDoc.activeMode === "geometry2d" ? "objects" : "functions");
                      setActiveTool("select");
                    }
                    clearRecoveryDraft(owner, recoveryDoc.id);
                    setRecoveryDoc(null);
                  }}
                >{tr("recoveryRestore")}</button>
                <button
                  onClick={() => {
                    clearRecoveryDraft(owner, recoveryDoc.id);
                    setRecoveryDoc(null);
                  }}
                >{tr("recoveryDismiss")}</button>
              </span>
            </div>
          )}
          {DEMO_MODE && <div className="demo-banner">{tr("demoReadonlyHint")}</div>}
          <div className="stage-layer">
            {is3D ? (
              <Stage3D document={state.document} selection={state.selection} onSelect={wb.select} tr={tr} dispatch={wb.dispatch} onViewControls={registerViewControls} />
            ) : (
              <Stage2D
                document={state.document}
                tool={activeTool}
                selection={state.selection}
                onSelect={wb.select}
                dispatch={wb.dispatch}
                tr={tr}
                readOnly={DEMO_MODE}
                onViewControls={registerViewControls}
              />
            )}
            <div className="stage-view-controls" data-testid="geometry-view-controls" role="toolbar" aria-label={tr("zoom")}>
              {!is3D && (
                <>
                  <button type="button" title={tr("zoomIn")} aria-label={tr("zoomIn")} onClick={() => viewControlsRef.current?.zoomIn?.()}>
                    <svg viewBox="0 0 16 16" aria-hidden><circle cx="7" cy="7" r="5" fill="none" stroke="currentColor" strokeWidth="1.5" /><line x1="10.5" y1="10.5" x2="14" y2="14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /><line x1="7" y1="4.8" x2="7" y2="9.2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /><line x1="4.8" y1="7" x2="9.2" y2="7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
                  </button>
                  <button type="button" title={tr("zoomOut")} aria-label={tr("zoomOut")} onClick={() => viewControlsRef.current?.zoomOut?.()}>
                    <svg viewBox="0 0 16 16" aria-hidden><circle cx="7" cy="7" r="5" fill="none" stroke="currentColor" strokeWidth="1.5" /><line x1="10.5" y1="10.5" x2="14" y2="14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /><line x1="4.8" y1="7" x2="9.2" y2="7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
                  </button>
                </>
              )}
              <button type="button" title={tr(is3D ? "resetCamera" : "resetView")} aria-label={tr(is3D ? "resetCamera" : "resetView")} onClick={() => viewControlsRef.current?.resetView?.()}>
                <svg viewBox="0 0 16 16" aria-hidden><circle cx="8" cy="8" r="2" fill="none" stroke="currentColor" strokeWidth="1.5" /><path d="M8 1.5v3M8 11.5v3M1.5 8h3M11.5 8h3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
              </button>
              <button type="button" title={tr("fitView")} aria-label={tr("fitView")} onClick={() => viewControlsRef.current?.fitView?.()}>
                <svg viewBox="0 0 16 16" aria-hidden><path d="M2 5V3.5A1.5 1.5 0 0 1 3.5 2H5M11 2h1.5A1.5 1.5 0 0 1 14 3.5V5M14 11v1.5a1.5 1.5 0 0 1-1.5 1.5H11M5 14H3.5A1.5 1.5 0 0 1 2 12.5V11" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
              </button>
            </div>
          </div>
        </main>
        <WorkbenchPanels
          tr={tr}
          lang={lang}
          document={state.document}
          drafts={state.drafts}
          pendingPlots={state.pendingPlots}
          selection={state.selection}
          tab={rightTab}
          open={rightOpen}
          onToggle={() => setRightOpen((v) => !v)}
          onTab={setRightTab}
          onSelect={wb.select}
          dispatch={wb.dispatch}
          setDraft={wb.setDraft}
          clearDraft={wb.clearDraft}
          setPendingPlots={wb.setPendingPlots}
          readOnly={DEMO_MODE}
        />
      </div>
      <footer className="workbench-status" data-testid="geometry-status">
        <span>{tr("statusMode")}：{modeLabel(tr, activeMode)}</span>
        <span className="ml-auto text-muted">{state.baseline ? tr("savedLocalOnly") : tr("unsaved")}</span>
      </footer>

      <UnsavedChangesDialog
        open={pendingIntent !== null}
        tr={tr}
        error={intentError}
        variant={pendingIntent?.kind === "switch-mode" ? "switch-mode" : "default"}
        onCancel={cancelPendingTransition}
        onSave={confirmSaveThenContinue}
        onDiscard={confirmDiscardThenContinue}
      />
      <FileDialogs
        tr={tr}
        store={store}
        currentId={state.document.id}
        activeMode={activeMode}
        fileListOpen={fileListOpen}
        onCloseFileList={() => setFileListOpen(false)}
        onOpenDrawing={(id) => { setFileListOpen(false); requestDestructiveTransition({ kind: "open", documentId: id }); }}
        onDeleteDrawing={(id) => setDeleteTarget(id)}
        renameOpen={renameOpen}
        renameInitial={state.document.name}
        onCloseRename={() => setRenameOpen(false)}
        onSubmitRename={(name) => {
          if (name.trim().length === 0) return;
          setRenameOpen(false);
          if (state.baseline) {
            setStore((prev) => renameDrawing(owner, prev, state.baseline?.documentId ?? state.document.id, name.trim()));
            wb.dispatch({ kind: "renameDocument", name: name.trim() }, "rename");
          } else {
            // First save with a fresh name (came from save-and-continue flow).
            const { result, store: next } = saveDrawing(owner, store, state.document, name.trim());
            if (result.ok) {
              setStore(next);
              wb.markSaved({ documentId: result.documentId, fingerprint: semanticFingerprint({ ...state.document, name: name.trim() }), savedAt: result.savedAt });
              clearRecoveryDraft(owner, state.document.id);
              const intent = pendingIntent;
              setPendingIntent(null);
              if (intent) void executeIntent(intent);
            } else {
              setIntentError(tr("saveFailed"));
            }
          }
        }}
        saveAsOpen={saveAsOpen}
        onCloseSaveAs={() => setSaveAsOpen(false)}
        onSubmitSaveAs={(name) => {
          if (name.trim().length === 0) return;
          setSaveAsOpen(false);
          const { result, store: next, documentId } = saveAsDrawing(owner, store, state.document, name.trim());
          if (result.ok && documentId) {
            setStore(next);
            const savedDoc = next.items.find((i) => i.id === documentId)?.document ?? state.document;
            wb.replaceDocument(savedDoc, { documentId, fingerprint: semanticFingerprint(savedDoc), savedAt: result.savedAt });
          } else setToast(tr("saveFailed"));
        }}
        deleteTarget={deleteTarget}
        onCloseDelete={() => setDeleteTarget(null)}
        onConfirmDelete={() => {
          if (!deleteTarget) return;
          const wasCurrent = state.baseline?.documentId === deleteTarget || state.document.id === deleteTarget;
          setStore((prev) => deleteDrawing(owner, prev, deleteTarget));
          setDeleteTarget(null);
          if (wasCurrent) {
            wb.replaceDocument(freshWorkbenchState("", activeMode).document, null);
            setRightTab(activeMode === "geometry2d" ? "objects" : "functions");
          }
        }}
        onImportFile={(file) => { requestDestructiveTransition({ kind: "replace-import", file }); }}
        importFailed={intentError}
        onImportError={() => setToast(tr("importFailed"))}
      />
      <Modal open={helpOpen} onClose={() => setHelpOpen(false)} title={tr("helpTitle")} width={420}>
        <div className="help-shortcuts">
          <p>{tr("helpBody")}</p>
          <p>{tr("helpWheel")}</p>
        </div>
      </Modal>
      {toast && (
        <div className="workbench-toast" role="status">
          <span>{toast}</span>
          <Button variant="ghost" size="sm" aria-label={tr("cancel")} onClick={() => setToast(null)}>×</Button>
        </div>
      )}
    </div>
  );
}

function modeLabel(tr: (k: string) => string, mode: DrawingMode): string {
  switch (mode) {
    case "functions2d": return tr("modeFunctions2d");
    case "geometry2d": return tr("modeGeometry2d");
    case "functions3d": return tr("modeFunctions3d");
  }
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 4000);
}

export function drawingFileName(name: string, ext: string): string {
  const safe = (name.trim() || "drawing").replace(/[\\/:*?"<>|]/g, "_").slice(0, 60);
  return `${safe}.${ext}`;
}
