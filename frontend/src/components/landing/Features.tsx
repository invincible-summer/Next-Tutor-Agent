"use client";
import { useEffect, useState } from "react";
import { Plus } from "lucide-react";
import { cn } from "@/lib/cn";
import { Reveal } from "./Reveal";
import type { LandingTr } from "./LandingNav";

const FEATURES = ["f1", "f2", "f3", "f4", "f5", "f6", "f7"] as const;
const POINTS = ["p1", "p2", "p3"] as const;

/**
 * 功能分节：编辑式编号列表（无卡片），行悬停染黛青。
 * 行可点击原地展开详述（单开手风子，不跳页）；页内锚点导航或离开
 * 页面即自动收起。展开动画用 grid-rows 0fr→1fr，零 JS 库。
 */
export function Features({ tr }: { tr: LandingTr }) {
  const [open, setOpen] = useState<string | null>(null);

  // 顶栏锚点导航（#features/#modules/#how）即"跳转走"，顺手收起展开行。
  useEffect(() => {
    const collapse = () => setOpen(null);
    window.addEventListener("hashchange", collapse);
    return () => window.removeEventListener("hashchange", collapse);
  }, []);

  return (
    <section id="features" data-landing-snap className="scroll-mt-24">
      <div className="mx-auto max-w-6xl px-4 py-24 md:py-36">
        <Reveal>
          <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-accent">
            {tr("landing.features.kicker")}
          </p>
          <h2 className="mt-4 max-w-2xl font-serif text-4xl font-bold leading-tight tracking-tight text-fg md:text-5xl">
            {tr("landing.features.title")}
          </h2>
          <p className="mt-5 max-w-xl leading-relaxed text-fg-secondary">
            {tr("landing.features.subtitle")}
          </p>
        </Reveal>

        <div className="mt-16 border-t border-border">
          {FEATURES.map((key, i) => {
            const expanded = open === key;
            return (
              <Reveal key={key} delay={i * 60}>
                <div className="border-b border-border">
                  <div className="group relative grid items-baseline gap-2 py-7 transition-colors duration-300 hover:bg-surface md:grid-cols-[72px_1fr_1.1fr_32px] md:gap-6 md:px-4">
                    {/* 底部黛青发线：hover 或展开时从左扫入并保持 */}
                    <span
                      aria-hidden
                      className={cn(
                        "pointer-events-none absolute inset-x-0 bottom-0 h-px origin-left bg-accent transition-transform duration-500 ease-out",
                        expanded ? "scale-x-100" : "scale-x-0 group-hover:scale-x-100",
                      )}
                    />
                    <span
                      className={cn(
                        "font-mono text-sm text-muted transition-colors duration-300 group-hover:text-accent2",
                        expanded && "text-accent2",
                      )}
                    >
                      0{i + 1}
                    </span>
                    <h3 className="font-serif text-xl font-semibold text-fg transition-all duration-300 group-hover:translate-x-1 group-hover:text-accent md:text-2xl">
                      {tr(`landing.features.${key}.title`)}
                    </h3>
                    <p className="text-sm leading-relaxed text-fg-secondary">
                      {tr(`landing.features.${key}.desc`)}
                    </p>
                    <Plus
                      size={18}
                      aria-hidden
                      className={`justify-self-end transition-all duration-300 ${
                        expanded ? "rotate-45 text-accent" : "text-fg-tertiary group-hover:text-accent"
                      }`}
                    />
                    {/* 整行可点击的透明按钮：保持标题/描述的原有排版与 a11y */}
                    <button
                      type="button"
                      aria-expanded={expanded}
                      aria-label={tr(`landing.features.${key}.title`)}
                      onClick={() => setOpen(expanded ? null : key)}
                      className="absolute inset-0 cursor-pointer rounded-[2px] outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
                    />
                  </div>
                  {/* 展开面板：与整行等宽（含序号列）的纸面卡片，宋体排版；
                      高度 0fr→1fr 展开，内层渐显上浮；reduced-motion 直接显隐。 */}
                  <div
                    className={cn(
                      "grid transition-[grid-template-rows] duration-500 ease-[cubic-bezier(0.22,1,0.36,1)] motion-reduce:transition-none",
                      expanded ? "grid-rows-[1fr]" : "grid-rows-[0fr]",
                    )}
                  >
                    <div className="min-h-0 overflow-hidden">
                      <div className="pb-7 md:px-4">
                        <div
                          className={cn(
                            "rounded-xl border border-border-light bg-surface p-5 shadow-sm transition duration-500 ease-out motion-reduce:transition-none md:p-6",
                            expanded ? "translate-y-0 opacity-100" : "-translate-y-1.5 opacity-0",
                          )}
                        >
                          <p className="max-w-3xl font-serif text-[15px] leading-loose text-fg-secondary md:text-base">
                            {tr(`landing.features.${key}.more`)}
                          </p>
                          <ul className="mt-5 grid gap-2.5 border-t border-border-light pt-5">
                            {POINTS.map((p) => (
                              <li
                                key={p}
                                className="flex items-baseline gap-3 font-serif text-sm leading-relaxed text-fg-secondary md:text-[15px]"
                              >
                                <span
                                  aria-hidden
                                  className="mt-[7px] h-1.5 w-1.5 shrink-0 rotate-45 bg-accent/75"
                                />
                                {tr(`landing.features.${key}.${p}`)}
                              </li>
                            ))}
                          </ul>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </Reveal>
            );
          })}
        </div>
      </div>
    </section>
  );
}
