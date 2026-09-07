"use client";
/**
 * 语音通话层（P10 沉浸式改版）：不再弹出对话卡——通话期间 chat 页面照常
 * 显示，语音轮次实时写入消息流（转写即用户消息、answer_delta 节流进
 * pendingAnswer、turn_end 落定），本层只补三件「电话感」：
 *
 *   1. 右上角「手机模拟」（原联系老师入口的位置，不与黑板同行、不挤
 *      黑板宽度）：虚拟空白头像 + 通话计时 + 声波状态，底部一排装饰
 *      导航图标（返回/主页/多任务，无真实功能）；挂断与停止播报都在
 *      底部控制条，窄屏隐藏手机只留黑板；
 *   2. 顶部「板书」黑板：接通即常驻、挂断才收起；三块等分黑板只记**块状
 *      公式段**（$$…$$ 本身——行内 $…$ 不上板、也不触发换板），且与实际
 *      播放同步：句子随其音频出队发声那一刻才挂板（useVoiceCall 的
 *      speakingSentence 已改为随播放队列更新）；从上往下写空板、三块都满
 *      时擦掉写得最早的那块再写新的；表格不逐格朗读——整块 markdown 即
 *      时整版驻留至少 7 秒（board_table），之后直到新公式/新表格需要板面
 *      才清空；
 *   3. 底部控制条：按住说话 / 停止播报 / 挂断，紧贴输入框上方——
 *      通话期间输入框保持可用，打字消息经同一条 WS 发出（sendText），
 *      老师的回复仍走 TTS 语音通道；浏览器不支持语音识别时可纯打字通话。
 *
 * 采集/协议/播放全在 useVoiceCall；会话写入与 store 的世代守卫和
 * handleSend（chat 页文字流）同构，语音轮与文字轮共享同一套渲染路径。
 */
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ChevronLeft, Home, Loader2, Mic, PhoneOff, Presentation, Square, UserRound, VolumeX } from "lucide-react";
import { cn } from "@/lib/cn";
import { t, type Lang } from "@/lib/i18n";
import { listSessions, loadSession } from "@/lib/api";
import { useChatStore } from "@/lib/store";
import { notifySessionChanged } from "@/lib/ws-settings";
import { Markdown, displayMathSegments } from "@/components/chat/markdown";
import { useVoiceCall, type VoicePhase } from "@/lib/voice/useVoiceCall";

const MINUTE = 60;

function fmtDuration(total: number): string {
  const mm = Math.floor(total / MINUTE).toString().padStart(2, "0");
  const ss = (total % MINUTE).toString().padStart(2, "0");
  return `${mm}:${ss}`;
}

/* ---- 板书：三块等分黑板只记公式句，接通常驻、挂断才关 -------------------- */

type BoardSlot = { key: number; text: string } | null;

/** 擦除动画时长（ms）：与 globals.css 的 panel-erase 时长保持一致。 */
const BOARD_ERASE_MS = 450;

export function FormulaBoard({ lang, sentence, table, active, onClearTable }: {
  lang: Lang;
  sentence: string | null;
  /** 表格整版驻留（board_table 事件）：独占整个板面至少 hold 窗口；
   *  窗口过后继续驻留，直到下一个新公式（或新表格）需要板面才被清空。 */
  table: { markdown: string; until: number } | null;
  /** 已接通（ready 起至通话结束）：黑板常驻，挂断随通话层卸载才消失。 */
  active: boolean;
  /** 表格窗口过后由新公式清空整版表格（boardTable 状态在 useVoiceCall）。 */
  onClearTable: () => void;
}) {
  const [slots, setSlots] = useState<[BoardSlot, BoardSlot, BoardSlot]>([null, null, null]);
  const [erasing, setErasing] = useState<0 | 1 | 2 | null>(null);
  const lastTextRef = useRef<string | null>(null);
  const keyRef = useRef(0);

  // 表格上板 = 三块黑板内容全部作废：表格清掉后新公式从第一块重新写起。
  useEffect(() => {
    if (!table) return;
    // setState 经 rAF 延迟触发（react-hooks/set-state-in-effect）。
    const raf = requestAnimationFrame(() => {
      setSlots([null, null, null]);
      lastTextRef.current = null;
    });
    return () => cancelAnimationFrame(raf);
  }, [table]);

  useEffect(() => {
    if (!sentence) return;
    // 只上板块状公式段（$$…$$ / \[…\]，含定界符）：行内 $…$ 的普通讲解句
    // 不上板、也不触发换板/擦板；同句多段块式拼成一次板书写入同一块板。
    const boardText = displayMathSegments(sentence).join("\n\n");
    if (!boardText) return;
    if (boardText === lastTextRef.current) return; // 同句重播不重复上板
    if (table) {
      // 表格驻留窗口内新公式不上板（音频播报不受影响）；窗口过后第一个
      // 新公式清空整版表格接管板面——table→null 后本 effect 重跑完成写入。
      // 表格不会到点自动消失，非公式句子也永不清板。
      if (Date.now() < table.until) return;
      onClearTable();
      return;
    }
    lastTextRef.current = boardText;
    // 从上到下写第一块空板；三块都满时擦掉写得最早的那块（key 即写入序）。
    let target: 0 | 1 | 2 = slots[0] === null
      ? 0
      : slots[1] === null
        ? 1
        : slots[2] === null ? 2 : 0;
    if (slots[0] !== null && slots[1] !== null && slots[2] !== null) {
      const keys = slots.map((s) => (s ? s.key : Infinity));
      target = keys.indexOf(Math.min(...keys)) as 0 | 1 | 2;
    }
    const write = () => {
      keyRef.current += 1;
      const key = keyRef.current;
      setSlots((prev) => {
        const next: [BoardSlot, BoardSlot, BoardSlot] = [prev[0], prev[1], prev[2]];
        next[target] = { key, text: boardText };
        return next;
      });
      setErasing(null);
    };
    if (slots[target] === null) {
      // setState 经 rAF 延迟触发（react-hooks/set-state-in-effect）。
      const raf = requestAnimationFrame(write);
      return () => cancelAnimationFrame(raf);
    }
    // setState 经 rAF 延迟触发（react-hooks/set-state-in-effect）：先挂
    // 擦除动画类，450ms 后再写入新内容。
    const raf = requestAnimationFrame(() => setErasing(target));
    const timer = window.setTimeout(write, BOARD_ERASE_MS);
    return () => {
      cancelAnimationFrame(raf);
      window.clearTimeout(timer);
    };
  }, [sentence, slots, table, onClearTable]);

  if (!active) return null;
  return (
    /* 黑板严格左右居中（无右侧预留，窄窗口下手机可能贴住板书右缘——已
       确认接受），固定占 3/7 页面高度（页面根为 h-screen）。等分的关键在
       两层都要 flex-1 min-h-0：外层 body 填满固定高板面（否则按内容自
       高，三块面板在内容高容器里 flex-1 各归各的内容高度——旧版不均匀
       的根因），内层三块面板再 flex-1 min-h-0 严格三等分，超出各自滚动。 */
    <div className="pointer-events-none absolute inset-x-0 top-10 z-10 flex justify-center px-4">
      <div className="voice-board board-in pointer-events-auto flex h-[calc(100vh*3/7)] w-full max-w-[min(94%,760px)] flex-col overflow-hidden">
        <div className="flex items-center gap-1.5 px-3.5 pb-1 pt-2.5">
          <Presentation size={12} className="shrink-0" />
          <span className="text-[0.66rem] font-medium tracking-wide">{t(lang, "chat.voice.call.board.title")}</span>
          <span className="board-chalk-dash mx-1.5 flex-1" aria-hidden />
        </div>
        {table ? (
          // 表格整版：整块 markdown 即时完整呈现（刻意无书写动画，不逐行
          // 画出），独占整个板面（超出滚动），驻留期间三块黑板隐藏；音频
          // 口播只有一句引导语，流水线不停。
          <div className="voice-board-body min-h-0 flex-1">
            <div className="voice-board-panel min-h-0 flex-1">
              <Markdown className="chat-prose">{table.markdown}</Markdown>
            </div>
          </div>
        ) : (
          <div className="voice-board-body flex min-h-0 flex-1 flex-col">
            {slots.map((slot, i) => (
              <Fragment key={i}>
                {i > 0 && <div className="voice-board-divider" aria-hidden />}
                <div className="voice-board-panel min-h-0 flex-1 overflow-y-auto">
                  {slot ? (
                    <div key={slot.key} className={erasing === i ? "panel-erase" : "panel-write"}>
                      <Markdown className="chat-prose">{slot.text}</Markdown>
                    </div>
                  ) : i === 0 ? (
                    <p className="text-[0.72rem] text-white/55">{t(lang, "chat.voice.call.board.empty")}</p>
                  ) : null}
                </div>
              </Fragment>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/* ---- 右上角的手机模拟（原联系老师入口位置）：空白头像 + 计时 + 装饰导航 -- */

export function PhoneMockup({ lang, voice }: {
  lang: Lang;
  voice: ReturnType<typeof useVoiceCall>;
}) {
  const tr = (k: string, fb?: string) => t(lang, k, fb);
  const dotCls = voice.error
    ? "bg-danger"
    : voice.phase === "ready"
      ? "bg-success"
      : voice.phase === "recording"
        ? "bg-danger animate-pulse"
        : "bg-accent animate-pulse";
  const waving = voice.phase === "speaking" || voice.phase === "recording";

  return (
    <div
      title={tr("chat.voice.call.inCall")}
      aria-label={tr("chat.voice.call.inCall")}
      className="voice-phone board-in pointer-events-none hidden flex-col items-center md:flex"
    >
      {/* 听筒 + 状态点：错误红 / 录音红闪 / 待机绿 / 工作蓝闪 */}
      <div className="flex w-full items-center justify-center gap-1.5 pt-2.5" aria-hidden>
        <span className={cn("h-1 w-1 shrink-0 rounded-full", dotCls)} />
        <span className="h-1 w-10 rounded-full bg-white/25" />
      </div>
      {/* 虚拟空白头像：老师的占位形象，说话时外圈涟漪 */}
      <div className="relative mt-3 flex h-14 w-14 items-center justify-center overflow-hidden rounded-full border border-white/15 bg-white/10">
        <UserRound size={30} className="text-white/70" />
        {waving && (
          <span className="call-pulse-ring absolute inset-0 rounded-full border border-white/40" aria-hidden />
        )}
      </div>
      {/* 通话计时 */}
      <span className="tnum mt-2.5 text-[0.72rem] font-medium tracking-wide text-white/90">
        {fmtDuration(voice.callSeconds)}
      </span>
      {/* 声波：老师说话/我说话时跳动 */}
      <span className="mt-1.5 flex h-4 items-end gap-[3px]" aria-hidden>
        {[7, 11, 9, 11, 7].map((h, i) => (
          <span
            key={i}
            style={{ height: `${h}px`, animationDelay: `${i * 0.13}s` }}
            className={cn("w-[2.5px] rounded-full bg-white/70", waving && "call-wave-bar")}
          />
        ))}
      </span>
      {/* 装饰导航栏：手机常见的返回上一页/返回首页/多任务，无真实用途 */}
      <div className="mt-auto flex w-full items-center justify-center gap-4 pb-2.5 pt-2.5" aria-hidden>
        <ChevronLeft size={14} className="text-white/45" />
        <Home size={13} className="text-white/45" />
        <Square size={10} className="text-white/45" />
      </div>
    </div>
  );
}

/* ---- 底部控制条（紧贴输入框上方，打字消息走同一条语音 WS） ---------------- */

function statusLineFor(lang: Lang, phase: VoicePhase, error: string | null): string {
  const tr = (k: string, fb?: string) => t(lang, k, fb);
  if (error) return tr(`chat.voice.call.err.${error}`, error);
  switch (phase) {
    case "connecting": return tr("chat.voice.call.connecting");
    case "ready": return tr("chat.voice.call.ready");
    case "recording": return tr("chat.voice.call.release");
    case "recognizing": return tr("chat.voice.call.recognizing");
    case "thinking": return tr("chat.voice.call.thinking");
    case "speaking": return tr("chat.voice.call.speaking");
    case "ended": return tr("chat.voice.call.err.network");
    default: return "";
  }
}

export function CallBar({ lang, voice, onHangUp }: {
  lang: Lang;
  voice: ReturnType<typeof useVoiceCall>;
  onHangUp: () => void;
}) {
  const tr = (k: string, fb?: string) => t(lang, k, fb);
  const holdable = voice.phase === "ready";
  const busy = voice.phase === "recognizing" || voice.phase === "thinking" || voice.phase === "speaking";
  const extraStatus = voice.statusKey && voice.phase !== "ended"
    ? tr(`chat.voice.call.${voice.statusKey}`, voice.statusKey)
    : null;

  return (
    <div className="px-4 pb-1 pt-0.5">
      <div className="mx-auto flex max-w-[820px] items-center gap-2.5 rounded-[14px] border border-border bg-surface p-2.5 shadow-md">
        {/* 状态区 */}
        <div className="flex min-w-0 flex-1 items-center gap-2.5 px-1.5">
          {voice.phase === "connecting" && <Loader2 size={15} className="shrink-0 animate-spin text-accent" />}
          {voice.phase === "recording" && (
            <span className="relative flex h-2.5 w-2.5 shrink-0">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-danger opacity-75" />
              <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-danger" />
            </span>
          )}
          {(busy || voice.phase === "ready") && voice.phase !== "connecting" && voice.phase !== "recording" && (
            <span className={cn("h-2.5 w-2.5 shrink-0 rounded-full", voice.phase === "ready" ? "bg-success" : "animate-pulse bg-accent")} />
          )}
          {voice.error && <span className="h-2.5 w-2.5 shrink-0 rounded-full bg-danger" />}
          <div className="min-w-0">
            <p className={cn("truncate text-[0.78rem] font-medium", voice.error ? "text-danger" : "text-fg")}>
              {statusLineFor(lang, voice.phase, voice.error)}
            </p>
            <p className="truncate text-[0.66rem] text-muted">
              {extraStatus ?? tr("chat.voice.call.inCall")}
            </p>
          </div>
        </div>

        {/* 停止播报 */}
        {voice.phase === "speaking" && (
          <button
            type="button"
            onClick={() => voice.stopSpeaking()}
            className="flex h-9 shrink-0 items-center gap-1.5 rounded-[10px] border border-border px-2.5 text-[0.72rem] text-fg-secondary transition-colors hover:bg-surface-hover"
            title={tr("chat.voice.call.stopAudio")}
          >
            <VolumeX size={14} />
            <span className="hidden sm:inline">{tr("chat.voice.call.stopAudio")}</span>
          </button>
        )}

        {/* 按住说话 */}
        <div className="relative flex h-12 w-12 shrink-0 items-center justify-center">
          {voice.phase === "recording" && (
            <span className="call-pulse-ring absolute inset-0 rounded-full border-2 border-danger/50" aria-hidden />
          )}
          <button
            type="button"
            disabled={!holdable}
            onPointerDown={(e) => { e.preventDefault(); if (holdable) void voice.beginTalk(); }}
            onPointerUp={() => voice.endTalk()}
            onPointerLeave={() => voice.endTalk()}
            onPointerCancel={() => voice.endTalk()}
            onContextMenu={(e) => e.preventDefault()}
            style={{ touchAction: "none" }}
            className={cn(
              "flex h-12 w-12 select-none items-center justify-center rounded-full border shadow-sm transition-all",
              voice.phase === "recording"
                ? "scale-105 border-danger bg-danger text-white"
                : holdable
                  ? "border-accent/40 bg-accent-soft/60 text-accent-strong hover:bg-accent-soft"
                  : "cursor-not-allowed border-border bg-surface-sunken text-muted",
            )}
            aria-label={tr("chat.voice.call.hold")}
          >
            {voice.phase === "recording"
              ? <Mic size={20} className="animate-pulse" />
              : busy ? <Loader2 size={18} className="animate-spin" /> : <Mic size={20} />}
          </button>
        </div>

        {/* 挂断 */}
        <button
          type="button"
          onClick={onHangUp}
          className="flex h-9 shrink-0 items-center gap-1.5 rounded-[10px] bg-danger px-3 text-[0.75rem] font-medium text-white shadow-sm transition-opacity hover:opacity-90"
        >
          <PhoneOff size={14} />
          <span className="hidden sm:inline">{tr("chat.voice.call.hangup")}</span>
        </button>
      </div>
    </div>
  );
}

/* ---- 通话层本体 ------------------------------------------------------------ */

/** 通话期间暴露给宿主页面的文本通道：打字消息改走语音 WS（回复仍 TTS 播报），
 *  停止按钮在语音轮次里对应打断播报。 */
export type VoiceTextController = {
  sendText: (text: string) => void;
  stopSpeaking: () => void;
};

export function VoiceCallLayer({ lang, sessionId, workspaceId, onClose, onRegisterController }: {
  lang: Lang;
  sessionId: string | null;
  workspaceId: string | null;
  onClose: () => void;
  onRegisterController?: (ctl: VoiceTextController | null) => void;
}) {
  const router = useRouter();
  const turnsRef = useRef(0);
  const boundSidRef = useRef<string | null>(sessionId);
  // 会话世代守卫：语音轮进行中用户切走会话时，残余写入全部丢弃。
  const genRef = useRef<number | null>(null);
  const answerAccumRef = useRef("");
  const flushTimerRef = useRef<number | null>(null);
  // 挂断收尾：轮次在途时挂断不立刻卸载本层——通话 UI 隐藏，文字写完
  // （或中止提交半截）后再 onClose。ref 供回调同步读取，state 只管渲染。
  const [finishing, setFinishing] = useState(false);
  const finishingRef = useRef(false);

  const flushNow = useCallback(() => {
    if (flushTimerRef.current !== null) {
      window.clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
    }
    const st = useChatStore.getState();
    if (genRef.current === null || st.generation === genRef.current) {
      st.flushPending("", answerAccumRef.current);
    }
  }, []);

  const refreshSessions = useCallback(() => {
    listSessions()
      .then((r) => {
        useChatStore.getState().setSessions(r.sessions);
        notifySessionChanged();
      })
      .catch(() => undefined);
  }, []);

  const reloadBoundSession = useCallback((sid: string) => {
    loadSession(sid)
      .then((detail) => {
        const st = useChatStore.getState();
        if (st.sessionId === sid) {
          st.loadFull(detail.messages || [], detail.knowledge_files || [], sid);
        }
      })
      .catch(() => undefined);
  }, []);

  /** 轮次终局的统一收尾：落定半截/完整回答、清 streaming 与 retry；
   *  挂断收尾（finishing）时最后收起通话层。世代不匹配（用户已切走
   *  会话）则一个字都不写——loadFull/newChat 已把 store 重置干净。 */
  const finalizeTurn = useCallback(() => {
    flushNow();
    const st = useChatStore.getState();
    if (genRef.current === null || st.generation === genRef.current) {
      if (st.pendingAnswer || st.pendingThinking || st.pendingToolCalls.length > 0) st.commitAssistant();
      st.setStreaming(false);
      st.setRetry(null);
    }
    refreshSessions();
    if (finishingRef.current) {
      finishingRef.current = false;
      setFinishing(false);
      onClose();
    }
  }, [flushNow, onClose, refreshSessions]);

  const voice = useVoiceCall({
    lang,
    onSessionBound: (sid) => {
      boundSidRef.current = sid;
      // 只绑 store、不 router.replace：Next 16 里 bare /chat → /chat/<sid>
      // 的 replace 会整页重挂载（动态段 key 变化），通话层与 WS 会随之
      // 被拆除。URL 由挂断时的 handleHangUp 补齐。
      useChatStore.getState().setSessionId(sid);
    },
    onTurnBegin: (text) => {
      const st = useChatStore.getState();
      genRef.current = st.generation;
      answerAccumRef.current = "";
      st.setStreaming(true);
      st.resetPending();
      st.setMessages([...st.messages, { role: "user" as const, content: text }]);
    },
    onAnswerDelta: (content) => {
      answerAccumRef.current += content;
      if (flushTimerRef.current === null) {
        flushTimerRef.current = window.setTimeout(() => {
          flushTimerRef.current = null;
          const st = useChatStore.getState();
          if (genRef.current === null || st.generation === genRef.current) {
            st.flushPending("", answerAccumRef.current);
          }
        }, 50);
      }
    },
    onToolStart: (name) => {
      const st = useChatStore.getState();
      if (!name || (genRef.current !== null && st.generation !== genRef.current)) return;
      st.addToolStart(name);
    },
    onToolResult: (result) => {
      const st = useChatStore.getState();
      if (genRef.current !== null && st.generation !== genRef.current) return;
      st.setToolResult(result);
    },
    onTurnEnd: (sid) => {
      if (sid) boundSidRef.current = sid;
      turnsRef.current += 1;
      finalizeTurn();
    },
    onTurnError: () => {
      // agent_error 杀死在途轮次：提交半截回答（与打字流中断一致）并立即
      // 解卡；服务端不落盘半截，重载反而会把它闪没。
      finalizeTurn();
    },
    onTurnAborted: () => {
      // 在途轮次再也无法完成（收尾超时/断网/服务端提前收线/卸载）：
      // 同样提交半截并解卡，聊天不允许悬空在流式态。
      finalizeTurn();
    },
  });

  // 挂载即拨号（StrictMode 双效应由 hook 内 phaseRef 守卫兜住）。
  useEffect(() => {
    turnsRef.current = 0;
    boundSidRef.current = sessionId;
    void voice.start(sessionId, workspaceId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 把文本通道注册给宿主页面；卸载时置空，输入框回落到文字流路径。
  useEffect(() => {
    onRegisterController?.({ sendText: voice.sendText, stopSpeaking: voice.stopSpeaking });
    return () => onRegisterController?.(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleHangUp = useCallback(() => {
    const hadTurns = turnsRef.current > 0;
    const sid = boundSidRef.current;
    const st = useChatStore.getState();
    // 完成过轮次、或挂断时仍有轮次在途：以服务端落盘为准重载对齐，
    // 本地半截流/悬空的用户消息都会被替换掉。
    const turnInFlight = st.streaming;
    voice.hangUp();
    if (turnInFlight) {
      // 挂断收尾：通话 UI 随本层隐藏，聊天流继续把回答写完，由
      // onTurnEnd/onTurnAborted 落定后再 onClose 收起本层。
      finishingRef.current = true;
      setFinishing(true);
      if (sid) router.replace(`/chat/${encodeURIComponent(sid)}`, { scroll: false });
      return;
    }
    flushNow();
    st.setStreaming(false);
    if (sid && sid === st.sessionId && hadTurns) {
      reloadBoundSession(sid);
    } else {
      st.resetPending();
    }
    refreshSessions();
    onClose();
    // 通话期间刻意没有 router.replace（见 onSessionBound 注释）；挂断后
    // 再把 URL 对齐到本次通话绑定的会话，深链/刷新语义与文字轮一致。
    if (sid && hadTurns) {
      router.replace(`/chat/${encodeURIComponent(sid)}`, { scroll: false });
    }
  }, [flushNow, onClose, refreshSessions, reloadBoundSession, router, voice]);

  // 接通（ready 起）板书常驻；connecting/ended 不算接通；挂断收尾全部隐藏。
  const boardActive = !finishing && (voice.phase === "ready" || voice.phase === "recording"
    || voice.phase === "recognizing" || voice.phase === "thinking" || voice.phase === "speaking");

  // 收尾期间本层只保留挂载（WS 与回调还在工作），不再渲染任何通话 UI。
  if (finishing) return null;

  return (
    <>
      <FormulaBoard
        lang={lang}
        sentence={voice.speakingSentence}
        table={voice.boardTable}
        active={boardActive}
        onClearTable={voice.clearBoardTable}
      />
      {/* 手机模拟独立于黑板条之外（absolute 定位在 .voice-phone），
          不挤占黑板宽度；与黑板同在接通（ready 起）后才出现。 */}
      {boardActive && <PhoneMockup lang={lang} voice={voice} />}
      <CallBar lang={lang} voice={voice} onHangUp={handleHangUp} />
    </>
  );
}
