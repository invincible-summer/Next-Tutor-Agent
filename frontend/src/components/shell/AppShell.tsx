"use client";
import { useEffect, type ReactNode } from "react";
import { useUIStore } from "@/lib/store";
import { SideNav } from "./SideNav";
import { TopBar } from "./TopBar";
import { WorkspaceSettingsModal } from "@/components/workspace/WorkspaceSettingsModal";

/** 学习工作区骨架：全局导航 + 顶栏 + 内容区。 */
export function AppShell({ children }: { children: ReactNode }) {
  useEffect(() => {
    // Hydrate persisted UI prefs (lang/theme/font) AFTER mount so SSR and the
    // first client render match (no hydration mismatch).
    useUIStore.getState().hydrateClient();
  }, []);

  return (
    <div className="flex h-screen overflow-hidden">
      <SideNav />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <main className="min-h-0 flex-1 overflow-hidden">{children}</main>
      </div>
      {/* 全局唯一的工作区设置弹窗（边栏/资料中心等入口经 useWsSettings 唤起） */}
      <WorkspaceSettingsModal />
    </div>
  );
}
