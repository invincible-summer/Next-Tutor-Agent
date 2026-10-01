"use client";
/* 备课上课 Hub（/course）：按辅导区分组的课程总览。
 * 继续学习横排（各区分组预取时顺带收集 resume）+ 辅导区折叠分组
 * （折叠 id 持久化 localStorage，默认展开）+ 一键备课（可选辅导区）。
 * 分组课程懒加载：首次展开（或预取）时拉首页 4 门并缓存于本页状态。 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ChevronDown, ChevronRight, FolderOpen, FolderPlus, Loader2, BookOpen, Play, Search, ArrowUpRight,
  Presentation, RefreshCw, RotateCcw, Sparkles,
} from "lucide-react";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { useWsSettings, WS_CHANGED_EVENT } from "@/lib/ws-settings";
import { Input } from "@/components/ui/Input";
import { Pager, paged } from "@/components/ui/Pager";
import { cn } from "@/lib/cn";
import { getSidebarSnapshot } from "@/lib/api";
import {
  ClassroomApiError, getClassroomCapabilities, listLessons,
} from "@/lib/api-classroom";
import type { ClassroomSummary, WorkspaceItem } from "@/lib/types";
import type {
  LessonSummaryPublic, ResumeCardPublic,
} from "@/lib/types-classroom.generated";
import { startLessonRun } from "@/lib/classroom/useClassroomPlayer";
import { classroomPath, lessonPath } from "@/lib/classroom/paths";
import { Button } from "@/components/ui/Button";
import { EmptyState, ErrorNote, PageSkeleton } from "@/components/ui/EmptyState";
import { LessonCard } from "@/components/classroom/LessonCard";
import { CreateLessonModal } from "@/components/classroom/CreateLessonModal";
import { consumeAssistantDraft, getAssistantDraft } from "@/lib/assistant/api";
import { useAssistantPage } from "@/lib/assistant/useAssistantPage";
import { currentRouteEpoch } from "@/lib/assistant/page-context";
import { STRINGS } from "./strings";

const GROUP_PAGE_SIZE = 3;
/** 折叠状态：JSON 数组存「已折叠」的辅导区 id；默认全部展开。 */
const COLLAPSE_KEY = "edu-agent-course-expanded";

interface GroupData {
  items: LessonSummaryPublic[];
  total: number;
  resume: ResumeCardPublic | null;
}

export default function CourseHubPage() {
  const { lang } = useUIStore();
  const tr = makePageT(lang, STRINGS);
  const router = useRouter();

  const [caps, setCaps] = useState<"loading" | "enabled" | "disabled">("loading");
  const [snapshotReady, setSnapshotReady] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [query, setQuery] = useState("");
  const [groupPage, setGroupPage] = useState(0);
  const [actionError, setActionError] = useState(false);
  const [workspaces, setWorkspaces] = useState<WorkspaceItem[]>([]);
  const [summaries, setSummaries] = useState<Record<string, ClassroomSummary>>({});
  // null = 尚未水合（首个客户端渲染与 SSR 一致：全部展开）。
  const [collapsed, setCollapsed] = useState<string[] | null>(null);
  const [groups, setGroups] = useState<Record<string, GroupData>>({});
  const [groupLoading, setGroupLoading] = useState<Record<string, boolean>>({});
  const [groupError, setGroupError] = useState<Record<string, boolean>>({});
  const [resumeBusy, setResumeBusy] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [createWsId, setCreateWsId] = useState<string | undefined>(undefined);
  // A13 助手备课草稿：?assistant_draft= 打开表单并预填，不自动生成（§9.5）。
  const [draftTopic, setDraftTopic] = useState<string | undefined>(undefined);
  const [draftGoals, setDraftGoals] = useState<string[] | undefined>(undefined);
  const [draftDuration, setDraftDuration] = useState<number | undefined>(undefined);
  const draftIdRef = useRef<string | null>(null);
  /** 已请求过的分组（防重复拉取；失败后移除以允许重试）。 */
  const requestedRef = useRef<Set<string>>(new Set());
  // §20.2 适配器：备课中心（课堂能力状态 + 创建表单打开中）。
  useAssistantPage({
    context: () => ({
      schema_version: 1,
      route_id: "course",
      route_epoch: currentRouteEpoch(),
      view: createOpen ? "lesson_form" : undefined,
    }),
    clientState: () => ({
      dirty: createOpen,
      blocking_activity: "none",
      safe_bottom_px: 24,
    }),
  });

  const loadSnapshot = useCallback(() => {
    setLoadError(false);
    getSidebarSnapshot()
      .then((snap) => {
        setWorkspaces(snap.workspaces ?? []);
        setSummaries(snap.classroom_summaries ?? {});
        setSnapshotReady(true);
      })
      .catch(() => setLoadError(true));
  }, []);

  useEffect(() => {
    window.addEventListener(WS_CHANGED_EVENT, loadSnapshot);
    return () => window.removeEventListener(WS_CHANGED_EVENT, loadSnapshot);
  }, [loadSnapshot]);

  // 能力门控：确认关闭后整页降级（与 SideNav 同一开关）；请求失败乐观放行，
  // 真被关闭时由分组加载的 classroom_disabled 错误码兜回 disabled 态。
  useEffect(() => {
    let cancelled = false;
    getClassroomCapabilities()
      .then((c) => {
        if (!cancelled) setCaps(c.enabled === false ? "disabled" : "enabled");
      })
      .catch(() => { if (!cancelled) setCaps("enabled"); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const id = setTimeout(() => loadSnapshot(), 0);
    return () => clearTimeout(id);
  }, [loadSnapshot]);

  // 折叠状态水合（微任务延迟：setState 不在 effect 体内同步触发）。
  useEffect(() => {
    let cancelled = false;
    void Promise.resolve().then(() => {
      if (cancelled) return;
      try {
        const raw = localStorage.getItem(COLLAPSE_KEY);
        const parsed: unknown = raw ? JSON.parse(raw) : [];
        setCollapsed(Array.isArray(parsed)
          ? parsed.filter((x): x is string => typeof x === "string") : []);
      } catch {
        setCollapsed([]);
      }
    });
    return () => { cancelled = true; };
  }, []);

  // 懒加载一个分组的课程首页（幂等：requestedRef 去重；继续学习卡与分组
  // 共用这份缓存，不重复请求）。
  const ensureGroupLoaded = useCallback((wsId: string) => {
    if (requestedRef.current.has(wsId)) return;
    requestedRef.current.add(wsId);
    setGroupLoading((prev) => ({ ...prev, [wsId]: true }));
    setGroupError((prev) => ({ ...prev, [wsId]: false }));
    listLessons(wsId, { page: 1, pageSize: GROUP_PAGE_SIZE })
      .then((res) => {
        setGroups((prev) => ({
          ...prev,
          [wsId]: {
            items: res.items ?? [],
            total: res.total ?? 0,
            resume: res.resume ?? null,
          },
        }));
      })
      .catch((err) => {
        requestedRef.current.delete(wsId);
        if (err instanceof ClassroomApiError && err.code === "classroom_disabled") {
          setCaps("disabled");
        } else {
          setGroupError((prev) => ({ ...prev, [wsId]: true }));
        }
      })
      .finally(() => {
        setGroupLoading((prev) => ({ ...prev, [wsId]: false }));
      });
  }, []);

  // 预取：有课程的区无论折叠与否都拉（继续学习行依赖）；摘要确认无课的区
  // 不请求；摘要缺失的区仅在展开时拉取。collapsed 为水合后的最新值。
  useEffect(() => {
    if (!snapshotReady || caps !== "enabled") return;
    const id = setTimeout(() => {
      for (const ws of workspaces) {
        const summary = summaries[ws.workspace_id];
        const open = !(collapsed?.includes(ws.workspace_id) ?? false);
        if (summary ? summary.lesson_count === 0 : !open) continue;
        ensureGroupLoaded(ws.workspace_id);
      }
    }, 0);
    return () => clearTimeout(id);
  }, [snapshotReady, caps, workspaces, summaries, collapsed, ensureGroupLoaded]);

  // ?create=1：进入页面即打开备课 Modal（与课堂列表页同模式）。
  useEffect(() => {
    if (!snapshotReady) return;
    if (window.location.search.includes("create=1")) {
      window.history.replaceState(null, "", window.location.pathname);
      const id = setTimeout(() => setCreateOpen(true), 0);
      return () => clearTimeout(id);
    }
  }, [snapshotReady]);

  const isCollapsed = (wsId: string) => collapsed?.includes(wsId) ?? false;

  const toggleGroup = (wsId: string) => {
    const cur = collapsed ?? [];
    const next = cur.includes(wsId)
      ? cur.filter((x) => x !== wsId) : [...cur, wsId];
    setCollapsed(next);
    try { localStorage.setItem(COLLAPSE_KEY, JSON.stringify(next)); } catch { /* ignore */ }
    if (!next.includes(wsId)) ensureGroupLoaded(wsId);
  };

  useEffect(() => {
    const draftId = new URLSearchParams(window.location.search)
      .get("assistant_draft");
    if (!draftId || draftIdRef.current) return;
    draftIdRef.current = draftId;
    getAssistantDraft(draftId)
      .then((draft) => {
        const prefill = (draft as {
          prefill?: {
            kind?: string; workspace_id?: string; topic?: string;
            objectives?: string; duration_minutes?: number;
          };
          consumed?: boolean; expired?: boolean;
        }).prefill;
        if (prefill?.kind !== "lesson" || draft.consumed || draft.expired) {
          return;
        }
        setDraftTopic(String(prefill.topic || ""));
        // objectives（换行/分号分隔）→ 学习目标列表（§19.6，≤5 条）。
        const goals = String(prefill.objectives || "")
          .split(/[\n；;]+/)
          .map((g) => g.trim())
          .filter(Boolean)
          .slice(0, 5);
        setDraftGoals(goals.length ? goals : undefined);
        const mins = Number(prefill.duration_minutes);
        // 课件时长为离散枚举（5/10/15/20/30），非法值保留向导默认。
        setDraftDuration(
          [5, 10, 15, 20, 30].includes(mins) ? mins : undefined);
        setCreateWsId(prefill.workspace_id || undefined);
        setCreateOpen(true);
        // 去除 assistant_draft 参数但保留其他定位参数（§19.5）。
        const params = new URLSearchParams(window.location.search);
        params.delete("assistant_draft");
        const query = params.toString();
        window.history.replaceState(null, "",
          query ? `/course?${query}` : "/course");
      })
      .catch(() => undefined);
  }, []);

  const openCreate = (wsId?: string) => {
    setCreateWsId(wsId);
    setCreateOpen(true);
  };

  const onCreated = (lessonId: string, wsId: string) => {
    const draftId = draftIdRef.current;
    if (draftId) {
      // 提交成功才消费（§19.6）；失败/取消不消费。
      consumeAssistantDraft(draftId, { kind: "lesson", id: lessonId })
        .catch(() => undefined);
    }
    router.push(lessonPath(wsId, lessonId));
  };

  // 继续学习：创建/复用 run 后跳播放页（resume_or_create 服务端幂等）。
  const onResume = async (
    wsId: string, lessonId: string, mode: "resume_or_create" | "restart",
  ) => {
    if (resumeBusy) return;
    setActionError(false);
    setResumeBusy(`${lessonId}:${mode}`);
    try {
      const runId = await startLessonRun(wsId, lessonId, mode);
      router.push(
        `${lessonPath(wsId, lessonId)}/learn/${encodeURIComponent(runId)}`);
    } catch {
      setActionError(true);
      setResumeBusy(null);
    }
  };

  const resumes = useMemo(
    () => workspaces.flatMap((ws) => {
      const resume = groups[ws.workspace_id]?.resume;
      return resume
        ? [{ wsId: ws.workspace_id, wsName: ws.name, resume }] : [];
    }),
    [workspaces, groups],
  );

  // 全量空态：所有区摘要齐全且课程数均为 0（且懒加载也未发现课程）。
  const allSummariesKnown = workspaces.length > 0
    && workspaces.every((ws) => summaries[ws.workspace_id]);
  const totalLessons = workspaces.reduce(
    (n, ws) => n + (summaries[ws.workspace_id]?.lesson_count ?? 0), 0);
  const anyLoadedLessons = useMemo(
    () => Object.values(groups).some((g) => g.items.length > 0), [groups]);
  const showHeroEmpty = allSummariesKnown && totalLessons === 0 && !anyLoadedLessons;

  const filteredWorkspaces = workspaces.filter((ws) => ws.name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const activeJobs = Object.values(summaries).reduce((n, s) => n + s.active_job_count, 0);

  return (
    <div className="course-hub page-in h-full overflow-y-auto p-4 sm:p-7 lg:p-9">
      <div className="mx-auto flex max-w-[1200px] flex-col gap-5">
        <header className="course-hero">
          <div className="relative z-10 max-w-xl">
            <span className="course-eyebrow"><span className="h-1.5 w-1.5 rounded-full bg-[#afcebe]" />{tr("course.eyebrow")}</span>
            <h1 className="mt-4 text-3xl font-semibold tracking-tight sm:text-4xl">{tr("nav.course")}</h1>
            <p className="mt-3 max-w-md text-sm leading-7 text-white/70">{tr("course.subtitle")}</p>
            <div className="mt-6 flex flex-wrap items-center gap-3">
              {caps !== "disabled" && workspaces.length > 0 && (
                <Button className="!h-11 !rounded-xl !bg-[#e4efe6] !px-5 !text-[#234b40] hover:!bg-white" icon={<Sparkles size={16} />} onClick={() => openCreate()}>
                  {tr("ws.classroom.create")}
                </Button>
              )}
              <button type="button" className="inline-flex h-11 items-center gap-2 rounded-xl border border-white/20 px-4 text-xs text-white/85 transition-colors hover:bg-white/10" onClick={() => useWsSettings.getState().open("new")}>
                <FolderPlus size={15} />{tr("course.newSpace")}
              </button>
            </div>
          </div>
          <div className="course-hero-art" aria-hidden="true">
            <div className="course-hero-sheet course-hero-sheet-back" />
            <div className="course-hero-sheet">
              <div className="flex items-center justify-between text-[9px] tracking-[.18em] opacity-60"><span>LESSON STUDIO</span><BookOpen size={14} /></div>
              <div className="mt-6 whitespace-pre-line text-xl font-semibold leading-relaxed">{tr("course.art.title")}</div>
              <div className="mt-3 h-1 w-9 rounded-full bg-[#649781]" />
              <div className="mt-5 flex items-end gap-2">{[26, 42, 36, 64, 52, 80].map((h, i) => <span key={i} className="w-5 rounded-t bg-[#75a58e]/30" style={{ height: h }} />)}<span className="ml-auto flex h-11 w-11 items-center justify-center rounded-full bg-[#2c5e4d] text-white"><Play size={16} fill="currentColor" /></span></div>
            </div>
          </div>
        </header>
        {snapshotReady && caps !== "disabled" && (
          <div className="course-stat-strip">
            {[[tr("course.stat.spaces"), workspaces.length], [tr("course.stat.lessons"), totalLessons], [tr("course.stat.progress"), activeJobs]].map(([label, value]) => (
              <div key={label} className="flex items-baseline gap-3"><strong className="tnum text-2xl font-medium tracking-tight text-fg">{value}</strong><span className="text-xs text-muted">{label}</span></div>
            ))}
            <span className="ml-auto hidden items-center gap-2 text-xs text-muted lg:flex"><Presentation size={14} />{tr("course.workflow")}</span>
          </div>
        )}
        {actionError && <ErrorNote message={tr("cls.error.load")} />}

        {caps === "disabled" ? (
          <EmptyState
            icon={<Presentation size={28} />}
            title={tr("cls.disabled.title")}
            desc={tr("cls.disabled.desc")}
          />
        ) : loadError ? (
          <ErrorNote message={tr("cls.error.load")} retry={loadSnapshot} />
        ) : !snapshotReady ? (
          <PageSkeleton />
        ) : workspaces.length === 0 ? (
          <EmptyState
            icon={<FolderOpen size={28} />}
            title={tr("ws.empty")}
            desc={tr("course.empty.noWs.desc")}
            action={(
              <Button icon={<FolderPlus size={15} />} onClick={() => useWsSettings.getState().open("new")}>{tr("course.newSpace")}</Button>
            )}
          />
        ) : (
          <>
            {/* 继续学习：各辅导区未完成 run 的置顶横排（无则隐藏） */}
            {resumes.length > 0 && (
              <section aria-label={tr("course.resume.title")}>
                <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-muted">
                  <Play size={12} />{tr("course.resume.title")}
                </h2>
                <div className="flex gap-3 overflow-x-auto pb-1">
                  {resumes.map(({ wsId, wsName, resume }) => {
                    const continueKey = `${resume.lesson_id}:resume_or_create`;
                    const restartKey = `${resume.lesson_id}:restart`;
                    return (
                      <div
                        key={wsId}
                        className="course-resume-item flex min-w-72 flex-1 flex-col gap-3 rounded-2xl border border-accent/20 bg-accent-soft/25 p-5"
                      >
                        <div className="flex items-start gap-2.5">
                          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[9px] bg-accent text-on-accent">
                            <Play size={14} />
                          </span>
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-[0.85rem] font-semibold text-fg">
                              {resume.title}
                            </p>
                            <p className="tnum mt-0.5 truncate text-[0.68rem] text-muted">
                              {wsName}
                              {resume.slide_count
                                ? ` · ${tr("cls.list.resume.page")
                                    .replace("%n", String(resume.run.cursor_slide_order ?? 1))
                                    .replace("%t", String(resume.slide_count))}`
                                : ""}
                            </p>
                          </div>
                        </div>
                        <div className="h-1 overflow-hidden rounded-full bg-accent/10"><div className="h-full rounded-full bg-accent/60" style={{ width: `${Math.min(100, Math.max(0, ((resume.run.cursor_slide_order ?? 1) - 1) / Math.max(1, resume.slide_count ?? 1) * 100))}%` }} /></div>
                        <div className="flex items-center gap-2">
                          <Button
                            size="sm"
                            disabled={resumeBusy !== null}
                            icon={resumeBusy === continueKey
                              ? <Loader2 size={12} className="animate-spin" />
                              : <Play size={12} />}
                            onClick={() => void onResume(wsId, resume.lesson_id, "resume_or_create")}
                          >
                            {tr("cls.list.resume")}
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={resumeBusy !== null}
                            icon={resumeBusy === restartKey
                              ? <Loader2 size={12} className="animate-spin" />
                              : <RotateCcw size={12} />}
                            onClick={() => void onResume(wsId, resume.lesson_id, "restart")}
                          >
                            {tr("cls.list.resume.restart")}
                          </Button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </section>
            )}

            <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
              <div><h2 className="text-lg font-semibold tracking-tight text-fg">{tr("course.library")}</h2><p className="mt-1 text-xs text-muted">{tr("course.library.hint")}</p></div>
              <div className="relative w-full sm:w-64"><Search size={15} className="absolute left-3 top-3 text-muted" /><Input aria-label={tr("course.search")} placeholder={tr("course.search")} value={query} onChange={(e) => { setQuery(e.target.value); setGroupPage(0); }} className="!h-10 !rounded-xl !pl-9" /></div>
            </div>
            {showHeroEmpty ? (
              <EmptyState
                icon={<Presentation size={28} />}
                title={tr("course.empty.lessons.title")}
                desc={tr("course.empty.lessons.desc")}
                action={(
                  <Button size="sm" icon={<Sparkles size={13} />} onClick={() => openCreate()}>
                    {tr("ws.classroom.create")}
                  </Button>
                )}
              />
            ) : (
              <div className="flex flex-col gap-3">
                {paged(filteredWorkspaces, groupPage).map((ws) => {
                  const summary = summaries[ws.workspace_id];
                  const group = groups[ws.workspace_id];
                  const collapsedNow = isCollapsed(ws.workspace_id);
                  const lessonCount = group?.total ?? summary?.lesson_count ?? 0;
                  const generating = summary?.active_job_count ?? 0;
                  return (
                    <section
                      key={ws.workspace_id}
                      className="course-group overflow-hidden rounded-2xl border border-border/70 bg-surface"
                    >
                      <div className="group flex flex-wrap items-center gap-2 px-4 py-4 sm:px-5">
                        <button
                          type="button"
                          onClick={() => toggleGroup(ws.workspace_id)}
                          aria-expanded={!collapsedNow}
                          className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 rounded-md py-1 text-left"
                        >
                          {collapsedNow
                            ? <ChevronRight size={14} className="shrink-0 text-muted" />
                            : <ChevronDown size={14} className="shrink-0 text-muted" />}
                          <FolderOpen size={15} className="shrink-0 text-accent" />
                          <span className="truncate text-sm font-semibold text-fg">{ws.name}</span>
                          <span className="tnum shrink-0 text-[0.68rem] text-muted">
                            {tr("course.group.lessons").replace("%n", String(lessonCount))}
                          </span>
                          {generating > 0 && (
                            <span className="tnum inline-flex shrink-0 items-center gap-1 rounded-full border border-accent/30 bg-accent-soft/40 px-2 py-0.5 text-[0.65rem] font-medium text-accent-strong">
                              <Loader2 size={10} className="animate-spin" />
                              {tr("ws.classroom.generating").replace("%n", String(generating))}
                            </span>
                          )}
                        </button>
                        {/* 窄屏常显，≥sm 悬停/聚焦显现 */}
                        <div className="flex shrink-0 items-center gap-1">
                          <button
                            type="button"
                            onClick={() => openCreate(ws.workspace_id)}
                            className="inline-flex min-h-8 cursor-pointer items-center gap-1 rounded-md px-2 text-[0.72rem] text-muted transition-colors hover:bg-surface-hover hover:text-accent-strong"
                          >
                            <Sparkles size={12} />{tr("ws.classroom.create")}
                          </button>
                          {lessonCount > 0 && (
                            <Link
                              href={classroomPath(ws.workspace_id)}
                              className="inline-flex min-h-8 items-center gap-0.5 rounded-md px-2 text-[0.72rem] text-muted transition-colors hover:bg-surface-hover hover:text-accent-strong"
                            >
                              {tr("course.group.viewAll")}<ArrowUpRight size={13} />
                            </Link>
                          )}
                        </div>
                      </div>
                      {!collapsedNow && (
                        <div className={cn(
                          "border-t border-border-light px-4 pb-5 pt-4 sm:px-5",
                        )}>
                          {groupLoading[ws.workspace_id] && !group ? (
                            <div className="flex items-center justify-center gap-2 py-8 text-muted">
                              <Loader2 size={14} className="animate-spin" />
                            </div>
                          ) : groupError[ws.workspace_id] && !group ? (
                            <div className="flex items-center justify-between gap-2 rounded-[10px] border border-danger/30 bg-danger/5 px-3 py-2 text-xs text-danger">
                              <span>{tr("cls.error.load")}</span>
                              <button
                                type="button"
                                onClick={() => ensureGroupLoaded(ws.workspace_id)}
                                className="inline-flex cursor-pointer items-center gap-1 text-[0.7rem] font-medium hover:underline"
                              >
                                <RefreshCw size={11} />{tr("cls.retry")}
                              </button>
                            </div>
                          ) : group && group.items.length > 0 ? (
                            <div className="course-card-grid">
                              {group.items.map((lesson) => (
                                <LessonCard
                                  key={lesson.lesson_id}
                                  wsId={ws.workspace_id}
                                  lesson={lesson}
                                  tr={tr}
                                />
                              ))}
                            </div>
                          ) : (
                            <div className="flex flex-wrap items-center justify-between gap-2 rounded-[10px] border border-dashed border-border px-3 py-2.5">
                              <span className="text-xs text-muted">
                                {tr("course.group.empty")}
                              </span>
                              <Button
                                size="sm"
                                variant="outline"
                                icon={<Sparkles size={12} />}
                                onClick={() => openCreate(ws.workspace_id)}
                              >
                                {tr("ws.classroom.create")}
                              </Button>
                            </div>
                          )}
                        </div>
                      )}
                    </section>
                  );
                })}
              </div>
            )}
            {!showHeroEmpty && filteredWorkspaces.length === 0 && <EmptyState icon={<Search size={24} />} title={tr("course.search.empty")} />}
            <Pager page={groupPage} total={filteredWorkspaces.length} onPage={setGroupPage} />
          </>
        )}

        {/* 一键备课 Modal（挂载式；选择器模式选择所属辅导区） */}
        {createOpen && (
          <CreateLessonModal
            open
            workspaceOptions={workspaces.map((w) => ({
              workspace_id: w.workspace_id, name: w.name,
            }))}
            initialWorkspaceId={createWsId}
            initialTopic={draftTopic}
            initialGoals={draftGoals}
            initialDuration={draftDuration}
            onClose={() => setCreateOpen(false)}
            onCreated={onCreated}
          />
        )}
      </div>
    </div>
  );
}
