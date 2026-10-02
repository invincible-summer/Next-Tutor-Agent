"use client";

import { useEffect, useLayoutEffect, type ReactNode } from "react";
import { installUnsavedChangesGuard } from "@/lib/use-unsaved-changes";
import { loadLang } from "@/lib/i18n";
import { useUIStore, useChatStore, useEvaluationCacheStore } from "@/lib/store";
import { hydrateAuth, useAuthStore } from "@/lib/auth-store";
import { endGuestSession } from "@/lib/guest-session";
import { clearQuizAnswerDrafts } from "@/lib/quiz-drafts";
import { clearAllDrafts } from "@/lib/chat-drafts";
import { gradeFromApi } from "@/lib/types";
import { ToastProvider } from "@/components/ui/Toast";
import { DEMO_MODE, DEMO_TOKEN_KEY } from "@/lib/demo";

/** Restore site preferences once, including routes outside the workspace. */
export function UIProvider({ children }: { children: ReactNode }) {
  useLayoutEffect(() => installUnsavedChangesGuard(), []);
  const lang = useUIStore((s) => s.lang);
  const guestGrade = useUIStore((s) => s.guestGrade);
  const userGrade = useAuthStore((s) => s.user?.profile.grade);
  const loaded = useAuthStore((s) => s.loaded);
  const preferenceGrade = userGrade === undefined ? guestGrade : gradeFromApi(userGrade);

  useEffect(() => {
    void hydrateAuth();
    const refreshAccess = () => { void useAuthStore.getState().fetchStatus(); };
    const expireAuth = () => { useAuthStore.getState().clearAuth(); refreshAccess(); };
    const pageExit = () => { endGuestSession(); if (!useAuthStore.getState().user) clearAllDrafts(); };
    const visible = () => { if (document.visibilityState === "visible") refreshAccess(); };
    window.addEventListener("edu-access-changed", refreshAccess);
    window.addEventListener("edu-auth-expired", expireAuth);
    window.addEventListener("pagehide", pageExit);
    document.addEventListener("visibilitychange", visible);
    const timer = window.setInterval(() => {
      if (!useAuthStore.getState().user && document.visibilityState === "visible") refreshAccess();
    }, 30000);
    return () => {
      window.removeEventListener("edu-access-changed", refreshAccess);
      window.removeEventListener("edu-auth-expired", expireAuth);
      window.removeEventListener("pagehide", pageExit);
      document.removeEventListener("visibilitychange", visible);
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    useUIStore.getState().hydrateClient();
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onSystemTheme = () => useUIStore.getState().syncTheme();
    media.addEventListener("change", onSystemTheme);
    onSystemTheme();
    const onStorage = (event: StorageEvent) => {
      if (event.key === (DEMO_MODE ? DEMO_TOKEN_KEY : "edu-agent-token")) {
        endGuestSession(); clearQuizAnswerDrafts(); clearAllDrafts();
        useChatStore.getState().newChat(); useEvaluationCacheStore.getState().clearAll();
        useAuthStore.setState({ user: null, token: null, loaded: false });
        void useAuthStore.getState().fetchMe();
      }
      if (event.key === "edu-agent-lang" || event.key === null) {
        useUIStore.setState({ lang: loadLang() });
      }
      if (event.key === "edu-agent-theme" || event.key === null) {
        const preference = localStorage.getItem("edu-agent-theme");
        useUIStore.setState({ themePreference: preference === "dark" || preference === "light" ? preference : "system" });
        onSystemTheme();
      }
      if (event.key === "edu-agent-grade") {
        const value = gradeFromApi(event.newValue || "本科");
        useUIStore.setState({ guestGrade: value });
        if (!useAuthStore.getState().user) useUIStore.getState().setDefaultGrade(value, false);
      }
    };
    window.addEventListener("storage", onStorage);
    return () => { window.removeEventListener("storage", onStorage); media.removeEventListener("change", onSystemTheme); };
  }, []);

  useEffect(() => {
    if (loaded) useUIStore.getState().setDefaultGrade(preferenceGrade, false);
  }, [loaded, preferenceGrade]);

  useEffect(() => {
    document.documentElement.lang = lang === "en" ? "en" : "zh-CN";
  }, [lang]);

  return <ToastProvider>{children}</ToastProvider>;
}
