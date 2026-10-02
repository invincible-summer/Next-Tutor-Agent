"use client";
/**
 * 语音通话 hook：push-to-talk 状态机 + 语音 WebSocket 协议客户端。
 *
 * 服务端协议见 backend/app/api/v1/voice.py：JSON 控制/事件帧 + 下行二进制帧
 * （输入侧只发送最终识别文本；下行
 * 每句一个 tts_start 元事件 + 44.1 kHz PCM16 帧 + tts_end）。播放端按到达
 * 顺序入队，AudioBuffer 按元事件携带的采样率创建（浏览器自动重采样到
 * 输出设备速率）。
 *
 * 沉浸式改版：本 hook 不再持有对话文本（旧版把字幕攒在弹窗里），而是把
 * 转写/回答增量通过回调交给宿主组件写入聊天 store——通话期间 chat 页面
 * 照常流式显示。tts_start 携带的原始句子（含 markdown 公式）暴露为
 * speakingSentence，供「板书」浮窗渲染 KaTeX。播放链路串了
 * DynamicsCompressor + makeup gain，与后端的逐句响度归一化（loudness.py）
 * 叠加，进一步抹平句间/句内音量起伏。
 *
 * 轮次在途时挂断进入「收尾」（drain）：音频立即停止，WS 保持存活继续把
 * answer_delta 写进聊天流直到 turn_end（服务端同样跑完并落盘）；超时/
 * 断网/服务端提前收线则兜底提交半截回答——聊天流在任何路径下都不允许
 * 悬空卡死。tool_start/tool_result 同样透传给宿主，通话中实时渲染
 * 题目卡与知识检索卡。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE, voiceTicket } from "@/lib/api";
import type { Lang } from "@/lib/i18n";

type SpeechRecognitionAlternativeLike = { transcript: string };
type SpeechRecognitionResultLike = {
  isFinal: boolean;
  length: number;
  [index: number]: SpeechRecognitionAlternativeLike;
};
type SpeechRecognitionResultListLike = {
  length: number;
  [index: number]: SpeechRecognitionResultLike;
};
type SpeechRecognitionEventLike = Event & {
  resultIndex: number;
  results: SpeechRecognitionResultListLike;
};
type SpeechRecognitionErrorEventLike = Event & {
  error: string;
  message?: string;
};
type BrowserSpeechRecognition = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEventLike) => void) | null;
  onend: (() => void) | null;
};
type BrowserSpeechRecognitionConstructor = new () => BrowserSpeechRecognition;

declare global {
  interface Window {
    SpeechRecognition?: BrowserSpeechRecognitionConstructor;
    webkitSpeechRecognition?: BrowserSpeechRecognitionConstructor;
  }
}

function browserSpeechErrorCode(error: string): string | null {
  switch (error) {
    case "aborted":
      return null;
    case "not-allowed":
    case "audio-capture":
      return "voice_permission_denied";
    case "service-not-allowed":
    case "network":
      return "voice_service_unavailable";
    case "no-speech":
      return "empty_transcript";
    default:
      return "voice_service_unavailable";
  }
}

export type VoicePhase =
  | "idle" | "connecting" | "ready" | "recording"
  | "recognizing" | "thinking" | "speaking" | "ended";

/** 挂断收尾兜底：turn_end 迟迟不来（服务端卡死/断网）就提交半截回答。 */
const DRAIN_TIMEOUT_MS = 90_000;

/** error 为后端 error 事件 code 或浏览器语音 API 的本地错误码。 */
export function useVoiceCall({ lang, onSessionBound, onTurnBegin, onAnswerDelta, onTurnEnd, onTurnError, onToolStart, onToolResult, onTurnAborted }: {
  lang: Lang;
  onSessionBound?: (sessionId: string) => void;
  /** 一轮开始（STT 结果就绪）：宿主把用户消息落进聊天流并置 streaming。 */
  onTurnBegin?: (userText: string) => void;
  /** 回答增量：宿主节流写入 pendingAnswer，页面正常流式渲染。 */
  onAnswerDelta?: (content: string) => void;
  /** 一轮结束（文本流完成，音频可能仍在播放）。 */
  onTurnEnd?: (sessionId: string | null) => void;
  /** 一轮中途失败（agent_error 杀死在途轮次）：宿主提交半截并解卡。 */
  onTurnError?: (code: string) => void;
  /** 工具调用开始/结果：宿主写入 pendingToolCalls，通话中同样渲染
   *  题目卡/知识检索卡（与打字流共用一套卡片渲染路径）。 */
  onToolStart?: (name: string) => void;
  onToolResult?: (result: unknown) => void;
  /** 在途轮次再也无法完成（挂断收尾超时/断网/服务端提前关闭/组件卸载）：
   *  宿主提交半截回答、清 streaming，聊天不允许永久卡在流式态。 */
  onTurnAborted?: () => void;
}) {
  const [phase, setPhase] = useState<VoicePhase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [statusKey, setStatusKey] = useState<string | null>(null);
  const [speakingSentence, setSpeakingSentence] = useState<string | null>(null);
  const [boardTable, setBoardTable] = useState<{ markdown: string; until: number } | null>(null);
  const [callSeconds, setCallSeconds] = useState(0);

  const phaseRef = useRef<VoicePhase>("idle");
  const wsRef = useRef<WebSocket | null>(null);
  const playCtxRef = useRef<AudioContext | null>(null);
  const playChainRef = useRef<AudioNode | null>(null);
  const playSrcRef = useRef<AudioBufferSourceNode | null>(null);
  const queueRef = useRef<AudioBuffer[]>([]);
  const playingRef = useRef(false);
  const pendingTtsRateRef = useRef<number>(16000);
  // 板书素材与播放同步：tts_start 只登记句子原文，随每帧 PCM 一起进播放
  // 队列（pcmMetaRef 与 queueRef 严格平行）；drainQueue 出队——那一帧真正
  // 开始发声——才 setSpeakingSentence。后端流水线刻意提前合成若干句，
  // tts_start 到达 ≠ 正在播报，直接挂板会比声音早好几句。
  const pendingTtsTextRef = useRef("");
  const pcmMetaRef = useRef<{ text: string }[]>([]);
  const endedByUserRef = useRef(false);
  // 挂断收尾（drain）：音频立即停，WS 保持存活把 answer_delta 写完。
  const drainingRef = useRef(false);
  const drainTimerRef = useRef<number | null>(null);
  // stt_result 已到、turn_end 未到：期间传输断掉必须通知宿主解卡。
  const turnInFlightRef = useRef(false);
  const sessionIdRef = useRef<string | null>(null);
  const startedAtRef = useRef<number | null>(null);
  const recognitionRef = useRef<BrowserSpeechRecognition | null>(null);
  const recognitionGenerationRef = useRef(0);
  const recognitionFinalTextRef = useRef("");
  const recognitionSubmittedRef = useRef(false);
  const recognitionHoldingRef = useRef(false);
  const recognitionStopRequestedRef = useRef(false);
  const recognitionFailedRef = useRef(false);
  const recognitionRestartTimerRef = useRef<number | null>(null);
  const startRecognitionRef = useRef<(generation: number) => void>(() => undefined);
  const cbRef = useRef({ onSessionBound, onTurnBegin, onAnswerDelta, onTurnEnd, onTurnError, onToolStart, onToolResult, onTurnAborted });
  useEffect(() => {
    cbRef.current = { onSessionBound, onTurnBegin, onAnswerDelta, onTurnEnd, onTurnError, onToolStart, onToolResult, onTurnAborted };
  }, [onSessionBound, onTurnBegin, onAnswerDelta, onTurnEnd, onTurnError, onToolStart, onToolResult, onTurnAborted]);

  const goto = useCallback((p: VoicePhase) => {
    phaseRef.current = p;
    setPhase(p);
  }, []);

  // ---- browser Speech Recognition -----------------------------------------

  const submitRecognition = useCallback((generation: number, recognition?: BrowserSpeechRecognition) => {
    if (generation !== recognitionGenerationRef.current || recognitionSubmittedRef.current) return;
    if (recognition && recognitionRef.current && recognitionRef.current !== recognition) return;
    recognitionSubmittedRef.current = true;
    const current = recognitionRef.current;
    if (current) {
      current.onresult = null;
      current.onerror = null;
      current.onend = null;
    }
    recognitionRef.current = null;
    const text = recognitionFinalTextRef.current.trim();
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      setError("network");
      goto("ended");
      return;
    }
    try {
      ws.send(JSON.stringify({ type: "utterance_end", text }));
    } catch {
      setError("network");
      goto("ended");
      return;
    }
    goto("recognizing");
  }, [goto]);

  const startRecognition = useCallback((generation: number) => {
    if (generation !== recognitionGenerationRef.current
        || !recognitionHoldingRef.current
        || recognitionFailedRef.current
        || endedByUserRef.current) return;
    const Recognition = window.SpeechRecognition ?? window.webkitSpeechRecognition;
    if (!Recognition) {
      recognitionFailedRef.current = true;
      recognitionHoldingRef.current = false;
      setError("voice_not_supported");
      goto("ready");
      return;
    }

    let recognition: BrowserSpeechRecognition;
    try {
      recognition = new Recognition();
    } catch {
      recognitionFailedRef.current = true;
      recognitionHoldingRef.current = false;
      setError("voice_service_unavailable");
      goto("ready");
      return;
    }
    recognition.lang = lang === "zh" ? "zh-CN" : "en-US";
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;
    const finalizedIndexes = new Set<number>();
    recognition.onresult = (event) => {
      if (generation !== recognitionGenerationRef.current || recognitionRef.current !== recognition) return;
      const startIndex = Math.max(0, event.resultIndex || 0);
      for (let i = startIndex; i < event.results.length; i++) {
        const result = event.results[i];
        if (!result?.isFinal || finalizedIndexes.has(i)) continue;
        finalizedIndexes.add(i);
        const transcript = result[0]?.transcript;
        if (transcript) recognitionFinalTextRef.current += transcript;
      }
    };
    recognition.onerror = (event) => {
      if (generation !== recognitionGenerationRef.current || recognitionRef.current !== recognition) return;
      const code = browserSpeechErrorCode(event.error);
      if (!code) return;
      recognitionFailedRef.current = true;
      recognitionHoldingRef.current = false;
      recognitionStopRequestedRef.current = true;
      if (recognitionRestartTimerRef.current !== null) {
        window.clearTimeout(recognitionRestartTimerRef.current);
        recognitionRestartTimerRef.current = null;
      }
      setError(code);
      if (phaseRef.current === "recording") goto("ready");
      recognition.onresult = null;
      recognition.onerror = null;
      recognition.onend = null;
      recognitionRef.current = null;
      try { recognition.abort(); } catch { /* noop */ }
    };
    recognition.onend = () => {
      if (generation !== recognitionGenerationRef.current || recognitionRef.current !== recognition) return;
      recognitionRef.current = null;
      recognition.onresult = null;
      recognition.onerror = null;
      recognition.onend = null;
      if (recognitionHoldingRef.current && !recognitionFailedRef.current
          && !recognitionStopRequestedRef.current && !endedByUserRef.current) {
        // Chrome/Edge may end a continuous session while the pointer is still
        // down.  Recreate it asynchronously and retain finalTextRef.
        if (recognitionRestartTimerRef.current === null) {
          recognitionRestartTimerRef.current = window.setTimeout(() => {
            recognitionRestartTimerRef.current = null;
            startRecognitionRef.current(generation);
          }, 0);
        }
        return;
      }
      submitRecognition(generation, recognition);
    };
    recognitionRef.current = recognition;
    try {
      recognition.start();
    } catch {
      recognitionFailedRef.current = true;
      recognitionHoldingRef.current = false;
      recognitionRef.current = null;
      recognition.onresult = null;
      recognition.onerror = null;
      recognition.onend = null;
      setError("voice_service_unavailable");
      goto("ready");
    }
  }, [goto, lang, submitRecognition]);

  useEffect(() => {
    startRecognitionRef.current = startRecognition;
  }, [startRecognition]);

  // ---- transport teardown（drain 辅助与 stopSpeaking 都依赖，故前置） --------

  const teardownRecognition = useCallback(() => {
    recognitionGenerationRef.current += 1;
    recognitionHoldingRef.current = false;
    recognitionStopRequestedRef.current = true;
    recognitionFailedRef.current = true;
    recognitionSubmittedRef.current = true;
    if (recognitionRestartTimerRef.current !== null) {
      window.clearTimeout(recognitionRestartTimerRef.current);
      recognitionRestartTimerRef.current = null;
    }
    const recognition = recognitionRef.current;
    recognitionRef.current = null;
    if (recognition) {
      recognition.onresult = null;
      recognition.onerror = null;
      recognition.onend = null;
      try { recognition.abort(); } catch { /* noop */ }
    }
    recognitionFinalTextRef.current = "";
  }, []);

  const teardownTransport = useCallback(() => {
    teardownRecognition();
    const ws = wsRef.current;
    wsRef.current = null;
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      ws.onclose = null;
      ws.onmessage = null;
      ws.close();
    }
  }, [teardownRecognition]);

  // ---- 挂断收尾（drain）------------------------------------------------------

  const clearDrainTimer = useCallback(() => {
    if (drainTimerRef.current !== null) {
      window.clearTimeout(drainTimerRef.current);
      drainTimerRef.current = null;
    }
  }, []);

  /** turn_end 已到（收尾成功）：拆传输即可，通话保持 ended 不复活 UI。 */
  const finishDrain = useCallback(() => {
    clearDrainTimer();
    drainingRef.current = false;
    teardownTransport();
  }, [clearDrainTimer, teardownTransport]);

  /** 收尾失败（超时/断网/服务端提前关闭）：放弃等待，通知宿主提交半截回答。 */
  const abortDrain = useCallback(() => {
    clearDrainTimer();
    drainingRef.current = false;
    teardownTransport();
    goto("ended");
    if (turnInFlightRef.current) {
      turnInFlightRef.current = false;
      cbRef.current.onTurnAborted?.();
    }
  }, [clearDrainTimer, goto, teardownTransport]);

  // ---- playback queue ------------------------------------------------------

  const ensurePlayCtx = useCallback(() => {
    if (!playCtxRef.current) {
      const ctx = new AudioContext();
      // 压缩器把句内/句间的响度差再抹平一层（后端已做逐句 RMS 归一化）：
      // threshold/knee/ratio 取广播级语音参数，makeup gain 补回损耗。
      const comp = ctx.createDynamicsCompressor();
      comp.threshold.value = -22;
      comp.knee.value = 24;
      comp.ratio.value = 5;
      comp.attack.value = 0.004;
      comp.release.value = 0.22;
      const gain = ctx.createGain();
      gain.gain.value = 1.2;
      comp.connect(gain);
      gain.connect(ctx.destination);
      playCtxRef.current = ctx;
      playChainRef.current = comp;
    }
    if (playCtxRef.current.state === "suspended") void playCtxRef.current.resume();
    return playCtxRef.current;
  }, []);

  // Recursive by design (onended chains the next sentence); the ref breaks
  // the self-reference so the callback identity stays stable for lint.
  const drainRef = useRef<() => void>(() => undefined);
  const drainQueue = useCallback(() => {
    if (playingRef.current) return;
    const buf = queueRef.current.shift();
    const meta = pcmMetaRef.current.shift();
    if (!buf) {
      if (phaseRef.current === "speaking") goto("ready");
      return;
    }
    const ctx = ensurePlayCtx();
    const node = ctx.createBufferSource();
    node.buffer = buf;
    node.connect(playChainRef.current ?? ctx.destination);
    playingRef.current = true;
    playSrcRef.current = node;
    // 这一帧音频此刻真正开始发声：所属句子此刻才暴露给黑板——语音没播
    // 到的部分不会提前上板（tts_start 提前到达只是入队登记）。
    setSpeakingSentence(meta?.text || null);
    node.onended = () => {
      playingRef.current = false;
      playSrcRef.current = null;
      drainRef.current();
    };
    node.start();
  }, [ensurePlayCtx, goto]);
  useEffect(() => { drainRef.current = drainQueue; }, [drainQueue]);

  const stopSpeaking = useCallback(() => {
    // 挂断收尾期间「停止播报」按钮/输入框停止键 = 放弃等剩余文字，提交半截。
    if (drainingRef.current) {
      abortDrain();
      return;
    }
    queueRef.current.length = 0;
    pcmMetaRef.current.length = 0;
    playingRef.current = false;
    try { playSrcRef.current?.stop(); } catch { /* already ended */ }
    playSrcRef.current = null;
    setSpeakingSentence(null);
    if (phaseRef.current === "speaking") goto("ready");
  }, [abortDrain, goto]);

  const enqueuePcm = useCallback((pcm: ArrayBuffer, sampleRate: number) => {
    const ctx = ensurePlayCtx();
    const src16 = new Int16Array(pcm);
    if (src16.length === 0) return;
    const floats = new Float32Array(src16.length);
    for (let i = 0; i < src16.length; i++) floats[i] = src16[i] / 32768;
    const buf = ctx.createBuffer(1, floats.length, sampleRate);
    buf.copyToChannel(floats, 0);
    queueRef.current.push(buf);
    pcmMetaRef.current.push({ text: pendingTtsTextRef.current });
    drainQueue();
  }, [drainQueue, ensurePlayCtx]);

  // ---- WS event handling ----------------------------------------------------

  const handleEvent = useCallback((ev: Record<string, unknown>) => {
    switch (ev.type) {
      case "session_bound":
        sessionIdRef.current = (ev.session_id as string) || null;
        goto("ready");
        if (sessionIdRef.current) cbRef.current.onSessionBound?.(sessionIdRef.current);
        break;
      case "stt_start":
        setStatusKey(null);
        goto("recognizing");
        break;
      case "stt_result":
        setSpeakingSentence(null);
        turnInFlightRef.current = true;
        cbRef.current.onTurnBegin?.(String(ev.text || ""));
        goto("thinking");
        break;
      case "step":
      case "tool_progress":
      case "tool_warning":
        setStatusKey("tool");
        break;
      case "tool_start":
        setStatusKey("tool");
        cbRef.current.onToolStart?.(String(ev.name || ""));
        break;
      case "tool_result":
        // 题目/检索载荷：与打字流同构地写进 pendingToolCalls，
        // commitAssistant 落定后通话中也能渲染答题卡与命中来源卡。
        cbRef.current.onToolResult?.(ev.result);
        break;
      case "retry":
        setStatusKey("retry");
        break;
      case "answer_delta":
        cbRef.current.onAnswerDelta?.(String(ev.content || ""));
        if (phaseRef.current === "thinking") setStatusKey(null);
        break;
      case "tts_start":
        // 挂断收尾：音频已停，迟到的 tts_start 不得把通话 UI 复活。
        if (drainingRef.current) break;
        pendingTtsRateRef.current = Number(ev.sample_rate) || 16000;
        // 板书素材只在此登记：本句原始 markdown（公式未清洗）随它的 PCM
        // 一起入队，真正开始发声（drainQueue 出队）才暴露给黑板。空 text
        // 是超长句的续片（后端只给首片带句子），属同一句、不覆盖。
        if (ev.text) pendingTtsTextRef.current = String(ev.text);
        goto("speaking");
        break;
      case "board_table":
        // 表格不逐格朗读：整块 markdown 上黑板整版驻留至少 hold 窗口
        // （后端下发，下限 7s）。没有到点自动消失——表格驻留到窗口结束
        // 之后，直到黑板层收到下一个新公式（经 clearBoardTable 清空）
        // 或新表格（直接替换并重置窗口）为止。
        setBoardTable({
          markdown: String(ev.markdown || ""),
          until: Date.now() + Math.max(7000, Number(ev.hold_ms) || 7000),
        });
        break;
      case "tts_end":
        break;
      case "tts_error":
        setStatusKey("tts_err");
        break;
      case "turn_end":
        setSpeakingSentence(null);
        turnInFlightRef.current = false;
        if (drainingRef.current) {
          finishDrain();
        } else if (phaseRef.current !== "speaking" && !playingRef.current) {
          goto("ready");
        }
        cbRef.current.onTurnEnd?.((ev.session_id as string) || sessionIdRef.current);
        break;
      case "bye":
        // 收尾期间 bye 必须跟在 turn_end 之后；先到说明服务端提前收线，
        // 在途轮次交给宿主提交半截，绝不能让聊天卡在流式态。
        if (drainingRef.current) {
          if (turnInFlightRef.current) abortDrain();
          else finishDrain();
        }
        goto("ended");
        break;
      case "error": {
        const code = String(ev.code || "agent");
        if (drainingRef.current) {
          if (turnInFlightRef.current) abortDrain();
          else finishDrain();
          break;
        }
        setError(code);
        if (code === "voice_disabled" || code === "session_not_found") {
          goto("ended");
        } else {
          // 只有 agent_error 真正杀死在途轮次；busy / empty_transcript
          // 不动聊天流，原轮自己的 turn_end 会来做清理。
          if (code === "agent_error" && turnInFlightRef.current) {
            turnInFlightRef.current = false;
            cbRef.current.onTurnError?.(code);
          }
          if (phaseRef.current !== "ready") goto("ready");
        }
        break;
      }
      default:
        break;
    }
  }, [abortDrain, finishDrain, goto]);

  // ---- lifecycle -------------------------------------------------------------

  const hangUp = useCallback(() => {
    if (drainingRef.current) return; // 二次挂断不得把收尾中断成「提交半截」
    // 以 turnInFlight 为准（而非 phase）：音频队列放完会把相位拨回
    // ready，但文字流可能仍在跑；它与宿主的 chat.streaming 严格同源。
    const turnActive = turnInFlightRef.current;
    endedByUserRef.current = true;
    stopSpeaking();
    void playCtxRef.current?.close().catch(() => undefined);
    playCtxRef.current = null;
    playChainRef.current = null;
    try { wsRef.current?.send(JSON.stringify({ type: "end" })); } catch { /* noop */ }
    goto("ended");
    // 轮次在途：音频已停，WS 保持存活进入收尾——answer_delta 继续把
    // 文字写进聊天流直到 turn_end（服务端同样跑完并落盘），超时或断线
    // 由 abortDrain 兜底提交半截。无在途轮次则立即拆线。
    if (!turnActive) {
      teardownTransport();
      return;
    }
    drainingRef.current = true;
    drainTimerRef.current = window.setTimeout(() => {
      if (drainingRef.current) abortDrain();
    }, DRAIN_TIMEOUT_MS);
  }, [abortDrain, goto, stopSpeaking, teardownTransport]);

  const start = useCallback(async (sessionId: string | null, workspaceId: string | null) => {
    if (phaseRef.current !== "idle") return; // dev StrictMode 双挂载只允许一条连接
    recognitionFinalTextRef.current = "";
    recognitionSubmittedRef.current = false;
    setError(null); setStatusKey(null); setSpeakingSentence(null);
    setCallSeconds(0);
    startedAtRef.current = Date.now();
    goto("connecting");
    try {
      const { ticket } = await voiceTicket();
      // 票据到手后再复位：StrictMode 双效应的 cleanup 会在 start() 的
      // await 间隙把它置 true，太早复位会被它盖掉。
      endedByUserRef.current = false;
      // 后端 WS 路由是 /api/v1/voice/ws：基址必须保留 API_BASE 的 /api/v1
      // 前缀，直连分支只换协议（http→ws、https→wss），同源分支拼当前 host
      // （生产同源走 nginx 的 WS 升级 location，不能依赖 next rewrite）。
      const wsBase = API_BASE.startsWith("http")
        ? API_BASE.replace(/^http/, "ws")
        : `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}${API_BASE}`;
      const ws = new WebSocket(`${wsBase}/voice/ws?ticket=${encodeURIComponent(ticket)}`);
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;
      ws.onopen = () => {
        ws.send(JSON.stringify({
          type: "start",
          session_id: sessionId,
          workspace_id: workspaceId,
          lang,
        }));
      };
      ws.onmessage = (ev) => {
        if (typeof ev.data === "string") {
          try { handleEvent(JSON.parse(ev.data)); } catch { /* skip malformed */ }
        } else if (!drainingRef.current) {
          // 收尾期间迟到的音频帧直接丢弃（AudioContext 已关，不再重建）。
          enqueuePcm(ev.data as ArrayBuffer, pendingTtsRateRef.current);
        }
      };
      ws.onclose = () => {
        if (drainingRef.current) {
          // 服务端在 turn_end 之前收线（或收尾已被 finishDrain 拆线）。
          if (turnInFlightRef.current) abortDrain();
          else finishDrain();
          return;
        }
        if (!endedByUserRef.current) {
          setError("network");
          goto("ended");
          // 断网杀死在途轮次时 turn_end 永远不会来：通知宿主提交半截并解卡。
          if (turnInFlightRef.current) {
            turnInFlightRef.current = false;
            cbRef.current.onTurnAborted?.();
          }
        }
      };
    } catch {
      setError("network");
      goto("ended");
    }
  }, [abortDrain, enqueuePcm, finishDrain, goto, handleEvent, lang]);

  // 通话时长（指示器上的 mm:ss）：从 start 起计时，ended 停止。
  useEffect(() => {
    if (phase === "idle" || phase === "ended" || startedAtRef.current === null) return;
    const id = window.setInterval(() => {
      if (startedAtRef.current !== null) {
        setCallSeconds(Math.floor((Date.now() - startedAtRef.current) / 1000));
      }
    }, 1000);
    return () => window.clearInterval(id);
  }, [phase]);

  // ---- push-to-talk -----------------------------------------------------------

  const beginTalk = useCallback(() => {
    if (phaseRef.current !== "ready") return;
    setError(null);
    setStatusKey(null);
    recognitionGenerationRef.current += 1;
    const generation = recognitionGenerationRef.current;
    recognitionFinalTextRef.current = "";
    recognitionSubmittedRef.current = false;
    recognitionHoldingRef.current = true;
    recognitionStopRequestedRef.current = false;
    recognitionFailedRef.current = false;
    try {
      const Recognition = window.SpeechRecognition ?? window.webkitSpeechRecognition;
      if (!Recognition) {
        recognitionHoldingRef.current = false;
        recognitionFailedRef.current = true;
        setError("voice_not_supported");
        goto("ready");
        return;
      }
      ensurePlayCtx(); // resume playback ctx inside the user gesture
      startRecognition(generation);
      if (recognitionRef.current) goto("recording");
    } catch {
      recognitionHoldingRef.current = false;
      recognitionFailedRef.current = true;
      setError("voice_service_unavailable");
      goto("ready");
    }
  }, [ensurePlayCtx, goto, startRecognition]);

  /** 文本兜底输入：跳过浏览器 STT，直接把打好的文本当本轮转写发上
   *  同一条 WS——回复仍走 TTS 语音通道。正在播报时先打断（barge-in）。 */
  const sendText = useCallback((raw: string) => {
    const text = raw.trim();
    if (!text) return;
    if (phaseRef.current !== "ready" && phaseRef.current !== "speaking") return;
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      setError("network");
      goto("ended");
      return;
    }
    if (phaseRef.current === "speaking") stopSpeaking();
    setError(null);
    setStatusKey(null);
    ensurePlayCtx(); // 文本回复的 TTS 播放也依赖用户手势解锁的 AudioContext
    try {
      ws.send(JSON.stringify({ type: "utterance_end", text }));
    } catch {
      setError("network");
      goto("ended");
      return;
    }
    goto("recognizing");
  }, [ensurePlayCtx, goto, stopSpeaking]);

  const endTalk = useCallback(() => {
    if (phaseRef.current !== "recording") return;
    recognitionHoldingRef.current = false;
    recognitionStopRequestedRef.current = true;
    if (recognitionRestartTimerRef.current !== null) {
      window.clearTimeout(recognitionRestartTimerRef.current);
      recognitionRestartTimerRef.current = null;
    }
    const generation = recognitionGenerationRef.current;
    const recognition = recognitionRef.current;
    if (recognition) {
      try {
        recognition.stop();
      } catch {
        submitRecognition(generation, recognition);
      }
    } else {
      submitRecognition(generation);
    }
    goto("recognizing");
  }, [goto, submitRecognition]);

  // 表格驻留窗口过后，黑板层遇到新公式时用它清空整版表格（板面交还）。
  const clearBoardTable = useCallback(() => setBoardTable(null), []);

  useEffect(() => () => {
    endedByUserRef.current = true;
    const hadTurn = turnInFlightRef.current;
    turnInFlightRef.current = false;
    clearDrainTimer();
    drainingRef.current = false;
    stopSpeaking();
    teardownTransport();
    void playCtxRef.current?.close().catch(() => undefined);
    // 卸载（切页/路由变化）打断在途轮次：宿主必须提交半截并解卡。
    if (hadTurn) cbRef.current.onTurnAborted?.();
  }, [clearDrainTimer, stopSpeaking, teardownTransport]);

  return {
    phase, error, statusKey, speakingSentence, boardTable, callSeconds,
    start, hangUp, beginTalk, endTalk, sendText, stopSpeaking, clearBoardTable,
  };
}
