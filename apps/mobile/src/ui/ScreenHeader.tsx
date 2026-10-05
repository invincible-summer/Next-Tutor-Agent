import React from "react";
import {
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type ViewStyle,
} from "react-native";

import { useTheme } from "./ThemeProvider";
import { Sparkles } from "lucide-react-native";
import { IconButton } from "./Button";
import { useAssistantUi } from "@/stores/assistant-ui";
import { useAuth } from "@/providers/AuthProvider";
import { useCopy } from "@/lib/copy";

interface ScreenHeaderProps {
  title: string;
  subtitle?: string | undefined;
  left?: React.ReactNode;
  right?: React.ReactNode;
  style?: StyleProp<ViewStyle>;
  assistant?: boolean;
}

/** Native page heading with wrapping titles and optional actions. */
export function ScreenHeader({
  title,
  subtitle,
  left,
  right,
  style,
  assistant = false,
}: ScreenHeaderProps) {
  const { theme } = useTheme();
  const { state } = useAuth();
  const c = useCopy();
  const setOpen = useAssistantUi((s) => s.setOpen);
  return (
    <View
      style={[
        styles.row,
        { borderBottomColor: theme.colors["border-light"] },
        style,
      ]}
    >
      {left ? <View style={styles.side}>{left}</View> : null}
      <View style={styles.center}>
        <View style={styles.titleRow}>
          <Text
            accessibilityRole="header"
            style={[
              theme.type.title,
              { color: theme.colors.fg, flexShrink: 1 },
            ]}
          >
            {title}
          </Text>
        </View>
        {subtitle ? (
          <Text
            style={[
              theme.type.caption,
              { color: theme.colors["fg-tertiary"], marginTop: 2 },
            ]}
          >
            {subtitle}
          </Text>
        ) : null}
      </View>
      {right ? <View style={styles.side}>{right}</View> : null}
      {assistant &&
      (state.status === "signed-in" || state.status === "guest") ? (
        <IconButton
          icon={<Sparkles size={20} color={theme.colors.accent} />}
          onPress={() => setOpen(true)}
          accessibilityLabel={c("打开学习助手", "Open learning assistant")}
        />
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 20,
    paddingVertical: 16,
  },
  center: { flex: 1, minWidth: 0 },
  titleRow: { flexDirection: "row", alignItems: "center" },
  side: { marginHorizontal: 4 },
});
