"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Flame, BookOpen, Settings } from "lucide-react";
import { useEffect, useState } from "react";
import { navItemByPath } from "@/lib/nav";
import { useUIStore } from "@/lib/store";
import { useAuthStore } from "@/lib/auth-store";
import { t, GRADE_LABELS } from "@/lib/i18n";
import { getUxMotivation } from "@/lib/api";
import { ModuleBadge } from "@/components/ui/Badge";
import { AccountMenu } from "./AccountMenu";

/** 顶栏：当前模块标题 + M 徽章 + 学段 + 连续学习火焰 + 账户菜单。 */
export function TopBar() {
  const pathname = usePathname();
  const { lang, grade, mounted } = useUIStore();
  const user = useAuthStore((s) => s.user);
  const [streakData, setStreakData] = useState({ owner: "", days: 0 });
  const streak = streakData.owner === user?.id ? streakData.days : 0;
  const tr = (k: string, fb?: string) => t(lang, k, fb);
  const item = navItemByPath(pathname);

  useEffect(() => {
    if (!mounted || !user) return;
    // 连续学习天数不随路由变化：挂载时拉一次即可，省去每次导航的重复请求。
    let alive = true;
    getUxMotivation()
      .then((m) => { if (alive) setStreakData({ owner: user.id, days: m.streak_days ?? 0 }); })
      .catch(() => { if (alive) setStreakData({ owner: user.id, days: 0 }); });
    return () => { alive = false; };
  }, [mounted, user]);

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
        {user && streak > 0 && (
          <Link
            href="/profile?section=motivation"
            title={tr("ux.streak")}
            className="mr-1 inline-flex items-center gap-1 rounded-full bg-accent2-soft px-2.5 py-1 text-xs font-semibold text-accent2-strong transition-colors hover:opacity-85"
          >
            <Flame size={13} />
            <span className="tnum">{streak}</span>
            <span className="hidden sm:inline">{tr("ux.streak.days")}</span>
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
        {user && <Link href="/settings" title={tr("settings.title")} aria-label={tr("settings.title")}
          aria-current={pathname === "/settings" ? "page" : undefined}
          className={`inline-flex h-8 w-8 items-center justify-center rounded-full transition-colors hover:bg-surface-hover hover:text-accent ${pathname === "/settings" ? "bg-accent-soft text-accent-strong" : "text-fg-tertiary"}`}>
          <Settings size={16} aria-hidden="true" />
        </Link>}
        <AccountMenu key={pathname + (user?.id ?? "guest")} />
      </div>
    </header>
  );
}
