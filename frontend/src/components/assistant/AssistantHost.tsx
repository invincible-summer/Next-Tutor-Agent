"use client";

// 助手宿主（plan.md §5.1，A06 挂载骨架 / A07 身份 / A08 页面上下文）：
// Portal 渲染，目标跟随 document.fullscreenElement（GAP-07）；
// 面板懒加载不进首屏包；Ctrl/Cmd+Shift+K 切换；enabled=false 不渲染入口；
// 路由变化提升 route_epoch 并刷新页面上下文（旧适配器随之失效）。
import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { createPortal } from "react-dom";
import dynamic from "next/dynamic";
import { useAssistantStore } from "@/lib/assistant/store";
import { useAuthStore } from "@/lib/auth-store";
import {
  activeAdapter,
  bumpRouteEpoch,
  routeIdFromPathname,
  workspaceIdFromPathname,
} from "@/lib/assistant/page-context";
import { AssistantLauncher } from "./AssistantLauncher";

const AssistantPanel = dynamic(
  () => import("./AssistantPanel").then((m) => ({ default: m.AssistantPanel })),
  { ssr: false },
);

export function AssistantHost({ safeBottomPx }: { safeBottomPx?: number }) {
  const capabilities = useAssistantStore((s) => s.capabilities);
  const refreshCapabilities = useAssistantStore((s) => s.refreshCapabilities);
  const toggle = useAssistantStore((s) => s.toggle);
  const user = useAuthStore((s) => s.user);
  const pathname = usePathname();
  const router = useRouter();
  const setPageContext = useAssistantStore((s) => s.setPageContext);
  const setRouterPush = useAssistantStore((s) => s.setRouterPush);
  const launcherRef = useRef<HTMLButtonElement>(null);
  const [portalTarget, setPortalTarget] = useState<Element | null>(null);
  const [checked, setChecked] = useState(false);

  // 动作命令执行需要的路由能力（A10）；store 在 React 外，经此注入。
  useEffect(() => {
    setRouterPush((url: string) => {
      const target = new URL(url, window.location.href);
      if (target.origin === window.location.origin && target.pathname === window.location.pathname) {
        window.history.pushState(null, "", url);
      } else {
        router.push(url);
      }
    });
  }, [router, setRouterPush]);

  // Portal 目标：默认 body；原生全屏时跟随 fullscreenElement（退出回 body）。
  useEffect(() => {
    const resolve = () =>
      setPortalTarget(document.fullscreenElement ?? document.body);
    resolve();
    document.addEventListener("fullscreenchange", resolve);
    return () => document.removeEventListener("fullscreenchange", resolve);
  }, []);

  // 路由变化：提升 epoch，按当前适配器（或路径回退）刷新上下文。
  useEffect(() => {
    const epoch = bumpRouteEpoch();
    const adapter = activeAdapter();
    const fallback = {
      schema_version: 1 as const,
      route_id: routeIdFromPathname(pathname),
      route_epoch: epoch,
      workspace_id: workspaceIdFromPathname(pathname),
    };
    setPageContext(adapter ? adapter.getContext() : fallback);
  }, [pathname, setPageContext]);

  // §5.3-1 课堂控制条避让：读取当前适配器上报的 safe_bottom_px。
  // 适配器契约是拉取式，用低频轮询同步（页面侧 ResizeObserver 实测）。
  const [adapterBottom, setAdapterBottom] = useState<number | null>(null);
  useEffect(() => {
    const read = () => {
      const value = activeAdapter()?.getClientState().safe_bottom_px ?? 0;
      setAdapterBottom(value > 24 ? value : null);
    };
    read();
    const id = window.setInterval(read, 1500);
    return () => window.clearInterval(id);
  }, []);

  // 能力检查一次；enabled=false 不渲染入口（§19.9）。
  useEffect(() => {
    if (!user) return;
    let alive = true;
    void refreshCapabilities().finally(() => {
      if (alive) setChecked(true);
    });
    return () => {
      alive = false;
    };
  }, [refreshCapabilities, user]);

  // 身份变化：清理内存会话与本地草稿（§4.2）。
  const userId = user?.id ?? null;
  const resetForIdentity = useAssistantStore((s) => s.resetForIdentity);
  const knownUser = useRef<string | null>(null);
  useEffect(() => {
    if (knownUser.current !== undefined
        && knownUser.current !== userId) {
      resetForIdentity(userId);
    }
    knownUser.current = userId;
  }, [userId, resetForIdentity]);

  // 快捷键：Ctrl/Cmd+Shift+K 切换（§3.6）。
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!user) return;
      if ((event.ctrlKey || event.metaKey) && event.shiftKey
          && event.key.toLowerCase() === "k") {
        event.preventDefault();
        toggle();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggle, user]);

  if (typeof window === "undefined" || !portalTarget) return null;
  if (!user || !checked || !capabilities || capabilities.enabled === false) return null;

  return createPortal(
    <>
      <AssistantLauncher bottomPx={adapterBottom ?? safeBottomPx} buttonRef={launcherRef} />
      <AssistantPanel launcherRef={launcherRef} />
    </>,
    portalTarget,
  );
}
