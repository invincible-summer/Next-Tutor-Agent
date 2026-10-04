"use client";

// 页面上下文与适配器注册（A08）。
// 每个页面注册一个可卸载适配器；同一时刻只有当前路由的主适配器有效。
// 服务端只接收 getContext() 中允许上传的白名单字段；dirty、本地函数
// 与 DOM 引用不发给模型。PageCommand 是封闭联合，不收字符串函数名。
import type {
  AssistantRouteId,
  NavigationTarget,
  PageCommand,
  PageCommandResult,
} from "./types.generated";

export interface PageContextEntityRef {
  kind: "chat" | "lesson" | "run" | "note" | "concept" | "task";
  id: string;
  parent_id?: string;
  revision?: string;
}

/** 发送给服务端的上下文（≤8KiB 由后端 schema 强制）。 */
export interface UploadablePageContext {
  schema_version: 1;
  route_id: AssistantRouteId;
  route_epoch: number;
  workspace_id?: string;
  entity?: PageContextEntityRef;
  view?: string;
  selection?: { text: string; label: string };
}

export interface AdapterClientState {
  dirty: boolean;
  blocking_activity: "none" | "assessment" | "voice_call" | "unsaved_editor";
  activity_label?: string;
  safe_bottom_px: number;
}

export interface AssistantPageAdapter {
  adapter_id: string;
  getContext(): UploadablePageContext;
  getClientState(): AdapterClientState;
  beforeNavigate(): Promise<"allow" | "stay">;
  navigationStatus(target: NavigationTarget): PageCommandResult | null;
  perform(command: PageCommand): Promise<PageCommandResult>;
}

interface Registration {
  adapter: AssistantPageAdapter;
  pathname: string;
}

let _active: Registration | null = null;
let _currentEpoch = 0;

/** 路由变化时提升自动执行 epoch；适配器按实际挂载路径检查，避免 effect 顺序使新页面失效。 */
export function bumpRouteEpoch(): number {
  _currentEpoch += 1;
  return _currentEpoch;
}

export function currentRouteEpoch(): number {
  return _currentEpoch;
}

/** 注册当前页面主适配器；返回卸载函数。重复注册以后者为准。 */
export function registerPageAdapter(adapter: AssistantPageAdapter): () => void {
  const registration: Registration = {
    adapter, pathname: window.location.pathname,
  };
  _active = registration;
  return () => {
    if (_active === registration) {
      _active = null;
    }
  };
}

export function activeAdapter(): AssistantPageAdapter | null {
  if (_active && _active.pathname === window.location.pathname) {
    return _active.adapter;
  }
  return null;
}

/** useAssistantPage 的入参形态：简单页面只给 context。 */
export interface UseAssistantPageOptions {
  context: () => UploadablePageContext;
  clientState?: () => AdapterClientState;
  beforeNavigate?: AssistantPageAdapter["beforeNavigate"];
  perform?: AssistantPageAdapter["perform"];
  /** null 表示仍在加载；只按已经提交到界面的真实状态确认。 */
  navigationStatus?: AssistantPageAdapter["navigationStatus"];
}

export function defaultClientState(): AdapterClientState {
  return {
    dirty: false,
    blocking_activity: "none",
    safe_bottom_px: 24,
  };
}

/** 路径 → 模块 route_id（与 NAV 一致；课堂子树归入 course）。 */
export function routeIdFromPathname(pathname: string): AssistantRouteId {
  if (pathname === "/" || pathname === "") return "home";
  if (/^\/workspaces\/[^/]+\/classroom(\/|$)/.test(pathname)) return "course";
  const table: Array<[string, AssistantRouteId]> = [
    ["/chat", "chat"],
    ["/course", "course"],
    ["/notes", "notes"],
    ["/dashboard", "dashboard"],
    ["/knowledge", "knowledge"],
    ["/orchestration", "orchestration"],
    ["/assessment", "assessment"],
    ["/tools/illustration", "tools_illustration"],
    ["/tools", "tools"],
    ["/memory", "memory"],
    ["/resources/files", "resources_files"],
    ["/resources/textbooks", "resources_textbooks"],
    ["/archive", "archive"],
    ["/profile", "profile"],
    ["/account", "account"],
    ["/settings", "settings"],
    ["/insights", "insights"],
    ["/docs", "docs"],
    ["/admin", "admin"],
    ["/login", "login"],
    ["/register", "register"],
  ];
  for (const [prefix, route] of table) {
    if (pathname === prefix || pathname.startsWith(prefix + "/")) return route;
  }
  if (pathname.startsWith("/resources")) return "resources_files";
  return "home";
}

/** 从工作区路径提取 workspace_id（如 /workspaces/{id}/classroom）。 */
export function workspaceIdFromPathname(pathname: string): string | undefined {
  const match = pathname.match(/^\/workspaces\/([^/]+)/);
  return match ? decodeURIComponent(match[1]) : undefined;
}

export type { NavigationTarget, PageCommand, PageCommandResult };
