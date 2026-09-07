"use client";
// 笔记中心：承接原左侧栏的全部管理能力（文件夹树/标签/搜索/多选批量/
// 拖拽归档/导出），以居中大弹窗呈现。左轨道管导航，主区卡片网格管浏览。
// 条件挂载：每次打开都是干净的初始状态（含 initialTag 预选）。
import { useMemo, useRef, useState } from "react";
import {
  ChevronRight, Download, FileText, Folder, FolderOpen, FolderPlus,
  MoreHorizontal, NotebookPen, Plus, Search, Sparkles, Tag, Trash2, X,
} from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Pager, paged } from "@/components/ui/Pager";
import { CenterPanel } from "@/components/ui/CenterPanel";
import { ConfirmModal } from "@/components/ui/Modal";
import { Dropdown, useClickOutside, type DropdownItem } from "@/components/sidebar/Dropdown";
import { InlineEdit } from "@/components/sidebar/InlineEdit";
import { bulkDeleteNotes, bulkMoveNotes } from "@/lib/api-notes";
import { cn } from "@/lib/cn";
import { relTime } from "@/lib/format";
import type { NoteSummary, NotesFolder, NoteTemplate, VaultSnapshot } from "@/lib/types-notes";

const PAGE_SIZE = 12;

export function NotesCenter({
  open, onClose, vault, currentId, initialTag, templates, tr, lang,
  onOpenNote, onCreateBlank, onCreateFromTemplate, onGenerate,
  onCreateFolder, onRenameFolder, onDeleteFolder,
  onExportAll, onExportFolder, onVaultChanged,
}: {
  open: boolean;
  onClose: () => void;
  vault: VaultSnapshot;
  currentId: string | null;
  initialTag: string | null;
  templates: NoteTemplate[];
  tr: (k: string, fallback?: string) => string;
  lang: "zh" | "en";
  onOpenNote: (id: string) => void;
  onCreateBlank: () => void;
  onCreateFromTemplate: (templateId: string) => void;
  onGenerate: () => void;
  onCreateFolder: (name: string, parentId?: string) => void;
  onRenameFolder: (folderId: string, name: string) => void;
  onDeleteFolder: (folderId: string) => void;
  onExportAll: () => void;
  onExportFolder: (folderId: string) => void;
  onVaultChanged: () => Promise<void> | void;
}) {
  const [activeFolder, setActiveFolder] = useState<string | null>(null);
  const [activeTag, setActiveTag] = useState<string | null>(initialTag);
  const [search, setSearch] = useState("");
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [page, setPage] = useState(0);
  const [newMenu, setNewMenu] = useState(false);
  const [folderMenu, setFolderMenu] = useState<string | null>(null);
  const [moveMenu, setMoveMenu] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set(vault.folders.filter((f) => !f.parent_id).map((f) => f.id)),
  );
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [dragOver, setDragOver] = useState<string | null>(null);
  const lastSelected = useRef<number | null>(null);
  const menuRef = useClickOutside<HTMLDivElement>(() => setNewMenu(false), newMenu);
  const moveRef = useClickOutside<HTMLDivElement>(() => setMoveMenu(false), moveMenu);
  // 文件夹菜单（在滚动列表内）的锚点：同一时刻只开一个，条件挂到当前行
  const folderMenuRef = useRef<HTMLDivElement>(null);

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    return vault.notes
      .filter((n) => activeFolder === null || n.folder_id === activeFolder)
      .filter((n) => !activeTag || n.tags.includes(activeTag))
      .filter((n) => !q || n.title.toLowerCase().includes(q) || n.tags.some((t) => t.toLowerCase().includes(q)))
      .sort((a, b) => b.updated_at - a.updated_at);
  }, [vault.notes, activeFolder, activeTag, search]);
  const pageItems = paged(visible, page, PAGE_SIZE);
  const folderCounts = useMemo(() => Object.fromEntries(vault.folders.map((f) => [f.id, vault.notes.filter((n) => n.folder_id === f.id).length])), [vault]);
  const folderById = useMemo(
    () => Object.fromEntries(vault.folders.map((f) => [f.id, f])), [vault]);
  const children = useMemo(() => {
    const map: Record<string, NotesFolder[]> = {};
    for (const folder of vault.folders) (map[folder.parent_id || ""] ||= []).push(folder);
    Object.values(map).forEach((items) => items.sort((a, b) => a.name.localeCompare(b.name)));
    return map;
  }, [vault.folders]);
  const folderPath = useMemo(() => {
    const path = (f: NotesFolder): string =>
      f.parent_id && folderById[f.parent_id] ? `${path(folderById[f.parent_id])} / ${f.name}` : f.name;
    return Object.fromEntries(vault.folders.map((f) => [f.id, path(f)]));
  }, [vault.folders, folderById]);
  const folderName = (id?: string) => (id ? folderById[id]?.name : undefined);

  const toggleSelected = (id: string, index: number, shift: boolean) => {
    setSelected((current) => {
      const next = new Set(current);
      if (shift && lastSelected.current !== null) {
        const [a, b] = [lastSelected.current, index].sort((x, y) => x - y);
        visible.slice(a, b + 1).forEach((note) => next.add(note.id));
      } else if (next.has(id)) next.delete(id); else next.add(id);
      lastSelected.current = index;
      return next;
    });
  };

  const moveSelection = async (folderId: string, fallbackId?: string) => {
    const ids = fallbackId && !selected.has(fallbackId)
      ? [fallbackId]
      : (selected.size > 0 ? [...selected] : (fallbackId ? [fallbackId] : []));
    if (ids.length === 0) return;
    await bulkMoveNotes(ids, folderId);
    setSelected(new Set());
    setDragOver(null);
    setMoveMenu(false);
    await onVaultChanged();
  };
  const deleteSelection = async () => {
    if (selected.size === 0) return;
    await bulkDeleteNotes([...selected]);
    setSelected(new Set());
    setDeleteOpen(false);
    await onVaultChanged();
  };

  const renderFolders = (parentId = ""): React.ReactNode => (children[parentId] || []).map((folder) => {
    const hasChildren = Boolean(children[folder.id]?.length);
    const isOpen = expanded.has(folder.id);
    const menuItems: DropdownItem[] = [
      { label: tr("center.folder.newChild"), icon: FolderPlus, onClick: () => { const name = window.prompt(tr("center.folder.newChild")); if (name?.trim()) onCreateFolder(name, folder.id); } },
      { label: tr("notes.rename", "重命名"), icon: NotebookPen, onClick: () => { const name = window.prompt(tr("notes.rename", "重命名"), folder.name); if (name?.trim()) onRenameFolder(folder.id, name); } },
      { label: tr("notes.export", "导出"), icon: Download, onClick: () => onExportFolder(folder.id) },
      { label: tr("notes.deleteFolder"), icon: Trash2, danger: true, onClick: () => { if (window.confirm(tr("notes.deleteFolder.confirm"))) onDeleteFolder(folder.id); } },
    ];
    return (
      <div key={folder.id}>
        <div
          onDragOver={(e) => { e.preventDefault(); setDragOver(folder.id); }}
          onDragLeave={() => setDragOver((id) => id === folder.id ? null : id)}
          onDrop={(e) => { e.preventDefault(); void moveSelection(folder.id, e.dataTransfer.getData("text/note-id")); }}
          className={cn("group flex items-center rounded-[8px] transition-colors hover:bg-surface-hover", dragOver === folder.id && "bg-accent-soft ring-1 ring-accent")}
        >
          <button onClick={() => setExpanded((set) => { const next = new Set(set); if (next.has(folder.id)) next.delete(folder.id); else next.add(folder.id); return next; })} className="p-1 text-muted">
            {hasChildren ? <ChevronRight size={12} className={cn("transition-transform duration-200", isOpen && "rotate-90")} /> : <span className="block size-3" />}
          </button>
          <button onClick={() => { setActiveFolder(folder.id); setPage(0); }} className={cn("flex min-w-0 flex-1 items-center gap-1.5 px-1 py-1.5 text-left text-xs", activeFolder === folder.id ? "font-medium text-accent-strong" : "text-fg-secondary")}>
            <Folder size={13} className="shrink-0" /><span className="truncate">{folder.name}</span><span className="ml-auto tnum text-[10px] text-muted">{folderCounts[folder.id] || 0}</span>
          </button>
          <div className="relative" ref={folderMenu === folder.id ? folderMenuRef : undefined}>
            <button onClick={() => setFolderMenu(folderMenu === folder.id ? null : folder.id)} className="p-1 text-muted opacity-0 transition-opacity group-hover:opacity-100"><MoreHorizontal size={13} /></button>
            {folderMenu === folder.id && <Dropdown items={menuItems} onClose={() => setFolderMenu(null)} anchorRef={folderMenuRef} />}
          </div>
        </div>
        {isOpen && hasChildren && (
          <div className="ml-3 border-l border-border-light pl-2">
            {renderFolders(folder.id)}
          </div>
        )}
      </div>
    );
  });

  const hasFilter = activeFolder !== null || activeTag !== null || search.trim() !== "";
  const clearFilters = () => {
    setActiveFolder(null);
    setActiveTag(null);
    setSearch("");
    setPage(0);
  };

  return (
    <CenterPanel
      open={open}
      onClose={onClose}
      width={960}
      title={
        <span className="flex items-center gap-2">
          <span className="flex size-7 items-center justify-center rounded-lg bg-accent-soft text-accent">
            <FolderOpen size={15} />
          </span>
          {tr("center.title")}
        </span>
      }
      extra={
        <span className="mr-2 hidden text-[11px] text-muted tnum sm:block">
          {tr("center.stats", "{n} 篇 · {m} 个文件夹")
            .replace("{n}", String(vault.stats.note_count))
            .replace("{m}", String(vault.stats.folder_count))}
        </span>
      }
      bodyClassName="flex flex-col overflow-hidden"
    >
      {/* 工具行：搜索 + 新建 + AI + 导出 */}
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border px-4 py-3">
        <div className="relative min-w-44 flex-1 sm:max-w-64">
          <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 z-10 -translate-y-1/2 text-muted" />
          <Input value={search} onChange={(e) => { setSearch(e.target.value); setPage(0); }} placeholder={tr("notes.search")} className="h-8 py-1! pl-7 pr-2 text-sm" />
        </div>
        <div className="relative" ref={menuRef}>
          <Button variant="primary" size="sm" icon={<Plus size={14} />} onClick={() => setNewMenu((v) => !v)}>{tr("notes.new")}</Button>
          {newMenu && <div className="motion-pop absolute left-0 top-9 z-30 w-52 rounded-[10px] border border-border bg-surface p-1 shadow-lg">
            <button className="w-full cursor-pointer rounded-md px-2 py-1.5 text-left text-xs transition-colors hover:bg-surface-hover" onClick={() => { setNewMenu(false); onCreateBlank(); }}>{tr("notes.blank")}</button>
            {templates.slice(0, 8).map((t) => <button key={t.id} className="w-full cursor-pointer truncate rounded-md px-2 py-1.5 text-left text-xs transition-colors hover:bg-surface-hover" onClick={() => { setNewMenu(false); onCreateFromTemplate(t.id); }}>{lang === "en" && t.name_en ? t.name_en : t.name}</button>)}
          </div>}
        </div>
        <Button variant="accent2" size="sm" icon={<Sparkles size={14} />} onClick={onGenerate}>AI</Button>
        <button onClick={onExportAll} title={tr("notes.exportAll")} aria-label={tr("notes.exportAll")}
          className="cursor-pointer rounded-md p-1.5 text-muted transition-colors hover:bg-surface-hover hover:text-accent">
          <Download size={15} />
        </button>
      </div>

      {/* 批量操作条 */}
      {selected.size > 0 && (
        <div className="relative flex shrink-0 flex-wrap items-center gap-1.5 border-b border-border bg-accent-soft px-4 py-1.5 text-[11px] text-accent-strong">
          <span className="mr-auto">{tr("center.selected", "已选 {n} 篇").replace("{n}", String(selected.size))}</span>
          <div className="relative" ref={moveRef}>
            <button className="cursor-pointer rounded-md px-2 py-1 transition-colors hover:bg-surface" onClick={() => setMoveMenu((v) => !v)}>{tr("center.move", "移动到")}</button>
            {moveMenu && <div className="motion-pop absolute bottom-9 right-0 z-30 max-h-64 w-56 overflow-y-auto rounded-[10px] border border-border bg-surface p-1 shadow-lg">
              <button className="w-full cursor-pointer truncate rounded-md px-2 py-1.5 text-left text-xs text-fg-secondary transition-colors hover:bg-surface-hover" onClick={() => void moveSelection("")}>{tr("notes.unfiled")}</button>
              {vault.folders.map((f) => (
                <button key={f.id} className="w-full cursor-pointer truncate rounded-md px-2 py-1.5 text-left text-xs text-fg-secondary transition-colors hover:bg-surface-hover" onClick={() => void moveSelection(f.id)}>{folderPath[f.id]}</button>
              ))}
            </div>}
          </div>
          <button className="cursor-pointer rounded-md px-2 py-1 text-danger transition-colors hover:bg-danger/10" onClick={() => setDeleteOpen(true)}>{tr("notes.delete", "删除")}</button>
          <button className="cursor-pointer rounded-md p-1 transition-colors hover:bg-surface" onClick={() => setSelected(new Set())}><X size={12} /></button>
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        {/* 左轨道：文件夹 + 标签（窄屏隐藏，与旧侧栏的 md 断点策略一致） */}
        <div className="hidden w-52 shrink-0 overflow-y-auto border-r border-border p-2.5 sm:block sm:w-60">
          <div className="px-1 pb-1 text-[10px] uppercase tracking-wide text-muted">{tr("tb.folder")}</div>
          <FolderRow icon={<FolderOpen size={13} />} label={tr("notes.all")} count={vault.stats.note_count} active={activeFolder === null} onClick={() => { setActiveFolder(null); setPage(0); }} />
          {renderFolders()}
          <div onDragOver={(e) => { e.preventDefault(); setDragOver("root"); }} onDrop={(e) => { e.preventDefault(); void moveSelection("", e.dataTransfer.getData("text/note-id")); }} className={cn("rounded-md", dragOver === "root" && "bg-accent-soft ring-1 ring-accent")}>
            <FolderRow icon={<FileText size={13} />} label={tr("notes.unfiled")} count={vault.notes.filter((n) => !n.folder_id).length} active={activeFolder === ""} onClick={() => { setActiveFolder(""); setPage(0); }} />
          </div>
          {creatingFolder
            ? <InlineEdit initialValue="" placeholder={tr("notes.newFolder")} onCommit={(v) => onCreateFolder(v, activeFolder || "")} onCancel={() => setCreatingFolder(false)} />
            : <button onClick={() => setCreatingFolder(true)} className="flex w-full cursor-pointer items-center gap-1.5 rounded-[8px] px-2 py-1.5 text-xs text-muted transition-colors hover:bg-surface-hover hover:text-accent"><FolderPlus size={13} /> {tr("notes.newFolder")}</button>}

          {Object.keys(vault.tags).length > 0 && (
            <div className="mt-3 border-t border-border pt-2">
              <div className="px-1 pb-1 text-[10px] uppercase tracking-wide text-muted">{tr("notes.tags")}</div>
              <div className="flex flex-wrap gap-1.5 px-1">
                {Object.entries(vault.tags).slice(0, 14).map(([tag, count]) => (
                  <button key={tag} onClick={() => { setActiveTag(activeTag === tag ? null : tag); setPage(0); }} className={cn("flex cursor-pointer items-center gap-1 rounded-full px-2.5 py-0.5 text-xs transition-colors", activeTag === tag ? "bg-accent-soft text-accent-strong" : "bg-surface-sunken text-muted hover:bg-accent-soft hover:text-accent")}><Tag size={10} />{tag}<span className="opacity-60">{count}</span></button>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* 主区：笔记卡片网格 */}
        <div className="min-w-0 flex-1 overflow-y-auto p-4">
          {vault.notes.length === 0 ? (
            <div className="flex h-full min-h-64 flex-col items-center justify-center gap-3 text-center">
              <NotebookPen size={26} className="text-muted" />
              <div className="text-sm font-medium text-fg">{tr("notes.empty.title")}</div>
              <div className="max-w-72 text-xs text-muted">{tr("notes.empty.desc")}</div>
              <div className="mt-1 flex items-center gap-2">
                <Button size="sm" variant="outline" icon={<Plus size={13} />} onClick={onCreateBlank}>{tr("notes.blank")}</Button>
                <Button size="sm" variant="accent2" icon={<Sparkles size={13} />} onClick={onGenerate}>{tr("notes.generate")}</Button>
              </div>
            </div>
          ) : pageItems.length === 0 ? (
            <div className="flex h-full min-h-64 flex-col items-center justify-center gap-2 text-center">
              <Search size={22} className="text-muted" />
              <div className="text-xs text-muted">{tr("center.empty", "没有符合条件的笔记")}</div>
              {hasFilter && <button onClick={clearFilters} className="cursor-pointer text-xs text-accent hover:underline">{tr("center.clearFilters", "清除筛选")}</button>}
            </div>
          ) : (
            <>
              <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                {pageItems.map((note) => {
                  const index = visible.findIndex((item) => item.id === note.id);
                  return <NoteCard key={note.id} note={note} folderLabel={folderName(note.folder_id)} current={note.id === currentId} selected={selected.has(note.id)} lang={lang} tr={tr}
                    onSelect={(event) => { event.stopPropagation(); toggleSelected(note.id, index, event.shiftKey); }}
                    onClick={() => onOpenNote(note.id)}
                    onDragStart={(event) => { event.dataTransfer.setData("text/note-id", note.id); event.dataTransfer.effectAllowed = "move"; }} />;
                })}
              </div>
              <div className="flex items-center justify-between gap-2 pt-3">
                <button onClick={() => setSelected(new Set(visible.map((n) => n.id)))} className="cursor-pointer text-[10px] uppercase tracking-wide text-muted transition-colors hover:text-accent">
                  {tr("notes.count", "{n} 篇").replace("{n}", String(visible.length))} · {tr("center.selectAll", "全选")}
                </button>
                <Pager page={page} total={visible.length} per={PAGE_SIZE} onPage={setPage} />
              </div>
            </>
          )}
        </div>
      </div>

      {/* 批量删除确认 */}
      <ConfirmModal
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        onConfirm={() => void deleteSelection()}
        title={tr("center.bulkDelete", "删除 {n} 篇笔记").replace("{n}", String(selected.size))}
        desc={tr("notes.confirmDelete")}
        confirmText={tr("notes.delete", "删除")}
        cancelText={tr("notes.cancel", "取消")}
      />
    </CenterPanel>
  );
}

function FolderRow({ icon, label, count, active, onClick }: { icon: React.ReactNode; label: string; count: number; active: boolean; onClick: () => void }) {
  return <button onClick={onClick} className={cn("flex w-full cursor-pointer items-center gap-1.5 rounded-[8px] px-2 py-1.5 text-xs transition-colors", active ? "bg-accent-soft font-medium text-accent-strong" : "text-fg-secondary hover:bg-surface-hover")}>{icon}<span className="truncate">{label}</span><span className="ml-auto tnum text-[10px] text-muted">{count}</span></button>;
}

function NoteCard({ note, folderLabel, current, selected, lang, tr, onSelect, onClick, onDragStart }: {
  note: NoteSummary;
  folderLabel?: string;
  current: boolean;
  selected: boolean;
  lang: "zh" | "en";
  tr: (k: string, fallback?: string) => string;
  onSelect: (event: React.MouseEvent<HTMLInputElement>) => void;
  onClick: () => void;
  onDragStart: (event: React.DragEvent<HTMLDivElement>) => void;
}) {
  return (
    <div draggable onDragStart={onDragStart}
      className={cn("group cursor-pointer rounded-[10px] border p-3 transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md",
        current ? "border-accent/60 bg-accent-soft/40" : "border-border bg-bg hover:border-accent/40",
        selected && "ring-1 ring-accent")}>
      <div className="flex items-start gap-2">
        <input type="checkbox" checked={selected} onClick={onSelect} onChange={() => undefined}
          className="mt-0.5 size-3.5 shrink-0 cursor-pointer accent-[var(--color-accent)] opacity-0 transition-opacity max-sm:opacity-60 group-hover:opacity-100 checked:opacity-100" />
        <div className="min-w-0 flex-1" onClick={onClick}>
          <div className="flex items-center gap-1.5">
            <FileText size={13} className={cn("shrink-0", current ? "text-accent" : "text-muted")} />
            <span className={cn("truncate text-[13px]", current ? "font-medium text-accent-strong" : "font-medium text-fg")}>
              {note.title || tr("tb.untitled")}
            </span>
          </div>
          {(folderLabel || note.tags.length > 0) && (
            <div className="mt-1.5 flex flex-wrap items-center gap-1">
              {folderLabel && <span className="flex items-center gap-0.5 rounded-full bg-surface-sunken px-1.5 py-0.5 text-[10px] text-muted"><Folder size={9} />{folderLabel}</span>}
              {note.tags.slice(0, 3).map((t) => <span key={t} className="rounded-full bg-accent-soft px-1.5 py-0.5 text-[10px] text-accent-strong">#{t}</span>)}
            </div>
          )}
          <div className="mt-1.5 text-[10px] text-muted tnum">{relTime(note.updated_at, lang)} · {tr("tb.words", "{n} 字").replace("{n}", String(note.word_count))}</div>
        </div>
      </div>
    </div>
  );
}
