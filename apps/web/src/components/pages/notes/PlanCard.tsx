"use client";
// 计划卡：plan 模式下智能体产出的结构化修改计划（数据来自服务端 pending_plan
// 状态机，而非对最后一条消息的启发式猜测）。状态徽标随批复流转：
// pending 待批复 → approved 执行中 → executed 已执行 / rejected 已驳回。
// 批复动作在输入框上方的批复条（AIPanel），本卡只读展示。
import { ClipboardList } from "lucide-react";
import { cn } from "@/lib/cn";
import type { AgentPlan } from "@/lib/types-notes";

// 计划卡的 JSON 围栏块只服务于传输/解析（后端 _PLAN_JSON_RE 取最后一个
// ```json 块），卡片本体已由 PlanCard 结构化呈现；消息正文里再展示一遍
// 原始 JSON 会破坏聊天观感，渲染时统一剥掉。流式期间 JSON 尚未闭合，
// 同样从 "```json" 起截断，避免半截 JSON 闪现。
export function stripPlanCardJson(text: string): string {
  let out = String(text ?? "");
  const closed = /```json\s*\{[\s\S]*?\}\s*```\s*$/g;
  let m: RegExpExecArray | null;
  let last: RegExpExecArray | null = null;
  while ((m = closed.exec(out)) !== null) last = m;
  if (last && last[0].includes('"steps"')) out = out.slice(0, last.index);
  const open = out.lastIndexOf("```json");
  if (open >= 0 && !out.slice(open + 7).includes("```")) out = out.slice(0, open);
  return out.replace(/\s+$/, "");
}

const STATUS_BADGE: Record<AgentPlan["status"], string> = {
  pending: "border-accent/40 bg-accent-soft text-accent-strong",
  approved: "border-accent/40 bg-accent-soft text-accent-strong",
  executed: "border-success/40 bg-success/10 text-success",
  rejected: "border-border bg-surface-sunken text-muted",
};

export function PlanCard({
  plan, tr,
}: {
  plan: AgentPlan;
  tr: (k: string, fallback?: string) => string;
}) {
  return (
    <div className="my-2 w-full rounded-xl border border-accent/25 bg-accent-soft/40 p-3">
      <div className="flex items-center gap-2">
        <ClipboardList size={13} className="shrink-0 text-accent-strong" />
        <span className="min-w-0 flex-1 truncate text-xs font-medium text-accent-strong">
          {plan.title || tr("ai.plan.card")}
        </span>
        <span className={cn(
          "shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-medium",
          STATUS_BADGE[plan.status] ?? STATUS_BADGE.pending,
        )}>
          {tr(`ai.plan.status.${plan.status}`, plan.status)}
        </span>
      </div>
      {plan.steps.length > 0 && (
        <ol className="mt-2 space-y-1.5">
          {plan.steps.map((step, i) => (
            <li key={`${step.title}-${i}`} className="flex gap-2 text-[11px] leading-relaxed text-fg-secondary">
              <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-surface-sunken text-[9px] font-semibold text-muted">
                {i + 1}
              </span>
              <span className="min-w-0">
                <span className="font-medium text-fg">{step.title}</span>
                {step.detail ? <span>：{step.detail}</span> : null}
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
