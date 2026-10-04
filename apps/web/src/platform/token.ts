// 浏览器 token 存取 adapter：共享客户端与 Web 侧遗留代码共用的唯一实现。
// localStorage 键名与既有部署兼容（DEMO 模式使用 demo 专用键）。
import { DEMO_MODE, DEMO_TOKEN_KEY } from "@/lib/demo";

export const TOKEN_KEY = DEMO_MODE ? DEMO_TOKEN_KEY : "edu-agent-token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  if (typeof window !== "undefined") localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  if (typeof window !== "undefined") localStorage.removeItem(TOKEN_KEY);
}
