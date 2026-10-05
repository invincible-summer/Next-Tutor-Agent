import React from "react";
import { RefreshControl, ScrollView, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useTheme } from "./ThemeProvider";
export function PageShell({
  children,
  scroll = true,
  refreshing = false,
  onRefresh,
}: {
  children: React.ReactNode;
  scroll?: boolean;
  refreshing?: boolean;
  onRefresh?: () => void;
}) {
  const { theme } = useTheme();
  const insets = useSafeAreaInsets();
  const style = {
    flex: 1,
    backgroundColor: theme.colors.bg,
    paddingTop: insets.top,
    paddingLeft: insets.left,
    paddingRight: insets.right,
  };
  if (!scroll) return <View style={style}>{children}</View>;
  return (
    <ScrollView
      style={style}
      contentContainerStyle={{ flexGrow: 1, paddingBottom: 24 }}
      keyboardShouldPersistTaps="handled"
      keyboardDismissMode="on-drag"
      refreshControl={
        onRefresh ? (
          <RefreshControl
            refreshing={refreshing}
            onRefresh={onRefresh}
            tintColor={theme.colors.accent}
          />
        ) : undefined
      }
    >
      {children}
    </ScrollView>
  );
}
