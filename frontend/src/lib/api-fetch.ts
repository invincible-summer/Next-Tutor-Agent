/**
 * Shared fetch wrapper that injects the Authorization header from the stored
 * JWT token. ALL backend calls (REST, SSE streams, FormData uploads) go
 * through this instead of bare fetch, so AUTH_MODE=1 works everywhere.
 *
 * SSE works because chatStream uses fetch + ReadableStream (not EventSource),
 * so the Authorization header is attached like any other request; the backend
 * only accepts the header, never a query-param token.
 */
const TOKEN_KEY = "edu-agent-token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const token = getToken();
  const headers: Record<string, string> = { ...(extra || {}) };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return headers;
}

export function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  const headers = authHeaders(
    (init?.headers as Record<string, string>) || undefined,
  );
  // GET 护栏：30s 超时——单个挂死的列表请求不再无限占用浏览器同源连接池
  // （HTTP/1.1 每源 ~6 连接，被占满时页面所有请求冻结）。仅限幂等 GET：
  // SSE 流（POST + ReadableStream）与上传绝不能被超时打断。
  const method = (init?.method || "GET").toUpperCase();
  const isIdempotentGet = (method === "GET" || method === "HEAD") && !init?.signal;
  const timeoutSignal = isIdempotentGet ? AbortSignal.timeout(30_000) : undefined;
  const signal = timeoutSignal ?? init?.signal;
  return fetch(input, { ...init, headers, signal });
}
