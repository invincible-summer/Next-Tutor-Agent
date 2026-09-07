"use client";

import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import type { ContextBudgetReport } from "@/lib/types-modules";
import type { Tr } from "./helpers";

const fmt = (n: number) => Math.round(n).toLocaleString();

export function ContextBudgetPanel({ data, tr }: { data: ContextBudgetReport; tr: Tr }) {
  const pressureTotal = Object.values(data.pressure).reduce((a, b) => a + b, 0);
  const nonNormal = (data.pressure.soft || 0) + (data.pressure.hard || 0);
  const pressureTone = (data.pressure.hard || 0) > 0 ? "danger" : nonNormal > 0 ? "warning" : "success";
  return (
    <Card>
      <CardHeader
        title={tr("ins.context.title")}
        desc={tr("ins.context.desc")}
        right={<div className="flex gap-1.5"><Badge tone={pressureTone}>{tr(`ins.context.pressure.${pressureTone}`)}</Badge><Badge tone="outline">{data.profile.tool_message_mode}</Badge></div>}
      />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[
          [tr("ins.context.window"), fmt(data.profile.context_window), tr("ins.context.tokens")],
          [tr("ins.context.avgInput"), fmt(data.usage.avg_prompt_tokens), `${data.llm_calls} calls`],
          [tr("ins.context.avgOutput"), fmt(data.usage.avg_completion_tokens), tr("ins.context.tokens")],
          [tr("ins.context.saved"), fmt(data.tool_projection.estimated_saved_tokens + data.compaction.estimated_saved_tokens), tr("ins.context.tokens")],
        ].map(([label, value, foot]) => (
          <div key={label} className="rounded-[8px] border border-border-light bg-surface-sunken px-3 py-2.5">
            <div className="text-[11px] text-muted">{label}</div>
            <div className="tnum mt-1 text-lg font-semibold text-fg">{value}</div>
            <div className="mt-0.5 text-[11px] text-muted">{foot}</div>
          </div>
        ))}
      </div>
      <div className="mt-3 grid gap-3 text-xs text-fg-secondary md:grid-cols-3">
        <div>{tr("ins.context.profile")}：<span className="font-medium text-fg">{data.profile.provider} · {data.profile.llm_runtime_mode}</span></div>
        <div>{tr("ins.context.projection")}：<span className="tnum text-fg">{Math.round(data.tool_projection.saved_ratio * 100)}%</span></div>
        <div>{tr("ins.context.recovery")}：<span className="tnum text-fg">{data.recovery.count} / {data.recovery.provider_or_protocol_fallbacks}</span></div>
        <div>{tr("ins.context.channels")}：<span className="tnum text-fg">{fmt(data.usage.avg_reasoning_channel_tokens)} / {fmt(data.usage.avg_answer_channel_tokens)}</span></div>
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-surface-hover">
        <div className="h-full rounded-full bg-accent" style={{ width: `${pressureTotal ? Math.min(100, (nonNormal / pressureTotal) * 100) : 0}%` }} />
      </div>
    </Card>
  );
}
