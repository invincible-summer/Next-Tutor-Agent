"use client";
import { useEffect, useRef, useState } from "react";
import {
  Folder, FolderOpen, FolderPlus, Layers, Inbox, MessageSquare,
  Pencil, Trash2,
} from "lucide-react";
import type { Lang } from "@/lib/i18n";
import { cn } from "@/lib/cn";
import { relTime } from "@/lib/format";
import type { LibraryFolder, LibraryTree, SessionItem } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import type { SourceRef } from "./types";

function GroupLabel({ children }: { children: string }) {
  return (
    <div className="px-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-muted/70">{children}</div>
  );
}

/** 行内编辑输入框：Enter/失焦提交，Esc 取消。 */
function InlineEdit({
  initial,
  placeholder,
  autoFocus = true,
  onSubmit,
  onCancel,
}: {
  initial: string;
  placeholder?: string;
  autoFocus?: boolean;
  onSubmit: (value: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initial);
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (autoFocus) ref.current?.focus();
  }, [autoFocus]);

  const commit = () => {
    const v = value.trim();
    if (v) onSubmit(v);
    else onCancel();
  };

  return (
    <input
      ref={ref}
      value={value}
      placeholder={placeholder}
      onChange={(e) => setValue(e.target.value)}
      onClick={(e) => e.stopPropagation()}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          commit();
        } else if (e.key === "Escape") {
          onCancel();
        }
      }}
      className="h-7 w-full rounded-[6px] border border-border bg-bg px-2 text-sm text-fg"
    />
  );
}

/** 左栏：资料库文件夹树（全部/未归档/我的文件夹/学习区专属夹）+ 会话附件。 */
export function SourceSidebar({
  lang,
  tr,
  tree,
  sessions,
  selected,
  onSelect,
  onCreateFolder,
  onRenameFolder,
  onDeleteFolder,
}: {
  lang: Lang;
  tr: (key: string, fallback?: string) => string;
  tree: LibraryTree;
  sessions: SessionItem[];
  selected: SourceRef | null;
  onSelect: (s: SourceRef) => void;
  onCreateFolder: (name: string) => void;
  onRenameFolder: (id: string, name: string) => void;
  onDeleteFolder: (f: LibraryFolder) => void;
}) {
  const [creating, setCreating] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);

  const rootCount = tree.files.filter((f) => !f.folder_id).length;
  const myFolders = tree.folders.filter((f) => !f.workspace_id);
  const exclusiveFolders = tree.folders.filter((f) => f.workspace_id);

  const rowCls = (active: boolean) =>
    cn(
      "group flex cursor-pointer items-center gap-2 rounded-[8px] px-2 py-1.5",
      active ? "bg-accent-soft" : "hover:bg-surface-hover",
    );
  const textCls = (active: boolean) =>
    cn("min-w-0 flex-1 truncate text-sm", active ? "font-medium text-accent-strong" : "text-fg");

  const folderRow = (f: LibraryFolder, exclusive: boolean) => {
    const active = selected?.kind === "folder" && selected.id === f.id;
    return (
      <div key={f.id} onClick={() => onSelect({ kind: "folder", id: f.id })} className={rowCls(active)}>
        {active ? (
          <FolderOpen size={14} className="shrink-0 text-accent" />
        ) : (
          <Folder size={14} className="shrink-0 text-muted" />
        )}
        {editingId === f.id ? (
          <InlineEdit
            initial={f.name}
            onSubmit={(v) => {
              onRenameFolder(f.id, v);
              setEditingId(null);
            }}
            onCancel={() => setEditingId(null)}
          />
        ) : (
          <>
            <span className={textCls(active)} title={f.name}>{f.name}</span>
            {exclusive && <Badge tone="accent">{tr("res.exclusive")}</Badge>}
            {!exclusive && (
              <span className="hidden items-center gap-0.5 group-hover:flex">
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setEditingId(f.id);
                  }}
                  title={tr("res.rename")}
                  className="cursor-pointer rounded-[5px] p-1 text-muted hover:bg-surface hover:text-fg"
                >
                  <Pencil size={12} />
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteFolder(f);
                  }}
                  title={tr("res.delete")}
                  className="cursor-pointer rounded-[5px] p-1 text-muted hover:bg-danger/10 hover:text-danger"
                >
                  <Trash2 size={12} />
                </button>
              </span>
            )}
            <Badge tone="muted" className={cn("tnum", !exclusive && "group-hover:hidden")}>
              {f.file_count}
            </Badge>
          </>
        )}
      </div>
    );
  };

  return (
    <aside className="flex w-72 shrink-0 flex-col overflow-y-auto border-r border-border bg-surface">
      <div className="flex flex-col gap-1 p-3">
        <div className="flex items-center justify-between pr-1">
          <GroupLabel>{tr("res.group.library")}</GroupLabel>
          <button
            onClick={() => setCreating(true)}
            title={tr("res.new.folder")}
            className="cursor-pointer rounded-[6px] p-1 text-muted hover:bg-surface-hover hover:text-accent"
          >
            <FolderPlus size={14} />
          </button>
        </div>

        {creating && (
          <div className="px-1 pb-1">
            <InlineEdit
              initial=""
              placeholder={tr("res.new.folder.placeholder")}
              onSubmit={(v) => {
                onCreateFolder(v);
                setCreating(false);
              }}
              onCancel={() => setCreating(false)}
            />
          </div>
        )}

        {/* 全部文件 / 未归档 */}
        <div onClick={() => onSelect({ kind: "all" })} className={rowCls(selected?.kind === "all")}>
          <Layers size={14} className={cn("shrink-0", selected?.kind === "all" ? "text-accent" : "text-muted")} />
          <span className={textCls(selected?.kind === "all")}>{tr("res.all")}</span>
          <Badge tone="muted" className="tnum">{tree.files.length}</Badge>
        </div>
        <div onClick={() => onSelect({ kind: "root" })} className={rowCls(selected?.kind === "root")}>
          <Inbox size={14} className={cn("shrink-0", selected?.kind === "root" ? "text-accent" : "text-muted")} />
          <span className={textCls(selected?.kind === "root")}>{tr("res.root")}</span>
          <Badge tone="muted" className="tnum">{rootCount}</Badge>
        </div>

        {myFolders.map((f) => folderRow(f, false))}

        {exclusiveFolders.length > 0 && (
          <div className="mt-2 border-t border-border-light pt-2">
            {exclusiveFolders.map((f) => folderRow(f, true))}
          </div>
        )}

        <div className="mt-3">
          <GroupLabel>{tr("res.group.sessions")}</GroupLabel>
        </div>

        {sessions.length === 0 && <div className="px-1 py-1 text-xs text-muted">{tr("res.noSessions")}</div>}

        {sessions.map((s) => {
          const active = selected?.kind === "session" && selected.id === s.session_id;
          return (
            <div
              key={s.session_id}
              onClick={() => onSelect({ kind: "session", id: s.session_id })}
              className={cn(
                "flex cursor-pointer items-center gap-2 rounded-[8px] px-2 py-1.5",
                active ? "bg-accent-soft" : "hover:bg-surface-hover",
              )}
            >
              <MessageSquare size={14} className={cn("shrink-0", active ? "text-accent" : "text-muted")} />
              <span className={cn("min-w-0 flex-1 truncate text-sm", active ? "font-medium text-accent-strong" : "text-fg")} title={s.title}>
                {s.title}
              </span>
              <span className="shrink-0 text-[10px] text-muted">{relTime(s.updated_at, lang)}</span>
              <Badge tone="muted" className="tnum">
                {s.file_count}
              </Badge>
            </div>
          );
        })}
      </div>
    </aside>
  );
}
