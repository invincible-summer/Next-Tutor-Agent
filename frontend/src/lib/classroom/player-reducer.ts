/* 课堂播放器状态机（plan.md §12.1/§12.2，阶段 G01）。
 *
 * 后端 run.status 只有 active/paused/completed/ended；浏览器临时状态更细。
 * generation token = {run_id, lesson_revision, lease_epoch, playback_epoch}：
 * 每次跳页/切段/打断/离开使 playback_epoch 递增；迟到音频、旧 ended、
 * 旧 SSE、旧问答完成事件 token 不匹配直接丢弃（§12.2）。
 * 页面卸载、音频错误、abort 都不得悄悄转成 completed。
 */
export type PlayerStatus =
  | "loading"
  | "ready"
  | "buffering"
  | "playing"
  | "paused"
  | "paused_text"
  | "asking"
  | "answering"
  | "suspended"
  | "ended";

export interface GenerationToken {
  runId: string;
  lessonRevision: number;
  leaseEpoch: number;
  playbackEpoch: number;
}

export interface PlayerCursor {
  slideId: string;
  segmentId: string;
  offsetMs: number;
}

export interface PlayerState {
  status: PlayerStatus;
  token: GenerationToken;
  slideOrder: number;          // 1-based
  segmentSeq: number;          // 全课 0-based 段序
  cursor: PlayerCursor;
  volume: number;              // 0–1
  playbackRate: number;        // 0.5–1.5（播放倍速，不重新计费）
  captions: boolean;
  lowStimulus: boolean;
  readingMode: boolean;
  fullscreen: boolean;
  notice: string | null;       // 一次性提示（如“已切换本地语音”）
  error: string | null;
  /** 用户手动滚动字幕后暂停自动跟随 */
  captionFollowPaused: boolean;
}

export interface SegmentRef {
  slideId: string;
  segmentId: string;
  spokenText: string;
  pauseAfterMs: number;
  estimatedMs: number;
}

export type PlayerAction =
  | { type: "loaded"; token: GenerationToken; slideOrder: number;
      segmentSeq: number; cursor: PlayerCursor; notice?: string | null }
  | { type: "status"; status: PlayerStatus; error?: string | null }
  | { type: "notice"; notice: string | null }
  | { type: "seek"; slideOrder: number; segmentSeq: number;
      cursor: PlayerCursor; token: GenerationToken }
  | { type: "settings"; volume?: number; playbackRate?: number;
      captions?: boolean; lowStimulus?: boolean; readingMode?: boolean;
      fullscreen?: boolean }
  | { type: "caption_follow"; paused: boolean }
  | { type: "lease"; token: GenerationToken };

export function makeToken(runId: string, lessonRevision: number,
                          leaseEpoch: number): GenerationToken {
  return { runId, lessonRevision, leaseEpoch, playbackEpoch: 1 };
}

/** 任何跳转/打断都走这里：token epoch 递增，旧回调全部作废。 */
export function bumpToken(token: GenerationToken): GenerationToken {
  return { ...token, playbackEpoch: token.playbackEpoch + 1 };
}

export function tokenEquals(a: GenerationToken | null,
                        b: GenerationToken | null): boolean {
  if (!a || !b) return false;
  return a.runId === b.runId && a.lessonRevision === b.lessonRevision
    && a.leaseEpoch === b.leaseEpoch
    && a.playbackEpoch === b.playbackEpoch;
}

export function initialPlayerState(token: GenerationToken): PlayerState {
  return {
    status: "loading",
    token,
    slideOrder: 1,
    segmentSeq: 0,
    cursor: { slideId: "", segmentId: "", offsetMs: 0 },
    volume: 1,
    playbackRate: 0.9,
    captions: true,
    lowStimulus: false,
    readingMode: false,
    fullscreen: false,
    notice: null,
    error: null,
    captionFollowPaused: false,
  };
}

const TERMINAL = new Set<PlayerStatus>(["ended"]);

export function playerReducer(
  state: PlayerState,
  action: PlayerAction,
): PlayerState {
  if (TERMINAL.has(state.status)) {
    // ended 之后只允许覆盖 notice/error 之外的显式恢复动作
    if (action.type !== "loaded" && action.type !== "notice") {
      return state;
    }
  }
  switch (action.type) {
    case "loaded":
      return {
        ...state,
        status: "ready",
        token: action.token,
        slideOrder: action.slideOrder,
        segmentSeq: action.segmentSeq,
        cursor: action.cursor,
        notice: action.notice ?? null,
        error: null,
      };
    case "status":
      return { ...state, status: action.status,
               error: action.error ?? state.error };
    case "notice":
      return { ...state, notice: action.notice };
    case "seek":
      return {
        ...state,
        token: action.token,
        slideOrder: action.slideOrder,
        segmentSeq: action.segmentSeq,
        cursor: action.cursor,
        captionFollowPaused: false,
        status: state.status === "playing" ? "buffering" : state.status,
      };
    case "settings":
      return {
        ...state,
        volume: action.volume ?? state.volume,
        playbackRate: action.playbackRate ?? state.playbackRate,
        captions: action.captions ?? state.captions,
        lowStimulus: action.lowStimulus ?? state.lowStimulus,
        readingMode: action.readingMode ?? state.readingMode,
        fullscreen: action.fullscreen ?? state.fullscreen,
      };
    case "caption_follow":
      return { ...state, captionFollowPaused: action.paused };
    case "lease":
      return { ...state, token: action.token };
    default:
      return state;
  }
}

/** §12.1 状态迁移合法性（供测试与防御式断言）。 */
export const ALLOWED_TRANSITIONS: Record<PlayerStatus, PlayerStatus[]> = {
  loading: ["ready", "suspended", "ended"],
  ready: ["buffering", "playing", "paused", "paused_text", "suspended",
          "ended"],
  buffering: ["playing", "paused", "paused_text", "suspended", "ended"],
  playing: ["paused", "asking", "buffering", "paused_text", "suspended",
            "ended"],
  paused: ["playing", "buffering", "paused_text", "suspended", "ended"],
  paused_text: ["playing", "paused", "suspended", "ended"],
  asking: ["answering", "paused", "suspended", "ended"],
  answering: ["paused", "suspended", "ended"],
  suspended: ["paused", "ready", "ended"],
  ended: [],
};

export function canTransition(from: PlayerStatus, to: PlayerStatus): boolean {
  return (ALLOWED_TRANSITIONS[from] ?? []).includes(to);
}
