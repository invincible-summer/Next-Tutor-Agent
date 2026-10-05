import React from "react";
import { View } from "react-native";
import { Gesture, GestureDetector } from "react-native-gesture-handler";
import Animated, {
  useAnimatedStyle,
  useSharedValue,
} from "react-native-reanimated";
import { Button } from "./Button";
import { useCopy } from "@/lib/copy";
export function ZoomViewport({
  children,
  height = 420,
}: {
  children: React.ReactNode;
  height?: number;
}) {
  const c = useCopy();
  const scale = useSharedValue(1),
    base = useSharedValue(1),
    x = useSharedValue(0),
    y = useSharedValue(0),
    bx = useSharedValue(0),
    by = useSharedValue(0);
  const pinch = Gesture.Pinch()
    .onUpdate((e) => {
      scale.value = Math.max(0.4, Math.min(6, base.value * e.scale));
    })
    .onEnd(() => {
      base.value = scale.value;
    });
  const pan = Gesture.Pan()
    .onUpdate((e) => {
      x.value = bx.value + e.translationX;
      y.value = by.value + e.translationY;
    })
    .onEnd(() => {
      bx.value = x.value;
      by.value = y.value;
    });
  const style = useAnimatedStyle(() => ({
    transform: [
      { translateX: x.value },
      { translateY: y.value },
      { scale: scale.value },
    ],
  }));
  return (
    <View style={{ gap: 8 }}>
      <View style={{ height, overflow: "hidden", borderRadius: 16 }}>
        <GestureDetector gesture={Gesture.Simultaneous(pinch, pan)}>
          <Animated.View style={[{ flex: 1 }, style]}>{children}</Animated.View>
        </GestureDetector>
      </View>
      <View style={{ flexDirection: "row", justifyContent: "flex-end" }}>
        <Button
          title="−"
          accessibilityLabel={c("缩小", "Zoom out")}
          variant="ghost"
          onPress={() => {
            scale.value = base.value = Math.max(0.4, scale.value / 1.3);
          }}
        />
        <Button
          title="+"
          accessibilityLabel={c("放大", "Zoom in")}
          variant="ghost"
          onPress={() => {
            scale.value = base.value = Math.min(6, scale.value * 1.3);
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
    </View>
  );
}
