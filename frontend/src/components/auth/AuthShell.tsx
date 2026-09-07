"use client";
import { useEffect, type ReactNode } from "react";
import { GraduationCap } from "lucide-react";
import { useUIStore } from "@/lib/store";
import { t } from "@/lib/i18n";
import { ModuleBadge } from "@/components/ui/Badge";

const FEATURES = [
  { module: "M1", key: "auth.brand.f1" },
  { module: "M9", key: "auth.brand.f2" },
  { module: "M6", key: "auth.brand.f3" },
];

/**
 * 认证页共享骨架（登录/注册）：桌面端左黛青品牌栏 + 右表单栏，移动端收起品牌栏。
 * 全 token 配色，自动适配浅/深主题；登录页不在 workspace layout 内，
 * 这里自行 hydrateClient() 以读取持久化的语言偏好。
 */
export function AuthShell({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const { lang, mounted, hydrateClient } = useUIStore();
  const tr = (k: string) => t(lang, k);

  useEffect(() => {
    if (!mounted) hydrateClient();
  }, [mounted, hydrateClient]);

  const brand = (
    <>
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[10px] bg-white/12 text-white shadow-sm">
        <GraduationCap size={20} />
      </span>
      <span className="min-w-0">
        <span className="block truncate font-serif text-lg font-semibold tracking-tight text-white">
          {tr("app.name")}
        </span>
        <span className="block text-xs text-white/70">{tr("app.role")}</span>
      </span>
    </>
  );

  return (
    <div className="flex min-h-screen bg-bg">
      {/* 品牌栏（桌面端）。固定深黛青（= 浅色 token --accent-strong），
          不随深色主题变浅，保证白字对比度。光斑/颗粒/竖排装饰对齐落地页 CtaBanner。 */}
      <aside className="relative hidden w-[420px] shrink-0 flex-col justify-between overflow-hidden bg-[#1d5650] p-10 lg:flex">
        {/* 漂移光斑 + 纸纹颗粒 */}
        <div
          aria-hidden
          className="drift-a pointer-events-none absolute -top-40 left-1/4 h-96 w-[42rem] rounded-full bg-white/10 blur-3xl"
        />
        <div
          aria-hidden
          className="drift-b pointer-events-none absolute -bottom-36 right-[8%] h-80 w-[30rem] rounded-full bg-white/[0.07] blur-3xl"
        />
        <div aria-hidden className="grain pointer-events-none absolute inset-0" />
        {/* 竖排装饰语 */}
        <span
          aria-hidden
          className="vertical-rl pointer-events-none absolute right-6 top-1/2 -translate-y-1/2 select-none font-serif text-xs tracking-[0.5em] text-white/25"
        >
          {tr("auth.brand.vertical")}
        </span>

        <div className="page-in relative flex items-center gap-3">{brand}</div>
        <div className="relative">
          <h2
            className="page-in font-serif text-[26px] font-semibold leading-relaxed text-white"
            style={{ animationDelay: "80ms" }}
          >
            {tr("app.tagline")}
          </h2>
          <ul className="page-in mt-10 space-y-4" style={{ animationDelay: "160ms" }}>
            {FEATURES.map((f) => (
              <li key={f.module} className="flex items-center gap-3">
                <ModuleBadge id={f.module} className="border-white/40 text-white" />
                <span className="text-sm text-white/85">{tr(f.key)}</span>
              </li>
            ))}
          </ul>
        </div>
        <p className="page-in relative text-xs text-white/45" style={{ animationDelay: "240ms" }}>
          M0 – M9 · {tr("app.role")}
        </p>
      </aside>

      {/* 表单栏 */}
      <main className="flex flex-1 items-center justify-center px-4 py-10">
        <div className="w-full max-w-sm">
          {/* 移动端紧凑品牌头 */}
          <div className="mb-8 flex items-center justify-center gap-2.5 lg:hidden">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[8px] bg-accent text-white shadow-sm">
              <GraduationCap size={18} />
            </span>
            <span>
              <span className="block font-serif text-[15px] font-semibold tracking-tight text-fg">
                {tr("app.name")}
              </span>
              <span className="block text-[10px] leading-tight text-muted">{tr("app.role")}</span>
            </span>
          </div>

          <h1 className="page-in text-center font-serif text-2xl font-bold tracking-tight text-fg lg:text-left">
            {title}
          </h1>
          <p
            className="page-in mt-1 text-center text-sm text-fg-secondary lg:text-left"
            style={{ animationDelay: "80ms" }}
          >
            {subtitle}
          </p>
          <div className="page-in mt-6" style={{ animationDelay: "160ms" }}>
            {children}
          </div>
          {footer && (
            <div
              className="page-in mt-6 text-center text-sm text-fg-secondary"
              style={{ animationDelay: "240ms" }}
            >
              {footer}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
