"use client";
import { create } from "zustand";
import { API_BASE } from "./api";

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
    /** 通用每用户偏好（ocr_parallel OCR 并行、tts_speed 朗读语速）。 */
    prefs?: { ocr_parallel?: boolean; tts_speed?: number };
  };
}

interface AuthState {
  token: string | null;
  user: AuthUser | null;
  authRequired: boolean; // AUTH_MODE=1 on the backend
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

const TOKEN_KEY = "edu-agent-token";
const AUTH_STATUS_CACHE_KEY = "edu-agent-auth-status";
const AUTH_STATUS_TTL_MS = 5 * 60 * 1000;

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
  authRequired: false,
  loaded: false,
  statusLoaded: false,
  loading: false,
  error: null,
  setAuth: (token, user) => {
    setToken(token);
    set({ token, user, error: null });
  },
  clearAuth: () => {
    clearToken();
    set({ token: null, user: null });
  },
  logout: () => {
    // Best-effort server logout (stateless JWT -- mainly client-side discard).
    authFetch(`${API_BASE}/auth/logout`, { method: "POST" }).catch(() => {});
    get().clearAuth();
  },
  fetchStatus: async () => {
    // authRequired（后端 AUTH_MODE）只有后端换配置重启才会变：结果缓存
    // sessionStorage（5 分钟 TTL），重复加载跳过这趟往返，白屏时间减半。
    try {
      const raw = sessionStorage.getItem(AUTH_STATUS_CACHE_KEY);
      if (raw) {
        const cached = JSON.parse(raw) as { required: boolean; at: number };
        if (Date.now() - cached.at < AUTH_STATUS_TTL_MS) {
          set({ authRequired: cached.required, statusLoaded: true });
          return;
        }
      }
    } catch { /* cache miss only */ }
    try {
      const res = await fetch(`${API_BASE}/auth/status`);
      const data = await res.json();
      const required = !!data.auth_required;
      set({ authRequired: required, statusLoaded: true });
      try {
        sessionStorage.setItem(AUTH_STATUS_CACHE_KEY, JSON.stringify({ required, at: Date.now() }));
      } catch { /* storage unavailable */ }
    } catch {
      set({ authRequired: false, statusLoaded: true });
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
        set({ token, user: data.user, loaded: true });
      } else {
        // token expired or invalid
        clearToken();
        set({ token: null, user: null, loaded: true });
      }
    } catch {
      set({ loaded: true });
    }
  },
}));

/** Hydrate the auth store on client mount: fetch backend auth mode + validate
 *  token. The two requests fly in parallel (they used to be a serial waterfall
 *  gating the first workspace render). Render gating additionally waits for
 *  `statusLoaded` so an unauthenticated user never sees a workspace flash. */
export async function hydrateAuth() {
  const { fetchStatus, fetchMe } = useAuthStore.getState();
  await Promise.all([fetchStatus(), fetchMe()]);
}

/** Convenience: is the user currently authenticated? */
export function isAuthenticated(): boolean {
  return !!getToken();
}
