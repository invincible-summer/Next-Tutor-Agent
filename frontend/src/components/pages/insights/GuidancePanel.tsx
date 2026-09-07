"use client";

// 生效中的教学指导（M7 提案 → 人工应用 → M3 教学引擎消费）。
// 每条显示指导文本/适用范围/影响轮数，可随时吊销（立即回滚教学行为）。
import { useState } from "react";
import { ShieldCheck, Undo2 } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState, ErrorNote } from "@/components/ui/EmptyState";
import { revokeEvalGuidance } from "@/lib/api-modules";
import { fmtTime } from "@/lib/format";
import type { EvalGuidanceEntry } from "@/lib/types-modules";
import type { Tr } from "./helpers";

export function GuidancePanel({
  entries,
  tr,
  onChanged,
}: {
  entries: EvalGuidanceEntry[];
  tr: Tr;
  onChanged: () => void;
}) {
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const active = entries.filter((e) => e.active);

  const revoke = async (id: string) => {
    setBusyId(id);
    setError(null);
    try {
      await revokeEvalGuidance(id);
      onChanged();
    } catch {
      setError(tr("ins.error.revoke"));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <Card>
      <CardHeader
        icon={<ShieldCheck size={16} />}
        title={tr("ins.guidance.title")}
        desc={tr("ins.guidance.desc")}
      />
      {error && (
        <div className="mb-3">
          <ErrorNote message={error} />
        </div>
      )}
      {active.length === 0 ? (
        <EmptyState title={tr("ins.guidance.empty")} />
      ) : (
        <div className="flex flex-col gap-3">
          {active.map((e) => (
            <div
              key={e.id}
              className="flex flex-col gap-1.5 rounded-[8px] border border-border-light bg-surface-sunken/40 p-3"
            >
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="success" dot>
                  {tr("ins.guidance.active")}
                </Badge>
                <span className="text-sm font-medium text-fg">{e.title}</span>
                <Badge tone="muted" className="tnum ml-auto">
                  {e.impact_turns == null
                    ? tr("ins.proposals.impactUnknown")
                    : `${tr("ins.proposals.impact")} ${e.impact_turns}`}
                </Badge>
                <span className="tnum text-xs text-muted">
                  {tr("ins.guidance.appliedAt")} {fmtTime(e.applied_at)}
                </span>
              </div>
              <div className="text-sm leading-relaxed text-fg-secondary">{e.guidance}</div>
              {e.applicability && (
                <div className="text-xs text-muted">
                  {tr("ins.proposals.scope")}：{e.applicability}
                </div>
              )}
              {e.cautions.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {e.cautions.map((c, i) => (
                    <Badge key={i} tone="warning">
                      {tr("ins.proposals.caution")}：{c}
                    </Badge>
                  ))}
                </div>
              )}
              <div className="flex items-center justify-between gap-2">
                <span className="text-[11px] text-muted">
                  {tr("ins.guidance.source")} #{e.source_proposal}
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  icon={<Undo2 size={13} />}
                  disabled={busyId === e.id}
                  onClick={() => revoke(e.id)}
                >
                  {tr("ins.guidance.revoke")}
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
