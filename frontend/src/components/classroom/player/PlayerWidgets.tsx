"use client";
/* 课堂播放器部件：字幕条 / 章节目录 / 控制条（plan.md §5.1–§5.3，G02/G04）。
 *
 * 舞台是视觉中心；字幕默认开（两行、18px 起、独立区域，换段淡入 + 下段
 * 预告）；目录 200–240px；控制条承载页/段/速度/音量（悬停滑杆）/语音源/
 * 专注/全屏，[没听懂][举个例子][提问] 在页面层快捷行（与抽屉共享预设）。
 * <640px 固定“播放/提问/目录/更多”四主操作（页面层处理）；reduced-motion
 * 与低刺激模式关闭动效；主触控目标 ≥44px；色彩不是唯一状态信号。
 */
import { useEffect, useRef, useState, type CSSProperties } from "react";
import {
  BookOpenText, ChevronLeft, ChevronRight, Maximize2, Minimize2,
  MoreHorizontal, Pause, Play, RotateCcw, Volume2, VolumeX, Captions, Loader2, Check,
} from "lucide-react";
import { Markdown } from "@/components/chat/markdown";
import { FIELD_CLS } from "@/components/ui/Input";
import { cn } from "@/lib/cn";
import type { FlatSegment } from "@/lib/classroom/useClassroomPlayer";
import type { PlayerState } from "@/lib/classroom/player-reducer";

export interface PlayerStrings {
  captionsLabel: string;
  captionsBackToCurrent: string;
  outlineTitle: string;
  play: string;
  pause: string;
  prevPage: string;
  nextPage: string;
  replay: string;
  volume: string;
  mute: string;
  unmute: string;
  progress: string;
  estimated: string;
  speed: string;
  focusMode: string;
  fullscreen: string;
  exitFullscreen: string;
  readingMode: string;
  exitReadingMode: string;
  pageOf: (n: number, total: number) => string;
  segmentOf: (n: number, total: number) => string;
  textMode: string;
  continueReading: string;
  moreSettings: string;
  bufferingSlow: string;
  remaining: (mins: number) => string;
}

export function CaptionBar(
  { current, next, captions, followPaused, onUserScroll, s }:
  {
    current: FlatSegment | null;
    next: FlatSegment | null;
    captions: boolean;
    followPaused: boolean;
    onUserScroll: (paused: boolean) => void;
    s: PlayerStrings;
  },
) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const atBottomRef = useRef(true);

  useEffect(() => {
    const el = boxRef.current;
    if (!el || followPaused || !atBottomRef.current) return;
    el.scrollTop = 0; // 新段从头阅读，避免长讲稿直接滚到末尾
  }, [current?.segmentId, next?.segmentId, followPaused]);

  if (!captions) return null;
  return (
    <section
      aria-label={s.captionsLabel}
      className="classroom-caption relative border-t border-border-light bg-surface px-5 py-3 sm:px-7"
    >
      <div className="mx-auto mb-1.5 flex max-w-4xl items-center gap-1.5 text-[10px] font-medium tracking-wider text-accent-strong"><Captions size={13} />{s.captionsLabel}</div>
      <div
        ref={boxRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 8;
          atBottomRef.current = atBottom;
          onUserScroll(!atBottom);
        }}
        className="mx-auto max-w-4xl overflow-y-auto"
        style={{ maxHeight: "5.5rem" }}
      >
        {current && (
          // key=segmentId：换段重挂载触发 motion-fade 淡入（reduced-motion 全局关闭）
          <div key={current.segmentId} className="motion-fade" aria-live="polite"><Markdown className="chat-prose classroom-prose classroom-caption-prose">{current.displayText || current.spokenText}</Markdown></div>
        )}
      </div>
      {next && (
        // 下一段预告：固定一行弱化展示，不占字幕滚动区（跟随逻辑不变）
        <p aria-hidden="true"
           className="mx-auto mt-1 max-w-4xl truncate text-[0.78rem] leading-snug text-muted">
          {next.spokenText}
        </p>
      )}
      {followPaused && (
        <button
          type="button"
          onClick={() => {
            atBottomRef.current = true;
            onUserScroll(false);
            const el = boxRef.current;
            if (el) el.scrollTop = el.scrollHeight;
          }}
          className="absolute right-3 top-1.5 rounded-full border border-border bg-surface px-2.5 py-0.5 text-xs text-muted hover:text-fg"
        >
          {s.captionsBackToCurrent}
        </button>
      )}
    </section>
  );
}

export function SlideOutline(
  { segments, slideTitles, slideOrders, current, onSelect, s }:
  {
    segments: FlatSegment[];
    slideTitles: Map<number, string>;
    slideOrders: number[];
    current: FlatSegment | null;
    onSelect: (order: number) => void;
    s: PlayerStrings;
  },
) {
  return (
    <nav aria-label={s.outlineTitle}
         className="h-full w-full overflow-y-auto bg-surface">
      <ol className="space-y-2">
        {slideOrders.map((order, idx) => {
          const active = current?.slideOrder === order;
          const seconds = Math.round(segments.filter((seg) => seg.slideOrder === order).reduce((n, seg) => n + seg.estimatedMs, 0) / 1000);
          return (
            <li key={order}>
              <button
                type="button"
                onClick={() => onSelect(order)}
                aria-current={active ? "true" : undefined}
                className={`flex w-full items-center gap-3 rounded-xl border border-transparent px-3 py-3.5 text-left text-sm transition-colors ${
                  active
                    ? "border-accent/20 bg-accent-soft/50 font-medium text-fg"
                    : "text-muted hover:bg-surface-hover hover:text-fg"}`}
              >
                <span className={`inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs ${
                  active ? "bg-accent text-on-accent"
                    : "border border-border text-muted"}`}>
                  {current && order < current.slideOrder ? <Check size={12} /> : String(idx + 1).padStart(2, "0")}
                </span>
                <span className="min-w-0 flex-1"><span className="block truncate text-xs leading-5">{slideTitles.get(order) ?? "—"}</span><span className="tnum mt-1 block text-[10px] font-normal text-muted">{Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, "0")}</span></span>
              </button>
            </li>
          );
        })}
      </ol>
      <p className="mt-3 px-2.5 text-xs text-muted">
        {s.segmentOf((current?.seq ?? 0) + 1, segments.length)}
      </p>
    </nav>
  );
}

const SPEED_STEPS = [0.5, 0.75, 0.9, 1.0, 1.25, 1.5];

export function PlayerControls({ state, current, segments, totalPages, textMode, onToggle, onPrev, onNext,
  onReplay, onSpeed, onVolume, onReading, onFullscreen, onCaptions, onSeek, voicePanel, className, s }: {
  state: PlayerState; current: FlatSegment | null; segments: FlatSegment[]; totalPages: number; textMode: boolean;
  onToggle: () => void; onPrev: () => void; onNext: () => void; onReplay: () => void;
  onSpeed: (rate: number) => void; onVolume: (volume: number) => void;
  onReading: (on: boolean) => void; onFullscreen: () => void; onCaptions: () => void;
  onSeek: (id: string) => void; voicePanel?: React.ReactNode; className?: string; s: PlayerStrings;
}) {
  const playing = state.status === "playing" || state.status === "buffering";
  const [moreOpen, setMoreOpen] = useState(false);
  const lastNonZeroVolRef = useRef(state.volume > 0 ? state.volume : 1);
  useEffect(() => { if (state.volume > 0) lastNonZeroVolRef.current = state.volume; }, [state.volume]);
  useEffect(() => {
    if (!moreOpen) return;
    const close = (e: KeyboardEvent) => { if (e.key === "Escape") setMoreOpen(false); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [moreOpen]);
  const toggleMute = () => onVolume(state.volume === 0 ? lastNonZeroVolRef.current : 0);
  const volumePct = Math.round(state.volume * 100);
  const disabled = !current || ["loading", "suspended", "ended"].includes(state.status);
  const remainingMs = segments.slice(current?.seq ?? 0).reduce((n, seg) => n + seg.estimatedMs, 0) / state.playbackRate;
  const volume = <div className="flex items-center gap-1"><CtlButton label={state.volume === 0 ? s.unmute : s.mute} onClick={toggleMute}>{state.volume === 0 ? <VolumeX size={17} /> : <Volume2 size={17} />}</CtlButton><input type="range" min={0} max={100} value={volumePct} aria-label={s.volume} style={{ "--range-progress": `${volumePct}%` } as CSSProperties} onChange={(e) => onVolume(Number(e.target.value) / 100)} className="classroom-range w-20" /><span className="tnum w-8 text-right text-[10px] text-muted">{volumePct}%</span></div>;
  const speed = <select aria-label={s.speed} value={state.playbackRate} onChange={(e) => onSpeed(Number(e.target.value))} className={cn(FIELD_CLS, "!h-9 !w-20 !rounded-lg !px-2 !text-xs")}>{SPEED_STEPS.map((rate) => <option key={rate} value={rate}>{rate}×</option>)}</select>;
  return (
    <footer className={cn("classroom-controls relative shrink-0 border-t border-border bg-surface", className)}>
      <div className="px-4 pt-2 sm:px-6">
        <div className="mb-1 flex items-center justify-between text-[10px] text-muted"><span>{s.segmentOf((current?.seq ?? 0) + 1, segments.length)}</span><span>{s.estimated.replace("%n", String(Math.max(1, Math.ceil(remainingMs / 60000))))}</span></div>
        <input type="range" min={0} max={Math.max(1, segments.length - 1)} value={current?.seq ?? 0} disabled={disabled || segments.length < 2} aria-label={s.progress} style={{ "--range-progress": `${((current?.seq ?? 0) / Math.max(1, segments.length - 1)) * 100}%` } as CSSProperties} aria-valuetext={s.segmentOf((current?.seq ?? 0) + 1, segments.length)} onChange={(e) => { const seg = segments[Number(e.target.value)]; if (seg) onSeek(seg.segmentId); }} className="classroom-range w-full" />
      </div>
      <div className="flex items-center gap-1 px-2 pb-3 pt-1 sm:gap-2 sm:px-5" role="toolbar" aria-label={s.play}>
        <CtlButton label={s.prevPage} onClick={onPrev} disabled={disabled || (current?.slideOrder ?? 1) <= 1}><ChevronLeft size={18} /></CtlButton>
        <CtlButton label={playing ? s.pause : textMode ? s.continueReading : s.play} onClick={onToggle} disabled={disabled} primary>{state.status === "buffering" ? <Loader2 size={20} className="animate-spin" /> : playing ? <Pause size={20} fill="currentColor" /> : textMode ? <ChevronRight size={22} /> : <Play size={19} fill="currentColor" />}</CtlButton>
        <CtlButton label={s.nextPage} onClick={onNext} disabled={disabled || (current?.slideOrder ?? 1) >= totalPages}><ChevronRight size={18} /></CtlButton>
        <span className="player-desktop-control"><CtlButton label={s.replay} onClick={onReplay} disabled={disabled}><RotateCcw size={16} /></CtlButton></span>
        <span className="tnum ml-1 whitespace-nowrap text-[11px] text-fg-secondary">{current?.slideOrder ?? 1}<span className="mx-1.5 text-muted/50">/</span>{totalPages}</span>
        <div className="player-desktop-control ml-auto items-center gap-3">
          {speed}{volume}
          <span className="mx-1 h-5 w-px bg-border" />
          <CtlButton label={s.captionsLabel} onClick={onCaptions} active={state.captions}><Captions size={18} /></CtlButton>
          <CtlButton label={state.readingMode ? s.exitReadingMode : s.readingMode} onClick={() => onReading(!state.readingMode)} active={state.readingMode}><BookOpenText size={17} /></CtlButton>
        </div>
        <div className="player-voice-slot ml-auto flex items-center">{voicePanel}</div>
        <CtlButton label={state.fullscreen ? s.exitFullscreen : s.fullscreen} onClick={onFullscreen}>{state.fullscreen ? <Minimize2 size={17} /> : <Maximize2 size={17} />}</CtlButton>
        <button type="button" className="player-mobile-control classroom-header-action shrink-0" aria-label={s.moreSettings} aria-expanded={moreOpen} onClick={() => setMoreOpen((v) => !v)}><MoreHorizontal size={20} /></button>
      </div>
      {moreOpen && <><div className="fixed inset-0 z-30" onClick={() => setMoreOpen(false)} /><div className="motion-pop absolute bottom-full right-3 z-40 mb-2 w-64 rounded-2xl border border-border bg-surface p-4 shadow-lg">
        <div className="mb-2 flex items-center justify-between gap-2 text-xs text-muted"><span>{s.speed}</span>{speed}</div>
        <div className="mb-3 flex items-center justify-between gap-2 text-xs text-muted"><span>{s.volume}</span>{volume}</div>
        <button type="button" className="classroom-more-action w-full" onClick={onCaptions}>{s.captionsLabel} {state.captions && "✓"}</button>
        <button type="button" className="classroom-more-action w-full" onClick={() => { onReading(!state.readingMode); setMoreOpen(false); }}>{state.readingMode ? s.exitReadingMode : s.readingMode}</button>
        <button type="button" className="classroom-more-action w-full" onClick={() => { onReplay(); setMoreOpen(false); }}>{s.replay}</button>
      </div></>}
    </footer>
  );
}

function CtlButton(
  { label, onClick, disabled, primary, active, children }:
  {
    label: string;
    onClick: () => void;
    disabled?: boolean;
    primary?: boolean;
    active?: boolean;
    children: React.ReactNode;
  },
) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      aria-pressed={active}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex h-11 w-11 shrink-0 cursor-pointer items-center justify-center rounded-xl transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-40 disabled:hover:bg-transparent ${
        primary
          ? "!rounded-full bg-accent text-white shadow-sm hover:bg-accent-strong"
          : active
            ? "bg-accent-soft text-fg"
            : "text-muted hover:bg-surface-hover hover:text-fg"}`}
    >
      {children}
    </button>
  );
}
