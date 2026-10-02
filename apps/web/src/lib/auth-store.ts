"use client";
import { create } from "zustand";
import { API_BASE } from "./api";
import { clearAllDrafts } from "./chat-drafts";
import { useChatStore, useEvaluationCacheStore } from "./store";
import { endGuestSession } from "./guest-session";
import { clearQuizAnswerDrafts } from "./quiz-drafts";
import { apiFetch } from "./api-fetch";
import { DEMO_MODE, DEMO_TOKEN_KEY } from "./demo";

// --- types ------------------------------------------------------------------

export interface AuthUser {
  id: string;
  email: string;
  username: string;
  role: string;
  created_at: number;
  last_login_at: number;
  profile: {
    name: string;
    grade: string;
    school: string;
    subjects: string[];
    avatar: string;
    /** 通用每用户偏好（ocr_parallel OCR 并行、tts_speed 朗读语速、classroom 课堂默认）。 */
    prefs?: { ocr_parallel?: boolean; tts_speed?: number; quiz_svg_enabled?: boolean;
      classroom?: import("./types-modules").ClassroomPrefs };
  };
}

interface AuthState {
  token: string | null;
  user: AuthUser | null;
  authRequired: boolean;
  guestAllowed: boolean;
  loaded: boolean; // hydrate complete?
  statusLoaded: boolean; // authRequired 已确定？（并行水合下防未登录闪屏）
  loading: boolean; // request in flight?
  error: string | null;
  setAuth: (token: string, user: AuthUser) => void;
  clearAuth: () => void;
  logout: () => void;
  fetchStatus: () => Promise<void>;
  fetchMe: () => Promise<void>;
}

const TOKEN_KEY = DEMO_MODE ? DEMO_TOKEN_KEY : "edu-agent-token";

// --- helpers ----------------------------------------------------------------

function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

function setToken(token: string) {
  if (typeof window !== "undefined") localStorage.setItem(TOKEN_KEY, token);
}

function clearToken() {
  if (typeof window !== "undefined") localStorage.removeItem(TOKEN_KEY);
}

/** Build the Authorization header object from the stored token. */
export function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** fetch wrapper that injects the Authorization header. */
export async function authFetch(input: string, init?: RequestInit): Promise<Response> {
  if (DEMO_MODE) return apiFetch(input, init);
  const headers: Record<string, string> = {
    ...(init?.headers as Record<string, string>),
  };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return fetch(input, { ...init, headers });
}

// --- store ------------------------------------------------------------------

export const useAuthStore = create<AuthState>((set, get) => ({
  token: null,
  user: null,
  authRequired: true,
  guestAllowed: false,
  loaded: false,
  statusLoaded: false,
  loading: false,
  error: null,
  setAuth: (token, user) => {
    clearQuizAnswerDrafts();
    endGuestSession();
    clearAllDrafts();
    useChatStore.getState().newChat();
    // 换账号登录：清空上一账号的评价查询缓存并 abort 在途请求（§15.3）。
    useEvaluationCacheStore.getState().clearAll();
    setToken(token);
    set({ token, user, error: null });
  },
  clearAuth: () => {
    clearQuizAnswerDrafts();
    endGuestSession();
    useChatStore.getState().newChat();
    useEvaluationCacheStore.getState().clearAll();
    // 登出清除聊天草稿仓（§3.2：含 sessionStorage 正文，不留给下一账号）。
    clearAllDrafts();
    clearToken();
    set({ token: null, user: null });
  },
  logout: () => {
    // Best-effort server logout (stateless JWT -- mainly client-side discard).
    authFetch(`${API_BASE}/auth/logout`, { method: "POST" }).catch(() => {});
    get().clearAuth();
  },
  fetchStatus: async () => {
    try {
      const res = await apiFetch(`${API_BASE}/auth/status`, { cache: "no-store" });
      if (!res.ok) throw new Error("status_unavailable");
      const data = await res.json();
      const allowed = data.guest_allowed === true;
      set({ authRequired: !allowed, guestAllowed: allowed, statusLoaded: true });
      if (!allowed) endGuestSession();
    } catch {
      set({ authRequired: true, guestAllowed: false, statusLoaded: true });
      endGuestSession();
    }
  },
  fetchMe: async () => {
    const token = getToken();
    if (!token) {
      set({ loaded: true });
      return;
    }
    try {
      const res = await authFetch(`${API_BASE}/auth/me`);
      if (res.ok) {
        const data = await res.json();
        if (getToken() !== token) return;
        if (get().user && get().user?.id !== data.user.id) get().setAuth(token, data.user);
        set({ token, user: data.user, loaded: true });
      } else {
        if (getToken() !== token) return;
        // token expired or invalid
        get().clearAuth();
        set({ loaded: true });
      }
    } catch {
      set({ loaded: true });
    }
  },
}));

/** Hydrate the auth store on client mount: fetch backend auth mode + validate
 *  token. The two requests fly in parallel (they used to be a serial waterfall
 *  gating the first workspace render). Render gating additionally waits for
 *  `statusLoaded` so an unauthenticated user never sees a workspace flash.
 *  In-flight dedup（plan.md §5.1）：根助手 Provider 与 WorkspaceLayout 并发
 *  mount 时共享同一次水合，不重复请求。 */
let _hydrateInFlight: Promise<void> | null = null;

export function hydrateAuth(): Promise<void> {
  if (!_hydrateInFlight) {
    const { fetchStatus, fetchMe } = useAuthStore.getState();
    _hydrateInFlight = Promise.all([fetchStatus(), fetchMe()])
      .then(() => undefined)
      .finally(() => {
        _hydrateInFlight = null;
      });
  }
  return _hydrateInFlight;
}

/** Convenience: is the user currently authenticated? */
export function isAuthenticated(): boolean {
  return !!getToken();
}
