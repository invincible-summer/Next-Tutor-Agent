"use client";

// AI 教学效果报告卡（A12）。
// 标题固定「AI 教学效果」、范围固定「与你的教学交互」（账号级）；
// 最多 3 项有记录的现象；样本 <5 只列现象不排名；提案状态如实展示，
// approved ≠ 已生效；impact_turns 只表示经过的教学轮次。
import { useAssistantStore } from "@/lib/assistant/store";
import type { TeachingReport } from "@/lib/assistant/types.generated";

const PROPOSAL_LABEL_ZH: Record<string, string> = {
  proposed: "待审批", approved: "已批准", applied: "已应用", rejected: "已拒绝",
};
const PROPOSAL_LABEL_EN: Record<string, string> = {
  proposed: "Proposed", approved: "Approved", applied: "Applied", rejected: "Rejected",
};

export function TeachingReportCard({ report }: { report: TeachingReport }) {
  const lang = useAssistantStore((s) => s.lang);
  const zh = lang === "zh";
  const proposalTable = zh ? PROPOSAL_LABEL_ZH : PROPOSAL_LABEL_EN;
  const strategies = report.top_strategies ?? [];
  const proposals = report.proposals ?? [];
  const pendingProposals = report.pending_proposals ?? 0;
  const coverage = report.coverage;

  return (
    <div className="assistant-card assistant-report-card" data-testid="assistant-teaching-report">
      <div className="assistant-report-head">
        <span className="assistant-report-title">
          {zh ? "AI 教学效果" : "AI teaching effectiveness"}
        </span>
        <span className="assistant-report-scope">
          {zh ? "与你的教学交互" : "Your teaching interactions"}
        </span>
      </div>
      <p className="assistant-report-window">
        {report.window.label} · {String(report.window.start_at).slice(0, 10)}
        {" → "}
        {String(report.window.end_at).slice(0, 10)}
      </p>

      {report.total_turns === 0 ? (
        <p className="assistant-report-empty">
          {zh ? "与你的教学交互暂无足够记录，无法给出教学效果结论。"
            : "Not enough teaching records for a conclusion."}
        </p>
      ) : (
        <>
          <dl className="assistant-report-facts">
            <div className="assistant-report-fact">
              <dt>{zh ? "窗口内教学轮" : "Teaching turns"}</dt>
              <dd>{report.total_turns}</dd>
            </div>
            {pendingProposals > 0 && (
              <div className="assistant-report-fact">
                <dt>{zh ? "待审批提案" : "Pending proposals"}</dt>
                <dd>{pendingProposals}</dd>
              </div>
            )}
          </dl>

          {strategies.length > 0 && (
            <div className="assistant-report-section">
              <h4>{zh ? "有记录的现象" : "Recorded observations"}</h4>
              <ul>
                {strategies.slice(0, 3).map((s, i) => (
                  <li key={i}>
                    <span className="assistant-report-step">
                      {s.label || s.name}
                    </span>
                    <span className="assistant-report-why">
                      {zh
                        ? `尝试 ${s.attempts} 次、成功 ${s.successes} 次（样本 ${s.attempts} 条）`
                        : `${s.successes}/${s.attempts} (sample ${s.attempts})`}
                      {s.attempts < 5 && (zh ? "；样本少，仅列现象"
                        : "; small sample, observation only")}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {proposals.length > 0 && (
            <div className="assistant-report-section">
              <h4>{zh ? "教学改进提案" : "Improvement proposals"}</h4>
              <ul>
                {proposals.slice(0, 4).map((p) => (
                  <li key={p.proposal_id}>
                    <span className="assistant-report-step">
                      {p.title || p.proposal_id}
                    </span>
                    <span className="assistant-report-why">
                      {proposalTable[p.status] ?? p.status}
                      {p.status === "approved" && (zh ? "（尚未生效）"
                        : " (not yet applied)")}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      <p className="assistant-report-foot">
        {zh
          ? `数据截至 ${String(report.generated_at).slice(0, 19).replace("T", " ")}`
          : `As of ${String(report.generated_at).slice(0, 19).replace("T", " ")}`}
        {coverage && !coverage.complete
          && (zh ? ` · 已检 ${coverage.inspected_count} 条，其中 ${coverage.invalid_count} 条损坏`
            : ` · inspected ${coverage.inspected_count}, ${coverage.invalid_count} invalid`)}
      </p>
    </div>
  );
}
