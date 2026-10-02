"use client";
import { useEffect, useRef, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import Link from "next/link";
import { ChevronLeft, ChevronRight, BookOpen } from "lucide-react";
import { getLesson, getRevisionFrame, getRun, type RunPublicExtra } from "@/lib/api-classroom";
import { demoLessonRuns } from "@/lib/demo-fetch";
import { localeFor } from "@/lib/i18n";
import type { LessonDetailPublic } from "@/lib/types-classroom.generated";
import { useUIStore } from "@/lib/store";
import { Button } from "@/components/ui/Button";
import { ErrorNote, PageSkeleton } from "@/components/ui/EmptyState";
import { MiniMarkdown } from "@/components/chat/markdown";
import SlideFrame, { type SlideFrameHandle } from "./SlideFrame";

/** Browse the frozen course and saved run without acquiring a lease or writing progress. */
export function DemoLessonViewer() {
  const params = useParams<{ workspaceId: string; lessonId: string; runId?: string }>();
  const workspaceId = decodeURIComponent(params.workspaceId);
  const lessonId = params.lessonId;
  const runId = params.runId;
  const revision = Number(useSearchParams().get("revision")) || undefined;
  const lang = useUIStore((s) => s.lang);
  const en = lang === "en";
  const [detail, setDetail] = useState<LessonDetailPublic | null>(null);
  const [html, setHtml] = useState("");
  const [error, setError] = useState("");
  const [page, setPage] = useState(0);
  const [notes, setNotes] = useState<{ text: string }[]>([]);
  const [runs, setRuns] = useState<RunPublicExtra[]>([]);
  const frame = useRef<SlideFrameHandle>(null);
  useEffect(() => {
    let active = true;
    void (async () => {
      const run = runId ? await getRun(workspaceId, lessonId, runId) : null;
      const [loaded, runIds] = await Promise.all([
        getLesson(workspaceId, lessonId, run?.lesson_revision ?? revision),
        demoLessonRuns(workspaceId, lessonId),
      ]);
      const [content, history] = await Promise.all([
        loaded.revision ? getRevisionFrame(workspaceId, lessonId, loaded.revision.revision) : Promise.resolve(""),
        Promise.all(runIds.map((id) => getRun(workspaceId, lessonId, id))),
      ]);
      if (!active) return;
      setDetail(loaded); setHtml(content);
      setPage(Math.max(0, loaded.revision?.slides.findIndex((slide) => slide.slide_id === run?.cursor.slide_id) ?? 0));
      setRuns(history.sort((a, b) => b.created_at.localeCompare(a.created_at)));
      setNotes((run?.demo_annotations || []).map((note) => ({ text: note.user_text || note.auto_excerpt || "" })));
    })().catch((failure) => { if (active) setError(String(failure)); });
    return () => { active = false; };
  }, [workspaceId, lessonId, runId, revision]);
  if (error) return <div className="p-6"><ErrorNote message={error} /></div>;
  if (!detail) return <PageSkeleton />;
  const slides = detail.revision?.slides || [];
  const slide = slides[page];
  const selectedRun = runs.find((run) => run.run_id === runId);
  const lessonHref = `/workspaces/${encodeURIComponent(workspaceId)}/classroom/${lessonId}`;
  const statusText = (status: RunPublicExtra["status"]) => ({ active: en ? "In progress" : "学习中", paused: en ? "Paused" : "已暂停", completed: en ? "Completed" : "已完成", ended: en ? "Ended" : "已结束" })[status];
  const changePage = (next: number) => {
    if (!slides[next]) return;
    setPage(next);
    frame.current?.gotoPage(slides[next].order);
  };
  return <div className="flex h-full flex-col overflow-hidden bg-bg">
    <div className="flex shrink-0 items-center gap-3 border-b border-border px-5 py-3">
      <Link href={`/workspaces/${encodeURIComponent(workspaceId)}/classroom`} className="text-xs text-accent hover:underline">{en ? "Courses" : "课程列表"}</Link>
      <BookOpen size={17} className="text-accent" />
      <h1 className="min-w-0 flex-1 truncate font-serif text-lg">{detail.title}</h1>
    </div>
    <div className="flex min-h-0 flex-1">
      <aside className="w-52 shrink-0 overflow-y-auto border-r border-border p-3">
        {slides.map((item, index) => <button key={item.slide_id} onClick={() => changePage(index)} className={`mb-1 w-full rounded-lg px-3 py-2 text-left text-xs ${page === index ? "bg-accent-soft text-accent" : "text-fg-secondary hover:bg-surface-hover"}`}>{index + 1}. {item.title}</button>)}
        <h2 className="mb-2 mt-5 px-3 text-xs font-medium text-muted">{en ? "Published versions" : "课件历史版本"}</h2>
        <div className="flex flex-wrap gap-2 px-3">
          {detail.published_revisions.map((number) => <Link key={number} href={`${lessonHref}?revision=${number}`} aria-current={!runId && detail.revision?.revision === number ? "page" : undefined} className="rounded-md border border-border px-2 py-1 text-xs text-accent hover:bg-accent-soft">{en ? "Version" : "版本"} {number}</Link>)}
        </div>
        {runs.length > 0 && <><h2 className="mb-2 mt-5 px-3 text-xs font-medium text-muted">{en ? "Saved classroom sessions" : "历史上课记录"}</h2>{runs.map((run) => <Link key={run.run_id} href={`${lessonHref}/learn/${run.run_id}`} aria-current={run.run_id === runId ? "page" : undefined} className={`mb-1 block rounded-lg px-3 py-2 text-xs ${run.run_id === runId ? "bg-accent-soft text-accent" : "text-fg-secondary hover:bg-surface-hover"}`}>
          <span className="block">{new Date(run.created_at).toLocaleString(localeFor(lang), { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>
          <span className="mt-1 block text-muted">{statusText(run.status)} · {en ? "Version" : "版本"} {run.lesson_revision}</span>
        </Link>)}</>}
      </aside>
      <div className="flex min-w-0 flex-1 flex-col p-4">
        {html ? <SlideFrame ref={frame} html={html} title={detail.title} onReady={() => { if (slides[page]) frame.current?.gotoPage(slides[page].order); }} onPageSelected={(order) => {
          const index = slides.findIndex((item) => item.order === order);
          if (index >= 0) setPage(index);
        }} className="min-h-0 flex-1" /> : <p className="p-6 text-muted">{en ? "No published slides in this record." : "此课程记录尚无发布的课件。"}</p>}
        <div className="mt-3 flex items-center justify-center gap-4">
          <Button variant="ghost" disabled={page <= 0} onClick={() => changePage(page - 1)} icon={<ChevronLeft size={15} />}>{en ? "Previous" : "上一页"}</Button>
          <span className="tnum text-xs text-muted">{slides.length ? page + 1 : 0} / {slides.length}</span>
          <Button variant="ghost" disabled={page >= slides.length - 1} onClick={() => changePage(page + 1)} icon={<ChevronRight size={15} />}>{en ? "Next" : "下一页"}</Button>
        </div>
      </div>
      <aside className="w-80 shrink-0 overflow-y-auto border-l border-border p-5">
        {selectedRun && <div className="mb-5 rounded-lg border border-border p-3 text-xs text-fg-secondary">
          <p>{statusText(selectedRun.status)} · {en ? "Pages visited" : "已浏览页数"} {selectedRun.visited_slide_count} / {slides.length}</p>
          {selectedRun.qa_session_id && <Link href={`/chat/${encodeURIComponent(selectedRun.qa_session_id)}`} className="mt-2 block text-accent hover:underline">{en ? "View saved classroom Q&A" : "查看已保存的课堂答疑"}</Link>}
        </div>}
        <h2 className="mb-3 font-serif text-base">{en ? "Lecture script" : "课程讲稿"}</h2>
        <MiniMarkdown className="chat-prose classroom-prose">{slide?.segments.map((segment) => segment.spoken_text || segment.display_text).join("\n\n") || ""}</MiniMarkdown>
        {notes.length > 0 && <><h2 className="mb-3 mt-8 font-serif text-base">{en ? "Saved classroom notes" : "已保存的课堂笔记"}</h2>{notes.map((note, index) => <MiniMarkdown key={index} className="chat-prose classroom-prose">{note.text}</MiniMarkdown>)}</>}
      </aside>
    </div>
  </div>;
}
