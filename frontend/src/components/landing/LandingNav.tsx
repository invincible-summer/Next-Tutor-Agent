"use client";
import Link from "next/link";
import { GraduationCap, Moon, Sun } from "lucide-react";
import { useUIStore } from "@/lib/store";

export type LandingTr = (key: string, fallback?: string) => string;

const ANCHORS = [
  { href: "#features", key: "landing.nav.features" },
  { href: "#modules", key: "landing.nav.modules" },
  { href: "#how", key: "landing.nav.how" },
];

/**
 * 落地页悬浮胶囊导航：脱离文档流的毛玻璃胶囊，
 * 品牌 + 锚点 + 语言/主题切换 + 登录/注册入口；已登录时主按钮指向 /chat。
 */
export function LandingNav({ tr, loggedIn }: { tr: LandingTr; loggedIn: boolean }) {
  const { lang, setLang, theme, toggleTheme } = useUIStore();

  return (
    <header className="fixed inset-x-0 top-4 z-50 flex justify-center px-4">
      <div className="flex h-12 items-center gap-0.5 rounded-full border border-border/70 bg-bg/75 pl-4 pr-1.5 shadow-md backdrop-blur-md">
        <Link href="/" className="flex items-center gap-2 pr-1">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent text-white">
            <GraduationCap size={14} />
          </span>
          <span className="whitespace-nowrap font-serif text-sm font-semibold tracking-tight text-fg">
            {tr("app.name")}
          </span>
        </Link>

        <span className="mx-2 hidden h-4 w-px bg-border md:block" />
        <nav className="hidden items-center md:flex">
          {ANCHORS.map((a) => (
            <a
              key={a.href}
              href={a.href}
              className="rounded-full px-3 py-1.5 text-[13px] text-fg-secondary transition-colors hover:bg-surface-hover hover:text-fg"
            >
              {tr(a.key)}
            </a>
          ))}
          {/* 使用文档：工作区路由（匿名可读），与页内锚点同款样式 */}
          <Link
            href="/docs"
            className="rounded-full px-3 py-1.5 text-[13px] text-fg-secondary transition-colors hover:bg-surface-hover hover:text-fg"
          >
            {tr("landing.nav.docs")}
          </Link>
        </nav>

        <span className="mx-2 hidden h-4 w-px bg-border sm:block" />
        <button
          type="button"
          onClick={() => setLang(lang === "zh" ? "en" : "zh")}
          className="flex h-8 w-8 cursor-pointer items-center justify-center rounded-full font-mono text-[11px] text-fg-secondary transition-colors hover:bg-surface-hover"
          title={lang === "zh" ? "Switch to English" : "切换到中文"}
        >
          {lang === "zh" ? "EN" : "中"}
        </button>
        <button
          type="button"
          onClick={toggleTheme}
          className="flex h-8 w-8 cursor-pointer items-center justify-center rounded-full text-fg-secondary transition-colors hover:bg-surface-hover"
          title={theme === "dark" ? "Light" : "Dark"}
        >
          {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
        </button>

        {loggedIn ? (
          <Link
            href="/chat"
            className="ml-1 inline-flex h-9 items-center whitespace-nowrap rounded-full bg-accent px-4 text-[13px] font-medium text-white transition-colors hover:bg-accent-strong"
          >
            {tr("landing.nav.workspace")}
          </Link>
        ) : (
          <>
            <Link
              href="/login"
              className="ml-1 hidden whitespace-nowrap rounded-full px-3 py-1.5 text-[13px] font-medium text-fg-secondary transition-colors hover:text-fg sm:block"
            >
              {tr("landing.nav.login")}
            </Link>
            <Link
              href="/register"
              className="inline-flex h-9 items-center whitespace-nowrap rounded-full bg-accent px-4 text-[13px] font-medium text-white transition-colors hover:bg-accent-strong"
            >
              {tr("landing.nav.start")}
            </Link>
          </>
        )}
      </div>
    </header>
  );
}
