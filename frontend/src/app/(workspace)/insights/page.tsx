"use client";

// /insights 系统洞察：M7 评估与改进智能的观察与人工确认入口。
import { useCallback, useEffect, useState } from "react";
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

  // 后端智能层被环境开关关闭时，端点会返回 { status: "disabled" }。
  const disabled =
    report != null && (report as unknown as { status?: string }).status === "disabled";

  return (
    <div className="h-full overflow-y-auto p-6 page-in">
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
            <ProposalsList proposals={proposals} tr={tr} onChanged={load} />
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
