import type { LandingTr } from "./LandingNav";

const ITEMS = ["1", "2", "3", "4", "5", "6", "7", "8"] as const;

/** 无限滚动词条带：核心能力关键词，悬停暂停。 */
export function Marquee({ tr }: { tr: LandingTr }) {
  const row = (hidden: boolean) => (
    <div aria-hidden={hidden} className="flex shrink-0 items-center">
      {ITEMS.map((k) => (
        <span key={k} className="flex items-center">
          <span className="whitespace-nowrap px-6 font-mono text-sm text-fg-secondary">
            {tr(`landing.marquee.${k}`)}
          </span>
          <span aria-hidden className="text-xs text-accent2">
            ✦
          </span>
        </span>
      ))}
    </div>
  );

  return (
    <div className="marquee overflow-hidden border-y border-border/60 py-4">
      <div className="marquee-track flex w-max">
        {row(false)}
        {row(true)}
      </div>
    </div>
  );
}
