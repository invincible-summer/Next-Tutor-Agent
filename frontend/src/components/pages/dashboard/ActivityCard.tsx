import { useMemo } from "react";
import { Activity } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Sparkline } from "@/components/charts/Sparkline";
import type { UxActivity } from "@/lib/types";
import type { Tr } from "./shared";

const SERIES = [
  { key: "answers", tone: "accent", label: "activity.answers" },
  { key: "teachings", tone: "success", label: "activity.teachings" },
  { key: "reviews", tone: "warning", label: "activity.reviews" },
] as const;

/** 近 14 天学习活动：统一活跃度聚合（/ux/activity）三系列 Sparkline。 */
export function ActivityCard({
  activity,
  tr,
}: {
  activity: UxActivity | null;
  tr: Tr;
}) {
  const { rows, total, first, last, empty } = useMemo(() => {
    const days = activity?.days ?? [];
    return {
      rows: SERIES.map((s) => ({ ...s, values: days.map((d) => d[s.key]) })),
      total: days.reduce((a, d) => a + d.answers + d.teachings + d.reviews, 0),
      first: days[0]?.date ?? "",
      last: days.length ? days[days.length - 1].date : "",
      empty: days.every((d) => d.answers + d.teachings + d.reviews === 0),
    };
  }, [activity]);

  return (
    <Card>
      <CardHeader
        icon={<Activity size={16} />}
        title={tr("activity.title")}
        desc={tr("activity.desc")}
        right={
          <div className="text-right">
            <div className="tnum text-xl font-semibold leading-none text-accent2">{total}</div>
            <div className="mt-1 text-[11px] text-muted">
              {tr("activity.total")} · {tr("activity.unit")}
            </div>
          </div>
        }
      />
      {empty ? (
        <EmptyState title={tr("empty.activity")} desc={tr("empty.activity.desc")} />
      ) : (
        <div className="flex flex-col gap-2.5">
          {rows.map((r) => (
            <div key={r.key} className="flex items-center gap-3">
              <div className="flex w-16 shrink-0 items-center gap-1.5 text-[11px] text-muted">
                <span
                  className="inline-block h-2 w-2 rounded-full"
                  style={{
                    background:
                      r.tone === "accent"
                        ? "rgb(var(--accent))"
                        : r.tone === "success"
                          ? "rgb(var(--success))"
                          : "rgb(var(--warning))",
                  }}
                />
                {tr(r.label)}
              </div>
              <div className="min-w-0 flex-1">
                <Sparkline values={r.values} tone={r.tone} height={30} />
              </div>
            </div>
          ))}
          <div className="flex justify-between text-[11px] text-muted">
            <span className="tnum">{first.slice(5)}</span>
            {activity?.source === "legacy_episodes" && (
              <span>{tr("activity.legacy")}</span>
            )}
            <span className="tnum">{last.slice(5)}</span>
          </div>
        </div>
      )}
    </Card>
  );
}
