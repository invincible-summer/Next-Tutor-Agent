"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { guardDemoAction } from "@/lib/demo";
import type { MathWorkbenchDocument } from "./workbench-types.ts";
import type { DirtyState } from "./document-state.ts";
import type { SaveResult } from "./storage.ts";

export function WorkbenchHeader({
  tr, document, dirty, canUndo, canRedo, owner,
  onBack, onUndo, onRedo, onNew, onOpen, onSave, onRename, onSaveAs, onImport, onExportJson, onExportCanvasRequest, onDeleteCurrent,
}: {
  tr: (key: string) => string;
  document: MathWorkbenchDocument;
  dirty: DirtyState;
  canUndo: boolean;
  canRedo: boolean;
  owner: string;
  onBack: () => void;
  onUndo: () => void;
  onRedo: () => void;
  onNew: () => void;
  onOpen: () => void;
  onSave: () => SaveResult;
  onRename: () => void;
  onSaveAs: () => void;
  onImport: (file: File) => void;
  onExportJson: () => void;
  onExportCanvasRequest: () => void;
  onDeleteCurrent: (() => void) | undefined;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const importRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) setMenuOpen(false);
    };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setMenuOpen(false); };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", escape);
    return () => { window.removeEventListener("mousedown", close); window.removeEventListener("keydown", escape); };
  }, [menuOpen]);

  const saveLabel = dirty.dirty ? tr("fileSave") : tr("fileSave");
  void saveLabel;
  const statusText = dirty.dirty ? tr("unsaved") : tr("savedInBrowser");

  return (
    <header className="workbench-header" data-owner={owner}>
      <div className="flex min-w-0 items-center gap-2">
        <Button variant="ghost" size="sm" onClick={onBack} aria-label={tr("backToTools")} title={tr("backToTools")}>←</Button>
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-fg">{document.name.trim() || tr("untitled")}</div>
        </div>
        <span
          className={`unsaved-indicator ${dirty.dirty ? "is-dirty" : "is-saved"}`}
          data-testid="geometry-unsaved-indicator"
          title={statusText}
        >{statusText}</span>
      </div>
      <div className="flex items-center gap-1.5">
        <Button variant="ghost" size="sm" onClick={onUndo} disabled={!canUndo} aria-label={tr("undo")} title={tr("undo")}>↺</Button>
        <Button variant="ghost" size="sm" onClick={onRedo} disabled={!canRedo} aria-label={tr("redo")} title={tr("redo")}>↻</Button>
        <div className="header-divider" aria-hidden />
        <Button variant="outline" size="sm" onClick={guardDemoAction(() => onNew())} data-testid="geometry-file-new" className="hidden sm:inline-flex">{tr("fileNew")}</Button>
        <Button variant="outline" size="sm" onClick={guardDemoAction(() => onOpen())} data-testid="geometry-file-open">{tr("fileOpen")}</Button>
        <Button
          variant="primary" size="sm" onClick={guardDemoAction(() => { onSave(); })}
          disabled={!dirty.dirty}
          data-testid="geometry-file-save"
          title="Ctrl/Cmd+S"
        >{tr("fileSave")}</Button>
        <div className="relative" ref={menuRef}>
          <Button variant="ghost" size="sm" onClick={() => setMenuOpen((v) => !v)} data-testid="geometry-file-menu" aria-haspopup="menu" aria-expanded={menuOpen}>⋯</Button>
          {menuOpen && (
            <div role="menu" className="file-menu">
              <MenuItem tr={tr} label={tr("rename")} onSelect={() => { setMenuOpen(false); onRename(); }} demo />
              <MenuItem tr={tr} label={tr("saveAs")} onSelect={() => { setMenuOpen(false); onSaveAs(); }} demo />
              <MenuItem tr={tr} label={tr("importJson")} onSelect={() => { setMenuOpen(false); importRef.current?.click(); }} demo />
              <MenuItem tr={tr} label={tr("exportJson")} onSelect={() => { setMenuOpen(false); onExportJson(); }} />
              <MenuItem tr={tr} label={tr("exportCanvas")} onSelect={() => { setMenuOpen(false); onExportCanvasRequest(); }} />
              {onDeleteCurrent && <MenuItem tr={tr} label={tr("deleteDrawing")} danger onSelect={() => { setMenuOpen(false); onDeleteCurrent(); }} demo />}
            </div>
          )}
        </div>
        <input
          ref={importRef}
          type="file"
          accept="application/json,.json"
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (file) onImport(file);
          }}
        />
      </div>
    </header>
  );
}

function MenuItem({ tr, label, onSelect, danger, demo }: { tr: (k: string) => string; label: string; onSelect: () => void; danger?: boolean; demo?: boolean }) {
  void tr;
  return (
    <button
      role="menuitem"
      className={`file-menu-item ${danger ? "text-danger" : ""}`}
      onClick={guardDemoAction(onSelect, demo)}
    >{label}</button>
  );
}
