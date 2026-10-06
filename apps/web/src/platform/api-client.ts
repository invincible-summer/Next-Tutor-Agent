// 浏览器 adapter：把平台相关的 fetch/token/demo 路由注入共享客户端。
// 这里是 Web 唯一允许触碰 window/localStorage/demoFetch 的客户端装配点；
// 其余模块一律消费 @next-tutor/api-client 的注入式接口。
import { createApiClient, type ApiClient, type FetchInitLike } from "@next-tutor/api-client";
import { API_BASE } from "@/lib/api";
import { silentRefresh } from "@/lib/api-fetch";
import { DEMO_MODE } from "@/lib/demo";
import { demoFetch } from "@/lib/demo-fetch";
import { endGuestSession, getGuestToken } from "@/lib/guest-session";
import { enterpriseSessionActive, getToken, setMemoryToken } from "./token";

interface BrowserClientOptions {
  /**
   * 默认开启：401 时派发 edu-auth-expired / edu-access-changed（与旧
   * apiFetch.checkAccess 语义一致）。auth-store 的 hydrate 路径自带清理
   * 联动，使用 withoutUnauthorizedHook 避免重复处理。
   */
  withUnauthorizedHook?: boolean;
}

function browserFetch(input: string, init?: FetchInitLike): Promise<Response> {
  // The transport only builds plain header maps and string/FormData bodies —
  // structurally valid RequestInit at runtime, cast for the DOM typings.
  const request = init as RequestInit | undefined;
  if (DEMO_MODE) return demoFetch(input, request);
  return fetch(input, request);
}

function dispatchUnauthorized(): void {
  if (typeof window === "undefined") return;
  if (getToken()) {
    window.dispatchEvent(new Event("edu-auth-expired"));
    return;
  }
  // 无登录 token 的 401 视为游客会话失效（与 apiFetch 的 guest 分支一致；
  // /guest/session 自身的管理仍由 guest-session 模块负责）。
  if (getGuestToken()) {
    endGuestSession();
    window.dispatchEvent(new Event("edu-access-changed"));
  }
}

/**
 * 401 处理（企业刷新轨优先）：先尝试用 HttpOnly cookie 静默换新 access
 * token；成功则更新内存令牌后直接返回——transport 会因 tokenProvider 产出
 * 了不同令牌而自动重试一次。刷新失败才降级为旧事件语义（会话失效）。
 */
async function handleUnauthorized(): Promise<void> {
  if (typeof window !== "undefined" && !DEMO_MODE && enterpriseSessionActive()) {
    const fresh = await silentRefresh();
    if (fresh) {
      setMemoryToken(fresh);
      return;
    }
  }
  dispatchUnauthorized();
}

export function createBrowserClient(options: BrowserClientOptions = {}): ApiClient {
  const { withUnauthorizedHook = true } = options;
  return createApiClient({
    baseUrl: API_BASE,
    fetchImpl: browserFetch,
    tokenProvider: () => getToken(),
    guestTokenProvider: () => (!DEMO_MODE && !getToken() ? getGuestToken() : null),
    clientMetadataProvider: () => ({ platform: "web" }),
    onUnauthorized: withUnauthorizedHook ? handleUnauthorized : undefined,
    // 与旧 apiFetch 的读超时一致：GET/HEAD 无显式 signal 时 30s。
    defaultReadTimeoutMs: 30_000,
  });
}

let sharedClient: ApiClient | null = null;

/** 全局单例（带 401 事件钩子），供已迁移域使用。 */
export function apiClient(): ApiClient {
  if (!sharedClient) sharedClient = createBrowserClient();
  return sharedClient;
}

export type { ApiClient };
