"use client";
/* 课堂播放器页（plan.md §3.1/§5/§12，阶段 G）。
 *
 * 布局：导航 | 章节目录 | 幻灯片舞台+字幕 | 讲稿（折叠） + 底部控制条。
 * 键盘 Space/←/→/C/F（输入框聚焦时不响应）；后台标签暂停；lease 丢失
 * 显示“在这里继续”接管；恢复音频需用户点击（§5.4）；reduced-motion 由
 * 全局 motion 类承担；<640px 由 CSS 折叠目录与右栏。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  ArrowLeft, CircleHelp, Lightbulb, LogOut, MessageCircle, MonitorPlay,
} from "lucide-react";
import { getLesson, getRevisionFrame } from "@/lib/api-classroom";
import type { LessonDetailPublic } from "@/lib/types-classroom.generated";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { Button } from "@/components/ui/Button";
import { WorkspaceModeBar } from "@/components/classroom/WorkspaceModeBar";
import SlideFrameDefault, {
  type SlideFrameHandle,
} from "@/components/classroom/SlideFrame";
import {
  CaptionBar, PlayerControls, SlideOutline, type PlayerStrings,
} from "@/components/classroom/player/PlayerWidgets";
import {
  QuestionDrawer,
} from "@/components/classroom/player/QuestionDrawer";
import { useClassroomPlayer } from "@/lib/classroom/useClassroomPlayer";
import {
  QUICK_CONFUSED, QUICK_EXAMPLE, useClassroomQA,
} from "@/lib/classroom/useClassroomQA";
import { STRINGS as PAGE_STRINGS } from "../../../strings";

const PLAYER_STR = {
  zh: {
    "cls.play.play": "播放", "cls.play.pause": "暂停",
    "cls.play.prev": "上一页", "cls.play.next": "下一页",
    "cls.play.replay": "本段重讲",
    "cls.play.captions": "字幕", "cls.play.captions.back": "回到当前讲解",
    "cls.play.outline": "章节目录", "cls.play.volume": "音量",
    "cls.play.mute": "静音", "cls.play.speed": "语速",
    "cls.play.fullscreen": "全屏", "cls.play.exit.fullscreen": "退出全屏",
    "cls.play.reading": "阅读模式", "cls.play.exit.reading": "退出阅读模式",
    "cls.play.textmode": "文字课堂", "cls.play.click.resume": "点击继续播放",
    "cls.ask.title": "课堂提问",
    "cls.ask.confused": "没听懂",
    "cls.ask.example": "举个例子",
    "cls.ask.open": "提问",
    "cls.ask.placeholder": "关于当前页的问题…（Enter 发送）",
    "cls.ask.send": "发送",
    "cls.ask.thinking": "正在讲解…",
    "cls.ask.resume": "继续原课",
    "cls.ask.resume.page": "从本页开始",
    "cls.ask.failed": "回答失败，请重试。",
    "cls.ask.close": "关闭提问",
    "cls.ask.hint": "提问会暂停讲授；回答结束后点击「继续原课」回到原位置。",
    "cls.play.suspended": "本课堂正在其他设备播放",
    "cls.play.suspended.takeover": "在这里继续",
    "cls.play.ended": "本节课已完成",
    "cls.play.back.course": "返回课程",
    "cls.play.exit": "退出课堂", "cls.play.loading": "正在装载课堂…",
    "cls.play.pageof": "%n / %t",
    "cls.play.segmentof": "第 %n 段 / 共 %t 段",
  },
  en: {
    "cls.play.play": "Play", "cls.play.pause": "Pause",
    "cls.play.prev": "Previous page", "cls.play.next": "Next page",
    "cls.play.replay": "Replay segment",
    "cls.play.captions": "Captions", "cls.play.captions.back": "Back to current",
    "cls.play.outline": "Outline", "cls.play.volume": "Volume",
    "cls.play.mute": "Mute", "cls.play.speed": "Speed",
    "cls.play.fullscreen": "Fullscreen",
    "cls.play.exit.fullscreen": "Exit fullscreen",
    "cls.play.reading": "Reading mode",
    "cls.play.exit.reading": "Exit reading mode",
    "cls.play.textmode": "Text-only lesson",
    "cls.play.click.resume": "Click to resume playback",
    "cls.ask.title": "Ask in class",
    "cls.ask.confused": "I'm lost",
    "cls.ask.example": "Give an example",
    "cls.ask.open": "Ask",
    "cls.ask.placeholder": "Ask about this page… (Enter to send)",
    "cls.ask.send": "Send",
    "cls.ask.thinking": "Explaining…",
    "cls.ask.resume": "Resume lesson",
    "cls.ask.resume.page": "From page start",
    "cls.ask.failed": "Answer failed, please retry.",
    "cls.ask.close": "Close questions",
    "cls.ask.hint": "Asking pauses the lesson; click “Resume lesson” afterwards.",
    "cls.play.suspended": "This lesson is playing on another device",
    "cls.play.suspended.takeover": "Continue here",
    "cls.play.ended": "Lesson completed",
    "cls.play.back.course": "Back to lesson",
    "cls.play.exit": "Exit lesson", "cls.play.loading": "Loading lesson…",
    "cls.play.pageof": "%n / %t",
    "cls.play.segmentof": "Segment %n of %t",
  },
} as const;

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
  const [frameHtml, setFrameHtml] = useState<string | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);
  const [askOpen, setAskOpen] = useState(false);
  const frameRef = useRef<SlideFrameHandle | null>(null);
  const shellRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const d = await getLesson(workspaceId, lessonId);
        if (!alive) return;
        setDetail(d);
        const rev = d.revision?.revision;
        if (rev) {
          const html = await getRevisionFrame(workspaceId, lessonId, rev,
                                              "presentation");
          if (alive) setFrameHtml(html);
        }
      } catch (err) {
        if (alive) setFatal(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => { alive = false; };
  }, [workspaceId, lessonId]);

  const player = useClassroomPlayer({
    workspaceId, lessonId, runId,
    detail: detail ?? ({ revision: null } as unknown as LessonDetailPublic),
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
  const resumeFromAnchor = useCallback(() => {
    qa.stopAudio();
    const segId = anchorSegmentId ?? player.current?.segmentId;
    if (segId) player.gotoSegment(segId, true);
    player.setSettings({ type: "status", status: "paused" });
  }, [qa, anchorSegmentId, player]);
  const resumeFromPage = useCallback(() => {
    qa.stopAudio();
    const anchorSlide = anchorSegmentId
      ? player.segments.find((s) => s.segmentId === anchorSegmentId)?.slideId
      : player.current?.slideId;
    const first = player.segments.find((s) => s.slideId === anchorSlide)
      ?? player.segments[0];
    if (first) player.gotoSegment(first.segmentId, true);
    player.setSettings({ type: "status", status: "paused" });
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
  useEffect(() => {
    frameRef.current?.gotoPage(slideOrder);
  }, [slideOrder]);
  useEffect(() => {
    const show = currentSeg?.showBlockIds ?? [];
    const focus = currentSeg?.focusBlockIds ?? [];
    frameRef.current?.setBlockState(show.length ? show : ["*"], focus);
  }, [currentSeg, slideOrder]);

  // 阅读模式（§5.2.4）：进入即暂停讲授、课件重排为可读版；退出后由用户
  // 点击播放恢复，不自动续播（§5.2.6）
  const toggleReading = useCallback((on: boolean) => {
    if (on) {
      if (player.state.status === "playing"
          || player.state.status === "buffering") {
        player.togglePlay();
      }
    }
    player.setSettings({ type: "settings", readingMode: on });
    frameRef.current?.setReading(on);
  }, [player]);

  const toggleFullscreen = useCallback(() => {
    const el = shellRef.current;
    if (!el) return;
    if (document.fullscreenElement) {
      void document.exitFullscreen().catch(() => undefined);
    } else {
      void el.requestFullscreen().catch(() => {
        // 浏览器不支持/拒绝全屏：退应用内专注布局（§5.2.10）
        document.documentElement.classList.toggle("player-focus-fallback");
      });
    }
  }, []);
  useEffect(() => {
    const onFs = () => player.setSettings({
      type: "settings", fullscreen: document.fullscreenElement != null,
    });
    document.addEventListener("fullscreenchange", onFs);
    return () => document.removeEventListener("fullscreenchange", onFs);
  }, [player]);

  // 键盘 Space/←/→/C/F：输入框聚焦时不响应（§5.3）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA"
                || t.isContentEditable || t.tagName === "SELECT")) return;
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      switch (e.key) {
        case " ":
          e.preventDefault();
          player.togglePlay();
          break;
        case "ArrowRight":
          player.nextSegment();
          break;
        case "ArrowLeft":
          player.prevSegment();
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
        case "q":
        case "Q":
          setAskOpen(true);
          break;
        default:
          break;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [player, toggleFullscreen]);

  const playerStrings: PlayerStrings = {
    captionsLabel: ps("cls.play.captions"),
    captionsBackToCurrent: ps("cls.play.captions.back"),
    outlineTitle: ps("cls.play.outline"),
    play: ps("cls.play.play"), pause: ps("cls.play.pause"),
    prevPage: ps("cls.play.prev"), nextPage: ps("cls.play.next"),
    replay: ps("cls.play.replay"), volume: ps("cls.play.volume"),
    mute: ps("cls.play.mute"), speed: ps("cls.play.speed"),
    focusMode: ps("cls.play.fullscreen"),
    fullscreen: ps("cls.play.fullscreen"),
    exitFullscreen: ps("cls.play.exit.fullscreen"),
    readingMode: ps("cls.play.reading"),
    exitReadingMode: ps("cls.play.exit.reading"),
    pageOf: (n, t) => ps("cls.play.pageof").replace("%n", String(n))
      .replace("%t", String(t)),
    segmentOf: (n, t) => ps("cls.play.segmentof").replace("%n", String(n))
      .replace("%t", String(t)),
    textMode: ps("cls.play.textmode"),
    bufferingSlow: ps("cls.play.click.resume"),
    remaining: (m) => `${m} min`,
  };

  if (fatal) {
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
        <div className="flex flex-1 items-center justify-center text-muted">
          {fatal}
        </div>
      </div>
    );
  }

  const status = player.state.status;
  const suspended = status === "suspended";
  const ended = status === "ended";

  return (
    <div ref={shellRef} className="flex h-full min-h-0 flex-col bg-bg">
      <header className="flex items-center gap-3 border-b border-border bg-surface px-4 py-2.5">
        <Link href={lessonHref} aria-label={tr("cls.back.to.list")}
              className="flex h-8 w-8 items-center justify-center rounded-[8px] text-muted hover:bg-surface-hover hover:text-fg">
          <ArrowLeft size={16} />
        </Link>
        <h1 className="min-w-0 truncate font-serif text-[1.02rem] font-bold tracking-tight text-fg">
          {detail?.title ?? tr("cls.learn.player.title")}
        </h1>
        <div className="ml-auto flex items-center gap-2">
          <WorkspaceModeBar workspaceId={workspaceId} mode="classroom" />
          <Button variant="ghost" size="sm"
                  icon={<LogOut size={14} />}
                  onClick={() => {
                    player.endLesson();
                    router.push(lessonHref);
                  }}>
            {ps("cls.play.exit")}
          </Button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <SlideOutline
          segments={player.segments} slideTitles={slideTitles}
          slideOrders={slideOrders} current={player.current}
          onSelect={player.gotoSlide} s={playerStrings} />

        <main className="relative flex min-w-0 flex-1 flex-col">
          <div className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden bg-[#2A2A2E]">
            {frameHtml ? (
              <SlideFrameDefault
                ref={frameRef} html={frameHtml}
                title={detail?.title ?? "classroom"}
                className="h-full w-full border-0" />
            ) : (
              <div className="flex items-center gap-2 text-sm text-white/60">
                <MonitorPlay size={18} />
                {ps("cls.play.loading")}
              </div>
            )}

            {suspended && (
              <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 bg-bg/80 backdrop-blur-sm">
                <p className="text-sm text-fg">{ps("cls.play.suspended")}</p>
                <Button variant="primary" size="sm"
                        onClick={() => window.location.reload()}>
                  {ps("cls.play.suspended.takeover")}
                </Button>
              </div>
            )}
            {ended && (
              <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 bg-bg/80 backdrop-blur-sm">
                <p className="text-base font-medium text-fg">
                  {ps("cls.play.ended")}
                </p>
                <Link href={lessonHref}>
                  <Button variant="outline" size="sm">
                    {ps("cls.play.back.course")}
                  </Button>
                </Link>
              </div>
            )}
            {player.state.error === "click_to_resume"
              && status === "paused" && (
              <button type="button" onClick={player.togglePlay}
                      className="absolute bottom-4 left-1/2 z-10 -translate-x-1/2 rounded-full bg-accent px-5 py-2.5 text-sm font-medium text-on-accent shadow-lg">
                ▶ {ps("cls.play.click.resume")}
              </button>
            )}

            {/* 问答抽屉：在当前页旁展开（§5.1），提问区不是聊天瀑布 */}
            {askOpen && (
              <QuestionDrawer
                turns={qa.turns}
                asking={qa.asking}
                onAsk={(text) => qa.ask(text)}
                onResume={resumeFromAnchor}
                onResumeFromPage={resumeFromPage}
                onClose={() => { qa.stopAudio(); setAskOpen(false); }}
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
                }} />
            )}
          </div>

          <CaptionBar
            current={player.current} next={player.next}
            captions={player.state.captions}
            followPaused={player.state.captionFollowPaused}
            onUserScroll={(paused) => player.setSettings(
              { type: "caption_follow", paused })}
            s={playerStrings} />
        </main>

        {/* 讲稿侧栏（默认展开为可滚动全稿；来源/提问/笔记在 H 阶段接入） */}
        <aside className="hidden w-[320px] shrink-0 flex-col border-l border-border bg-surface xl:flex">
          <div className="border-b border-border px-4 py-2.5 text-sm font-medium text-fg">
            {tr("cls.learn.script")}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
            <ol className="space-y-3 text-[15px] leading-relaxed">
              {player.segments.map((seg) => (
                <li key={seg.segmentId}
                    className={seg.seq === player.current?.seq
                      ? "rounded-[8px] bg-accent-soft px-2.5 py-2 text-fg"
                      : "px-2.5 py-1.5 text-muted"}>
                  {seg.spokenText}
                </li>
              ))}
            </ol>
          </div>
        </aside>
      </div>

      {/* §5.1 第二行：快捷补讲 + 提问（真实用户操作表达，§12.6） */}
      <div className="flex flex-wrap items-center gap-2 border-t border-border bg-surface px-4 py-1.5">
        <button type="button" disabled={qa.asking || ended}
                onClick={() => { setAskOpen(true); qa.ask(QUICK_CONFUSED); }}
                className="inline-flex h-11 cursor-pointer items-center gap-1.5 rounded-full border border-border px-3.5 text-xs text-fg-secondary transition-colors hover:border-accent/50 hover:text-fg disabled:opacity-50">
          <CircleHelp size={13} /> {ps("cls.ask.confused")}
        </button>
        <button type="button" disabled={qa.asking || ended}
                onClick={() => { setAskOpen(true); qa.ask(QUICK_EXAMPLE); }}
                className="inline-flex h-11 cursor-pointer items-center gap-1.5 rounded-full border border-border px-3.5 text-xs text-fg-secondary transition-colors hover:border-accent/50 hover:text-fg disabled:opacity-50">
          <Lightbulb size={13} /> {ps("cls.ask.example")}
        </button>
        <button type="button" disabled={ended}
                onClick={() => setAskOpen(true)}
                aria-pressed={askOpen}
                className={`inline-flex h-11 cursor-pointer items-center gap-1.5 rounded-full border px-3.5 text-xs transition-colors disabled:opacity-50 ${
                  askOpen
                    ? "border-accent bg-accent-soft/50 text-accent-strong"
                    : "border-border text-fg-secondary hover:border-accent/50 hover:text-fg"}`}>
          <MessageCircle size={13} /> {ps("cls.ask.open")}
        </button>
        <span className="ml-auto hidden text-[0.68rem] text-muted/70 md:inline">
          {ps("cls.ask.hint")}
        </span>
      </div>

      <PlayerControls
        state={player.state} current={player.current}
        totalPages={slideOrders.length || 1}
        textMode={player.textMode}
        onToggle={player.togglePlay}
        onPrev={player.prevSegment}
        onNext={player.nextSegment}
        onReplay={player.replaySegment}
        onSpeed={(rate) => player.setSettings(
          { type: "settings", playbackRate: rate })}
        onVolume={(v) => player.setSettings({ type: "settings", volume: v })}
        onReading={toggleReading}
        onFullscreen={toggleFullscreen}
        s={playerStrings} />
    </div>
  );
}
