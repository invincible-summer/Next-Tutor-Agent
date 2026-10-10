"use client";
import { useEffect, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { useAuthStore } from "@/lib/auth-store";
import { useUIStore } from "@/lib/store";
import { useAssistantStore } from "@/lib/assistant/store";
import { SideNav } from "./SideNav";
import { TopBar } from "./TopBar";
import { WorkspaceSettingsModal } from "@/components/workspace/WorkspaceSettingsModal";

/** 学习工作区骨架：全局导航 + 顶栏 + 内容区。 */
export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const owner = useAuthStore((s) => s.user?.id ?? "guest");
  const electricalImmersive = pathname === "/tools/lab/electronics" || pathname.startsWith("/tools/lab/electronics/");
  const geometryImmersive = pathname === "/tools/geometry" || pathname.startsWith("/tools/geometry/");
  const immersive = /\/classroom\/[^/]+\/learn\/[^/]+\/?$/.test(pathname) || electricalImmersive || geometryImmersive;
  useEffect(() => {
    // The chat session rail is useful by default on desktop, but at phone
    // widths its 16rem flex width leaves only a sliver for the learning card.
    // Keep the global module icon rail visible and start only the session rail
    // closed; the chat page's menu button can still open it on demand.
    if (window.matchMedia("(max-width: 767px)").matches
        && useUIStore.getState().sidebarOpen) {
      useUIStore.setState({ sidebarOpen: false });
    }
  }, []);

  // 沉浸式课堂上报：进入课堂播放时助手收起并保留草稿。
  useEffect(() => {
    useAssistantStore.setState({ immersiveMode: immersive });
    if (immersive) {
      useAssistantStore.getState().collapse();
    }
  }, [immersive]);

  return (
    <div className="flex h-screen overflow-hidden print:h-auto print:overflow-visible">
      {!immersive && <div className="print:hidden"><SideNav /></div>}
      <div className="flex min-w-0 flex-1 flex-col">
        {!immersive && <div className="print:hidden"><TopBar key={`topbar:${owner}`} /></div>}
        <main key={`content:${owner}`} className="min-h-0 flex-1 overflow-hidden print:h-auto print:overflow-visible">{children}</main>
      </div>
      {/* 全局唯一的工作区设置弹窗（边栏/资料中心等入口经 useWsSettings 唤起）。
          电路实验室与几何作图器是独立场景，不把全局弹层带进工作台。 */}
      {!electricalImmersive && !geometryImmersive && <WorkspaceSettingsModal />}
    </div>
  );
}
