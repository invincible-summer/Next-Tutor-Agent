"use client";
import { create } from "zustand";
import { clearAllDrafts } from "./chat-drafts";
import { useChatStore, useEvaluationCacheStore } from "./store";
import { endGuestSession } from "./guest-session";
import { clearQuizAnswerDrafts } from "./quiz-drafts";
import { createBrowserClient } from "@/platform/api-client";
import { clearToken, getToken, setToken } from "@/platform/token";
import { UnauthorizedError } from "@next-tutor/api-client";

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
      quiz_illustration_mode?: "v1" | "v2" | "v3";
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

// hydrate 路径自带清理联动，使用无 401 事件钩子的客户端实例，避免与
// clearAuth 重复处理。
const authClient = createBrowserClient({ withUnauthorizedHook: false });

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
    authClient.auth.logout().catch(() => {});
    get().clearAuth();
  },
  fetchStatus: async () => {
    try {
      const data = await authClient.auth.status();
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
      const data = await authClient.auth.me<AuthUser>();
      if (getToken() !== token) return;
      if (get().user && get().user?.id !== data.user.id) get().setAuth(token, data.user);
      set({ token, user: data.user, loaded: true });
    } catch (error) {
      if (getToken() !== token) return;
      // 仅明确的 401 清会话；网络失败保持现状（与旧契约一致）。
      if (error instanceof UnauthorizedError) get().clearAuth();
      set({ loaded: true });
    }
  },
}));

/** Hydrate the auth store on client mount: fetch backend auth mode + validate
 *  token. The two requests fly in parallel (they used to be a serial waterfall
 *  gating the first workspace render). Render gating additionally waits for
 *  `statusLoaded` so an unauthenticated user never sees a workspace flash.
 *  In-flight dedup：根助手 Provider 与 WorkspaceLayout 并发
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
