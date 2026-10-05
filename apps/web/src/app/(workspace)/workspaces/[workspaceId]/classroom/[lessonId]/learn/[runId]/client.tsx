"use client";
import { navigationSucceeded, navigationFailed } from "@/lib/assistant/navigation";

/* 选页预览 → 全屏播放器；对话、讲稿与笔记共用右侧栏。 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  ArrowLeft, LogOut, Play,
  MonitorPlay, AudioLines, MessageCircle, BookOpenText, NotebookPen,
} from "lucide-react";
import { getLesson, getRevisionFrame, getRun,
  type RunPublicExtra } from "@/lib/api-classroom";
import type { LessonDetailPublic } from "@next-tutor/contracts/classroom";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { Button } from "@/components/ui/Button";
import SlideFrameDefault, {
  type SlideFrameHandle,
} from "@/components/classroom/SlideFrame";
import {
  CaptionBar, PlayerControls, type PlayerStrings,
} from "@/components/classroom/player/PlayerWidgets";
import { consumeAssistantDraft, getAssistantDraft } from "@/lib/assistant/api";
import {
  QuestionDrawer,
} from "@/components/classroom/player/QuestionDrawer";
import { SlideThumbnail } from "@/components/classroom/SlideThumbnail";
import { VoicePanel } from "@/components/classroom/player/VoicePanel";
import { ScriptPanel } from "@/components/classroom/player/ScriptPanel";
import { ClassroomNotes } from "@/components/classroom/player/ClassroomNotes";
import {
  CheckpointPanel,
} from "@/components/classroom/player/CheckpointPanel";
import { useClassroomPlayer } from "@/lib/classroom/useClassroomPlayer";
import { useClassroomQA } from "@/lib/classroom/useClassroomQA";
import { useAssistantPage } from "@/lib/assistant/useAssistantPage";
import { currentRouteEpoch } from "@/lib/assistant/page-context";
import { STRINGS as PLAYER_STR } from "@/components/classroom/strings";
import { STRINGS as PAGE_STRINGS } from "../../../strings";

const PREFS_KEY = "edu-agent-player-prefs";

/** 播放器音量/语速偏好：localStorage 持久化（SSR 安全，坏数据静默丢弃）。 */
function readPlayerPrefs(): { volume?: number; playbackRate?: number } {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(PREFS_KEY);
    if (!raw) return {};
    const data = JSON.parse(raw) as Record<string, unknown>;
    const out: { volume?: number; playbackRate?: number } = {};
    if (typeof data.volume === "number" && Number.isFinite(data.volume)) {
      out.volume = Math.min(1, Math.max(0, data.volume));
    }
    if (typeof data.playbackRate === "number"
        && Number.isFinite(data.playbackRate)) {
      out.playbackRate = Math.min(1.5, Math.max(0.5, data.playbackRate));
    }
    return out;
  } catch {
    return {};
  }
}

function writePlayerPrefs(prefs: { volume: number; playbackRate: number }) {
  try {
    window.localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  } catch { /* 隐私模式等场景静默失败 */ }
}

export default function LearnRunPage() {
  const { lang } = useUIStore();
  const tr = makePageT(lang, PAGE_STRINGS);
  const ps = makePageT(lang, PLAYER_STR);
  const params = useParams<{
    workspaceId: string; lessonId: string; runId: string;
  }>();
  const router = useRouter();
  const safe = (s: string) => {
    try { return decodeURIComponent(s); } catch { return s; }
  };
  const workspaceId = safe(params.workspaceId ?? "");
  const lessonId = safe(params.lessonId ?? "");
  const runId = safe(params.runId ?? "");
  const lessonHref = `/workspaces/${encodeURIComponent(workspaceId)}` +
    `/classroom/${encodeURIComponent(lessonId)}`;

  const [detail, setDetail] = useState<LessonDetailPublic | null>(null);
  const [frameIdentity, setFrameIdentity] = useState("");
  const [frameHtml, setFrameHtml] = useState<string | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);
  const [sidePanel, setSidePanel] = useState<"chat" | "script" | "notes">("chat");
  // A13 助手插问草稿：?assistant_draft= 打开抽屉并预填，不自动提交。
  const [questionDraft, setQuestionDraft] = useState<string | undefined>(undefined);
  const questionDraftIdRef = useRef<string | null>(null);
  useEffect(() => {
    const draftId = new URLSearchParams(window.location.search)
      .get("assistant_draft");
    if (!draftId || questionDraftIdRef.current) return;
    questionDraftIdRef.current = draftId;
    getAssistantDraft(draftId)
      .then((draft) => {
        const prefill = (draft as {
          prefill?: { kind?: string; question?: string };
          consumed?: boolean; expired?: boolean;
        }).prefill;
        if (prefill?.kind !== "classroom_question"
            || draft.consumed || draft.expired) {
          return;
        }
        setQuestionDraft(String(prefill.question || ""));
        setSidePanel("chat");
        const params = new URLSearchParams(window.location.search);
        params.delete("assistant_draft");
        const query = params.toString();
        window.history.replaceState(null, "", query
          ? `${window.location.pathname}?${query}`
          : window.location.pathname);
      })
      .catch(() => undefined);
  }, []);
  const [initialRun, setInitialRun] = useState<RunPublicExtra | null>(null);
  const [voiceOpen, setVoiceOpen] = useState(false);
  const [summary, setSummary] = useState<{
    slides?: { visited: number; total: number };
    segments?: { listened: number; total: number };
    questions_asked?: { count: number };
    checkpoints?: { items: { state: string }[] };
    mastery?: { note: string };
  } | null>(null);
  const frameRef = useRef<SlideFrameHandle | null>(null);
  const shellRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const loadedRun = await getRun(workspaceId, lessonId, runId);
        const d = await getLesson(workspaceId, lessonId,
                                  loadedRun.lesson_revision);
        if (!alive) return;
        setInitialRun(loadedRun);
        setDetail(d);
        const rev = d.revision?.revision;
        if (rev) {
          const html = await getRevisionFrame(workspaceId, lessonId, rev,
                                              "presentation");
          if (alive) { setFrameHtml(html); setFrameIdentity(`${workspaceId}:${lessonId}:${runId}`); }
        }
      } catch (err) {
        if (alive) setFatal(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => { alive = false; };
  }, [workspaceId, lessonId, runId]);

  const player = useClassroomPlayer({
    workspaceId, lessonId, runId,
    detail: detail ?? ({ revision: null } as unknown as LessonDetailPublic),
    initialRun,
  });
  const slides = useMemo(() => detail?.revision?.slides ?? [], [detail]);

  // 课堂插问（§12.4/§12.5）：ask 暂停讲授；resume 回到最初被打断段
  const qa = useClassroomQA({
    workspaceId, lessonId, runId,
    leaseEpoch: player.run?.lease?.lease_epoch ?? 0,
    current: player.current,
    lessonRevision: detail?.revision?.revision ?? 1,
    withVoice: !player.textMode,
    pauseNarration: player.pauseForAsk,
  });
  const anchorSegmentId = player.run?.resume_anchor?.segment_id ?? null;

  // §5.3-1 助手按钮避让课堂控制条：实测控制条高度，经适配器上报。
  const [controlsHeight, setControlsHeight] = useState(0);
  useEffect(() => {
    const el = shellRef.current?.querySelector<HTMLElement>(
      ".classroom-controls");
    if (!el || typeof ResizeObserver === "undefined") {
      setControlsHeight(el?.offsetHeight ?? 0);
      return;
    }
    setControlsHeight(el.offsetHeight);
    const ro = new ResizeObserver(() => setControlsHeight(el.offsetHeight));
    ro.observe(el);
    return () => ro.disconnect();
  }, [frameHtml, player.state.fullscreen, detail]);

  // §20.2 页面适配器：课堂播放上下文（run 实体 + 预览/全屏态）。
  // 离开课堂不阻断导航（暂停/恢复由播放器既有行为处理，§5.4）。
  useAssistantPage({
    navigationStatus: (target) => {
      if (fatal) return navigationFailed;
      if (!detail || !frameHtml || frameIdentity !== `${workspaceId}:${lessonId}:${runId}` || initialRun?.run_id !== runId) return null;
      return target.kind === "classroom_run" && target.workspace_id === workspaceId && target.lesson_id === lessonId && target.run_id === runId
        ? navigationSucceeded : null;
    },
    context: () => ({
      schema_version: 1,
      route_id: "course" as const,
      route_epoch: currentRouteEpoch(),
      workspace_id: workspaceId || undefined,
      entity: runId ? {
        kind: "run" as const,
        id: runId,
        parent_id: lessonId || undefined,
        revision: detail?.revision
          ? String(detail.revision.revision) : undefined,
      } : undefined,
      view: player.state.fullscreen ? "fullscreen" : "preview",
    }),
    clientState: () => ({
      dirty: false,
      blocking_activity: "none" as const,
      safe_bottom_px: Math.max(24, controlsHeight + 16),
    }),
  });
  const resumeFromAnchor = useCallback(() => {
    qa.stopAudio();
    const segId = anchorSegmentId ?? player.current?.segmentId;
    if (segId) player.gotoSegment(segId, true);
  }, [qa, anchorSegmentId, player]);
  const resumeFromPage = useCallback(() => {
    qa.stopAudio();
    const anchorSlide = anchorSegmentId
      ? player.segments.find((s) => s.segmentId === anchorSegmentId)?.slideId
      : player.current?.slideId;
    const first = player.segments.find((s) => s.slideId === anchorSlide)
      ?? player.segments[0];
    if (first) player.gotoSegment(first.segmentId, true);
  }, [qa, anchorSegmentId, player]);

  const slideOrders = useMemo(
    () => [...slides].sort((a, b) => a.order - b.order).map((s) => s.order),
    [slides]);
  const slideTitles = useMemo(() => {
    const m = new Map<number, string>();
    for (const s of slides) m.set(s.order, s.title);
    return m;
  }, [slides]);

  // 段动作 → frame 指令（翻页 + 块显隐/高亮）
  const slideOrder = player.current?.slideOrder ?? 1;
  const currentSeg = player.current;

  const checkpointDue = player.pendingCheckpoint;

  useEffect(() => {
    frameRef.current?.gotoPage(slideOrder);
  }, [slideOrder, frameHtml]);
  useEffect(() => {
    const show = currentSeg?.showBlockIds ?? [];
    const focus = currentSeg?.focusBlockIds ?? [];
    const preview = !player.state.fullscreen;
    frameRef.current?.setBlockState(preview || !show.length ? ["*"] : show, preview ? [] : focus);
  }, [currentSeg, slideOrder, frameHtml, player.state.fullscreen]);

  // 音量/语速偏好：课堂装载完成后恢复一次（覆盖 reducer 初始值）
  const prefsRestoredRef = useRef(false);
  useEffect(() => {
    if (prefsRestoredRef.current || player.state.status === "loading") return;
    prefsRestoredRef.current = true;
    const prefs = readPlayerPrefs();
    if (prefs.volume !== undefined || prefs.playbackRate !== undefined) {
      player.setSettings({ type: "settings", ...prefs });
    }
  }, [player]);

  // 一次性 notice（语音源切换/本地回退）：短暂展示后清除。
  // setSettings 经 ref 调用，effect 只随 notice 码重置计时（播放中推进
  // 新段导致的重渲染不应延长展示）。
  const playerSettingsRef = useRef(player.setSettings);
  useEffect(() => { playerSettingsRef.current = player.setSettings; });
  const noticeCode = player.state.notice;
  useEffect(() => {
    if (!noticeCode) return;
    const t = window.setTimeout(() => {
      playerSettingsRef.current({ type: "notice", notice: null });
    }, 3000);
    return () => window.clearTimeout(t);
  }, [noticeCode]);

  const toggleFullscreen = useCallback(() => {
    const el = shellRef.current;
    if (!el) return;
    if (document.fullscreenElement) {
      void document.exitFullscreen().catch(() => undefined);
    } else if (el.classList.contains("player-focus-fallback")) {
      el.classList.remove("player-focus-fallback");
      player.setSettings({ type: "settings", fullscreen: false });
    } else {
      const fallback = () => {
        el.classList.add("player-focus-fallback");
        player.setSettings({ type: "settings", fullscreen: true });
      };
      if (el.requestFullscreen) void el.requestFullscreen().catch(fallback);
      else fallback();
    }
  }, [player]);

  const startPlayback = useCallback(() => {
    if (["loading", "suspended", "ended"].includes(player.state.status) || player.pendingCheckpoint || qa.asking) return;
    qa.stopAudio();
    const playing = ["playing", "buffering"].includes(player.state.status);
    if (!playing && !player.state.fullscreen) {
      toggleFullscreen();
      if (player.textMode) return;
    }
    if (player.textMode) player.nextSegment();
    else player.togglePlay();
  }, [player, qa, toggleFullscreen]);

  useEffect(() => {
    const onFs = () => player.setSettings({
      type: "settings", fullscreen: document.fullscreenElement === shellRef.current,
    });
    document.addEventListener("fullscreenchange", onFs);
    return () => document.removeEventListener("fullscreenchange", onFs);
  }, [player]);

  // 课后回顾（§13.5）：确定性 summary，完成层展示
  const playStatus = player.state.status;
  useEffect(() => {
    if (playStatus !== "ended" || summary) return;
    let alive = true;
    void (async () => {
      try {
        const { apiFetch } = await import("@/lib/api-fetch");
        const { API_BASE } = await import("@/lib/api");
        const res = await apiFetch(
          API_BASE +
          "/workspaces/" + encodeURIComponent(workspaceId) +
          "/classroom/lessons/" + encodeURIComponent(lessonId) +
          "/runs/" + encodeURIComponent(runId) + "/summary");
        if (alive && res.ok) setSummary(await res.json());
      } catch { /* 回顾可选 */ }
    })();
    return () => { alive = false; };
  }, [playStatus, summary, workspaceId, lessonId, runId]);

  // 键盘 Space/←/→/C/F：输入框聚焦时不响应（§5.3）
  const handlePlayerKey = useCallback((key: string) => {
      if ((["loading", "suspended", "ended"].includes(player.state.status) || player.pendingCheckpoint || qa.asking)
          && !["f", "F", "c", "C", "Escape"].includes(key)) return;
      switch (key) {
        case " ":
          startPlayback();
          break;
        case "ArrowRight":
          player.gotoSlide(Math.min(slideOrders.length,
                                    (player.current?.slideOrder ?? 1) + 1));
          break;
        case "ArrowLeft":
          player.gotoSlide(Math.max(1,
                                    (player.current?.slideOrder ?? 1) - 1));
          break;
        case "c":
        case "C":
          player.setSettings({ type: "settings",
                               captions: !player.state.captions });
          break;
        case "f":
        case "F":
          toggleFullscreen();
          break;
        case "Escape":
          if (shellRef.current?.classList.contains("player-focus-fallback")) toggleFullscreen();
          break;
        case "q":
        case "Q":
          setSidePanel("chat");
          break;
        default:
          break;
      }
  }, [player, qa.asking, startPlayback, toggleFullscreen, slideOrders.length]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA"
                || t.isContentEditable || t.tagName === "SELECT")) return;
      if (e.altKey || e.ctrlKey || e.metaKey || voiceOpen || checkpointDue) return;
      if (t?.closest("button, a, [role=dialog]")) return;
      if (e.key === " ") e.preventDefault();
      handlePlayerKey(e.key);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [handlePlayerKey, voiceOpen, checkpointDue]);

  const playerStrings: PlayerStrings = {
    captionsLabel: ps("cls.play.captions"),
    captionsBackToCurrent: ps("cls.play.captions.back"),
    outlineTitle: ps("cls.play.outline"),
    play: ps("cls.play.play"), pause: ps("cls.play.pause"),
    prevPage: ps("cls.play.prev"), nextPage: ps("cls.play.next"),
    replay: ps("cls.play.replay"), volume: ps("cls.play.volume"),
    mute: ps("cls.play.mute"), unmute: ps("cls.play.unmute"),
    progress: ps("cls.play.progress"), estimated: ps("cls.play.estimated"), speed: ps("cls.play.speed"),
    focusMode: ps("cls.play.fullscreen"),
    fullscreen: ps("cls.play.fullscreen"),
    exitFullscreen: ps("cls.play.exit.fullscreen"),
    pageOf: (n, t) => ps("cls.play.pageof").replace("%n", String(n))
      .replace("%t", String(t)),
    segmentOf: (n, t) => ps("cls.play.segmentof").replace("%n", String(n))
      .replace("%t", String(t)),
    textMode: ps("cls.play.textmode"),
    continueReading: ps("cls.play.continue.reading"),
    moreSettings: ps("cls.play.more.settings"),
    bufferingSlow: ps("cls.play.click.resume"),
    remaining: (m) => `${m} min`,
  };

  // 一次性提示码 → 本地化文案（useClassroomPlayer 只发码不发文案）
  const noticeText = player.state.notice
    ? {
        local_fallback_notice: ps("cls.play.notice.local_fallback"),
        voice_switched: ps("cls.voice.switched"),
        voice_switch_failed: ps("cls.voice.switch.failed"),
        voice_switch_conflict: ps("cls.voice.switch.conflict"),
      }[player.state.notice] ?? null
    : null;

  if (fatal) {
    // 深链失效（课程/课堂记录被删除）给出可识别状态（§H04），不只是裸错误
    const gone = fatal.includes("source_not_found")
      || fatal.includes("不存在") || fatal.includes("404");
    return (
      <div className="flex h-full flex-col">
        <header className="flex items-center gap-3 border-b border-border px-5 py-3">
          <Link href={lessonHref} aria-label={tr("cls.back.to.list")}
                className="flex h-8 w-8 items-center justify-center rounded-[8px] text-muted hover:bg-surface-hover hover:text-fg">
            <ArrowLeft size={16} />
          </Link>
          <h1 className="font-serif text-[1.05rem] font-bold text-fg">
            {tr("cls.learn.player.title")}
          </h1>
        </header>
        <div className="flex flex-1 flex-col items-center justify-center gap-3 text-muted">
          <p>{gone ? tr("cls.detail.notfound") : ps("cls.play.suspended.error")}</p>
          {!gone && <Button variant="primary" size="sm"
                            onClick={() => window.location.reload()}>
            {ps("cls.play.suspended.retry")}
          </Button>}
          <Link href={lessonHref}
                className="text-sm text-accent-strong hover:underline">
            {ps("cls.play.back.course")}
          </Link>
        </div>
      </div>
    );
  }

  const status = player.state.status;
  const suspended = status === "suspended";
  const ended = status === "ended";

  return (
    <div ref={shellRef} data-fullscreen={player.state.fullscreen} className="classroom-player flex h-full min-h-0 flex-col bg-bg">
      <header className={`flex min-h-14 items-center gap-2 border-b border-border bg-surface px-3 sm:gap-3 sm:px-5`}>
        <button type="button" aria-label={tr("cls.back.to.list")}
              onClick={() => void player.leaveLesson().then(() => router.push(lessonHref))}
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-[8px] text-muted hover:bg-surface-hover hover:text-fg">
          <ArrowLeft size={16} />
        </button>
        <div className="min-w-0 flex-1">
          <div className="mb-0.5 flex items-center gap-1.5 text-[9px] font-medium tracking-widest text-accent-strong"><AudioLines size={11} />{ps("cls.play.studio")}</div>
          <h1 className="truncate text-sm font-semibold tracking-tight text-fg">{detail?.title ?? tr("cls.learn.player.title")}</h1>
        </div>
        <span className="ml-auto hidden rounded-full border border-border px-3 py-1 text-xs tabular-nums text-muted sm:inline-flex">
          {playerStrings.pageOf(slideOrder, slideOrders.length || 1)}
        </span>
        <div className="flex items-center gap-1 sm:gap-2">
          <Button variant="ghost" size="sm"
                  className="min-h-11 min-w-11"
                  icon={<LogOut size={14} />}
                  onClick={() => {
                    void player.leaveLesson().then(() => {
                    router.push(lessonHref);
                    });
                  }}>
            <span className="hidden sm:inline">{ps("cls.play.exit")}</span>
          </Button>
        </div>
      </header>

      <div className="relative flex min-h-0 flex-1">
        {noticeText && (
          <div role="status"
               className="motion-fade pointer-events-none absolute bottom-4 left-1/2 z-40 max-w-[85%] -translate-x-1/2 rounded-full border border-border bg-surface px-4 py-2 text-center text-xs text-fg shadow-lg">
            {noticeText}
          </div>
        )}
        {!player.state.fullscreen && (
          <nav aria-label={lang === "en" ? "Course slides" : "课件列表"} className="classroom-slide-list w-44 shrink-0 overflow-y-auto border-r border-border bg-surface p-3">
            <p className="mb-3 px-1 text-xs font-medium text-muted">{lang === "en" ? "Choose a slide" : "选择课件页"} <span className="tnum ml-1">{slides.length}</span></p>
            <ol className="space-y-3">{slides.map((slide) => <li key={slide.slide_id}>
              <button disabled={Boolean(checkpointDue) || suspended || qa.asking} type="button" aria-current={slideOrder === slide.order ? "true" : undefined}
                onClick={() => { qa.stopAudio(); player.gotoSlide(slide.order); }}
                className={`w-full rounded-xl border p-1.5 text-left transition-colors disabled:opacity-50 ${slideOrder === slide.order ? "border-accent/50 bg-accent-soft/40" : "border-transparent hover:bg-surface-hover"}`}>
                <SlideThumbnail slide={slide} themeId={detail?.revision?.brief?.theme_id ?? "academic_clear@2"} />
                <span className="mt-2 flex gap-2 px-1 text-[11px] leading-5 text-fg-secondary"><span className="tnum text-muted">{String(slide.order).padStart(2, "0")}</span><span className="truncate">{slide.title}</span></span>
              </button>
            </li>)}</ol>
          </nav>
        )}
        <main className="relative flex min-w-0 flex-1 flex-col">
          <div className="classroom-stage relative flex min-h-0 flex-1 items-center justify-center overflow-hidden">
            {frameHtml ? (
              <SlideFrameDefault
                ref={frameRef} html={frameHtml}
                title={detail?.title ?? "classroom"}
                onHotkey={handlePlayerKey}
                className="classroom-slide-frame h-full w-full border-0" />
            ) : (
              <div className="flex items-center gap-2 text-sm text-white/60">
                <MonitorPlay size={18} />
                {ps("cls.play.loading")}
              </div>
            )}

            {suspended && (
              <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 bg-bg/80 px-6 backdrop-blur-sm">
                <p className="text-sm text-fg">
                  {player.state.error
                    ? ps("cls.play.suspended.error")
                    : ps("cls.play.suspended")}
                </p>
                {!player.state.error && (
                  <p className="max-w-sm text-center text-xs text-fg-secondary">
                    {ps("cls.play.suspended.hint")}
                  </p>
                )}
                <Button variant="primary" size="sm" className="min-h-11 px-5 text-sm"
                        onClick={() => {
                          if (player.state.error) window.location.reload();
                          else void player.takeover();
                        }}>
                  {player.state.error
                    ? ps("cls.play.suspended.retry")
                    : ps("cls.play.suspended.takeover")}
                </Button>
              </div>
            )}
            {ended && (
              <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 bg-bg/80 px-6 backdrop-blur-sm">
                <p className="text-base font-medium text-fg">
                  {ps("cls.play.ended")}
                </p>
                {summary && (
                  <div className="max-w-md rounded-[12px] border border-border bg-surface px-4 py-3 text-[0.78rem] text-fg-secondary">
                    <p className="tnum">
                      {ps("cls.sum.slides")
                        .replace("%n", String(summary.slides?.visited ?? 0))
                        .replace("%t", String(summary.slides?.total ?? 0))}
                      {" · "}
                      {ps("cls.sum.segments")
                        .replace("%n", String(summary.segments?.listened ?? 0))
                        .replace("%t", String(summary.segments?.total ?? 0))}
                    </p>
                    <p className="tnum mt-1">
                      {ps("cls.sum.asked")
                        .replace("%n", String(summary.questions_asked?.count ?? 0))}
                      {" · "}
                      {ps("cls.sum.checkpoints")
                        .replace("%n", String((summary.checkpoints?.items ?? [])
                          .filter((c) => c.state === "answered").length))}
                    </p>
                    <p className="mt-1 text-muted">
                      {summary.mastery?.note}
                    </p>
                  </div>
                )}
                <div className="flex items-center gap-2">
                  <Link href={lessonHref}>
                    <Button variant="outline" size="sm" className="min-h-11 px-5 text-sm">
                      {ps("cls.play.back.course")}
                    </Button>
                  </Link>
                </div>
              </div>
            )}
            {player.state.error === "click_to_resume"
              && status === "paused" && (
              <button type="button" onClick={startPlayback}
                      className="absolute bottom-4 left-1/2 z-10 -translate-x-1/2 rounded-full bg-accent px-5 py-2.5 text-sm font-medium text-on-accent shadow-lg">
                ▶ {ps("cls.play.click.resume")}
              </button>
            )}

            {!player.state.fullscreen && ["ready", "paused", "paused_text"].includes(status) && !checkpointDue && !player.state.captions && (
              <Button onClick={startPlayback} icon={<Play size={15} />} className="absolute bottom-8 left-1/2 z-10 -translate-x-1/2 !rounded-full px-6 shadow-lg">
                {lang === "en" ? "Start from this slide" : "从本页开始上课"}
              </Button>
            )}
          <CaptionBar
            current={player.current} next={player.next}
            captions={player.state.captions}
            followPaused={player.state.captionFollowPaused}
            onUserScroll={(paused) => player.setSettings(
              { type: "caption_follow", paused })}
            s={playerStrings} />
            {/* 检查点（§13.1）：本页最后一段结束后出现 */}
            {checkpointDue && !ended && !suspended && (
              <CheckpointPanel
                workspaceId={workspaceId}
                lessonId={lessonId}
                runId={runId}
                checkpointId={checkpointDue}
                onResolved={() => player.resolveCheckpoint()}
                s={{
                  reflectTitle: ps("cls.ckp.reflect"),
                  reflectContinue: ps("cls.ckp.continue"),
                  reflectThinkMore: ps("cls.ckp.think"),
                  questionTitle: ps("cls.ckp.question"),
                  skip: ps("cls.ckp.skip"),
                  skipped: ps("cls.ckp.skipped"),
                }} />
            )}
          </div>

        </main>

        <aside className="classroom-side-panel relative flex w-[340px] shrink-0 flex-col border-l border-border bg-surface" aria-label={lang === "en" ? "Classroom sidebar" : "课堂侧栏"}>
          <div className="flex h-14 shrink-0 items-center border-b border-border px-3">
            <div className="classroom-tool-tabs flex w-full gap-1" role="tablist" aria-label={lang === "en" ? "Classroom tools" : "课堂工具"}>
              {(["chat", "script", "notes"] as const).map((tab) => <button type="button" role="tab" aria-selected={sidePanel === tab} key={tab} onClick={() => { if (tab !== "chat") qa.stopAudio(); setSidePanel(tab); }} className={`inline-flex flex-1 items-center justify-center gap-1.5 rounded-lg px-2 py-2 text-xs font-medium ${sidePanel === tab ? "bg-accent-soft text-accent-strong" : "text-muted hover:bg-surface-hover"}`}>{tab === "chat" ? <MessageCircle size={14} /> : tab === "script" ? <BookOpenText size={14} /> : <NotebookPen size={14} />}{tab === "chat" ? (lang === "en" ? "Chat" : "对话") : tab === "notes" ? ps("cls.note.title") : (lang === "en" ? "Script" : "课程讲稿")}</button>)}
            </div>
          </div>
          <div className={sidePanel === "chat" ? "min-h-0 flex-1" : "hidden"}>
              <QuestionDrawer embedded disabled={suspended || ended || Boolean(checkpointDue) || status === "loading"}
                turns={qa.turns}
                asking={qa.asking}
                sttLang={lang === "en" ? "en" : "zh"}
                onMicPress={() => {
                  qa.stopAudio();
                  player.pauseForAsk();
                }}
                initialDraft={questionDraft}
                onAsk={(text) => {
                  const draftId = questionDraftIdRef.current;
                  if (draftId) {
                    questionDraftIdRef.current = null;
                    consumeAssistantDraft(draftId,
                      { kind: "classroom_question", id: text.slice(0, 64) })
                      .catch(() => undefined);
                  }
                  qa.ask(text);
                }}
                onResume={() => { resumeFromAnchor(); setSidePanel("chat"); }}
                onResumeFromPage={() => { resumeFromPage(); setSidePanel("chat"); }}
                onClose={() => { qa.stopAudio(); setSidePanel("chat"); }}
                s={{
                  title: ps("cls.ask.title"),
                  confused: ps("cls.ask.confused"),
                  example: ps("cls.ask.example"),
                  placeholder: ps("cls.ask.placeholder"),
                  send: ps("cls.ask.send"),
                  thinking: ps("cls.ask.thinking"),
                  resume: ps("cls.ask.resume"),
                  resumeFromPage: ps("cls.ask.resume.page"),
                  failed: ps("cls.ask.failed"),
                  close: ps("cls.ask.close"),
                  micHold: ps("cls.ask.mic.hold"),
                  micRecording: ps("cls.ask.mic.recording"),
                }} />
          </div>
          <div className={sidePanel === "script" ? "min-h-0 flex-1 overflow-y-auto p-4" : "hidden"}>
            {(
              <ScriptPanel
                active={sidePanel === "script"}
                disabled={suspended || ended || Boolean(checkpointDue) || qa.asking || status === "loading"}
                segments={player.segments} slideTitles={slideTitles}
                slideOrders={slideOrders} current={player.current}
                onSelect={(segmentId) => { if (!player.state.fullscreen) toggleFullscreen(); player.gotoSegment(segmentId, true); }}
                s={{ pageOf: (order) => ps("cls.script.page").replace("%n", String(order)), follow: ps("cls.script.follow"), play: lang === "en" ? "Play this page" : "从本页开始听", hint: lang === "en" ? "Expand a page to read its full script" : "按页展开，查看完整讲稿" }} />
            )}
          </div>
          <div className={sidePanel === "notes" ? "min-h-0 flex-1 overflow-y-auto p-4" : "hidden"}>
            {(
              <ClassroomNotes
                workspaceId={workspaceId}
                lessonId={lessonId}
                runId={runId}
                current={player.current}
                s={{
                  title: ps("cls.note.title"),
                  addHere: ps("cls.note.add"),
                  placeholder: ps("cls.note.placeholder"),
                  save: ps("cls.note.add"),
                  saved: ps("cls.note.saved"),
                  saveToCenter: ps("cls.note.to.center"),
                  savedToCenter: ps("cls.note.to.center.done"),
                  empty: ps("cls.note.empty"),
                  excerpt: ps("cls.note.excerpt"),
                }} />
            )}
            </div>
        </aside>
      </div>

      <PlayerControls
        blocked={Boolean(checkpointDue) || qa.asking}
        state={player.state} current={player.current} segments={player.segments}
        totalPages={slideOrders.length || 1}
        pageTitle={slideTitles.get(slideOrder)}
        textMode={player.textMode}
        onToggle={startPlayback}
        onPrev={() => player.gotoSlide(slideOrder - 1)}
        onNext={() => player.gotoSlide(slideOrder + 1)}
        onReplay={() => { if (!player.state.fullscreen) toggleFullscreen(); player.replaySegment(); }}
        onSpeed={(rate) => {
          player.setSettings({ type: "settings", playbackRate: rate });
          writePlayerPrefs({ volume: player.state.volume,
                             playbackRate: rate });
        }}
        onVolume={(v) => {
          player.setSettings({ type: "settings", volume: v });
          writePlayerPrefs({ volume: v,
                             playbackRate: player.state.playbackRate });
        }}
        onCaptions={() => player.setSettings({ type: "settings", captions: !player.state.captions })}
        onSeek={(segmentId) => player.gotoSegment(segmentId, false)}
        onFullscreen={toggleFullscreen}
        voicePanel={(
          <VoicePanel
            workspaceId={workspaceId}
            run={player.run}
            onApply={player.applyAudioProfile}
            onPreviewStart={() => {
              if (player.state.status === "playing"
                  || player.state.status === "buffering") {
                player.togglePlay();
              }
            }}
            onOpenChange={setVoiceOpen} />
        )}
        s={playerStrings} />
    </div>
  );
}
