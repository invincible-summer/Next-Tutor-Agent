// 深链定位公共逻辑（plan.md §8.3，A11）。
// 数据加载后再定位；找不到给温和提示；高亮不等于选择/批准。
// 统一使用 data-*-id 属性锚点，禁止任意 returnTo URL。
"use client";

import { useEffect, useRef } from "react";
import { useSearchParams } from "next/navigation";

/** 读取当前 URL 的深链参数（客户端）。 */
export function deepParam(key: string): string {
  if (typeof window === "undefined") return "";
  try {
    return new URLSearchParams(window.location.search).get(key) || "";
  } catch {
    return "";
  }
}

export function deepParams(...keys: string[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const key of keys) out[key] = deepParam(key);
  return out;
}

/**
 * 同页 query 变化响应（§8.3/AC-23）：挂载时与每次 query 变化（助手
 * pushState 或浏览器前进/后退）时回调 keys 的当前值；签名未变不重放。
 * 使用 useSearchParams，页面需将其置于 <Suspense> 边界内（组件本身
 * 渲染 null，边界只包裹它即可）。
 */
export function DeepLinkQueryReader({
  keys, onParams,
}: {
  keys: string[];
  onParams: (params: Record<string, string>) => void;
}) {
  const search = useSearchParams();
  const onRef = useRef(onParams);
  useEffect(() => {
    onRef.current = onParams;
  }, [onParams]);
  const signature = JSON.stringify(keys.map((k) => [k, search.get(k) ?? ""]));
  const lastRef = useRef<string | null>(null);
  useEffect(() => {
    if (lastRef.current === signature) return;
    lastRef.current = signature;
    const out: Record<string, string> = {};
    for (const key of keys) out[key] = search.get(key) || "";
    onRef.current(out);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);
  return null;
}

const HIGHLIGHT_CLASS = "assistant-deep-highlight";

/** 定位并短暂高亮 [data-<attr>="<value>"]；返回是否找到。 */
export function focusDeepTarget(
    attr: string, value: string, holdMs = 2400): boolean {
  if (!value || typeof document === "undefined") return false;
  const el = document.querySelector<HTMLElement>(
    `[data-${attr}="${CSS.escape(value)}"]`);
  if (!el) return false;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.add(HIGHLIGHT_CLASS);
  window.setTimeout(() => el.classList.remove(HIGHLIGHT_CLASS), holdMs);
  return true;
}

/** 同页同步查询参数（保留其他参数；value 为空时移除该键）。 */
export function syncQueryParam(
    router: { replace: (url: string) => unknown },
    updates: Record<string, string | null>,
    pathname?: string) {
  if (typeof window === "undefined") return;
  const params = new URLSearchParams(window.location.search);
  for (const [key, value] of Object.entries(updates)) {
    if (value) params.set(key, value);
    else params.delete(key);
  }
  const path = pathname || window.location.pathname;
  const query = params.toString();
  router.replace(query ? `${path}?${query}` : path);
}
