// 浏览器 token 存取 adapter：共享客户端与 Web 侧遗留代码共用的唯一实现。
// 企业模式（登录响应含 access_token/refresh_token）下 access token 只留
// 内存，刷新凭据在 HttpOnly cookie——localStorage 不再保存任何可重放的
// 令牌，只留一个会话模式标记。文件模式（无 refresh 端点）维持旧
// localStorage 契约：legacy token 是唯一凭据。
import { DEMO_MODE, DEMO_TOKEN_KEY } from "@/lib/demo";

export const TOKEN_KEY = DEMO_MODE ? DEMO_TOKEN_KEY : "edu-agent-token";
export const AUTH_MODE_KEY = "edu-auth-session";

let memoryToken: string | null = null;

/** 企业会话进行中（标记不是凭据，不含任何令牌材料）。 */
export function enterpriseSessionActive(): boolean {
  if (typeof window === "undefined") return false;
  return window.localStorage.getItem(AUTH_MODE_KEY) === "enterprise";
}

function readStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function getToken(): string | null {
  return memoryToken ?? readStoredToken();
}

/** 刷新轮换后仅更新内存令牌（cookie 由服务端 Set-Cookie 同步轮换）。 */
export function setMemoryToken(token: string): void {
  memoryToken = token;
}

export function setToken(token: string): void {
  memoryToken = token;
  if (typeof window !== "undefined" && !enterpriseSessionActive()) {
    window.localStorage.setItem(TOKEN_KEY, token);
  }
}

/**
 * login/register 成功后的唯一入口。enterprise=true：令牌只进内存并标记
 * 会话模式，同时一次性清掉历史部署遗留的长期 token（迁移窗口收口）；
 * 否则维持文件模式的 localStorage 持久化。
 */
export function startSessionToken(token: string, enterprise: boolean): void {
  memoryToken = token;
  if (typeof window === "undefined") return;
  if (enterprise && !DEMO_MODE) {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.setItem(AUTH_MODE_KEY, "enterprise");
    return;
  }
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  memoryToken = null;
  if (typeof window !== "undefined") {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(AUTH_MODE_KEY);
  }
}
