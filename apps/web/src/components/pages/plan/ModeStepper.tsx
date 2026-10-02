import { Fragment } from "react";
import { cn } from "@/lib/cn";
import { dt } from "@/lib/labels";
import type { Lang } from "@/lib/i18n";

const MODES = [
  "introduction",
  "explanation",
  "remediation",
  "practice",
  "review",
  "challenge",
] as const;

/** 六模式横向步进条：当前模式 accent 高亮，其余 muted。 */
export function ModeStepper({
  current,
  lang,
}: {
  current: string | null;
  lang: Lang;
}) {
  return (
    <div className="flex items-start">
      {MODES.map((m, i) => {
        const active = m === current;
        return (
          <Fragment key={m}>
            {i > 0 && <div className="mx-1 mt-[7px] h-px min-w-3 flex-1 bg-border" />}
            <div className="flex shrink-0 flex-col items-center gap-1.5">
              <span
                className={cn(
                  "h-3.5 w-3.5 rounded-full border-2 transition-colors",
                  active
                    ? "border-accent bg-accent shadow-[0_0_0_3px_rgb(var(--accent)/0.18)]"
                    : "border-border bg-surface-hover",
                )}
              />
              <span
                className={cn(
                  "whitespace-nowrap text-[11px] leading-4",
                  active ? "font-semibold text-accent-strong" : "text-muted",
                )}
              >
                {dt(lang, `mode.${m}`, m)}
              </span>
            </div>
          </Fragment>
        );
      })}
    </div>
  );
}
