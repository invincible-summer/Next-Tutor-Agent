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
        "relative flex h-full shrink-0 flex-col border-r border-border-light bg-linear-to-b from-surface via-surface to-bg transition-[width] duration-300 ease-out motion-reduce:transition-none",
        navCollapsed ? "w-[68px]" : "w-[232px]",
      )}
    >
      {/* 品牌：点击回首页 */}
      <Link
        href="/"
        aria-label={tr("app.name")}
        title={navCollapsed ? tr("app.name") : undefined}
        className={cn(
          "group flex h-[76px] shrink-0 items-center gap-3 px-5 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent",
          navCollapsed && "justify-center px-0",
        )}
      >
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent text-surface shadow-sm ring-1 ring-inset ring-white/15 transition-transform duration-200 group-hover:-rotate-6 motion-reduce:transform-none">
          <GraduationCap size={20} strokeWidth={1.7} aria-hidden="true" />
        </span>
        {!navCollapsed && (
          <span className="min-w-0">
            <span className="block truncate font-serif text-[16px] font-semibold tracking-tight text-fg">
              {tr("app.name")}
            </span>
            <span className="mt-1 block text-[10px] tracking-[0.08em] text-muted">{tr("app.role")}</span>
          </span>
        )}
      </Link>

      {/* 导航组 */}
      <nav id="workspace-navigation" aria-label={tr("nav.label")} className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-2.5 pb-4">
        {groups.map((group, groupIndex) => (
          <div key={group.i18nKey} className={cn(groupIndex > 0 && "mt-4")}>
            {navCollapsed && groupIndex > 0 && <div aria-hidden="true" className="mx-auto mb-3 h-px w-5 bg-border" />}
            {!navCollapsed && (
              <div className="mb-2 flex items-center gap-3 px-3 pt-1 text-[10px] font-medium tracking-[0.16em] text-muted">
                <span className="shrink-0">{tr(group.i18nKey)}</span>
                <span aria-hidden="true" className="h-px flex-1 bg-border-light" />
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
                      "group relative flex h-10 shrink-0 items-center gap-3 rounded-xl text-[13px] outline-none transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-inset motion-reduce:transition-none",
                      navCollapsed
                        ? "mx-auto w-11 justify-center p-0"
                        : "px-3",
                      active
                        ? "bg-accent-soft font-semibold text-accent-strong ring-1 ring-inset ring-accent/10"
                        : "text-fg-secondary hover:bg-surface-hover hover:text-fg",
                    )}
                  >
                    {active && <span aria-hidden="true" className="absolute left-0 h-4 w-[3px] rounded-r-full bg-accent" />}
                    <Icon size={18} strokeWidth={active ? 2 : 1.65} aria-hidden="true" className={cn("shrink-0 transition-colors", !active && "text-muted group-hover:text-accent")} />
                    {!navCollapsed && (
                      <>
                        <span className="min-w-0 flex-1 truncate">{tr(item.i18nKey)}</span>
                        {active && <span aria-hidden="true" className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent/70" />}
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
      <div className="mx-2.5 shrink-0 border-t border-border-light py-3">
        <button
          onClick={toggleNav}
          className={cn(
            "flex h-10 w-full cursor-pointer items-center gap-3 rounded-xl border border-transparent px-3 text-xs text-muted outline-none transition-colors hover:border-border-light hover:bg-surface hover:text-fg focus-visible:ring-2 focus-visible:ring-accent",
            navCollapsed && "justify-center px-0",
          )}
          aria-label={toggleLabel}
          title={toggleLabel}
          aria-expanded={!navCollapsed}
          aria-controls="workspace-navigation"
        >
          {navCollapsed ? <PanelLeftOpen size={17} aria-hidden="true" /> : <PanelLeftClose size={17} aria-hidden="true" />}
          {!navCollapsed && <span>{tr("nav.collapse")}</span>}
        </button>
      </div>
    </aside>
  );
}
