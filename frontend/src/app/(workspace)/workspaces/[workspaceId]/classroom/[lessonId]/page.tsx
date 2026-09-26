"use client";
/* 课程详情/预览路由（plan.md §3.1 / §4.3；E01 骨架，E04 完整编辑器）。
 * ?revision=N 固定预览版本；缺省最新已发布版本。预览绝不创建 run、
 * 不触发 TTS。E01 展示已发布 revision 的首帧预览与生成中的任务状态；
 * 左缩略图/右侧讲稿栏与单页操作在 E04 落地。
 */
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import {
  ArrowLeft, FileQuestion, Loader2, Play, Presentation,
} from "lucide-react";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { ClassroomApiError, getJobPreview, getLesson, getRevisionFrame } from "@/lib/api-classroom";
import type { JobPreviewResponse, LessonDetailPublic } from "@/lib/types-classroom.generated";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { WorkspaceModeBar } from "@/components/classroom/WorkspaceModeBar";
import { GenerationProgress } from "@/components/classroom/GenerationProgress";
import { LessonEditor, ExportButtons } from "@/components/classroom/LessonEditor";
import SlideFrame from "@/components/classroom/SlideFrame";
import { startLessonRun } from "@/lib/classroom/useClassroomPlayer";
import { STRINGS } from "../strings";

function LessonDetailInner() {
  const { lang } = useUIStore();
  const tr = makePageT(lang, STRINGS);
  const router = useRouter();
  const params = useParams<{ workspaceId: string; lessonId: string }>();
  const searchParams = useSearchParams();
  const safeDecode = (s: string) => {
    try { return decodeURIComponent(s); } catch { return s; }
  };
  const workspaceId = safeDecode(params.workspaceId ?? "");
  const lessonId = safeDecode(params.lessonId ?? "");
  const revParam = searchParams.get("revision");
  const fixedRevision = revParam && /^\d+$/.test(revParam) ? Number(revParam) : undefined;

  const [detail, setDetail] = useState<LessonDetailPublic | null>(null);
  const [frameHtml, setFrameHtml] = useState<string | null>(null);
  const [draft, setDraft] = useState<JobPreviewResponse | null>(null);
  const [draftOpen, setDraftOpen] = useState(false);
  const [missing, setMissing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);

  const learnHref = useCallback((runId: string) =>
    `/workspaces/${encodeURIComponent(workspaceId)}` +
    `/classroom/${encodeURIComponent(lessonId)}` +
    `/learn/${encodeURIComponent(runId)}`,
  [workspaceId, lessonId]);

  // 开始/继续上课（§21.1）：预览本身不建 run（§3.1），点击才是用户手势
  const onStart = useCallback(async () => {
    if (starting) return;
    setStarting(true);
    try {
      const run = await startLessonRun(workspaceId, lessonId);
      router.push(learnHref(run));
    } catch {
      setStarting(false);
    }
  }, [starting, workspaceId, lessonId, learnHref, router]);

  const load = useCallback((explicitRevision?: number) => {
    setLoading(true);
    setMissing(false);
    getLesson(workspaceId, lessonId, explicitRevision ?? fixedRevision)
      .then((d) => {
        setDetail(d);
        const rev = d.revision?.revision;
        if (rev != null) {
          return getRevisionFrame(workspaceId, lessonId, rev)
            .then(setFrameHtml)
            .catch(() => setFrameHtml(null));
        }
        setFrameHtml(null);
        return undefined;
      })
      .catch((err) => {
        if (err instanceof ClassroomApiError &&
            (err.code === "source_not_found" || err.code === "classroom_disabled")) {
          setMissing(true);
        } else {
          setMissing(true);
        }
      })
      .finally(() => setLoading(false));
  }, [workspaceId, lessonId, fixedRevision]);

  // 微任务延迟首载（setState 不在 effect 体内同步触发；后台标签页 rAF 不可用）。
  useEffect(() => {
    const id = setTimeout(() => load(), 0);
    return () => clearTimeout(id);
  }, [load]);

  const backHref = `/workspaces/${encodeURIComponent(workspaceId)}/classroom`;

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-muted">
        <Loader2 size={18} className="animate-spin" />
      </div>
    );
  }

  if (missing || !detail) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <EmptyState
          icon={<FileQuestion size={28} />}
          title={tr("cls.detail.notfound")}
          action={<Link href={backHref}><Button variant="outline" icon={<ArrowLeft size={14} />}>{tr("cls.back.to.list")}</Button></Link>}
        />
      </div>
    );
  }

  const generating = detail.revision == null;
  // 未结束 run（§12.1 active/paused）直接续播；结束后“开始上课”开新 run
  const resumable = detail.recent_run != null
    && (detail.recent_run.status === "active"
        || detail.recent_run.status === "paused")
    ? detail.recent_run : null;

  return (
    <div className="flex h-full flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-border px-5 py-3">
        <Link
          href={backHref}
          className="flex h-8 w-8 items-center justify-center rounded-[8px] text-muted transition-colors hover:bg-surface-hover hover:text-fg"
          aria-label={tr("cls.back.to.list")}
        >
          <ArrowLeft size={16} />
        </Link>
        <h1 className="min-w-0 truncate font-serif text-[1.05rem] font-bold tracking-tight text-fg">
          {detail.title}
        </h1>
        <div className="ml-auto flex items-center gap-2">
          {!generating && detail.revision && (
            <Button
              size="sm"
              icon={starting
                ? <Loader2 size={14} className="animate-spin" />
                : <Play size={14} />}
              disabled={starting}
              onClick={() => {
                if (resumable) router.push(learnHref(resumable.run_id));
                else void onStart();
              }}
              title={starting ? tr("cls.detail.starting") : undefined}
            >
              {resumable
                ? tr("cls.detail.continue")
                : tr("cls.detail.start")}
            </Button>
          )}
          {!generating && detail.revision && (
            <span className="tnum hidden rounded-full border border-border px-2 py-0.5 text-[0.65rem] text-muted sm:inline">
              {tr("cls.card.revision").replace("%n", String(detail.revision.revision))}
            </span>
          )}
          {!generating && detail.revision && (
            <ExportButtons
              workspaceId={workspaceId}
              lessonId={lessonId}
              revision={detail.revision.revision}
              tr={tr}
            />
          )}
          <WorkspaceModeBar workspaceId={workspaceId} mode="classroom" />
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-5">
        {generating ? (
          <div className="mx-auto flex max-w-2xl flex-col gap-4">
            <h2 className="text-[0.95rem] font-semibold text-fg">
              {tr("cls.detail.noready.title")}
            </h2>
            {detail.latest_job && (
              <GenerationProgress
                workspaceId={workspaceId}
                lessonId={lessonId}
                job={detail.latest_job}
                onUpdated={(job) => {
                  // 终态（成功/失败/取消）后重载课程详情：发布版本或草稿状态
                  if (["succeeded", "failed", "cancelled"].includes(job.state)) {
                    setDraft(null);
                    setDraftOpen(false);
                    load();
                  }
                }}
              />
            )}
            {!detail.latest_job && (
              <p className="text-[0.78rem] text-muted">{tr("cls.detail.noready.desc")}</p>
            )}
            {/* 失败/取消后：已生成草稿页的只读预览（§4.2 草稿水印） */}
            {(detail.latest_job?.state === "failed"
              || detail.latest_job?.state === "cancelled") &&
              (detail.latest_job.progress.completed_slides ?? 0) > 0 && (
              <div>
                <button
                  type="button"
                  onClick={() => {
                    const next = !draftOpen;
                    setDraftOpen(next);
                    if (next && !draft) {
                      getJobPreview(workspaceId, lessonId,
                        detail.latest_job!.job_id)
                        .then(setDraft)
                        .catch(() => setDraft(null));
                    }
                  }}
                  className="cursor-pointer text-[0.75rem] font-medium text-accent-strong hover:underline"
                  aria-expanded={draftOpen}
                >
                  {tr("cls.detail.draft.open")}
                </button>
                {draftOpen && (
                  draft?.html ? (
                    <SlideFrame
                      html={draft.html}
                      title={detail.title}
                      className="mt-3 aspect-video w-full overflow-hidden rounded-[12px] border border-border bg-white shadow-md"
                    />
                  ) : draft ? (
                    <ol className="mt-3 flex flex-col gap-1 rounded-[10px] border border-border bg-surface p-3">
                      {(draft.slides ?? []).map((sl) => (
                        <li key={sl.slide_id} className="flex items-baseline gap-2 text-[0.75rem] text-fg-secondary">
                          <span className="tnum w-5 shrink-0 text-muted">{sl.order}</span>
                          <span className="truncate">{sl.title}</span>
                          <span className="ml-auto shrink-0 text-[0.65rem] text-muted/70">{sl.layout}</span>
                        </li>
                      ))}
                    </ol>
                  ) : (
                    <p className="mt-2 flex items-center gap-1.5 text-[0.72rem] text-muted">
                      <Loader2 size={12} className="animate-spin" />
                    </p>
                  )
                )}
              </div>
            )}
          </div>
        ) : frameHtml ? (
          <LessonEditor
            workspaceId={workspaceId}
            lessonId={lessonId}
            detail={detail}
            frameHtml={frameHtml}
            onReload={(rev) => load(rev)}
          />
        ) : (
          <EmptyState
            icon={<Presentation size={28} />}
            title={tr("cls.detail.preview")}
            desc={tr("cls.error.load")}
            action={<Button variant="outline" size="sm" onClick={() => load()}>{tr("cls.retry")}</Button>}
          />
        )}
      </div>
    </div>
  );
}

export default function LessonDetailPage() {
  return (
    <Suspense fallback={<div className="h-full w-full bg-bg" />}>
      <LessonDetailInner />
    </Suspense>
  );
}
