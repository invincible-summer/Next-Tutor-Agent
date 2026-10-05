import React, { useEffect, useRef } from "react";
import {
  Animated,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  View,
  type StyleProp,
  type ViewStyle,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useAdaptive } from "@/shell/adaptive/window-class";
import { useCopy } from "@/lib/copy";
import { useTheme } from "./ThemeProvider";
import { useReducedMotion } from "./useReducedMotion";
export function Sheet({
  open,
  onClose,
  children,
  heightRatio = 0.9,
  style,
  accessibilityLabel,
  label,
  centered = false,
  width = 580,
}: {
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
  heightRatio?: number;
  style?: StyleProp<ViewStyle>;
  accessibilityLabel?: string;
  label?: string;
  centered?: boolean;
  width?: number;
}) {
  const { theme } = useTheme();
  const c = useCopy();
  const insets = useSafeAreaInsets();
  const { isCompact, compactHeight } = useAdaptive();
  const reduced = useReducedMotion();
  const opacity = useRef(new Animated.Value(0)).current;
  const translate = useRef(new Animated.Value(16)).current;
  const floating = centered || !isCompact;
  useEffect(() => {
    if (!open) {
      opacity.setValue(0);
      translate.setValue(reduced ? 0 : 16);
      return;
    }
    const animation = Animated.parallel([
      Animated.timing(opacity, {
        toValue: 1,
        duration: reduced ? 0 : 180,
        useNativeDriver: true,
      }),
      Animated.timing(translate, {
        toValue: 0,
        duration: reduced ? 0 : 240,
        useNativeDriver: true,
      }),
    ]);
    animation.start();
    return () => animation.stop();
  }, [open, reduced, opacity, translate]);
  return (
    <Modal
      visible={open}
      transparent
      animationType="none"
      onRequestClose={onClose}
      statusBarTranslucent
      navigationBarTranslucent
    >
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={{
          flex: 1,
          justifyContent: floating ? "center" : "flex-end",
          alignItems: "center",
          paddingTop: insets.top + 12,
          paddingHorizontal: floating ? 24 : 0,
        }}
      >
        <Animated.View style={{ position: "absolute", inset: 0, opacity }}>
          <Pressable
            style={{ flex: 1, backgroundColor: "rgba(7,18,17,0.38)" }}
            onPress={onClose}
            accessibilityRole="button"
            accessibilityLabel={c("关闭", "Close")}
          />
        </Animated.View>
        <Animated.View
          accessibilityViewIsModal
          accessibilityLabel={accessibilityLabel ?? label}
          style={[
            {
              width: "100%",
              maxWidth: floating ? width : undefined,
              maxHeight: `${Math.round((compactHeight ? 0.98 : heightRatio) * 100)}%`,
              backgroundColor: theme.colors.surface,
              borderRadius: floating ? 24 : 0,
              borderTopLeftRadius: 24,
              borderTopRightRadius: 24,
              paddingBottom: floating ? 20 : insets.bottom + 16,
              overflow: "hidden",
              opacity,
              transform: [{ translateY: translate }],
            },
            style,
          ]}
        >
          {!floating ? (
            <View
              style={{
                alignSelf: "center",
                width: 36,
                height: 4,
                borderRadius: 2,
                marginTop: 10,
                marginBottom: 8,
                backgroundColor: theme.colors.border,
              }}
            />
          ) : null}
          {children}
        </Animated.View>
      </KeyboardAvoidingView>
    </Modal>
  );
}
export function Dialog(props: {
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
  width?: number;
}) {
  return <Sheet {...props} centered style={{ padding: 20 }} />;
}
