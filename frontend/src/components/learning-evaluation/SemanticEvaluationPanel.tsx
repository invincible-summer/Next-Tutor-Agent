"use client";
// 概念详情统一组件（plan §14.3）：同一面板供 Memory 概念区、知识图谱
// ConceptDrawer 使用；judgment_id 与状态映射全站一致（evaluation-labels）。
// 类别不是等级：无数值、无进度条；帮助/任务变化用行为语言呈现。
import { useCallback, useEffect, useState } from "react";
import { BookOpen, Compass, Map } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Tabs } from "@/components/ui/Tabs";
import { EmptyState } from "@/components/ui/EmptyState";
import { ClaimList } from "./ClaimList";
import { LearningChange } from "./LearningChange";
import { NextProbeAction } from "./NextProbeAction";
import { EvidenceTimeline } from "./EvidenceTimeline";
import { StatusNote } from "./StatusNote";
import { et, evalStateTone, type Lang } from "@/lib/evaluation-labels";
import { getEvalEvidence } from "@/lib/api-modules";
import type {
  ConceptEvaluationView,
  EvalNextProbe,
  EvalSourceTimelineItem,
} from "@/lib/types-modules";

function fmtIso(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function SemanticEvaluationPanel({
  view,
  lang,
  workspaceId = "",
  prerequisites,
  onStartProbe,
  onExplain,
  onViewInGraph,
}: {
  view: ConceptEvaluationView;
  lang: Lang;
  /** 提供时启用证据时间线（按 concept_key 过滤）。 */
  workspaceId?: string;
  /** 相关前置（容器有图谱数据时传入；面板不自行查图）。 */
  prerequisites?: { id: string; name: string }[];
  /** [开始下一验证]：next_probe 是可选行动，由容器决定如何启动。 */
  onStartProbe?: (probe: EvalNextProbe) => void;
  /** [先看讲解]。 */
  onExplain?: () => void;
  /** [在图谱中查看]。 */
  onViewInGraph?: (conceptId: string) => void;
}) {
  const t = (zh: string, en: string) => (lang === "en" ? en : zh);
  const [tab, setTab] = useState<string>(workspaceId ? "evidence" : "prereq");
  const [evidence, setEvidence] = useState<EvalSourceTimelineItem[] | null>(null);
  const [evidenceError, setEvidenceError] = useState(false);

  const conceptKey = view.concept_ref.key;
  const loadEvidence = useCallback(() => {
    if (!workspaceId || !conceptKey) return;
    getEvalEvidence(workspaceId, { conceptKey, limit: 50 })
      .then((r) => {
        setEvidence(r.items || []);
        setEvidenceError(false);
      })
      .catch(() => setEvidenceError(true));
  }, [workspaceId, conceptKey]);

  useEffect(() => {
    if (tab === "evidence" && evidence === null && !evidenceError) loadEvidence();
  }, [tab, evidence, evidenceError, loadEvidence]);

  const state = view.state || "not_observed";
  const displayName = view.concept_ref.display_name || view.concept_ref.concept_id;
  const supportedClaims = view.claims.filter(
    (c) => c.status === "supported" || c.status === "tentative",
  );
  const limits = view.claims.flatMap((c) =>
    c.status !== "challenged" ? c.limits.map((l) => ({ key: `${c.claim_id}:${l}`, l })) : [],
  );
  const challenged = view.claims.filter((c) => c.status === "challenged");

  const probeActions =
    onStartProbe || onExplain ? (
      <>
        {onStartProbe && view.next_probe && (
          <Button size="sm" onClick={() => onStartProbe(view.next_probe!)}>
            <Compass size={12} className="mr-1" />
            {t("开始下一验证", "Start next check")}
          </Button>
        )}
        {onExplain && (
          <Button size="sm" variant="outline" onClick={onExplain}>
            <BookOpen size={12} className="mr-1" />
            {t("先看讲解", "Explanation first")}
          </Button>
        )}
      </>
    ) : undefined;

  return (
    <div className="space-y-3" data-testid="semantic-evaluation-panel">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="font-serif text-base font-semibold text-fg">{displayName}</h3>
          <p className="mt-0.5 text-[0.7rem] text-muted">
            {view.concept_ref.textbook_id}
            {view.updated_at ? ` · ${t("最后表现", "Last seen")} ${fmtIso(view.updated_at)}` : ""}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <Badge tone={evalStateTone(state)}>{et(lang, `eval.state.${state}`)}</Badge>
          <StatusNote status={view.evaluation_status} lang={lang} />
        </div>
      </header>

      {state === "not_observed" && !view.claims.length ? (
        <EmptyState
          title={et(lang, "eval.state.not_observed")}
          desc={t(
            "完成一次讲解、练习或对话后，这里会出现有条件的学习结论。",
            "After an explanation, practice, or dialogue, conditional conclusions appear here.",
          )}
        />
      ) : (
        <>
          {supportedClaims.length > 0 && (
            <section>
              <p className="mb-1.5 text-[0.7rem] font-medium text-fg-secondary">
                {t("当前可以说明", "Can currently show")}
              </p>
              <ClaimList claims={supportedClaims} lang={lang} />
            </section>
          )}
          {(limits.length > 0 || challenged.length > 0) && (
            <section>
              <p className="mb-1.5 text-[0.7rem] font-medium text-fg-secondary">
                {t("目前还不能说明", "Not yet shown")}
              </p>
              <ul className="space-y-0.5">
                {limits.slice(0, 6).map(({ key, l }) => (
                  <li key={key} className="text-xs leading-relaxed text-fg-secondary">· {l}</li>
                ))}
                {challenged.map((c) => (
                  <li key={c.claim_id} className="text-xs leading-relaxed text-fg-secondary">
                    · <Badge tone="danger" className="mr-1">{et(lang, "eval.claim.challenged")}</Badge>
                    {c.statement}
                  </li>
                ))}
              </ul>
            </section>
          )}
          {view.statement && state !== "not_observed" && (
            <p className="rounded-[8px] bg-surface-hover/50 px-3 py-2 text-xs leading-relaxed text-fg-secondary">
              {view.statement}
            </p>
          )}
          <LearningChange change={view.change} lang={lang} />
          <NextProbeAction probe={view.next_probe} lang={lang} actions={probeActions} />
        </>
      )}

      {(workspaceId || prerequisites) && (
        <>
          <Tabs
            active={tab}
            onChange={(k) => {
              setTab(k);
              if (k === "evidence" && evidence === null) loadEvidence();
            }}
            items={[
              ...(workspaceId
                ? [{ key: "evidence", label: t("证据时间线", "Evidence timeline") }]
                : []),
              ...(prerequisites
                ? [{ key: "prereq", label: t("相关前置", "Prerequisites") }]
                : []),
            ]}
          />
          {tab === "evidence" && workspaceId && (
            <>
              {evidence === null && !evidenceError ? (
                <p className="py-2 text-xs text-muted">…</p>
              ) : evidenceError ? (
                <EmptyState
                  title={t("证据加载失败", "Failed to load evidence")}
                  action={
                    <Button size="sm" onClick={loadEvidence}>
                      {t("重试", "Retry")}
                    </Button>
                  }
                />
              ) : (
                <EvidenceTimeline
                  items={evidence || []}
                  lang={lang}
                  conceptNames={() => displayName}
                />
              )}
            </>
          )}
          {tab === "prereq" && prerequisites && (
            <ul className="space-y-1">
              {prerequisites.length === 0 ? (
                <li className="text-[0.72rem] text-muted">
                  {t("暂无相关前置概念。", "No prerequisite concepts.")}
                </li>
              ) : (
                prerequisites.map((p) => (
                  <li key={p.id} className="flex items-center gap-2 py-0.5">
                    <Badge tone="outline">{t("前置", "Prereq")}</Badge>
                    <span className="min-w-0 flex-1 truncate text-xs text-fg">{p.name}</span>
                  </li>
                ))
              )}
            </ul>
          )}
        </>
      )}

      <div className="flex flex-wrap items-center gap-2 border-t border-border-light pt-3">
        {onViewInGraph && (
          <Button size="sm" variant="ghost" onClick={() => onViewInGraph(view.concept_ref.concept_id)}>
            <Map size={12} className="mr-1" />
            {t("在图谱中查看", "View in graph")}
          </Button>
        )}
      </div>
    </div>
  );
}
