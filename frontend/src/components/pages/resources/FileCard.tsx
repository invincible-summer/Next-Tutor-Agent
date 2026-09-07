"use client";
import { useRef, useState } from "react";
import { Check, Download, FolderInput, Pencil, Trash2, X } from "lucide-react";
import type { Lang } from "@/lib/i18n";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { AnchoredPopover } from "@/components/ui/AnchoredPopover";
import { FileTypeIcon } from "./file-icon";
import type { ResourceFile } from "./types";

/** 紧凑数字：中文过万用「万」，英文过千用「k」。 */
function fmtCount(n: number, lang: Lang): string {
  if (lang === "zh") {
    return n >= 10000 ? `${(n / 10000).toFixed(1)} 万` : String(n);
  }
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

/** 单个资料文件卡：类型图标 + 文件名 + 统计 + 摘要/主题 + 下载/移动/删除。 */
export function FileCard({
  file,
  lang,
  tr,
  moveTargets,
  onDownload,
  onMove,
  onRename,
  onDelete,
}: {
  file: ResourceFile;
  lang: Lang;
  tr: (key: string, fallback?: string) => string;
  /** 「移动到…」候选（不含当前所在文件夹）；不传则不显示移动按钮。 */
  moveTargets?: { id: string; name: string }[];
  onDownload?: () => void;
  onMove?: (folderId: string) => void;
  onRename?: (filename: string) => Promise<void> | void;
  onDelete?: () => void;
}) {
  const [moveOpen, setMoveOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(file.filename);
  const [savingName, setSavingName] = useState(false);
  const moveRef = useRef<HTMLDivElement>(null);

  const summary = file.summary && file.summary.length > 150 ? `${file.summary.slice(0, 150)}…` : file.summary;
  const stats = [
    `${fmtCount(file.char_count, lang)} ${tr("res.chars.unit")}`,
    file.chunk_count != null ? `${fmtCount(file.chunk_count, lang)} ${tr("res.chunks.unit")}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  const iconBtn =
    "cursor-pointer rounded-[6px] p-1 text-muted opacity-0 transition-opacity group-hover:opacity-100";

  return (
    <Card className="group relative flex h-full flex-col gap-2 p-3.5 transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md" pad={false}>
      <div className="flex items-start gap-2.5">
        <FileTypeIcon filename={file.filename} />
        <div className="min-w-0 flex-1">
          {editing ? (
            <div className="flex items-center gap-1">
              <input autoFocus value={name} onChange={(e) => setName(e.target.value)}
                className="min-w-0 flex-1 rounded border border-accent bg-transparent px-1.5 py-1 text-sm text-fg outline-none" />
              <button disabled={savingName} onClick={() => {
                if (!onRename || !name.trim()) return;
                setSavingName(true);
                Promise.resolve(onRename(name.trim())).then(() => setEditing(false)).finally(() => setSavingName(false));
              }} className="rounded p-1 text-accent hover:bg-accent-soft disabled:opacity-50"><Check size={13} /></button>
              <button disabled={savingName} onClick={() => { setName(file.filename); setEditing(false); }} className="rounded p-1 text-muted hover:bg-surface-hover disabled:opacity-50"><X size={13} /></button>
            </div>
          ) : (
            <div className="truncate text-sm font-medium text-fg" title={file.filename}>
              {file.filename}
            </div>
          )}
          <div className="tnum mt-0.5 text-[11px] text-muted">{stats}</div>
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          {onDownload && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onDownload();
              }}
              title={tr("res.download")}
              className={`${iconBtn} hover:bg-accent-soft hover:text-accent`}
            >
              <Download size={14} />
            </button>
          )}
          {onRename && !editing && (
            <button onClick={(e) => { e.stopPropagation(); setName(file.filename); setEditing(true); }} title={tr("res.rename")} className={`${iconBtn} hover:bg-accent-soft hover:text-accent`}>
              <Pencil size={14} />
            </button>
          )}
          {moveTargets && onMove && (
            <div className="relative" ref={moveRef}>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setMoveOpen((v) => !v);
                }}
                title={tr("res.move")}
                className={`${iconBtn} hover:bg-accent-soft hover:text-accent`}
              >
                <FolderInput size={14} />
              </button>
              {moveOpen && (
                <AnchoredPopover
                  anchorRef={moveRef}
                  open
                  onClose={() => setMoveOpen(false)}
                  placement="bottom-end"
                  className="z-20 w-40 rounded-[10px] border border-border bg-surface py-1 shadow-lg"
                >
                  {moveTargets.map((tgt) => (
                    <button
                      key={tgt.id}
                      onClick={(e) => {
                        e.stopPropagation();
                        setMoveOpen(false);
                        onMove(tgt.id);
                      }}
                      className="flex w-full cursor-pointer items-center gap-2 px-3 py-1.5 text-left text-xs text-fg-secondary hover:bg-surface-hover"
                    >
                      <span className="truncate">{tgt.name}</span>
                    </button>
                  ))}
                </AnchoredPopover>
              )}
            </div>
          )}
          {onDelete && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onDelete();
              }}
              title={tr("res.delete")}
              className={`${iconBtn} hover:bg-danger/10 hover:text-danger`}
            >
              <Trash2 size={14} />
            </button>
          )}
        </div>
      </div>
      {summary && <p className="line-clamp-3 text-xs leading-relaxed text-fg-secondary">{summary}</p>}
      {file.topics && file.topics.length > 0 && (
        <div className="mt-auto flex flex-wrap gap-1 pt-1">
          {file.topics.map((tp) => (
            <Badge key={tp} tone="accent">
              {tp}
            </Badge>
          ))}
        </div>
      )}
    </Card>
  );
}
