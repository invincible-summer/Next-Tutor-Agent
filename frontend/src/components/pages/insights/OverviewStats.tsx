"use client";

// 顶部四张统计卡：总轮次 / 已评估 / 平均学习增量 / 待审批提案。
import { Stat } from "@/components/ui/Stat";
import type { EvalReport } from "@/lib/types-modules";
import { fmtGain, type Tr } from "./helpers";

export function OverviewStats({ report, tr }: { report: EvalReport; tr: Tr }) {
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
        label={tr("ins.stat.gain")}
        value={fmtGain(report.avg_learning_gain)}
        tone={report.avg_learning_gain == null ? "default" : report.avg_learning_gain >= 0 ? "success" : "danger"}
        foot={tr("ins.stat.gain.foot")}
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
