import React, { useEffect, useRef, useState } from "react";
import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { ChevronDown, Brain } from "lucide-react-native";

import { useTheme } from "@/ui/ThemeProvider";
import { alpha } from "@/ui/theme";
import { useI18n } from "@/providers/I18nProvider";
import { useReducedMotion } from "@/ui/useReducedMotion";

interface ThinkingBlockProps {
  text: string;
  /** 流式期间自动展开，实时预览真实推理（有界截断展示）。 */
  isStreaming?: boolean;
}

/** 思考块：可折叠的 muted 卡，标题行 + 正文（系统字体）。 */
export function ThinkingBlock({
  text,
  isStreaming = false,
}: ThinkingBlockProps) {
  const { theme } = useTheme();
  const { t } = useI18n();
  const reduced = useReducedMotion();
  const [open, setOpen] = useState(isStreaming);
  const chevron = useRef(new Animated.Value(isStreaming ? 1 : 0)).current;

  useEffect(() => {
    if (isStreaming) setOpen(true);
  }, [isStreaming]);

  useEffect(() => {
    Animated.timing(chevron, {
      toValue: open ? 1 : 0,
      duration: reduced ? 0 : 180,
      useNativeDriver: true,
    }).start();
  }, [open, chevron, reduced]);

  if (!text) return null;
  const rotate = chevron.interpolate({
    inputRange: [0, 1],
    outputRange: ["-90deg", "0deg"],
  });
  // 有界展示：长推理只露尾部 1200 字（折叠时由 numberOfLines 再截断）。
  const shown = text.length > 1200 ? `…${text.slice(-1200)}` : text;

  return (
    <View
      style={[
        styles.box,
        {
          borderColor: alpha(theme.colors.border, 0.8),
          backgroundColor: alpha(theme.colors["surface-hover"], 0.5),
        },
      ]}
    >
      <Pressable
        onPress={() => setOpen((v) => !v)}
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        accessibilityLabel={t("thinking.title")}
        style={styles.header}
      >
        <Brain size={12} color={theme.colors.muted} />
        <Text
          style={[
            theme.type.caption,
            { color: theme.colors.muted, marginLeft: 6, flex: 1 },
          ]}
        >
          {t("thinking.title")}
        </Text>
        <Animated.View style={{ transform: [{ rotate }] }}>
          <ChevronDown size={12} color={theme.colors.muted} />
        </Animated.View>
      </Pressable>
      {open ? (
        <Text
          style={[
            theme.type.caption,
            {
              color: theme.colors["fg-tertiary"],
              fontFamily: theme.fonts.sans,
              fontStyle: "italic",
              marginTop: 6,
              lineHeight: 19,
            },
          ]}
        >
          {shown}
        </Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  box: {
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 8,
    marginBottom: 6,
  },
  header: { flexDirection: "row", alignItems: "center", minHeight: 48 },
});
