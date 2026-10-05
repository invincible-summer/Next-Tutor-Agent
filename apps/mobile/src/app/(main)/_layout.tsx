import React from "react";
import { Tabs } from "expo-router";

import { AdaptiveTabBar } from "@/shell/AdaptiveTabBar";
import { useAdaptive } from "@/shell/adaptive/window-class";

/**
 * (main) 一级壳：一套路由树，Compact → 底部 Tab，Medium+ → 左侧 NavRail
 * 。
 */
export default function MainLayout() {
  const adaptive = useAdaptive();
  const rail = !(adaptive.isCompact || adaptive.compactHeight);

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarPosition: rail ? "left" : "bottom",
      }}
      tabBar={(props) => <AdaptiveTabBar {...props} />}
    >
      <Tabs.Screen name="index" />
      <Tabs.Screen name="tutor" />
      <Tabs.Screen name="learn" />
      <Tabs.Screen name="library" />
      <Tabs.Screen name="me" />
    </Tabs>
  );
}
