"use client";
import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { AppShell } from "@/components/shell/AppShell";
import { useAuthStore, hydrateAuth } from "@/lib/auth-store";

export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  const { loaded, statusLoaded, authRequired, user } = useAuthStore();
  const pathname = usePathname();

  useEffect(() => {
    hydrateAuth();
  }, []);

  // M0: when AUTH_MODE=1 and not authenticated, redirect to login.
  // `statusLoaded` 并入门控：水合并行化后 authRequired 与 token 校验可能
  // 乱序完成，两者都落定才渲染，未登录用户绝不闪现工作区。
  // /docs 使用文档是产品文档（后端 GET /docs/content 本就公开），
  // 匿名访客可读；从 /docs 侧栏进入其他工作区页仍会照常跳登录。
  const needsRedirect = loaded && statusLoaded && authRequired && !user && pathname !== "/docs";
  useEffect(() => {
    if (needsRedirect) {
      const redirect = encodeURIComponent(window.location.pathname);
      window.location.href = `/login?redirect=${redirect}`;
    }
  }, [needsRedirect]);

  // Show nothing while hydrating or redirecting (prevents flash of content)
  if (!loaded || !statusLoaded || needsRedirect) {
    return <div className="min-h-screen bg-bg" />;
  }

  return <AppShell>{children}</AppShell>;
}
