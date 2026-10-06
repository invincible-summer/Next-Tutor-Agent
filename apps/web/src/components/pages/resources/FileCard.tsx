"use client";
import { useRef, useState } from "react";
import { Check, Download, Eye, FolderInput, Pencil, Trash2, X } from "lucide-react";
import type { Lang } from "@/lib/i18n";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
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
  onPreview,
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
  onPreview?: () => void;
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

  return (
    <Card className="group relative flex h-full flex-col gap-2 p-3.5 transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md" pad={false}>
      <div className="flex items-start gap-2.5">
        <FileTypeIcon filename={file.filename} />
        <div className="min-w-0 flex-1">
          {editing ? (
            <div className="flex items-center gap-1">
              <input autoFocus value={name} onChange={(e) => setName(e.target.value)}
                aria-label={tr("res.rename")}
                className="min-w-0 flex-1 rounded border border-accent bg-transparent px-1.5 py-1 text-sm text-fg outline-none" />
              <Button iconOnly variant="ghost" tone="accent" size="sm" disabled={savingName} aria-label={tr("res.rename")} title={tr("res.rename")}
                onClick={() => {
                  if (!onRename || !name.trim()) return;
                  setSavingName(true);
                  Promise.resolve(onRename(name.trim())).then(() => setEditing(false)).finally(() => setSavingName(false));
                }}
              >
                <Check size={13} />
              </Button>
              <Button iconOnly variant="ghost" size="sm" disabled={savingName} aria-label={tr("common.cancel")} title={tr("common.cancel")}
                onClick={() => { setName(file.filename); setEditing(false); }}
              >
                <X size={13} />
              </Button>
            </div>
          ) : (
            <div className="truncate text-sm font-medium text-fg" title={file.filename}>
              {file.filename}
            </div>
          )}
          <div className="tnum mt-0.5 text-[11px] text-muted">{stats}</div>
        </div>
        {/* 行内动作：悬停/键盘聚焦显现，触屏（<sm）常显，避免 hover-only */}
        <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100 max-sm:opacity-100">
          {onPreview && (
            <Button iconOnly variant="ghost" tone="accent" size="sm" onClick={onPreview}
              title={lang === "en" ? "Preview PDF" : "预览 PDF"}
              aria-label={lang === "en" ? "Preview PDF" : "预览 PDF"}
            >
              <Eye size={14} />
            </Button>
          )}
          {onDownload && (
            <Button
              iconOnly variant="ghost" tone="accent" size="sm"
              onClick={(e) => {
                e.stopPropagation();
                onDownload();
              }}
              title={tr("res.download")}
              aria-label={tr("res.download")}
            >
              <Download size={14} />
            </Button>
          )}
          {onRename && !editing && (
            <Button iconOnly variant="ghost" tone="accent" size="sm"
              onClick={(e) => { e.stopPropagation(); setName(file.filename); setEditing(true); }}
              title={tr("res.rename")} aria-label={tr("res.rename")}
            >
              <Pencil size={14} />
            </Button>
          )}
          {moveTargets && onMove && (
            <div className="relative" ref={moveRef}>
              <Button
                iconOnly variant="ghost" tone="accent" size="sm"
                onClick={(e) => {
                  e.stopPropagation();
                  setMoveOpen((v) => !v);
                }}
                title={tr("res.move")}
                aria-label={tr("res.move")}
              >
                <FolderInput size={14} />
              </Button>
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
            <Button
              iconOnly variant="ghost" tone="danger" size="sm"
              onClick={(e) => {
                e.stopPropagation();
                onDelete();
              }}
              title={tr("res.delete")}
              aria-label={tr("res.delete")}
            >
              <Trash2 size={14} />
            </Button>
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
