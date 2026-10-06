"use client";
/* 课程卡（课堂列表页与 /course Hub 共用；自列表页抽取，标记保持不变）。
 * 主题色横幅 + 状态 chip + 生成中阶段进度 + 失败原因；onRetry/onArchive
 * 缺省时对应操作不渲染（Hub 等轻量场景）。词条由调用方 tr 提供（cls.*）。 */
import { useMemo } from "react";
import Link from "next/link";
import {
  Archive, BookOpen, ChevronRight, FileWarning, Loader2, Presentation,
  RotateCcw,
} from "lucide-react";
import { cn } from "@/lib/cn";
import { useUIStore } from "@/lib/store";
import { localeFor } from "@/lib/i18n";
import type { LessonSummaryPublic } from "@next-tutor/contracts/classroom";
import { classroomPath } from "@/lib/classroom/paths";
import { cardPaletteFor } from "./theme-palette";

function statusLabel(tr: (k: string, fb?: string) => string, status: string) {
  return tr(`cls.status.${status}`, status);
}

function statusCls(status: string) {
  switch (status) {
    case "generating":
      return "border-accent/30 bg-accent-soft/40 text-accent-strong";
    case "ready":
      return "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400";
    case "needs_attention":
      return "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400";
    case "failed":
      return "border-danger/30 bg-danger/10 text-danger";
    default:
      return "border-border bg-surface-hover text-muted";
  }
}

function phaseLabel(tr: (k: string, fb?: string) => string,
  phase: string | null | undefined) {
  if (!phase) return "";
  return tr(`cls.phase.${phase}`, phase);
}

export function LessonCard({ wsId, lesson, tr, hrefBase, onRetry, retrying,
  onArchive }: {
  wsId: string;
  lesson: LessonSummaryPublic;
  tr: (k: string, fb?: string) => string;
  /** 详情链接基址（不含 lessonId），默认该辅导区的课堂列表路径。 */
  hrefBase?: string;
  onRetry?: (lesson: LessonSummaryPublic) => void;
  retrying?: boolean;
  onArchive?: (lesson: LessonSummaryPublic) => void;
}) {
  const lang = useUIStore((s) => s.lang);
  const href = `${hrefBase ?? classroomPath(wsId)}/${encodeURIComponent(lesson.lesson_id)}`;
  const updated = useMemo(() => {
    const d = new Date(lesson.updated_at);
    return Number.isNaN(d.getTime()) ? "" :
      d.toLocaleString(localeFor(lang), { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }, [lesson.updated_at, lang]);
  const generating = lesson.status === "generating";
  const failed = lesson.status === "failed";
  const job = lesson.latest_job;
  const progress = job?.progress;
  const palette = cardPaletteFor(lesson.brief?.theme_id);

  return (
    <div className={cn(
      "lesson-card group min-w-0 overflow-hidden rounded-2xl border bg-surface transition-all",
      failed ? "border-danger/30" : "border-border hover:border-accent/40 hover:shadow",
    )}>
      <Link href={href} aria-hidden="true" tabIndex={-1}
            className="lesson-cover relative block overflow-hidden p-6"
            style={{ background: palette.bg, color: palette.text }}>
        <div className="flex items-center justify-between">
          <span className="text-[9px] font-semibold tracking-[.2em] opacity-50">LESSON / {String(lesson.extra?.slide_count ?? '—').padStart(2, '0')}</span>
          <Presentation size={15} className="opacity-45" />
        </div>
        <span className="relative z-10 mt-5 line-clamp-2 max-w-[85%] text-xl font-semibold leading-snug tracking-tight">{lesson.title}</span>
        <span className="mt-4 block h-0.5 w-8" style={{ background: palette.accent }} />
        <span className="lesson-cover-orbit" style={{ borderColor: palette.accent }} />
        <span className="lesson-cover-orbit lesson-cover-orbit-small" style={{ borderColor: palette.accent }} />
        <span className="absolute bottom-4 right-5 text-[10px] tracking-widest opacity-40">{lesson.brief?.duration_minutes ?? '—'} MIN</span>
      </Link>
      <div className="p-4">
        <div className="mb-3 flex items-center justify-between gap-2">
          <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2 py-1 text-[10px] font-medium", statusCls(lesson.status))}>
            {generating ? <Loader2 size={10} className="animate-spin" /> : failed ? <FileWarning size={10} /> : <span className="h-1 w-1 rounded-full bg-current" />}
            {statusLabel(tr, lesson.status)}
          </span>
          {updated && <span className="tnum text-[10px] text-muted">{updated}</span>}
        </div>
        <Link href={href} className="line-clamp-2 text-sm font-semibold leading-6 text-fg transition-colors hover:text-accent-strong">{lesson.title}</Link>
        <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted">
          {lesson.brief && <span>{tr("cls.card.minutes").replace("%n", String(lesson.brief.duration_minutes))}</span>}
          {(lesson.extra?.slide_count ?? 0) > 0 && <span>{tr("cls.card.pages").replace("%n", String(lesson.extra?.slide_count))}</span>}
          {lesson.extra?.chapter_label && <span className="inline-flex min-w-0 items-center gap-1"><BookOpen size={11} /><span className="truncate">{lesson.extra.chapter_label}</span></span>}
        </p>
          {/* 生成中：阶段 + 页数进度；失败：具体阶段 + 重试（§3.3） */}
          {generating && (
            <p className="mt-3 flex items-center gap-1.5 text-xs text-accent-strong/90">
              <Loader2 size={11} className="animate-spin" />
              {phaseLabel(tr, job?.phase)}
              {progress?.total_slides
                ? ` · ${progress.completed_slides ?? 0}/${progress.total_slides}`
                : ""}
            </p>
          )}
          {failed && (
            <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[0.7rem] text-danger">
              <span>
                {phaseLabel(tr, job?.phase) || tr("cls.status.failed")}
                {job?.last_error ? ` · ${job.last_error.slice(0, 80)}` : ""}
              </span>
              {job?.next_actions?.includes("retry") && onRetry && (
                <button
                  type="button"
                  disabled={retrying}
                  onClick={() => onRetry(lesson)}
                  className="inline-flex cursor-pointer items-center gap-1 rounded-full border border-danger/40 px-2 py-0.5 text-[0.6875rem] transition-colors hover:bg-danger/10 disabled:opacity-50"
                >
                  {retrying
                    ? <Loader2 size={10} className="animate-spin" />
                    : <RotateCcw size={10} />}
                  {tr("cls.card.retry")}
                </button>
              )}
            </div>
          )}
        <div className="mt-4 flex items-center justify-between gap-2 border-t border-border-light pt-3">
          <Link href={href} className="inline-flex min-h-8 items-center gap-1.5 text-xs font-medium text-accent-strong">{tr("cls.card.open")}<ChevronRight size={13} /></Link>
          {onArchive && <button type="button" onClick={() => onArchive(lesson)} className="flex h-8 w-8 items-center justify-center rounded-lg text-muted hover:bg-surface-hover hover:text-fg" title={tr("cls.card.archive")} aria-label={`${tr("cls.card.archive")}：${lesson.title}`}><Archive size={14} /></button>}
        </div>
      </div>
    </div>
  );
}
