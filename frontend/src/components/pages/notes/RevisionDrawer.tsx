"use client";
// 修订历史抽屉：版本列表（作者徽章 user/agent）+ 预览 + 恢复。
import { useEffect, useState } from "react";
import { Bot, Eye, History, RotateCcw, UserRound } from "lucide-react";
import { Drawer } from "@/components/ui/Drawer";
import { MiniMarkdown } from "@/components/chat/markdown";
import { cn } from "@/lib/cn";
import { getNoteRevisions, readNoteRevision, restoreNoteRevision } from "@/lib/api-notes";
import type { NoteRevision } from "@/lib/types-notes";
import { fmtTime } from "@/lib/format";

export function RevisionDrawer({
  open, onClose, noteId, currentRevision, tr,
  onRestored,
}: {
  open: boolean;
  onClose: () => void;
  noteId: string;
  currentRevision: number;
  tr: (k: string, fallback?: string) => string;
  onRestored: () => void;
}) {
  const [revisions, setRevisions] = useState<NoteRevision[]>([]);
  const [busy, setBusy] = useState(false);

  const [previewRev, setPreviewRev] = useState<number | null>(null);
  const [previewContent, setPreviewContent] = useState("");
  useEffect(() => {
    if (!open || !noteId) return;
    let alive = true;
    getNoteRevisions(noteId)
      .then((r) => { if (alive) setRevisions(r.revisions); })
      .catch(() => { if (alive) setRevisions([]); });
    return () => { alive = false; };
  }, [open, noteId]);
  const preview = previewRev === null ? null : { rev: previewRev, content: previewContent };

  const view = async (rev: number) => {
    if (previewRev === rev) {
      setPreviewRev(null);
      return;
    }
    try {
      const { content } = await readNoteRevision(noteId, rev);
      setPreviewRev(rev);
      setPreviewContent(content);
    } catch { /* ignore */ }
  };

  const restore = async (rev: number) => {
    if (!window.confirm(tr("rev.restore.confirm", "用版本 {n} 的内容替换当前正文？").replace("{n}", String(rev)))) return;
    setBusy(true);
    try {
      await restoreNoteRevision(noteId, rev);
      onRestored();
      onClose();
    } catch { /* ignore */ } finally {
      setBusy(false);
    }
  };

  return (
    <Drawer open={open} onClose={() => { setPreviewRev(null); onClose(); }} title={
      <span className="flex items-center gap-1.5"><History size={14} /> {tr("rev.title")}</span>
    } width={460}>
      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-4 py-3">
        {revisions.length === 0 && (
          <div className="py-8 text-center text-xs text-muted">{tr("rev.empty")}</div>
        )}
        {revisions.map((r) => {
          const isCurrent = r.revision === currentRevision;
          return (
            <div key={r.revision}
              className={cn(
                "rounded-[10px] border px-3 py-2.5",
                isCurrent ? "border-accent/40 bg-accent-soft" : "border-border",
              )}>
              <div className="flex items-center gap-2">
                <span className={cn(
                  "flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px]",
                  r.author === "agent" ? "bg-accent2/10 text-accent2" : "bg-surface-hover text-muted",
                )}>
                  {r.author === "agent" ? <Bot size={10} /> : <UserRound size={10} />}
                  {r.author === "agent" ? tr("rev.author.agent") : tr("rev.author.user")}
                </span>
                <span className="text-xs font-medium text-fg tnum">v{r.revision}</span>
                {isCurrent && (
                  <span className="rounded-md bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent">
                    {tr("rev.current")}
                  </span>
                )}
                <span className="ml-auto text-[11px] text-muted tnum">
                  {fmtTime(r.ts)}
                </span>
              </div>
              <div className="mt-1 flex items-center gap-2">
                <span className="text-[10px] text-muted tnum">
                  {tr("tb.words", "{n} 字").replace("{n}", String(r.word_count))}
                </span>
                <button
                  onClick={() => void view(r.revision)}
                  className="ml-auto flex cursor-pointer items-center gap-1 text-[11px] text-muted transition-colors hover:text-accent">
                  <Eye size={11} /> {tr("rev.view")}
                </button>
                {!isCurrent && (
                  <button
                    disabled={busy}
                    onClick={() => void restore(r.revision)}
                    className="flex cursor-pointer items-center gap-1 text-[11px] text-muted transition-colors hover:text-accent2 disabled:opacity-50">
                    <RotateCcw size={11} /> {tr("rev.restore")}
                  </button>
                )}
              </div>
              {preview?.rev === r.revision && (
                <div className="mt-2 max-h-56 overflow-y-auto rounded-md border border-border bg-bg p-2">
                  <MiniMarkdown className="text-[11px]">{preview.content}</MiniMarkdown>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </Drawer>
  );
}
