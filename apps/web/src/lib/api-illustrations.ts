import { API_BASE } from "./api";
import { apiFetch } from "./api-fetch";
import type { QuestionIllustrationData } from "./types";

export type VisualRole = "none" | "supplemental" | "essential";
export interface IllustrationJob {
  status: "queued" | "running" | "ready" | "not_required" | "failed";
  question_id: string;
  question_revision: number;
  visual_role?: VisualRole;
  job_id?: string;
  artifact_id?: string | null;
  illustration?: QuestionIllustrationData | null;
  failure?: { code: string; retryable: boolean } | null;
  code?: string;
  retryable?: boolean;
  progress?: { stage: string; percent: number };
}

async function request(path: string, init?: RequestInit): Promise<IllustrationJob> {
  const response = await apiFetch(`${API_BASE}${path}`, init);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(String(payload?.detail?.error?.code || payload?.error?.code || `status_${response.status}`));
  return payload as IllustrationJob;
}

export function startIllustration(questionId: string, revision: number) {
  return request(`/assessment/questions/${encodeURIComponent(questionId)}/illustration`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question_revision: revision }),
  });
}
export function getIllustrationJob(jobId: string) {
  return request(`/illustration-jobs/${encodeURIComponent(jobId)}`);
}
export function retryIllustrationJob(jobId: string) {
  return request(`/illustration-jobs/${encodeURIComponent(jobId)}/retry`, { method: "POST" });
}
export function getFrozenIllustration(questionId: string, revision: number) {
  return request(`/questions/${encodeURIComponent(questionId)}/illustration?question_revision=${revision}`);
}

export function illustrationStage(stage: string, english: boolean): string {
  const labels: Record<string, [string, string]> = {
    preparation: ["准备条件", "Preparing material"], retrieval: ["检索素材", "Finding components"],
    composition: ["设计图面", "Composing diagram"], review: ["检查图文", "Reviewing material"], complete: ["完成", "Complete"],
  };
  return labels[stage]?.[english ? 1 : 0] ?? (english ? "Designing diagram" : "题图设计中");
}

export function illustrationFailure(code: string, english: boolean): string {
  const labels: Record<string, [string, string]> = {
    candidate_not_found: ["素材库缺少合适素材", "No suitable components"],
    missing_fact_binding: ["图中参数缺少题目依据", "Missing material conditions"],
    question_material_incomplete: ["题目材料不完整，需要修订题目", "Question material needs revision"],
    provider_unavailable: ["图像审查模型暂不可用", "Visual reviewer unavailable"],
    preview_unavailable: ["图面预览暂不可用", "Diagram preview unavailable"],
    visual_review_failed: ["题图未通过审查", "Diagram did not pass review"],
    budget_exhausted: ["配图超出本次时间或调用预算", "Diagram generation timed out"],
    policy_disabled: ["题图生成已关闭", "Diagram generation disabled"],
    run_interrupted: ["配图任务中断，可重试", "Diagram job interrupted"],
  };
  return labels[code]?.[english ? 1 : 0] ?? (english ? "The diagram could not be completed" : "本次配图未完成");
}
