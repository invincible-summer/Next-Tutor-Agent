import React, { useState } from "react";
import { View } from "react-native";
import { SvgXml } from "react-native-svg";
import { Gesture, GestureDetector } from "react-native-gesture-handler";
import Animated, {
  useAnimatedStyle,
  useSharedValue,
} from "react-native-reanimated";
import { Button } from "./Button";
import { Hint } from "./Elements";
import { useCopy } from "@/lib/copy";
export function isSafeSvg(svg: string): boolean {
  return (
    svg.length <= 2_000_000 &&
    (svg.match(/</g)?.length ?? 0) <= 12000 &&
    !/<(?:script|foreignObject|iframe|image)\b|\bon\w+\s*=|(?:href|src)\s*=\s*["']\s*(?!#)|@import|url\(\s*["']?\s*(?!#)/i.test(
      svg,
    ) &&
    /<svg\b/i.test(svg)
  );
}
export function SvgCanvas({
  svg,
  alt,
  height = 280,
}: {
  svg: string;
  alt?: string | undefined;
  height?: number;
}) {
  const c = useCopy();
  const [broken, setBroken] = useState(false);
  const scale = useSharedValue(1);
  const base = useSharedValue(1);
  const x = useSharedValue(0);
  const y = useSharedValue(0);
  const bx = useSharedValue(0);
  const by = useSharedValue(0);
  const pinch = Gesture.Pinch()
    .onUpdate((e) => {
      scale.value = Math.max(0.5, Math.min(5, base.value * e.scale));
    })
    .onEnd(() => {
      base.value = scale.value;
    });
  const pan = Gesture.Pan()
    .minDistance(8)
    .onUpdate((e) => {
      x.value = bx.value + e.translationX;
      y.value = by.value + e.translationY;
    })
    .onEnd(() => {
      bx.value = x.value;
      by.value = y.value;
    });
  const animated = useAnimatedStyle(() => ({
    transform: [
      { translateX: x.value },
      { translateY: y.value },
      { scale: scale.value },
    ],
  }));
  if (!isSafeSvg(svg) || broken)
    return (
      <Hint>
        {c("此图暂时无法安全显示。", "This image cannot be displayed safely.")}
      </Hint>
    );
  return (
    <View style={{ gap: 8 }}>
      <View
        style={{
          height,
          backgroundColor: "#fff",
          borderRadius: 16,
          overflow: "hidden",
        }}
        accessible
        accessibilityRole="image"
        accessibilityLabel={alt || c("可缩放图示", "Zoomable illustration")}
      >
        <GestureDetector gesture={Gesture.Simultaneous(pinch, pan)}>
          <Animated.View style={[{ width: "100%", height: "100%" }, animated]}>
            <SvgXml
              xml={svg}
              width="100%"
              height="100%"
              onError={() => setBroken(true)}
            />
          </Animated.View>
        </GestureDetector>
      </View>
      <View
        style={{ flexDirection: "row", justifyContent: "flex-end", gap: 4 }}
      >
        <Button
          title="−"
          accessibilityLabel={c("缩小", "Zoom out")}
          variant="ghost"
          onPress={() => {
            scale.value = Math.max(0.5, scale.value / 1.3);
            base.value = scale.value;
          }}
        />
        <Button
          title="+"
          accessibilityLabel={c("放大", "Zoom in")}
          variant="ghost"
          onPress={() => {
            scale.value = Math.min(5, scale.value * 1.3);
            base.value = scale.value;
          }}
        />
        <Button
          title={c("复位", "Reset")}
          variant="ghost"
          onPress={() => {
            scale.value = base.value = 1;
            x.value = y.value = bx.value = by.value = 0;
          }}
        />
      </View>
      {alt ? <Hint>{alt}</Hint> : null}
    </View>
  );
}
