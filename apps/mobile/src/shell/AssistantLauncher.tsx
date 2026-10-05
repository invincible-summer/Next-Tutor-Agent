import React from "react";
import { Pressable, View, useWindowDimensions } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useSegments } from "expo-router";
import { Sparkles } from "lucide-react-native";
import { useAuth } from "@/providers/AuthProvider";
import { useAssistantUi } from "@/stores/assistant-ui";
import { useCopy } from "@/lib/copy";
import { useTheme } from "@/ui/ThemeProvider";
/** A quiet edge handle; its inward hit area remains at least 48 dp wide. */
export function AssistantLauncher() {
  const { state } = useAuth();
  const { open, setOpen } = useAssistantUi();
  const { theme } = useTheme();
  const c = useCopy();
  const insets = useSafeAreaInsets();
  const { height } = useWindowDimensions();
  const segments: string[] = useSegments();
  if (
    open ||
    segments[0] === "(auth)" ||
    !["signed-in", "guest"].includes(state.status)
  )
    return null;
  return (
    <View
      pointerEvents="box-none"
      style={{
        position: "absolute",
        right: insets.right,
        top: Math.max(insets.top + 88, height * 0.42),
      }}
    >
      <Pressable
        testID="assistant-edge-handle"
        accessibilityRole="button"
        accessibilityLabel={c("打开导航助手", "Open navigation assistant")}
        accessibilityHint={c(
          "打开学习导航和跨页面操作面板",
          "Open learning navigation and actions",
        )}
        hitSlop={{ left: 18, top: 8, bottom: 8, right: 0 }}
        onPress={() => setOpen(true)}
        style={({ pressed }) => ({
          width: 48,
          minHeight: 56,
          right: -18,
          paddingRight: 15,
          alignItems: "center",
          justifyContent: "center",
          borderTopLeftRadius: 18,
          borderBottomLeftRadius: 18,
          borderWidth: 1,
          borderRightWidth: 0,
          borderColor: theme.colors.border,
          backgroundColor: pressed
            ? theme.colors["accent-soft"]
            : theme.colors.surface,
          opacity: pressed ? 1 : 0.94,
        })}
      >
        <Sparkles size={19} color={theme.colors.accent} />
      </Pressable>
    </View>
  );
}
