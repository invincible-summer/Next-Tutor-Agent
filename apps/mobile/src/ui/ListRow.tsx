import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { useTheme } from "./ThemeProvider";
import { Badge, type BadgeTone } from "./Badge";

interface ListRowProps {
  title: string;
  subtitle?: string | undefined;
  left?: React.ReactNode;
  right?: React.ReactNode;
  badge?: { label: string; tone?: BadgeTone };
  onPress?: () => void;
  accessibilityLabel?: string;
}

/** 通用列表行：左图标位 + 标题/副标题 + badge + 右侧位。 */
export function ListRow({
  title,
  subtitle,
  left,
  right,
  badge,
  onPress,
  accessibilityLabel,
}: ListRowProps) {
  const { theme } = useTheme();
  return (
    <Pressable
      onPress={onPress}
      disabled={!onPress}
      accessibilityRole={onPress ? "button" : "text"}
      accessibilityLabel={accessibilityLabel ?? title}
      style={({ pressed }) => [
        styles.row,
        {
          backgroundColor: pressed
            ? theme.colors["surface-hover"]
            : "transparent",
          minHeight: 48,
        },
      ]}
    >
      {left ? <View style={styles.left}>{left}</View> : null}
      <View style={styles.body}>
        <View style={styles.titleRow}>
          <Text
            style={[theme.type.body, { color: theme.colors.fg, flexShrink: 1 }]}
          >
            {title}
          </Text>
          {badge ? (
            <View style={{ marginLeft: 8 }}>
              <Badge label={badge.label} tone={badge.tone ?? "muted"} />
            </View>
          ) : null}
        </View>
        {subtitle ? (
          <Text
            style={[
              theme.type.caption,
              { color: theme.colors["fg-tertiary"], marginTop: 1 },
            ]}
          >
            {subtitle}
          </Text>
        ) : null}
      </View>
      {right ? <View style={styles.right}>{right}</View> : null}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 8,
  },
  left: { marginRight: 12 },
  body: { flex: 1, minWidth: 0 },
  titleRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    alignItems: "center",
    gap: 6,
  },
  right: { marginLeft: 12 },
});
