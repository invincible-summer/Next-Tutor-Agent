"use client";
import { useRef, useState } from "react";
import { CheckSquare, ClipboardList, MoreHorizontal, Paperclip, Pencil, Square, Trash2, FolderMinus } from "lucide-react";
import { useUIStore } from "@/lib/store";
import { t, type Lang } from "@/lib/i18n";
import { cn } from "@/lib/cn";
import { Dropdown, type DropdownItem } from "./Dropdown";
import { InlineEdit } from "./InlineEdit";
import type { SessionItem } from "@/lib/types";

/** 相对时间：刚刚 / n 分钟前 / n 小时前 / n 天前。 */
function relTime(ts: number, lang: Lang): string {
  const tr = (k: string) => t(lang, k, k);
  const diff = Math.max(0, Date.now() / 1000 - ts);
  if (diff < 60) return tr("time.just.now");
  if (diff < 3600) return tr("time.minutes.ago").replace("%n", String(Math.floor(diff / 60)));
  if (diff < 86400) return tr("time.hours.ago").replace("%n", String(Math.floor(diff / 3600)));
  return tr("time.days.ago").replace("%n", String(Math.floor(diff / 86400)));
}

/** 会话行：标题 + 相对时间 + 轮数/quiz/文件小徽标；hover 出现操作菜单。 */
export function SessionRow({
  session,
  active,
  loose,
  workspaceId,
  onSelect,
  onRename,
  onDelete,
  onRemoveFromWs,
  onDragStart,
  selectable,
  selected,
  onToggleSelect,
}: {
  session: SessionItem;
  active: boolean;
  /** Loose sessions render as full-size rows; workspace sessions as tree children. */
  loose: boolean;
  /** Set when the row lives inside a workspace (enables "remove from workspace"). */
  workspaceId?: string;
  onSelect: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  onRemoveFromWs?: (wsId: string, sessionId: string) => void;
  onDragStart: (id: string) => void;
  /** Batch-manage mode: rows show a checkbox and clicks toggle selection. */
  selectable?: boolean;
  selected?: boolean;
  onToggleSelect?: (id: string) => void;
}) {
  const { lang } = useUIStore();
  const tr = (k: string, fb?: string) => t(lang, k, fb);
  const [menuOpen, setMenuOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  if (editing) {
    return (
      <div className={loose ? "px-1.5 py-1" : "px-1 py-0.5"}>
        <InlineEdit
          initialValue={session.title}
          placeholder={tr("sidebar.untitled")}
          onCommit={(v) => { onRename(session.session_id, v); setEditing(false); }}
          onCancel={() => setEditing(false)}
        />
      </div>
    );
  }

  const items: DropdownItem[] = [
    { icon: Pencil, label: tr("ws.session.rename"), onClick: () => setEditing(true) },
    ...(workspaceId && onRemoveFromWs
      ? [{ icon: FolderMinus, label: tr("ws.session.remove"), onClick: () => onRemoveFromWs(workspaceId, session.session_id) }]
      : []),
    { icon: Trash2, label: tr("ws.delete"), danger: true, dividerBefore: true, onClick: () => onDelete(session.session_id) },
  ];

  return (
    <div
      draggable={!selectable}
      onDragStart={() => onDragStart(session.session_id)}
      onClick={() => (selectable ? onToggleSelect?.(session.session_id) : onSelect(session.session_id))}
      className={cn(
        "group cursor-pointer rounded-[10px] transition-colors",
        loose ? "px-2.5 py-2" : "px-2 py-1.5",
        selected ? "bg-accent-soft" : active ? "bg-accent-soft" : "hover:bg-surface-hover",
      )}
    >
      <div className="flex items-center gap-1.5">
        {selectable && (
          <span className={cn("shrink-0", selected ? "text-accent" : "text-muted/50")}>
            {selected ? <CheckSquare size={14} /> : <Square size={14} />}
          </span>
        )}
        <span className={cn(
          "min-w-0 flex-1 truncate",
          loose ? "text-[0.8rem]" : "text-[0.75rem]",
          active ? "font-medium text-accent-strong" : "text-fg-secondary group-hover:text-fg",
        )}>
          {session.title || tr("sidebar.untitled")}
        </span>
        {/* Row menu */}
        {!selectable && (
          <div className="relative shrink-0" ref={menuRef}>
            <button
              onClick={(e) => { e.stopPropagation(); setMenuOpen((v) => !v); }}
              className="flex items-center rounded-[4px] p-0.5 text-muted opacity-0 transition-opacity hover:text-fg group-hover:opacity-100"
            >
              <MoreHorizontal size={13} />
            </button>
            {menuOpen && <Dropdown items={items} onClose={() => setMenuOpen(false)} anchorRef={menuRef} />}
          </div>
        )}
      </div>
      {/* 元信息行：相对时间 + 轮数/quiz/文件徽标 */}
      <div className={cn(
        "mt-0.5 flex items-center gap-2 text-[0.62rem]",
        active ? "text-accent-strong/70" : "text-muted/80",
      )}>
        <span className="tnum">{relTime(session.updated_at, lang)}</span>
        <span className="tnum">{session.round_count ?? session.message_count} {tr("sidebar.rounds")}</span>
        {session.quiz_count > 0 && (
          <span className="tnum flex items-center gap-0.5">
            <ClipboardList size={9} />
            {session.quiz_count} {tr("sidebar.quiz.unit")}
          </span>
        )}
        {session.file_count > 0 && (
          <span className="tnum flex items-center gap-0.5">
            <Paperclip size={9} />
            {session.file_count} {tr("sidebar.file.unit")}
          </span>
        )}
      </div>
    </div>
  );
}
