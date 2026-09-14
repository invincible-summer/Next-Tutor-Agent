"use client";

// 顶部统计卡（plan §13.7/§14.7）：教学质量口径——轮次/已评估/待审批提案；
// 平均学习增益等学生数值字段已删除。
import { Stat } from "@/components/ui/Stat";
import type { EvalReport } from "@/lib/types-modules";
import type { Tr } from "./helpers";

export function OverviewStats({ report, tr }: { report: EvalReport; tr: Tr }) {
  const strategies = report.top_strategies?.length ?? 0;
  return (
    <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
      <Stat label={tr("ins.stat.turns")} value={report.total_turns} />
      <Stat
        label={tr("ins.stat.evaluated")}
        value={report.total_evaluated}
        foot={
          report.total_turns > 0
            ? `${Math.round((report.total_evaluated / report.total_turns) * 100)}%`
            : undefined
        }
      />
      <Stat
        label={tr("ins.stat.strategies", "教学策略")}
        value={strategies}
        tone="accent"
        foot={tr("ins.stat.strategies.foot", "有实测记录的教学策略数")}
      />
      <Stat
        label={tr("ins.stat.pending")}
        value={report.pending_proposals}
        tone={report.pending_proposals > 0 ? "accent2" : "default"}
        foot={tr("ins.stat.pending.foot")}
      />
    </div>
  );
}
