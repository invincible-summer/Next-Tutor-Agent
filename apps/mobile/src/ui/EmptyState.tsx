import React, { useEffect, useRef } from "react";
import { Animated, StyleSheet, Text, View } from "react-native";

import { useReducedMotion } from "./useReducedMotion";
import { useTheme } from "./ThemeProvider";
import { alpha } from "./theme";
import { Button } from "./Button";

interface EmptyStateProps {
  icon?: React.ReactNode;
  title: string;
  hint?: string;
  actionLabel?: string;
  onAction?: () => void;
}

/** 空态：虚线边框 + accent-soft 圆形图标座（对齐 Web EmptyState）。 */
export function EmptyState({
  icon,
  title,
  hint,
  actionLabel,
  onAction,
}: EmptyStateProps) {
  const { theme } = useTheme();
  return (
    <View
      style={[
        styles.empty,
        { borderColor: theme.colors.border, backgroundColor: "transparent" },
      ]}
    >
      {icon ? (
        <View
          style={[
            styles.iconSeat,
            { backgroundColor: alpha(theme.colors.accent, 0.1) },
          ]}
        >
          {icon}
        </View>
      ) : null}
      <Text
        style={[
          theme.type.bodyStrong,
          { color: theme.colors.fg, textAlign: "center" },
        ]}
      >
        {title}
      </Text>
      {hint ? (
        <Text
          style={[
            theme.type.caption,
            {
              color: theme.colors["fg-tertiary"],
              textAlign: "center",
              marginTop: 4,
            },
          ]}
        >
          {hint}
        </Text>
      ) : null}
      {actionLabel && onAction ? (
        <View style={{ marginTop: 12 }}>
          <Button
            title={actionLabel}
            variant="outline"
            size="sm"
            onPress={onAction}
          />
        </View>
      ) : null}
    </View>
  );
}

export function ErrorState({
  title,
  onRetry,
  retryLabel,
}: {
  title: string;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  const { theme } = useTheme();
  return (
    <View
      style={[
        styles.error,
        {
          borderColor: alpha(theme.colors.danger, 0.3),
          backgroundColor: alpha(theme.colors.danger, 0.08),
        },
      ]}
    >
      <Text style={[theme.type.label, { color: theme.colors.danger, flex: 1 }]}>
        {title}
      </Text>
      {onRetry ? (
        <Button
          title={retryLabel ?? "重试"}
          variant="ghost"
          size="sm"
          onPress={onRetry}
        />
      ) : null}
    </View>
  );
}

/** 骨架块：透明度呼吸（对齐 Web .skeleton shimmer 1.6s）。 */
export function Skeleton({
  width,
  height = 14,
  radius = 6,
  style,
}: {
  width?: number | `${number}%`;
  height?: number;
  radius?: number;
  style?: object;
}) {
  const { theme } = useTheme();
  const reduced = useReducedMotion();
  const opacity = useRef(new Animated.Value(0.5)).current;

  useEffect(() => {
    if (reduced) {
      opacity.setValue(1);
      return;
    }
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, {
          toValue: 1,
          duration: 800,
          useNativeDriver: true,
        }),
        Animated.timing(opacity, {
          toValue: 0.5,
          duration: 800,
          useNativeDriver: true,
        }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [opacity, reduced]);

  return (
    <Animated.View
      style={[
        {
          width: width ?? "100%",
          height,
          borderRadius: radius,
          backgroundColor: theme.colors["surface-hover"],
          opacity,
        },
        style,
      ]}
    />
  );
}

const styles = StyleSheet.create({
  empty: {
    borderWidth: 1,
    borderStyle: "dashed",
    borderRadius: 12,
    padding: 24,
    alignItems: "center",
    justifyContent: "center",
  },
  iconSeat: {
    width: 48,
    height: 48,
    borderRadius: 24,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 12,
  },
  error: {
    flexDirection: "row",
    alignItems: "center",
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    gap: 8,
  },
});
