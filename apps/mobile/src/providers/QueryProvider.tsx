import React, { useEffect, useState } from "react";
import { AppState } from "react-native";
import {
  focusManager,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { registerSessionCleanup } from "@/lib/session-lifecycle";
export function QueryProvider({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { staleTime: 30_000, gcTime: 300_000, retry: 1 },
          mutations: { retry: 0 },
        },
      }),
  );
  useEffect(
    () =>
      registerSessionCleanup(async () => {
        await client.cancelQueries();
        client.clear();
      }),
    [client],
  );
  useEffect(() => {
    focusManager.setFocused(AppState.currentState === "active");
    const subscription = AppState.addEventListener("change", (next) =>
      focusManager.setFocused(next === "active"),
    );
    return () => subscription.remove();
  }, []);
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
