import { AlertTriangle, CheckCircle2, Flame, Layers } from "lucide-react";
import { Stat } from "@/components/ui/Stat";
import type { WorkspaceEvaluationListItem } from "@/lib/types-modules";
import type { UxMotivation } from "@/lib/types";
import { fill, type Tr } from "./shared";

/** 四统计卡（plan §14.1：Dashboard 不再另算指标——数字是各学习区覆盖计数
 * 的求和与活跃天数，不是成绩或能力值）。 */
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
  return (
    <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
      <Stat
        icon={<Layers size={16} />}
        label={tr("stat.workspaces")}
        value={workspaces.length}
        tone="accent"
      />
      <Stat
        icon={<CheckCircle2 size={16} />}
        label={tr("stat.observed")}
        value={observed}
        tone="success"
      />
      <Stat
        icon={<AlertTriangle size={16} />}
        label={tr("stat.attention")}
        value={resolve}
        tone="warning"
      />
      <Stat
        icon={<Flame size={16} />}
        label={tr("stat.streak")}
        value={motivation?.streak_days ?? 0}
        tone="accent2"
        foot={motivation ? fill(tr("stat.active.foot"), motivation.active_days) : undefined}
      />
    </div>
  );
}
