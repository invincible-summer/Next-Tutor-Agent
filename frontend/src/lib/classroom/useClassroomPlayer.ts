/* 课堂播放器编排 hook（plan.md §12/§5，阶段 G03）。
 *
 * 职责：加载 run+课件 → lease 心跳(15s)/接管 → 音频段请求（当前+后2，
 * 轮询 0.5/1/2s 退避，后台暂停轮询）→ 进度节流上报（5s + 段结束/跳页/
 * 暂停/退出立即 flush；pagehide keepalive）→ 播放状态机驱动 → 后台标签
 * 暂停 → 断点恢复（从段开头重讲）。段完成只信 audio ended 事件（§12.2）。
 * 语音不可用时进入 paused_text 文字课堂，可完整读讲稿完成（G 退出门）；
 * 课中可经 applyAudioProfile 切换语音源（CAS + token 递增作废旧音色缓存）。
 *
 * 函数按依赖顺序声明（编译器规则禁止前向引用）；跨 effect 的回调经
 * ref 间接调用。
 */
"use client";
import { t } from "@/lib/i18n";
import { useUIStore } from "@/lib/store";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  acquireLease, ClassroomApiError, fetchClipBlobUrl, getRun,
  requestRunAudio, renewLease, releaseLease, updateAudioProfile,
  updateProgress, type RunPublicExtra,
} from "@/lib/api-classroom";
import type {
  LessonDetailPublic, VoicePolicy,
} from "@/lib/types-classroom.generated";
import {
  acquireAudioFocus, releaseAudioFocus,
} from "@/lib/classroom/audio-focus";
import {
  ClassroomAudioController, type SegmentAudio,
} from "@/lib/classroom/audio-controller";
import {
  bumpToken, initialPlayerState, makeToken, playerReducer, tokenEquals,
  type GenerationToken, type PlayerAction, type PlayerState,
} from "@/lib/classroom/player-reducer";

const HEARTBEAT_MS = 15_000;
const PROGRESS_TICK_MS = 5_000;
const POLL_BACKOFF_MS = [500, 1000, 2000];
const POLL_MAX_MS = 60_000;
const AUDIO_WINDOW = 3;   // 当前段+后2（§11.4）
const CLIENT_ID_KEY = "edu-agent-classroom-client-id";
const CLIENT_ID_RE = /^cl-[A-Za-z0-9-]{6,61}$/;  // 全长 9–64（后端约束）
let fallbackClientId = "";

/** 播放控制者标识（§5.4）：每个标签页一个、刷新后保持不变的随机 id。
 * sessionStorage 按标签页隔离且关闭即弃——同标签页刷新沿用同一 id 无缝
 * 续约，新标签页/其他设备得到不同 id，“接管判定 client_id 是否相同”由
 * 此可靠。clientId 只在 effects 里进入请求，SSR 期间返回占位值不参与
 * 水合。存储不可用（隐私模式）时退化为本次页面加载内的随机值。 */
function playbackClientId(): string {
  if (typeof window === "undefined") return "cl-ssr";
  try {
    let id = window.sessionStorage.getItem(CLIENT_ID_KEY) ?? "";
    if (!CLIENT_ID_RE.test(id)) {
      const uuid = typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID()
        : `${Math.random().toString(36).slice(2, 12)}${Date.now().toString(36)}`;
      id = `cl-${uuid}`;
      window.sessionStorage.setItem(CLIENT_ID_KEY, id);
    }
    return id;
  } catch {
    if (!fallbackClientId) {
      fallbackClientId = `cl-${Math.random().toString(36).slice(2, 14)}`;
    }
    return fallbackClientId;
  }
}

export interface FlatSegment {
  seq: number;              // 全课 0-based
  slideOrder: number;       // 1-based 页序
  slideId: string;
  segmentId: string;
  spokenText: string;
  displayText: string;
  pauseAfterMs: number;
  estimatedMs: number;
  showBlockIds: string[];
  focusBlockIds: string[];
}

export function flattenSegments(detail: LessonDetailPublic): FlatSegment[] {
  const out: FlatSegment[] = [];
  const slides = [...(detail.revision?.slides ?? [])]
    .sort((a, b) => a.order - b.order);
  for (const slide of slides) {
    for (const seg of slide.segments) {
      out.push({
        seq: out.length, slideOrder: slide.order, slideId: slide.slide_id,
        segmentId: seg.segment_id, spokenText: seg.spoken_text,
        displayText: seg.display_text,
        pauseAfterMs: seg.pause_after_ms ?? 0,
        estimatedMs: seg.estimated_ms ?? 0,
        showBlockIds: seg.show_block_ids ?? [],
        focusBlockIds: seg.focus_block_ids ?? [],
      });
    }
  }
  return out;
}

interface PlayerDeps {
  workspaceId: string;
  lessonId: string;
  runId: string;
  detail: LessonDetailPublic;
  initialRun?: RunPublicExtra | null;
}

export interface PlayerApi {
  state: PlayerState;
  segments: FlatSegment[];
  run: RunPublicExtra | null;
  current: FlatSegment | null;
  next: FlatSegment | null;
  textMode: boolean;
  clientId: string;
  pendingCheckpoint: string | null;
  resolveCheckpoint: () => void;
  takeover: () => Promise<void>;
  togglePlay: () => void;
  nextSegment: () => void;
  prevSegment: () => void;
  gotoSlide: (order: number) => void;
  /** 跳到任意段（问答恢复：从打断段/本页开头重讲）。 */
  gotoSegment: (segmentId: string, autoPlay: boolean) => void;
  replaySegment: () => void;
  /** 插问打断：立即停声并进入 asking（§12.1 playing→asking）。 */
  pauseForAsk: () => void;
  setSettings: (action: PlayerAction) => void;
  /** 课中切换语音源（§11.1/§14.2 PUT audio-profile，CAS 冲突对齐后报 notice）。
   * 成功返回 true；新档案无 provider 进文字课堂，原播放中则用新音色重讲当前段。 */
  applyAudioProfile: (prefs: { policy: VoicePolicy; voice_id?: string | null;
    allow_local_fallback?: boolean }) => Promise<boolean>;
  endLesson: () => void;
  leaveLesson: () => Promise<void>;
  clipUrl: (segmentId: string) => string | null;
}

export function useClassroomPlayer(
  { workspaceId, lessonId, runId, detail, initialRun }: PlayerDeps,
): PlayerApi {
  const segments = useMemo(() => flattenSegments(detail), [detail]);
  const baseRevision = detail.revision?.revision ?? 1;
  const [state, dispatch] = useState<PlayerState | null>(null);
  const [run, setRun] = useState<RunPublicExtra | null>(null);
  const [textMode, setTextMode] = useState(false);
  const [pendingCheckpoint, setPendingCheckpoint] = useState<string | null>(null);
  const pendingCheckpointRef = useRef<string | null>(null);
  const resolvedCheckpointsRef = useRef<Set<string>>(new Set());
  const clientId = useMemo(() => playbackClientId(), []);
  const checkpoints = useMemo(() => new Map(
    (detail.revision?.slides ?? []).flatMap((slide) => {
      const block = slide.blocks.find((item) => item.kind === "checkpoint");
      return block && "checkpoint_id" in block
        ? [[slide.slide_id, block.checkpoint_id] as const] : [];
    })), [detail]);

  const runRef = useRef<RunPublicExtra | null>(null);
  const stateRef = useRef<PlayerState | null>(null);
  const controllerRef = useRef<ClassroomAudioController | null>(null);
  const blobUrlsRef = useRef(new Map<string, string>());
  const leaseEpochRef = useRef(0);
  const tokenRef = useRef<GenerationToken | null>(null);
  const lastProgressRef = useRef(0);
  const eventSeqRef = useRef(0);
  const eventSessionRef = useRef("");
  const playedRef = useRef<Set<string>>(new Set());
  const lastCompletedRef = useRef<string | null>(null);
  const playedSkippedRef = useRef<Set<string>>(new Set());
  const pendingProgressRef = useRef(false);
  const advanceRef = useRef<((fromEnded: boolean,
    token: GenerationToken) => void) | null>(null);
  const flushRef = useRef<((action: "progress" | "pause" | "complete"
    | "end", target?: FlatSegment) => void) | null>(null);

  const setStateBoth = useCallback((action: PlayerAction) => {
    dispatch((prev) => {
      const next = prev ?? initialPlayerState(
        tokenRef.current ?? makeToken(runId, baseRevision, 1));
      const reduced = playerReducer(next, action);
      stateRef.current = reduced;
      return reduced;
    });
  }, [runId, baseRevision]);

  // ---- 进度上报（§12.3：5s 节流 + 关键点 flush + pagehide keepalive） ----
  const sendProgress = useCallback(async (
    action: "progress" | "pause" | "complete" | "end",
    target?: FlatSegment, keepalive = false,
  ) => {
    const cur = stateRef.current;
    const run0 = runRef.current;
    if (!cur || !run0) return;
    const seg = target ?? segments[cur.segmentSeq];
    if (!eventSessionRef.current) {
      eventSessionRef.current = typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID()
        : Math.random().toString(36).slice(2, 14);
    }
    eventSeqRef.current += 1;
    const body = {
      expected_state_revision: run0.state_revision,
      client_event_id: `ev-${clientId}-${eventSessionRef.current}-${eventSeqRef.current}`,
      client_seq: eventSeqRef.current,
      lease_epoch: leaseEpochRef.current,
      action,
      cursor: { slide_id: seg.slideId, segment_id: seg.segmentId,
                chunk_index: 0, offset_ms: Math.round(
                  Math.min(lastProgressRef.current, seg.estimatedMs)),
                last_completed_segment_id: lastCompletedRef.current },
      played_segment_ids: [...playedRef.current].slice(-48),
      skipped_slide_ids: [...playedSkippedRef.current].slice(-24),
    };
    try {
      if (keepalive) {
        const { apiFetch } = await import("@/lib/api-fetch");
        const { API_BASE } = await import("@/lib/api");
        await apiFetch(
          `${API_BASE}/workspaces/${encodeURIComponent(workspaceId)}` +
          `/classroom/lessons/${encodeURIComponent(lessonId)}` +
          `/runs/${encodeURIComponent(runId)}/progress`,
          { method: "PUT", keepalive: true,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body) });
      } else {
        const res = await updateProgress(workspaceId, lessonId, runId, body);
        runRef.current = { ...run0, state_revision: res.state_revision };
      }
    } catch (err) {
      if (err instanceof ClassroomApiError
          && (err.code === "revision_conflict"
              || err.code === "lease_conflict")) {
        // 冲突：重读 run 对齐（不丢弃本地播放位置）
        try {
          runRef.current = await getRun(workspaceId, lessonId, runId);
        } catch { /* 离线时下轮再对齐 */ }
      }
    }
  }, [segments, clientId, workspaceId, lessonId, runId]);

  const flushProgress = useCallback((action: "progress" | "pause"
                                     | "complete" | "end",
                                     target?: FlatSegment) => {
    void sendProgress(action, target);
  }, [sendProgress]);
  useEffect(() => {
    flushRef.current = flushProgress;
  }, [flushProgress]);

  // ---- 音频（当前+后2 段一次 POST；pending 才退避轮询） --------------------
  const loadBlob = useCallback(async (clipId: string, segmentId: string,
                                      token: GenerationToken) => {
    const cached = blobUrlsRef.current.get(segmentId);
    if (cached) return cached;
    try {
      const url = await fetchClipBlobUrl(workspaceId, lessonId, runId, clipId);
      if (!tokenEquals(token, tokenRef.current)) {
        URL.revokeObjectURL(url);
        return null;
      }
      blobUrlsRef.current.set(segmentId, url);
      return url;
    } catch {
      return null;
    }
  }, [workspaceId, lessonId, runId]);

  const pollUntilReady = useCallback((clipId: string,
                                      token: GenerationToken,
                                      seg: FlatSegment) => {
    if (!clipId) return;
    const started = Date.now();
    let attempt = 0;
    const tick = async () => {
      if (!tokenEquals(token, tokenRef.current)) return;
      if (document.hidden) {
        window.setTimeout(tick, 5000);
        return;
      }
      if (Date.now() - started > POLL_MAX_MS) {
        // 语音准备较慢（§5.2.11）：转入文字继续，不无限旋转
        setTextMode(true);
        setStateBoth({ type: "status", status: "paused_text" });
        return;
      }
      try {
        const { getClipStatus } = await import("@/lib/api-classroom");
        const status = await getClipStatus(workspaceId, lessonId, runId,
                                           clipId);
        if (!tokenEquals(token, tokenRef.current)) return;
        if (status.state === "ready") {
          const url = await loadBlob(clipId, seg.segmentId, token);
          const controller = controllerRef.current;
          if (!url || !controller
              || !tokenEquals(token, tokenRef.current)) return;
          try {
            await controller.play(
              { segmentId: seg.segmentId, url,
                durationMs: seg.estimatedMs }, null, token);
            setStateBoth({ type: "status", status: "playing" });
          } catch { /* play_rejected 由 onError 处理 */ }
          return;
        }
        if (status.state === "failed") {
          setTextMode(true);
          setStateBoth({ type: "status", status: "paused_text" });
          return;
        }
      } catch { /* 网络抖动继续轮询 */ }
      const delay = POLL_BACKOFF_MS[Math.min(attempt,
                                             POLL_BACKOFF_MS.length - 1)];
      attempt += 1;
      window.setTimeout(tick, delay);
    };
    void tick();
  }, [workspaceId, lessonId, runId, loadBlob, setStateBoth]);

  const ensureAudioAndPlay = useCallback(async (
    seg: FlatSegment, token: GenerationToken,
  ) => {
    const controller = controllerRef.current;
    if (!controller) return;
    const ids = segments.slice(seg.seq, seg.seq + AUDIO_WINDOW)
      .map((s) => s.segmentId);
    let clips: { clip_id: string; state: string }[] = [];
    try {
      const res = await requestRunAudio(workspaceId, lessonId, runId, {
        segment_ids: ids, lease_epoch: leaseEpochRef.current,
      }, `aud-${runId}-${seg.seq}-${token.playbackEpoch}`);
      clips = (res.clips ?? []).map((c) => ({
        clip_id: c.clip_id, state: String(c.state) }));
    } catch (err) {
      if (err instanceof ClassroomApiError
          && (err.code === "voice_unavailable" || err.code === "audio_busy"
              || err.status >= 500 || err.status === 429)) {
        setTextMode(true);
        setStateBoth({ type: "status", status: "paused_text" });
        return;
      }
      throw err;
    }
    if (!tokenEquals(token, tokenRef.current)) return;   // 迟到响应丢弃
    const first = clips[0] ?? null;
    if (!first || first.state !== "ready") {
      pollUntilReady(first?.clip_id ?? "", token, seg);
      setStateBoth({ type: "status", status: "buffering" });
      return;
    }
    const url = await loadBlob(first.clip_id, seg.segmentId, token);
    if (url === null || !tokenEquals(token, tokenRef.current)) return;
    let next: SegmentAudio | null = null;
    const second = clips[1] ?? null;
    if (second && second.state === "ready") {
      const nextSeg = segments[seg.seq + 1];
      if (nextSeg) {
        const nurl = await loadBlob(second.clip_id, nextSeg.segmentId, token);
        if (nurl) next = { segmentId: nextSeg.segmentId, url: nurl,
                           durationMs: nextSeg.estimatedMs };
      }
    }
    const cur0 = stateRef.current;
    const offset = cur0 && cur0.cursor.segmentId === seg.segmentId
      ? cur0.cursor.offsetMs : 0;
    try {
      await controller.play(
        { segmentId: seg.segmentId, url, durationMs: seg.estimatedMs },
        next, token, offset);
      setStateBoth({ type: "status", status: "playing" });
    } catch { /* play_rejected 已由 onError 处理 */ }
  }, [segments, workspaceId, lessonId, runId, loadBlob, pollUntilReady,
       setStateBoth]);

  // ---- 段推进（ended 事件驱动；epoch 递增丢弃旧回调） --------------------
  const advance = useCallback((fromEnded: boolean, token: GenerationToken) => {
    const cur = stateRef.current;
    if (!cur || !tokenEquals(token, tokenRef.current)) return;
    const seq = cur.segmentSeq;
    const currentSeg = segments[seq];
    if (!currentSeg) return;
    if (fromEnded) lastCompletedRef.current = currentSeg.segmentId;
    const isLastOnSlide = seq + 1 >= segments.length
      || segments[seq + 1].slideId !== currentSeg.slideId;
    const checkpointId = checkpoints.get(currentSeg.slideId);
    if ((fromEnded || textMode) && isLastOnSlide && checkpointId
        && !resolvedCheckpointsRef.current.has(checkpointId)) {
      controllerRef.current?.pause();
      pendingCheckpointRef.current = checkpointId;
      setPendingCheckpoint(checkpointId);
      setStateBoth({ type: "status", status: "paused" });
      flushRef.current?.("pause", currentSeg);
      return;
    }
    if (seq + 1 >= segments.length) {
      if (!fromEnded) controllerRef.current?.abort();
      flushRef.current?.("complete");   // 服务端据实际听完的段判断 listened/browsed
      controllerRef.current?.pause();
      setStateBoth({ type: "status", status: "ended" });
      return;
    }
    const nextSeg = segments[seq + 1];
    if (!fromEnded) {
      controllerRef.current?.abort();
      if (nextSeg.slideId !== currentSeg.slideId
          && lastCompletedRef.current !== currentSeg.segmentId) {
        playedSkippedRef.current.add(currentSeg.slideId);
      }
    }
    const newToken = bumpToken(tokenRef.current ?? token);
    tokenRef.current = newToken;
    controllerRef.current?.setToken(newToken);
    setStateBoth({
      type: "seek", slideOrder: nextSeg.slideOrder, segmentSeq: nextSeg.seq,
      cursor: { slideId: nextSeg.slideId, segmentId: nextSeg.segmentId,
                offsetMs: 0 },
      token: newToken,
    });
    flushRef.current?.("progress", nextSeg);
    if (textMode) {
      setStateBoth({ type: "status", status: "paused_text" });
    } else {
      void ensureAudioAndPlay(nextSeg, newToken);
    }
  }, [segments, checkpoints, textMode, ensureAudioAndPlay, setStateBoth]);

  useEffect(() => {
    advanceRef.current = advance;
  }, [advance]);

  // ---- 控制器（一次性）：ended 只由媒体事件触发 ---------------------------
  useEffect(() => {
    const controller = new ClassroomAudioController({
      onEnded: (segmentId, token) => {
        if (!tokenEquals(token, tokenRef.current)) return;
        playedRef.current.add(segmentId);
        advanceRef.current?.(true, token);
      },
      onBuffering: (_sid, token) => {
        if (!tokenEquals(token, tokenRef.current)) return;
        setStateBoth({ type: "status", status: "buffering" });
      },
      onTime: (_segmentId, positionMs, token) => {
        if (!tokenEquals(token, tokenRef.current)) return;
        lastProgressRef.current = positionMs;
      },
      onError: (segmentId, message, token) => {
        if (!tokenEquals(token, tokenRef.current)) return;
        void segmentId;
        if (message === "play_rejected") {
          setStateBoth({ type: "status", status: "paused",
                         error: "click_to_resume" });
          return;
        }
        // 音频失败不隐藏讲稿（§5.2.13）：退回文字课堂继续
        setTextMode(true);
        setStateBoth({ type: "status", status: "paused_text" });
      },
    });
    controllerRef.current = controller;
    acquireAudioFocus(`classroom-${runId}`, () => {
      controller.pause();
      const cur = stateRef.current;
      if (cur && cur.status === "playing") {
        setStateBoth({ type: "status", status: "paused" });
      }
    });
    const urls = blobUrlsRef.current;
    return () => {
      releaseAudioFocus(`classroom-${runId}`);
      controller.abort();
      for (const url of Array.from(urls.values())) {
        URL.revokeObjectURL(url);
      }
      urls.clear();
    };
  }, [runId, setStateBoth]);

  // 5s 节流 tick + pagehide best-effort
  useEffect(() => {
    const id = window.setInterval(() => {
      const cur = stateRef.current;
      if (cur && cur.status === "playing" && !pendingProgressRef.current) {
        pendingProgressRef.current = true;
        void sendProgress("progress").finally(() => {
          pendingProgressRef.current = false;
        });
      }
    }, PROGRESS_TICK_MS);
    const onHide = () => {
      void sendProgress("pause", undefined, true);
    };
    window.addEventListener("pagehide", onHide);
    return () => {
      window.clearInterval(id);
      window.removeEventListener("pagehide", onHide);
    };
  }, [sendProgress]);

  // ---- 初始化：load run → lease → 从段开头恢复 ---------------------------
  useEffect(() => {
    if (!initialRun || !segments.length || !detail.revision) return;
    let cancelled = false;
    void (async () => {
      try {
        const loaded = initialRun;
        if (loaded.lesson_revision !== detail.revision?.revision) {
          throw new Error(t(useUIStore.getState().lang, "classroom.error.revision"));
        }
        if (cancelled) return;
        runRef.current = loaded;
        setRun(loaded);
        let epoch = 0;
        try {
          const resp = await acquireLease(workspaceId, lessonId, runId, {
            client_id: clientId,
            takeover: false,
          });
          epoch = resp.lease_epoch;
        } catch (err) {
          if (err instanceof ClassroomApiError
              && err.code === "lease_conflict") {
            setStateBoth({ type: "status", status: "suspended" });
            return;
          }
          throw err;
        }
        leaseEpochRef.current = epoch;
        const token = makeToken(runId, loaded.lesson_revision, epoch);
        tokenRef.current = token;
        controllerRef.current?.setToken(token);
        // 恢复从段开头重讲（§5.2.8）：不使用半句 offset
        const seg = segments[Math.min(loaded.cursor_index,
                                      Math.max(0, segments.length - 1))]
          ?? segments[0];
        setStateBoth({
          type: "loaded", token, slideOrder: seg.slideOrder,
          segmentSeq: seg.seq,
          cursor: { slideId: seg.slideId, segmentId: seg.segmentId,
                    offsetMs: 0 },
          notice: loaded.tts_fallback_notified
            ? "local_fallback_notice" : null,
        });
        const provider = String(
          (loaded.audio_profile as { provider?: string }).provider ?? "");
        if (!provider) setTextMode(true);
      } catch (err) {
        setStateBoth({ type: "status", status: "suspended",
                       error: String(err) });
      }
    })();
    return () => { cancelled = true; };
  }, [workspaceId, lessonId, runId, clientId, segments, setStateBoth,
      initialRun, detail.revision]);

  // lease 心跳 15s（§5.4）；被接管 → 停声挂起
  useEffect(() => {
    const id = window.setInterval(() => {
      if (!leaseEpochRef.current) return;
      void renewLease(workspaceId, lessonId, runId, {
        client_id: clientId, lease_epoch: leaseEpochRef.current,
      }).catch(async (err: unknown) => {
        if (err instanceof ClassroomApiError
            && err.code === "lease_conflict") {
          controllerRef.current?.pause();
          const cur = stateRef.current;
          if (cur && cur.status !== "ended") {
            setStateBoth({ type: "status", status: "suspended" });
          }
          return;
        }
        try {
          const resp = await acquireLease(workspaceId, lessonId, runId, {
            client_id: clientId, takeover: false });
          leaseEpochRef.current = resp.lease_epoch;
          const token = makeToken(runId, runRef.current?.lesson_revision ?? 1,
                                  resp.lease_epoch);
          tokenRef.current = token;
          controllerRef.current?.setToken(token);
        } catch { /* 离线：下轮心跳再试 */ }
      });
    }, HEARTBEAT_MS);
    return () => window.clearInterval(id);
  }, [workspaceId, lessonId, runId, clientId, setStateBoth]);

  // 后台标签暂停（§5.2.9）
  useEffect(() => {
    const onVisibility = () => {
      if (document.hidden) {
        const cur = stateRef.current;
        if (cur && cur.status === "playing") {
          controllerRef.current?.pause();
          setStateBoth({ type: "status", status: "paused" });
          flushRef.current?.("pause");
        }
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange",
                                              onVisibility);
  }, [setStateBoth]);

  // ---- 用户操作 ------------------------------------------------------------
  const jumpTo = useCallback((seg: FlatSegment, autoPlay: boolean) => {
    if (pendingCheckpointRef.current) return;
    const previous = stateRef.current
      ? segments[stateRef.current.segmentSeq] : null;
    if (previous && seg.slideOrder > previous.slideOrder) {
      const completedPage = lastCompletedRef.current === previous.segmentId
        && (previous.seq + 1 >= segments.length
            || segments[previous.seq + 1].slideId !== previous.slideId);
      if (!completedPage) {
        playedSkippedRef.current.add(previous.slideId);
      }
      for (const bypassed of segments) {
        if (bypassed.slideOrder > previous.slideOrder
            && bypassed.slideOrder < seg.slideOrder) {
          playedSkippedRef.current.add(bypassed.slideId);
        }
      }
    }
    const token = bumpToken(tokenRef.current
                            ?? makeToken(runId, baseRevision, 1));
    tokenRef.current = token;
    controllerRef.current?.abort();
    controllerRef.current?.setToken(token);
    setStateBoth({
      type: "seek", slideOrder: seg.slideOrder, segmentSeq: seg.seq,
      cursor: { slideId: seg.slideId, segmentId: seg.segmentId,
                offsetMs: 0 },
      token,
    });
    flushRef.current?.("progress", seg);
    if (autoPlay && !textMode) void ensureAudioAndPlay(seg, token);
  }, [textMode, ensureAudioAndPlay, setStateBoth, runId, baseRevision,
      segments]);

  const togglePlay = useCallback(() => {
    const cur = stateRef.current;
    if (!cur) return;
    if (cur.status === "playing" || cur.status === "buffering") {
      controllerRef.current?.pause();
      setStateBoth({ type: "status", status: "paused" });
      flushRef.current?.("pause");
      return;
    }
    if (cur.status === "ended") return;
    if (textMode || cur.status === "paused_text") {
      setStateBoth({ type: "status", status: "paused_text" });
      return;
    }
    const seg = segments[cur.segmentSeq];
    const token = tokenRef.current;
    if (seg && token) void ensureAudioAndPlay(seg, token);
  }, [segments, textMode, ensureAudioAndPlay, setStateBoth]);

  const current = state ? segments[state.segmentSeq] ?? null : null;
  const next = state ? segments[state.segmentSeq + 1] ?? null : null;

  // ---- 课中切换语音源：CAS 更新 run → bump token → 作废未播段缓存 -----------
  const applyAudioProfile = useCallback(async (prefs: {
    policy: VoicePolicy; voice_id?: string | null;
    allow_local_fallback?: boolean;
  }): Promise<boolean> => {
    const run0 = runRef.current;
    if (!run0) return false;
    let updated: RunPublicExtra;
    try {
      const oldProfile = run0.audio_profile as { playback_speed?: unknown };
      updated = await updateAudioProfile(workspaceId, lessonId, runId, {
        // CAS 守 state_revision（后端 AudioProfileRequest 字段名）
        expected_state_revision: run0.state_revision,
        voice_preferences: {
          policy: prefs.policy,
          voice_id: prefs.voice_id ?? "",
          allow_local_fallback: prefs.allow_local_fallback ?? true,
          // 不改动合成语速：缺省会回落 0.9，显式带上现值
          playback_speed: typeof oldProfile.playback_speed === "number"
            ? oldProfile.playback_speed : 0.9,
        },
      });
    } catch (err) {
      if (err instanceof ClassroomApiError
          && (err.status === 409 || err.code === "revision_conflict")) {
        // CAS 冲突：重读 run 对齐（同进度上报冲突路径），提示用户重试
        try {
          const fresh = await getRun(workspaceId, lessonId, runId);
          runRef.current = fresh;
          setRun(fresh);
        } catch { /* 离线时下轮操作再对齐 */ }
        setStateBoth({ type: "notice", notice: "voice_switch_conflict" });
        return false;
      }
      setStateBoth({ type: "notice", notice: "voice_switch_failed" });
      return false;
    }
    const cur = stateRef.current;
    runRef.current = updated;
    setRun(updated);
    const wasPlaying = Boolean(
      cur && (cur.status === "playing" || cur.status === "buffering"));
    // 旧音色的一切在途回调作废；当前及之后段改用新档案重新合成
    const newToken = bumpToken(tokenRef.current
                               ?? makeToken(runId, updated.lesson_revision,
                                            leaseEpochRef.current));
    tokenRef.current = newToken;
    controllerRef.current?.setToken(newToken);
    controllerRef.current?.abort();
    for (const [segId, url] of Array.from(blobUrlsRef.current)) {
      const seg = segments.find((s) => s.segmentId === segId);
      if (!cur || !seg || seg.seq >= cur.segmentSeq) {
        URL.revokeObjectURL(url);
        blobUrlsRef.current.delete(segId);
      }
    }
    const provider = String(
      (updated.audio_profile as { provider?: string }).provider ?? "");
    setTextMode(!provider);
    setStateBoth({ type: "notice", notice: "voice_switched" });
    if (!provider) {
      // 静音/无可用音色：同加载路径进文字课堂
      if (wasPlaying) setStateBoth({ type: "status", status: "paused_text" });
      return true;
    }
    if (cur?.status === "paused_text") {
      setStateBoth({ type: "status", status: "paused" });
    }
    if (wasPlaying && cur) {
      const seg = segments[cur.segmentSeq];
      if (seg) void ensureAudioAndPlay(seg, newToken);
    }
    return true;
  }, [workspaceId, lessonId, runId, segments, setStateBoth,
      ensureAudioAndPlay]);

  const api: PlayerApi = useMemo(() => ({
    state: state ?? initialPlayerState(makeToken(runId, baseRevision, 1)),
    segments, run, current, next, textMode, clientId, pendingCheckpoint,
    resolveCheckpoint: () => {
      if (pendingCheckpointRef.current) {
        resolvedCheckpointsRef.current.add(pendingCheckpointRef.current);
      }
      pendingCheckpointRef.current = null;
      setPendingCheckpoint(null);
      const token = tokenRef.current;
      if (token) advanceRef.current?.(false, token);
    },
    takeover: async () => {
      const response = await acquireLease(workspaceId, lessonId, runId, {
        client_id: clientId, takeover: true,
      });
      leaseEpochRef.current = response.lease_epoch;
      const token = makeToken(runId, runRef.current?.lesson_revision ?? baseRevision,
                              response.lease_epoch);
      tokenRef.current = token;
      controllerRef.current?.setToken(token);
      const cur = stateRef.current;
      if (cur) setStateBoth({ type: "loaded", token,
        slideOrder: cur.slideOrder, segmentSeq: cur.segmentSeq,
        cursor: cur.cursor });
    },
    togglePlay,
    nextSegment: () => {
      if (current && tokenRef.current) {
        advanceRef.current?.(false, tokenRef.current);
      }
    },
    prevSegment: () => {
      if (current) jumpTo(segments[current.seq - 1] ?? current, true);
    },
    gotoSlide: (order: number) => {
      const seg = segments.find((s) => s.slideOrder === order);
      if (seg) jumpTo(seg, stateRef.current?.status === "playing");
    },
    gotoSegment: (segmentId: string, autoPlay: boolean) => {
      const seg = segments.find((s) => s.segmentId === segmentId);
      if (seg) jumpTo(seg, autoPlay && !textMode);
    },
    pauseForAsk: () => {
      const cur = stateRef.current;
      controllerRef.current?.pause();
      if (cur && (cur.status === "playing" || cur.status === "buffering")) {
        setStateBoth({ type: "status", status: "asking" });
        flushRef.current?.("pause");
      }
    },
    replaySegment: () => { if (current) jumpTo(current, true); },
    applyAudioProfile,
    setSettings: (action) => {
      setStateBoth(action);
      if (action.type === "settings") {
        if (action.volume !== undefined) {
          controllerRef.current?.setVolume(action.volume);
        }
        if (action.playbackRate !== undefined) {
          controllerRef.current?.setPlaybackRate(action.playbackRate);
        }
      }
    },
    endLesson: () => {
      controllerRef.current?.pause();
      setStateBoth({ type: "status", status: "ended" });
      flushRef.current?.("end");
      if (leaseEpochRef.current) {
        void releaseLease(workspaceId, lessonId, runId, {
          client_id: clientId, lease_epoch: leaseEpochRef.current,
        }).catch(() => undefined);
      }
    },
    leaveLesson: async () => {
      controllerRef.current?.pause();
      await sendProgress("pause");
      if (leaseEpochRef.current) {
        await releaseLease(workspaceId, lessonId, runId, {
          client_id: clientId, lease_epoch: leaseEpochRef.current,
        }).catch(() => undefined);
        leaseEpochRef.current = 0;
      }
    },
    clipUrl: (segmentId: string) =>
      blobUrlsRef.current.get(segmentId) ?? null,
  }), [state, segments, run, current, next, textMode, clientId,
       pendingCheckpoint, togglePlay, jumpTo, applyAudioProfile, setStateBoth,
       sendProgress, workspaceId, lessonId, runId, baseRevision]);

  return api;
}

/** 供“开始/继续上课”按钮：创建/复用 run 并跳转播放页。
 *
 * resume_or_create 服务端天然幂等（复用未结束 run）；restart 每次点击
 * 都是明确的新 run，双击由调用方 UI 状态防抖。 */
export async function startLessonRun(
  workspaceId: string, lessonId: string,
  mode: "resume_or_create" | "restart" = "resume_or_create",
  lessonRevision?: number,
): Promise<string> {
  const { createRun: create } = await import("@/lib/api-classroom");
  const resp = await create(workspaceId, lessonId,
    { mode, lesson_revision: lessonRevision ?? null, voice_preferences: null },
    `run-${lessonId}-${Date.now().toString(36)}`);
  return resp.run_id;
}
