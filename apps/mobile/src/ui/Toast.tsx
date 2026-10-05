import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Animated, StyleSheet, Text } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { useTheme } from "./ThemeProvider";
import { useReducedMotion } from "./useReducedMotion";
import { registerSessionCleanup } from "@/lib/session-lifecycle";
import { alpha } from "./theme";

type ToastKind = "success" | "error" | "info";

interface ToastItem {
  id: number;
  message: string;
  kind: ToastKind;
}

interface ToastContextValue {
  toast: (message: string, kind?: ToastKind) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const { theme } = useTheme();
  const reduced = useReducedMotion();
  const insets = useSafeAreaInsets();
  const [item, setItem] = useState<ToastItem | null>(null);
  const opacity = useRef(new Animated.Value(0)).current;
  const counter = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const toast = useCallback((message: string, kind: ToastKind = "success") => {
    counter.current += 1;
    setItem({ id: counter.current, message, kind });
  }, []);

  useEffect(
    () =>
      registerSessionCleanup(() => {
        setItem(null);
      }),
    [],
  );
  useEffect(() => {
    if (!item) return;
    if (timer.current) clearTimeout(timer.current);
    Animated.timing(opacity, {
      toValue: 1,
      duration: reduced ? 0 : 160,
      useNativeDriver: true,
    }).start();
    timer.current = setTimeout(
      () => {
        Animated.timing(opacity, {
          toValue: 0,
          duration: reduced ? 0 : 160,
          useNativeDriver: true,
        }).start(({ finished }) => {
          if (finished) setItem((cur) => (cur?.id === item.id ? null : cur));
        });
      },
      item.kind === "error" ? 6000 : 3000,
    );
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [item, opacity, reduced]);

  const value = useMemo(() => ({ toast }), [toast]);

  const bg =
    item?.kind === "error"
      ? theme.colors.danger
      : item?.kind === "info"
        ? theme.colors.info
        : theme.colors.accent;

  return (
    <ToastContext.Provider value={value}>
      {children}
      {item ? (
        <Animated.View
          pointerEvents="none"
          style={[
            styles.toast,
            {
              top: insets.top + 8,
              backgroundColor: bg,
              shadowColor: "#000",
              shadowOpacity: 0.2,
              shadowRadius: 12,
              shadowOffset: { width: 0, height: 4 },
              opacity,
              transform: [
                {
                  translateY: opacity.interpolate({
                    inputRange: [0, 1],
                    outputRange: [-8, 0],
                  }),
                },
              ],
            },
          ]}
          accessibilityLiveRegion="polite"
        >
          <Text
            style={[
              theme.type.label,
              {
                color: item.kind === "success" ? theme.colors.onAccent : "#fff",
              },
            ]}
          >
            {item.message}
          </Text>
        </Animated.View>
      ) : null}
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue["toast"] {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within ToastProvider");
  return ctx.toast;
}

const styles = StyleSheet.create({
  toast: {
    position: "absolute",
    alignSelf: "center",
    borderRadius: 12,
    paddingHorizontal: 16,
    paddingVertical: 10,
    maxWidth: "86%",
    elevation: 6,
  },
});
