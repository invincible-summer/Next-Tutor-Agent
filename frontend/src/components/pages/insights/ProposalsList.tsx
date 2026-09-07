"use client";

// 改进提案列表（人工确认门）：批准 / 拒绝 / 标记已应用。
// 现行提案为开放式教学指导（标题/适用范围/指导文本/注意事项），
// 旧式 target 型提案回落 change/rationale 展示；已应用提案带影响回显。
import { useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState, ErrorNote } from "@/components/ui/EmptyState";
import { Pager, paged, pageCount } from "@/components/ui/Pager";
import { Progress } from "@/components/ui/Progress";
import { patchProposal } from "@/lib/api-modules";
import { fmtTime } from "@/lib/format";
import type { EvalProposal } from "@/lib/types-modules";
import { proposalStatusTone, proposalTargetTone, type Tr } from "./helpers";

type PatchStatus = "approved" | "rejected" | "applied";

export function ProposalsList({
  proposals,
  tr,
  onChanged,
}: {
  proposals: EvalProposal[];
  tr: Tr;
  onChanged: () => void;
}) {
  const [busyId, setBusyId] = useState<string | null>(null);
  const [patchError, setPatchError] = useState<string | null>(null);
  const [page, setPage] = useState(0);

  const act = async (id: string, status: PatchStatus) => {
    setBusyId(id);
    setPatchError(null);
    try {
      await patchProposal(id, status);
      onChanged();
    } catch {
      setPatchError(tr("ins.error.patch"));
    } finally {
      setBusyId(null);
    }
  };

  // 待审批置顶，其余按时间倒序。
  const ordered = [...proposals].sort((a, b) => {
    const pa = a.status === "proposed" ? 0 : 1;
    const pb = b.status === "proposed" ? 0 : 1;
    return pa - pb || b.ts - a.ts;
  });
  // 数据回源后条数可能变少：切片页码先钳位，避免停在空白页。
  const cur = Math.min(page, pageCount(ordered.length) - 1);
  const rows = paged(ordered, cur);

  return (
    <Card>
      <CardHeader title={tr("ins.proposals.title")} desc={tr("ins.proposals.desc")} />
      {patchError && (
        <div className="mb-3">
          <ErrorNote message={patchError} />
        </div>
      )}
      {ordered.length === 0 ? (
        <EmptyState title={tr("ins.proposals.empty")} />
      ) : (
        <div className="flex flex-col gap-3">
          {rows.map((p) => (
            <ProposalRow key={p.id} p={p} tr={tr} busy={busyId === p.id} onAct={act} />
          ))}
        </div>
      )}
      <Pager page={cur} total={ordered.length} onPage={setPage} />
    </Card>
  );
}

function ProposalRow({
  p,
  tr,
  busy,
  onAct,
}: {
  p: EvalProposal;
  tr: Tr;
  busy: boolean;
  onAct: (id: string, status: PatchStatus) => void;
}) {
  // 现行格式：教学指导文本非空；否则是旧式 target 型提案
  const isGuidance = Boolean(p.guidance);
  return (
    <div className="flex flex-col gap-2 rounded-[8px] border border-border-light bg-surface-sunken/40 p-3">
      <div className="flex flex-wrap items-center gap-2">
        {isGuidance ? (
          <Badge tone="accent">{tr("ins.target.guidance")}</Badge>
        ) : (
          <Badge tone={proposalTargetTone(p.target)}>
            {tr(`ins.target.${p.target}`, p.target)}
          </Badge>
        )}
        <Badge tone={proposalStatusTone(p.status)} dot>
          {tr(`ins.status.${p.status}`, p.status)}
        </Badge>
        {p.status === "applied" && (
          <Badge tone="muted" className="tnum">
            {p.impact_turns == null
              ? tr("ins.proposals.impactUnknown")
              : `${tr("ins.proposals.impact")} ${p.impact_turns}`}
          </Badge>
        )}
        <span className="tnum ml-auto text-xs text-muted">{fmtTime(p.ts)}</span>
      </div>

      {isGuidance ? (
        <>
          <div className="text-sm font-medium leading-relaxed text-fg">{p.title}</div>
          <div className="text-sm leading-relaxed text-fg-secondary">{p.guidance}</div>
          {p.applicability && (
            <div className="text-xs leading-relaxed text-muted">
              {tr("ins.proposals.scope")}：{p.applicability}
            </div>
          )}
          {p.cautions && p.cautions.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {p.cautions.map((c, i) => (
                <Badge key={i} tone="warning">
                  {tr("ins.proposals.caution")}：{c}
                </Badge>
              ))}
            </div>
          )}
        </>
      ) : (
        <>
          <div className="text-sm leading-relaxed text-fg">{p.change}</div>
          {p.rationale && (
            <div className="text-xs leading-relaxed text-muted">{p.rationale}</div>
          )}
        </>
      )}

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="flex min-w-[160px] flex-1 items-center gap-2">
          <span className="shrink-0 text-xs text-muted">{tr("ins.proposals.confidence")}</span>
          <Progress value={p.confidence} tone="accent" className="max-w-[120px]" />
          <span className="tnum text-xs text-fg-secondary">
            {Math.round(p.confidence * 100)}%
          </span>
        </div>
        <span className="tnum text-xs text-muted">
          {p.evidence?.length ?? 0} {tr("ins.proposals.evidence")}
        </span>

        {p.status === "proposed" && (
          <div className="ml-auto flex gap-2">
            <Button
              size="sm"
              disabled={busy}
              onClick={() => onAct(p.id, "approved")}
            >
              {tr("ins.proposals.approve")}
            </Button>
            <Button
              size="sm"
              variant="danger"
              disabled={busy}
              onClick={() => onAct(p.id, "rejected")}
            >
              {tr("ins.proposals.reject")}
            </Button>
          </div>
        )}
        {p.status === "approved" && (
          <Button
            size="sm"
            variant="outline"
            className="ml-auto"
            disabled={busy}
            onClick={() => onAct(p.id, "applied")}
          >
            {tr("ins.proposals.apply")}
          </Button>
        )}
      </div>
    </div>
  );
}
