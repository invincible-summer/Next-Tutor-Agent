const TOKEN_KEY = "edu-agent-token";
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
  const headers = new Headers(init?.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const method = (init?.method || "GET").toUpperCase();
  const read = method === "GET" || method === "HEAD";
  const signal = read && !init?.signal ? AbortSignal.timeout(30_000) : init?.signal;
  const options = { ...init, headers, signal };
  const pathname = input.split("?")[0].replace(/\/$/, "");
  const start = method === "POST" && pathname.endsWith("/assessment/start");
  const next = method === "POST" && pathname.endsWith("/assessment/next");
  if ((!start && !next) || init?.signal || typeof init?.body !== "string") {
    return request(input, options, next);
  }
  const key = JSON.stringify([input, Array.from(headers.entries()), init.body]);
  let pending = pendingRequests.get(key);
  if (!pending) {
    pending = request(input, options, next).finally(() => pendingRequests.delete(key));
    pendingRequests.set(key, pending);
  }
  return pending.then((response) => response.clone());
}