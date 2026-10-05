import React, { useEffect, useRef } from "react";
import { AppState } from "react-native";
import { File, Paths } from "expo-file-system";
import { randomUUID } from "expo-crypto";
import {
  setAudioModeAsync,
  useAudioPlayer,
  useAudioPlayerStatus,
} from "expo-audio";
import { Volume2, Square } from "lucide-react-native";
import { apiClient } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { useAction, useServerQuery } from "@/lib/server-state";
import { IconButton, useTheme } from "@/ui";
export function SpeechButton({ text }: { text: string }) {
  const c = useCopy();
  const { theme } = useTheme();
  const action = useAction();
  const player = useAudioPlayer();
  const status = useAudioPlayerStatus(player);
  const file = useRef<File | null>(null);
  const capabilities = useServerQuery(
    ["capabilities"],
    (s) => apiClient().capabilities.get(s),
    { public: true },
  );
  useEffect(() => {
    const sub = AppState.addEventListener("change", (s) => {
      if (s !== "active") player.pause();
    });
    return () => {
      sub.remove();
      player.pause();
      if (file.current?.exists) file.current.delete();
    };
  }, [player]);
  if (!capabilities.data?.cloud_tts.available) return null;
  return (
    <IconButton
      accessibilityLabel={
        status.playing
          ? c("停止朗读", "Stop reading")
          : c("朗读消息", "Read message aloud")
      }
      icon={
        status.playing ? (
          <Square size={17} color={theme.colors.accent} />
        ) : (
          <Volume2 size={17} color={theme.colors.muted} />
        )
      }
      disabled={action.pending}
      onPress={() => {
        if (status.playing) {
          player.pause();
          return;
        }
        void action.run(async () => {
          const audio = await apiClient().voice.synthesize({
            text: text.slice(0, 6000),
            policy: "cloud",
            allow_local_fallback: false,
          });
          if (file.current?.exists) file.current.delete();
          const f = new File(Paths.cache, "nt-tts-" + randomUUID() + ".wav");
          f.write(new Uint8Array(audio.audio));
          file.current = f;
          await setAudioModeAsync({
            allowsRecording: false,
            playsInSilentMode: true,
            shouldPlayInBackground: false,
          });
          player.replace(f.uri);
          player.play();
        });
      }}
    />
  );
}
