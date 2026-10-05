import React from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { useTheme } from "./ThemeProvider";
import { alpha } from "./theme";

export interface TabItem {
  key: string;
  label: string;
  badge?: string | number;
}

/** 下划线式 Tabs（对齐 Web Tabs：accent 激活 + 下划线过渡）。 */
export function Tabs({
  items,
  active,
  onChange,
}: {
  items: TabItem[];
  active: string;
  onChange: (key: string) => void;
}) {
  const { theme } = useTheme();
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      style={{ flexGrow: 0 }}
      contentContainerStyle={[
        styles.tabs,
        { borderBottomColor: theme.colors["border-light"] },
      ]}
    >
      {items.map((item) => {
        const isActive = item.key === active;
        return (
          <Pressable
            key={item.key}
            onPress={() => onChange(item.key)}
            accessibilityRole="tab"
            accessibilityState={{ selected: isActive }}
            style={styles.tab}
          >
            <Text
              style={[
                theme.type.label,
                {
                  color: isActive
                    ? theme.colors.accent
                    : theme.colors["fg-secondary"],
                  fontWeight: isActive ? "700" : "500",
                },
              ]}
            >
              {item.label}
            </Text>
            {item.badge !== undefined ? (
              <View
                style={[
                  styles.tabBadge,
                  { backgroundColor: theme.colors["accent-soft"] },
                ]}
              >
                <Text
                  style={[
                    theme.type.caption,
                    { color: theme.colors["accent-strong"], fontSize: 10 },
                  ]}
                >
                  {item.badge}
                </Text>
              </View>
            ) : null}
            <View
              style={[
                styles.underline,
                {
                  backgroundColor: isActive
                    ? theme.colors.accent
                    : "transparent",
                },
              ]}
            />
          </Pressable>
        );
      })}
    </ScrollView>
  );
}

/** 分段控件（iOS 风格，用于二元/三元切换）。 */
export function SegmentedControl({
  items,
  active,
  onChange,
}: {
  items: TabItem[];
  active: string;
  onChange: (key: string) => void;
}) {
  const { theme } = useTheme();
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      style={{ flexGrow: 0 }}
      contentContainerStyle={[
        styles.segmented,
        {
          backgroundColor: theme.colors["surface-sunken"],
          borderColor: theme.colors["border-light"],
        },
      ]}
    >
      {items.map((item) => {
        const isActive = item.key === active;
        return (
          <Pressable
            key={item.key}
            onPress={() => onChange(item.key)}
            accessibilityRole="button"
            accessibilityState={{ selected: isActive }}
            style={[
              styles.segment,
              isActive && {
                backgroundColor: theme.colors.surface,
                shadowColor: "#000",
                shadowOpacity: 0.08,
                shadowRadius: 3,
                shadowOffset: { width: 0, height: 1 },
                elevation: 1,
              },
            ]}
          >
            <Text
              style={[
                theme.type.label,
                {
                  color: isActive
                    ? theme.colors.fg
                    : theme.colors["fg-tertiary"],
                  fontWeight: isActive ? "600" : "500",
                  fontSize: 12.5,
                },
              ]}
            >
              {item.label}
            </Text>
          </Pressable>
        );
      })}
    </ScrollView>
  );
}

/** 进度条：全圆角细条，surface-hover 轨道（对齐 Web Progress）。 */
export function Progress({
  value,
  tone = "accent",
  height = 5,
}: {
  value: number;
  tone?: "accent" | "accent2" | "success" | "warning" | "danger";
  height?: number;
}) {
  const { theme } = useTheme();
  const clamped = Math.max(0, Math.min(1, value));
  return (
    <View
      style={[
        styles.track,
        {
          height,
          borderRadius: height / 2,
          backgroundColor: theme.colors["surface-hover"],
        },
      ]}
      accessibilityRole="progressbar"
      accessibilityValue={{ min: 0, max: 100, now: Math.round(clamped * 100) }}
    >
      <View
        style={{
          width: `${clamped * 100}%`,
          height,
          borderRadius: height / 2,
          backgroundColor: theme.colors[tone],
        }}
      />
    </View>
  );
}

export function Divider({ inset = 0 }: { inset?: number }) {
  const { theme } = useTheme();
  return (
    <View
      style={{
        height: StyleSheet.hairlineWidth,
        backgroundColor: theme.colors["border-light"],
        marginLeft: inset,
      }}
    />
  );
}

const styles = StyleSheet.create({
  tabs: { flexDirection: "row", borderBottomWidth: StyleSheet.hairlineWidth },
  tab: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 14,
    paddingVertical: 10,
    minHeight: 48,
    gap: 6,
  },
  tabBadge: { borderRadius: 999, paddingHorizontal: 6, paddingVertical: 1 },
  underline: {
    position: "absolute",
    left: 12,
    right: 12,
    bottom: 0,
    height: 2,
    borderRadius: 1,
  },
  segmented: {
    flexDirection: "row",
    borderRadius: 10,
    borderWidth: 1,
    padding: 2,
    alignSelf: "flex-start",
  },
  segment: {
    paddingHorizontal: 14,
    paddingVertical: 6,
    borderRadius: 8,
    minHeight: 48,
    justifyContent: "center",
  },
  track: { overflow: "hidden", alignSelf: "stretch" },
});

export { alpha };
