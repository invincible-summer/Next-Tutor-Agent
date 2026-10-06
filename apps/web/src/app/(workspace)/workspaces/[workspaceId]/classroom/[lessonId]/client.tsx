"use client";
import { navigationSucceeded, navigationMissing, navigationFailed } from "@/lib/assistant/navigation";

/* 默认课程介绍；?edit=1 按需加载编辑器；?revision=N 固定预览版本。 */
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import {
  Archive, ArrowLeft, FileQuestion, Loader2, Play, Presentation,
} from "lucide-react";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { archiveLesson, ClassroomApiError, getJobPreview, getLesson, getRevisionFrame } from "@/lib/api-classroom";
import type { JobPreviewResponse, LessonDetailPublic } from "@next-tutor/contracts/classroom";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Modal } from "@/components/ui/Modal";
import { ClassroomBreadcrumb } from "@/components/classroom/Breadcrumb";
import { GenerationProgress } from "@/components/classroom/GenerationProgress";
import { LessonEditor, ExportButtons } from "@/components/classroom/LessonEditor";
import { LessonOverview } from "@/components/classroom/LessonOverview";
import SlideFrame from "@/components/classroom/SlideFrame";
import { startLessonRun } from "@/lib/classroom/useClassroomPlayer";
import { useAssistantPage } from "@/lib/assistant/useAssistantPage";
import { currentRouteEpoch } from "@/lib/assistant/page-context";
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
  const editing = searchParams.get("edit") === "1";
  const revParam = searchParams.get("revision");
  const fixedRevision = revParam && /^\d+$/.test(revParam) ? Number(revParam) : undefined;

  const [detail, setDetail] = useState<LessonDetailPublic | null>(null);
  const [frameIdentity, setFrameIdentity] = useState("");
  const [frameHtml, setFrameHtml] = useState<string | null>(null);
  const [frameFailed, setFrameFailed] = useState(false);
  const [draft, setDraft] = useState<JobPreviewResponse | null>(null);
  const [draftOpen, setDraftOpen] = useState(false);
  const [missing, setMissing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [archiveError, setArchiveError] = useState("");
  // 课件编辑器未保存信号（§5.4 导航保护：组件/讲稿表单修改未点保存）。
  const [editorDirty, setEditorDirty] = useState(false);

  // §20.2 页面适配器：课程介绍/编辑视图上下文 + 未保存保护。保存动作在
  // 编辑器子表单内（按组件/按页提交），页面只能真实提供「放弃并前往/
  // 留在此页」，不假装已自动保存。
  useAssistantPage({
    navigationStatus: (target) => {
      if (loading) return null;
      if (missing || !detail) return navigationMissing;
      if (target.kind !== "lesson" || target.lesson_id !== detail.lesson_id || target.workspace_id !== detail.workspace_id) return null;
      if ((target.view === "edit") !== editing) return null;
      if (target.revision && detail.revision?.revision !== target.revision) return null;
      if (editing && (!frameHtml || frameIdentity !== `${workspaceId}:${lessonId}:${detail.revision?.revision}`)) return frameFailed ? navigationFailed : null;
      return navigationSucceeded;
    },
    context: () => ({
      schema_version: 1,
      route_id: "course" as const,
      route_epoch: currentRouteEpoch(),
      workspace_id: workspaceId || undefined,
      entity: lessonId ? {
        kind: "lesson" as const,
        id: lessonId,
        revision: detail?.revision
          ? String(detail.revision.revision) : undefined,
      } : undefined,
      view: editing ? "edit" : "overview",
    }),
    clientState: () => ({
      dirty: editorDirty,
      blocking_activity: editorDirty
        ? ("unsaved_editor" as const) : ("none" as const),
      activity_label: detail?.title,
      safe_bottom_px: 24,
    }),
    beforeNavigate: async () => {
      if (!editorDirty) return "allow";
      if (window.confirm(STRINGS[lang]["cls.edit.unsaved"])) return "allow";
      return "stay";
    },
  });

  const learnHref = useCallback((runId: string) =>
    `/workspaces/${encodeURIComponent(workspaceId)}` +
    `/classroom/${encodeURIComponent(lessonId)}` +
    `/learn/${encodeURIComponent(runId)}`,
  [workspaceId, lessonId]);

  // 开始/继续上课（§21.1）：预览本身不建 run（§3.1），点击才是用户手势
  const onStart = async () => {
    if (starting) return;
    setStarting(true);
    try {
      const run = await startLessonRun(workspaceId, lessonId, "resume_or_create", detail?.revision?.revision);
      router.push(learnHref(run));
    } catch (error) {
      setArchiveError(error instanceof Error ? error.message : tr("cls.action.error"));
      setStarting(false);
    }
  };

  const load = useCallback((explicitRevision?: number, background = false) => {
    if (!background) setLoading(true);
    setMissing(false);
    getLesson(workspaceId, lessonId, explicitRevision ?? fixedRevision)
      .then((d) => {
        setDetail(d);

      })
      .catch((err) => {
        if (background) return;
        if (err instanceof ClassroomApiError &&
            (err.code === "source_not_found" || err.code === "classroom_disabled")) {
          setMissing(true);
        } else {
          setMissing(true);
        }
      })
      .finally(() => { if (!background) setLoading(false); });
  }, [workspaceId, lessonId, fixedRevision]);

  // 微任务延迟首载（setState 不在 effect 体内同步触发；后台标签页 rAF 不可用）。
  useEffect(() => {
    const id = setTimeout(() => load(), 0);
    return () => clearTimeout(id);
  }, [load]);

  useEffect(() => {
    if (!editing || !detail?.revision || detail.lesson_id !== lessonId || detail.workspace_id !== workspaceId) return;
    let active = true;
    getRevisionFrame(workspaceId, lessonId, detail.revision.revision)
      .then((html) => { if (active) { setFrameHtml(html); setFrameIdentity(`${workspaceId}:${lessonId}:${detail.revision?.revision}`); setFrameFailed(false); } })
      .catch(() => { if (active) { setFrameHtml(null); setFrameFailed(true); } });
    return () => { active = false; };
  }, [editing, detail, workspaceId, lessonId]);

  const setEditMode = (on: boolean) => {
    const query = new URLSearchParams(searchParams.toString());
    if (on) query.set("edit", "1"); else query.delete("edit");
    router.replace(`?${query.toString()}`);
  };

  const backHref = `/workspaces/${encodeURIComponent(workspaceId)}/classroom`;

  const onArchive = async () => {
    if (archiving) return;
    setArchiving(true);
    setArchiveError("");
    try {
      await archiveLesson(workspaceId, lessonId);
      setArchiveOpen(false);
      router.push(backHref);
    } catch (error) {
      setArchiveError(error instanceof Error ? error.message : tr("cls.action.error"));
    } finally {
      setArchiving(false);
    }
  };

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
    && detail.recent_run.lesson_revision === detail.revision?.revision
    && (detail.recent_run.status === "active"
        || detail.recent_run.status === "paused")
    ? detail.recent_run : null;

  return (
    <div className="lesson-detail flex h-full flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-border bg-surface px-4 py-3 sm:px-6">
        <Link
          href={backHref}
          className="flex h-8 w-8 items-center justify-center rounded-[8px] text-muted transition-colors hover:bg-surface-hover hover:text-fg"
          aria-label={tr("cls.back.to.list")}
        >
          <ArrowLeft size={16} />
        </Link>
        <div className="min-w-0 flex-1">
          <ClassroomBreadcrumb workspaceId={workspaceId} listLabel={tr("cls.list.title.suffix")} />
          <h1 className="mt-1 truncate text-lg font-semibold tracking-tight text-fg">{detail.title}</h1>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {editing && <Button size="sm" variant="outline" onClick={() => setEditMode(false)}>{lang === "en" ? "Course overview" : "课程预览"}</Button>}
          {editing && !generating && detail.revision && (
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
            <span className="tnum hidden rounded-full border border-border px-2 py-0.5 text-[0.6875rem] text-muted sm:inline">
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
          <Button size="sm" variant="outline" icon={<Archive size={13} />}
            onClick={() => { setArchiveError(""); setArchiveOpen(true); }}>
            {tr("cls.card.archive")}
          </Button>

        </div>
      </header>

      {archiveError && !archiveOpen && <p role="alert" className="px-6 py-2 text-sm text-danger">{archiveError}</p>}
      <div className={generating || !editing
        ? "min-h-0 flex-1 overflow-y-auto p-5"
        : "min-h-0 flex-1 overflow-hidden"}>
        {generating ? (
          <div className="mx-auto flex max-w-2xl flex-col gap-5 py-8">
            <h2 className="text-center text-xl font-semibold tracking-tight text-fg">
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
                    load(undefined, true);
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
                          <span className="ml-auto shrink-0 text-[0.6875rem] text-muted/70">{sl.layout}</span>
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
        ) : !editing ? (
          <LessonOverview detail={detail} starting={starting} resumable={Boolean(resumable)}
            onEdit={() => setEditMode(true)}
            onStart={() => { if (resumable) router.push(learnHref(resumable.run_id)); else void onStart(); }} />
        ) : frameHtml ? (
          <LessonEditor
            workspaceId={workspaceId}
            lessonId={lessonId}
            detail={detail}
            frameHtml={frameHtml}
            onReload={(rev) => load(rev, true)}
            onDirtyChange={setEditorDirty}
          />
        ) : !frameFailed ? (
          <div role="status" className="flex h-full items-center justify-center gap-2 text-sm text-muted"><Loader2 size={18} className="animate-spin" />{lang === "en" ? "Loading course…" : "正在加载课件…"}</div>
        ) : (
          <EmptyState
            icon={<Presentation size={28} />}
            title={tr("cls.detail.preview")}
            desc={tr("cls.error.load")}
            action={<Button variant="outline" size="sm" onClick={() => load()}>{tr("cls.retry")}</Button>}
          />
        )}
      </div>
      <Modal open={archiveOpen} onClose={() => { if (!archiving) setArchiveOpen(false); }}
        title={tr("cls.archive.title")}
        footer={<>
          <Button variant="ghost" size="sm" disabled={archiving} onClick={() => setArchiveOpen(false)}>{tr("cls.archive.cancel")}</Button>
          <Button variant="danger" size="sm" disabled={archiving}
            icon={archiving ? <Loader2 size={13} className="animate-spin" /> : <Archive size={13} />}
            onClick={() => void onArchive()}>{tr("cls.card.archive")}</Button>
        </>}>
        <p className="break-words">{tr("cls.archive.desc").replace("%s", detail.title)}</p>
        {archiveError && <p role="alert" className="mt-3 text-xs text-danger">{archiveError}</p>}
      </Modal>
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
