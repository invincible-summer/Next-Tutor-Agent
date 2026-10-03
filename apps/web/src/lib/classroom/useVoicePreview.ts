/* 语音试听 hook（POST W/voice-preview）。
 *
 * createVoicePreview（服务端同步合成固定试听句，幂等键防重）→
 * fetchVoicePreviewBlobUrl 拉 WAV → new Audio() 播放 → ended/error/停止时
 * 撤销 Object URL。再次点击/组件卸载作废旧请求（seq 递增，迟到响应丢弃）。
 * 试听走独立通道，不占用课堂音频控制器；调用方负责在试听前暂停讲授。
 */
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  createVoicePreview, fetchVoicePreviewBlobUrl,
} from "@/lib/api-classroom";
import type {
  LessonLanguage, VoicePreferences,
} from "@/lib/types-classroom.generated";

export interface VoicePreviewRequest {
  language: LessonLanguage;
  voicePreferences: VoicePreferences;
}

export interface VoicePreviewApi {
  /** 正在生成/播放的试听 key（通常传 voice_id）；null = 空闲。 */
  previewing: string | null;
  /** 上一次试听失败（网络/配额/语音不可用）。 */
  failed: boolean;
  preview: (req: VoicePreviewRequest, key?: string) => Promise<void>;
  stop: () => void;
}

export function useVoicePreview(workspaceId: string): VoicePreviewApi {
  const [previewing, setPreviewing] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);
  const seqRef = useRef(0);

  const stop = useCallback(() => {
    seqRef.current += 1;
    audioRef.current?.pause();
    audioRef.current = null;
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    }
    setPreviewing(null);
  }, []);

  // 卸载即停声并撤销 URL（面板关闭=组件卸载时试听不残留）
  useEffect(() => stop, [stop]);

  const preview = useCallback(async (req: VoicePreviewRequest, key = "") => {
    stop();
    const seq = seqRef.current;
    setFailed(false);
    setPreviewing(key);
    try {
      const idem = `vp-${Date.now().toString(36)}-${
        Math.random().toString(36).slice(2, 10)}`;
      const res = await createVoicePreview(workspaceId, {
        language: req.language,
        voice_preferences: req.voicePreferences,
      }, idem);
      if (seq !== seqRef.current) return;
      const url = await fetchVoicePreviewBlobUrl(workspaceId, res.clip_id);
      if (seq !== seqRef.current) {
        URL.revokeObjectURL(url);
        return;
      }
      urlRef.current = url;
      const audio = new Audio(url);
      audioRef.current = audio;
      const done = () => {
        if (urlRef.current === url) {
          URL.revokeObjectURL(url);
          urlRef.current = null;
        }
        if (audioRef.current === audio) audioRef.current = null;
        setPreviewing((cur) => (cur === key ? null : cur));
      };
      audio.onended = done;
      audio.onerror = done;
      await audio.play();
    } catch {
      if (seq === seqRef.current) {
        setFailed(true);
        setPreviewing(null);
      }
    }
  }, [workspaceId, stop]);

  return { previewing, failed, preview, stop };
}
