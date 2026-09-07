"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { GraduationCap, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { NAV } from "@/lib/nav";
import { useUIStore } from "@/lib/store";
import { useAuthStore } from "@/lib/auth-store";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/cn";
import { ModuleBadge } from "@/components/ui/Badge";

/** 全局左侧导航：学习工作区各模块入口（M1–M8）。 */
export function SideNav() {
  const pathname = usePathname();
  const { lang, navCollapsed, toggleNav } = useUIStore();
  const isAdmin = useAuthStore((s) => s.user?.role === "admin");
  const tr = (k: string, fb?: string) => t(lang, k, fb);

  return (
    <aside
      className={cn(
        "flex h-full flex-col border-r border-border bg-surface transition-[width] duration-300 ease-out",
        navCollapsed ? "w-[60px]" : "w-[224px]",
      )}
    >
      {/* 品牌：点击回首页 */}
      <Link
        href="/"
        className={cn(
          "flex h-14 shrink-0 items-center gap-2.5 border-b border-border-light px-3",
          navCollapsed && "justify-center px-0",
        )}
      >
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[8px] bg-accent text-white shadow-sm">
          <GraduationCap size={17} />
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
      <nav className="flex-1 overflow-y-auto px-2 py-3">
        {NAV.map((group) => (
          <div key={group.i18nKey} className="mb-5">
            {!navCollapsed && (
              <div className="mb-1.5 px-3 text-[10px] font-semibold uppercase tracking-[0.18em] text-muted/70">
                {tr(group.i18nKey)}
              </div>
            )}
            <div className="flex flex-col gap-1">
              {group.items.filter((item) => !item.adminOnly || isAdmin).map((item) => {
                const active = pathname === item.href || pathname.startsWith(item.href + "/");
                const Icon = item.icon;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    title={navCollapsed ? tr(item.i18nKey) : undefined}
                    className={cn(
                      "flex items-center gap-2.5 text-[13px] transition-all duration-200",
                      navCollapsed
                        ? "mx-auto h-9 w-9 justify-center rounded-full p-0"
                        : "rounded-full px-3.5 py-2",
                      active
                        ? "bg-accent-soft font-medium text-accent-strong"
                        : "text-fg-secondary hover:bg-surface-hover hover:text-fg",
                    )}
                  >
                    <Icon size={17} className="shrink-0" />
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
      <div className="border-t border-border-light p-2">
        <button
          onClick={toggleNav}
          className={cn(
            "flex w-full cursor-pointer items-center gap-2 rounded-[8px] px-2.5 py-2 text-xs text-muted hover:bg-surface-hover hover:text-fg",
            navCollapsed && "justify-center px-0",
          )}
          aria-label="toggle navigation"
        >
          {navCollapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
          {!navCollapsed && <span>{tr("nav.collapse")}</span>}
        </button>
      </div>
    </aside>
  );
}
