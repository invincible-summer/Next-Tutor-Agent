import React from "react";
import { View } from "react-native";
import { useAdaptive } from "./window-class";
import { useTheme } from "@/ui/ThemeProvider";
export function AdaptivePane({
  master,
  children,
  inspector,
  masterWidth = 260,
  inspectorWidth = 300,
}: {
  master?: React.ReactNode;
  children: React.ReactNode;
  inspector?: React.ReactNode;
  masterWidth?: number;
  inspectorWidth?: number;
}) {
  const { maxPanes } = useAdaptive();
  const { theme } = useTheme();
  return (
    <View style={{ flex: 1, flexDirection: "row" }}>
      {master && maxPanes >= 2 ? (
        <View
          style={{
            width: masterWidth,
            borderRightWidth: 1,
            borderRightColor: theme.colors["border-light"],
          }}
        >
          {master}
        </View>
      ) : null}
      <View style={{ flex: 1, minWidth: 0 }}>{children}</View>
      {inspector && maxPanes >= (master ? 3 : 2) ? (
        <View
          style={{
            width: inspectorWidth,
            borderLeftWidth: 1,
            borderLeftColor: theme.colors["border-light"],
          }}
        >
          {inspector}
        </View>
      ) : null}
    </View>
  );
}
