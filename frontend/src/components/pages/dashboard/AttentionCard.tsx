import { useState } from "react";
import Link from "next/link";
import { ChevronRight } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/Card";
import { Badge, ModuleBadge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { et, evalStateTone, type Lang } from "@/lib/evaluation-labels";
import type { WorkspaceEvaluationListItem } from "@/lib/types-modules";
import type { Tr } from "./shared";

/** 学习区近况（plan §14.1/§15.3）：各工作区的简短评价状态，点击回学习
 * 档案；Dashboard 不另算指标、不展示总体能力等级。 */
export function AttentionCard({
  workspaces,
  lang,
  tr,
}: {
  workspaces: WorkspaceEvaluationListItem[];
  lang: Lang;
  tr: Tr;
}) {
  const [open, setOpen] = useState(false);
  const rows = open ? workspaces : workspaces.slice(0, 4);
  return (
    <Card>
      <CardHeader
        icon={<ChevronRight size={16} />}
        title={tr("attention.title")}
        desc={tr("attention.desc")}
        right={<ModuleBadge id="L1" />}
      />
      {workspaces.length === 0 ? (
        <EmptyState title={tr("empty.attention")} desc={tr("empty.attention.desc")} />
      ) : (
        <div className="-mx-2 flex flex-col">
          {rows.map((w) => {
            const by = w.coverage?.by_state ?? {};
            const resolve = (by.fragile ?? 0) + (by.conflicting ?? 0);
            return (
              <Link
                key={w.workspace_id}
                href={"/memory?ws=" + encodeURIComponent(w.workspace_id)}
                className="block rounded-[8px] px-2 py-2 transition-colors hover:bg-surface-hover"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm text-fg">
                    {w.workspace_name || w.workspace_id}
                  </span>
                  <ChevronRight size={14} className="shrink-0 text-muted" />
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-1.5">
                  {resolve > 0 && (
                    <Badge tone={evalStateTone("fragile")}>
                      {et(lang, "eval.state.fragile")} {resolve}
                    </Badge>
                  )}
                  {(by.emerging ?? 0) > 0 && (
                    <Badge tone="info">
                      {et(lang, "eval.state.emerging")} {by.emerging}
                    </Badge>
                  )}
                  {(by.supported_in_scope ?? 0) > 0 && (
                    <Badge tone="success">
                      {et(lang, "eval.state.supported_in_scope")} {by.supported_in_scope}
                    </Badge>
                  )}
                  <span className="text-[0.66rem] text-muted">
                    {et(lang, "eval.state.not_observed")} {w.coverage?.not_observed_concepts ?? 0}
                  </span>
                </div>
              </Link>
            );
          })}
          {workspaces.length > 4 && (
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              className="cursor-pointer px-2 py-1.5 text-left text-xs text-accent hover:underline"
            >
              {open ? tr("attention.less") : tr("attention.more")}
            </button>
          )}
        </div>
      )}
    </Card>
  );
}
