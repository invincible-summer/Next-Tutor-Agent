"use client";
// LLM 运行参数面板（「运行参数」页签）：上下文预算 / 生成参数 / 出题校验
// 模式。保存即写 chat_history/settings/llm_policy.json 并热更新——下一次
// LLM 调用生效，无需重启。每个字段带问号悬浮提示。
import { useEffect, useState } from "react";
import { Check, Save, SlidersHorizontal } from "lucide-react";
import { getAdminLLMRunPolicy, setAdminLLMRunPolicy, type AdminLLMRunPolicy } from "@/lib/api";
import { Card, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Field, inputCls, type Tr } from "./Field";

export function LLMRunPolicyPanel({ tr }: { tr: Tr }) {
  const [busy, setBusy] = useState(false);
  const [applied, setApplied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [policy, setPolicy] = useState<AdminLLMRunPolicy | null>(null);
  const [contextWindow, setContextWindow] = useState(165536);
  const [maxOutputTokens, setMaxOutputTokens] = useState(20000);
  const [temperature, setTemperature] = useState(0.3);
  const [agentMaxSteps, setAgentMaxSteps] = useState(6);
  const [llmMaxTokens, setLlmMaxTokens] = useState(4000);
  const [verifyMode, setVerifyMode] = useState<AdminLLMRunPolicy["quiz_verify_mode"]>("critic");

  useEffect(() => {
    getAdminLLMRunPolicy().then((p) => {
      setPolicy(p);
      setContextWindow(p.context_window);
      setMaxOutputTokens(p.max_output_tokens);
      setTemperature(p.temperature);
      setAgentMaxSteps(p.agent_max_steps);
      setLlmMaxTokens(p.llm_max_tokens);
      setVerifyMode(p.quiz_verify_mode);
    }).catch(() => setError(tr("adm.llm.loadFail")));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const apply = async () => {
    setBusy(true);
    setError(null);
    try {
      const p = await setAdminLLMRunPolicy({
        context_window: contextWindow,
        max_output_tokens: maxOutputTokens,
        temperature,
        agent_max_steps: agentMaxSteps,
        llm_max_tokens: llmMaxTokens,
        quiz_verify_mode: verifyMode,
      });
      setPolicy(p);
      setApplied(true);
      setTimeout(() => setApplied(false), 1800);
    } catch {
      setError(tr("adm.llm.applyFail"));
    } finally {
      setBusy(false);
    }
  };

  const verifyModeLabels: Record<AdminLLMRunPolicy["quiz_verify_mode"], string> = {
    critic: tr("adm.llm.verify.critic"),
    basic: tr("adm.llm.verify.basic"),
    off: tr("adm.llm.verify.off"),
  };

  return (
    <Card>
      <CardHeader
        icon={<SlidersHorizontal size={16} />}
        title={tr("adm.llm.title")}
        desc={tr("adm.llm.desc")}
      />
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        <Field label={tr("adm.llm.contextWindow")} hint={tr("adm.llm.contextWindow.hint")}>
          <input type="number" step={1024}
            min={policy?.min_context_window ?? 8192}
            max={policy?.max_context_window ?? 1000000} value={contextWindow}
            onChange={(e) => setContextWindow(Number(e.target.value))}
            className={inputCls} />
        </Field>
        <Field label={tr("adm.llm.maxOutputTokens")} hint={tr("adm.llm.maxOutputTokens.hint")}>
          <input type="number" step={512}
            min={policy?.min_max_output_tokens ?? 1024}
            max={policy?.max_max_output_tokens ?? 200000} value={maxOutputTokens}
            onChange={(e) => setMaxOutputTokens(Number(e.target.value))}
            className={inputCls} />
        </Field>
        <Field label={tr("adm.llm.temperature")} hint={tr("adm.llm.temperature.hint")}>
          <input type="number" step={0.05}
            min={policy?.min_temperature ?? 0}
            max={policy?.max_temperature ?? 2} value={temperature}
            onChange={(e) => setTemperature(Number(e.target.value))}
            className={inputCls} />
        </Field>
        <Field label={tr("adm.llm.agentMaxSteps")} hint={tr("adm.llm.agentMaxSteps.hint")}>
          <input type="number"
            min={policy?.min_agent_max_steps ?? 1}
            max={policy?.max_agent_max_steps ?? 30} value={agentMaxSteps}
            onChange={(e) => setAgentMaxSteps(Number(e.target.value))}
            className={inputCls} />
        </Field>
        <Field label={tr("adm.llm.llmMaxTokens")} hint={tr("adm.llm.llmMaxTokens.hint")}>
          <input type="number" step={256}
            min={policy?.min_llm_max_tokens ?? 512}
            max={policy?.max_llm_max_tokens ?? 100000} value={llmMaxTokens}
            onChange={(e) => setLlmMaxTokens(Number(e.target.value))}
            className={inputCls} />
        </Field>
        <Field label={tr("adm.llm.verifyMode")} hint={tr("adm.llm.verifyMode.hint")}>
          <select value={verifyMode}
            onChange={(e) => setVerifyMode(e.target.value as AdminLLMRunPolicy["quiz_verify_mode"])}
            className={inputCls}>
            <option value="critic">{verifyModeLabels.critic}</option>
            <option value="basic">{verifyModeLabels.basic}</option>
            <option value="off">{verifyModeLabels.off}</option>
          </select>
        </Field>
      </div>
      {error && <p className="mt-2 text-xs text-danger">{error}</p>}
      <div className="mt-3 flex items-center justify-between">
        <span className="text-[0.65rem] text-muted">
          {policy?.updated_at
            ? `${tr("adm.llm.updatedAt")}: ${new Date(policy.updated_at * 1000).toLocaleString()}`
            : ""}
        </span>
        <Button size="sm" disabled={busy} onClick={() => void apply()}
          icon={applied ? <Check size={13} /> : <Save size={13} />}>
          {applied ? tr("adm.llm.applied") : tr("adm.llm.apply")}
        </Button>
      </div>
    </Card>
  );
}
