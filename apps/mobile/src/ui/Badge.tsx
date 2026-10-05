import React from "react";
import {
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type ViewStyle,
} from "react-native";

import { useTheme } from "./ThemeProvider";
import { alpha } from "./theme";

export type BadgeTone =
  | "accent"
  | "accent2"
  | "success"
  | "warning"
  | "danger"
  | "info"
  | "muted"
  | "outline";

interface BadgeProps {
  label: string;
  tone?: BadgeTone;
  /** 左侧状态点。 */
  dot?: boolean;
  style?: StyleProp<ViewStyle>;
}

/** 徽章：soft 底 + strong 字（对齐 Web Badge 语义色）。 */
export function Badge({
  label,
  tone = "muted",
  dot = false,
  style,
}: BadgeProps) {
  const { theme } = useTheme();
  const c = theme.colors;
  const tones: Record<BadgeTone, { bg: string; fg: string; border: string }> = {
    accent: {
      bg: c["accent-soft"],
      fg: c["accent-strong"],
      border: "transparent",
    },
    accent2: {
      bg: c["accent2-soft"],
      fg: c["accent2-strong"],
      border: "transparent",
    },
    success: {
      bg: alpha(c.success, 0.12),
      fg: c.success,
      border: "transparent",
    },
    warning: {
      bg: alpha(c.warning, 0.12),
      fg: c.warning,
      border: "transparent",
    },
    danger: { bg: alpha(c.danger, 0.12), fg: c.danger, border: "transparent" },
    info: { bg: alpha(c.info, 0.12), fg: c.info, border: "transparent" },
    muted: {
      bg: c["surface-hover"],
      fg: c["fg-secondary"],
      border: "transparent",
    },
    outline: { bg: "transparent", fg: c["fg-secondary"], border: c.border },
  };
  const colors = tones[tone];
  return (
    <View
      style={[
        styles.badge,
        { backgroundColor: colors.bg, borderColor: colors.border },
        style,
      ]}
      accessibilityRole="text"
    >
      {dot ? (
        <View style={[styles.dot, { backgroundColor: colors.fg }]} />
      ) : null}
      <Text
        style={[theme.type.caption, { color: colors.fg, fontSize: 11 }]}
        numberOfLines={1}
      >
        {label}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  badge: {
    flexDirection: "row",
    alignItems: "center",
    alignSelf: "flex-start",
    borderRadius: 999,
    borderWidth: 1,
    paddingHorizontal: 8,
    paddingVertical: 2,
  },
  dot: { width: 6, height: 6, borderRadius: 3, marginRight: 5 },
});
