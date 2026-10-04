// 题图（V1/V2/V3）读模型客户端：传输与错误映射在共享包
// @next-tutor/api-client（illustration 域），本文件保留 Web 的类型与
// 展示层辅助函数（阶段/失败文案），并把 typed 错误适配为 Web 既有错误类型。
import type { QuestionIllustrationData } from "./types";
import { IllustrationApiError } from "@next-tutor/api-client";
import { apiClient } from "@/platform/api-client";

export type VisualRole = "none" | "supplemental" | "essential";
export type IllustrationMode = "v1" | "v2" | "v3";

export function illustrationMode(value: unknown): IllustrationMode {
  return value === "v2" || value === "v3" ? value : "v1";
}
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

export class IllustrationRequestError extends Error {
  constructor(code: string, readonly retryable: boolean) {
    super(code);
    this.name = "IllustrationRequestError";
  }
}

function adapt(error: unknown): never {
  if (error instanceof IllustrationApiError) {
    throw new IllustrationRequestError(error.code, error.retryable);
  }
  throw error;
}

export function startIllustration(questionId: string, revision: number, signal?: AbortSignal) {
  return apiClient()
    .illustration.start(questionId, revision, { signal })
    .then((view) => view as IllustrationJob, adapt);
}
export function getIllustrationJob(jobId: string, signal?: AbortSignal) {
  return apiClient()
    .illustration.getJob(jobId, signal)
    .then((view) => view as IllustrationJob, adapt);
}
export function retryIllustrationJob(jobId: string, signal?: AbortSignal) {
  return apiClient()
    .illustration.retry(jobId, { signal })
    .then((view) => view as IllustrationJob, adapt);
}
export function getFrozenIllustration(questionId: string, revision: number, signal?: AbortSignal) {
  return apiClient()
    .illustration.frozen(questionId, revision, signal)
    .then((view) => view as IllustrationJob, adapt);
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
    invalid_contract: ["配图需求未形成有效说明", "Diagram requirements could not be validated"],
    missing_material: ["题图缺少必要材料", "Required diagram material is missing"],
    candidate_not_found: ["素材库缺少合适素材", "No suitable components"],
    candidate_capability_mismatch: ["素材功能不符合题目需求", "Components do not support the requested diagram"],
    scene_schema_invalid: ["题图结构未通过检查", "Diagram structure could not be validated"],
    scene_asset_not_authorized: ["题图引用了不可用素材", "A referenced asset is unavailable"],
    missing_fact_binding: ["图中参数缺少题目依据", "Missing material conditions"],
    parameter_unbound: ["图中参数尚未绑定题目条件", "Diagram parameters are not bound to question conditions"],
    geometry_out_of_bounds: ["题图超出画面边界", "Diagram extends beyond the canvas"],
    collision_unresolved: ["题图元素重叠尚未修正", "Diagram overlaps could not be resolved"],
    relation_unrealizable: ["题图中的连接关系尚未完成", "Diagram connections could not be completed"],
    text_not_legible: ["题图文字尚不清晰", "Diagram text is not legible"],
    question_material_incomplete: ["题目材料不完整，需要修订题目", "Question material needs revision"],
    provider_unavailable: ["配图模型暂不可用", "Diagram model unavailable"],
    preview_unavailable: ["图面预览暂不可用", "Diagram preview unavailable"],
    visual_review_failed: ["题图未通过审查", "Diagram did not pass review"],
    joint_review_failed: ["题图与题目条件未通过一致性检查", "Diagram and question did not pass consistency review"],
    patch_conflict: ["题图修正发生冲突", "Diagram revisions could not be applied"],
    budget_exhausted: ["配图超出本次时间或调用预算", "Diagram generation timed out"],
    assessment_question_not_current: ["该题已不是进行中测评的当前题", "This question is no longer active"],
    illustration_disabled: ["题图生成已关闭", "Diagram generation disabled"],
    policy_disabled: ["题图生成已关闭", "Diagram generation disabled"],
    run_interrupted: ["配图任务中断，可重试", "Diagram job interrupted"],
  };
  return labels[code]?.[english ? 1 : 0] ?? (english ? "The diagram could not be completed" : "本次配图未完成");
}
