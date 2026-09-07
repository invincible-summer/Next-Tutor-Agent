import { ModuleBadge } from "@/components/ui/Badge";
import { Reveal } from "./Reveal";
import type { LandingTr } from "./LandingNav";

const MODULES = ["m0", "m1", "m2", "m3", "m4", "m5", "m6", "m7", "m8", "m9", "m10"] as const;

/** 模块分节：M0–M10 编排流水线一览（无边框文字网格 + 徽章）。 */
export function Modules({ tr }: { tr: LandingTr }) {
  return (
    <section id="modules" className="scroll-mt-24 border-y border-border/60 bg-surface-sunken">
      <div className="mx-auto max-w-6xl px-4 py-24 md:py-36">
        <Reveal>
          <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-accent">
            {tr("landing.modules.kicker")}
          </p>
          <h2 className="mt-4 max-w-2xl font-serif text-4xl font-bold leading-tight tracking-tight text-fg md:text-5xl">
            {tr("landing.modules.title")}
          </h2>
          <p className="mt-5 max-w-xl leading-relaxed text-fg-secondary">
            {tr("landing.modules.subtitle")}
          </p>
        </Reveal>

        <div className="mt-16 grid gap-x-10 gap-y-8 sm:grid-cols-2 lg:grid-cols-3">
          {MODULES.map((key, i) => (
            <Reveal key={key} delay={Math.min(i * 40, 320)}>
              <div className="group relative pt-4">
                {/* 顶部发线：hover 时黛青扫入 */}
                <span aria-hidden className="absolute inset-x-0 top-0 h-px bg-border" />
                <span
                  aria-hidden
                  className="absolute inset-x-0 top-0 h-px origin-left scale-x-0 bg-accent transition-transform duration-500 ease-out group-hover:scale-x-100"
                />
                <div className="flex items-center gap-2.5 transition-transform duration-300 group-hover:translate-x-1">
                  <ModuleBadge id={key.toUpperCase()} />
                  <h3 className="text-sm font-semibold text-fg transition-colors duration-300 group-hover:text-accent">
                    {tr(`landing.modules.${key}.name`)}
                  </h3>
                </div>
                <p className="mt-2 text-xs leading-relaxed text-muted">
                  {tr(`landing.modules.${key}.desc`)}
                </p>
              </div>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
