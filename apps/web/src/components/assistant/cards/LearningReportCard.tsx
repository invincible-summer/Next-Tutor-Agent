"use client";

// 近期学习报告卡（A12）。
// 五段固定结构：范围/日期/截至 → 学习事实 → 已有学习评价 → 需要关注 →
// 下一步 → 依据与限制。数字与主张全部来自服务端报告，不在前端计算。
import { useAssistantStore } from "@/lib/assistant/store";
import type { LearningReport } from "@/lib/assistant/types.generated";

export function LearningReportCard({ report }: { report: LearningReport }) {
  const lang = useAssistantStore((s) => s.lang);
  const zh = lang === "zh";
  const facts = report.facts ?? [];
  const summaries = report.workspace_summaries ?? [];
  const notices = report.notices ?? [];
  const steps = report.next_steps ?? [];

  const scopeLabel = report.scope.mode === "workspace"
    ? (zh ? "指定工作区" : "Workspace")
    : report.scope.mode === "account"
      ? (zh ? "账号" : "Account")
      : (zh ? "全部工作区" : "All workspaces");
  const hasFacts = facts.some((f) => f.value != null && f.value !== 0);
  const attention: string[] = [];
  for (const ws of summaries) {
    const pending = ws.pending_counts?.evaluation_pending ?? 0;
    const reviews = ws.pending_counts?.active_reviews ?? 0;
    const failed = ws.pending_counts?.failed_jobs ?? 0;
    if (pending) {
      attention.push(zh
        ? `「${ws.workspace_name}」有 ${pending} 条新作答已完成判定、评价仍在处理`
        : `${ws.workspace_name}: ${pending} new answer(s) awaiting evaluation`);
    }
    if (reviews) {
      attention.push(zh
        ? `「${ws.workspace_name}」有 ${reviews} 条证据处于复核中，相关旧结论暂不作为推荐依据`
        : `${ws.workspace_name}: ${reviews} evidence under review`);
    }
    if (failed) {
      attention.push(zh
        ? `「${ws.workspace_name}」有 ${failed} 条评价作业失败`
        : `${ws.workspace_name}: ${failed} failed evaluation job(s)`);
    }
  }

  return (
    <div className="assistant-card assistant-report-card" data-testid="assistant-learning-report">
      <div className="assistant-report-head">
        <span className="assistant-report-title">
          {zh ? "近期学习报告" : "Learning report"}
        </span>
        <span className="assistant-report-scope">
          {zh ? `范围：${scopeLabel}` : `Scope: ${scopeLabel}`}
        </span>
      </div>
      <p className="assistant-report-window">
        {report.window.label} · {String(report.window.start_at).slice(0, 10)}
        {" → "}
        {String(report.window.end_at).slice(0, 10)}（{report.window.timezone}）
      </p>

      {!hasFacts ? (
        <p className="assistant-report-empty">
          {zh ? "目前没有足够记录。" : "Not enough records yet."}
        </p>
      ) : (
        <dl className="assistant-report-facts">
          {facts.map((fact) => (
            <div key={fact.key} className="assistant-report-fact">
              <dt>{fact.label}</dt>
              <dd>
                {fact.value == null
                  ? (zh ? "暂不可用" : "n/a")
                  : `${fact.value}${fact.unit ? ` ${fact.unit}` : ""}`}
                {fact.completeness === "partial"
                  && fact.known_minimum != null && (
                  <span className="assistant-report-min">
                    {zh ? `（至少 ${fact.known_minimum}）`
                      : ` (≥${fact.known_minimum})`}
                  </span>
                )}
              </dd>
            </div>
          ))}
        </dl>
      )}

      {summaries.length > 0 && (
        <div className="assistant-report-section">
          <h4>{zh ? "已有学习评价" : "Existing evaluation"}</h4>
          {summaries.map((ws) => (
            <div key={ws.workspace_id} className="assistant-report-ws">
              <span className="assistant-report-ws-name">
                {ws.workspace_name}
              </span>
              {ws.coverage?.observed_concepts != null && (
                <span className="assistant-report-ws-meta">
                  {zh ? `观测概念 ${ws.coverage.observed_concepts}`
                    : `${ws.coverage.observed_concepts} observed`}
                </span>
              )}
              {(ws.statements ?? []).map((st, i) => (
                <p key={i} className="assistant-report-statement">{st.text}</p>
              ))}
              {(ws.statements ?? []).length === 0 && (
                <p className="assistant-report-statement muted">
                  {zh ? "暂无可追溯主张。" : "No traceable claims yet."}
                </p>
              )}
            </div>
          ))}
        </div>
      )}

      {(attention.length > 0 || notices.length > 0) && (
        <div className="assistant-report-section">
          <h4>{zh ? "需要关注" : "Attention"}</h4>
          <ul>
            {attention.slice(0, 4).map((line, i) => (
              <li key={i}>{line}</li>
            ))}
            {notices.slice(0, 3).map((n, i) => (
              <li key={`n${i}`} className="muted">{n.message}</li>
            ))}
          </ul>
        </div>
      )}

      {steps.length > 0 && (
        <div className="assistant-report-section">
          <h4>{zh ? "下一步" : "Next steps"}</h4>
          <ul>
            {steps.map((step, i) => (
              <li key={i}>
                <span className="assistant-report-step">{step.title}</span>
                <span className="assistant-report-why">{step.rationale}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="assistant-report-foot">
        {zh
          ? `数据截至 ${String(report.generated_at).slice(0, 19).replace("T", " ")}`
          : `As of ${String(report.generated_at).slice(0, 19).replace("T", " ")}`}
        {!report.complete && (zh ? " · 部分数据缺失" : " · partial data")}
        {report.unscoped_activity?.included && (
          zh ? " · 含未绑定工作区记录" : " · includes unscoped records"
        )}
      </p>
    </div>
  );
}
