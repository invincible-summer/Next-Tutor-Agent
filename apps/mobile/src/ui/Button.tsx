import React from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
  type GestureResponderEvent,
  type StyleProp,
  type ViewStyle,
} from "react-native";

import { useTheme } from "./ThemeProvider";
import { alpha } from "./theme";
import { useReducedMotion } from "./useReducedMotion";

export type ButtonVariant =
  "primary" | "outline" | "ghost" | "danger" | "accent2";
export type ButtonSize = "sm" | "md" | "lg";

interface ButtonProps {
  title: string;
  onPress?: (event: GestureResponderEvent) => void;
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: React.ReactNode;
  leftIcon?: React.ReactNode;
  rightIcon?: React.ReactNode;
  testID?: string;
  disabled?: boolean;
  loading?: boolean;
  fullWidth?: boolean;
  style?: StyleProp<ViewStyle>;
  accessibilityLabel?: string;
}

const HEIGHTS: Record<ButtonSize, number> = { sm: 48, md: 48, lg: 54 };

export function Button({
  title,
  onPress,
  variant = "primary",
  size = "md",
  icon,
  leftIcon,
  rightIcon,
  testID,
  disabled = false,
  loading = false,
  fullWidth = false,
  style,
  accessibilityLabel,
}: ButtonProps) {
  const { theme } = useTheme();
  const reduced = useReducedMotion();
  const inactive = disabled || loading;

  const colors = {
    primary: {
      bg: theme.colors.accent,
      bgPressed: theme.colors["accent-strong"],
      fg: theme.colors.onAccent,
      border: theme.colors.accent,
    },
    accent2: {
      bg: theme.colors["accent-soft"],
      bgPressed: theme.colors["surface-hover"],
      fg: theme.colors["accent-strong"],
      border: "transparent",
    },
    danger: {
      bg: theme.colors.danger,
      bgPressed: theme.colors.danger,
      fg: "#fff",
      border: theme.colors.danger,
    },
    outline: {
      bg: "transparent",
      bgPressed: theme.colors["surface-hover"],
      fg: theme.colors.fg,
      border: theme.colors.border,
    },
    ghost: {
      bg: "transparent",
      bgPressed: theme.colors["surface-hover"],
      fg: theme.colors["fg-secondary"],
      border: "transparent",
    },
  }[variant];

  return (
    <Pressable
      testID={testID}
      onPress={onPress}
      disabled={inactive}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? title}
      accessibilityState={{ disabled: inactive, busy: loading }}
      style={({ pressed }) => [
        styles.base,
        {
          minHeight: HEIGHTS[size],
          paddingVertical: 12,
          paddingHorizontal: size === "sm" ? 12 : size === "md" ? 16 : 20,
          backgroundColor: pressed ? colors.bgPressed : colors.bg,
          borderColor: colors.border,
          opacity: inactive ? 0.5 : 1,
          // 复刻 Web active:scale-[0.97] 的按压反馈。
          transform: [{ scale: pressed && !inactive && !reduced ? 0.98 : 1 }],
        },
        variant === "primary" || variant === "accent2" ? theme.shadow.sm : null,
        fullWidth && styles.fullWidth,
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator size="small" color={colors.fg} />
      ) : (
        <View style={styles.content}>
          {icon || leftIcon ? (
            <View style={styles.icon}>{icon ?? leftIcon}</View>
          ) : null}
          <Text
            style={[
              theme.type.label,
              { color: colors.fg, flexShrink: 1, textAlign: "center" },
            ]}
          >
            {title}
          </Text>
          {rightIcon ? (
            <View style={{ marginLeft: 8 }}>{rightIcon}</View>
          ) : null}
        </View>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  base: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 14,
    borderWidth: 1,
    minWidth: 44,
  },
  content: { flexDirection: "row", alignItems: "center", flexShrink: 1 },
  icon: { marginRight: 6 },
  fullWidth: { alignSelf: "stretch" },
});

interface IconButtonProps {
  icon: React.ReactNode;
  onPress?: (event: GestureResponderEvent) => void;
  accessibilityLabel: string;
  disabled?: boolean;
  size?: number;
  tone?: "default" | "accent" | "danger";
  style?: StyleProp<ViewStyle>;
}

export function IconButton({
  icon,
  onPress,
  accessibilityLabel,
  disabled = false,
  size = 40,
  tone = "default",
  style,
}: IconButtonProps) {
  const { theme } = useTheme();
  const reduced = useReducedMotion();
  const bg =
    tone === "accent"
      ? theme.colors["accent-soft"]
      : tone === "danger"
        ? alpha(theme.colors.danger, 0.08)
        : "transparent";
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel}
      accessibilityState={{ disabled }}
      hitSlop={6}
      style={({ pressed }) => [
        {
          width: Math.max(size, 48),
          height: Math.max(size, 48),
          borderRadius: 14,
          alignItems: "center",
          justifyContent: "center",
          backgroundColor: pressed ? theme.colors["surface-hover"] : bg,
          opacity: disabled ? 0.4 : 1,
          transform: [{ scale: pressed && !disabled && !reduced ? 0.98 : 1 }],
        },
        style,
      ]}
    >
      {icon}
    </Pressable>
  );
}
