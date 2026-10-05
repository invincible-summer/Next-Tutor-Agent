import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import type { Tabs } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { useI18n } from "@/providers/I18nProvider";
import { BookOpen } from "lucide-react-native";
import { useTheme } from "@/ui/ThemeProvider";
import { alpha } from "@/ui/theme";
import { useAdaptive } from "./adaptive/window-class";
import { NAV_ITEMS } from "./nav-items";

/** expo-router 自定义 tabBar 的真实 props 类型（从 Tabs 组件签名提取）。 */
export type TabBarProps = Parameters<
  NonNullable<React.ComponentProps<typeof Tabs>["tabBar"]>
>[0];

function itemFor(name: string) {
  return NAV_ITEMS.find((i) => name === i.name || name.startsWith(i.name));
}

/**
 * 自适应导航：Compact/矮高 → 底部 5 Tab；Medium+ → 左侧 NavRail。
 * 同一套路由树，只切换导航呈现。
 */
export function AdaptiveTabBar(props: TabBarProps) {
  const adaptive = useAdaptive();
  const rail = !(adaptive.isCompact || adaptive.compactHeight);
  return rail ? <NavRail {...props} /> : <BottomBar {...props} />;
}

function useTabPress(
  state: TabBarProps["state"],
  navigation: TabBarProps["navigation"],
) {
  return (name: string, key: string) => {
    const event = navigation.emit({
      type: "tabPress",
      target: key,
      canPreventDefault: true,
    });
    const focused = state.routes[state.index]?.name === name;
    if (!focused && !event.defaultPrevented) navigation.navigate(name);
  };
}

function BottomBar({ state, navigation }: TabBarProps) {
  const { theme } = useTheme();
  const { t } = useI18n();
  const insets = useSafeAreaInsets();
  const onPress = useTabPress(state, navigation);

  return (
    <View
      style={[
        styles.bottomBar,
        {
          backgroundColor: theme.colors.surface,
          borderTopColor: theme.colors["border-light"],
          paddingBottom: Math.max(insets.bottom, 6),
        },
      ]}
      accessibilityRole="tablist"
    >
      {state.routes.map((route, index) => {
        const item = itemFor(route.name);
        if (!item) return null;
        const focused = index === state.index;
        const Icon = item.icon;
        const color = focused
          ? theme.colors["accent-strong"]
          : theme.colors["fg-tertiary"];
        return (
          <Pressable
            key={route.key}
            onPress={() => onPress(route.name, route.key)}
            accessibilityRole="tab"
            accessibilityState={{ selected: focused }}
            accessibilityLabel={t(item.i18nKey)}
            style={styles.bottomItem}
          >
            <View
              style={[
                styles.iconPill,
                focused && { backgroundColor: theme.colors["accent-soft"] },
              ]}
            >
              <Icon size={21} color={color} strokeWidth={focused ? 2.2 : 1.8} />
            </View>
            <Text
              style={[
                theme.type.caption,
                { color, fontSize: 12, fontWeight: focused ? "600" : "400" },
              ]}
            >
              {t(item.i18nKey)}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

function NavRail({ state, navigation }: TabBarProps) {
  const { theme } = useTheme();
  const { t } = useI18n();
  const insets = useSafeAreaInsets();
  const adaptive = useAdaptive();
  const withLabels = adaptive.isLargeUp;
  const onPress = useTabPress(state, navigation);

  return (
    <View
      style={[
        styles.rail,
        {
          backgroundColor: theme.colors.surface,
          borderRightColor: theme.colors["border-light"],
          paddingTop: insets.top + 12,
          paddingBottom: insets.bottom + 12,
          width: withLabels ? 176 : 80,
        },
      ]}
      accessibilityRole="tablist"
    >
      <View style={[styles.brand, !withLabels && styles.brandCompact]}>
        <View
          style={[styles.brandMark, { backgroundColor: theme.colors.accent }]}
        >
          <BookOpen size={21} color={theme.colors.onAccent} />
        </View>
        {withLabels ? (
          <View style={{ marginLeft: 10, minWidth: 0 }}>
            <Text
              style={[theme.type.titleSmall, { color: theme.colors.fg }]}
              numberOfLines={1}
            >
              Next Tutor
            </Text>
            <Text
              style={[
                theme.type.caption,
                { color: theme.colors["fg-tertiary"] },
              ]}
              numberOfLines={1}
            >
              {t("nav.home")}
            </Text>
          </View>
        ) : null}
      </View>

      <View style={styles.railItems}>
        {state.routes.map((route, index) => {
          const item = itemFor(route.name);
          if (!item) return null;
          const focused = index === state.index;
          const Icon = item.icon;
          const color = focused
            ? theme.colors["accent-strong"]
            : theme.colors["fg-tertiary"];
          return (
            <Pressable
              key={route.key}
              onPress={() => onPress(route.name, route.key)}
              accessibilityRole="tab"
              accessibilityState={{ selected: focused }}
              accessibilityLabel={t(item.i18nKey)}
              style={({ pressed }) => [
                styles.railItem,
                !withLabels && styles.railItemCompact,
                {
                  backgroundColor: focused
                    ? theme.colors["accent-soft"]
                    : pressed
                      ? theme.colors["surface-hover"]
                      : "transparent",
                },
              ]}
            >
              <Icon size={21} color={color} strokeWidth={focused ? 2.2 : 1.8} />
              {withLabels ? (
                <Text
                  style={[
                    theme.type.label,
                    {
                      color,
                      marginLeft: 12,
                      fontWeight: focused ? "600" : "500",
                    },
                  ]}
                  numberOfLines={1}
                >
                  {t(item.i18nKey)}
                </Text>
              ) : null}
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  bottomBar: {
    flexDirection: "row",
    borderTopWidth: StyleSheet.hairlineWidth,
    paddingTop: 6,
  },
  bottomItem: { flex: 1, alignItems: "center", minHeight: 48 },
  iconPill: {
    borderRadius: 999,
    paddingHorizontal: 14,
    paddingVertical: 3,
    marginBottom: 1,
  },
  rail: {
    borderRightWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 10,
  },
  brand: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 6,
    marginBottom: 18,
  },
  brandCompact: { justifyContent: "center", paddingHorizontal: 0 },
  brandMark: {
    width: 34,
    height: 34,
    borderRadius: 9,
    alignItems: "center",
    justifyContent: "center",
  },
  railItems: { gap: 2 },
  railItem: {
    flexDirection: "row",
    alignItems: "center",
    borderRadius: 10,
    paddingHorizontal: 12,
    minHeight: 52,
  },
  railItemCompact: { justifyContent: "center", paddingHorizontal: 0 },
});

export { alpha };
