"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Flame } from "lucide-react";
import { BookOpen, LayoutDashboard, LogOut, User as UserIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { navItemByPath } from "@/lib/nav";
import { useUIStore } from "@/lib/store";
import { useAuthStore } from "@/lib/auth-store";
import { t, GRADE_LABELS } from "@/lib/i18n";
import { getUxMotivation } from "@/lib/api";
import { ModuleBadge } from "@/components/ui/Badge";
import { SettingsGear } from "@/components/SettingsGear";

/** 顶栏：当前模块标题 + M 徽章 + 学段 + 连续学习火焰 + 设置。 */
export function TopBar() {
  const pathname = usePathname();
  const { lang, grade, mounted } = useUIStore();
  const { user, logout } = useAuthStore();
  const [streak, setStreak] = useState(0);
  const tr = (k: string, fb?: string) => t(lang, k, fb);
  const item = navItemByPath(pathname);

  useEffect(() => {
    if (!mounted) return;
    // 连续学习天数不随路由变化：挂载时拉一次即可，省去每次导航的重复请求。
    getUxMotivation()
      .then((m) => setStreak(m.streak_days ?? 0))
      .catch(() => {});
  }, [mounted]);

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border-light bg-surface px-4">
      {item && item.module && <ModuleBadge id={item.module} />}
      <h1 className="font-serif text-[15px] font-semibold tracking-tight text-fg">
        {item ? tr(item.i18nKey) : tr("app.name")}
      </h1>
      <span className="rounded-full bg-surface-sunken px-2 py-0.5 text-xs text-muted">
        {GRADE_LABELS[lang].find((g) => g.token === grade)?.label ?? grade}
      </span>

      <div className="ml-auto flex items-center gap-1.5">
        {streak > 0 && (
          <Link
            href="/profile"
            title={tr("ux.streak")}
            className="mr-1 inline-flex items-center gap-1 rounded-full bg-accent2-soft px-2.5 py-1 text-xs font-semibold text-accent2-strong transition-colors hover:opacity-85"
          >
            <Flame size={13} />
            <span className="tnum">{streak}</span>
            <span className="hidden sm:inline">{tr("ux.streak.days")}</span>
          </Link>
        )}
        {/* 总览快捷入口：已在 /dashboard 时隐藏，避免冗余 */}
        {!pathname.startsWith("/dashboard") && (
          <Link
            href="/dashboard"
            title={tr("nav.dashboard")}
            aria-label={tr("nav.dashboard")}
            className="inline-flex h-8 w-8 items-center justify-center rounded-full text-fg-tertiary transition-colors hover:bg-surface-hover hover:text-accent"
          >
            <LayoutDashboard size={15} />
          </Link>
        )}
        <Link
          href="/docs"
          title={tr("docs.entry", "使用文档")}
          aria-label={tr("docs.entry", "使用文档")}
          className="inline-flex h-8 w-8 items-center justify-center rounded-full text-fg-tertiary transition-colors hover:bg-surface-hover hover:text-accent"
        >
          <BookOpen size={15} />
        </Link>
        <SettingsGear />
        {user && (
          <div className="ml-1 flex items-center gap-2 border-l border-border pl-2">
            <span className="inline-flex items-center gap-1.5 text-xs font-medium text-fg-secondary">
              <UserIcon size={13} className="text-accent" />
              {user.profile.name || user.username || user.email.split("@")[0]}
            </span>
            <button
              onClick={logout}
              title={tr("auth.logout", "退出登录")}
              className="inline-flex h-8 w-8 items-center justify-center rounded-full text-fg-tertiary transition-colors hover:bg-surface-hover hover:text-danger"
            >
              <LogOut size={14} />
            </button>
          </div>
        )}
        {!user && (
          <Link
            href={`/login?redirect=${encodeURIComponent(pathname)}`}
            className="ml-1 inline-flex items-center gap-1.5 rounded-full border border-border px-3.5 py-1.5 text-xs font-medium text-fg-secondary transition-colors hover:border-accent hover:text-accent"
          >
            <UserIcon size={13} />
            {tr("auth.login", "登录")}
          </Link>
        )}
      </div>
    </header>
  );
}
