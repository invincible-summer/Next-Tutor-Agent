"use client";

/** Saved-drawing library, rename / save-as / delete-confirm modals. */

import { useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import type { DrawingMode } from "./workbench-types.ts";
import type { DrawingStore } from "./storage.ts";

const MODE_LABEL_KEYS: Record<DrawingMode, string> = {
  functions2d: "modeFunctions2d",
  geometry2d: "modeGeometry2d",
  functions3d: "modeFunctions3d",
};

export function FileDialogs({
  tr, store, currentId, activeMode,
  fileListOpen, onCloseFileList, onOpenDrawing, onDeleteDrawing,
  renameOpen, renameInitial, onCloseRename, onSubmitRename,
  saveAsOpen, onCloseSaveAs, onSubmitSaveAs,
  deleteTarget, onCloseDelete, onConfirmDelete,
  onImportFile, importFailed, onImportError,
}: {
  tr: (key: string) => string;
  store: DrawingStore;
  currentId: string;
  activeMode: DrawingMode;
  fileListOpen: boolean;
  onCloseFileList: () => void;
  onOpenDrawing: (id: string) => void;
  onDeleteDrawing: (id: string) => void;
  renameOpen: boolean;
  renameInitial: string;
  onCloseRename: () => void;
  onSubmitRename: (name: string) => void;
  saveAsOpen: boolean;
  onCloseSaveAs: () => void;
  onSubmitSaveAs: (name: string) => void;
  deleteTarget: string | null;
  onCloseDelete: () => void;
  onConfirmDelete: () => void;
  onImportFile: (file: File) => void;
  importFailed: string | null;
  onImportError: () => void;
}) {
  const [search, setSearch] = useState("");
  const [onlyCurrentMode, setOnlyCurrentMode] = useState(true);
  const [renameText, setRenameText] = useState(renameInitial);
  const [saveAsText, setSaveAsText] = useState("");
  const importRef = useRef<HTMLInputElement>(null);

  const sorted = useMemo(() => {
    const query = search.trim().toLowerCase();
    return [...store.items]
      .filter((item) => !onlyCurrentMode || item.document.activeMode === activeMode)
      .filter((item) => !query || item.name.toLowerCase().includes(query))
      .sort((a, b) => b.updatedAt - a.updatedAt);
  }, [store.items, search, onlyCurrentMode, activeMode]);

  return (
    <>
      <Modal open={fileListOpen} onClose={onCloseFileList} title={tr("fileOpen")} width={520}>
        <div className="file-dialog">
          <div className="file-dialog-toolbar">
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={tr("searchDrawings")}
              aria-label={tr("searchDrawings")}
            />
            <label className="file-mode-filter">
              <input type="checkbox" checked={onlyCurrentMode} onChange={(event) => setOnlyCurrentMode(event.target.checked)} />
              {tr("fileFilterMode")}
            </label>
            <Button variant="outline" size="sm" onClick={() => importRef.current?.click()}>{tr("importJson")}</Button>
            <input
              ref={importRef}
              type="file"
              accept="application/json,.json"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (file) { onCloseFileList(); onImportFile(file); }
              }}
            />
          </div>
          {importFailed && <p className="text-danger" role="alert">{tr("importFailed")}</p>}
          <div className="file-list" role="list">
            {sorted.length === 0 && <p className="panel-hint">{tr("noDrawings")}</p>}
            {sorted.map((item) => (
              <div key={item.id} className={`file-item ${item.id === currentId ? "is-current" : ""}`} role="listitem">
                <div className="file-item-main">
                  <span className="file-item-name">{item.name}</span>
                  <span className="file-item-meta">
                    <span className="file-mode-badge">{tr(MODE_LABEL_KEYS[item.document.activeMode])}</span>
                    {new Date(item.updatedAt).toLocaleString()} · {item.document.plots2d.length + item.document.plots3d.length} f · {item.document.objects2d.length} obj
                  </span>
                </div>
                <Button variant="outline" size="sm" onClick={() => onOpenDrawing(item.id)}>{tr("openDrawingAction")}</Button>
                <Button variant="ghost" size="sm" onClick={() => onDeleteDrawing(item.id)} aria-label={tr("deleteDrawing")}>×</Button>
              </div>
            ))}
          </div>
          <p className="file-dialog-note">{tr("savedLocalOnly")}</p>
          {void onImportError}
        </div>
      </Modal>

      <Modal
        open={renameOpen}
        onClose={() => { setRenameText(""); onCloseRename(); }}
        title={tr("rename")}
        width={380}
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => { setRenameText(""); onCloseRename(); }}>{tr("cancel")}</Button>
            <Button variant="primary" size="sm" disabled={renameText.trim().length === 0} onClick={() => { const name = renameText; setRenameText(""); onSubmitRename(name); }}>OK</Button>
          </>
        }
      >
        <label className="block text-xs font-medium text-fg-secondary">{tr("saveNamePrompt")}</label>
        <Input value={renameText} placeholder={tr("saveNamePlaceholder")} onChange={(event) => setRenameText(event.target.value)} autoFocus />
      </Modal>

      <Modal
        open={saveAsOpen}
        onClose={() => { setSaveAsText(""); onCloseSaveAs(); }}
        title={tr("saveAs")}
        width={380}
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => { setSaveAsText(""); onCloseSaveAs(); }}>{tr("cancel")}</Button>
            <Button variant="primary" size="sm" disabled={saveAsText.trim().length === 0} onClick={() => { const name = saveAsText; setSaveAsText(""); onSubmitSaveAs(name); }}>OK</Button>
          </>
        }
      >
        <label className="block text-xs font-medium text-fg-secondary">{tr("saveNamePrompt")}</label>
        <Input value={saveAsText} placeholder={tr("saveNamePlaceholder")} onChange={(event) => setSaveAsText(event.target.value)} autoFocus />
      </Modal>

      <Modal
        open={deleteTarget !== null}
        onClose={onCloseDelete}
        title={tr("confirmDeleteTitle")}
        width={380}
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={onCloseDelete}>{tr("cancel")}</Button>
            <Button variant="danger" size="sm" onClick={onConfirmDelete}>{tr("deleteDrawing")}</Button>
          </>
        }
      >
        <p>{tr("confirmDeleteBody")}</p>
      </Modal>
    </>
  );
}
