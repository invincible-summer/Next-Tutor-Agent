"use client";
/* 课堂插问 hook（plan.md §12.4/§12.5，阶段 H02）。
 *
 * 首条提问经 classroom_ref 由服务端创建/复用答疑 session；回答文字实时
 * 显示；done 后从 session 尾部取 reply message_id，按句合成回复语音
 * （qa-audio，只接服务端保存的回复）。再次提问立即停止上一回复声音；
 * 原课堂 resume anchor 只保留最初一次（服务端语义），前端「继续原课」
 * 跳回 anchor 段。ask 不发送 end/complete——插问永不使 run 自动结束。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api-fetch";
import { chatStream } from "@/lib/api";
import { API_BASE } from "@/lib/api";
import {
  acquireAudioFocus, releaseAudioFocus,
} from "@/lib/classroom/audio-focus";
import type { FlatSegment } from "@/lib/classroom/useClassroomPlayer";

export const QUICK_CONFUSED = "请用更直观的方式解释刚才这一段，我还没有听懂。";
export const QUICK_EXAMPLE = "请针对刚才讲的概念举一个具体的例子。";

export interface QATurn {
  question: string;
  answer: string;
  done: boolean;
  error?: string;
}

interface QADeps {
  workspaceId: string;
  lessonId: string;
  runId: string;
  leaseEpoch: number;
  current: FlatSegment | null;
  lessonRevision: number;
  withVoice: boolean;
  /** 提问时暂停讲授；恢复由用户点击（§5.2.6）。 */
  pauseNarration: () => void;
}

export interface QAApi {
  turns: QATurn[];
  asking: boolean;
  ask: (text: string) => void;
  stopAudio: () => void;
  qaSessionId: string | null;
}

interface SessionTail {
  messages?: {
    message_id?: string;
    role?: string;
  }[];
}

export function useClassroomQA(deps: QADeps): QAApi {
  const [turns, setTurns] = useState<QATurn[]>([]);
  const [asking, setAsking] = useState(false);
  const [qaSessionId, setQaSessionId] = useState<string | null>(null);
  const sessionRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const focusKeyRef = useRef<string>("");

  const stopAudio = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = "";
      audioRef.current = null;
    }
    if (focusKeyRef.current) {
      releaseAudioFocus(focusKeyRef.current);
      focusKeyRef.current = "";
    }
  }, []);

  useEffect(() => stopAudio, [stopAudio]);

  const playReplyAudio = useCallback(async (replyMessageId: string) => {
    if (!deps.withVoice) return;
    try {
      const key = `qa-${deps.runId}-${Date.now().toString(36)}`;
      const res = await apiFetch(
        `${API_BASE}/workspaces/${encodeURIComponent(deps.workspaceId)}` +
        `/classroom/lessons/${encodeURIComponent(deps.lessonId)}` +
        `/runs/${encodeURIComponent(deps.runId)}/qa-audio`,
        { method: "POST",
          headers: { "Content-Type": "application/json",
                     "Idempotency-Key": key },
          body: JSON.stringify({ reply_message_id: replyMessageId,
                                 lease_epoch: deps.leaseEpoch }) });
      if (!res.ok) return;
      const { clips } = await res.json() as {
        clips: { clip_id: string; state: string }[] };
      const ready: string[] = [];
      const pending: string[] = [];
      for (const c of clips ?? []) {
        (c.state === "ready" ? ready : pending).push(c.clip_id);
      }
      // pending 句最多轮询 60s；用户随时可停止（§14.2 音频轮询退避）
      const deadline = Date.now() + 60_000;
      while (pending.length && Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 1000));
        for (let i = pending.length - 1; i >= 0; i -= 1) {
          const st = await apiFetch(
            `${API_BASE}/workspaces/${encodeURIComponent(deps.workspaceId)}` +
            `/classroom/lessons/${encodeURIComponent(deps.lessonId)}` +
            `/runs/${encodeURIComponent(deps.runId)}` +
            `/audio/${encodeURIComponent(pending[i])}`);
          if (!st.ok) { pending.splice(i, 1); continue; }
          const body = await st.json() as { state?: string };
          if (body.state === "ready") {
            ready.push(pending.splice(i, 1)[0]);
          } else if (body.state === "failed") {
            pending.splice(i, 1);
          }
        }
      }
      for (const clipId of ready) {
        const cres = await apiFetch(
          `${API_BASE}/workspaces/${encodeURIComponent(deps.workspaceId)}` +
          `/classroom/lessons/${encodeURIComponent(deps.lessonId)}` +
          `/runs/${encodeURIComponent(deps.runId)}` +
          `/audio/${encodeURIComponent(clipId)}/content`);
        if (!cres.ok) continue;
        const blob = await cres.blob();
        const url = URL.createObjectURL(blob);
        await new Promise<void>((resolve) => {
          const el = new Audio(url);
          audioRef.current = el;
          focusKeyRef.current = `classroom-qa-${deps.runId}`;
          acquireAudioFocus(focusKeyRef.current, () => el.pause());
          el.onended = () => { URL.revokeObjectURL(url); resolve(); };
          el.onerror = () => { URL.revokeObjectURL(url); resolve(); };
          void el.play().catch(() => { URL.revokeObjectURL(url); resolve(); });
        });
        audioRef.current = null;
        releaseAudioFocus(focusKeyRef.current);
        focusKeyRef.current = "";
      }
    } catch {
      /* 语音失败不隐藏文字回复（§5.2.13） */
    }
  }, [deps.withVoice, deps.workspaceId, deps.lessonId, deps.runId,
      deps.leaseEpoch]);

  const ask = useCallback((text: string) => {
    const trimmed = text.trim();
    if (!trimmed || asking) return;
    const seg = deps.current;
    if (!seg) return;
    stopAudio();
    deps.pauseNarration();
    setAsking(true);
    setTurns((prev) => [...prev, { question: trimmed, answer: "", done: false }]);
    const controller = new AbortController();
    abortRef.current = controller;
    let answerText = "";

    void (async () => {
      try {
        for await (const ev of chatStream({
          message: trimmed,
          session_id: sessionRef.current,
          classroom_ref: {
            run_id: deps.runId,
            lesson_revision: deps.lessonRevision,
            slide_id: seg.slideId,
            segment_id: seg.segmentId,
            workspace_id: deps.workspaceId,
            lesson_id: deps.lessonId,
          },
        }, controller.signal)) {
          if (controller.signal.aborted) return;
          if (ev.type === "answer" && typeof ev.content === "string") {
            answerText += ev.content;
            setTurns((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last) next[next.length - 1] = { ...last, answer: answerText };
              return next;
            });
          } else if (ev.type === "done") {
            if (typeof ev.session_id === "string"
                && ev.session_id !== sessionRef.current) {
              sessionRef.current = ev.session_id;
              setQaSessionId(ev.session_id);
            }
            if (typeof ev.answer === "string" && ev.answer.length
                > answerText.length) {
              answerText = ev.answer;
              setTurns((prev) => {
                const next = [...prev];
                const last = next[next.length - 1];
                if (last) next[next.length - 1] = { ...last,
                                                    answer: answerText };
                return next;
              });
            }
            // 从 session 尾部取已保存回复的 message_id（qa-audio 引用）
            const sid = sessionRef.current;
            if (sid) {
              try {
                const sres = await apiFetch(
                  `${API_BASE}/chat/sessions/${encodeURIComponent(sid)}?tail=2`);
                if (sres.ok) {
                  const sbody = await sres.json() as SessionTail;
                  const reply = [...(sbody.messages ?? [])]
                    .reverse().find((m) => m.role === "assistant"
                                     && m.message_id);
                  if (reply?.message_id) {
                    void playReplyAudio(reply.message_id);
                  }
                }
              } catch { /* 语音可选 */ }
            }
            setTurns((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last) next[next.length - 1] = { ...last, answer: answerText,
                                                  done: true };
              return next;
            });
            return;
          } else if (ev.type === "error") {
            const message = typeof ev.message === "string"
              ? ev.message : "回答失败，请重试。";
            setTurns((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last) next[next.length - 1] = { ...last, done: true,
                                                  error: message };
              return next;
            });
            return;
          }
        }
      } catch {
        setTurns((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last && !last.done) {
            next[next.length - 1] = { ...last, done: true,
                                      error: "网络中断，请重试。" };
          }
          return next;
        });
      } finally {
        setAsking(false);
        abortRef.current = null;
      }
    })();
  }, [asking, deps, stopAudio, playReplyAudio]);

  return { turns, asking, ask, stopAudio, qaSessionId };
}
