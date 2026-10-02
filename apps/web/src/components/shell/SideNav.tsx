"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { GraduationCap, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { NAV, navItemByPath } from "@/lib/nav";
import { useUIStore } from "@/lib/store";
import { useAuthStore } from "@/lib/auth-store";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/cn";
import { ModuleBadge } from "@/components/ui/Badge";
import { getClassroomCapabilities } from "@/lib/api-classroom";

// 课堂特性开关：乐观显示，确认关闭后隐藏（模块级缓存避免重复请求）。
let classroomEnabledCache: boolean | null = null;
let classroomEnabledReq: Promise<boolean> | null = null;
function loadClassroomEnabled(): Promise<boolean> {
  if (classroomEnabledCache !== null) return Promise.resolve(classroomEnabledCache);
  if (!classroomEnabledReq) {
    classroomEnabledReq = getClassroomCapabilities()
      .then((caps) => {
        classroomEnabledCache = caps.enabled !== false;
        return classroomEnabledCache;
      })
      .catch(() => {
        classroomEnabledReq = null;
        return true; // 网络失败时保持显示，由页面自身兜底
      });
  }
  return classroomEnabledReq;
}

/** 全局左侧导航：学习工作区各模块入口（M1–M8）。 */
export function SideNav() {
  const pathname = usePathname();
  const { lang, navCollapsed, toggleNav } = useUIStore();
  const isAdmin = useAuthStore((s) => s.user?.role === "admin");
  const authenticated = useAuthStore((s) => !!s.user);
  const guestAllowed = useAuthStore((s) => s.guestAllowed);
  const tr = (k: string, fb?: string) => t(lang, k, fb);
  const [classroomEnabled, setClassroomEnabled] = useState(true);

  useEffect(() => {
    if (!authenticated) return;
    let alive = true;
    void loadClassroomEnabled().then((on) => {
      if (alive) setClassroomEnabled(on);
    });
    return () => { alive = false; };
  }, [authenticated]);

  const groups = NAV.map((group) => ({ ...group, items: group.items.filter((item) =>
    (authenticated || (guestAllowed && ["/chat", "/assessment"].includes(item.href)))
    && (!item.adminOnly || isAdmin) && (!item.classroomOnly || classroomEnabled)) })).filter((group) => group.items.length > 0);
  const activeHref = navItemByPath(pathname)?.href;
  const toggleLabel = navCollapsed ? tr("nav.expand") : tr("nav.collapse");

  return (
    <aside
      className={cn(
        "flex h-full shrink-0 flex-col border-r border-border bg-surface transition-[width] duration-300 ease-out motion-reduce:transition-none",
        navCollapsed ? "w-[60px]" : "w-[224px]",
      )}
    >
      {/* 品牌：点击回首页 */}
      <Link
        href="/"
        aria-label={tr("app.name")}
        title={navCollapsed ? tr("app.name") : undefined}
        className={cn(
          "flex h-14 shrink-0 items-center gap-2.5 border-b border-border-light px-3 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent",
          navCollapsed && "justify-center px-0",
        )}
      >
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[8px] bg-accent text-white shadow-sm">
          <GraduationCap size={17} aria-hidden="true" />
        </span>
        {!navCollapsed && (
          <span className="min-w-0">
            <span className="block truncate font-serif text-[15px] font-semibold tracking-tight text-fg">
              {tr("app.name")}
            </span>
            <span className="block text-[10px] leading-tight text-muted">{tr("app.role")}</span>
          </span>
        )}
      </Link>

      {/* 导航组 */}
      <nav id="workspace-navigation" aria-label={tr("nav.label")} className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-2 py-3">
        {groups.map((group) => (
          <div key={group.i18nKey} className="mb-5">
            {!navCollapsed && (
              <div className="mb-1.5 px-3 text-[10px] font-semibold uppercase tracking-[0.18em] text-muted/70">
                {tr(group.i18nKey)}
              </div>
            )}
            <div className="flex flex-col gap-1">
              {group.items.map((item) => {
                const active = activeHref === item.href;
                const Icon = item.icon;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    aria-label={tr(item.i18nKey)}
                    aria-current={active ? "page" : undefined}
                    title={navCollapsed ? tr(item.i18nKey) : undefined}
                    className={cn(
                      "flex shrink-0 items-center gap-2.5 text-[13px] outline-none transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-inset motion-reduce:transition-none",
                      navCollapsed
                        ? "mx-auto h-9 w-9 justify-center rounded-full p-0"
                        : "rounded-full px-3.5 py-2",
                      active
                        ? "bg-accent-soft font-medium text-accent-strong"
                        : "text-fg-secondary hover:bg-surface-hover hover:text-fg",
                    )}
                  >
                    <Icon size={17} aria-hidden="true" className="shrink-0" />
                    {!navCollapsed && (
                      <>
                        <span className="min-w-0 flex-1 truncate">{tr(item.i18nKey)}</span>
                        {item.module && <ModuleBadge id={item.module} />}
                      </>
                    )}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* 折叠开关 */}
      <div className="shrink-0 border-t border-border-light p-2">
        <button
          onClick={toggleNav}
          className={cn(
            "flex w-full cursor-pointer items-center gap-2 rounded-[8px] px-2.5 py-2 text-xs text-muted outline-none transition-colors hover:bg-surface-hover hover:text-fg focus-visible:ring-2 focus-visible:ring-accent",
            navCollapsed && "justify-center px-0",
          )}
          aria-label={toggleLabel}
          title={toggleLabel}
          aria-expanded={!navCollapsed}
          aria-controls="workspace-navigation"
        >
          {navCollapsed ? <PanelLeftOpen size={16} aria-hidden="true" /> : <PanelLeftClose size={16} aria-hidden="true" />}
          {!navCollapsed && <span>{tr("nav.collapse")}</span>}
        </button>
      </div>
    </aside>
  );
}
