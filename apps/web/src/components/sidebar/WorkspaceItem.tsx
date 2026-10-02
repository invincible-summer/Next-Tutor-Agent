"use client";
import { useRef, useState } from "react";
import {
  ChevronDown, ChevronRight, FolderOpen, MoreHorizontal,
  Plus, Pencil, Trash2, Brain, Settings2,
} from "lucide-react";
import { useUIStore } from "@/lib/store";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/cn";
import { deleteWorkspaceFile } from "@/lib/api";
import { notifyWsChanged, useWsSettings } from "@/lib/ws-settings";
import { Dropdown, type DropdownItem } from "./Dropdown";
import { InlineEdit } from "./InlineEdit";
import { WorkspaceFiles } from "./WorkspaceFiles";
import { SessionRow } from "./SessionRow";
import type { WorkspaceItem as WorkspaceInfo, WorkspaceDetail, SessionItem, AttachmentMeta } from "@/lib/types";

export function WorkspaceItem({
  ws,
  detail,
  sessions,
  activeSessionId,
  isExpanded,
  isDragOver,
  onToggle,
  onDragOver,
  onDragLeave,
  onDrop,
  onNewChat,
  onRename,
  onDelete,
  onRefresh,
  onSelectSession,
  onRenameSession,
  onDeleteSession,
  onRemoveFromWs,
  onSessionDragStart,
  askConfirm,
  selectable,
  isSelected,
  onToggleSelect,
}: {
  ws: WorkspaceInfo;
  detail: WorkspaceDetail | undefined;
  sessions: SessionItem[];
  activeSessionId: string | null;
  isExpanded: boolean;
  isDragOver: boolean;
  onToggle: () => void;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: () => void;
  onDrop: (e: React.DragEvent) => void;
  onNewChat: () => void;
  onRename: (name: string) => void;
  onDelete: () => void;
  /** Reload workspace list + this workspace's detail (after upload/file delete). */
  onRefresh: () => void;
  onSelectSession: (id: string) => void;
  onRenameSession: (id: string, title: string) => void;
  onDeleteSession: (id: string) => void;
  onRemoveFromWs: (wsId: string, sessionId: string) => void;
  onSessionDragStart: (id: string) => void;
  askConfirm: (opts: { title: string; desc?: string; onConfirm: () => void }) => void;
  /** Batch-manage mode pass-through (see Sidebar). */
  selectable?: boolean;
  isSelected?: (id: string) => boolean;
  onToggleSelect?: (id: string) => void;
}) {
  const { lang } = useUIStore();
  const tr = (k: string, fb?: string) => t(lang, k, fb);
  const [menuOpen, setMenuOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [memOpen, setMemOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  const onDeleteFile = (file: AttachmentMeta) => {
    askConfirm({
      title: tr("ws.files.delete.title"),
      desc: `${file.filename} — ${tr("ws.files.delete.desc")}`,
      onConfirm: async () => {
        try {
          await deleteWorkspaceFile(ws.workspace_id, file.id);
        } catch { /* already gone — refresh either way */ }
        notifyWsChanged();
        onRefresh();
      },
    });
  };

  const menuItems: DropdownItem[] = [
    { icon: Plus, label: tr("ws.new.chat"), onClick: onNewChat },
    { icon: Settings2, label: tr("ws.settings"), onClick: () => useWsSettings.getState().open(ws.workspace_id) },
    { icon: Pencil, label: tr("ws.rename"), onClick: () => setEditing(true) },
    { icon: Trash2, label: tr("ws.delete"), danger: true, dividerBefore: true, onClick: onDelete },
  ];

  const hasMemory = !!detail?.public_memory.trim();

  return (
    <div className="mb-0.5">
      <div
        onClick={onToggle}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        className={cn(
          "group flex cursor-pointer items-center gap-1.5 rounded-[10px] border px-2 py-1.5 transition-colors",
          isDragOver
            ? "border-dashed border-accent bg-accent-soft/40"
            : "border-transparent hover:bg-surface-hover",
        )}
      >
        {isExpanded ? <ChevronDown size={14} className="flex-shrink-0 text-muted" /> : <ChevronRight size={14} className="flex-shrink-0 text-muted" />}
        <FolderOpen size={14} className="flex-shrink-0 text-accent" />
        {editing ? (
          <span className="flex-1" onClick={(e) => e.stopPropagation()}>
            <InlineEdit
              initialValue={ws.name}
              placeholder={tr("ws.untitled")}
              onCommit={(v) => { onRename(v); setEditing(false); }}
              onCancel={() => setEditing(false)}
            />
          </span>
        ) : (
          <span className="flex-1 truncate text-[0.8rem] font-medium text-fg-secondary group-hover:text-fg">{ws.name}</span>
        )}
        <span className="tnum text-[0.62rem] text-muted">{ws.session_count}</span>
        {/* Workspace menu */}
        <div className="relative" ref={menuRef}>
          <button
            onClick={(e) => { e.stopPropagation(); setMenuOpen((v) => !v); }}
            className="flex items-center rounded-[4px] p-0.5 text-muted opacity-0 transition-opacity hover:text-fg group-hover:opacity-100"
          >
            <MoreHorizontal size={14} />
          </button>
          {menuOpen && <Dropdown items={menuItems} onClose={() => setMenuOpen(false)} anchorRef={menuRef} />}
        </div>
      </div>

      {/* Expanded: shared files + public memory + sessions */}
      {isExpanded && (
        <div className="ml-5 mt-0.5 border-l border-border-light pl-1.5">
          <WorkspaceFiles
            detail={detail}
            onDeleteFile={onDeleteFile}
          />

          {/* Public memory (collapsible) */}
          {detail && hasMemory && (
            <div className="mt-1 rounded-[10px] border border-border-light">
              <button
                onClick={() => setMemOpen((v) => !v)}
                className="flex w-full items-center gap-1.5 px-2 py-1.5 text-[0.7rem] text-muted hover:text-fg-secondary transition-colors"
              >
                <Brain size={11} className="flex-shrink-0 text-accent" />
                <span className="flex-1 text-left font-medium">{tr("ws.memory")}</span>
                {memOpen ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
              </button>
              {memOpen && (
                <p className="px-2 pb-2 text-[0.7rem] leading-relaxed text-fg-secondary whitespace-pre-wrap">{detail.public_memory}</p>
              )}
            </div>
          )}
          {detail && !hasMemory && (
            <div className="px-2 py-1 text-[0.7rem] text-muted/50 flex items-center gap-1.5">
              <Brain size={11} /> {tr("ws.no.memory")}
            </div>
          )}

          {/* Sessions in workspace */}
          {sessions.length === 0 && (
            <p className="px-2 py-1 text-[0.7rem] text-muted/50">{tr("sidebar.empty")}</p>
          )}
          {sessions.map((s) => (
            <SessionRow
              key={s.session_id}
              session={s}
              active={activeSessionId === s.session_id}
              loose={false}
              workspaceId={ws.workspace_id}
              onSelect={onSelectSession}
              onRename={onRenameSession}
              onDelete={onDeleteSession}
              onRemoveFromWs={onRemoveFromWs}
              onDragStart={onSessionDragStart}
              selectable={selectable}
              selected={isSelected?.(s.session_id)}
              onToggleSelect={onToggleSelect}
            />
          ))}
        </div>
      )}
    </div>
  );
}
