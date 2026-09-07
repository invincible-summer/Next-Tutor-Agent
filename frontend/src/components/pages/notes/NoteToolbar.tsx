"use client";
// 笔记工具栏（单行）：标题 InlineEdit、保存状态、视图切换、图谱/专注入口、
// 两侧栏折叠、齿轮设置菜单（文件夹/标签/温故/历史/导出/删除）。
// 409 冲突弹窗与删除确认也在这里承接。
import { useState } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Check, CloudOff, Columns2, Download, Eye, FolderOpen, History, Loader2, Maximize2,
  Network, PencilLine, RefreshCw, Settings2, Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/Button";
import { ConfirmModal } from "@/components/ui/Modal";
import { InlineEdit } from "@/components/sidebar/InlineEdit";
import { Modal } from "@/components/ui/Modal";
import { useClickOutside } from "@/components/sidebar/Dropdown";
import { cn } from "@/lib/cn";
import type { NoteDetail, NotesFolder } from "@/lib/types-notes";
import type { SaveState } from "@/lib/store-notes";
import { PanelToggleButton } from "./PanelToggleButton";

export type ViewMode = "edit" | "preview" | "split";

const VIEW_MODES = [
  ["edit", PencilLine, "ed.edit"],
  ["split", Columns2, "ed.split"],
  ["preview", Eye, "ed.preview"],
] as const;

export function ViewModeSwitch({
  mode,
  onChange,
  tr,
}: {
  mode: ViewMode;
  onChange: (m: ViewMode) => void;
  tr: (k: string, fallback?: string) => string;
}) {
  return (
    <div className="flex shrink-0 items-center gap-0.5 rounded-full bg-surface-sunken p-0.5">
      {VIEW_MODES.map(([m, Icon, key]) => (
        <button
          key={m}
          onClick={() => onChange(m)}
          title={tr(key)}
          aria-label={tr(key)}
          className={cn(
            "cursor-pointer rounded-full p-1.5 transition-colors",
            mode === m
              ? "bg-surface text-accent shadow-sm"
              : "text-muted hover:text-fg",
          )}
        >
          <Icon size={13} />
        </button>
      ))}
    </div>
  );
}

export function SaveBadge({ saveState, tr }: {
  saveState: SaveState;
  tr: (k: string, fallback?: string) => string;
}) {
  const badge: Record<SaveState, { icon: LucideIcon; text: string; cls: string }> = {
    saved: { icon: Check, text: tr("tb.saved"), cls: "text-success" },
    saving: { icon: Loader2, text: tr("tb.saving"), cls: "text-muted animate-spin" },
    dirty: { icon: CloudOff, text: tr("tb.dirty"), cls: "text-warning" },
    error: { icon: CloudOff, text: tr("tb.error"), cls: "text-danger" },
    conflict: { icon: RefreshCw, text: tr("tb.conflict"), cls: "text-accent2" },
  };
  const b = badge[saveState];
  const Icon = b.icon;
  return (
    <span
      title={saveState === "saved" ? `${b.text} · ${tr("tb.autosaveHint")}` : b.text}
      className={cn("flex shrink-0 items-center gap-1 text-[11px]", b.cls)}
    >
      <Icon size={13} />
    </span>
  );
}

export function NoteToolbar({
  detail,
  folders,
  saveState,
  conflictOpen,
  onCloseConflict,
  onLoadLatest,
  onOverwrite,
  onRename,
  onMove,
  onTags,
  onHistory,
  onExport,
  onDelete,
  onOpenCenter,
  rightOpen,
  onToggleRight,
  viewMode,
  onViewMode,
  graphOn,
  onToggleGraph,
  onFocus,
  tr,
}: {
  detail: NoteDetail;
  folders: NotesFolder[];
  saveState: SaveState;
  conflictOpen: boolean;
  onCloseConflict: () => void;
  onLoadLatest: () => void;
  onOverwrite: () => void;
  onRename: (title: string) => void;
  onMove: (folderId: string) => void;
  onTags: (tags: string[]) => void;
  onHistory: () => void;
  onExport: () => void;
  onDelete: () => void;
  onOpenCenter: () => void;
  rightOpen: boolean;
  onToggleRight: () => void;
  viewMode: ViewMode;
  onViewMode: (m: ViewMode) => void;
  graphOn: boolean;
  onToggleGraph: () => void;
  onFocus: () => void;
  tr: (k: string, fallback?: string) => string;
}) {
  const note = detail.note;
  const [tagInput, setTagInput] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useClickOutside<HTMLDivElement>(() => setMenuOpen(false), menuOpen);

  const addTag = () => {
    const name = tagInput.trim().replace(/^#/, "");
    if (!name || note.tags.includes(name)) {
      setTagInput("");
      return;
    }
    onTags([...note.tags, name].slice(0, 12));
    setTagInput("");
  };

  const menuAction = (Icon: LucideIcon, label: string, run: () => void, danger = false) => (
    <button
      onClick={() => { setMenuOpen(false); run(); }}
      className={cn(
        "flex w-full cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-xs transition-colors",
        danger ? "text-danger hover:bg-danger/10" : "text-fg-secondary hover:bg-surface-hover",
      )}
    >
      <Icon size={13} /> {label}
    </button>
  );

  return (
    <div className="border-b border-border bg-surface">
      <div className="flex items-center gap-1.5 px-2 py-2">
        <button
          onClick={onOpenCenter}
          title={tr("tb.center")}
          aria-label={tr("tb.center")}
          className="shrink-0 cursor-pointer rounded-md p-1.5 text-muted transition-colors hover:bg-surface-hover hover:text-accent"
        >
          <FolderOpen size={15} />
        </button>
        <div className="min-w-0 flex-1">
          <InlineEdit
            initialValue={note.title}
            placeholder={tr("tb.untitled")}
            onCommit={onRename}
            onCancel={() => {}}
          />
        </div>
        {note.status === "draft" && (
          <span className="shrink-0 rounded-md bg-warning/10 px-1.5 py-0.5 text-[11px] text-warning">
            {tr("tb.draft")}
          </span>
        )}
        <SaveBadge saveState={saveState} tr={tr} />
        <span className="mx-1 h-4 w-px shrink-0 bg-border" />
        <ViewModeSwitch mode={viewMode} onChange={onViewMode} tr={tr} />
        <span className="mx-1 h-4 w-px shrink-0 bg-border" />
        <button
          onClick={onToggleGraph}
          title={tr("graph.title")}
          aria-label={tr("graph.title")}
          className={cn(
            "shrink-0 cursor-pointer rounded-md p-1.5 transition-colors",
            graphOn
              ? "bg-accent-soft text-accent-strong"
              : "text-muted hover:bg-surface-hover hover:text-accent",
          )}
        >
          <Network size={15} />
        </button>
        <button
          onClick={onFocus}
          title={tr("tb.focus")}
          aria-label={tr("tb.focus")}
          className="shrink-0 cursor-pointer rounded-md p-1.5 text-muted transition-colors hover:bg-surface-hover hover:text-accent"
        >
          <Maximize2 size={15} />
        </button>
        <span className="mx-1 h-4 w-px shrink-0 bg-border" />
        <div className="relative shrink-0" ref={menuRef}>
          <button
            onClick={() => setMenuOpen((v) => !v)}
            title={tr("tb.settings")}
            aria-label={tr("tb.settings")}
            className={cn(
              "cursor-pointer rounded-md p-1.5 transition-colors",
              menuOpen
                ? "bg-surface-hover text-accent"
                : "text-muted hover:bg-surface-hover hover:text-accent",
            )}
          >
            <Settings2 size={15} />
          </button>
          {menuOpen && (
            <div className="motion-pop absolute right-0 top-9 z-40 w-72 space-y-2.5 rounded-[10px] border border-border bg-surface p-3 shadow-lg">
              {/* 文件夹 */}
              <div className="space-y-1">
                <div className="text-[10px] uppercase tracking-wide text-muted">{tr("tb.folder")}</div>
                <select
                  value={note.folder_id}
                  onChange={(e) => onMove(e.target.value)}
                  className="h-7 w-full cursor-pointer rounded-md border border-border bg-surface px-1.5 text-xs text-fg-secondary outline-none hover:border-accent"
                >
                  <option value="">{tr("notes.unfiled")}</option>
                  {folders.map((f) => (
                    <option key={f.id} value={f.id}>{f.name}</option>
                  ))}
                </select>
              </div>
              {/* 标签 */}
              <div className="space-y-1">
                <div className="text-[10px] uppercase tracking-wide text-muted">{tr("tb.tags")}</div>
                <div className="flex flex-wrap items-center gap-1">
                  {note.tags.map((t) => (
                    <span key={t} className="group flex items-center gap-0.5 rounded-md bg-accent-soft px-1.5 py-0.5 text-[11px] text-accent-strong">
                      #{t}
                      <button
                        onClick={() => onTags(note.tags.filter((x) => x !== t))}
                        className="cursor-pointer opacity-0 transition-opacity group-hover:opacity-100"
                        aria-label={`remove ${t}`}
                      >
                        ×
                      </button>
                    </span>
                  ))}
                  <input
                    value={tagInput}
                    onChange={(e) => setTagInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") { e.preventDefault(); addTag(); }
                    }}
                    onBlur={addTag}
                    placeholder={tr("tb.tagsPlaceholder")}
                    className="w-24 rounded-md border border-dashed border-border bg-transparent px-1.5 py-0.5 text-[11px] text-fg outline-none focus:border-accent"
                  />
                </div>
              </div>
              <div className="border-t border-border pt-1.5">
                {menuAction(History, tr("tb.history"), onHistory)}
                {menuAction(Download, tr("tb.export"), onExport)}
                {menuAction(Maximize2, tr("tb.focus"), onFocus)}
                {menuAction(Trash2, tr("tb.delete"), () => setDeleteOpen(true), true)}
              </div>
              <div className="border-t border-border pt-1.5 text-[10px] text-muted tnum">
                {tr("tb.words", "{n} 字").replace("{n}", String(note.word_count))} · v{note.revision}
              </div>
            </div>
          )}
        </div>
        <PanelToggleButton
          side="right" open={rightOpen} onToggle={onToggleRight}
          label={tr("tb.toggleAi")}
        />
      </div>

      {/* 409 冲突弹窗 */}
      <Modal
        open={conflictOpen}
        onClose={onCloseConflict}
        title={tr("tb.conflict")}
        footer={
          <>
            <Button variant="outline" size="sm" onClick={onLoadLatest}>
              {tr("tb.conflict.load")}
            </Button>
            <Button variant="primary" size="sm" onClick={onOverwrite}>
              {tr("tb.conflict.overwrite")}
            </Button>
          </>
        }
      >
        {tr("tb.conflict.desc")}
      </Modal>

      <ConfirmModal
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        onConfirm={() => {
          setDeleteOpen(false);
          onDelete();
        }}
        title={tr("tb.delete")}
        desc={tr("notes.confirmDelete")}
        confirmText={tr("tb.delete")}
        cancelText={tr("notes.cancel", "取消")}
      />
    </div>
  );
}
