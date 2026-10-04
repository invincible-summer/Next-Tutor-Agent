"use client";
/* 工作区课堂列表（E02）。
 * 三态筛选（全部/可上课/生成中）+ Pager(5) + 最近更新排序；生成中的任务与
 * 课程同一张卡；失败卡显示具体阶段并可重试；空态双路径（教材章节 / 主题）。
 * ?create=1 或「一键备课」直接打开备课 Modal。ID 一律 encodeURIComponent。
 */
import { useAssistantPage } from "@/lib/assistant/useAssistantPage";
import { currentRouteEpoch } from "@/lib/assistant/page-context";
import { navigationSucceeded, navigationMissing, navigationFailed, navigationUnavailable } from "@/lib/assistant/navigation";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Suspense } from "react";
import {
  Archive, ArrowLeft, BookOpen, FileWarning, Loader2, Play,
  Presentation, RefreshCw, RotateCcw, Sparkles,
} from "lucide-react";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { cn } from "@/lib/cn";
import { getWorkspace } from "@/lib/api";
import {
  archiveLesson, ClassroomApiError, listLessons, retryJob,
} from "@/lib/api-classroom";
import type {
  LessonSummaryPublic, ResumeCardPublic,
} from "@next-tutor/contracts/classroom";
import { startLessonRun } from "@/lib/classroom/useClassroomPlayer";
import { lessonPath } from "@/lib/classroom/paths";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Pager } from "@/components/ui/Pager";
import { Modal } from "@/components/ui/Modal";
import { ClassroomBreadcrumb } from "@/components/classroom/Breadcrumb";
import { CreateLessonModal } from "@/components/classroom/CreateLessonModal";
import { LessonCard } from "@/components/classroom/LessonCard";
import { STRINGS } from "./strings";

const PAGE_SIZE = 5;
const FILTERS = [
  { value: "", key: "cls.filter.all" },
  { value: "ready", key: "cls.filter.ready" },
  { value: "generating", key: "cls.filter.generating" },
] as const;

function ClassroomListInner() {
  const { lang } = useUIStore();
  const tr = makePageT(lang, STRINGS);
  const router = useRouter();
  const params = useParams<{ workspaceId: string }>();
  // 动态段可能是仍编码的中文 slug：手动解码一次（与 chat 路由同模式），
  // 构造链接时再 encodeURIComponent，绝不二次解码。
  const safeDecode = (s: string) => {
    try { return decodeURIComponent(s); } catch { return s; }
  };
  const workspaceId = safeDecode(params.workspaceId ?? "");

  const [loadedWorkspace, setLoadedWorkspace] = useState("");
  const [wsName, setWsName] = useState("");
  const [lessons, setLessons] = useState<LessonSummaryPublic[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [wsMissing, setWsMissing] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [createTopic, setCreateTopic] = useState<string | undefined>(undefined);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [archiveTarget, setArchiveTarget] = useState<LessonSummaryPublic | null>(null);
  const [archiving, setArchiving] = useState(false);
  const [actionError, setActionError] = useState("");
  const [archiveError, setArchiveError] = useState("");
  const [classroomDisabled, setClassroomDisabled] = useState(false);
  const [resume, setResume] = useState<ResumeCardPublic | null>(null);
  const [restarting, setRestarting] = useState(false);

  useAssistantPage({
    context: () => ({ schema_version: 1, route_id: "course", route_epoch: currentRouteEpoch(), workspace_id: workspaceId }),
    navigationStatus: (target) => {
      if (wsMissing) return navigationMissing;
      if (classroomDisabled) return navigationUnavailable;
      if (failed) return navigationFailed;
      if (loading || loadedWorkspace !== workspaceId) return null;
      return target.kind === "workspace_courses" && target.workspace_id === workspaceId ? navigationSucceeded : null;
    },
  });

  const refresh = useCallback(() => {
    setLoading(true);
    setFailed(false);
    listLessons(workspaceId, {
      page: page + 1, pageSize: PAGE_SIZE,
      status: filter || undefined,
    })
      .then((res) => {
        setLessons(res.items ?? []);
        setTotal(res.total ?? 0);
        setResume(res.resume ?? null);
        setClassroomDisabled(false);
      })
      .catch((err) => {
        if (err instanceof ClassroomApiError && err.code === "classroom_disabled") {
          setLessons([]);
          setTotal(0);
          setClassroomDisabled(true);
        } else {
          setFailed(true);
        }
      })
      .finally(() => setLoading(false));
  }, [workspaceId, page, filter]);

  useEffect(() => {
    if (!workspaceId) return;
    // 微任务延迟（setState 不在 effect 体内同步触发；与 Sidebar 同模式）。
    const id = setTimeout(() => refresh(), 0);
    return () => clearTimeout(id);
  }, [workspaceId, refresh]);

  useEffect(() => {
    if (!workspaceId) return;
    let alive = true;
    getWorkspace(workspaceId)
      .then((ws) => { if (alive) { setLoadedWorkspace(workspaceId); setWsName(ws.name); setWsMissing(false); } })
      .catch(() => { if (alive) setWsMissing(true); });
    return () => { alive = false; };
  }, [workspaceId]);

  // ?create=1：进入页面即打开备课 Modal（工作区菜单「一键备课」深链）。
  // 微任务延迟开窗（setState 不在 effect 体内同步触发）。
  useEffect(() => {
    if (!workspaceId) return;
    if (window.location.search.includes("create=1")) {
      window.history.replaceState(null, "", window.location.pathname);
      const id = setTimeout(() => setCreateOpen(true), 0);
      return () => clearTimeout(id);
    }
  }, [workspaceId]);

  const onRetry = async (lesson: LessonSummaryPublic) => {
    const job = lesson.latest_job;
    if (!job || retryingId) return;
    setRetryingId(lesson.lesson_id);
    setActionError("");
    try {
      await retryJob(workspaceId, lesson.lesson_id, job.job_id, {
        expected_state_revision: job.state_revision,
      });
    } catch (error) {
      setActionError(error instanceof Error ? error.message : tr("cls.action.error"));
    }
    finally {
      setRetryingId(null);
      refresh();
    }
  };

  const onArchive = async () => {
    if (!archiveTarget || archiving) return;
    setArchiving(true);
    setArchiveError("");
    try {
      await archiveLesson(workspaceId, archiveTarget.lesson_id);
      const archivedId = archiveTarget.lesson_id;
      setArchiveTarget(null);
      setLessons((current) => current.filter((item) => item.lesson_id !== archivedId));
      setResume((current) => current?.lesson_id === archivedId ? null : current);
      const remaining = Math.max(0, total - 1);
      setTotal(remaining);
      if (page > 0 && page * PAGE_SIZE >= remaining) setPage(page - 1);
      else refresh();
    } catch (error) {
      setArchiveError(error instanceof Error ? error.message : tr("cls.action.error"));
    } finally {
      setArchiving(false);
    }
  };

  const onCreated = (lessonId: string) => {
    router.push(lessonPath(workspaceId, lessonId));
  };

  const learnHrefOf = (lessonId: string, runId: string) =>
    `${lessonPath(workspaceId, lessonId)}/learn/${encodeURIComponent(runId)}`;

  // 从头开始：显式 restart 开新 run（旧 run 标 ended，复用课件内容）
  const onRestart = async () => {
    if (!resume || restarting) return;
    setRestarting(true);
    try {
      const runId = await startLessonRun(workspaceId, resume.lesson_id,
                                         "restart");
      router.push(learnHrefOf(resume.lesson_id, runId));
    } catch {
      setRestarting(false);
    }
  };

  if (wsMissing) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <EmptyState
          icon={<FileWarning size={28} />}
          title={tr("ws.notfound.title")}
          desc={tr("ws.notfound.desc")}
          action={<Link href="/chat"><Button variant="outline" icon={<ArrowLeft size={14} />}>{tr("cls.back.to.list")}</Button></Link>}
        />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* 顶部：返回 + 标题 + 面包屑 + 主操作 */}
      <header className="flex flex-wrap items-center gap-3 border-b border-border bg-surface px-4 py-4 sm:px-7">
        <Link
          href="/chat"
          className="flex h-8 w-8 items-center justify-center rounded-[8px] text-muted transition-colors hover:bg-surface-hover hover:text-fg"
          aria-label="back to chat"
        >
          <ArrowLeft size={16} />
        </Link>
        <div className="min-w-0">
          <h1 className="truncate font-serif text-[1.3rem] font-bold tracking-tight text-fg">
            {wsName ? `${wsName} · ` : ""}{tr("cls.list.title.suffix")}
          </h1>
          <p className="hidden text-[0.68rem] text-muted sm:block">{tr("cls.list.subtitle")}</p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <ClassroomBreadcrumb
            current={`${wsName ? `${wsName} · ` : ""}${tr("cls.list.title.suffix")}`}
            className="mr-1 hidden md:flex"
          />
          <Link href="/archive" className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-border px-3 text-xs text-fg-secondary hover:bg-surface-hover">
            <Archive size={13} />{tr("cls.archive.center")}
          </Link>
          <Button demoWrite
            icon={<Sparkles size={14} />}
            onClick={() => { setCreateTopic(undefined); setCreateOpen(true); }}
          >
            {tr("cls.create")}
          </Button>
        </div>
      </header>

      {/* 三态筛选 */}
      {actionError && <p role="alert" className="mx-4 mt-3 rounded-lg border border-danger/30 bg-danger/5 px-3 py-2 text-xs text-danger sm:mx-7">{actionError}</p>}
      <div className="flex items-center gap-2 px-4 py-4 sm:px-7">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            type="button"
            onClick={() => { setFilter(f.value); setPage(0); }}
            aria-pressed={filter === f.value}
            className={cn(
              "min-h-10 cursor-pointer rounded-full border px-4 py-2 text-xs font-medium transition-colors",
              filter === f.value
                ? "border-accent bg-accent-soft/40 text-accent-strong"
                : "border-border text-muted hover:border-accent/40 hover:text-fg-secondary",
            )}
          >
            {tr(f.key)}
          </button>
        ))}
        <span className="tnum ml-auto text-[0.68rem] text-muted/70">{total}</span>
      </div>

      {/* 课程卡列表 */}
      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-6 sm:px-7">
        {loading ? (
          <div className="flex items-center justify-center gap-2 py-16 text-muted">
            <Loader2 size={16} className="animate-spin" />
          </div>
        ) : failed ? (
          <EmptyState
            icon={<FileWarning size={28} />}
            title={tr("cls.error.load")}
            action={<Button variant="outline" size="sm" icon={<RefreshCw size={13} />} onClick={refresh}>{tr("cls.retry")}</Button>}
          />
        ) : classroomDisabled ? (
          <div className="py-6">
            <EmptyState
              icon={<Presentation size={28} />}
              title={tr("cls.disabled.title")}
              desc={tr("cls.disabled.desc")}
            />
          </div>
        ) : lessons.length === 0 ? (
          <div className="py-6">
            <EmptyState
              icon={<Presentation size={28} />}
              title={filter ? tr("cls.empty.filtered") : tr("cls.empty.title")}
              desc={filter ? undefined : tr("cls.empty.desc")}
              action={!filter && !classroomDisabled ? (
                <div className="mt-1 flex flex-wrap items-center justify-center gap-2">
                  <Button
                    size="sm"
                    icon={<BookOpen size={13} />}
                    onClick={() => { setCreateTopic(undefined); setCreateOpen(true); }}
                  >
                    {tr("cls.empty.textbook")}
                  </Button>
                  <Button demoWrite
                    size="sm"
                    variant="outline"
                    icon={<Sparkles size={13} />}
                    onClick={() => { setCreateTopic(""); setCreateOpen(true); }}
                  >
                    {tr("cls.empty.topic")}
                  </Button>
                </div>
              ) : undefined}
            />
            {!filter && !classroomDisabled && (
              <p className="mt-1 text-center text-[0.68rem] text-muted/70">
                {tr("cls.empty.general.hint")}
              </p>
            )}
          </div>
        ) : (
          <div className="course-card-grid mx-auto max-w-6xl">
            {/* §3.3 置顶“继续上课”卡（未完成 run；从头开始开新 run） */}
            {resume && (
              <div className="flex flex-wrap items-center gap-3 rounded-[18px] border border-accent/35 bg-accent-soft/30 p-5 shadow-sm col-span-full">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] bg-accent text-on-accent">
                  <Play size={16} />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[0.9rem] font-semibold text-fg">
                    {tr("cls.list.resume")} · {resume.title}
                  </p>
                  {resume.slide_count ? (
                    <p className="tnum mt-0.5 text-[0.7rem] text-muted">
                      {tr("cls.list.resume.page")
                        .replace("%n", String(resume.run.cursor_slide_order ?? 1))
                        .replace("%t", String(resume.slide_count))}
                    </p>
                  ) : null}
                </div>
                <div className="flex items-center gap-2">
                  <Button size="sm"
                          onClick={() =>
                            router.push(learnHrefOf(resume.lesson_id,
                                                    resume.run.run_id))}>
                    {tr("cls.list.resume")}
                  </Button>
                  <Button demoWrite size="sm" variant="outline" disabled={restarting}
                          icon={restarting
                            ? <Loader2 size={13} className="animate-spin" />
                            : <RotateCcw size={13} />}
                          onClick={() => void onRestart()}>
                    {tr("cls.list.resume.restart")}
                  </Button>
                </div>
              </div>
            )}
            {lessons.map((lesson) => (
              <LessonCard
                key={lesson.lesson_id}
                wsId={workspaceId}
                lesson={lesson}
                tr={tr}
                onRetry={(l) => void onRetry(l)}
                retrying={retryingId === lesson.lesson_id}
                onArchive={(l) => { setArchiveError(""); setArchiveTarget(l); }}
              />
            ))}
            <Pager
              page={page}
              total={total}
              per={PAGE_SIZE}
              onPage={setPage}
              className="mx-auto w-full md:col-span-2"
            />
          </div>
        )}
      </div>

      {/* 一键备课 Modal（挂载式：每次打开全新状态） */}
      {createOpen && (
        <CreateLessonModal
          open
          workspaceId={workspaceId}
          workspaceName={wsName || undefined}
          initialTopic={createTopic || undefined}
          onClose={() => setCreateOpen(false)}
          onCreated={onCreated}
        />
      )}
      <Modal open={archiveTarget !== null} onClose={() => { if (!archiving) setArchiveTarget(null); }}
        title={tr("cls.archive.title")}
        footer={<>
          <Button variant="ghost" size="sm" disabled={archiving} onClick={() => setArchiveTarget(null)}>{tr("cls.archive.cancel")}</Button>
          <Button variant="danger" size="sm" disabled={archiving}
            icon={archiving ? <Loader2 size={13} className="animate-spin" /> : <Archive size={13} />}
            onClick={() => void onArchive()}>{tr("cls.card.archive")}</Button>
        </>}>
        <p className="break-words">{tr("cls.archive.desc").replace("%s", archiveTarget?.title ?? "")}</p>
        {archiveError && <p role="alert" className="mt-3 text-xs text-danger">{archiveError}</p>}
      </Modal>
    </div>
  );
}

export default function ClassroomListPage() {
  return (
    <Suspense fallback={<div className="h-full w-full bg-bg" />}>
      <ClassroomListInner />
    </Suspense>
  );
}
