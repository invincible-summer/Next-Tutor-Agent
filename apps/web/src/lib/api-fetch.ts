import { endGuestSession, getGuestToken } from "./guest-session";
import { API_BASE } from "./api";
import { DEMO_MODE, DEMO_TOKEN_KEY } from "./demo";
import { demoFetch } from "./demo-fetch";

const TOKEN_KEY = DEMO_MODE ? DEMO_TOKEN_KEY : "edu-agent-token";
const pendingRequests = new Map<string, Promise<Response>>();

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const headers = { ...(extra || {}) };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

/**
 * Only attach credentials to requests we know point at this app (relative
 * paths, or absolute URLs on the page/backend origin). Callers must never
 * feed response-supplied absolute URLs into an authenticated fetch blind:
 * a poisoned `content_url`/`thumbnail_url` would otherwise ship the user's
 * Bearer token to an attacker-controlled host.
 */
export function trustedRequestUrl(input: string): boolean {
  if (typeof window === "undefined") return false;
  const trimmed = input.trim();
  // Protocol-relative "//host/…" carries a foreign host — treat it like an
  // absolute URL (origin check below), never as a same-app relative path.
  if (!trimmed.startsWith("//") && !/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(trimmed)) {
    return true;
  }
  let origin = "";
  try {
    origin = new URL(trimmed, window.location.href).origin;
  } catch {
    return false;
  }
  if (origin === window.location.origin) return true;
  // Explicitly configured external backend (NEXT_PUBLIC_BACKEND_URL) is a
  // deployment decision and equally trusted.
  if (/^https?:\/\//.test(API_BASE)) {
    try {
      return new URL(API_BASE).origin === origin;
    } catch {
      return false;
    }
  }
  return false;
}

function pause(milliseconds: number, signal?: AbortSignal | null): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(signal.reason);
      return;
    }
    const abort = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      reject(signal?.reason);
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", abort);
      resolve();
    }, milliseconds);
    signal?.addEventListener("abort", abort, { once: true });
  });
}

async function request(input: string, init: RequestInit, waitForEvaluation: boolean): Promise<Response> {
  const deadline = Date.now() + 20_000;
  let retries = 0;
  while (true) {
    const response = await fetch(input, init);
    if (!waitForEvaluation || response.status !== 409 || Date.now() >= deadline) return response;
    const payload = await response.clone().json().catch(() => null);
    const code = payload?.detail?.error?.code ?? payload?.error?.code;
    if (code !== "evaluation_pending") return response;
    const remaining = deadline - Date.now();
    if (remaining <= 0 || retries >= 10) return response;
    retries += 1;
    await pause(Math.min(remaining, 1000 + retries * 200), init.signal);
    if (Date.now() >= deadline) return response;
  }
}

export function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  if (DEMO_MODE) return demoFetch(input, init);
  const headers = new Headers(init?.headers);
  // Credentials only travel to this app's origin (see trustedRequestUrl):
  // a backend-supplied absolute URL must never receive the Bearer token.
  const trusted = trustedRequestUrl(input);
  const token = trusted ? getToken() : null;
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const guestToken = trusted && !token ? getGuestToken() : null;
  if (guestToken && !headers.has("X-Guest-Token")) headers.set("X-Guest-Token", guestToken);
  const method = (init?.method || "GET").toUpperCase();
  const read = method === "GET" || method === "HEAD";
  const signal = read && !init?.signal ? AbortSignal.timeout(30_000) : init?.signal;
  const options = { ...init, headers, signal };
  const pathname = input.split("?")[0].replace(/\/$/, "");
  const start = method === "POST" && pathname.endsWith("/assessment/start");
  const next = method === "POST" && pathname.endsWith("/assessment/next");
  const checkAccess = (response: Response) => {
    if (response.status === 401 && typeof window !== "undefined") {
      if (!token && ((guestToken && getGuestToken() === guestToken) || pathname.endsWith("/guest/session"))) {
        endGuestSession();
        window.dispatchEvent(new Event("edu-access-changed"));
      } else if (token && getToken() === token) {
        window.dispatchEvent(new Event("edu-auth-expired"));
      }
    }
    return response;
  };
  if ((!start && !next) || init?.signal || typeof init?.body !== "string") {
    return request(input, options, next).then(checkAccess);
  }
  const key = JSON.stringify([input, Array.from(headers.entries()), init.body]);
  let pending = pendingRequests.get(key);
  if (!pending) {
    pending = request(input, options, next).finally(() => pendingRequests.delete(key));
    pendingRequests.set(key, pending);
  }
  return pending.then((response) => checkAccess(response.clone()));
}
