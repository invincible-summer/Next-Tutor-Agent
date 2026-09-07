"use client";
import { useEffect, useRef } from "react";
import { Reveal } from "./Reveal";
import type { LandingTr } from "./LandingNav";

const STEPS = ["s1", "s2", "s3"] as const;

/**
 * 如何使用分节：大号衬线序号 + 三步上手。
 * 滚动视差：--sp（0..1，段落穿过视口的进度）驱动序号差速浮动
 * 与顶部黛青虚线的生长，rAF 节流，遵循 reduced-motion。
 */
export function HowItWorks({ tr }: { tr: LandingTr }) {
  const ref = useRef<HTMLElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let raf = 0;
    const update = () => {
      raf = 0;
      const r = el.getBoundingClientRect();
      const vh = window.innerHeight;
      const p = Math.min(1, Math.max(0, (vh - r.top) / (vh + r.height)));
      el.style.setProperty("--sp", p.toFixed(3));
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(update);
    };
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, []);

  return (
    <section ref={ref} id="how" className="scroll-mt-24">
      <div className="mx-auto max-w-6xl px-4 py-24 md:py-36">
        <Reveal>
          <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-accent">
            {tr("landing.how.kicker")}
          </p>
          <h2 className="mt-4 max-w-2xl font-serif text-4xl font-bold leading-tight tracking-tight text-fg md:text-5xl">
            {tr("landing.how.title")}
          </h2>
        </Reveal>

        {/* 连接线：黛青虚线随滚动进度生长 */}
        <div aria-hidden className="relative mt-16 hidden h-px md:block">
          <span className="absolute inset-x-0 top-0 border-t border-dashed border-border" />
          <span
            className="absolute inset-x-0 top-0 origin-left border-t border-accent/60"
            style={{ transform: "scaleX(var(--sp, 0))" }}
          />
        </div>

        <div className="mt-10 grid gap-12 md:mt-0 md:grid-cols-3 md:pt-12">
          {STEPS.map((key, i) => (
            <Reveal key={key} delay={i * 100}>
              <span
                className="block font-serif text-7xl font-bold leading-none text-accent/20 will-change-transform md:text-8xl"
                style={{ transform: `translateY(calc((var(--sp, 0.5) - 0.5) * ${26 + i * 16}px))` }}
              >
                0{i + 1}
              </span>
              <h3 className="mt-6 font-serif text-xl font-semibold text-fg">
                {tr(`landing.how.${key}.title`)}
              </h3>
              <p className="mt-3 text-sm leading-relaxed text-fg-secondary">
                {tr(`landing.how.${key}.desc`)}
              </p>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
