"use client";
import { BookOpen, Check, Clock3, Layers3, Pencil, Play, Loader2 } from "lucide-react";
import type { LessonDetailPublic } from "@next-tutor/contracts/classroom";
import { useUIStore } from "@/lib/store";
import { Button } from "@/components/ui/Button";

export function LessonOverview({ detail, onEdit, onStart, starting, resumable }: {
  detail: LessonDetailPublic; onEdit: () => void; onStart: () => void;
  starting: boolean; resumable: boolean;
}) {
  const { lang } = useUIStore();
  const en = lang === "en";
  const revision = detail.revision!;
  const goals = revision.objectives?.map((o) => o.text).filter(Boolean) ?? [];
  const minutes = revision.brief?.duration_minutes ?? Math.max(1,
    Math.round(revision.slides.reduce((n, slide) => n + (slide.estimated_seconds ?? 0), 0) / 60));
  return (
    <div className="lesson-overview mx-auto w-full max-w-5xl px-8 py-10">
      <section className="lesson-intro relative overflow-hidden rounded-3xl border border-border bg-surface p-9">
        <div className="pointer-events-none absolute -right-12 -top-16 h-72 w-72 rounded-full border-[40px] border-accent/5" />
        <div className="relative max-w-2xl">
          <p className="mb-5 flex items-center gap-2 text-xs font-medium tracking-widest text-accent-strong"><BookOpen size={15} />{en ? "COURSE OVERVIEW" : "课程内容预览"}</p>
          <h2 className="text-3xl font-semibold leading-snug tracking-tight text-fg">{detail.title}</h2>
          <p className="mt-4 text-sm leading-7 text-fg-secondary">{en ? "Explore what you’ll learn, refine your course, or choose a page and start learning." : "先了解这节课的学习内容，也可以调整课件，再按自己的节奏开始上课。"}</p>
          <div className="mt-6 flex items-center gap-6 text-xs text-muted">
            <span className="flex items-center gap-2"><Clock3 size={15} />{minutes} {en ? "min" : "分钟"}</span>
            <span className="flex items-center gap-2"><Layers3 size={15} />{revision.slides.length} {en ? "slides" : "页课件"}</span>
            {revision.brief?.grade && <span>{revision.brief.grade}</span>}
          </div>
          <div className="mt-8 flex flex-wrap gap-3">
            <Button variant="outline" className="min-h-12 min-w-40 justify-center !rounded-xl" icon={<Pencil size={16} />} onClick={onEdit}>{en ? "Edit course" : "编辑课程"}</Button>
            <Button className="min-h-12 min-w-40 justify-center !rounded-xl" icon={starting ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />} disabled={starting} onClick={onStart}>{resumable ? (en ? "Continue class" : "继续上课") : (en ? "Start class" : "开始上课")}</Button>
          </div>
        </div>
      </section>
      <div className="mt-9 grid grid-cols-[1fr_1.15fr] gap-10">
        <section>
          <h3 className="mb-5 text-sm font-semibold text-fg">{en ? "What you’ll learn" : "这节课，你将学到"}</h3>
          <ul className="space-y-4">
            {(goals.length ? goals : revision.brief?.goals?.length ? revision.brief.goals : [detail.title]).map((goal, i) => <li key={i} className="flex gap-3 text-sm leading-7 text-fg-secondary"><span className="mt-1.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-accent-soft text-accent-strong"><Check size={12} /></span>{goal}</li>)}
          </ul>
        </section>
        <section>
          <h3 className="mb-3 text-sm font-semibold text-fg">{en ? "Course plan" : "课程内容安排"}</h3>
          <ol className="divide-y divide-border-light">
            {revision.slides.map((slide, i) => <li key={slide.slide_id} className="flex items-start gap-4 py-3.5 text-sm"><span className="tnum pt-0.5 text-xs text-muted">{String(i + 1).padStart(2, "0")}</span><span className="leading-6 text-fg-secondary">{slide.title}</span></li>)}
          </ol>
        </section>
      </div>
    </div>
  );
}
