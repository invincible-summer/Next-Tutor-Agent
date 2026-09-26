"use client";
/* 工作区课堂列表（plan.md §3.3 / E02）。
 * 三态筛选（全部/可上课/生成中）+ Pager(5) + 最近更新排序；生成中的任务与
 * 课程同一张卡；失败卡显示具体阶段并可重试；空态双路径（教材章节 / 主题）。
 * ?create=1 或「一键备课」直接打开备课 Modal。ID 一律 encodeURIComponent。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Suspense } from "react";
import {
  ArrowLeft, BookOpen, ChevronRight, FileWarning, Loader2, Play,
  Presentation, RefreshCw, RotateCcw, Sparkles,
} from "lucide-react";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { cn } from "@/lib/cn";
import { getWorkspace } from "@/lib/api";
import {
  ClassroomApiError, listLessons, retryJob,
} from "@/lib/api-classroom";
import type {
  LessonSummaryPublic, ResumeCardPublic,
} from "@/lib/types-classroom.generated";
import { startLessonRun } from "@/lib/classroom/useClassroomPlayer";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Pager } from "@/components/ui/Pager";
import { WorkspaceModeBar } from "@/components/classroom/WorkspaceModeBar";
import { CreateLessonModal } from "@/components/classroom/CreateLessonModal";
import { STRINGS } from "./strings";

const PAGE_SIZE = 5;
const PHASE_LABELS_ZH: Record<string, string> = {
  resolve_sources: "读取教材",
  research: "查找补充资料",
  outline: "组织课程",
  visual_assets: "配图",
  author_slides: "编写课件与讲稿",
  checkpoints: "随堂检查",
  review: "整体校验",
  render: "检查排版",
  publish: "准备开课",
};
const FILTERS = [
  { value: "", key: "cls.filter.all" },
  { value: "ready", key: "cls.filter.ready" },
  { value: "generating", key: "cls.filter.generating" },
] as const;

function lessonHref(workspaceId: string, lessonId: string): string {
  return `/workspaces/${encodeURIComponent(workspaceId)}` +
    `/classroom/${encodeURIComponent(lessonId)}`;
}

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

function phaseLabel(phase: string | null | undefined) {
  if (!phase) return "";
  return PHASE_LABELS_ZH[phase] ?? phase;
}

function LessonCard({ wsId, lesson, tr, onRetry, retrying }: {
  wsId: string;
  lesson: LessonSummaryPublic;
  tr: (k: string, fb?: string) => string;
  onRetry?: (lesson: LessonSummaryPublic) => void;
  retrying?: boolean;
}) {
  const updated = useMemo(() => {
    const d = new Date(lesson.updated_at);
    return Number.isNaN(d.getTime()) ? "" :
      d.toLocaleString(undefined, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }, [lesson.updated_at]);
  const generating = lesson.status === "generating";
  const failed = lesson.status === "failed";
  const job = lesson.latest_job;
  const progress = job?.progress;

  return (
    <div className={cn(
      "group rounded-[12px] border bg-surface p-4 shadow-sm transition-all",
      failed ? "border-danger/30" : "border-border hover:border-accent/40 hover:shadow",
    )}>
      <div className="flex items-start gap-3">
        <Link
          href={lessonHref(wsId, lesson.lesson_id)}
          className={cn(
            "flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px]",
            generating ? "bg-accent-soft text-accent-strong" : "bg-accent-soft/60 text-accent",
          )}
          aria-label={lesson.title}
        >
          {generating
            ? <Loader2 size={16} className="animate-spin" />
            : failed ? <FileWarning size={16} className="text-danger" />
            : <Presentation size={16} />}
        </Link>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Link
              href={lessonHref(wsId, lesson.lesson_id)}
              className="min-w-0 flex-1 truncate text-[0.9rem] font-semibold text-fg hover:text-accent-strong"
            >
              {lesson.title}
            </Link>
            <span className={cn(
              "shrink-0 rounded-full border px-2 py-0.5 text-[0.65rem] font-medium",
              statusCls(lesson.status),
            )}>
              {statusLabel(tr, lesson.status)}
            </span>
          </div>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[0.7rem] text-muted">
            {lesson.extra?.chapter_label && (
              <span className="inline-flex max-w-56 items-center gap-1 truncate">
                <BookOpen size={10} />{lesson.extra.chapter_label}
              </span>
            )}
            {lesson.brief && (
              <span className="tnum">
                {tr("cls.card.minutes").replace("%n", String(lesson.brief.duration_minutes))}
              </span>
            )}
            {(lesson.extra?.slide_count ?? 0) > 0 && (
              <span className="tnum">
                {tr("cls.card.pages").replace("%n", String(lesson.extra?.slide_count))}
              </span>
            )}
            {lesson.latest_ready_revision != null && (
              <span className="tnum">
                {tr("cls.card.revision").replace("%n", String(lesson.latest_ready_revision))}
              </span>
            )}
            {updated && <span className="tnum">{updated}</span>}
          </p>
          {/* 生成中：阶段 + 页数进度；失败：具体阶段 + 重试（§3.3） */}
          {generating && (
            <p className="mt-1.5 flex items-center gap-1.5 text-[0.7rem] text-accent-strong/90">
              <Loader2 size={11} className="animate-spin" />
              {phaseLabel(job?.phase)}
              {progress?.total_slides
                ? ` · ${progress.completed_slides ?? 0}/${progress.total_slides}`
                : ""}
            </p>
          )}
          {failed && (
            <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[0.7rem] text-danger">
              <span>
                {phaseLabel(job?.phase) || tr("cls.status.failed")}
                {job?.last_error ? ` · ${job.last_error.slice(0, 80)}` : ""}
              </span>
              {job && onRetry && (
                <button
                  type="button"
                  disabled={retrying}
                  onClick={() => onRetry(lesson)}
                  className="inline-flex cursor-pointer items-center gap-1 rounded-full border border-danger/40 px-2 py-0.5 text-[0.65rem] transition-colors hover:bg-danger/10 disabled:opacity-50"
                >
                  {retrying
                    ? <Loader2 size={10} className="animate-spin" />
                    : <RotateCcw size={10} />}
                  {tr("cls.card.retry")}
                </button>
              )}
            </div>
          )}
        </div>
        <Link
          href={lessonHref(wsId, lesson.lesson_id)}
          className="mt-1.5 shrink-0 text-muted/40 transition-transform group-hover:translate-x-0.5 group-hover:text-accent"
          aria-label={tr("cls.card.open")}
        >
          <ChevronRight size={16} />
        </Link>
      </div>
    </div>
  );
}

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
  const [classroomDisabled, setClassroomDisabled] = useState(false);
  const [resume, setResume] = useState<ResumeCardPublic | null>(null);
  const [restarting, setRestarting] = useState(false);

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
    getWorkspace(workspaceId)
      .then((ws) => { setWsName(ws.name); setWsMissing(false); })
      .catch(() => setWsMissing(true));
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
    try {
      await retryJob(workspaceId, lesson.lesson_id, job.job_id, {
        expected_state_revision: job.state_revision,
      });
    } catch { /* CAS 冲突等：刷新拿最新状态 */ }
    finally {
      setRetryingId(null);
      refresh();
    }
  };

  const onCreated = (lessonId: string) => {
    router.push(lessonHref(workspaceId, lessonId));
  };

  const learnHrefOf = (lessonId: string, runId: string) =>
    `/workspaces/${encodeURIComponent(workspaceId)}` +
    `/classroom/${encodeURIComponent(lessonId)}` +
    `/learn/${encodeURIComponent(runId)}`;

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
      {/* 顶部：返回 + 标题 + 模式条 + 主操作 */}
      <header className="flex flex-wrap items-center gap-3 border-b border-border px-5 py-3">
        <Link
          href="/chat"
          className="flex h-8 w-8 items-center justify-center rounded-[8px] text-muted transition-colors hover:bg-surface-hover hover:text-fg"
          aria-label="back to chat"
        >
          <ArrowLeft size={16} />
        </Link>
        <div className="min-w-0">
          <h1 className="truncate font-serif text-[1.05rem] font-bold tracking-tight text-fg">
            {wsName ? `${wsName} · ` : ""}{tr("cls.list.title.suffix")}
          </h1>
          <p className="hidden text-[0.68rem] text-muted sm:block">{tr("cls.list.subtitle")}</p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <WorkspaceModeBar workspaceId={workspaceId} mode="classroom" />
          <Button
            icon={<Sparkles size={14} />}
            onClick={() => { setCreateTopic(undefined); setCreateOpen(true); }}
          >
            {tr("cls.create")}
          </Button>
        </div>
      </header>

      {/* 三态筛选 */}
      <div className="flex items-center gap-1.5 px-5 py-2">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            type="button"
            onClick={() => { setFilter(f.value); setPage(0); }}
            aria-pressed={filter === f.value}
            className={cn(
              "cursor-pointer rounded-full border px-3 py-1 text-[0.7rem] font-medium transition-colors",
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
      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-4">
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
                  <Button
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
          <div className="mx-auto flex max-w-3xl flex-col gap-2.5">
            {/* §3.3 置顶“继续上课”卡（未完成 run；从头开始开新 run） */}
            {resume && (
              <div className="flex flex-wrap items-center gap-3 rounded-[12px] border border-accent/35 bg-accent-soft/30 p-4 shadow-sm">
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
                  <Button size="sm" variant="outline" disabled={restarting}
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
              />
            ))}
            <Pager
              page={page}
              total={total}
              per={PAGE_SIZE}
              onPage={setPage}
              className="mx-auto w-full max-w-3xl"
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
