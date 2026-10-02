import Link from "next/link";
import { GraduationCap } from "lucide-react";
import type { LandingTr } from "./LandingNav";

/**
 * 落地页页脚：巨型幽灵字标（固定高度有意裁切，只露上半）+ 底栏。
 * 底栏（© 品牌 / 文档 / 登入 / 注册）是页面的最后一行。
 */
export function Footer({ tr }: { tr: LandingTr }) {
  return (
    <footer className="border-t border-border/60">
      {/* 幽灵字标：裁切高度固定，不再被视口底边截出"半行" */}
      <div aria-hidden className="select-none overflow-hidden pt-14 text-[9vw] leading-none">
        <div className="h-[0.66em] overflow-hidden">
          <div className="whitespace-nowrap text-center font-serif font-bold leading-[0.85] text-fg/5">
            NEXT TUTOR AGENT
          </div>
        </div>
      </div>
      {/* 底栏：页面最后一行 */}
      <div className="mt-10 border-t border-border/60">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-4 py-6 sm:flex-row">
          <div className="flex items-center gap-2">
            <span className="flex h-6 w-6 items-center justify-center rounded-[6px] bg-accent text-white">
              <GraduationCap size={13} />
            </span>
            <span className="text-sm text-fg-secondary">
              © {new Date().getFullYear()} {tr("app.name")}
            </span>
          </div>
          <nav className="flex items-center gap-5">
            <Link href="/docs" className="text-sm text-muted transition-colors hover:text-fg">
              {tr("landing.footer.docs")}
            </Link>
            <Link href="/login" className="text-sm text-muted transition-colors hover:text-fg">
              {tr("landing.footer.login")}
            </Link>
            <Link href="/register" className="text-sm text-muted transition-colors hover:text-fg">
              {tr("landing.footer.register")}
            </Link>
          </nav>
        </div>
      </div>
    </footer>
  );
}
