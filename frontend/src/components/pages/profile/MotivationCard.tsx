import { Fragment } from "react";
import { Flame } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/Card";
import { cn } from "@/lib/cn";
import type { UxMotivation } from "@/lib/types";

type Tr = (key: string, fallback?: string) => string;

/** 学习激励卡：连续天数 + 累计活跃 + 里程碑节点条。 */
export function MotivationCard({ moti, tr }: { moti: UxMotivation; tr: Tr }) {
  const streak = moti.streak_days;
  return (
    <Card>
      <CardHeader icon={<Flame size={16} />} title={tr("moti.title")} desc={tr("moti.desc")} />

      <div className="flex flex-col gap-5">
        <div className="flex items-end gap-8">
          <div>
            <div className="flex items-center gap-2">
              <Flame size={26} className="text-accent2" />
              <span className="tnum text-3xl font-semibold leading-none text-accent2">{streak}</span>
            </div>
            <div className="mt-1.5 text-xs text-muted">
              {tr("moti.streak")} · {tr("moti.days")}
            </div>
          </div>
          <div>
            <div className="tnum text-3xl font-semibold leading-none text-fg">{moti.active_days}</div>
            <div className="mt-1.5 text-xs text-muted">
              {tr("moti.active")} · {tr("moti.days")}
            </div>
          </div>
          {moti.next_milestone != null && (
            <div className="ml-auto text-right">
              <div className="tnum text-lg font-semibold leading-none text-accent2-strong">
                {moti.next_milestone}
              </div>
              <div className="mt-1.5 text-xs text-muted">{tr("moti.next")}</div>
            </div>
          )}
        </div>

        {/* 里程碑节点条 */}
        <div className="flex items-center">
          {moti.milestones.map((m, i) => {
            const achieved = streak >= m;
            const isNext = moti.next_milestone === m;
            return (
              <Fragment key={m}>
                {i > 0 && <div className={cn("h-px min-w-3 flex-1", achieved ? "bg-accent2" : "bg-border")} />}
                <div
                  title={`${m} ${tr("moti.days")}`}
                  className={cn(
                    "tnum flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-[11px] font-medium",
                    achieved
                      ? "border-accent2 bg-accent2 text-surface"
                      : isNext
                        ? "border-accent2 bg-accent2-soft text-accent2-strong ring-2 ring-accent2/25"
                        : "border-border bg-surface text-muted",
                  )}
                >
                  {m}
                </div>
              </Fragment>
            );
          })}
        </div>

        <div className="border-t border-border-light pt-2 text-[11px] text-muted">
          {tr("moti.sources")}
        </div>
      </div>
    </Card>
  );
}
