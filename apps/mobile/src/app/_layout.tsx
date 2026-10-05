import React, { useEffect } from "react";
import { ActivityIndicator, StatusBar, View } from "react-native";
import {
  Stack,
  useGlobalSearchParams,
  usePathname,
  useRouter,
  useSegments,
} from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { AppProviders } from "@/providers/AppProviders";
import { useAuth } from "@/providers/AuthProvider";
import { useTheme } from "@/ui/ThemeProvider";
import { ErrorState } from "@/ui/EmptyState";
import { Body } from "@/ui/Elements";
import { useCopy } from "@/lib/copy";
import { errorMessage } from "@/lib/feedback";
import { consumeDestination, rememberDestination } from "@/shell/routes";
void SplashScreen.preventAutoHideAsync().catch(() => {});
function AuthGate() {
  const { state, retry, owner } = useAuth();
  const segments: string[] = useSegments();
  const pathname = usePathname();
  const params = useGlobalSearchParams();
  const router = useRouter();
  const c = useCopy();
  const { theme } = useTheme();
  useEffect(() => {
    if (state.status === "loading" || state.status === "error") return;
    const inAuth = segments[0] === "(auth)";
    if (state.status === "signed-out" && !inAuth) {
      const query = new URLSearchParams();
      for (const key of ["ws", "runId", "conceptId", "q"]) {
        const value = params[key];
        if (typeof value === "string") query.set(key, value);
      }
      rememberDestination(
        pathname + (query.size ? "?" + query.toString() : ""),
      );
      router.replace("/(auth)/welcome");
    } else if (state.status === "signed-in" && inAuth)
      router.replace(consumeDestination());
    else if (state.status === "guest" && inAuth && segments[1] === "welcome")
      router.replace("/");
  }, [state.status, segments, pathname, params, router]);
  useEffect(() => {
    if (state.status !== "loading")
      void SplashScreen.hideAsync().catch(() => {});
  }, [state.status]);
  if (state.status === "loading")
    return (
      <View style={{ flex: 1, justifyContent: "center", alignItems: "center" }}>
        <ActivityIndicator color={theme.colors.accent} />
      </View>
    );
  if (state.status === "error")
    return (
      <Body style={{ marginTop: 80 }}>
        <ErrorState
          title={errorMessage(state.error, c)}
          retryLabel={c("重新连接", "Reconnect")}
          onRetry={() => void retry()}
        />
      </Body>
    );
  return (
    <Stack
      key={owner}
      screenOptions={{ headerShown: false, animation: "fade" }}
    >
      <Stack.Screen name="(auth)" />
      <Stack.Screen name="(main)" />
    </Stack>
  );
}
function ThemedRoot() {
  const { theme } = useTheme();
  return (
    <View style={{ flex: 1, backgroundColor: theme.colors.bg }}>
      <StatusBar barStyle={theme.isDark ? "light-content" : "dark-content"} />
      <AuthGate />
    </View>
  );
}
export default function RootLayout() {
  return (
    <AppProviders>
      <ThemedRoot />
    </AppProviders>
  );
}
