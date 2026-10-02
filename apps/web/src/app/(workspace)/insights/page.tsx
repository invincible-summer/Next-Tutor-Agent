"use client";
import { navigationAnchor, navigationSucceeded } from "@/lib/assistant/navigation";


// /insights 系统洞察：M7 评估与改进智能的观察与人工确认入口。
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import {
  DeepLinkQueryReader, deepParam, focusDeepTarget,
} from "@/lib/assistant/deep-link";
import { useAssistantPage } from "@/lib/assistant/useAssistantPage";
import { currentRouteEpoch } from "@/lib/assistant/page-context";
import { ModuleBadge } from "@/components/ui/Badge";
import { EmptyState, ErrorNote, PageSkeleton } from "@/components/ui/EmptyState";
import { getContextBudgetReport, getEvalGuidance, getEvalProposals, getEvalReport, getEvalTraces } from "@/lib/api-modules";
import { makePageT } from "@/lib/i18n-page";
import { useUIStore } from "@/lib/store";
import type { ContextBudgetReport, EvalGuidanceEntry, EvalProposal, EvalReport, EvalTrace } from "@/lib/types-modules";
import { DiagnosisCharts } from "@/components/pages/insights/DiagnosisCharts";
import { GuidancePanel } from "@/components/pages/insights/GuidancePanel";
import { OverviewStats } from "@/components/pages/insights/OverviewStats";
import { ProposalsList } from "@/components/pages/insights/ProposalsList";
import { TracesTable } from "@/components/pages/insights/TracesTable";
import { ContextBudgetPanel } from "@/components/pages/insights/ContextBudgetPanel";
import { STRINGS } from "./strings";

export default function InsightsPage() {
  const lang = useUIStore((s) => s.lang);
  const tr = makePageT(lang, STRINGS);

  const [report, setReport] = useState<EvalReport | null>(null);
  const [traces, setTraces] = useState<EvalTrace[]>([]);
  const [proposals, setProposals] = useState<EvalProposal[]>([]);
  const [guidance, setGuidance] = useState<EvalGuidanceEntry[]>([]);
  const [contextBudget, setContextBudget] = useState<ContextBudgetReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useAssistantPage({
    navigationStatus: (target) => {
      if (loading) return null;
      if (target.kind === "module") return navigationSucceeded;
      if (target.kind === "teaching_proposal") return navigationAnchor("proposal-id", target.proposal_id);
      if (target.kind === "teaching_guidance") return navigationAnchor("guidance-id", target.guidance_id);
      return null;
    },
    context: () => ({
      schema_version: 1,
      route_id: "insights",
      route_epoch: currentRouteEpoch(),
    }),
  });
  // §20.1 系统洞察：?proposal= 与 ?guidance= 双深链（同页变化均响应）。
  const [deepFocus, setDeepFocus] = useState({ proposal: "", guidance: "" });
  const applyDeepLink = useCallback((params: Record<string, string>) => {
    setDeepFocus({ proposal: params.proposal || "", guidance: params.guidance || "" });
  }, []);

  const fetchAll = useCallback(async () => {
    const [r, t, p, g, c] = await Promise.all([getEvalReport(), getEvalTraces(50), getEvalProposals(), getEvalGuidance(), getContextBudgetReport()]);
    setReport(r);
    setTraces(Array.isArray(t) ? t : []);
    setProposals(Array.isArray(p) ? p : []);
    setGuidance(Array.isArray(g) ? g : []);
    setContextBudget(c);
  }, []);

  // 重试入口（事件处理器中调用，可以同步 set loading）。
  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      await fetchAll();
    } catch {
      setError(tr("ins.error.load"));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- tr 随 lang 变化时无需重新拉取
  }, [fetchAll]);

  useEffect(() => {
    // 初次拉取：setState 只在 Promise 回调里发生（lint: set-state-in-effect）。
    let alive = true;
    Promise.all([getEvalReport(), getEvalTraces(50), getEvalProposals(), getEvalGuidance(), getContextBudgetReport()])
      .then(([r, t, p, g, c]) => {
        if (!alive) return;
        setReport(r);
        setTraces(Array.isArray(t) ? t : []);
        setProposals(Array.isArray(p) ? p : []);
        setGuidance(Array.isArray(g) ? g : []);
        setContextBudget(c);
      })
      .catch(() => {
        if (alive) setError(tr("ins.error.load"));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 仅挂载时拉取一次
  }, []);

  // §8.3 助手深链 ?proposal= / ?guidance=：数据加载后定位高亮（高亮不等于
  // 批准/应用）；同页 query 变化同样响应；找不到给温和提示。
  const [deepProposalMissing, setDeepProposalMissing] = useState(false);
  const [deepGuidanceMissing, setDeepGuidanceMissing] = useState(false);
  const deepProposalLast = useRef("");
  const deepGuidanceLast = useRef("");
  useEffect(() => {
    if (loading) return;
    const proposal = deepFocus.proposal
      || (deepProposalLast.current ? "" : deepParam("proposal"));
    if (proposal && deepProposalLast.current !== proposal) {
      deepProposalLast.current = proposal;
      const found = focusDeepTarget("proposal-id", proposal);
      if (!found) void Promise.resolve().then(() => setDeepProposalMissing(true));
    }
    const guidanceId = deepFocus.guidance
      || (deepGuidanceLast.current ? "" : deepParam("guidance"));
    if (guidanceId && deepGuidanceLast.current !== guidanceId) {
      deepGuidanceLast.current = guidanceId;
      const found = focusDeepTarget("guidance-id", guidanceId);
      if (!found) void Promise.resolve().then(() => setDeepGuidanceMissing(true));
    }
  }, [loading, proposals, guidance, deepFocus]);

  // 后端智能层被环境开关关闭时，端点会返回 { status: "disabled" }。
  const disabled =
    report != null && (report as unknown as { status?: string }).status === "disabled";

  return (
    <div className="h-full overflow-y-auto p-6 page-in">
      <Suspense><DeepLinkQueryReader keys={["proposal", "guidance"]} onParams={applyDeepLink} /></Suspense>
      <div className="mx-auto flex max-w-[1200px] flex-col gap-4">
        <header>
          <div className="flex items-center gap-2.5">
            <h1 className="font-serif text-xl font-bold tracking-tight text-fg">
              {tr("ins.title")}
            </h1>
            <ModuleBadge id="M7" />
          </div>
          <p className="mt-1 text-xs text-muted">{tr("ins.subtitle")}</p>
        </header>

        <div className="flex items-start gap-2.5 rounded-[10px] border border-accent/30 bg-accent-soft px-3.5 py-2.5">
          <svg
            viewBox="0 0 16 16"
            className="mt-0.5 h-4 w-4 shrink-0 text-accent"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
          >
            <circle cx="8" cy="8" r="6.25" />
            <path d="M8 7.2v3.3M8 5.1v.1" strokeLinecap="round" />
          </svg>
          <p className="text-xs leading-relaxed text-accent-strong">{tr("ins.observer")}</p>
        </div>

        {error && <ErrorNote message={error} retry={load} />}

        {loading ? (
          <PageSkeleton />
        ) : disabled ? (
          <EmptyState title={tr("ins.disabled")} />
        ) : report ? (
          <>
            <OverviewStats report={report} tr={tr} />
            {contextBudget && <ContextBudgetPanel data={contextBudget} tr={tr} />}
            <DiagnosisCharts report={report} tr={tr} lang={lang} />
            {deepProposalMissing && (
              <p className="text-xs text-muted" role="status">
                {tr("ins.deep.proposalMissing")}
              </p>
            )}
            {deepGuidanceMissing && (
              <p className="text-xs text-muted" role="status">
                {tr("ins.deep.guidanceMissing")}
              </p>
            )}
            <ProposalsList deepProposalId={deepFocus.proposal} proposals={proposals} tr={tr} onChanged={load} />
            <GuidancePanel entries={guidance} tr={tr} onChanged={load} />
            <TracesTable traces={traces} tr={tr} lang={lang} />
          </>
        ) : (
          <EmptyState title={tr("ins.traces.empty")} />
        )}
      </div>
    </div>
  );
}
