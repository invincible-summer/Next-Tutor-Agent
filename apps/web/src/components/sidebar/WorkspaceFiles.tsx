"use client";
import { useState } from "react";
import { FileText, FileType, FileCode, Presentation, Trash2, Download } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useUIStore } from "@/lib/store";
import { t } from "@/lib/i18n";
import { downloadLibraryFile } from "@/lib/api";
import type { AttachmentMeta } from "@/lib/types";

function fileIcon(name: string): LucideIcon {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  switch (ext) {
    case "pdf": return FileText;
    case "docx": case "doc": return FileType;
    case "pptx": case "ppt": return Presentation;
    case "md": case "markdown": return FileCode;
    default: return FileText;
  }
}

function fileMeta(f: AttachmentMeta, chunkUnit: string): string {
  const kb = Math.max(1, Math.round(f.char_count / 1024));
  return f.chunk_count ? `${f.chunk_count} ${chunkUnit} · ${kb} KB` : `${kb} KB`;
}

/** Workspace-readable file list: per-file rows with download + (exclusive
 *  folder files only) delete.
 *  P6-C3：边栏上传入口已移除——上传统一在「资料中心」进行（教材库/文件库）。 */
export function WorkspaceFiles({
  detail,
  onDeleteFile,
}: {
  detail: { knowledge_files: AttachmentMeta[]; library_folder_id?: string } | undefined;
  onDeleteFile: (file: AttachmentMeta) => void;
}) {
  const { lang } = useUIStore();
  const tr = (k: string, fb?: string) => t(lang, k, fb);
  const [dlError, setDlError] = useState<string | null>(null);
  const files = detail?.knowledge_files ?? [];
  const exclusiveId = detail?.library_folder_id ?? "";

  return (
    <div className="px-1 py-1">
      <p className="px-1.5 pb-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-muted/70">
        {tr("ws.shared.files")}{files.length > 0 ? ` · ${files.length}` : ""}
      </p>

      {files.length === 0 && (
        <p className="px-1.5 pb-1.5 text-[0.7rem] leading-relaxed text-muted/60">{tr("ws.files.empty")}</p>
      )}

      {files.map((f) => {
        const Icon = fileIcon(f.filename);
        // Only exclusive-folder files are deletable here — they belong to the
        // workspace. Files from other selected sources are library files;
        // deselect them in the workspace settings modal instead.
        const deletable = !!exclusiveId && f.folder_id === exclusiveId;
        return (
          <div
            key={f.id}
            className="group flex items-center gap-2 rounded-[10px] px-1.5 py-1.5 transition-colors hover:bg-surface-hover"
          >
            <Icon size={13} className="flex-shrink-0 text-accent/70" />
            <div className="flex-1 min-w-0">
              <p className="truncate text-xs text-fg-secondary" title={f.filename}>{f.filename}</p>
              <p className="text-[0.65rem] text-muted/70">{fileMeta(f, tr("ws.chunks.unit"))}</p>
            </div>
            {f.has_original && (
              <button
                onClick={() => {
                  setDlError(null);
                  downloadLibraryFile(f.id).catch(() => setDlError(tr("ws.files.download.failed")));
                }}
                title={tr("ws.files.download")}
                className="flex-shrink-0 opacity-0 group-hover:opacity-100 text-muted hover:text-accent transition-opacity"
              >
                <Download size={12} />
              </button>
            )}
            {deletable && (
              <button
                onClick={() => onDeleteFile(f)}
                title={tr("ws.files.delete.title")}
                className="flex-shrink-0 opacity-0 group-hover:opacity-100 text-muted hover:text-danger transition-opacity"
              >
                <Trash2 size={12} />
              </button>
            )}
          </div>
        );
      })}
      {dlError && <p className="px-1.5 pt-1 text-[0.65rem] text-danger">{dlError}</p>}
    </div>
  );
}
