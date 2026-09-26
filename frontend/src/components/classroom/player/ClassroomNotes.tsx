"use client";
/* 课堂笔记面板（plan.md §12.6/§13.5，阶段 H04）。
 *
 * 「记下这里」落本 run 批注（slide/segment 定位 + 自动摘录与手写分开）；
 * 「保存到笔记中心」走后端确定性汇总（不额外调 LLM），同
 * Idempotency-Key 重放返回同一笔记。批注只影响当前课堂，不伪装成
 * 学生总结。列表为本播放会话内的本地视图；完整批注随 save-note 汇总
 * 进笔记中心。
 */
import { useState } from "react";
import { Check, Loader2, NotebookPen, StickyNote } from "lucide-react";
import type { FlatSegment } from "@/lib/classroom/useClassroomPlayer";

export interface ClassroomNotesStrings {
  title: string;
  addHere: string;
  placeholder: string;
  save: string;
  saved: string;
  saveToCenter: string;
  savedToCenter: string;
  empty: string;
}

interface LocalAnnotation {
  annotation_id: string;
  user_text: string;
  auto_excerpt: string;
}

export function ClassroomNotes(
  { workspaceId, lessonId, runId, current, s }:
  {
    workspaceId: string;
    lessonId: string;
    runId: string;
    current: FlatSegment | null;
    s: ClassroomNotesStrings;
  },
) {
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [savedFlash, setSavedFlash] = useState(false);
  const [centerSaving, setCenterSaving] = useState(false);
  const [centerNoteId, setCenterNoteId] = useState<string | null>(null);
  const [annotations, setAnnotations] = useState<LocalAnnotation[]>([]);

  const save = async () => {
    if (!current || saving) return;
    setSaving(true);
    try {
      const { addRunNote } = await import("@/lib/api-classroom");
      const text = draft.trim();
      const resp = await addRunNote(workspaceId, lessonId, runId, {
        slide_id: current.slideId,
        segment_id: current.segmentId,
        user_text: text,
      });
      setAnnotations((prev) => [...prev, {
        annotation_id: resp.annotation_id,
        user_text: text,
        auto_excerpt: current.displayText,
      }]);
      setDraft("");
      setSavedFlash(true);
      window.setTimeout(() => setSavedFlash(false), 1600);
    } finally {
      setSaving(false);
    }
  };

  const saveToCenter = async () => {
    if (centerSaving) return;
    setCenterSaving(true);
    try {
      const { saveRunNote } = await import("@/lib/api-classroom");
      // 稳定 key：一个 run 的笔记中心汇总只创建一份（重放返回同一笔记）
      const key = `note-${runId}-summary-0001`.slice(0, 64);
      const resp = await saveRunNote(workspaceId, lessonId, runId,
                                     { include_user_notes: true }, key);
      setCenterNoteId(resp.note_id);
    } finally {
      setCenterSaving(false);
    }
  };

  return (
    <section aria-label={s.title} className="flex flex-col gap-2">
      <div className="flex items-center gap-1.5 text-sm font-medium text-fg">
        <StickyNote size={14} className="text-accent" />
        {s.title}
      </div>
      {current && (
        <>
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={2}
            maxLength={4000}
            placeholder={s.placeholder}
            className="resize-none rounded-[10px] border border-border bg-bg px-3 py-2 text-[0.78rem] text-fg outline-none transition-colors placeholder:text-muted focus:border-accent" />
          <div className="flex items-center gap-2">
            <button type="button" onClick={() => void save()}
                    disabled={saving || !draft.trim()}
                    className="inline-flex h-9 cursor-pointer items-center gap-1.5 rounded-full border border-border px-3 text-xs text-fg-secondary transition-colors hover:border-accent/50 hover:text-fg disabled:opacity-50">
              {saving
                ? <Loader2 size={12} className="animate-spin" />
                : savedFlash
                  ? <Check size={12} className="text-emerald-500" />
                  : null}
              {savedFlash ? s.saved : s.addHere}
            </button>
            <button type="button" onClick={() => void saveToCenter()}
                    disabled={centerSaving}
                    className="ml-auto inline-flex h-9 cursor-pointer items-center gap-1.5 rounded-full border border-border px-3 text-xs text-fg-secondary transition-colors hover:border-accent/50 hover:text-fg disabled:opacity-50">
              {centerSaving
                ? <Loader2 size={12} className="animate-spin" />
                : <NotebookPen size={12} />}
              {centerNoteId ? s.savedToCenter : s.saveToCenter}
            </button>
          </div>
        </>
      )}
      {annotations.length > 0 ? (
        <ul className="space-y-1.5">
          {annotations.map((a) => (
            <li key={a.annotation_id}
                className="rounded-[8px] border border-border/70 bg-bg px-2.5 py-1.5 text-[0.72rem] leading-relaxed">
              {a.user_text && <p className="text-fg">{a.user_text}</p>}
              {a.auto_excerpt && (
                <p className="mt-0.5 truncate text-muted/80">
                  摘录：{a.auto_excerpt}
                </p>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-[0.7rem] text-muted/70">{s.empty}</p>
      )}
    </section>
  );
}
