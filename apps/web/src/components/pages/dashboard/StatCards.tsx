import type { ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Flame, Layers } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { cn } from "@/lib/cn";
import type { WorkspaceEvaluationListItem } from "@/lib/types-modules";
import type { UxMotivation } from "@/lib/types";
import { fill, type Tr } from "./shared";

const TONE_CLS = {
  accent: "text-accent",
  accent2: "text-accent2",
  success: "text-success",
  warning: "text-warning",
} as const;

/** 汇总带（summary band）：一个容器内 4 组 metric，组间 hairline 分隔。
 * 数字保持 tnum；Dashboard 不再另算指标——数字是各学习区覆盖计数
 * 的求和与活跃天数，不是成绩或能力值。 */
export function StatCards({
  workspaces,
  motivation,
  tr,
}: {
  workspaces: WorkspaceEvaluationListItem[];
  motivation: UxMotivation | null;
  tr: Tr;
}) {
  let observed = 0;
  let resolve = 0;
  for (const w of workspaces) {
    observed += w.coverage?.observed_concepts ?? 0;
    const by = w.coverage?.by_state ?? {};
    resolve += (by.fragile ?? 0) + (by.conflicting ?? 0);
  }
  const metrics: { icon: ReactNode; label: string; value: ReactNode; tone: keyof typeof TONE_CLS; foot?: string }[] = [
    { icon: <Layers size={12} />, label: tr("stat.workspaces"), value: workspaces.length, tone: "accent" },
    { icon: <CheckCircle2 size={12} />, label: tr("stat.observed"), value: observed, tone: "success" },
    { icon: <AlertTriangle size={12} />, label: tr("stat.attention"), value: resolve, tone: "warning" },
    {
      icon: <Flame size={12} />,
      label: tr("stat.streak"),
      value: motivation?.streak_days ?? 0,
      tone: "accent2",
      foot: motivation ? fill(tr("stat.active.foot"), motivation.active_days) : undefined,
    },
  ];
  return (
    <Card pad={false} className="overflow-hidden">
      <div className="grid grid-cols-2 gap-px bg-border-light xl:grid-cols-4">
        {metrics.map((m) => (
          <div key={m.label} className="flex flex-col gap-1 bg-surface px-4 py-3">
            <span className="flex items-center gap-1.5 text-[0.6875rem] text-muted">
              <span className="text-muted/70">{m.icon}</span>
              {m.label}
            </span>
            <span className={cn("tnum text-xl font-semibold leading-none", TONE_CLS[m.tone])}>
              {m.value}
            </span>
            {m.foot && <span className="text-[0.6875rem] text-muted/80">{m.foot}</span>}
          </div>
        ))}
      </div>
    </Card>
  );
}
