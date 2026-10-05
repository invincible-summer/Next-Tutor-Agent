import React from "react";
import {
  Pressable,
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type ViewStyle,
} from "react-native";

import { useTheme } from "./ThemeProvider";

interface CardProps {
  children: React.ReactNode;
  onPress?: () => void;
  /** 内边距倍数（4pt 网格）。 */
  pad?: number;
  style?: StyleProp<ViewStyle>;
  accessibilityLabel?: string;
}

/** 纸面卡片：surface 底 + 1px border + radius 10 + sm 阴影。 */
export function Card({
  children,
  onPress,
  pad = 4,
  style,
  accessibilityLabel,
}: CardProps) {
  const { theme } = useTheme();
  const cardStyle = [theme.card, { padding: theme.space(pad) }, style];
  if (!onPress) return <View style={cardStyle}>{children}</View>;
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel}
      style={({ pressed }) => [
        cardStyle,
        {
          backgroundColor: pressed
            ? theme.colors["surface-hover"]
            : theme.colors.surface,
        },
      ]}
    >
      {children}
    </Pressable>
  );
}

export function CardTitle({ children }: { children: React.ReactNode }) {
  const { theme } = useTheme();
  return (
    <Text style={[theme.type.titleSmall, { color: theme.colors.fg }]}>
      {children}
    </Text>
  );
}

export function CardSubtitle({ children }: { children: React.ReactNode }) {
  const { theme } = useTheme();
  return (
    <Text
      style={[
        theme.type.caption,
        { color: theme.colors["fg-tertiary"], marginTop: 2 },
      ]}
    >
      {children}
    </Text>
  );
}
