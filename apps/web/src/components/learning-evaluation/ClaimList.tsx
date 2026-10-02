"use client";
// 有条件的具体主张列表（plan §14.2/§14.3）：默认显示摘要、逐条展开，
// 不把完整原答案塞在图谱节点 tooltip。按主张状态分组渲染。
// support/challenge refs 是观察 id（obs_*）：仅作溯源徽标展示；
// 证据原文经 [证据时间线]（按 concept_key 过滤来源）查看。
import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { claimTone, et, type Lang } from "@/lib/evaluation-labels";
import type { EvalClaim } from "@/lib/types-modules";

function ClaimRow({
  claim,
  defaultOpen,
  lang,
}: {
  claim: EvalClaim;
  defaultOpen: boolean;
  lang: Lang;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const t = (zh: string, en: string) => (lang === "en" ? en : zh);
  return (
    <li className="rounded-[8px] border border-border-light bg-surface">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full cursor-pointer items-start gap-1.5 px-2.5 py-2 text-left"
      >
        {open ? (
          <ChevronDown size={13} className="mt-0.5 shrink-0 text-muted" />
        ) : (
          <ChevronRight size={13} className="mt-0.5 shrink-0 text-muted" />
        )}
        <span className="min-w-0 flex-1">
          <span className="block text-xs leading-relaxed text-fg">{claim.statement}</span>
          <span className="mt-1 flex flex-wrap items-center gap-1.5">
            <Badge tone={claimTone(claim.status)}>
              {et(lang, `eval.claim.${claim.status || "unobserved"}`)}
            </Badge>
            {claim.assistance_scope && (
              <Badge tone="outline">{claim.assistance_scope}</Badge>
            )}
          </span>
        </span>
      </button>
      {open && (
        <div className="space-y-1.5 border-t border-border-light px-3 py-2 text-[0.7rem] leading-relaxed text-fg-secondary">
          {claim.limits?.length > 0 && (
            <div>
              <p className="mb-0.5 font-medium text-fg-secondary">
                {t("还不能说明：", "Not yet shown:")}
              </p>
              <ul className="space-y-0.5">
                {claim.limits.map((l, i) => (
                  <li key={i}>· {l}</li>
                ))}
              </ul>
            </div>
          )}
          {(claim.support_refs?.length > 0 || claim.challenge_refs?.length > 0) && (
            <div className="flex flex-wrap items-center gap-1.5">
              {claim.support_refs.map((ref) => (
                <Badge key={ref} tone="success">
                  {t("依据", "Evidence")} {ref}
                </Badge>
              ))}
              {claim.challenge_refs.map((ref) => (
                <Badge key={ref} tone="danger">
                  {t("反例", "Counter")} {ref}
                </Badge>
              ))}
            </div>
          )}
        </div>
      )}
    </li>
  );
}

/** 主张列表：supported/challenged 默认展开首条，其余折叠。 */
export function ClaimList({
  claims,
  lang,
  emptyText,
}: {
  claims: EvalClaim[];
  lang: Lang;
  emptyText?: string;
}) {
  const t = (zh: string, en: string) => (lang === "en" ? en : zh);
  if (!claims || claims.length === 0) {
    return (
      <p className="text-[0.72rem] text-muted">
        {emptyText || t("尚无具体主张，需要新的学习证据。", "No specific claims yet.")}
      </p>
    );
  }
  const firstOpen = claims.findIndex(
    (c) => c.status === "supported" || c.status === "challenged",
  );
  return (
    <ul className="space-y-1.5" data-testid="claim-list">
      {claims.map((c, i) => (
        <ClaimRow
          key={c.claim_id}
          claim={c}
          defaultOpen={i === (firstOpen === -1 ? 0 : firstOpen)}
          lang={lang}
        />
      ))}
    </ul>
  );
}
