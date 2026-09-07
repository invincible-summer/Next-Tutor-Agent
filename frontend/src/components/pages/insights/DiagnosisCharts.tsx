"use client";

// 两栏诊断卡：失败诊断分布（Donut）+ 策略效果排名（Bars）。
import { Bars, type BarItem } from "@/components/charts/Bars";
import { Donut, DonutLegend, type DonutSlice } from "@/components/charts/Donut";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { dt, type Lang } from "@/lib/labels";
import type { EvalReport } from "@/lib/types-modules";
import { failureColor, failureLabel, fmtGain, type Tr } from "./helpers";

export function DiagnosisCharts({ report, tr, lang }: { report: EvalReport; tr: Tr; lang: Lang }) {
  const failures = Object.entries(report.failure_distribution ?? {})
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1]);

  const slices: DonutSlice[] = failures.map(([ft, n], i) => ({
    label: failureLabel(tr, ft),
    value: n,
    color: failureColor(ft, i),
  }));

  const strategies: BarItem[] = (report.top_strategies ?? []).map((s) => ({
    label: `${dt(lang, `mode.${s.strategy}`, s.strategy)} · ${s.subject || "—"}`,
    value: s.avg_success_rate,
    display: `${fmtGain(s.avg_gain)} / ${Math.round(s.avg_success_rate * 100)}%`,
    hint: `${s.sample_size} ${tr("ins.strategy.samples")}`,
  }));

  const failTotal = failures.reduce((s, [, n]) => s + n, 0);

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader title={tr("ins.fail.title")} desc={tr("ins.fail.desc")} />
        {slices.length === 0 ? (
          <EmptyState title={tr("ins.fail.empty")} />
        ) : (
          <div className="flex items-center gap-6">
            <Donut
              slices={slices}
              size={168}
              centerValue={String(failTotal)}
              centerLabel={tr("ins.stat.evaluated")}
            />
            <div className="min-w-0 flex-1">
              <DonutLegend slices={slices} />
            </div>
          </div>
        )}
      </Card>

      <Card>
        <CardHeader title={tr("ins.strategy.title")} desc={tr("ins.strategy.desc")} />
        {strategies.length === 0 ? (
          <EmptyState title={tr("ins.strategy.empty")} />
        ) : (
          <Bars items={strategies} />
        )}
      </Card>
    </div>
  );
}
