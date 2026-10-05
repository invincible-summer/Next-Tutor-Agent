import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { useTheme } from "./ThemeProvider";
import { alpha } from "./theme";

/** 选择片：筛选/选项用的可按压胶囊。 */
export function Chip({
  label,
  active = false,
  onPress,
  disabled = false,
}: {
  label: string;
  active?: boolean;
  onPress?: () => void;
  disabled?: boolean;
}) {
  const { theme } = useTheme();
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      accessibilityRole="button"
      accessibilityState={{ selected: active, disabled }}
      style={({ pressed }) => [
        styles.chip,
        {
          backgroundColor: active
            ? theme.colors["accent-soft"]
            : pressed
              ? theme.colors["surface-hover"]
              : theme.colors.surface,
          borderColor: active
            ? alpha(theme.colors.accent, 0.4)
            : theme.colors.border,
          opacity: disabled ? 0.45 : 1,
        },
      ]}
    >
      <Text
        style={[
          theme.type.label,
          {
            color: active
              ? theme.colors["accent-strong"]
              : theme.colors["fg-secondary"],
          },
        ]}
        numberOfLines={1}
      >
        {label}
      </Text>
    </Pressable>
  );
}

/** 表单分区标题（对齐 Web FormSection）。 */
export function FormSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  const { theme } = useTheme();
  return (
    <View style={styles.section}>
      <Text
        style={[
          theme.type.label,
          {
            color: theme.colors["fg-tertiary"],
            marginBottom: 8,
            paddingHorizontal: 4,
          },
        ]}
      >
        {title}
      </Text>
      <View
        style={[
          styles.body,
          {
            backgroundColor: theme.colors.surface,
            borderColor: theme.colors["border-light"],
          },
        ]}
      >
        {children}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  chip: {
    borderRadius: 999,
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 7,
    minHeight: 36,
    justifyContent: "center",
  },
  section: { alignSelf: "stretch", marginBottom: 20 },
  body: { borderRadius: 12, borderWidth: 1, overflow: "hidden" },
});
