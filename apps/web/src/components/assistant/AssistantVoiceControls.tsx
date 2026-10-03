"use client";

// 助手语音控件（B11）：录入（浏览器语音识别 → 可编辑输入
// 框，不自动发送）与朗读（合成任务 → 片段顺序播放；焦点规则 §24.5）。
// 识别能力随浏览器；不支持时按钮禁用并说明，打字与朗读不受影响。
import { useCallback, useEffect, useRef, useState } from "react";
import { Mic, MicOff, Square, Volume2 } from "lucide-react";
import { apiFetch } from "@/lib/api-fetch";
import { API_BASE } from "@/lib/api";
import { BrowserRecognition } from "@/lib/voice/browser-recognition";
import { acquireAudioFocus, releaseAudioFocus } from "@/lib/classroom/audio-focus";
import { useAssistantStore } from "@/lib/assistant/store";
import { stringsFor } from "./strings";

interface AudioJob {
  job_id: string;
  state: "queued" | "running" | "ready" | "failed" | "cancelled";
  clips: Array<{ clip_id: string; state: string }>;
  truncated: boolean;
}

const MAX_RECORD_SECONDS = 60;

export function AssistantVoiceControls() {
  const lang = useAssistantStore((s) => s.lang);
  const setDraft = useAssistantStore((s) => s.setDraft);
  const draft = useAssistantStore((s) => s.draft);
  const [recording, setRecording] = useState(false);
  const [recognizing, setRecognizing] = useState(false);
  const [recordError, setRecordError] = useState("");
  const [reading, setReading] = useState(false);
  const recognitionRef = useRef<BrowserRecognition | null>(null);
  const baseTextRef = useRef("");
  const stopTimerRef = useRef<number | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const jobIdRef = useRef<string | null>(null);
  const cancelledRef = useRef(false);

  const stopRecording = useCallback(() => {
    if (stopTimerRef.current) {
      window.clearTimeout(stopTimerRef.current);
      stopTimerRef.current = null;
    }
    recognitionRef.current?.stop();
    recognitionRef.current = null;
    setRecording(false);
  }, []);

  const startRecording = useCallback(() => {
    if (!BrowserRecognition.supported()) return;
    setRecordError("");
    baseTextRef.current = draft ? draft + "\n" : "";
    const recognition = new BrowserRecognition();
    recognitionRef.current = recognition;
    setRecording(true);
    // §24.1：单次录入最多 60 秒，到时自动停止。
    stopTimerRef.current = window.setTimeout(stopRecording,
                                             MAX_RECORD_SECONDS * 1000);
    recognition.start(lang === "en" ? "en" : "zh", {
      onText: (finalText, interim) => {
        // 识别结果先进入可编辑输入框，不自动发送（§24.1）。
        setDraft(baseTextRef.current + finalText + interim);
      },
      onError: (code) => {
        setRecordError(code === "not-allowed"
          ? (lang === "en" ? "Microphone permission denied."
                           : "麦克风权限被拒绝。")
          : (lang === "en" ? "Recognition error." : "识别出错，请重试。"));
        stopRecording();
      },
    });
  }, [draft, lang, setDraft, stopRecording]);

  const stopReading = useCallback(() => {
    cancelledRef.current = true;
    const audio = audioRef.current;
    if (audio) {
      audio.pause();
      audio.src = "";
    }
    audioRef.current = null;
    if (jobIdRef.current) {
      releaseAudioFocus(`assistant:${jobIdRef.current}`);
      void apiFetch(`${API_BASE}/assistant/audio/jobs/${jobIdRef.current}`
        + "/cancel", { method: "POST" }).catch(() => undefined);
      jobIdRef.current = null;
    }
    setReading(false);
  }, []);

  useEffect(() => {
    // 挂载探测识别能力走微任务：避免 effect 体内同步 setState 的级联
    // 渲染（react-hooks/set-state-in-effect；与深链消费同一约定）。
    let alive = true;
    queueMicrotask(() => {
      if (alive) setRecognizing(BrowserRecognition.supported());
    });
    return () => {
      alive = false;
      stopRecording();
      stopReading();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 片段顺序播放：递归经 ref 分派（编译器不可变性的自引用限制）。
  const playClipRef = useRef<(job: AudioJob, index: number) => void>(
    () => undefined);

  const playClip = useCallback((job: AudioJob, index: number) => {
    if (cancelledRef.current) return;
    const clip = job.clips[index];
    if (!clip || clip.state !== "ready") {
      // 片段未就绪/结束：正常收尾（部分失败按已完成片段收听）。
      releaseAudioFocus(`assistant:${job.job_id}`);
      jobIdRef.current = null;
      setReading(false);
      return;
    }
    const audio = new Audio(
      `${API_BASE}/assistant/audio/clips/${clip.clip_id}/content`);
    audioRef.current = audio;
    const stillFocus = acquireAudioFocus(
      `assistant:${job.job_id}`, () => audio.pause());
    if (!stillFocus) {
      releaseAudioFocus(`assistant:${job.job_id}`);
      setReading(false);
      return;
    }
    audio.onended = () => {
      releaseAudioFocus(`assistant:${job.job_id}`);
      playClipRef.current(job, index + 1);
    };
    audio.onerror = () => {
      releaseAudioFocus(`assistant:${job.job_id}`);
      setReading(false);
    };
    void audio.play().catch(() => {
      // 浏览器禁止自动播放：显示「点击播放」，不伪装播放中（§24.1）。
      setReading(false);
      releaseAudioFocus(`assistant:${job.job_id}`);
    });
  }, []);

  useEffect(() => {
    playClipRef.current = playClip;
  }, [playClip]);

  const waitForJob = useCallback(async (jobId: string) => {
    for (let waited = 0; waited < 120_000; waited += 800) {
      const job = (await apiFetch(
        `${API_BASE}/assistant/audio/jobs/${jobId}`)
        .then((r: Response) => (r.ok ? r.json()
          : Promise.reject(new Error("job fetch failed"))))) as AudioJob;
      if (job.state === "ready" || job.state === "failed"
          || job.state === "cancelled") {
        return job;
      }
      await new Promise((r) => setTimeout(r, 800));
    }
    return null;
  }, []);

  const startReading = useCallback(async () => {
    const messages = useAssistantStore.getState().messages;
    const last = [...messages].reverse()
      .find((m) => m.role === "assistant");
    if (!last?.message_id) return;
    cancelledRef.current = false;
    setReading(true);
    try {
      const resp = (await apiFetch(
        `${API_BASE}/assistant/audio/jobs`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message_id: last.message_id,
            policy: "auto",
            language: lang === "en" ? "en" : "zh",
            client_request_id: crypto.randomUUID(),
          }),
        }).then((r: Response) => (r.ok ? r.json()
          : Promise.reject(new Error("audio job rejected"))))) as AudioJob;
      jobIdRef.current = resp.job_id;
      const job = resp.state === "ready" ? resp : await waitForJob(resp.job_id);
      if (!job || job.state !== "ready") {
        setReading(false);
        return;
      }
      playClip(job, 0);
    } catch {
      setReading(false);
    }
  }, [lang, playClip, waitForJob]);

  return (
    <div className="assistant-voice-controls">
      {recognizing ? (
        <button
          type="button"
          className="assistant-icon-btn"
          title={recording ? stringsFor(lang).voiceStopRecording : stringsFor(lang).voiceHold}
          aria-label={recording ? stringsFor(lang).voiceStopRecording : stringsFor(lang).voiceHold}
          onPointerDown={(e) => { e.preventDefault(); startRecording(); }}
          onPointerUp={() => stopRecording()}
          onPointerLeave={() => { if (recording) stopRecording(); }}
        >
          {recording
            ? <Square size={15} aria-hidden />
            : <Mic size={15} aria-hidden />}
        </button>
      ) : (
        <span className="assistant-voice-unsupported"
              title={lang === "en"
                ? "Browser speech recognition unavailable"
                : "当前浏览器不支持语音识别，可继续打字与朗读"}>
          <MicOff size={15} aria-hidden />
        </span>
      )}
      <button
        type="button"
        className="assistant-icon-btn"
        aria-label={reading ? stringsFor(lang).voiceStopReading : stringsFor(lang).voiceReadLatest}
        onClick={() => { if (reading) stopReading(); else void startReading(); }}
      >
        <Volume2 size={15} aria-hidden />
      </button>
      {recording && (
        <span className="assistant-voice-hint">
          {lang === "en" ? "recording…" : "录入中…"}
        </span>
      )}
      {recordError && (
        <span className="assistant-voice-error" role="alert">
          {recordError}
        </span>
      )}
      {reading && (
        <span className="assistant-voice-hint">
          {stringsFor(lang).voiceReading}
        </span>
      )}
    </div>
  );
}
