import React, { useEffect, useRef, useState } from "react";
import { AppState } from "react-native";
import { File } from "expo-file-system";
import {
  AudioModule,
  RecordingPresets,
  setAudioModeAsync,
  useAudioRecorder,
  useAudioRecorderState,
} from "expo-audio";
import { Mic, Square } from "lucide-react-native";
import { apiClient } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { errorMessage } from "@/lib/feedback";
import { useServerQuery } from "@/lib/server-state";
import { IconButton, useTheme, useToast } from "@/ui";
export function VoiceInput({
  onText,
  disabled = false,
}: {
  onText: (text: string) => void;
  disabled?: boolean;
}) {
  const c = useCopy();
  const { theme } = useTheme();
  const toast = useToast();
  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const status = useAudioRecorderState(recorder);
  const [pending, setPending] = useState(false);
  const busy = useRef(false);
  const ctl = useRef<AbortController | null>(null);
  const capabilities = useServerQuery(
    ["capabilities"],
    (s) => apiClient().capabilities.get(s),
    { public: true },
  );
  const remove = () => {
    if (recorder.uri) {
      try {
        const file = new File(recorder.uri);
        if (file.exists) file.delete();
      } catch {}
    }
  };
  useEffect(() => {
    const app = AppState.addEventListener("change", (next) => {
      if (next !== "active") {
        ctl.current?.abort();
        if (recorder.isRecording) void recorder.stop().finally(remove);
      }
    });
    return () => {
      app.remove();
      ctl.current?.abort();
      if (recorder.isRecording) void recorder.stop().finally(remove);
      else remove();
    };
  }, [recorder]);
  async function toggle() {
    if (busy.current) return;
    busy.current = true;
    try {
      if (recorder.isRecording) {
        const duration = recorder.getStatus().durationMillis;
        await recorder.stop();
        setPending(true);
        const controller = new AbortController();
        ctl.current = controller;
        if (!recorder.uri) return;
        const result = await apiClient().voice.transcribe(
          new FormData(),
          {
            file: {
              data: {
                uri: recorder.uri,
                name: "utterance.m4a",
                type: "audio/mp4",
              },
            },
            durationMs: duration,
          },
          { signal: controller.signal },
        );
        if (!controller.signal.aborted) onText(result.text);
        remove();
        setPending(false);
      } else {
        const permission = await AudioModule.requestRecordingPermissionsAsync();
        if (!permission.granted) {
          toast(
            c(
              "麦克风未获授权，可以继续文字输入。",
              "Microphone access was denied. Text input is still available.",
            ),
            "info",
          );
          return;
        }
        await setAudioModeAsync({
          allowsRecording: true,
          playsInSilentMode: true,
          shouldPlayInBackground: false,
        });
        await recorder.prepareToRecordAsync();
        recorder.record({ forDuration: 60 });
      }
    } catch (e) {
      toast(errorMessage(e, c), "error");
      remove();
      setPending(false);
    } finally {
      busy.current = false;
    }
  }
  if (!capabilities.data?.cloud_stt.available) return null;
  return (
    <IconButton
      icon={
        status.isRecording ? (
          <Square size={20} color={theme.colors.danger} />
        ) : (
          <Mic size={20} color={theme.colors.accent} />
        )
      }
      accessibilityLabel={
        status.isRecording
          ? c("停止录音并转写", "Stop recording and transcribe")
          : pending
            ? c("正在转写", "Transcribing")
            : c("语音输入", "Voice input")
      }
      disabled={disabled || pending}
      onPress={() => void toggle()}
    />
  );
}
