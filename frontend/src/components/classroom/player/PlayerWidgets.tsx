"use client";
/* 课堂播放器部件：字幕条 / 章节目录 / 控制条（plan.md §5.1–§5.3，G02/G04）。
 *
 * 舞台是视觉中心；字幕默认开（两行、18px 起、独立区域）；目录 200–240px；
 * 控制条承载页/段/速度/音量/专注/全屏与 [没听懂][举个例子][提问]。
 * <640px 固定“播放/提问/目录/更多”四主操作（页面层处理）；reduced-motion
 * 与低刺激模式关闭动效；主触控目标 ≥44px；色彩不是唯一状态信号。
 */
import { useEffect, useRef } from "react";
import {
  BookOpenText, Captions, ChevronLeft, ChevronRight, Maximize2, Minimize2,
  Pause, Play, RotateCcw, Volume2, VolumeX, Zap,
} from "lucide-react";
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
  speed: string;
  focusMode: string;
  fullscreen: string;
  exitFullscreen: string;
  readingMode: string;
  exitReadingMode: string;
  pageOf: (n: number, total: number) => string;
  segmentOf: (n: number, total: number) => string;
  textMode: string;
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
    el.scrollTop = el.scrollHeight;   // 自动跟随；用户滚动后暂停
  }, [current?.segmentId, next?.segmentId, followPaused]);

  if (!captions) return null;
  return (
    <section
      aria-label={s.captionsLabel}
      className="relative border-t border-border bg-surface px-4 py-2.5"
    >
      <div
        ref={boxRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 8;
          atBottomRef.current = atBottom;
          onUserScroll(!atBottom);
        }}
        className="mx-auto max-w-3xl space-y-1.5 overflow-y-auto text-[18px] leading-relaxed"
        style={{ maxHeight: "7.5rem" }}
      >
        {current && (
          <p className="text-fg" aria-live="polite">{current.spokenText}</p>
        )}
        {next && (
          <p className="text-muted/70">{next.spokenText}</p>
        )}
      </div>
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
         className="hidden h-full w-[220px] shrink-0 overflow-y-auto border-r border-border bg-surface px-2 py-3 lg:block">
      <ol className="space-y-1">
        {slideOrders.map((order, idx) => {
          const active = current?.slideOrder === order;
          return (
            <li key={order}>
              <button
                type="button"
                onClick={() => onSelect(order)}
                aria-current={active ? "true" : undefined}
                className={`flex w-full items-center gap-2 rounded-[8px] px-2.5 py-2 text-left text-sm transition-colors ${
                  active
                    ? "bg-accent-soft font-medium text-fg"
                    : "text-muted hover:bg-surface-hover hover:text-fg"}`}
              >
                <span className={`inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs ${
                  active ? "bg-accent text-on-accent"
                    : "border border-border text-muted"}`}>
                  {idx + 1}
                </span>
                <span className="min-w-0 truncate">
                  {slideTitles.get(order) ?? `—`}
                </span>
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

export function PlayerControls(
  { state, current, totalPages, textMode, onToggle, onPrev, onNext,
    onReplay, onSpeed, onVolume, onReading, onFullscreen, s }:
  {
    state: PlayerState;
    current: FlatSegment | null;
    totalPages: number;
    textMode: boolean;
    onToggle: () => void;
    onPrev: () => void;
    onNext: () => void;
    onReplay: () => void;
    onSpeed: (rate: number) => void;
    onVolume: (volume: number) => void;
    onReading: (on: boolean) => void;
    onFullscreen: () => void;
    s: PlayerStrings;
  },
) {
  const playing = state.status === "playing" || state.status === "buffering";
  const speedIdx = SPEED_STEPS.indexOf(state.playbackRate);
  const nextSpeed = SPEED_STEPS[
    (speedIdx === -1 ? 2 : speedIdx + 1) % SPEED_STEPS.length];
  return (
    <footer className="flex flex-wrap items-center gap-2 border-t border-border bg-surface px-4 py-2.5"
            role="toolbar" aria-label="classroom-controls">
      <CtlButton label={s.prevPage} onClick={onPrev} disabled={current?.seq === 0}>
        <ChevronLeft size={18} />
      </CtlButton>
      <CtlButton label={playing ? s.pause : s.play} onClick={onToggle}
                 primary>
        {playing ? <Pause size={20} /> : <Play size={20} />}
      </CtlButton>
      <CtlButton label={s.nextPage} onClick={onNext}
                 disabled={current !== null
                   && current.seq >= 1e9 /* 由页面按总段数禁用 */}>
        <ChevronRight size={18} />
      </CtlButton>
      <CtlButton label={s.replay} onClick={onReplay}>
        <RotateCcw size={17} />
      </CtlButton>
      <span className="ml-1 min-w-[4.5rem] text-center text-sm text-muted">
        {s.pageOf(current?.slideOrder ?? 1, totalPages)}
      </span>
      {textMode && (
        <span className="rounded-full border border-border px-2.5 py-0.5 text-xs text-muted">
          {s.textMode}
        </span>
      )}
      <div className="ml-auto flex items-center gap-2">
        <CtlButton label={s.speed + " " + state.playbackRate}
                   onClick={() => onSpeed(nextSpeed)}>
          <span className="text-xs font-medium tabular-nums">
            {state.playbackRate.toFixed(2).replace(/0$/, "")}×
          </span>
        </CtlButton>
        <CtlButton label={state.volume === 0 ? s.mute : s.volume}
                   onClick={() => onVolume(state.volume === 0 ? 1 : 0)}>
          {state.volume === 0 ? <VolumeX size={17} /> : <Volume2 size={17} />}
        </CtlButton>
        <CtlButton label={state.readingMode ? s.exitReadingMode : s.readingMode}
                   onClick={() => onReading(!state.readingMode)}
                   active={state.readingMode}>
          <BookOpenText size={17} />
        </CtlButton>
        <CtlButton label={state.fullscreen ? s.exitFullscreen : s.fullscreen}
                   onClick={onFullscreen}>
          {state.fullscreen ? <Minimize2 size={17} /> : <Maximize2 size={17} />}
        </CtlButton>
      </div>
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
      className={`inline-flex h-11 w-11 items-center justify-center rounded-[10px] transition-colors disabled:opacity-40 disabled:hover:bg-transparent ${
        primary
          ? "bg-accent text-on-accent hover:bg-accent/85"
          : active
            ? "bg-accent-soft text-fg"
            : "text-muted hover:bg-surface-hover hover:text-fg"}`}
    >
      {children}
    </button>
  );
}

export const PLAYER_ICONS = { Captions, BookOpenText, Zap };
